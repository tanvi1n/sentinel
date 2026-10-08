"""
contracts/decision.py
=====================
GuardDecision and related enumerations.

This is the authoritative output of Sentinel's evaluation pipeline.

Owned by: Person 2 (shared contract — Person 1 reads, Person 3 reads)
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Decision outcome
# ---------------------------------------------------------------------------

class DecisionOutcome(str, enum.Enum):
    """
    The three possible outcomes of guardrail evaluation.

    APPROVE : action may proceed immediately.
    REVIEW  : action requires human approval before execution.
    BLOCK   : action is prohibited; cannot be approved or executed.
    """
    APPROVE = "APPROVE"
    REVIEW  = "REVIEW"
    BLOCK   = "BLOCK"


# ---------------------------------------------------------------------------
# Decision lifecycle state
# ---------------------------------------------------------------------------

class DecisionLifecycle(str, enum.Enum):
    """
    Lifecycle state of a stored decision record.

    EVALUATED        : freshly evaluated, not yet acted upon.
    PENDING_REVIEW   : waiting for human approval (outcome was REVIEW).
    APPROVED         : human approved a REVIEW decision.
    REJECTED         : human rejected a REVIEW decision.
    EXECUTING        : execution in progress (brief transitional state).
    EXECUTED         : successfully executed.
    EXECUTION_FAILED : execution attempted but failed.
    BLOCKED          : blocked at evaluation; terminal state.
    UNDONE           : execution was undone; world restored.
    REVALIDATION_FAILED : revalidation check blocked execution.
    INTEGRITY_FAILED : stored action was tampered with.
    """
    EVALUATED            = "EVALUATED"
    PENDING_REVIEW       = "PENDING_REVIEW"
    APPROVED             = "APPROVED"
    REJECTED             = "REJECTED"
    EXECUTING            = "EXECUTING"
    EXECUTED             = "EXECUTED"
    EXECUTION_FAILED     = "EXECUTION_FAILED"
    BLOCKED              = "BLOCKED"
    UNDONE               = "UNDONE"
    REVALIDATION_FAILED  = "REVALIDATION_FAILED"
    INTEGRITY_FAILED     = "INTEGRITY_FAILED"


# ---------------------------------------------------------------------------
# Violation / finding
# ---------------------------------------------------------------------------

class Violation(BaseModel):
    """A single deterministic rule violation or risk finding."""

    rule_id: str = Field(..., description="Identifier for the rule that fired.")
    message: str = Field(..., description="Human-readable description of the violation.")
    severity: str = Field(
        default="HIGH",
        description="CRITICAL | HIGH | MEDIUM | LOW",
    )

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Canonical action — the normalized, trusted representation
# ---------------------------------------------------------------------------

class CanonicalAction(BaseModel):
    """
    The normalized representation of what will actually be evaluated / executed.

    Built by intake.py from the untrusted ActionProposal.
    The integrity hash is computed from this plus trusted context.
    """
    agent_id: str
    tool: str
    arguments: dict[str, Any]          # normalized, validated arguments
    arg_provenance: dict[str, str]      # harness-supplied provenance
    integrity_hash: str                 # SHA-256 of canonical content

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# GuardDecision — the authoritative Sentinel verdict
# ---------------------------------------------------------------------------

class GuardDecision(BaseModel):
    """
    The authoritative output of the Sentinel guardrail evaluation.

    This is the primary contract between Person 2 and:
      - Person 1 (reads outcome + canonical action for harness)
      - Person 3 (renders in the UI)
      - The execution gateway (uses decision_id + canonical action)
    """

    decision_id: str = Field(
        ...,
        description="Unique identifier for this decision record.",
    )
    outcome: DecisionOutcome = Field(
        ...,
        description="APPROVE | REVIEW | BLOCK",
    )
    lifecycle: DecisionLifecycle = Field(
        ...,
        description="Current lifecycle state of this decision.",
    )

    # The action that was actually evaluated (not the raw proposal)
    canonical_action: CanonicalAction = Field(
        ...,
        description="Normalized, integrity-hashed action that was evaluated.",
    )

    # Risk
    risk_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Normalized risk score in [0, 1].",
    )
    risk_label: str = Field(
        default="LOW",
        description="CRITICAL | HIGH | MEDIUM | LOW",
    )

    # Violations / findings from deterministic rules
    violations: list[Violation] = Field(
        default_factory=list,
        description="Deterministic rule violations and risk findings.",
    )

    # Which rules fired
    rules_fired: list[str] = Field(
        default_factory=list,
        description="Identifiers of all deterministic rules that fired.",
    )

    # Semantic layer information (advisory only)
    semantic_outcome: str | None = Field(
        default=None,
        description=(
            "Outcome from the semantic/LLM layer: "
            "SAFE | SUSPICIOUS | UNSAFE | INVALID | TIMEOUT | UNAVAILABLE | None"
        ),
    )
    semantic_reason: str | None = Field(
        default=None,
        description="Explanation from the semantic layer.",
    )
    semantic_provider: str | None = Field(
        default=None,
        description="Which semantic provider produced this result: MOCK | REPLAY | QUALCOMM",
    )

    # Reversibility
    is_reversible: bool = Field(
        default=True,
        description="Whether the action supports undo.",
    )

    # Timestamps
    evaluated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the evaluation was completed.",
    )
    decided_at: datetime | None = Field(
        default=None,
        description="When a human review decision was made.",
    )
    executed_at: datetime | None = Field(
        default=None,
        description="When execution completed.",
    )

    # Human reviewer (populated after review)
    reviewer_id: str | None = Field(
        default=None,
        description="Identity of the human who approved/rejected.",
    )

    # Execution result (populated after execution)
    execution_result: dict[str, Any] | None = Field(
        default=None,
        description="Output from the tool execution.",
    )

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Valid lifecycle transitions
# ---------------------------------------------------------------------------

# Maps (current_lifecycle, action) → next_lifecycle
# Used by the service layer to enforce valid state machine transitions.
VALID_TRANSITIONS: dict[tuple[DecisionLifecycle, str], DecisionLifecycle] = {
    (DecisionLifecycle.EVALUATED,       "approve"):  DecisionLifecycle.APPROVED,
    (DecisionLifecycle.EVALUATED,       "reject"):   DecisionLifecycle.REJECTED,
    (DecisionLifecycle.PENDING_REVIEW,  "approve"):  DecisionLifecycle.APPROVED,
    (DecisionLifecycle.PENDING_REVIEW,  "reject"):   DecisionLifecycle.REJECTED,
    (DecisionLifecycle.EVALUATED,       "execute"):  DecisionLifecycle.EXECUTING,
    (DecisionLifecycle.APPROVED,        "execute"):  DecisionLifecycle.EXECUTING,
    (DecisionLifecycle.EXECUTING,       "complete"): DecisionLifecycle.EXECUTED,
    (DecisionLifecycle.EXECUTING,       "fail"):     DecisionLifecycle.EXECUTION_FAILED,
    (DecisionLifecycle.EXECUTED,        "undo"):     DecisionLifecycle.UNDONE,
}
