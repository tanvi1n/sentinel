"""
api/routes/demo.py
==================
Demo and scenario runner API routes for SENTINEL.

Endpoints required by the project plan:
  GET  /api/scenarios             — list available demo scenarios
  POST /api/demo/start            — start a scenario run
  POST /api/demo/{run_id}/step    — evaluate next step in run
  POST /api/demo/{run_id}/execute — execute approved action for run

Owned by: Person 2
"""

from __future__ import annotations

import threading
import uuid
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.contracts.decision import DecisionLifecycle, DecisionOutcome, GuardDecision
from agent.provenance import origin_labels
from agent.scenario import load_scenarios
from app.contracts.proposal import ActionProposal, ProposalContext
from app.guard.service import get_guard_service
from app.semantic.bridge import advise

router = APIRouter()


# ---------------------------------------------------------------------------
# Scenario Definitions
# ---------------------------------------------------------------------------

def _load_scenarios() -> dict[str, dict[str, Any]]:
    """The demo scenarios, read from scenarios/*.json (the single source of truth).

    Each step keeps only data: the request, whether it may auto-execute, the
    mock semantic variant (used only in mock mode) and whether undo is expected.
    Provenance is not scripted; it is worked out from observed content at run time.
    """
    out: dict[str, dict[str, Any]] = {}
    for sc in load_scenarios():
        out[sc.id] = {
            "id": sc.id,
            "title": sc.title,
            "description": sc.description,
            "agent_id": sc.agent_id,
            "user_task": sc.user_task,
            "expected_verdict": sc.expected_verdict.value,
            "steps": [
                {
                    "proposal": {
                        "agent_id": sc.agent_id,
                        "tool": step.request.tool,
                        "arguments": dict(step.request.arguments),
                    },
                    "auto_execute": step.auto_execute,
                    "mock_semantic": step.mock_semantic,
                    "undo_expected": step.undo_expected,
                }
                for step in sc.steps
            ],
        }
    return out


SCENARIOS: dict[str, dict[str, Any]] = _load_scenarios()


# ---------------------------------------------------------------------------
# In-memory Demo Run State
# ---------------------------------------------------------------------------

class DemoRun:
    def __init__(self, run_id: str, scenario_id: str, scenario_data: dict[str, Any]) -> None:
        self.run_id = run_id
        self.scenario_id = scenario_id
        self.scenario_data = scenario_data
        self.current_step_index = 0
        self.step_decisions: list[str] = []
        # External content recorded by this trusted route from executed reads
        # (source -> text); shown to the semantic layer as quoted data.
        self.observed: dict[str, str] = {}
        self.status = "started"  # "started" | "in_progress" | "completed"


_runs: dict[str, DemoRun] = {}
_runs_lock = threading.Lock()


def get_demo_run(run_id: str) -> DemoRun:
    with _runs_lock:
        if run_id not in _runs:
            raise HTTPException(status_code=404, detail=f"Demo run '{run_id}' not found.")
        return _runs[run_id]


# ---------------------------------------------------------------------------
# Request / Response Schemas
# ---------------------------------------------------------------------------

class DemoStartRequest(BaseModel):
    scenario_id: str = Field(..., description="ID of the scenario to run")


class DemoStepResponse(BaseModel):
    run_id: str
    scenario_id: str
    step_index: int
    total_steps: int
    decision: GuardDecision
    can_execute: bool
    requires_review: bool
    is_blocked: bool
    undo_expected: bool = False


class DemoExecuteResponse(BaseModel):
    run_id: str
    decision_id: str
    execution_result: dict[str, Any]
    next_step: int
    total_steps: int
    status: str


# ---------------------------------------------------------------------------
# Route Implementations
# ---------------------------------------------------------------------------

@router.get("/scenarios")
@router.get("/api/scenarios")
def list_scenarios() -> list[dict[str, Any]]:
    """List all available demo scenarios."""
    return [
        {
            "id": s["id"],
            "title": s["title"],
            "description": s["description"],
            "agent_id": s["agent_id"],
            "user_task": s["user_task"],
            "expected_verdict": s["expected_verdict"],
            "step_count": len(s["steps"]),
        }
        for s in SCENARIOS.values()
    ]


@router.post("/demo/start")
@router.post("/api/demo/start")
def start_demo(request: DemoStartRequest) -> dict[str, Any]:
    """Start a demo scenario run."""
    scenario_id = request.scenario_id
    if scenario_id not in SCENARIOS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown scenario '{scenario_id}'. Available: {list(SCENARIOS.keys())}",
        )

    run_id = f"run-{uuid.uuid4().hex[:8]}"
    run = DemoRun(run_id, scenario_id, SCENARIOS[scenario_id])
    with _runs_lock:
        _runs[run_id] = run

    return {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "title": run.scenario_data["title"],
        "description": run.scenario_data["description"],
        "total_steps": len(run.scenario_data["steps"]),
        "current_step": 0,
        "status": "started",
    }


@router.post("/demo/{run_id}/step")
@router.post("/api/demo/{run_id}/step")
def step_demo(run_id: str) -> DemoStepResponse:
    """Evaluate the current step in a demo run."""
    run = get_demo_run(run_id)
    steps = run.scenario_data["steps"]

    if run.current_step_index >= len(steps):
        raise HTTPException(
            status_code=400,
            detail=f"Run '{run_id}' has already evaluated all {len(steps)} steps.",
        )

    step_data = steps[run.current_step_index]
    service = get_guard_service()

    # Build ActionProposal
    prop_data = step_data["proposal"]
    proposal = ActionProposal(
        agent_id=prop_data["agent_id"],
        tool=prop_data["tool"],
        arguments=prop_data.get("arguments", {}),
        context=ProposalContext(
            user_task=run.scenario_data["user_task"],
            session_id=run_id,
            observed_external_content=dict(run.observed),
        ),
    )

    # Trusted provenance: which arguments appear in content observed in this run
    # (or in the user's task). Worked out here, never taken from the request.
    harness_prov = origin_labels(
        proposal.arguments, run.scenario_data["user_task"], run.observed
    ) or None

    # Semantic result from the provider layer (mode set via /admin/semantic-mode).
    # A tool that requires a check always gets one; a failing provider yields
    # INVALID / TIMEOUT / UNAVAILABLE, which the guard turns into REVIEW.
    # "mock_semantic" only picks the mock variant and is ignored in other modes.
    semantic_result = advise(
        proposal, harness_prov, mock_mode=step_data.get("mock_semantic")
    )

    # Evaluate
    decision = service.evaluate(
        proposal,
        semantic_result=semantic_result,
        session_id=run_id,
        harness_provenance=harness_prov,
    )

    run.step_decisions.append(decision.decision_id)
    run.status = "in_progress"

    can_execute = (
        decision.outcome == DecisionOutcome.APPROVE
        or decision.lifecycle == DecisionLifecycle.APPROVED
    )
    requires_review = (
        decision.outcome == DecisionOutcome.REVIEW
        and decision.lifecycle != DecisionLifecycle.APPROVED
    )
    is_blocked = decision.outcome == DecisionOutcome.BLOCK

    return DemoStepResponse(
        run_id=run_id,
        scenario_id=run.scenario_id,
        step_index=run.current_step_index,
        total_steps=len(steps),
        decision=decision,
        can_execute=can_execute,
        requires_review=requires_review,
        is_blocked=is_blocked,
        undo_expected=step_data.get("undo_expected", False),
    )


@router.post("/demo/{run_id}/execute")
@router.post("/api/demo/{run_id}/execute")
def execute_demo_step(run_id: str) -> DemoExecuteResponse:
    """Execute the decision for the current step in a demo run."""
    run = get_demo_run(run_id)
    if not run.step_decisions:
        raise HTTPException(
            status_code=400,
            detail="No step evaluated yet. Call /step first.",
        )

    current_idx = run.current_step_index
    if current_idx >= len(run.step_decisions):
        raise HTTPException(
            status_code=400,
            detail="Current step decision not found.",
        )

    decision_id = run.step_decisions[current_idx]
    service = get_guard_service()

    try:
        exec_result = service.execute(decision_id, session_id=run_id)
    except Exception as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    # Record content a read tool returned, for later semantic checks.
    meta = getattr(exec_result, "metadata", None) or {}
    output = getattr(exec_result, "output", None)
    if meta.get("is_external_content") and isinstance(output, dict):
        run.observed[str(meta.get("url", "external"))] = str(output.get("content", ""))

    run.current_step_index += 1
    total = len(run.scenario_data["steps"])
    if run.current_step_index >= total:
        run.status = "completed"

    return DemoExecuteResponse(
        run_id=run_id,
        decision_id=decision_id,
        execution_result=exec_result.to_dict(),
        next_step=run.current_step_index,
        total_steps=total,
        status=run.status,
    )


def reset_demo_runs() -> None:
    """Clear all active demo runs."""
    with _runs_lock:
        _runs.clear()
