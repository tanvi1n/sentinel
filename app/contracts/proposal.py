"""
contracts/proposal.py
=====================
ActionProposal — what the agent wants to do.

The agent is UNTRUSTED. Fields such as permissions, risk, role, and
user_confirmation must NOT be accepted from the agent. This contract
carries only what the agent is allowed to supply.

Owned by: Person 2 (shared contract — Person 1 reads, Person 3 reads)
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Provenance labels for argument values
# ---------------------------------------------------------------------------

class ProvenanceLabel(str):
    """
    Semantic provenance labels carried alongside argument values.

    These tell the guardrail where an argument value came from so that
    provenance-based rules (e.g. prompt-injection detection) can fire
    correctly.
    """
    USER_TASK      = "user_task"          # came from the user's direct request
    AGENT_INTERNAL = "agent_internal"     # agent's own reasoning
    EXTERNAL       = "external_content"   # fetched from an untrusted external source
    SYSTEM         = "system"             # trusted system/harness-supplied value


# ---------------------------------------------------------------------------
# Context for the proposal
# ---------------------------------------------------------------------------

class ProposalContext(BaseModel):
    """
    Trusted context supplied by the harness (NOT the agent).

    The harness / trusted server sets:
      - user_task: the human's original task text
      - session_id: groups related actions together
      - observed_content_urls: which URLs were fetched in this session
        (used to evaluate provenance of arguments)
    """
    user_task: str | None = Field(
        default=None,
        description="The human operator's original task text.",
    )
    session_id: str | None = Field(
        default=None,
        description="Groups sequential actions for session-scoped rules.",
    )
    # Harness-supplied record of external content observed in this session.
    # Maps url → content snippet. Used for provenance checks.
    observed_external_content: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Harness-supplied map of url→content observed in this session. "
            "Used to evaluate argument provenance."
        ),
    )


# ---------------------------------------------------------------------------
# Argument with optional provenance
# ---------------------------------------------------------------------------

class ArgumentValue(BaseModel):
    """
    An argument value with optional provenance annotation.

    Provenance is supplied by the harness / trusted layer, NOT the agent.
    When the agent simply passes a value, provenance defaults to AGENT_INTERNAL.
    """
    value: Any
    provenance: str = ProvenanceLabel.AGENT_INTERNAL


# ---------------------------------------------------------------------------
# ActionProposal — the core untrusted input
# ---------------------------------------------------------------------------

class ActionProposal(BaseModel):
    """
    An action proposed by an AI agent.

    The agent supplies:
      - agent_id    : who is proposing (string identifier)
      - tool        : the tool it wants to invoke
      - arguments   : dict of argument name → value (plain Any)
      - arg_provenance : optional harness-supplied provenance for each arg
      - context     : optional trusted context from the harness

    The agent must NOT supply:
      - permissions
      - user_confirmation
      - role
      - risk
      - reversibility
      - observed_content (as trusted input)

    These are rejected at intake if present.
    """

    agent_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Identifier for the agent making the proposal.",
    )
    tool: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="The tool the agent wants to invoke.",
    )
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments to pass to the tool. All values are untrusted.",
    )
    # Harness may annotate argument provenance separately; agent cannot.
    arg_provenance: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Harness-supplied provenance annotations for argument values. "
            "Keys are argument names; values are ProvenanceLabel strings."
        ),
    )
    context: ProposalContext = Field(
        default_factory=ProposalContext,
        description="Harness-supplied context (not agent-supplied).",
    )

    # ----- Reject any agent-supplied trusted fields -------------------------

    @model_validator(mode="before")
    @classmethod
    def reject_trusted_fields(cls, values: dict[str, Any]) -> dict[str, Any]:
        """
        Reject proposals that carry agent-supplied trusted fields.

        If the agent tries to supply these, the proposal is malformed and
        must be rejected before any evaluation.
        """
        forbidden = {
            "permissions", "permission", "role", "user_confirmation",
            "confirmed", "risk", "risk_level", "reversibility",
            "is_reversible", "observed_content", "trusted_content",
        }
        found = forbidden & set(values.keys())
        if found:
            raise ValueError(
                f"ActionProposal contains forbidden agent-supplied trusted "
                f"fields: {sorted(found)}. These fields are not accepted from "
                f"the agent."
            )
        return values

    model_config = {"extra": "forbid"}
