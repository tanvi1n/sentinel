"""
tests/security/test_integrity_tampering.py
==========================================
Security tests for stored canonical action integrity and tampering detection.

Requirements:
  - Stored canonical action must pass integrity verification before execution.
  - Tampering of stored canonical action raises IntegrityError.
  - INTEGRITY_FAILED audit event is recorded.
  - Decision lifecycle is transitioned to INTEGRITY_FAILED.
  - Execution is refused.
"""

from __future__ import annotations

import pytest

from app.contracts.audit import AuditEvent
from app.contracts.decision import DecisionLifecycle, DecisionOutcome
from app.contracts.errors import IntegrityError
from app.contracts.proposal import ActionProposal, ProvenanceLabel
from app.guard.client import GuardClient


def test_tampered_amount_raises_integrity_error(fresh_service):
    """If stored canonical action amount is tampered with, execute() raises IntegrityError."""
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 500.0, "recipient": "alice"},
    )
    decision = client.evaluate(proposal)
    assert decision.outcome == DecisionOutcome.APPROVE

    # Tamper with stored action: elevate amount
    stored = fresh_service._store.get(decision.decision_id)
    tampered_canonical = stored.canonical_action.model_copy(
        update={"arguments": {"amount": 500000.0, "recipient": "alice"}}
    )
    tampered_decision = stored.model_copy(update={"canonical_action": tampered_canonical})
    fresh_service._store.save(tampered_decision)

    # Attempt execution
    with pytest.raises(IntegrityError, match="tampered"):
        client.execute(decision.decision_id)


def test_tampered_recipient_raises_integrity_error(fresh_service):
    """If stored canonical recipient is tampered with, execute() raises IntegrityError."""
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 500.0, "recipient": "alice"},
    )
    decision = client.evaluate(proposal)

    # Tamper with recipient
    stored = fresh_service._store.get(decision.decision_id)
    tampered_canonical = stored.canonical_action.model_copy(
        update={"arguments": {"amount": 500.0, "recipient": "attacker_account"}}
    )
    fresh_service._store.save(
        stored.model_copy(update={"canonical_action": tampered_canonical})
    )

    with pytest.raises(IntegrityError):
        client.execute(decision.decision_id)


def test_tampered_tool_raises_integrity_error(fresh_service):
    """If stored canonical tool is tampered with, execute() raises IntegrityError."""
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="calendar_read",
        arguments={},
    )
    decision = client.evaluate(proposal)

    # Tamper with tool
    stored = fresh_service._store.get(decision.decision_id)
    tampered_canonical = stored.canonical_action.model_copy(
        update={"tool": "file_delete"}
    )
    fresh_service._store.save(
        stored.model_copy(update={"canonical_action": tampered_canonical})
    )

    with pytest.raises(IntegrityError):
        client.execute(decision.decision_id)


def test_tampered_provenance_raises_integrity_error(fresh_service):
    """If stored argument provenance is tampered with, execute() raises IntegrityError."""
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 500.0, "recipient": "alice"},
    )
    decision = client.evaluate(proposal)

    # Tamper with provenance
    stored = fresh_service._store.get(decision.decision_id)
    tampered_canonical = stored.canonical_action.model_copy(
        update={"arg_provenance": {"amount": "external_content", "recipient": "external_content"}}
    )
    fresh_service._store.save(
        stored.model_copy(update={"canonical_action": tampered_canonical})
    )

    with pytest.raises(IntegrityError):
        client.execute(decision.decision_id)


def test_integrity_failure_records_audit_event(fresh_service):
    """INTEGRITY_FAILED audit event must be recorded with decision and error details."""
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 500.0, "recipient": "alice"},
    )
    decision = client.evaluate(proposal)

    # Tamper
    stored = fresh_service._store.get(decision.decision_id)
    tampered = stored.model_copy(
        update={
            "canonical_action": stored.canonical_action.model_copy(
                update={"arguments": {"amount": 999999.0, "recipient": "attacker"}}
            )
        }
    )
    fresh_service._store.save(tampered)

    with pytest.raises(IntegrityError):
        client.execute(decision.decision_id)

    # Check audit entries
    audit_entries = client.get_audit_for_decision(decision.decision_id)
    integrity_events = [e for e in audit_entries if e.event == AuditEvent.INTEGRITY_FAILED]
    assert len(integrity_events) == 1
    assert "tampered" in integrity_events[0].message.lower()


def test_integrity_failure_updates_decision_lifecycle(fresh_service):
    """Decision lifecycle must be updated to INTEGRITY_FAILED."""
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 500.0, "recipient": "alice"},
    )
    decision = client.evaluate(proposal)

    # Tamper
    stored = fresh_service._store.get(decision.decision_id)
    tampered = stored.model_copy(
        update={
            "canonical_action": stored.canonical_action.model_copy(
                update={"arguments": {"amount": 999999.0, "recipient": "attacker"}}
            )
        }
    )
    fresh_service._store.save(tampered)

    with pytest.raises(IntegrityError):
        client.execute(decision.decision_id)

    updated_decision = client.get_decision(decision.decision_id)
    assert updated_decision.lifecycle == DecisionLifecycle.INTEGRITY_FAILED
