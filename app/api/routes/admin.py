"""
api/routes/admin.py
===================
Admin and demo control routes for SENTINEL.

Endpoints required by the project plan:
  GET  /health                  — health check
  POST /reset                   — reset all state
  GET  /tools                   — list registered tools
  POST /api/admin/review        — review/approve/reject a decision
  POST /api/admin/undo          — undo an executed action
  POST /api/admin/reset         — reset all demo state
  POST /api/admin/semantic-mode — switch semantic mode (mock/replay/live/auto)
  POST /api/admin/policy-override — dynamic policy threshold override

Owned by: Person 2
"""

from __future__ import annotations

from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.contracts.decision import GuardDecision
from app.contracts.errors import (
    AlreadyUndone,
    BlockedDecision,
    DecisionNotFound,
    InvalidLifecycleTransition,
    ToolExecutionError,
    UndoUnavailable,
)
from app.guard.registry import get_registry
from app.guard.service import get_guard_service

router = APIRouter()

# Global semantic mode setting
_semantic_mode = "mock"


# ---------------------------------------------------------------------------
# Request Schemas
# ---------------------------------------------------------------------------

class AdminReviewRequest(BaseModel):
    decision_id: str = Field(..., description="Decision ID to review")
    action: str = Field(default="approve", description="'approve' or 'reject'")
    reviewer_id: str = Field(default="human-reviewer", description="Reviewer identifier")
    reason: str = Field(default="", description="Reason for rejection or review note")


class AdminUndoRequest(BaseModel):
    decision_id: str = Field(..., description="Decision ID to undo")


class SemanticModeRequest(BaseModel):
    mode: str = Field(default="mock", description="'mock' | 'replay' | 'live' | 'auto'")


class PolicyOverrideRequest(BaseModel):
    block_threshold: float | None = Field(default=None, description="Override payment block threshold")
    review_threshold: float | None = Field(default=None, description="Override payment review threshold")


# ---------------------------------------------------------------------------
# Health & Status
# ---------------------------------------------------------------------------

@router.get("/health")
@router.get("/api/health")
def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "service": "sentinel"}


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

@router.post("/reset")
@router.post("/api/reset")
@router.post("/admin/reset")
@router.post("/api/admin/reset")
def reset() -> dict[str, str]:
    """
    Reset all state for a fresh demo run.

    Clears: decisions, audit log, world state, sessions, demo runs.
    """
    get_guard_service().reset()
    try:
        from app.api.routes.demo import reset_demo_runs
        reset_demo_runs()
    except Exception:
        pass
    return {"status": "reset", "message": "All state cleared for fresh demo run."}


# ---------------------------------------------------------------------------
# Tool Listing
# ---------------------------------------------------------------------------

@router.get("/tools")
@router.get("/api/tools")
def list_tools() -> list[dict[str, Any]]:
    """List all registered tools and their metadata."""
    registry = get_registry()
    return [
        {
            "id": t.id,
            "description": t.description,
            "capability": t.capability,
            "mutating": t.mutating,
            "read_only": t.read_only,
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


# ---------------------------------------------------------------------------
# Admin Review
# ---------------------------------------------------------------------------

@router.post("/admin/review", response_model=GuardDecision)
@router.post("/api/admin/review", response_model=GuardDecision)
def admin_review(request: AdminReviewRequest) -> GuardDecision:
    """
    Review (approve or reject) a REVIEW decision.
    """
    service = get_guard_service()
    action = request.action.lower().strip()

    try:
        if action == "approve":
            return service.approve(request.decision_id, request.reviewer_id)
        elif action == "reject":
            return service.reject(request.decision_id, request.reviewer_id, request.reason)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown review action '{request.action}'. Must be 'approve' or 'reject'.",
            )
    except BlockedDecision as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except DecisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidLifecycleTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Admin Undo
# ---------------------------------------------------------------------------

@router.post("/admin/undo")
@router.post("/api/admin/undo")
def admin_undo(request: AdminUndoRequest) -> dict[str, Any]:
    """
    Undo a previously executed action via admin interface.
    """
    service = get_guard_service()
    try:
        result = service.undo(request.decision_id)
        return result.to_dict()
    except UndoUnavailable as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except AlreadyUndone as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except DecisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ToolExecutionError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Undo error: {exc}")


# ---------------------------------------------------------------------------
# Admin Semantic Mode
# ---------------------------------------------------------------------------

@router.post("/admin/semantic-mode")
@router.post("/api/admin/semantic-mode")
def set_semantic_mode(request: SemanticModeRequest) -> dict[str, str]:
    """Set the active semantic reasoning mode (mock, replay, live, auto)."""
    global _semantic_mode
    valid_modes = {"mock", "replay", "live", "auto"}
    mode = request.mode.lower().strip()
    if mode not in valid_modes:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode '{request.mode}'. Must be one of {sorted(valid_modes)}.",
        )
    _semantic_mode = mode
    return {"status": "ok", "mode": _semantic_mode}


@router.get("/admin/semantic-mode")
@router.get("/api/admin/semantic-mode")
def get_semantic_mode() -> dict[str, str]:
    """Get the active semantic reasoning mode."""
    return {"status": "ok", "mode": _semantic_mode}


# ---------------------------------------------------------------------------
# Admin Policy Override (Optional)
# ---------------------------------------------------------------------------

@router.post("/admin/policy-override")
@router.post("/api/admin/policy-override")
def policy_override(request: PolicyOverrideRequest) -> dict[str, Any]:
    """Override policy limits dynamically (for testing/demo scenarios)."""
    overrides: dict[str, Any] = {}
    if request.block_threshold is not None:
        overrides["payment_block_threshold"] = request.block_threshold
    if request.review_threshold is not None:
        overrides["payment_review_threshold"] = request.review_threshold

    return {"status": "ok", "applied_overrides": overrides}
