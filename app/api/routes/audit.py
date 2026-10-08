"""
api/routes/audit.py
===================
GET /audit         — get recent audit entries
GET /audit/{id}    — get audit entries for a specific decision
GET /world         — get current world state summary
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from typing import Any

from app.audit.log import get_audit_log
from app.contracts.audit import AuditEntry
from app.world.state import get_world_store

router = APIRouter()


@router.get("/audit", response_model=list[AuditEntry])
def get_audit(n: int = Query(default=50, ge=1, le=1000)) -> list[AuditEntry]:
    """Return the most recent n audit log entries."""
    return get_audit_log().get_recent(n)


@router.get("/audit/decision/{decision_id}", response_model=list[AuditEntry])
def get_audit_for_decision(decision_id: str) -> list[AuditEntry]:
    """Return all audit entries for a specific decision."""
    return get_audit_log().get_for_decision(decision_id)


@router.get("/world")
def get_world_state() -> dict[str, Any]:
    """Return a summary of the current world state."""
    return get_world_store().summary()
