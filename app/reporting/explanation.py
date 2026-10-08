"""Template explanations for an already-computed decision.

Pure text: nothing here decides anything. It reports the final decision, what
the deterministic rules found, what the semantic layer said (kept apart and
labelled advisory), and what happens next. The model's rationale is quoted as
its own words, never mixed into the rule findings.
"""

import json
from collections.abc import Mapping, Sequence

from app.contracts import (
    Axis,
    AxisStatus,
    CanonicalAction,
    Decision,
    FindingSource,
    GuardDecision,
    PolicyHit,
    ProvenanceFlag,
    RiskLevel,
    SemanticResult,
    SemanticStatus,
)

_HEADLINE = {
    Decision.APPROVE: "APPROVE - the action may run.",
    Decision.REVIEW: (
        "REVIEW - not approved. The action does not run unless a human reviewer approves it."
    ),
    Decision.BLOCK: "BLOCK - the action is prohibited and cannot run.",
}

_NEXT = {
    Decision.APPROVE: "Next: the action may run.",
    Decision.REVIEW: "Next: a human reviewer must approve or reject it. Execution requires that approval.",
    Decision.BLOCK: (
        "Next: execution is prohibited. A reviewer cannot override this block, "
        "and neither can the semantic layer."
    ),
}


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _hit_line(hit: PolicyHit) -> str:
    return f"  - {hit.effect.value} {hit.id}: {hit.reason}"


def _semantic_section(semantic: SemanticResult, hits: Sequence[PolicyHit]) -> list[str]:
    if semantic.status is SemanticStatus.SKIPPED:
        lines = ["Semantic check: not required for this tool."]
    elif semantic.status is SemanticStatus.VALID and semantic.findings is not None:
        f = semantic.findings
        label = semantic.source.value + (f", {semantic.provider}" if semantic.provider else "")
        injection = "injection suspected" if f.injection_suspected else "no injection suspected"
        lines = [
            f"Semantic check (advisory, {label}): intent {f.intent_alignment.value}, "
            f"{injection}, ambiguity {f.ambiguity.value}.",
            f'  Model rationale: "{f.rationale}"',
        ]
    else:
        reason = f": {semantic.error}" if semantic.error else ""
        lines = [
            f"Semantic check (advisory): unavailable ({semantic.status.value}{reason}). "
            "No semantic finding was produced."
        ]
    lines.extend(_hit_line(h) for h in hits)
    return lines


def render(
    *,
    decision: Decision,
    deterministic_decision: Decision,
    canonical_action: CanonicalAction,
    semantic: SemanticResult,
    risk_level: RiskLevel | None = None,
    policies_triggered: Sequence[PolicyHit] = (),
    provenance_flags: Sequence[ProvenanceFlag] = (),
    axes: Mapping[str, Axis] | None = None,
) -> str:
    """Explain a decision that has already been made.

    Raises ValueError for a combination the pipeline can never produce (the
    final decision below the rules' decision, a BLOCK the rules did not make,
    or a semantic BLOCK finding), so a wrong explanation is never shown.
    """
    if decision < deterministic_decision:
        raise ValueError("final decision cannot be less severe than the rules' decision")
    if decision is Decision.BLOCK and deterministic_decision is not Decision.BLOCK:
        raise ValueError("a BLOCK must come from the deterministic rules")

    rule_hits = [h for h in policies_triggered if h.source is FindingSource.DETERMINISTIC]
    semantic_hits = [h for h in policies_triggered if h.source is FindingSource.SEMANTIC]
    if any(h.effect is Decision.BLOCK for h in semantic_hits):
        raise ValueError("a semantic finding cannot be a BLOCK")
    if deterministic_decision is not Decision.BLOCK and any(
        h.effect is Decision.BLOCK for h in rule_hits
    ):
        raise ValueError("a BLOCK rule hit requires a deterministic BLOCK")

    lines = [_HEADLINE[decision], ""]
    a = canonical_action
    lines += [
        f"Action: {a.tool_id}",
        f"Arguments: {_json(a.args)}",
        f"Justification: {a.justification or '(none given)'}",
    ]
    if risk_level is not None:
        lines.append(f"Risk: {risk_level}")
    lines.append("")

    # Deterministic findings: rules, then the origin check.
    lines.append(f"Rules (deterministic): rules alone gave {deterministic_decision.value}.")
    lines += [_hit_line(h) for h in rule_hits] or ["  - no rule raised a finding"]
    if provenance_flags:
        lines.append("Origin check (deterministic): arguments found in external content:")
        lines += [
            f'  - "{p.arg}" appears in {p.origin_label.value} content (item {p.item_id})'
            for p in provenance_flags
        ]
    lines.append("")

    # Semantic findings: advisory, separate.
    lines += _semantic_section(semantic, semantic_hits)
    if decision is Decision.BLOCK:
        lines.append("Semantic findings are advisory and did not affect this block.")
    elif decision > deterministic_decision:
        lines.append(
            f"The semantic layer raised the decision from {deterministic_decision.value} "
            f"to {decision.value}; it can raise a decision to REVIEW but never lower one."
        )
    lines.append("")

    if axes:
        lines.append("Checks: " + "; ".join(f"{k} {v.status.value}" for k, v in axes.items()))
        lines += [f"  - {k}: {v.reason}" for k, v in axes.items() if v.status is not AxisStatus.PASS]
        reversible = axes.get("reversible")
        if reversible is not None and reversible.status is AxisStatus.PASS and decision is not Decision.APPROVE:
            lines.append("Reversible, but reversibility does not make an action approved.")
        lines.append("")

    lines.append(_NEXT[decision])
    return "\n".join(lines)


def render_decision(d: GuardDecision) -> str:
    """Convenience: explain a fully built GuardDecision."""
    return render(
        decision=d.decision,
        deterministic_decision=d.deterministic_decision,
        canonical_action=d.canonical_action,
        semantic=d.semantic,
        risk_level=d.risk_level,
        policies_triggered=d.policies_triggered,
        provenance_flags=d.provenance_flags,
        axes={
            "safe": d.safe,
            "authorized": d.authorized,
            "explainable": d.explainable,
            "reversible": d.reversible,
        },
    )
