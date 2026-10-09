import ast
import itertools
import re
from pathlib import Path

import pytest

from agent.harness import Harness, HarnessError, HarnessHalted, origin_labels
from agent.scripted_agent import AgentRequest, ScriptedAgent
from app.contracts.decision import DecisionLifecycle, DecisionOutcome
from app.contracts.proposal import ProvenanceLabel
from app.guard.client import GuardClient

HARNESS_PY = Path(__file__).resolve().parents[2] / "agent" / "harness.py"
PAGE = "https://malicious.example.com/inject"
CAL = {"tool": "calendar_read", "arguments": {}}
FETCH = {"tool": "web_fetch", "arguments": {"url": PAGE}}
PAY = {"tool": "payment_transfer", "arguments": {"amount": 100000, "recipient": "999888777"}}
DELETE = {"tool": "file_delete", "arguments": {"path": "/home/user/downloads/setup.exe"}}


def harness(service, steps, task="Reconcile invoices", agent_id="demo-agent", **kw):
    return Harness(GuardClient(service), ScriptedAgent(steps),
                   agent_id=agent_id, session_id="s-1", user_task=task, **kw)


class Recording:
    """Wraps the real client: records calls, optionally rewrites the decision."""

    def __init__(self, service, rewrite=None):
        self.inner = GuardClient(service)
        self.rewrite = rewrite
        self.evaluated, self.executed = [], []

    def evaluate(self, proposal, semantic_result=None, session_id=None, harness_provenance=None):
        self.evaluated.append((proposal, semantic_result, session_id, harness_provenance))
        d = self.inner.evaluate(proposal, semantic_result, session_id, harness_provenance)
        return d.model_copy(update=self.rewrite) if self.rewrite else d

    def execute(self, decision_id, session_id=None):
        self.executed.append(decision_id)
        return self.inner.execute(decision_id, session_id)


def with_recording(service, steps, **kw):
    rec = Recording(service, **kw)
    h = Harness(rec, ScriptedAgent(steps), agent_id="demo-agent", session_id="s-1", user_task="u")
    return rec, h


# ---- trusted fields come from the harness ---------------------------------------


def test_proposal_is_built_by_the_harness(fresh_service):
    rec = Recording(fresh_service)
    h = Harness(rec, ScriptedAgent([CAL]), agent_id="demo-agent", session_id="s-1", user_task="my task")
    h.step()
    proposal, _, session_id, _ = rec.evaluated[0]
    assert (proposal.agent_id, proposal.context.user_task, proposal.context.session_id) == (
        "demo-agent", "my task", "s-1")
    assert session_id == "s-1" and proposal.arg_provenance == {}


def test_an_argument_named_like_a_trusted_field_is_refused_by_the_guard(fresh_service):
    from app.contracts.errors import UnknownArgument

    h = harness(fresh_service, [{"tool": "calendar_read", "arguments": {"agent_id": "evil"}}])
    with pytest.raises(UnknownArgument):
        h.step()


@pytest.mark.parametrize("bad", [{"tool": "t", "arguments": {}}, "text", 42, object()])
def test_agent_returning_a_non_request_is_refused(fresh_service, bad):
    class BadAgent:
        def propose(self):
            return bad

    rec = Recording(fresh_service)
    h = Harness(rec, BadAgent(), agent_id="a", session_id="s", user_task="u")
    with pytest.raises(HarnessError):
        h.step()
    assert rec.evaluated == [] and rec.executed == []


def test_subclass_with_extra_attributes_is_stripped(fresh_service):
    class Evil(AgentRequest):
        agent_id: str = "evil"
        risk_level: str = "LOW"

    class EvilAgent:
        def propose(self):
            return Evil(tool="calendar_read", arguments={})

    rec = Recording(fresh_service)
    result = Harness(rec, EvilAgent(), agent_id="demo-agent", session_id="s", user_task="u").step()
    assert type(result.request) is AgentRequest
    assert rec.evaluated[0][0].agent_id == "demo-agent"


def test_step_returns_none_when_the_script_is_finished(fresh_service):
    assert harness(fresh_service, []).step() is None


# ---- auto-execution: a clean APPROVE only --------------------------------------


def test_approve_is_auto_executed(fresh_service):
    h = harness(fresh_service, [CAL])
    result = h.step()
    assert result.decision.outcome is DecisionOutcome.APPROVE
    assert result.execution["success"] is True and not h.halted


def test_a_script_can_forbid_auto_execution(fresh_service):
    rec, h = with_recording(fresh_service, [CAL])
    result = h.step(may_auto_execute=False)
    assert result.execution is None and rec.executed == [] and h.halted


@pytest.mark.parametrize(
    "outcome,lifecycle",
    list(itertools.product(list(DecisionOutcome), list(DecisionLifecycle))),
)
def test_only_a_clean_approve_is_ever_auto_executed(fresh_service, outcome, lifecycle):
    # Rewrite what the guard says, to prove the harness itself never runs anything else.
    rec, h = with_recording(fresh_service, [CAL],
                            rewrite={"outcome": outcome, "lifecycle": lifecycle})
    h.step()
    expected = outcome is DecisionOutcome.APPROVE and lifecycle is DecisionLifecycle.EVALUATED
    assert (len(rec.executed) == 1) is expected


def test_block_stops_the_run_and_nothing_executes(fresh_service):
    rec, h = with_recording(fresh_service, [PAY, CAL])
    result = h.step()
    assert result.decision.outcome is DecisionOutcome.BLOCK and h.halted
    assert rec.executed == [] and result.execution is None
    with pytest.raises(HarnessHalted):
        h.step()
    assert len(rec.evaluated) == 1  # the next request was never evaluated


def test_review_stops_the_run(fresh_service):
    rec, h = with_recording(fresh_service, [DELETE, CAL])
    result = h.step()
    assert result.decision.outcome is DecisionOutcome.REVIEW and rec.executed == [] and h.halted
    with pytest.raises(HarnessHalted):
        h.step()


def test_non_decision_reply_from_the_guard_is_refused():
    class Broken:
        executed = []

        def evaluate(self, *a, **k):
            return {"outcome": "APPROVE"}

        def execute(self, *a, **k):
            self.executed.append(1)

    client = Broken()
    h = Harness(client, ScriptedAgent([CAL]), agent_id="a", session_id="s", user_task="u",
                advisor=lambda *a, **k: None)
    with pytest.raises(HarnessError):
        h.step()
    assert client.executed == [] and h.halted


# ---- semantic result handed to the guard ----------------------------------------


def test_semantic_result_comes_from_the_advisor_and_reaches_the_guard(fresh_service):
    seen = {}

    def advisor(proposal, provenance, mock_mode=None):
        seen.update(tool=proposal.tool, provenance=provenance, mock=mock_mode)
        return None

    rec = Recording(fresh_service)
    Harness(rec, ScriptedAgent([CAL]), agent_id="demo-agent", session_id="s", user_task="u",
            advisor=advisor).step(mock_semantic="misaligned")
    assert seen == {"tool": "calendar_read", "provenance": None, "mock": "misaligned"}
    assert rec.evaluated[0][1] is None


def test_default_advisor_is_the_real_semantic_bridge(fresh_service):
    small_pay = {"tool": "payment_transfer", "arguments": {"amount": 500, "recipient": "bob"}}
    rec, h = with_recording(fresh_service, [small_pay])
    h.step(mock_semantic="misaligned", may_auto_execute=False)
    sem = rec.evaluated[0][1]
    assert sem.provider == "MOCK" and sem.outcome.value == "UNSAFE"


# ---- observed content and provenance --------------------------------------------


def test_fetched_page_is_recorded_and_flags_the_payee(fresh_service):
    rec = Recording(fresh_service)
    h = Harness(rec, ScriptedAgent([FETCH, PAY]), agent_id="demo-agent", session_id="s-1",
                user_task="Reconcile invoices")
    assert h.observed_content == {}
    h.step()
    assert list(h.observed_content) == [PAGE] and "999888777" in h.observed_content[PAGE]
    result = h.step()
    proposal, _, _, provenance = rec.evaluated[1]
    assert PAGE in proposal.context.observed_external_content
    assert provenance == {"recipient": ProvenanceLabel.EXTERNAL}
    assert result.decision.outcome is DecisionOutcome.BLOCK
    assert "PAYMENT_EXTERNAL_PROVENANCE" in result.decision.rules_fired
    assert result.decision.canonical_action.arg_provenance["recipient"] == ProvenanceLabel.EXTERNAL


def test_agent_cannot_pass_provenance_through_its_request():
    with pytest.raises(Exception):
        ScriptedAgent([{**PAY, "arg_provenance": {"recipient": "user_task"}}])


def test_observed_snapshot_is_not_aliased(fresh_service):
    h = harness(fresh_service, [FETCH])
    h.step()
    h.observed_content.clear()
    assert len(h.observed_content) == 1


def test_a_read_that_did_not_run_records_nothing(fresh_service):
    h = harness(fresh_service, [FETCH, CAL])
    h.step(may_auto_execute=False)
    assert h.observed_content == {}


# ---- origin_labels is a pure helper -------------------------------------------------


@pytest.mark.parametrize(
    "arguments,task,observed,expected",
    [
        ({"recipient": "999888777"}, "reconcile", {"u": "send it to 999888777 now"},
         {"recipient": ProvenanceLabel.EXTERNAL}),
        ({"recipient": "999888777"}, "pay 999888777", {"u": "page 999888777"},
         {"recipient": ProvenanceLabel.USER_TASK}),  # the user said it first
        ({"recipient": "ACCT-9"}, "t", {"u": "pay acct-9 today"},
         {"recipient": ProvenanceLabel.EXTERNAL}),
        ({"recipient": "bob"}, "t", {"u": "bobby tables"}, {}),  # word boundary
        ({"recipient": "ab"}, "t", {"u": "ab ab ab"}, {}),  # too short to say anything
        ({"amount": 100000}, "t", {"u": "100000"}, {}),  # only text values
        ({"recipient": "  x-ray  "}, "t", {"u": "an x-ray report"},
         {"recipient": ProvenanceLabel.EXTERNAL}),
        ({"recipient": "zzz"}, "t", {}, {}),
    ],
)
def test_origin_labels(arguments, task, observed, expected):
    assert origin_labels(arguments, task, observed) == expected


# ---- human-approved execution path -------------------------------------------------


def test_reviewed_decision_executes_only_after_the_human_approves(fresh_service):
    client = GuardClient(fresh_service)
    h = Harness(client, ScriptedAgent([DELETE, CAL]), agent_id="demo-agent",
                session_id="s", user_task="Clean up")
    d = h.step().decision
    assert d.outcome is DecisionOutcome.REVIEW
    with pytest.raises(Exception):
        h.execute(d.decision_id)  # the gateway refuses: not approved yet
    assert h.halted
    client.approve(d.decision_id, "human")
    assert h.execute(d.decision_id)["success"] is True
    assert not h.halted
    with pytest.raises(HarnessError):
        h.execute(d.decision_id)  # single use
    assert h.step().decision.outcome is DecisionOutcome.APPROVE


def test_blocked_or_unknown_decisions_never_reach_execute(fresh_service):
    rec, h = with_recording(fresh_service, [PAY])
    d = h.step().decision
    with pytest.raises(HarnessError):
        h.execute(d.decision_id)
    with pytest.raises(HarnessError):
        h.execute("never-seen")
    assert rec.executed == []


# ---- import boundary ------------------------------------------------------------------


def test_harness_imports_and_vocabulary():
    src = HARNESS_PY.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        mods = []
        if isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        elif isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        for m in mods:
            for bad in ("fastapi", "app.api", "app.world", "app.guard"):
                assert not m.startswith(bad), m
    low = src.lower()
    for word in ("email", "calendar", "payment", "website", "invoice", "inbox", "transfer",
                 "delete", "scenario"):
        assert word not in low, word
    assert re.findall(r"^class (\w*Client\w*)\(", src, re.M) == ["GuardClientProtocol"]
