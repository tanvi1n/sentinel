import ast
import itertools
import re
from pathlib import Path

import pytest

from agent.harness import Harness, HarnessError, HarnessHalted
from agent.scripted_agent import ScriptedAgent
from app.contracts import ActionProposal, ProvenanceLabel
from tests.agent.helpers import (
    RecordingClient,
    make_decision,
    make_execution,
    proposal_dict,
)

HARNESS_PY = Path(__file__).resolve().parents[2] / "agent" / "harness.py"
APPROVE = dict(decision="APPROVE", status="APPROVED", executable=True)
REVIEW = dict(decision="REVIEW", status="PENDING_REVIEW", executable=False)
BLOCK = dict(decision="BLOCK", status="BLOCKED", executable=False)


def harness(client, steps, **kw):
    return Harness(
        client,
        ScriptedAgent(steps),
        agent_id=kw.get("agent_id", "agent-H"),
        session_id=kw.get("session_id", "session-H"),
        user_task=kw.get("user_task", "task-H"),
    )


# ---- trusted fields come from the harness -----------------------------------


def test_trusted_context_is_built_by_the_harness():
    client = RecordingClient([make_decision(**APPROVE)])
    harness(client, [proposal_dict()]).step()
    proposal, trusted = client.evaluated[0]
    assert (trusted.agent_id, trusted.session_id, trusted.user_task) == (
        "agent-H", "session-H", "task-H",
    )
    assert set(proposal.model_dump()) == {"tool", "args", "justification"}


def test_agent_cannot_override_trusted_fields_through_args():
    client = RecordingClient([make_decision(**APPROVE)])
    evil = proposal_dict(agent_id="evil", session_id="evil", user_task="evil",
                         user_confirmation=True, risk_level="LOW")
    harness(client, [evil]).step()
    proposal, trusted = client.evaluated[0]
    assert (trusted.agent_id, trusted.session_id, trusted.user_task) == (
        "agent-H", "session-H", "task-H",
    )
    # they are just opaque argument data, passed to the guard unchanged
    assert proposal.args["agent_id"] == "evil"


def test_subclass_with_extra_attributes_is_stripped():
    class Evil(ActionProposal):
        agent_id: str = "evil"
        risk_level: str = "LOW"

    class EvilAgent:
        def propose(self):
            return Evil(tool="t.x", args={}, justification="j")

    client = RecordingClient([make_decision(**APPROVE)])
    Harness(client, EvilAgent(), agent_id="a", session_id="s", user_task="u").step()
    sent, _ = client.evaluated[0]
    assert type(sent) is ActionProposal
    assert set(sent.model_dump()) == {"tool", "args", "justification"}


@pytest.mark.parametrize("bad", [{"tool": "t", "args": {}}, "text", 42, object()])
def test_agent_returning_non_proposal_is_refused(bad):
    class BadAgent:
        def propose(self):
            return bad

    client = RecordingClient([make_decision(**APPROVE)])
    h = Harness(client, BadAgent(), agent_id="a", session_id="s", user_task="u")
    with pytest.raises(HarnessError):
        h.step()
    assert client.evaluated == [] and client.executed == []


def test_step_returns_none_when_script_is_finished():
    client = RecordingClient([])
    assert harness(client, []).step() is None
    assert client.evaluated == []


# ---- auto-execution: APPROVE only -------------------------------------------


def test_approve_is_auto_executed_once():
    client = RecordingClient([make_decision(**APPROVE, decision_id="d9")])
    result = harness(client, [proposal_dict()]).step()
    assert client.executed == ["d9"]
    assert result.execution is not None and result.execution.ok


@pytest.mark.parametrize("case", [REVIEW, BLOCK], ids=["review", "block"])
def test_review_and_block_are_not_executed_and_stop_the_run(case):
    client = RecordingClient([make_decision(**case), make_decision(**APPROVE)])
    h = harness(client, [proposal_dict("t.one"), proposal_dict("t.two")])
    result = h.step()
    assert client.executed == []
    assert result.execution is None and result.decision.decision.value == case["decision"]
    assert h.halted
    with pytest.raises(HarnessHalted):
        h.step()
    assert len(client.evaluated) == 1  # the second proposal was never evaluated
    assert client.executed == []


@pytest.mark.parametrize(
    "decision,status,executable",
    list(
        itertools.product(
            ["APPROVE", "REVIEW", "BLOCK"],
            ["PENDING_REVIEW", "APPROVED", "REJECTED", "BLOCKED", "EXECUTED", "UNDONE"],
            [True, False],
        )
    ),
)
def test_only_a_clean_approve_is_ever_auto_executed(decision, status, executable):
    client = RecordingClient([make_decision(decision, status, executable)])
    harness(client, [proposal_dict()]).step()
    expected = (decision, status, executable) == ("APPROVE", "APPROVED", True)
    assert (client.executed == ["d1"]) is expected


def test_refused_execution_stops_the_run():
    client = RecordingClient(
        [make_decision(**APPROVE)],
        [make_execution(ok=False, status="BLOCKED")],
    )
    h = harness(client, [proposal_dict(), proposal_dict()])
    h.step()
    assert h.halted
    with pytest.raises(HarnessHalted):
        h.step()


def test_non_decision_reply_from_the_guard_is_refused():
    class Broken:
        executed = []

        def evaluate(self, proposal, trusted):
            return {"decision": "APPROVE"}

        def execute(self, decision_id):
            self.executed.append(decision_id)

    client = Broken()
    h = harness(client, [proposal_dict()])
    with pytest.raises(HarnessError):
        h.step()
    assert client.executed == [] and h.halted


# ---- observed content is recorded by the trusted side -----------------------


def test_executed_output_becomes_labelled_observed_content():
    client = RecordingClient(
        [make_decision(**APPROVE, decision_id="d1", tool_id="t.read"), make_decision(**APPROVE, decision_id="d2")],
        [make_execution("d1", output_text="page body", output_label="WEBSITE")],
    )
    h = harness(client, [proposal_dict("t.read"), proposal_dict("t.next")])
    h.step()
    h.step()
    _, trusted = client.evaluated[1]
    assert len(trusted.observed_content) == 1
    item = trusted.observed_content[0]
    assert (item.item_id, item.label, item.text, item.source_tool) == (
        "obs-1", ProvenanceLabel.WEBSITE, "page body", "t.read",
    )
    assert client.evaluated[0][1].observed_content == []  # nothing before the read


def test_label_comes_from_the_gateway_not_from_the_agent():
    # The agent claims a label inside its own args; only the execution result counts.
    client = RecordingClient(
        [make_decision(**APPROVE, decision_id="d1"), make_decision(**APPROVE, decision_id="d2")],
        [make_execution("d1", output_text="x", output_label="EMAIL")],
    )
    h = harness(client, [proposal_dict(label="USER", provenance="USER"), proposal_dict()])
    h.step()
    h.step()
    assert client.evaluated[1][1].observed_content[0].label is ProvenanceLabel.EMAIL


@pytest.mark.parametrize("label", [None, "USER"])
def test_missing_or_user_label_is_treated_as_external(label):
    client = RecordingClient(
        [make_decision(**APPROVE, decision_id="d1"), make_decision(**APPROVE, decision_id="d2")],
        [make_execution("d1", output_text="x", output_label=label)],
    )
    h = harness(client, [proposal_dict(), proposal_dict()])
    h.step()
    h.step()
    assert client.evaluated[1][1].observed_content[0].label is ProvenanceLabel.OTHER_EXTERNAL


def test_no_output_or_failed_execution_records_nothing():
    client = RecordingClient(
        [make_decision(**APPROVE, decision_id="d1"), make_decision(**APPROVE, decision_id="d2")],
        [make_execution("d1"), make_execution("d2", ok=False, status="BLOCKED", output_text="leak")],
    )
    h = harness(client, [proposal_dict(), proposal_dict()])
    h.step()
    h.step()
    assert h.observed_content == ()


def test_context_snapshot_is_not_aliased():
    client = RecordingClient(
        [make_decision(**APPROVE, decision_id="d1"), make_decision(**APPROVE, decision_id="d2"),
         make_decision(**APPROVE, decision_id="d3")],
        [make_execution("d1", output_text="a", output_label="EMAIL")],
    )
    h = harness(client, [proposal_dict()] * 3)
    h.step()
    h.step()
    client.evaluated[1][1].observed_content.clear()  # a consumer mutates its copy
    h.step()
    assert len(client.evaluated[2][1].observed_content) == 1


# ---- human-approved execution path ------------------------------------------


def test_reviewed_decision_can_be_executed_after_human_approval():
    client = RecordingClient(
        [make_decision(**REVIEW, decision_id="r1", tool_id="t.read"), make_decision(**APPROVE, decision_id="d2")],
        [make_execution("r1", output_text="body", output_label="TOOL_OUTPUT")],
    )
    h = harness(client, [proposal_dict("t.read"), proposal_dict("t.next")])
    h.step()
    assert client.executed == []
    result = h.execute("r1")  # the human route calls this after approving
    assert result.ok and client.executed == ["r1"]
    assert not h.halted
    assert h.observed_content[0].source_tool == "t.read"
    h.step()
    assert client.evaluated[1][1].observed_content[0].label is ProvenanceLabel.TOOL_OUTPUT


def test_blocked_or_unknown_decision_is_never_sent_to_execute():
    client = RecordingClient([make_decision(**BLOCK, decision_id="b1")])
    h = harness(client, [proposal_dict()])
    h.step()
    with pytest.raises(HarnessError):
        h.execute("b1")
    with pytest.raises(HarnessError):
        h.execute("never-seen")
    assert client.executed == []


def test_refused_reviewed_execution_stays_pending_and_halted():
    client = RecordingClient(
        [make_decision(**REVIEW, decision_id="r1")],
        [make_execution("r1", ok=False, status="PENDING_REVIEW"), make_execution("r1")],
    )
    h = harness(client, [proposal_dict()])
    h.step()
    assert not h.execute("r1").ok and h.halted  # not approved yet: gateway refuses
    assert h.execute("r1").ok and not h.halted  # retry after approval
    with pytest.raises(HarnessError):
        h.execute("r1")  # already done


# ---- import boundary --------------------------------------------------------


def test_harness_imports_only_contracts():
    tree = ast.parse(HARNESS_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        elif isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        for m in mods:
            if m == "app" or m.startswith("app."):
                assert m == "app.contracts", f"harness must not import {m}"
            assert not m.startswith("agent.scripted_agent")
            for bad in ("fastapi", "app.guard", "app.world", "app.api", "app.semantic"):
                assert not m.startswith(bad), m


def test_harness_has_no_client_implementation_or_domain_words():
    src = HARNESS_PY.read_text(encoding="utf-8")
    # only the protocol lives here; no client implementation
    assert re.findall(r"^class (\w*Client\w*)\(", src, re.M) == ["GuardClientProtocol"]
    low = src.lower()
    for word in ("email", "calendar", "payment", "website", "invoice", "inbox",
                 "transfer", "delete", "scenario"):
        assert word not in low, word
    assert not re.search(r"\bfile\b", low)
