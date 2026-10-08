"""
tests/unit/test_engine_genericity.py
====================================
Tests for engine genericity:
  - Tool definitions and rules flow from config, not hardcoded engine logic.
  - Dynamically adding a new tool to registry works end-to-end without engine changes.
  - Unknown tools are rejected generically.
"""

from __future__ import annotations

import pytest

from app.contracts.decision import DecisionOutcome
from app.contracts.proposal import ActionProposal
from app.guard.engine import GuardEngine
from app.guard.registry import ArgumentSpec, ToolDefinition, ToolRegistry
from app.guard.risk import RiskCalculator
from app.guard.rules import PolicyConfig, RulesEngine


def test_custom_tool_registered_dynamically_evaluates_correctly():
    """A completely new tool registered at runtime evaluates through the engine."""
    # Create custom registry
    registry = ToolRegistry()
    custom_tool = ToolDefinition(
        id="slack_notify",
        description="Post a message to a Slack channel",
        capability="slack.notify",
        mutating=True,
        read_only=False,
        reversible=False,
        undo_supported=False,
        high_impact=False,
        requires_semantic=False,
        base_risk=0.25,
        arguments={
            "channel": ArgumentSpec(name="channel", type="string", required=True),
            "message": ArgumentSpec(name="message", type="string", required=True, max_length=1000),
        },
    )
    registry._tools["slack_notify"] = custom_tool

    # Policy granting slack.notify capability
    policy = PolicyConfig()
    policy._data["agent_capabilities"]["demo-agent"].append("slack.notify")

    rules_engine = RulesEngine(policy_config=policy, registry=registry)
    risk_calc = RiskCalculator(registry=registry)
    engine = GuardEngine(
        registry=registry,
        policy=policy,
        rules_engine=rules_engine,
        risk_calculator=risk_calc,
    )

    # Valid proposal for new tool
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="slack_notify",
        arguments={"channel": "#general", "message": "System status OK"},
    )

    decision = engine.evaluate(proposal)
    assert decision.outcome == DecisionOutcome.APPROVE
    assert decision.canonical_action.tool == "slack_notify"
    assert decision.risk_score == pytest.approx(0.25)


def test_custom_tool_unauthorized_blocked_generically():
    """A new tool without capability grant is automatically blocked by the generic engine."""
    registry = ToolRegistry()
    registry._tools["db_drop"] = ToolDefinition(
        id="db_drop",
        description="Drop a database table",
        capability="db.admin",
        mutating=True,
        read_only=False,
        reversible=False,
        undo_supported=False,
        high_impact=True,
        requires_semantic=False,
        base_risk=0.9,
        arguments={
            "table": ArgumentSpec(name="table", type="string", required=True),
        },
    )

    policy = PolicyConfig()
    # demo-agent does NOT have db.admin

    rules_engine = RulesEngine(policy_config=policy, registry=registry)
    risk_calc = RiskCalculator(registry=registry)
    engine = GuardEngine(
        registry=registry,
        policy=policy,
        rules_engine=rules_engine,
        risk_calculator=risk_calc,
    )

    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="db_drop",
        arguments={"table": "users"},
    )

    decision = engine.evaluate(proposal)
    assert decision.outcome == DecisionOutcome.BLOCK
    assert "UNAUTHORIZED_CAPABILITY" in decision.rules_fired
