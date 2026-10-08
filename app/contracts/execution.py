"""Execution, world and audit contracts. Imports from common and below."""

from typing import Any

from pydantic import Field, RootModel

from app.contracts.common import DecisionStatus, ProvenanceLabel, StrictModel


class DiffEntry(StrictModel):
    """Computed on the server; the UI never diffs."""

    op: str
    path: str
    detail: str


class WorldState(RootModel[dict[str, Any]]):
    """Snapshot of the mock world as domain name -> JSON state.

    The blueprint's demo keys are files, trash, emails, payments and calendar.
    Kept as an open mapping on purpose: the structure of the demo world belongs
    to the world/tool layer, not to this generic contract.
    """


class ExecutionResult(StrictModel):
    decision_id: str
    status: DecisionStatus  # EXECUTED, UNDONE, or BLOCKED after failed revalidation
    ok: bool
    refused_reason: str | None = None
    output_text: str | None = None  # set for read tools from the registry
    output_label: ProvenanceLabel | None = None
    world_before: WorldState | None = None
    world_after: WorldState | None = None
    diff: list[DiffEntry] = Field(default_factory=list)
    undo_available: bool = False  # False for read-only tools


class AuditEvent(StrictModel):
    """One JSONL line per lifecycle event."""

    ts: str
    event: str
    decision_id: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    tool: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
