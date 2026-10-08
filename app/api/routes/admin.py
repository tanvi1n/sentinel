"""
api/routes/admin.py
===================
Demo admin routes for reset and health check.

POST /reset  — reset all state for a fresh demo run
GET  /health — health check
GET  /tools  — list registered tools (for Person 3 UI)
"""

from __future__ import annotations

from fastapi import APIRouter
from typing import Any

from app.guard.registry import get_registry
from app.guard.service import get_guard_service

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "service": "sentinel"}


@router.post("/reset")
def reset() -> dict[str, str]:
    """
    Reset all state for a fresh demo run.

    Clears: decisions, audit log, world state, sessions.
    """
    get_guard_service().reset()
    return {"status": "reset", "message": "All state cleared for fresh demo run."}


@router.get("/tools")
def list_tools() -> list[dict[str, Any]]:
    """List all registered tools and their metadata."""
    registry = get_registry()
    return [
        {
            "id": t.id,
            "description": t.description,
            "capability": t.capability,
            "mutating": t.mutating,
            "reversible": t.reversible,
            "undo_supported": t.undo_supported,
            "high_impact": t.high_impact,
            "requires_semantic": t.requires_semantic,
            "base_risk": t.base_risk,
            "arguments": {
                name: {
                    "type": spec.type,
                    "required": spec.required,
                    "description": spec.description,
                }
                for name, spec in t.arguments.items()
            },
        }
        for t in registry.all_tools()
    ]
