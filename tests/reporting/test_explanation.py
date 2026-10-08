import ast
import re
from pathlib import Path

import pytest

from app.contracts import (
    Axis,
    CanonicalAction,
    Decision,
    PolicyHit,
    ProvenanceFlag,
    RiskLevel,
    SemanticFindings,
    SemanticResult,
)
from app.reporting.explanation import render, render_decision
from tests.agent.helpers import make_decision

EXPLANATION_PY = Path(__file__).resolve().parents[2] / "app" / "reporting" / "explanation.py"
D = Decision


def action(args=None, tool="t.act", justification="because"):
    return CanonicalAction(
        tool_id=tool, args=args if args is not None else {"b": 2, "a": 1},
        justification=justification, agent_id="a", session_id="s", user_task="u",
    )


def valid(alignment="ALIGNED", injection=False, ambiguity="LOW", source="MOCK",
          provider="mock:aligned", rationale="the rationale text"):
    return SemanticResult(
        status="VALID", source=source, provider=provider,
        findings=SemanticFindings(
            intent_alignment=alignment, injection_suspected=injection,
            ambiguity=ambiguity, rationale=rationale),
    )


SKIPPED = SemanticResult(status="SKIPPED", source="NONE")


def hit(effect, id="r.one", source="DETERMINISTIC", reason="why"):
    return PolicyHit(id=id, effect=effect, reason=reason, source=source)


def run(decision, det=None, semantic=SKIPPED, **kw):
    return render(
        decision=decision, deterministic_decision=det or decision,
        canonical_action=kw.pop("canonical_action", action()), semantic=semantic, **kw,
    )


# ---- the three decisions ----------------------------------------------------


def test_approve_says_it_may_run():
    text = run(D.APPROVE)
    assert text.startswith("APPROVE")
    assert "may run" in text
    assert "human" not in text.split("Next:")[1]


def test_review_requires_human_approval():
    text = run(D.REVIEW, policies_triggered=[hit(D.REVIEW)])
    assert text.startswith("REVIEW - not approved")
    assert "does not run unless a human reviewer approves" in text
    assert "Execution requires that approval" in text


def test_block_prohibits_execution_and_cannot_be_overridden():
    text = run(D.BLOCK, policies_triggered=[hit(D.BLOCK)])
    assert text.startswith("BLOCK")
    assert "prohibited" in text and "cannot run" in text
    assert "reviewer cannot override" in text
    assert "neither can the semantic layer" in text


# ---- exact canonical action -------------------------------------------------


def test_exact_tool_and_arguments_are_preserved():
    paths = [f"tmp/old_{n:02d}.tmp" for n in range(1, 26)]
    text = run(D.REVIEW, canonical_action=action({"paths": paths, "permanent": False}, "x.remove"))
    assert "Action: x.remove" in text
    for p in paths:
        assert p in text  # every path, none summarised
    assert '"permanent": false' in text


def test_arguments_rendered_in_stable_order():
    a = run(D.APPROVE, canonical_action=action({"b": 2, "a": 1}))
    b = run(D.APPROVE, canonical_action=action({"a": 1, "b": 2}))
    assert a == b and 'Arguments: {"a": 1, "b": 2}' in a


def test_missing_justification_is_stated():
    assert "(none given)" in run(D.REVIEW, canonical_action=action(justification=""))


def test_non_ascii_arguments_kept():
    assert "é" in run(D.APPROVE, canonical_action=action({"k": "é"}))


# ---- deterministic vs semantic are distinct ---------------------------------


def test_rule_hits_and_semantic_hits_are_in_separate_sections():
    text = run(
        D.REVIEW,
        semantic=valid("MISALIGNED"),
        policies_triggered=[
            hit(D.REVIEW, "rule.bulk", "DETERMINISTIC", "too many"),
            hit(D.REVIEW, "sem.intent", "SEMANTIC", "does not fit the task"),
        ],
    )
    rules, semantic = text.split("Semantic check")
    assert "rule.bulk" in rules and "sem.intent" not in rules
    assert "sem.intent" in semantic and "rule.bulk" not in semantic
    assert "Rules (deterministic)" in rules and "advisory" in text


def test_semantic_rationale_is_quoted_and_labelled_with_source():
    text = run(D.REVIEW, det=D.APPROVE, semantic=valid("MISALIGNED", rationale="mismatch found",
                                                      source="LIVE", provider="qualcomm:m"))
    assert 'Model rationale: "mismatch found"' in text
    assert "advisory, LIVE, qualcomm:m" in text
    assert "intent MISALIGNED" in text


def test_semantic_raise_is_explained_as_raise_only():
    text = run(D.REVIEW, det=D.APPROVE, semantic=valid("MISALIGNED"))
    assert "rules alone gave APPROVE" in text
    assert "raised the decision from APPROVE to REVIEW" in text
    assert "never lower" in text


def test_no_raise_sentence_when_semantic_changed_nothing():
    text = run(D.REVIEW, semantic=valid("ALIGNED"), policies_triggered=[hit(D.REVIEW)])
    assert "raised the decision" not in text


def test_block_stands_even_if_semantic_says_aligned():
    text = run(D.BLOCK, semantic=valid("ALIGNED", provider="mock:compromised"),
               policies_triggered=[hit(D.BLOCK, "rule.limit")])
    assert "did not affect this block" in text
    assert "raised the decision" not in text
    assert "intent ALIGNED" in text  # reported honestly, but as advisory


def test_block_text_never_credits_the_semantic_layer():
    text = run(D.BLOCK, semantic=valid("MISALIGNED", injection=True),
               policies_triggered=[hit(D.BLOCK, "rule.limit")])
    assert "injection suspected" in text
    assert "did not affect this block" in text


@pytest.mark.parametrize("status", ["INVALID", "TIMEOUT", "UNAVAILABLE"])
def test_non_valid_semantic_is_reported_without_findings(status):
    sem = SemanticResult(status=status, source="NONE", error="replay miss")
    text = run(D.REVIEW, det=D.APPROVE, semantic=sem)
    assert f"unavailable ({status}: replay miss)" in text
    assert "No semantic finding was produced" in text
    assert "intent " not in text


def test_skipped_semantic_says_not_required():
    assert "not required for this tool" in run(D.APPROVE)


def test_provenance_flags_listed_as_deterministic():
    text = run(D.BLOCK, policies_triggered=[hit(D.BLOCK)],
               provenance_flags=[ProvenanceFlag(arg="dest", origin_label="WEBSITE", item_id="obs-1")])
    section = text.split("Origin check (deterministic)")[1].split("Semantic check")[0]
    assert '"dest" appears in WEBSITE content (item obs-1)' in section
    assert "Origin check" not in run(D.APPROVE)


def test_no_rule_findings_stated():
    assert "no rule raised a finding" in run(D.APPROVE)


# ---- axes, risk -------------------------------------------------------------


AXES = {
    "safe": Axis(status="PASS", reason="ok"),
    "authorized": Axis(status="PASS", reason="ok"),
    "explainable": Axis(status="PASS", reason="ok"),
    "reversible": Axis(status="PASS", reason="can be undone"),
}


def test_reversible_pass_is_not_presented_as_approval():
    text = run(D.REVIEW, policies_triggered=[hit(D.REVIEW)], axes=AXES)
    assert "Checks: safe PASS; authorized PASS; explainable PASS; reversible PASS" in text
    assert "reversibility does not make an action approved" in text
    assert "reversibility does not make" not in run(D.APPROVE, axes=AXES)


def test_non_pass_axes_show_their_reason_and_risk_is_shown():
    axes = {**AXES, "reversible": Axis(status="WARN", reason="cannot be undone")}
    text = run(D.APPROVE, axes=axes, risk_level=RiskLevel.LOW)
    assert "reversible WARN" in text and "reversible: cannot be undone" in text
    assert "Risk: LOW" in text and "safe: ok" not in text


# ---- guard rails ------------------------------------------------------------


def test_impossible_combinations_are_refused():
    with pytest.raises(ValueError):
        run(D.APPROVE, det=D.REVIEW)  # semantic cannot lower
    with pytest.raises(ValueError):
        run(D.REVIEW, det=D.BLOCK)  # rule BLOCK cannot be weakened
    with pytest.raises(ValueError):
        run(D.BLOCK, det=D.APPROVE)  # semantic cannot produce BLOCK
    with pytest.raises(ValueError):
        run(D.BLOCK, det=D.REVIEW)


def test_render_is_pure_and_deterministic():
    kwargs = dict(
        decision=D.REVIEW, deterministic_decision=D.REVIEW,
        canonical_action=action(), semantic=valid(),
        policies_triggered=[hit(D.REVIEW)],
    )
    before = repr(kwargs)
    assert render(**kwargs) == render(**kwargs)
    assert repr(kwargs) == before


def test_render_decision_matches_render():
    d = make_decision("REVIEW", "PENDING_REVIEW", False)
    expected = render(
        decision=d.decision, deterministic_decision=d.deterministic_decision,
        canonical_action=d.canonical_action, semantic=d.semantic, risk_level=d.risk_level,
        axes={"safe": d.safe, "authorized": d.authorized, "explainable": d.explainable,
              "reversible": d.reversible},
    )
    assert render_decision(d) == expected


def test_module_is_generic_and_imports_only_contracts():
    src = EXPLANATION_PY.read_text(encoding="utf-8")
    low = src.lower()
    for word in ("email", "calendar", "payment", "website", "invoice", "inbox", "transfer",
                 "delete", "scenario", "web.fetch", "file.delete", "payment.transfer"):
        assert word not in low, word
    assert not re.search(r"\bfile\b", low)
    for node in ast.walk(ast.parse(src)):
        mods = []
        if isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        elif isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        for m in mods:
            if m == "app" or m.startswith("app."):
                assert m == "app.contracts", m


def test_semantic_block_finding_is_refused():
    with pytest.raises(ValueError):
        run(D.REVIEW, policies_triggered=[hit(D.BLOCK, "sem.x", "SEMANTIC")])


def test_block_rule_hit_requires_a_deterministic_block():
    with pytest.raises(ValueError):
        run(D.REVIEW, policies_triggered=[hit(D.BLOCK, "rule.x", "DETERMINISTIC")])
    assert run(D.BLOCK, policies_triggered=[hit(D.BLOCK, "rule.x")]).startswith("BLOCK")


def test_approve_headline_makes_no_claim_about_other_checks():
    assert run(D.APPROVE).splitlines()[0] == "APPROVE - the action may run."


def test_plain_string_risk_and_non_json_args_do_not_crash():
    text = run(D.APPROVE, risk_level="LOW", canonical_action=action({"k": {1, 2}}))
    assert "Risk: LOW" in text and "Action: t.act" in text
