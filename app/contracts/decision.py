"""Decision contracts. Imports from common, proposal and semantic."""

from pydantic import Field, field_validator

from app.contracts.common import (
    AxisStatus,
    Decision,
    DecisionStatus,
    FindingSource,
    ProvenanceLabel,
    ReviewAction,
    RiskLevel,
    StrictModel,
)
from app.contracts.proposal import CanonicalAction
from app.contracts.semantic import SemanticResult


class Axis(StrictModel):
    status: AxisStatus
    reason: str


class PolicyHit(StrictModel):
    id: str
    effect: Decision
    reason: str
    source: FindingSource

    @field_validator("effect")
    @classmethod
    def _effect_is_review_or_block(cls, v: Decision) -> Decision:
        if v is Decision.APPROVE:
            raise ValueError("a policy hit has effect REVIEW or BLOCK")
        return v


class ProvenanceFlag(StrictModel):
    """Argument found in external content."""

    arg: str
    origin_label: ProvenanceLabel
    item_id: str


class ToolFactsView(StrictModel):
    """Registry facts shown to the reviewer."""

    description: str
    operation: str
    resource: str
    external_effect: bool
    effective_reversibility: str


class GuardDecision(StrictModel):
    decision_id: str
    decision: Decision
    deterministic_decision: Decision  # "rules alone", for display only
    status: DecisionStatus
    executable: bool  # True only for APPROVED
    risk_level: RiskLevel
    safe: Axis
    authorized: Axis
    explainable: Axis
    reversible: Axis
    policies_triggered: list[PolicyHit] = Field(default_factory=list)
    provenance_flags: list[ProvenanceFlag] = Field(default_factory=list)
    canonical_action: CanonicalAction
    tool_facts: ToolFactsView
    action_hash: str  # hash of canonical action plus observed snapshot
    semantic: SemanticResult
    explanation: str  # template text, never from the model
    created_at: str


class ReviewRequest(StrictModel):
    decision_id: str
    action: ReviewAction


class ReviewResult(StrictModel):
    decision_id: str
    status: DecisionStatus
    decision: Decision
