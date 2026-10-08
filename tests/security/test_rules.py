"""
tests/security/test_rules.py
============================
Security tests covering deterministic rules, boundary conditions,
capability enforcement, and untrusted proposal rejection.

Covers:
  - Every deterministic rule in rules.py / policies.yaml
  - Boundary conditions (payment thresholds)
  - Permission / capability violations
  - Agent-supplied trusted fields rejection
"""

from __future__ import annotations

import pytest

from app.contracts.decision import DecisionOutcome
from app.contracts.errors import (
    PathTraversalError,
    UnknownTool,
)
from app.contracts.proposal import ActionProposal, ProvenanceLabel
from app.guard.client import GuardClient
from app.guard.intake import process_proposal
from app.guard.rules import PolicyConfig, RulesEngine, SessionContext


# ---------------------------------------------------------------------------
# 1. UNAUTHORIZED_CAPABILITY
# ---------------------------------------------------------------------------

def test_unauthorized_capability_blocks(fresh_service):
    """An agent without the required capability must be BLOCKED."""
    client = GuardClient(fresh_service)
    # readonly-agent only has calendar.read, email.read, web.fetch
    p = ActionProposal(
        agent_id="readonly-agent",
        tool="payment_transfer",
        arguments={"amount": 100.0, "recipient": "alice"},
    )
    decision = client.evaluate(p)
    assert decision.outcome == DecisionOutcome.BLOCK
    assert "UNAUTHORIZED_CAPABILITY" in decision.rules_fired


def test_restricted_agent_blocked_from_file_delete(fresh_service):
    """restricted-agent lacks file.delete capability -> BLOCKED."""
    client = GuardClient(fresh_service)
    p = ActionProposal(
        agent_id="restricted-agent",
        tool="file_delete",
        arguments={"path": "/home/user/documents/report_q4.pdf"},
    )
    decision = client.evaluate(p)
    assert decision.outcome == DecisionOutcome.BLOCK
    assert "UNAUTHORIZED_CAPABILITY" in decision.rules_fired


def test_unknown_agent_defaults_to_minimal_capabilities(fresh_service):
    """Unknown agent uses __default__ (calendar.read only). Other tools BLOCKED."""
    client = GuardClient(fresh_service)
    p = ActionProposal(
        agent_id="completely-unknown-agent",
        tool="email_read",
        arguments={},
    )
    decision = client.evaluate(p)
    assert decision.outcome == DecisionOutcome.BLOCK
    assert "UNAUTHORIZED_CAPABILITY" in decision.rules_fired


def test_authorized_agent_permitted(fresh_service):
    """demo-agent has all capabilities and can use tools within limits."""
    client = GuardClient(fresh_service)
    p = ActionProposal(
        agent_id="demo-agent",
        tool="calendar_read",
        arguments={},
    )
    decision = client.evaluate(p)
    assert decision.outcome == DecisionOutcome.APPROVE
    assert "UNAUTHORIZED_CAPABILITY" not in decision.rules_fired


# ---------------------------------------------------------------------------
# 2. PATH_TRAVERSAL
# ---------------------------------------------------------------------------

def test_path_traversal_blocked_in_rules(fresh_engine):
    """Path traversal pattern is detected as critical violation."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="file_delete",
        arguments={"path": "../etc/passwd"},
    )
    # Intake catches it first
    with pytest.raises(PathTraversalError):
        fresh_engine.evaluate(p)


# ---------------------------------------------------------------------------
# 3. PERMANENT_DELETE
# ---------------------------------------------------------------------------

def test_permanent_delete_blocked_by_policy(fresh_service):
    """Permanent file delete is hard-blocked by policy."""
    client = GuardClient(fresh_service)
    p = ActionProposal(
        agent_id="demo-agent",
        tool="file_delete",
        arguments={"path": "/home/user/documents/report_q4.pdf", "permanent": True},
    )
    decision = client.evaluate(p)
    assert decision.outcome == DecisionOutcome.BLOCK
    assert "PERMANENT_DELETE" in decision.rules_fired


def test_soft_delete_not_blocked_by_permanent_delete_rule(fresh_service):
    """Non-permanent file delete does not trigger PERMANENT_DELETE rule."""
    client = GuardClient(fresh_service)
    p = ActionProposal(
        agent_id="demo-agent",
        tool="file_delete",
        arguments={"path": "/home/user/documents/report_q4.pdf", "permanent": False},
    )
    decision = client.evaluate(p)
    assert "PERMANENT_DELETE" not in decision.rules_fired
    # It requires REVIEW due to risk score / high-impact
    assert decision.outcome == DecisionOutcome.REVIEW


# ---------------------------------------------------------------------------
# 4. Payment Rules & Boundary Conditions
# ---------------------------------------------------------------------------

def test_payment_small_approved(fresh_service):
    """Payment <= 5000 is APPROVE."""
    client = GuardClient(fresh_service)
    p = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 1000.0, "recipient": "bob"},
    )
    decision = client.evaluate(p)
    assert decision.outcome == DecisionOutcome.APPROVE
    assert "PAYMENT_REVIEW" not in decision.rules_fired
    assert "LARGE_PAYMENT" not in decision.rules_fired


def test_payment_boundary_at_review_threshold(fresh_service):
    """Payment at exactly 5000.0 is APPROVE (> 5000 triggers review)."""
    client = GuardClient(fresh_service)
    p = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 5000.0, "recipient": "bob"},
    )
    decision = client.evaluate(p)
    assert decision.outcome == DecisionOutcome.APPROVE


def test_payment_boundary_just_above_review_threshold(fresh_service):
    """Payment at 5000.01 triggers PAYMENT_REVIEW -> REVIEW."""
    client = GuardClient(fresh_service)
    p = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 5000.01, "recipient": "bob"},
    )
    decision = client.evaluate(p)
    assert decision.outcome == DecisionOutcome.REVIEW
    assert "PAYMENT_REVIEW" in decision.rules_fired


def test_payment_boundary_at_block_threshold(fresh_service):
    """Payment at exactly 25000.0 triggers PAYMENT_REVIEW (not LARGE_PAYMENT)."""
    client = GuardClient(fresh_service)
    p = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 25000.0, "recipient": "bob"},
    )
    decision = client.evaluate(p)
    assert decision.outcome == DecisionOutcome.REVIEW
    assert "PAYMENT_REVIEW" in decision.rules_fired
    assert "LARGE_PAYMENT" not in decision.rules_fired


def test_payment_boundary_just_above_block_threshold(fresh_service):
    """Payment at 25000.01 triggers LARGE_PAYMENT -> BLOCK."""
    client = GuardClient(fresh_service)
    p = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 25000.01, "recipient": "bob"},
    )
    decision = client.evaluate(p)
    assert decision.outcome == DecisionOutcome.BLOCK
    assert "LARGE_PAYMENT" in decision.rules_fired


def test_payment_very_large_blocked(fresh_service):
    """Payment well over block threshold is hard-blocked."""
    client = GuardClient(fresh_service)
    p = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 100000.0, "recipient": "attacker"},
    )
    decision = client.evaluate(p)
    assert decision.outcome == DecisionOutcome.BLOCK
    assert "LARGE_PAYMENT" in decision.rules_fired


# ---------------------------------------------------------------------------
# 5. Provenance Rules
# ---------------------------------------------------------------------------

def test_payment_external_provenance_recipient_blocked(fresh_service):
    """Payment with external recipient is hard-blocked (prompt injection)."""
    client = GuardClient(fresh_service)
    p = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 100.0, "recipient": "attacker_account"},
    )
    decision = client.evaluate(
        p,
        harness_provenance={"recipient": ProvenanceLabel.EXTERNAL},
    )
    assert decision.outcome == DecisionOutcome.BLOCK
    assert "PAYMENT_EXTERNAL_PROVENANCE" in decision.rules_fired


def test_payment_external_provenance_amount_blocked(fresh_service):
    """Payment with external amount is hard-blocked."""
    client = GuardClient(fresh_service)
    p = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 100.0, "recipient": "legit_vendor"},
    )
    decision = client.evaluate(
        p,
        harness_provenance={"amount": ProvenanceLabel.EXTERNAL},
    )
    assert decision.outcome == DecisionOutcome.BLOCK
    assert "PAYMENT_EXTERNAL_PROVENANCE" in decision.rules_fired


def test_external_content_mutation_triggers_review(fresh_service):
    """Mutating tool with external provenance triggers EXTERNAL_CONTENT_MUTATION -> REVIEW."""
    client = GuardClient(fresh_service)
    p = ActionProposal(
        agent_id="demo-agent",
        tool="email_send",
        arguments={
            "to": "partner@example.com",
            "subject": "Injected Subject",
            "body": "Safe body",
        },
    )
    decision = client.evaluate(
        p,
        harness_provenance={"subject": ProvenanceLabel.EXTERNAL},
    )
    assert decision.outcome == DecisionOutcome.REVIEW
    assert "EXTERNAL_CONTENT_MUTATION" in decision.rules_fired


# ---------------------------------------------------------------------------
# 6. Session Flow: SEND_AFTER_PRIVATE_READ
# ---------------------------------------------------------------------------

def test_send_after_private_read_triggers_review(fresh_service):
    """In a session with prior executed email_read, email_send triggers REVIEW."""
    client = GuardClient(fresh_service)
    session_id = "session-exfil-test"

    # Step 1: Read email and execute it
    p_read = ActionProposal(
        agent_id="demo-agent",
        tool="email_read",
        arguments={},
    )
    d_read = client.evaluate(p_read, session_id=session_id)
    # email_read has base_risk 0.20, no review rules fired -> APPROVE
    assert d_read.outcome == DecisionOutcome.APPROVE
    client.execute(d_read.decision_id, session_id=session_id)

    # Step 2: Now propose email_send in the same session
    p_send = ActionProposal(
        agent_id="demo-agent",
        tool="email_send",
        arguments={
            "to": "external@example.com",
            "subject": "Leak",
            "body": "Confidential info",
        },
    )
    d_send = client.evaluate(p_send, session_id=session_id)
    assert d_send.outcome == DecisionOutcome.REVIEW
    assert "SEND_AFTER_PRIVATE_READ" in d_send.rules_fired


def test_send_without_private_read_does_not_fire_session_rule(fresh_service):
    """In a session without email_read, SEND_AFTER_PRIVATE_READ does not fire."""
    rules_engine = RulesEngine()
    p = ActionProposal(
        agent_id="demo-agent",
        tool="email_send",
        arguments={
            "to": "bob@example.com",
            "subject": "Hello",
            "body": "World",
        },
    )
    canonical = process_proposal(p)
    empty_ctx = SessionContext(private_read_executed=False)
    res = rules_engine.evaluate(canonical, empty_ctx)
    assert "SEND_AFTER_PRIVATE_READ" not in res.rules_fired


# ---------------------------------------------------------------------------
# 7. Agent-Supplied Trusted Fields Must Be Rejected
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("forbidden_field", [
    "permissions",
    "permission",
    "role",
    "user_confirmation",
    "confirmed",
    "risk",
    "risk_level",
    "reversibility",
    "is_reversible",
    "observed_content",
    "trusted_content",
])
def test_agent_supplied_trusted_fields_rejected(forbidden_field):
    """ActionProposal must reject any attempt by the agent to supply trusted fields."""
    with pytest.raises(ValueError, match="forbidden agent-supplied trusted"):
        ActionProposal.model_validate({
            "agent_id": "malicious-agent",
            "tool": "payment_transfer",
            "arguments": {"amount": 50000, "recipient": "attacker"},
            forbidden_field: True,
        })
