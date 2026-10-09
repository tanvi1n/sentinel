"""
tests/security/test_semantic_invariants.py
==========================================
Security tests for semantic invariants:
  - Deterministic BLOCK can NEVER become REVIEW or APPROVE under ANY semantic result.
  - Semantic reasoning can NEVER produce BLOCK.
  - Semantic reasoning can ONLY escalate APPROVE -> REVIEW.
  - Semantic reasoning can NEVER lower a decision (REVIEW cannot become APPROVE).
  - Semantic provider failures (INVALID, TIMEOUT, UNAVAILABLE) -> REVIEW where required.
"""

from __future__ import annotations

import pytest

from app.contracts.decision import DecisionOutcome
from app.contracts.proposal import ActionProposal, ProvenanceLabel
from app.contracts.semantic import SemanticOutcome, SemanticResult
from app.guard.client import GuardClient


ALL_SEMANTIC_OUTCOMES = [
    SemanticOutcome.SAFE,
    SemanticOutcome.SUSPICIOUS,
    SemanticOutcome.UNSAFE,
    SemanticOutcome.INVALID,
    SemanticOutcome.TIMEOUT,
    SemanticOutcome.UNAVAILABLE,
]


# ---------------------------------------------------------------------------
# 1. Deterministic BLOCK is invariant under every semantic result
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sem_outcome", ALL_SEMANTIC_OUTCOMES + [None])
def test_block_invariant_under_all_semantic_results(fresh_service, sem_outcome):
    """
    When deterministic rules block (large payment > 25000), the outcome
    MUST remain BLOCK regardless of whether semantic reasoning says
    SAFE, SUSPICIOUS, UNSAFE, TIMEOUT, INVALID, or UNAVAILABLE.
    """
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 50000.0, "recipient": "bob"},
    )
    sem_result = (
        SemanticResult(outcome=sem_outcome, reason=f"mock {sem_outcome}", provider="MOCK")
        if sem_outcome is not None
        else None
    )

    decision = client.evaluate(proposal, semantic_result=sem_result)
    assert decision.outcome == DecisionOutcome.BLOCK
    assert "LARGE_PAYMENT" in decision.rules_fired


@pytest.mark.parametrize("sem_outcome", ALL_SEMANTIC_OUTCOMES + [None])
def test_external_provenance_block_invariant_under_all_semantic_results(
    fresh_service, sem_outcome
):
    """
    Prompt injection attempt (external provenance payment) is deterministically BLOCKED.
    Even a compromised semantic checker saying 'SAFE' must NOT change BLOCK.
    """
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 100.0, "recipient": "attacker"},
    )
    sem_result = (
        SemanticResult(outcome=sem_outcome, reason=f"compromised {sem_outcome}", provider="MOCK")
        if sem_outcome is not None
        else None
    )

    decision = client.evaluate(
        proposal,
        semantic_result=sem_result,
        harness_provenance={"recipient": ProvenanceLabel.EXTERNAL},
    )
    assert decision.outcome == DecisionOutcome.BLOCK


# ---------------------------------------------------------------------------
# 2. Semantic reasoning can NEVER produce BLOCK
# ---------------------------------------------------------------------------

def test_semantic_unsafe_cannot_block_safe_proposal(fresh_service):
    """
    A proposal that is deterministically safe (calendar_read) must NEVER
    become BLOCK, even when semantic reasoning returns UNSAFE.
    It can at most escalate to REVIEW.
    """
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="calendar_read",
        arguments={},
    )
    sem_unsafe = SemanticResult(
        outcome=SemanticOutcome.UNSAFE,
        reason="Semantic checker objects strongly",
        provider="MOCK",
    )

    decision = client.evaluate(proposal, semantic_result=sem_unsafe)
    assert decision.outcome != DecisionOutcome.BLOCK
    assert decision.outcome == DecisionOutcome.REVIEW


def test_semantic_suspicious_cannot_block_payment_review(fresh_service):
    """
    A proposal that is deterministically REVIEW (payment 10000) must stay REVIEW,
    not BLOCK, when semantic says UNSAFE.
    """
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 10000.0, "recipient": "bob"},
    )
    sem_unsafe = SemanticResult(
        outcome=SemanticOutcome.UNSAFE,
        reason="Object",
        provider="MOCK",
    )

    decision = client.evaluate(proposal, semantic_result=sem_unsafe)
    assert decision.outcome == DecisionOutcome.REVIEW


# ---------------------------------------------------------------------------
# 3. Semantic reasoning can NEVER lower a decision
# ---------------------------------------------------------------------------

def test_semantic_safe_cannot_lower_review_to_approve(fresh_service):
    """
    When deterministic rules require REVIEW (payment > 5000), a semantic
    result of 'SAFE' must NOT lower the outcome to APPROVE.
    """
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 10000.0, "recipient": "bob"},
    )
    sem_safe = SemanticResult(
        outcome=SemanticOutcome.SAFE,
        reason="Looks completely fine",
        provider="MOCK",
    )

    decision = client.evaluate(proposal, semantic_result=sem_safe)
    assert decision.outcome == DecisionOutcome.REVIEW


def test_semantic_safe_cannot_lower_block_to_anything(fresh_service):
    """
    When deterministic rules BLOCK (unauthorized capability), a semantic
    result of 'SAFE' must NOT lower the outcome to APPROVE or REVIEW.
    """
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="readonly-agent",
        tool="payment_transfer",
        arguments={"amount": 100.0, "recipient": "bob"},
    )
    sem_safe = SemanticResult(
        outcome=SemanticOutcome.SAFE,
        reason="Safe transaction",
        provider="MOCK",
    )

    decision = client.evaluate(proposal, semantic_result=sem_safe)
    assert decision.outcome == DecisionOutcome.BLOCK


# ---------------------------------------------------------------------------
# 4. Semantic escalation APPROVE -> REVIEW
# ---------------------------------------------------------------------------

def test_semantic_suspicious_escalates_approve_to_review(fresh_service):
    """Intent mismatch: safe proposal escalated to REVIEW by SUSPICIOUS."""
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="calendar_read",
        arguments={},
    )
    sem_suspicious = SemanticResult(
        outcome=SemanticOutcome.SUSPICIOUS,
        reason="User did not ask to read calendar",
        provider="MOCK",
    )

    decision = client.evaluate(proposal, semantic_result=sem_suspicious)
    assert decision.outcome == DecisionOutcome.REVIEW
    assert decision.semantic_outcome == "SUSPICIOUS"


def test_semantic_safe_preserves_approve(fresh_service):
    """When both rules and semantic agree safe, outcome is APPROVE."""
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="calendar_read",
        arguments={},
    )
    sem_safe = SemanticResult(
        outcome=SemanticOutcome.SAFE,
        reason="Aligned with user intent",
        provider="MOCK",
    )

    decision = client.evaluate(proposal, semantic_result=sem_safe)
    assert decision.outcome == DecisionOutcome.APPROVE


# ---------------------------------------------------------------------------
# 5. Semantic provider failures -> REVIEW for tools requiring semantic check
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("failure_outcome", [
    SemanticOutcome.INVALID,
    SemanticOutcome.TIMEOUT,
    SemanticOutcome.UNAVAILABLE,
])
def test_semantic_failure_escalates_to_review_for_required_tool(
    fresh_service, failure_outcome
):
    """
    For a tool with requires_semantic=True (email_send, file_delete,
    payment_transfer), semantic failures (TIMEOUT, INVALID, UNAVAILABLE) must
    trigger REVIEW.
    """
    client = GuardClient(fresh_service)
    # payment_transfer has requires_semantic: true, and a small amount is
    # APPROVE on the rules alone, so the failure itself must cause the REVIEW.
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 500.0, "recipient": "bob"},
    )
    assert client.evaluate(proposal).outcome == DecisionOutcome.APPROVE
    sem_failure = SemanticResult(
        outcome=failure_outcome,
        reason="Provider error",
        provider="MOCK",
    )

    decision = client.evaluate(proposal, semantic_result=sem_failure)
    assert decision.outcome == DecisionOutcome.REVIEW
    assert "SEMANTIC_REQUIRED_UNAVAILABLE" in decision.rules_fired
