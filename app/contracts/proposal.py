"""Agent input and trusted context. Imports only from common."""

from typing import Any

from pydantic import Field, field_validator

from app.contracts.common import ProvenanceLabel, StrictModel


class ActionProposal(StrictModel):
    """The only thing the untrusted agent may submit."""

    tool: str
    args: dict[str, Any]
    justification: str = Field(default="", max_length=500)


class ObservedItem(StrictModel):
    """Content recorded by the trusted harness. The text stays untrusted."""

    item_id: str
    label: ProvenanceLabel
    text: str
    source_tool: str | None = None

    @field_validator("label")
    @classmethod
    def _label_not_user(cls, v: ProvenanceLabel) -> ProvenanceLabel:
        if v is ProvenanceLabel.USER:
            raise ValueError("USER is not a label for observed content")
        return v


class TrustedContext(StrictModel):
    """Supplied by the trusted harness, never by the agent."""

    agent_id: str
    session_id: str
    user_task: str
    observed_content: list[ObservedItem] = Field(default_factory=list)


class EvaluateRequest(StrictModel):
    proposal: ActionProposal
    trusted: TrustedContext


class CanonicalAction(StrictModel):
    """Produced by intake; this is what gets stored, shown and executed."""

    tool_id: str
    args: dict[str, Any]
    justification: str
    agent_id: str
    session_id: str
    user_task: str
