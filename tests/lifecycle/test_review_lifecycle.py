"""
tests/lifecycle/test_review_lifecycle.py
========================================
Lifecycle tests for the human review approval gate:
  - REVIEW requires human approval before execution.
  - BLOCK can NEVER be approved.
  - APPROVE executes directly; manual approval is rejected.
  - Only REVIEW transitions through approval/rejection.
  - Lifecycle state machine validity.
"""

from __future__ import annotations

import pytest

from app.contracts.decision import DecisionLifecycle, DecisionOutcome
from app.contracts.errors import (
    AlreadyExecuted,
    BlockedDecision,
    InvalidLifecycleTransition,
    RejectedDecision,
    ReviewRequired,
)
from app.contracts.proposal import ActionProposal
from app.guard.client import GuardClient


# ---------------------------------------------------------------------------
# 1. REVIEW approval gate
# ---------------------------------------------------------------------------

def test_review_decision_requires_approval_before_execution(fresh_service):
    """An unapproved REVIEW decision cannot be executed."""
    client = GuardClient(fresh_service)
    # payment 10000 -> REVIEW
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 10000.0, "recipient": "bob"},
    )
    decision = client.evaluate(proposal)
    assert decision.outcome == DecisionOutcome.REVIEW
    assert decision.lifecycle == DecisionLifecycle.PENDING_REVIEW

    # Direct execution attempt must fail
    with pytest.raises(ReviewRequired, match="requires human approval"):
        client.execute(decision.decision_id)


def test_review_decision_executes_after_approval(fresh_service):
    """An approved REVIEW decision transitions to APPROVED and executes."""
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 10000.0, "recipient": "bob"},
    )
    decision = client.evaluate(proposal)

    # Human approves
    approved = client.approve(decision.decision_id, reviewer_id="security-lead")
    assert approved.lifecycle == DecisionLifecycle.APPROVED
    assert approved.reviewer_id == "security-lead"

    # Now execution succeeds
    result = client.execute(decision.decision_id)
    assert result["success"] is True

    # Post-execution lifecycle
    final = client.get_decision(decision.decision_id)
    assert final.lifecycle == DecisionLifecycle.EXECUTED


def test_review_decision_rejected_cannot_execute(fresh_service):
    """A rejected REVIEW decision cannot be executed."""
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 10000.0, "recipient": "bob"},
    )
    decision = client.evaluate(proposal)

    # Human rejects
    rejected = client.reject(
        decision.decision_id, reviewer_id="security-lead", reason="Suspicious recipient"
    )
    assert rejected.lifecycle == DecisionLifecycle.REJECTED

    # Execution attempt must fail
    with pytest.raises(RejectedDecision, match="REJECTED"):
        client.execute(decision.decision_id)


# ---------------------------------------------------------------------------
# 2. BLOCK can NEVER be approved
# ---------------------------------------------------------------------------

def test_block_decision_cannot_be_approved(fresh_service):
    """Attempting to approve a BLOCK decision raises BlockedDecision."""
    client = GuardClient(fresh_service)
    # payment 50000 -> BLOCK
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 50000.0, "recipient": "bob"},
    )
    decision = client.evaluate(proposal)
    assert decision.outcome == DecisionOutcome.BLOCK
    assert decision.lifecycle == DecisionLifecycle.BLOCKED

    with pytest.raises(BlockedDecision, match="BLOCKED and cannot be approved"):
        client.approve(decision.decision_id)


def test_block_decision_cannot_be_executed(fresh_service):
    """Attempting to execute a BLOCK decision raises BlockedDecision."""
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 50000.0, "recipient": "bob"},
    )
    decision = client.evaluate(proposal)

    with pytest.raises(BlockedDecision, match="BLOCKED and cannot be executed"):
        client.execute(decision.decision_id)


# ---------------------------------------------------------------------------
# 3. APPROVE decisions execute directly — cannot be manually approved
# ---------------------------------------------------------------------------

def test_approve_decision_executes_directly_without_manual_approval(fresh_service):
    """APPROVE outcome executes directly without requiring approve() step."""
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="calendar_read",
        arguments={},
    )
    decision = client.evaluate(proposal)
    assert decision.outcome == DecisionOutcome.APPROVE
    assert decision.lifecycle == DecisionLifecycle.EVALUATED

    result = client.execute(decision.decision_id)
    assert result["success"] is True

    final = client.get_decision(decision.decision_id)
    assert final.lifecycle == DecisionLifecycle.EXECUTED


def test_manual_approval_of_approve_decision_rejected(fresh_service):
    """Calling approve() on an APPROVE decision raises InvalidLifecycleTransition."""
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="calendar_read",
        arguments={},
    )
    decision = client.evaluate(proposal)

    with pytest.raises(InvalidLifecycleTransition, match="does not require human approval"):
        client.approve(decision.decision_id)


# ---------------------------------------------------------------------------
# 4. State transitions & query methods
# ---------------------------------------------------------------------------

def test_list_pending_review(fresh_service):
    """list_pending_review() returns only decisions in PENDING_REVIEW state."""
    client = GuardClient(fresh_service)

    # Decision 1: APPROVE
    client.evaluate(ActionProposal(agent_id="demo-agent", tool="calendar_read", arguments={}))

    # Decision 2: REVIEW
    d2 = client.evaluate(
        ActionProposal(
            agent_id="demo-agent",
            tool="payment_transfer",
            arguments={"amount": 10000.0, "recipient": "bob"},
        )
    )

    pending = client.list_pending_review()
    assert len(pending) == 1
    assert pending[0].decision_id == d2.decision_id

    # Approve it -> no longer pending
    client.approve(d2.decision_id)
    assert len(client.list_pending_review()) == 0


def test_double_approval_rejected(fresh_service):
    """Approving an already APPROVED decision raises InvalidLifecycleTransition."""
    client = GuardClient(fresh_service)
    d = client.evaluate(
        ActionProposal(
            agent_id="demo-agent",
            tool="payment_transfer",
            arguments={"amount": 10000.0, "recipient": "bob"},
        )
    )
    client.approve(d.decision_id)

    with pytest.raises(InvalidLifecycleTransition):
        client.approve(d.decision_id)


def test_double_execution_rejected(fresh_service):
    """Executing an already EXECUTED decision raises AlreadyExecuted."""
    client = GuardClient(fresh_service)
    d = client.evaluate(
        ActionProposal(agent_id="demo-agent", tool="calendar_read", arguments={})
    )
    client.execute(d.decision_id)

    with pytest.raises(AlreadyExecuted):
        client.execute(d.decision_id)
