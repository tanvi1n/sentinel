"""
tests/lifecycle/test_execution_gateway.py
=========================================
Tests for the execution gateway:
  - Revalidation immediately before execution (deterministic only, no semantic call).
  - Policy / capability changes between evaluation and execution refuse execution.
  - Execution-time failure handling and lifecycle updates.
  - Stored canonical action usage during execution.
"""

from __future__ import annotations

import pytest

from app.contracts.audit import AuditEvent
from app.contracts.decision import DecisionLifecycle, DecisionOutcome
from app.contracts.errors import RevalidationFailed, ToolExecutionError
from app.contracts.proposal import ActionProposal
from app.guard.client import GuardClient
from app.guard.rules import PolicyConfig, RulesEngine


def test_revalidation_fails_if_policy_threshold_tightened_after_evaluation(
    fresh_service,
):
    """
    If payment policy block threshold is lowered between evaluation and execution,
    execution-time revalidation must refuse execution.
    """
    client = GuardClient(fresh_service)
    # Propose payment of 20000 -> evaluated as REVIEW (< 25000 block threshold)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 20000.0, "recipient": "bob"},
    )
    decision = client.evaluate(proposal)
    assert decision.outcome == DecisionOutcome.REVIEW

    # Reviewer approves
    client.approve(decision.decision_id)

    # Now simulate policy change: tighten block threshold to 15000
    tightened_policy = PolicyConfig()
    tightened_policy._data["limits"]["payment"]["block_threshold"] = 15000
    # Update engine policy
    fresh_service._engine._policy = tightened_policy
    fresh_service._engine._rules = RulesEngine(
        policy_config=tightened_policy, registry=fresh_service._engine._registry
    )

    # Execution must fail at revalidation
    with pytest.raises(RevalidationFailed, match="Revalidation BLOCKED"):
        client.execute(decision.decision_id)

    # Check updated lifecycle and audit
    d_after = client.get_decision(decision.decision_id)
    assert d_after.lifecycle == DecisionLifecycle.REVALIDATION_FAILED

    audit = client.get_audit_for_decision(decision.decision_id)
    rev_events = [e for e in audit if e.event == AuditEvent.REVALIDATION_FAILED]
    assert len(rev_events) == 1


def test_revalidation_fails_if_capability_revoked_after_evaluation(fresh_service):
    """
    If agent capability is revoked after evaluation, revalidation must refuse execution.
    """
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="calendar_read",
        arguments={},
    )
    decision = client.evaluate(proposal)
    assert decision.outcome == DecisionOutcome.APPROVE

    # Revoke calendar.read from demo-agent
    revoked_policy = PolicyConfig()
    revoked_policy._data["agent_capabilities"]["demo-agent"] = ["email.read"]
    fresh_service._engine._policy = revoked_policy
    fresh_service._engine._rules = RulesEngine(
        policy_config=revoked_policy, registry=fresh_service._engine._registry
    )

    with pytest.raises(RevalidationFailed, match="does not have capability"):
        client.execute(decision.decision_id)

    d_after = client.get_decision(decision.decision_id)
    assert d_after.lifecycle == DecisionLifecycle.REVALIDATION_FAILED


def test_tool_execution_error_updates_lifecycle_to_failed(fresh_service):
    """When a tool raises ToolExecutionError, lifecycle is set to EXECUTION_FAILED."""
    client = GuardClient(fresh_service)
    # File does not exist in seeded world
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="file_delete",
        arguments={"path": "/nonexistent/path/file.txt"},
    )
    decision = client.evaluate(proposal)
    assert decision.outcome == DecisionOutcome.REVIEW
    client.approve(decision.decision_id)

    with pytest.raises(ToolExecutionError, match="File not found"):
        client.execute(decision.decision_id)

    d_after = client.get_decision(decision.decision_id)
    assert d_after.lifecycle == DecisionLifecycle.EXECUTION_FAILED

    audit = client.get_audit_for_decision(decision.decision_id)
    fail_events = [e for e in audit if e.event == AuditEvent.EXECUTION_FAILED]
    assert len(fail_events) == 1
