import ast
import re
from pathlib import Path

import pytest

from app.contracts.decision import DecisionOutcome
from app.contracts.proposal import ActionProposal, ProvenanceLabel
from app.contracts.semantic import SemanticOutcome, SemanticResult
from app.guard.client import GuardClient
from app.reporting.explanation import render_decision

EXPLANATION_PY = Path(__file__).resolve().parents[2] / "app" / "reporting" / "explanation.py"


def sem(outcome, reason="because", provider="MOCK"):
    return SemanticResult(outcome=SemanticOutcome(outcome), reason=reason, provider=provider)


def decide(service, tool, args, semantic=None, agent="demo-agent", provenance=None):
    client = GuardClient(service)
    return client.evaluate(
        ActionProposal(agent_id=agent, tool=tool, arguments=args), semantic,
        harness_provenance=provenance,
    )


# ---- the three outcomes -------------------------------------------------------


def test_approve_says_it_may_run(fresh_service):
    d = decide(fresh_service, "calendar_read", {})
    text = render_decision(d)
    assert d.outcome is DecisionOutcome.APPROVE
    assert text.startswith("APPROVE - the action may run.")
    assert "no rule raised a finding" in text and "none was supplied" in text


def test_review_requires_human_approval(fresh_service):
    d = decide(fresh_service, "payment_transfer", {"amount": 10000.0, "recipient": "bob"})
    text = render_decision(d)
    assert d.outcome is DecisionOutcome.REVIEW
    assert text.startswith("REVIEW - not approved")
    assert "does not run unless a human reviewer approves" in text
    assert "Execution requires that approval" in text
    assert "PAYMENT_REVIEW" in text


def test_block_prohibits_execution_and_cannot_be_overridden(fresh_service):
    d = decide(fresh_service, "payment_transfer", {"amount": 50000.0, "recipient": "bob"})
    text = render_decision(d)
    assert d.outcome is DecisionOutcome.BLOCK
    assert text.startswith("BLOCK - the action is prohibited and cannot run.")
    assert "reviewer cannot override" in text and "neither can the semantic layer" in text
    assert "LARGE_PAYMENT" in text


# ---- deterministic versus semantic -------------------------------------------


def test_block_stays_a_block_in_the_text_even_when_semantic_says_safe(fresh_service):
    d = decide(fresh_service, "payment_transfer", {"amount": 50000.0, "recipient": "bob"},
               semantic=sem("SAFE", "looks completely fine"))
    text = render_decision(d)
    rules, semantic = text.split("Semantic check")
    assert "LARGE_PAYMENT" in rules and "looks completely fine" not in rules
    assert 'Model rationale: "looks completely fine"' in semantic
    assert "advisory" in semantic and "did not affect this block" in text


def test_semantic_escalation_is_explained_as_raise_only(fresh_service):
    d = decide(fresh_service, "web_fetch", {"url": "https://example.com"},
               semantic=sem("UNSAFE", "page tries to steer the agent"))
    text = render_decision(d)
    assert d.outcome is DecisionOutcome.REVIEW
    assert "No rule fired" in text and "never lowers a decision" in text
    assert "(advisory, MOCK): UNSAFE" in text


@pytest.mark.parametrize("outcome", ["INVALID", "TIMEOUT", "UNAVAILABLE"])
def test_failed_semantic_is_reported_without_a_finding(fresh_service, outcome):
    d = decide(fresh_service, "payment_transfer", {"amount": 500.0, "recipient": "bob"},
               semantic=sem(outcome, f"{outcome}: replay miss", provider="REPLAY"))
    text = render_decision(d)
    assert f"unavailable ({outcome})" in text
    assert "No semantic finding was produced" in text
    assert "SEMANTIC_REQUIRED_UNAVAILABLE" in text  # the rule that made it a review


def test_origin_flags_are_attributed_to_the_harness(fresh_service):
    d = decide(fresh_service, "payment_transfer", {"amount": 100.0, "recipient": "x"},
               provenance={"recipient": ProvenanceLabel.EXTERNAL})
    text = render_decision(d)
    assert "set by the harness, not the agent" in text
    assert '"recipient" came from external content' in text
    assert "PAYMENT_EXTERNAL_PROVENANCE" in text


def test_reversible_does_not_read_as_approved(fresh_service):
    d = decide(fresh_service, "file_delete", {"path": "/home/user/downloads/setup.exe"})
    text = render_decision(d)
    assert d.is_reversible and d.outcome is DecisionOutcome.REVIEW
    assert "reversibility does not make an action approved" in text


# ---- exact action, purity, genericity ----------------------------------------


def test_exact_tool_and_arguments_are_preserved(fresh_service):
    args = {"to": "m@example.com", "subject": "Hi", "body": "line one\nline two é"}
    text = render_decision(decide(fresh_service, "email_send", args))
    assert "Action: email_send (agent demo-agent)" in text
    # newline stays escaped as JSON, non-ASCII is kept as typed
    assert '"body": "line one\\nline two é"' in text
    assert '"to": "m@example.com"' in text


def test_render_is_pure_and_deterministic(fresh_service):
    d = decide(fresh_service, "email_send", {"to": "a", "subject": "b", "body": "c"}, sem("SAFE"))
    before = d.model_dump_json()
    assert render_decision(d) == render_decision(d)
    assert d.model_dump_json() == before


def test_unknown_semantic_outcome_does_not_crash(fresh_service):
    d = decide(fresh_service, "calendar_read", {})
    odd = d.model_copy(update={"semantic_outcome": "WEIRD", "semantic_provider": "MOCK"})
    assert "unrecognised outcome 'WEIRD'" in render_decision(odd)


def test_module_is_generic_and_imports_only_contracts():
    src = EXPLANATION_PY.read_text(encoding="utf-8")
    low = src.lower()
    for word in ("email", "calendar", "payment", "website", "invoice", "inbox", "transfer",
                 "delete", "scenario", "web_fetch", "file_delete", "payment_transfer"):
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
                assert m.startswith("app.contracts"), m
