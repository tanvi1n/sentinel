"""Adapter between the semantic layer and the backend's contracts.

This is the only module in app/semantic/ that knows the guard. It does two
things and decides nothing:

1. builds the model's input from a proposal, using only trusted facts (the
   registry's tool description, the normalized arguments, the user task and
   observed content recorded by the harness);
2. converts a provider's result into the guard's SemanticResult.

What the guard does with that result is combine()'s job. A semantic result can
at most escalate APPROVE to REVIEW; this module cannot express BLOCK because
the guard's SemanticOutcome has no such value. A tool that requires a semantic
check always gets a result from here (a failure becomes INVALID, TIMEOUT or
UNAVAILABLE), so a missing or broken provider fails closed to REVIEW instead of
being skipped.
"""

from app.contracts.proposal import ActionProposal, ProvenanceLabel
from app.contracts.semantic import SemanticOutcome, SemanticResult
from app.guard.intake import process_proposal
from app.guard.registry import ToolRegistry, get_registry
from app.semantic import factory
from app.semantic.base import SemanticReasoner, build_semantic_context
from app.semantic.models import (
    Ambiguity,
    ContentLabel,
    IntentAlignment,
    ProviderResult,
    SemanticContext,
    SemanticFlaggedArg,
    SemanticObservedItem,
    SemanticSource,
    SemanticStatus,
)

# Guard-side provider names (see app.contracts.semantic.SemanticResult).
_PROVIDER_FOR_SOURCE = {
    SemanticSource.LIVE: "QUALCOMM",
    SemanticSource.REPLAY: "REPLAY",
    SemanticSource.MOCK: "MOCK",
}
_NON_VALID_OUTCOME = {
    SemanticStatus.INVALID: SemanticOutcome.INVALID,
    SemanticStatus.TIMEOUT: SemanticOutcome.TIMEOUT,
    SemanticStatus.UNAVAILABLE: SemanticOutcome.UNAVAILABLE,
    # A reasoner is only asked for tools that require a check, so "skipped"
    # there means no check happened: treat it as unavailable.
    SemanticStatus.SKIPPED: SemanticOutcome.UNAVAILABLE,
}


def _provider_name(result: ProviderResult) -> str:
    """Truthful guard-side provider label, even for failures."""
    if result.source in _PROVIDER_FOR_SOURCE:
        return _PROVIDER_FOR_SOURCE[result.source]
    prefix = (result.provider or "").split(":", 1)[0].lower()
    return {"qualcomm": "QUALCOMM", "replay": "REPLAY", "mock": "MOCK"}.get(prefix, "NONE")


def to_guard_result(result: ProviderResult) -> SemanticResult:
    """Convert a provider's result to the guard's contract.

    VALID findings map to SAFE, SUSPICIOUS or UNSAFE. Anything that is not a
    valid answer maps to INVALID, TIMEOUT or UNAVAILABLE.
    """
    extra = {
        "source": result.source.value,
        "provider_detail": result.provider,
        "latency_ms": result.latency_ms,
        "prompt_version": result.prompt_version,
        "recorded_at": result.recorded_at,
    }
    provider = _provider_name(result)

    if result.status is not SemanticStatus.VALID or result.findings is None:
        outcome = _NON_VALID_OUTCOME[result.status]
        reason = f"{result.status.value}: {result.error or 'no semantic result'}"
        return SemanticResult(
            outcome=outcome, reason=reason, provider=provider, flags=[], **extra
        )

    f = result.findings
    flags: list[str] = []
    if f.injection_suspected:
        flags.append("injection_suspected")
    if f.intent_alignment is not IntentAlignment.ALIGNED:
        flags.append(f"intent_{f.intent_alignment.value.lower()}")
    if f.ambiguity is Ambiguity.HIGH:
        flags.append("ambiguity_high")

    if f.injection_suspected or f.intent_alignment is IntentAlignment.MISALIGNED:
        outcome = SemanticOutcome.UNSAFE
    elif f.intent_alignment is IntentAlignment.SUSPICIOUS or f.ambiguity is Ambiguity.HIGH:
        outcome = SemanticOutcome.SUSPICIOUS
    else:
        outcome = SemanticOutcome.SAFE
    return SemanticResult(
        outcome=outcome, reason=f.rationale, provider=provider, flags=flags, **extra
    )


def unavailable_result(error: str) -> SemanticResult:
    """A fail-closed result for when no check could be run."""
    return SemanticResult(
        outcome=SemanticOutcome.UNAVAILABLE,
        reason=f"UNAVAILABLE: {error}",
        provider="NONE",
        flags=[],
        source="NONE",
    )


def context_for_proposal(
    proposal: ActionProposal,
    registry: ToolRegistry | None = None,
) -> SemanticContext:
    """Build the model input for a proposal (validated and normalized first).

    `proposal` should already carry the harness-supplied provenance in
    `arg_provenance`, exactly as the guard will see it. Intake errors (unknown
    tool, bad argument, path traversal) propagate, as they would from the guard.
    """
    registry = registry or get_registry()
    tool_def = registry.get(proposal.tool)
    canonical = process_proposal(proposal, registry)

    observed = [
        SemanticObservedItem(
            item_id=source, label=ContentLabel.EXTERNAL, text=str(text)
        )
        for source, text in proposal.context.observed_external_content.items()
    ]
    flagged = [
        SemanticFlaggedArg(arg=arg, origin_label=ContentLabel.EXTERNAL)
        for arg, label in canonical.arg_provenance.items()
        if label == ProvenanceLabel.EXTERNAL
    ]
    return build_semantic_context(
        tool_id=tool_def.id,
        tool_description=tool_def.description,
        arguments=canonical.arguments,
        user_task=proposal.context.user_task or "",
        observed_content=observed,
        flagged_args=flagged,
    )


def advise(
    proposal: ActionProposal,
    harness_provenance: dict[str, str] | None = None,
    *,
    reasoner: SemanticReasoner | None = None,
    mock_mode: str | None = None,
    registry: ToolRegistry | None = None,
) -> SemanticResult | None:
    """The semantic result to hand to GuardClient.evaluate for this proposal.

    Returns None for a tool that does not require a semantic check (nothing is
    sent to any model). For a tool that does, always returns a result: the
    provider's answer, or INVALID / TIMEOUT / UNAVAILABLE if it fails.

    `harness_provenance` is the same trusted map passed to the guard.
    `mock_mode` picks a mock variant for this call and only matters in mock mode.
    """
    registry = registry or get_registry()
    if not registry.get(proposal.tool).requires_semantic:
        return None
    if harness_provenance:
        proposal = proposal.model_copy(update={"arg_provenance": harness_provenance})

    context = context_for_proposal(proposal, registry)  # intake errors propagate
    try:
        chosen = reasoner or factory.get_reasoner(mock_mode)
        return to_guard_result(chosen.analyze(context))
    except Exception as exc:  # noqa: BLE001 - fail closed, never skip the check
        return unavailable_result(type(exc).__name__)
