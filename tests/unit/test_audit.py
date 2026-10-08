"""
tests/unit/test_audit.py
========================
Tests for the audit logging system:
  - Append-only recording of all lifecycle events
  - Querying by decision ID and recent count
  - Event chronology and metadata preservation
  - Reset / clearing for demo runs
"""

from __future__ import annotations

import pytest

from app.contracts.audit import AuditEvent
from app.contracts.decision import DecisionOutcome
from app.contracts.proposal import ActionProposal
from app.guard.client import GuardClient


def test_full_lifecycle_audit_trail(fresh_service):
    """
    Tracing a full review -> approve -> execute -> undo lifecycle
    must generate a complete, ordered audit trail.
    """
    client = GuardClient(fresh_service)
    p = ActionProposal(
        agent_id="demo-agent",
        tool="file_delete",
        arguments={"path": "/home/user/documents/report_q4.pdf"},
    )

    decision = client.evaluate(p)
    client.approve(decision.decision_id, reviewer_id="security-reviewer")
    client.execute(decision.decision_id)
    client.undo(decision.decision_id)

    # Query audit for this decision
    entries = client.get_audit_for_decision(decision.decision_id)
    events = [e.event for e in entries]

    assert AuditEvent.REVIEW_REQUESTED in events
    assert AuditEvent.APPROVED in events
    assert AuditEvent.REVALIDATION_PASSED in events
    assert AuditEvent.EXECUTING in events
    assert AuditEvent.EXECUTED in events
    assert AuditEvent.UNDO_REQUESTED in events
    assert AuditEvent.UNDONE in events

    # Check reviewer metadata on approved entry
    approved_entry = next(e for e in entries if e.event == AuditEvent.APPROVED)
    assert approved_entry.metadata["reviewer_id"] == "security-reviewer"


def test_audit_get_recent(fresh_service):
    """get_audit_log(n) returns the most recent n entries."""
    client = GuardClient(fresh_service)
    for _ in range(5):
        client.evaluate(
            ActionProposal(agent_id="demo-agent", tool="calendar_read", arguments={})
        )

    all_entries = client.get_audit_log()
    assert len(all_entries) >= 10  # ACTION_PROPOSED + EVALUATED for each

    recent_3 = client.get_audit_log(n=3)
    assert len(recent_3) == 3
    assert recent_3 == all_entries[-3:]


def test_audit_reset_clears_log(fresh_service):
    """reset() clears all audit log entries."""
    client = GuardClient(fresh_service)
    client.evaluate(
        ActionProposal(agent_id="demo-agent", tool="calendar_read", arguments={})
    )
    assert len(client.get_audit_log()) > 0

    client.reset()
    assert len(client.get_audit_log()) == 0
