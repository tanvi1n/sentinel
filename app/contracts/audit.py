"""
contracts/audit.py
==================
AuditEntry — the contract for audit log entries.

Every significant lifecycle event produces an AuditEntry.
The audit log is append-only.

Owned by: Person 2
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Audit event types
# ---------------------------------------------------------------------------

class AuditEvent(str, enum.Enum):
    """Exhaustive set of lifecycle events that are audited."""

    ACTION_PROPOSED          = "ACTION_PROPOSED"
    INTAKE_REJECTED          = "INTAKE_REJECTED"
    EVALUATED                = "EVALUATED"
    REVIEW_REQUESTED         = "REVIEW_REQUESTED"
    APPROVED                 = "APPROVED"
    REJECTED                 = "REJECTED"
    BLOCKED                  = "BLOCKED"
    EXECUTING                = "EXECUTING"
    EXECUTED                 = "EXECUTED"
    EXECUTION_FAILED         = "EXECUTION_FAILED"
    UNDO_REQUESTED           = "UNDO_REQUESTED"
    UNDONE                   = "UNDONE"
    UNDO_FAILED              = "UNDO_FAILED"
    REVALIDATION_PASSED      = "REVALIDATION_PASSED"
    REVALIDATION_FAILED      = "REVALIDATION_FAILED"
    INTEGRITY_VERIFIED       = "INTEGRITY_VERIFIED"
    INTEGRITY_FAILED         = "INTEGRITY_FAILED"
    SESSION_CONTEXT_UPDATED  = "SESSION_CONTEXT_UPDATED"


# ---------------------------------------------------------------------------
# AuditEntry
# ---------------------------------------------------------------------------

class AuditEntry(BaseModel):
    """
    A single immutable audit log entry.

    Do NOT log secrets, API keys, or PII values.
    """

    entry_id: str = Field(
        ...,
        description="Unique identifier for this audit entry.",
    )
    event: AuditEvent = Field(
        ...,
        description="The type of lifecycle event.",
    )
    decision_id: str | None = Field(
        default=None,
        description="The associated GuardDecision ID, if applicable.",
    )
    agent_id: str | None = Field(
        default=None,
        description="The agent that proposed the action.",
    )
    tool: str | None = Field(
        default=None,
        description="The tool involved in the action.",
    )
    outcome: str | None = Field(
        default=None,
        description="Decision outcome at the time of the event.",
    )
    lifecycle: str | None = Field(
        default=None,
        description="Lifecycle state at the time of the event.",
    )
    message: str = Field(
        default="",
        description="Human-readable description of the event.",
    )
    metadata: dict = Field(
        default_factory=dict,
        description="Additional structured data for this event (no secrets).",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when this entry was created.",
    )
    latency_ms: float | None = Field(
        default=None,
        description="Latency in milliseconds for timed operations.",
    )

    model_config = {"extra": "forbid"}
