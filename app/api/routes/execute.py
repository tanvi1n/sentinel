"""
api/routes/execute.py
=====================
POST /execute  — execute an approved decision.
POST /undo     — undo an executed decision.
GET  /decisions — list all decisions.
GET  /decisions/{decision_id} — get a specific decision.

Thin wrappers around GuardService.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any

from app.contracts.decision import GuardDecision
from app.contracts.errors import (
    AlreadyExecuted,
    AlreadyUndone,
    BlockedDecision,
    DecisionNotFound,
    IntegrityError,
    InvalidLifecycleTransition,
    RevalidationFailed,
    RejectedDecision,
    ReviewRequired,
    ToolExecutionError,
    UndoUnavailable,
)
from app.guard.service import get_guard_service

router = APIRouter()


class ExecuteRequest(BaseModel):
    decision_id: str
    session_id: str | None = None


class UndoRequest(BaseModel):
    decision_id: str


@router.post("/execute")
def execute(request: ExecuteRequest) -> dict[str, Any]:
    """
    Execute an approved action.

    Returns the tool execution result.
    """
    service = get_guard_service()
    try:
        result = service.execute(request.decision_id, request.session_id)
        return result.to_dict()
    except BlockedDecision as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except RejectedDecision as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ReviewRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except AlreadyExecuted as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RevalidationFailed as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except DecisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ToolExecutionError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Execution error: {exc}")


@router.post("/undo")
def undo(request: UndoRequest) -> dict[str, Any]:
    """
    Undo a previously executed action.

    Only works for tools with undo support (e.g. file_delete).
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


@router.get("/decisions", response_model=list[GuardDecision])
def list_decisions() -> list[GuardDecision]:
    """List all stored decisions."""
    return get_guard_service().list_decisions()


@router.get("/decisions/{decision_id}", response_model=GuardDecision)
def get_decision(decision_id: str) -> GuardDecision:
    """Get a specific decision by ID."""
    try:
        return get_guard_service().get_decision(decision_id)
    except DecisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
