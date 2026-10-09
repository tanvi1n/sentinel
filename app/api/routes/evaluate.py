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
from app.guard.service import get_guard_service
from app.semantic.bridge import advise, unavailable_result

# Intake errors: the same proposal is re-checked (and the rejection audited) by
# the guard service, so the semantic step just stays out of the way.
_INTAKE_ERRORS = (
    InvalidProposal, UnknownTool, UnknownArgument, InvalidArgument,
    OversizedValue, PathTraversalError,
)

router = APIRouter()


class EvaluateRequest(BaseModel):
    """
    Request body for POST /evaluate.

    TRUST BOUNDARY NOTE:
    - agent_id, tool, arguments are agent-supplied (untrusted)
    - context is harness-supplied trusted context
    - semantic_result is NOT accepted here: the server runs the semantic check
      itself for every tool whose registry entry requires one. A caller could
      otherwise skip a required check by omitting the field, or relax it by
      sending a fabricated SAFE result. Sending the field returns 422, the same
      convention used for arg_provenance.
    - arg_provenance is NOT accepted here — it must come from the trusted
      harness via the GuardClient.evaluate() Python interface directly.
      Accepting provenance over the HTTP boundary would let any caller
      fabricate trusted provenance labels.

    Trusted in-process harness:
      Use GuardClient.evaluate(proposal, semantic_result, harness_provenance=...)
      with a semantic result from app.semantic.bridge.advise().
    """
    agent_id: str
    tool: str
    arguments: dict[str, Any] = {}
    # NOTE: arg_provenance is intentionally absent — agent cannot supply it.
    # The harness supplies provenance via GuardClient.evaluate(harness_provenance=...)
    context: ProposalContext = ProposalContext()
    session_id: str | None = None

    model_config = {"extra": "forbid"}


@router.post("/evaluate", response_model=GuardDecision)
def evaluate(request: EvaluateRequest) -> GuardDecision:
    """
    Evaluate an action proposal.

    Returns a GuardDecision with outcome APPROVE | REVIEW | BLOCK.

    arg_provenance cannot be supplied via this HTTP endpoint (trust boundary).
    Use GuardClient.evaluate() directly from the trusted harness to supply
    provenance for Scenario 4 (prompt injection detection).
    """
    service = get_guard_service()

    # Build ActionProposal — no arg_provenance from the agent
    try:
        proposal = ActionProposal(
            agent_id=request.agent_id,
            tool=request.tool,
            arguments=request.arguments,
            # arg_provenance left empty — harness must supply via GuardClient
            context=request.context,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Server-side semantic check (advisory). None for tools that do not require
    # one; a provider failure comes back as INVALID / TIMEOUT / UNAVAILABLE and
    # the guard turns that into REVIEW. It never sees the guard's verdict.
    try:
        semantic_result = advise(proposal)
    except _INTAKE_ERRORS:
        # The service repeats intake below and rejects (and audits) the proposal.
        # Fail closed anyway: if it ever accepted it, the check counts as unavailable.
        semantic_result = unavailable_result("semantic check could not run")

    try:
        decision = service.evaluate(
            proposal,
            semantic_result=semantic_result,
            session_id=request.session_id or request.context.session_id,
            # harness_provenance=None here — HTTP callers cannot supply it
        )
    except (InvalidProposal, UnknownTool, UnknownArgument, InvalidArgument,
            OversizedValue, PathTraversalError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Evaluation error: {exc}")

    return decision
