"""
api/routes/review.py
====================
POST /review/approve  — approve a REVIEW decision.
POST /review/reject   — reject a REVIEW decision.

Thin wrappers around GuardService.approve() / reject().
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.contracts.decision import GuardDecision
from app.contracts.errors import (
    BlockedDecision,
    DecisionNotFound,
    InvalidLifecycleTransition,
)
from app.guard.service import get_guard_service

router = APIRouter()


class ReviewApproveRequest(BaseModel):
    decision_id: str
    reviewer_id: str = "human-reviewer"


class ReviewRejectRequest(BaseModel):
    decision_id: str
    reviewer_id: str = "human-reviewer"
    reason: str = ""


@router.post("/review/approve", response_model=GuardDecision)
def approve(request: ReviewApproveRequest) -> GuardDecision:
    """Approve a REVIEW decision (human reviewer action)."""
    service = get_guard_service()
    try:
        return service.approve(request.decision_id, request.reviewer_id)
    except BlockedDecision as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except DecisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidLifecycleTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/review/reject", response_model=GuardDecision)
def reject(request: ReviewRejectRequest) -> GuardDecision:
    """Reject a REVIEW decision (human reviewer action)."""
    service = get_guard_service()
    try:
        return service.reject(request.decision_id, request.reviewer_id, request.reason)
    except DecisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidLifecycleTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/review/pending")
def list_pending() -> list[GuardDecision]:
    """List all decisions currently pending human review."""
    service = get_guard_service()
    return service.list_pending_review()
