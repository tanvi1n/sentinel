"""Semantic layer contracts (owner: Risha). Imports only from common."""

from typing import Any, Self

from pydantic import Field, model_validator

from app.contracts.common import (
    Ambiguity,
    IntentAlignment,
    ProvenanceLabel,
    SemanticSource,
    SemanticStatus,
    StrictModel,
)


class SemanticObservedItem(StrictModel):
    """Observed content as shown to the model; text truncated by the builder."""

    item_id: str
    label: ProvenanceLabel
    text: str


class SemanticFlaggedArg(StrictModel):
    """An argument that appears in external content."""

    arg: str
    origin_label: ProvenanceLabel
    item_id: str


class SemanticContext(StrictModel):
    tool_id: str
    tool_description: str
    canonical_args: dict[str, Any]
    user_task: str
    justification: str
    observed_content: list[SemanticObservedItem] = Field(default_factory=list)
    flagged_args: list[SemanticFlaggedArg] = Field(default_factory=list)


class SemanticFindings(StrictModel):
    intent_alignment: IntentAlignment
    injection_suspected: bool
    ambiguity: Ambiguity
    rationale: str = Field(max_length=400)


class SemanticResult(StrictModel):
    status: SemanticStatus
    source: SemanticSource
    provider: str | None = None
    latency_ms: int | None = None
    recorded_at: str | None = None
    prompt_version: str | None = None
    findings: SemanticFindings | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _findings_only_when_valid(self) -> Self:
        if self.status is SemanticStatus.VALID and self.findings is None:
            raise ValueError("a VALID result must carry findings")
        if self.status is not SemanticStatus.VALID and self.findings is not None:
            raise ValueError("findings are present only when status is VALID")
        return self
