"""Template explanations for an already-computed GuardDecision.

Pure text: nothing here decides anything. It reports the final outcome, what the
deterministic rules found, what the semantic layer said (kept apart and labelled
advisory), and what happens next. The model's own words are quoted as its
rationale and never mixed into the rule findings.
"""

import json

from app.contracts.decision import DecisionOutcome, GuardDecision
from app.contracts.proposal import ProvenanceLabel
from app.contracts.semantic import SemanticOutcome

_HEADLINE = {
    DecisionOutcome.APPROVE: "APPROVE - the action may run.",
    DecisionOutcome.REVIEW: (
        "REVIEW - not approved. The action does not run unless a human reviewer approves it."
    ),
    DecisionOutcome.BLOCK: "BLOCK - the action is prohibited and cannot run.",
}

_NEXT = {
    DecisionOutcome.APPROVE: "Next: the action may run.",
    DecisionOutcome.REVIEW: (
        "Next: a human reviewer must approve or reject it. Execution requires that approval."
    ),
    DecisionOutcome.BLOCK: (
        "Next: execution is prohibited. A reviewer cannot override this block, "
        "and neither can the semantic layer."
    ),
}

_FAILED = {SemanticOutcome.INVALID, SemanticOutcome.TIMEOUT, SemanticOutcome.UNAVAILABLE}


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _semantic_lines(d: GuardDecision) -> list[str]:
    if d.semantic_outcome is None:
        return ["Semantic check: none was supplied for this action."]
    provider = d.semantic_provider or "unknown provider"
    try:
        outcome = SemanticOutcome(d.semantic_outcome)
    except ValueError:
        return [f"Semantic check (advisory, {provider}): unrecognised outcome '{d.semantic_outcome}'."]
    if outcome in _FAILED:
        return [
            f"Semantic check (advisory, {provider}): unavailable ({outcome.value}). "
            "No semantic finding was produced.",
        ]
    lines = [f"Semantic check (advisory, {provider}): {outcome.value}."]
    if d.semantic_reason:
        lines.append(f'  Model rationale: "{d.semantic_reason}"')
    return lines


def render_decision(d: GuardDecision) -> str:
    """Explain a fully built GuardDecision."""
    a = d.canonical_action
    lines = [
        _HEADLINE[d.outcome],
        "",
        f"Action: {a.tool} (agent {a.agent_id})",
        f"Arguments: {_json(a.arguments)}",
        f"Risk: {d.risk_label} ({d.risk_score:.2f}). Reversible: {'yes' if d.is_reversible else 'no'}.",
        "",
        "Rules (deterministic):",
    ]
    if d.violations:
        lines += [f"  - {v.rule_id} [{v.severity}]: {v.message}" for v in d.violations]
    else:
        lines.append("  - no rule raised a finding")

    external = [n for n, p in a.arg_provenance.items() if p == ProvenanceLabel.EXTERNAL]
    if external:
        lines.append("Argument origin (set by the harness, not the agent):")
        lines += [f'  - "{n}" came from external content' for n in external]
    lines.append("")

    lines += _semantic_lines(d)
    if d.outcome is DecisionOutcome.BLOCK:
        lines.append("Semantic findings are advisory and did not affect this block.")
    elif d.outcome is DecisionOutcome.REVIEW and not d.violations:
        lines.append(
            "No rule fired, so this review comes from the risk score or from the "
            "semantic layer raising an approval to a review. It never lowers a decision."
        )
    if d.is_reversible and d.outcome is not DecisionOutcome.APPROVE:
        lines.append("Reversible, but reversibility does not make an action approved.")
    lines += ["", _NEXT[d.outcome]]
    return "\n".join(lines)
