"""Contract v0 tests: imports, dependency direction, strictness, constraints."""

import ast
import importlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts import (
    ActionProposal,
    AuditEvent,
    CanonicalAction,
    Decision,
    DecisionStatus,
    EvaluateRequest,
    ExecutionResult,
    GuardDecision,
    ObservedItem,
    PolicyHit,
    ReviewAction,
    ReviewRequest,
    ReviewResult,
    SemanticContext,
    SemanticFindings,
    SemanticResult,
    TrustedContext,
    WorldState,
)

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "app" / "contracts"
ORDER = ["common", "proposal", "semantic", "decision", "execution"]


# ---- imports and dependency direction ---------------------------------------


@pytest.mark.parametrize("name", ORDER + ["__init__"])
def test_module_imports(name):
    mod = "app.contracts" if name == "__init__" else f"app.contracts.{name}"
    importlib.import_module(mod)


@pytest.mark.parametrize("name", ORDER)
def test_imports_only_flow_downstream(name):
    allowed = {f"app.contracts.{m}" for m in ORDER[: ORDER.index(name)]}
    tree = ast.parse((CONTRACTS_DIR / f"{name}.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        elif isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        for m in mods:
            if m == "app" or m.startswith("app."):
                assert m in allowed, f"{name}.py must not import {m}"


# ---- builders ---------------------------------------------------------------


def findings(**kw):
    base = dict(
        intent_alignment="ALIGNED",
        injection_suspected=False,
        ambiguity="LOW",
        rationale="ok",
    )
    return {**base, **kw}


def canonical():
    return dict(
        tool_id="x.do",
        args={"a": 1},
        justification="why",
        agent_id="agent",
        session_id="s1",
        user_task="task",
    )


def axis(status="PASS"):
    return {"status": status, "reason": "r"}


def guard_decision(**kw):
    base = dict(
        decision_id="d1",
        decision="REVIEW",
        deterministic_decision="APPROVE",
        status="PENDING_REVIEW",
        executable=False,
        risk_level="LOW",
        safe=axis(),
        authorized=axis(),
        explainable=axis(),
        reversible=axis("WARN"),
        canonical_action=canonical(),
        tool_facts=dict(
            description="d",
            operation="o",
            resource="r",
            external_effect=True,
            effective_reversibility="irreversible",
        ),
        action_hash="abc",
        semantic=dict(status="SKIPPED", source="NONE"),
        explanation="e",
        created_at="2026-10-07T00:00:00Z",
    )
    return {**base, **kw}


# ---- proposal / trust boundary ----------------------------------------------


def test_proposal_minimal_and_defaults():
    p = ActionProposal(tool="t.x", args={})
    assert p.justification == ""


@pytest.mark.parametrize(
    "field",
    ["observed_content", "user_confirmation", "role", "permissions", "risk"],
)
def test_proposal_rejects_agent_supplied_extras(field):
    with pytest.raises(ValidationError):
        ActionProposal(tool="t.x", args={}, **{field: "x"})


def test_proposal_justification_max_500():
    ActionProposal(tool="t.x", args={}, justification="a" * 500)
    with pytest.raises(ValidationError):
        ActionProposal(tool="t.x", args={}, justification="a" * 501)


def test_proposal_requires_tool_and_args():
    with pytest.raises(ValidationError):
        ActionProposal(args={})
    with pytest.raises(ValidationError):
        ActionProposal(tool="t.x")


def test_trusted_context_defaults_and_extras():
    t = TrustedContext(agent_id="a", session_id="s", user_task="u")
    assert t.observed_content == []
    with pytest.raises(ValidationError):
        TrustedContext(agent_id="a", session_id="s", user_task="u", is_admin=True)


def test_trusted_context_requires_ids_and_task():
    for missing in ("agent_id", "session_id", "user_task"):
        data = dict(agent_id="a", session_id="s", user_task="u")
        del data[missing]
        with pytest.raises(ValidationError):
            TrustedContext(**data)


@pytest.mark.parametrize("label", ["EMAIL", "WEBSITE", "TOOL_OUTPUT", "OTHER_EXTERNAL"])
def test_observed_item_allowed_labels(label):
    assert ObservedItem(item_id="i", label=label, text="t").source_tool is None


def test_observed_item_rejects_user_and_unknown_label():
    with pytest.raises(ValidationError):
        ObservedItem(item_id="i", label="USER", text="t")
    with pytest.raises(ValidationError):
        ObservedItem(item_id="i", label="TRUSTED", text="t")


def test_evaluate_request_rejects_extras_at_every_level():
    good = dict(
        proposal=dict(tool="t.x", args={}),
        trusted=dict(agent_id="a", session_id="s", user_task="u"),
    )
    EvaluateRequest(**good)
    with pytest.raises(ValidationError):
        EvaluateRequest(**good, extra=1)
    bad = {**good, "proposal": {**good["proposal"], "observed_content": []}}
    with pytest.raises(ValidationError):
        EvaluateRequest(**bad)


def test_canonical_action_roundtrip_and_extras():
    c = CanonicalAction(**canonical())
    assert CanonicalAction.model_validate(c.model_dump()) == c
    with pytest.raises(ValidationError):
        CanonicalAction(**canonical(), risk="LOW")


# ---- semantic ---------------------------------------------------------------


def test_semantic_context_defaults():
    c = SemanticContext(
        tool_id="x", tool_description="d", canonical_args={},
        user_task="u", justification="j",
    )
    assert c.observed_content == [] and c.flagged_args == []


def test_semantic_findings_constraints():
    SemanticFindings(**findings(rationale="a" * 400))
    with pytest.raises(ValidationError):
        SemanticFindings(**findings(rationale="a" * 401))
    with pytest.raises(ValidationError):
        SemanticFindings(**findings(intent_alignment="MAYBE"))
    with pytest.raises(ValidationError):
        SemanticFindings(**findings(ambiguity="MEDIUM"))
    with pytest.raises(ValidationError):
        SemanticFindings(**findings(decision="BLOCK"))


def test_semantic_findings_missing_field():
    data = findings()
    del data["rationale"]
    with pytest.raises(ValidationError):
        SemanticFindings(**data)


def test_semantic_result_valid_requires_findings():
    r = SemanticResult(status="VALID", source="MOCK", findings=findings())
    assert r.findings.intent_alignment == "ALIGNED"
    with pytest.raises(ValidationError):
        SemanticResult(status="VALID", source="MOCK")


@pytest.mark.parametrize("status", ["INVALID", "TIMEOUT", "UNAVAILABLE", "SKIPPED"])
def test_semantic_result_non_valid_has_no_findings(status):
    SemanticResult(status=status, source="NONE", error="e")
    with pytest.raises(ValidationError):
        SemanticResult(status=status, source="NONE", findings=findings())


def test_semantic_result_enums_and_extras():
    with pytest.raises(ValidationError):
        SemanticResult(status="OK", source="NONE")
    with pytest.raises(ValidationError):
        SemanticResult(status="SKIPPED", source="CLOUD")
    with pytest.raises(ValidationError):
        SemanticResult(status="SKIPPED", source="NONE", decision="BLOCK")


# ---- decision ---------------------------------------------------------------


def test_guard_decision_valid_and_roundtrip():
    g = GuardDecision(**guard_decision())
    assert g.policies_triggered == [] and g.provenance_flags == []
    assert GuardDecision.model_validate_json(g.model_dump_json()) == g


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision", "DENY"),
        ("status", "DONE"),
        ("risk_level", "EXTREME"),
        ("safe", {"status": "OK", "reason": "r"}),
    ],
)
def test_guard_decision_rejects_bad_values(field, value):
    with pytest.raises(ValidationError):
        GuardDecision(**guard_decision(**{field: value}))


def test_guard_decision_rejects_extra_and_missing():
    with pytest.raises(ValidationError):
        GuardDecision(**guard_decision(approved_by_agent=True))
    data = guard_decision()
    del data["canonical_action"]
    with pytest.raises(ValidationError):
        GuardDecision(**data)


def test_policy_hit_effect_review_or_block_only():
    PolicyHit(id="p", effect="REVIEW", reason="r", source="DETERMINISTIC")
    PolicyHit(id="p", effect="BLOCK", reason="r", source="SEMANTIC")
    with pytest.raises(ValidationError):
        PolicyHit(id="p", effect="APPROVE", reason="r", source="DETERMINISTIC")
    with pytest.raises(ValidationError):
        PolicyHit(id="p", effect="REVIEW", reason="r", source="AGENT")


def test_review_action_is_lower_case():
    assert ReviewRequest(decision_id="d", action="approve").action is ReviewAction.approve
    with pytest.raises(ValidationError):
        ReviewRequest(decision_id="d", action="APPROVE")
    with pytest.raises(ValidationError):
        ReviewRequest(decision_id="d", action="approve", force=True)


def test_review_result():
    r = ReviewResult(decision_id="d", status="APPROVED", decision="REVIEW")
    assert r.status is DecisionStatus.APPROVED


def test_decision_severity_block_review_approve():
    assert Decision.BLOCK > Decision.REVIEW > Decision.APPROVE
    assert Decision.BLOCK.severity > Decision.REVIEW.severity > Decision.APPROVE.severity
    assert Decision.APPROVE < Decision.REVIEW < Decision.BLOCK
    assert Decision.REVIEW >= Decision.REVIEW and Decision.REVIEW <= Decision.REVIEW


def test_decision_max_never_picks_review_over_block():
    # Alphabetical string order would make REVIEW > BLOCK; severity must win.
    assert max(Decision.REVIEW, Decision.BLOCK) is Decision.BLOCK
    assert max(Decision.BLOCK, Decision.REVIEW) is Decision.BLOCK
    assert max(Decision.APPROVE, Decision.REVIEW) is Decision.REVIEW
    assert max([Decision.APPROVE, Decision.BLOCK, Decision.REVIEW]) is Decision.BLOCK
    assert sorted(Decision, reverse=True) == [
        Decision.BLOCK, Decision.REVIEW, Decision.APPROVE,
    ]


def test_decision_ordering_rejects_non_decisions():
    with pytest.raises(TypeError):
        Decision.BLOCK < "REVIEW"


def test_enum_members_match_reference():
    assert [d.value for d in Decision] == ["APPROVE", "REVIEW", "BLOCK"]
    assert [s.value for s in DecisionStatus] == [
        "PENDING_REVIEW", "APPROVED", "REJECTED", "BLOCKED", "EXECUTED", "UNDONE",
    ]


# ---- execution / audit ------------------------------------------------------


def test_execution_result_defaults():
    r = ExecutionResult(decision_id="d", status="EXECUTED", ok=True)
    assert r.diff == [] and r.undo_available is False and r.world_before is None


def test_execution_result_full_and_roundtrip():
    r = ExecutionResult(
        decision_id="d",
        status="EXECUTED",
        ok=True,
        output_text="hi",
        output_label="EMAIL",
        world_before={"files": [1]},
        world_after={"files": []},
        diff=[{"op": "remove", "path": "files/1", "detail": "gone"}],
        undo_available=True,
    )
    assert isinstance(r.world_before, WorldState)
    assert ExecutionResult.model_validate_json(r.model_dump_json()) == r


def test_execution_result_refusal_and_extras():
    r = ExecutionResult(
        decision_id="d", status="BLOCKED", ok=False,
        refused_reason="revalidation failed: limit",
    )
    assert r.refused_reason.startswith("revalidation failed")
    with pytest.raises(ValidationError):
        ExecutionResult(decision_id="d", status="DONE", ok=True)
    with pytest.raises(ValidationError):
        ExecutionResult(decision_id="d", status="EXECUTED", ok=True, output_label="USER2")
    with pytest.raises(ValidationError):
        ExecutionResult(decision_id="d", status="EXECUTED", ok=True, extra=1)


def test_world_state_serializes_as_plain_mapping():
    w = WorldState({"files": [], "calendar": []})
    assert w.model_dump() == {"files": [], "calendar": []}


def test_audit_event():
    e = AuditEvent(ts="t", event="evaluated", details={"provider": "mock:aligned"})
    assert e.decision_id is None
    with pytest.raises(ValidationError):
        AuditEvent(ts="t")
    with pytest.raises(ValidationError):
        AuditEvent(ts="t", event="e", secret="k")


# ---- genericity -------------------------------------------------------------


def test_contract_layer_has_no_domain_vocabulary():
    banned = ["email.", "calendar", "payment", "web.fetch", "file.delete"]
    for name in ORDER:
        src = (CONTRACTS_DIR / f"{name}.py").read_text(encoding="utf-8").lower()
        # WorldState's docstring documents the blueprint's demo keys; exempt it.
        if name == "execution":
            src = src.replace("files, trash, emails, payments and calendar", "")
        for word in banned:
            assert word not in src, f"{name}.py mentions {word!r}"
