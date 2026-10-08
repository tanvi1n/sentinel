"""
contracts/semantic.py
=====================
SemanticResult — the data contract between Person 1's semantic layer
and Person 2's guardrail engine.

OWNERSHIP:
  Person 1 OWNS the semantic implementation (semantic providers,
  MockReasoner, QualcommReasoner, ReplayReasoner).

  Person 2 OWNS this contract definition and consumes SemanticResult.

  DO NOT add provider logic here.
  DO NOT modify the provider behavior.
  This file defines only the data shape that both sides agree on.

IMPORTANT: Person 2's engine treats semantic results as ADVISORY.
  Semantic findings can raise APPROVE → REVIEW.
  Semantic findings can NEVER lower BLOCK → anything else.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Semantic outcome values
# ---------------------------------------------------------------------------

class SemanticOutcome(str, enum.Enum):
    """
    The possible outcomes from the semantic reasoning layer.

    SAFE        : semantic analysis found no concerns.
    SUSPICIOUS  : semantic analysis flagged something worth reviewing.
    UNSAFE      : semantic analysis strongly objects (advisory escalation).
    INVALID     : the request was malformed or could not be reasoned about.
    TIMEOUT     : the semantic provider timed out.
    UNAVAILABLE : the semantic provider is not reachable.
    """
    SAFE        = "SAFE"
    SUSPICIOUS  = "SUSPICIOUS"
    UNSAFE      = "UNSAFE"
    INVALID     = "INVALID"
    TIMEOUT     = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"


# ---------------------------------------------------------------------------
# SemanticResult — the shared data contract
# ---------------------------------------------------------------------------

class SemanticResult(BaseModel):
    """
    The result of semantic / LLM reasoning about a proposed action.

    Produced by: Person 1's semantic provider (Mock, Replay, or Qualcomm).
    Consumed by: Person 2's combine() function.

    Advisory role:
      - SAFE        → no escalation needed
      - SUSPICIOUS  → engine may escalate APPROVE → REVIEW
      - UNSAFE      → engine may escalate APPROVE → REVIEW
      - INVALID / TIMEOUT / UNAVAILABLE
                    → if semantic is required for this tool,
                       engine moves to REVIEW (not APPROVE)

    Invariant enforced in combine():
      SemanticResult can NEVER cause a BLOCK to become REVIEW or APPROVE.
    """

    outcome: SemanticOutcome = Field(
        ...,
        description="High-level outcome from the semantic reasoning layer.",
    )
    reason: str = Field(
        default="",
        description="Human-readable explanation of the semantic result.",
    )
    provider: str = Field(
        default="MOCK",
        description="Which provider produced this: MOCK | REPLAY | QUALCOMM",
    )
    # Optional: provider-specific confidence score in [0, 1]
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Provider confidence score (optional).",
    )
    # Optional: flags the provider wants to communicate
    flags: list[str] = Field(
        default_factory=list,
        description="Optional named flags from the semantic provider.",
    )

    model_config = {"extra": "allow"}   # allow providers to attach extra fields
