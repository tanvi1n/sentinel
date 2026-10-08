"""
api/routes/evaluate.py
======================
POST /evaluate — evaluate an action proposal.

Thin wrapper around GuardService.evaluate().
No business logic here.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any

from app.contracts.decision import GuardDecision
from app.contracts.errors import (
    InvalidProposal,
    UnknownTool,
    UnknownArgument,
    InvalidArgument,
    OversizedValue,
    PathTraversalError,
)
from app.contracts.proposal import ActionProposal, ProposalContext
from app.contracts.semantic import SemanticResult
from app.guard.service import get_guard_service

router = APIRouter()


class EvaluateRequest(BaseModel):
    """Request body for POST /evaluate."""
    agent_id: str
    tool: str
    arguments: dict[str, Any] = {}
    arg_provenance: dict[str, str] = {}
    context: ProposalContext = ProposalContext()
    semantic_result: SemanticResult | None = None
    session_id: str | None = None

    model_config = {"extra": "forbid"}


@router.post("/evaluate", response_model=GuardDecision)
def evaluate(request: EvaluateRequest) -> GuardDecision:
    """
    Evaluate an action proposal.

    Returns a GuardDecision with outcome APPROVE | REVIEW | BLOCK.
    """
    service = get_guard_service()

    # Build ActionProposal from request fields
    try:
        proposal = ActionProposal(
            agent_id=request.agent_id,
            tool=request.tool,
            arguments=request.arguments,
            arg_provenance=request.arg_provenance,
            context=request.context,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        decision = service.evaluate(
            proposal,
            semantic_result=request.semantic_result,
            session_id=request.session_id or request.context.session_id,
        )
    except (InvalidProposal, UnknownTool, UnknownArgument, InvalidArgument,
            OversizedValue, PathTraversalError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Evaluation error: {exc}")

    return decision
