"""Internal data shapes of the semantic layer.

These are private to app/semantic/. They describe what a reasoner is given
(SemanticContext) and what a provider produced (ProviderResult, with its
source, status and findings). The guard's own contract is
app.contracts.semantic.SemanticResult; app/semantic/bridge.py is the only place
that converts between the two. Nothing here imports the backend.
"""

from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SemanticStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    SKIPPED = "SKIPPED"


class SemanticSource(StrEnum):
    LIVE = "LIVE"
    REPLAY = "REPLAY"
    MOCK = "MOCK"
    NONE = "NONE"


class IntentAlignment(StrEnum):
    ALIGNED = "ALIGNED"
    SUSPICIOUS = "SUSPICIOUS"
    MISALIGNED = "MISALIGNED"


class Ambiguity(StrEnum):
    LOW = "LOW"
    HIGH = "HIGH"


class ContentLabel(StrEnum):
    """Where observed content came from, as recorded by the trusted side."""

    EXTERNAL = "EXTERNAL"  # untrusted content from outside the system
    TOOL_OUTPUT = "TOOL_OUTPUT"  # what a tool returned


class SemanticObservedItem(StrictModel):
    """Observed content as shown to the model; text truncated by the builder."""

    item_id: str
    label: ContentLabel
    text: str


class SemanticFlaggedArg(StrictModel):
    """An argument the trusted side marked as coming from external content."""

    arg: str
    origin_label: ContentLabel
    item_id: str | None = None


class SemanticContext(StrictModel):
    tool_id: str
    tool_description: str
    canonical_args: dict[str, Any]
    user_task: str
    justification: str = ""
    observed_content: list[SemanticObservedItem] = Field(default_factory=list)
    flagged_args: list[SemanticFlaggedArg] = Field(default_factory=list)


class SemanticFindings(StrictModel):
    intent_alignment: IntentAlignment
    injection_suspected: bool
    ambiguity: Ambiguity
    rationale: str = Field(max_length=400)


class ProviderResult(StrictModel):
    """What a provider produced, with its honest source label."""

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
