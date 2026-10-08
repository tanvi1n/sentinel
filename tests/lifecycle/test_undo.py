"""
tests/lifecycle/test_undo.py
============================
Tests for undo functionality:
  - Exact prior state restoration for reversible actions (file_delete).
  - Double undo prevention (AlreadyUndone).
  - Read-only actions reporting undo unavailable (UndoUnavailable).
  - Irreversible actions reporting undo unavailable (UndoUnavailable).
  - Lifecycle and audit tracking of undo operations.
"""

from __future__ import annotations

import pytest

from app.contracts.audit import AuditEvent
from app.contracts.decision import DecisionLifecycle, DecisionOutcome
from app.contracts.errors import AlreadyUndone, UndoUnavailable
from app.contracts.proposal import ActionProposal
from app.guard.client import GuardClient


def test_file_delete_exact_undo(fresh_service):
    """
    File delete executes and removes file.
    Undo restores exact prior file content and state.
    """
    client = GuardClient(fresh_service)
    world = fresh_service._gateway._executor._world
    file_path = "/home/user/documents/report_q4.pdf"

    # Verify file exists before
    assert file_path in world.state.files
    original_content = world.state.files[file_path].content

    # Evaluate, approve, execute
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="file_delete",
        arguments={"path": file_path},
    )
    decision = client.evaluate(proposal)
    assert decision.outcome == DecisionOutcome.REVIEW
    client.approve(decision.decision_id)
    exec_res = client.execute(decision.decision_id)
    assert exec_res["success"] is True

    # File is gone from world
    assert file_path not in world.state.files

    # Undo
    undo_res = client.undo(decision.decision_id)
    assert undo_res["success"] is True

    # File is restored with exact original content
    assert file_path in world.state.files
    assert world.state.files[file_path].content == original_content

    # Lifecycle updated to UNDONE
    d_after = client.get_decision(decision.decision_id)
    assert d_after.lifecycle == DecisionLifecycle.UNDONE

    # Audit events
    audit = client.get_audit_for_decision(decision.decision_id)
    events = [e.event for e in audit]
    assert AuditEvent.UNDO_REQUESTED in events
    assert AuditEvent.UNDONE in events


def test_double_undo_fails(fresh_service):
    """Calling undo twice on the same decision raises AlreadyUndone."""
    client = GuardClient(fresh_service)
    file_path = "/home/user/documents/report_q4.pdf"

    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="file_delete",
        arguments={"path": file_path},
    )
    decision = client.evaluate(proposal)
    client.approve(decision.decision_id)
    client.execute(decision.decision_id)

    # First undo succeeds
    client.undo(decision.decision_id)

    # Second undo must fail
    with pytest.raises(AlreadyUndone, match="already been undone"):
        client.undo(decision.decision_id)


def test_readonly_calendar_read_undo_unavailable(fresh_service):
    """Read-only action calendar_read must report undo unavailable."""
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="calendar_read",
        arguments={},
    )
    decision = client.evaluate(proposal)
    client.execute(decision.decision_id)

    with pytest.raises(UndoUnavailable, match="does not support undo"):
        client.undo(decision.decision_id)


def test_readonly_email_read_undo_unavailable(fresh_service):
    """Read-only action email_read must report undo unavailable."""
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="email_read",
        arguments={},
    )
    decision = client.evaluate(proposal)
    client.execute(decision.decision_id)

    with pytest.raises(UndoUnavailable, match="does not support undo"):
        client.undo(decision.decision_id)


def test_readonly_web_fetch_undo_unavailable(fresh_service):
    """Read-only action web_fetch must report undo unavailable."""
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="web_fetch",
        arguments={"url": "https://example.com/safe-page"},
    )
    decision = client.evaluate(proposal)
    client.execute(decision.decision_id)

    with pytest.raises(UndoUnavailable, match="does not support undo"):
        client.undo(decision.decision_id)


def test_irreversible_payment_undo_unavailable(fresh_service):
    """Irreversible action payment_transfer must report undo unavailable."""
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 500.0, "recipient": "bob"},
    )
    decision = client.evaluate(proposal)
    client.execute(decision.decision_id)

    with pytest.raises(UndoUnavailable, match="does not support undo"):
        client.undo(decision.decision_id)


def test_irreversible_email_send_undo_unavailable(fresh_service):
    """Irreversible action email_send must report undo unavailable."""
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="email_send",
        arguments={
            "to": "bob@example.com",
            "subject": "Hi",
            "body": "Hello",
        },
    )
    decision = client.evaluate(proposal)
    # email_send has base_risk 0.50 -> REVIEW
    client.approve(decision.decision_id)
    client.execute(decision.decision_id)

    with pytest.raises(UndoUnavailable, match="does not support undo"):
        client.undo(decision.decision_id)


def test_undo_on_non_executed_decision_fails(fresh_service):
    """Attempting undo on a decision that was never executed raises UndoUnavailable."""
    client = GuardClient(fresh_service)
    proposal = ActionProposal(
        agent_id="demo-agent",
        tool="file_delete",
        arguments={"path": "/home/user/documents/report_q4.pdf"},
    )
    decision = client.evaluate(proposal)
    # Not executed

    with pytest.raises(UndoUnavailable, match="Can only undo an EXECUTED decision"):
        client.undo(decision.decision_id)
