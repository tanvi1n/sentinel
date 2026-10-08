import ast
import subprocess
import sys
from pathlib import Path

import pytest

from agent.fake_client import FAKE_NOTICE, FakeGuardClient
from agent.scenario import SCENARIOS_DIR, load_scenarios, load_seed
from app.contracts import DecisionStatus, SemanticSource, SemanticStatus
from tests.agent.helpers import proposal_dict

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_scenario.py"


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-I", str(RUNNER), *args], capture_output=True, text=True, cwd=ROOT
    )


def test_runs_every_scenario_file_with_no_arguments():
    proc = run_cli()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count("OK (expected verdict") == 4
    assert proc.stdout.startswith("FAKE CLIENT")
    assert "MISMATCH" not in proc.stdout


@pytest.mark.parametrize("path", sorted(SCENARIOS_DIR.glob("*.json")), ids=lambda p: p.name)
def test_runs_a_single_scenario_file(path):
    proc = run_cli(str(path))
    assert proc.returncode == 0 and proc.stdout.count("== ") == 1


def test_expected_outcomes_appear_in_output():
    out = run_cli().stdout
    assert "calendar.read -> APPROVE (APPROVED) [ran]" in out
    assert "email.send -> REVIEW (PENDING_REVIEW)" in out
    assert "file.delete -> REVIEW" in out and "simulated human approve -> EXECUTED" in out
    assert "payment.transfer -> BLOCK (BLOCKED)" in out
    assert "observed content recorded: 1 item(s)" in out  # the fetched page


def test_bad_path_exits_nonzero(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    assert run_cli(str(bad)).returncode == 2
    assert run_cli(str(tmp_path / "missing.json")).returncode == 2


def test_runner_has_no_scenario_specific_logic():
    src = RUNNER.read_text(encoding="utf-8")
    for token in ("safe-action", "intent-mismatch", "bulk-delete", "prompt-injection",
                  "calendar.read", "email.send", "file.delete", "payment.transfer", "web.fetch"):
        assert token not in src
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Compare):
            consts = [c.value for c in ast.walk(node) if isinstance(c, ast.Constant)]
            assert not any(isinstance(c, str) and "-" in c for c in consts)


# ---- the fake client is honest about being fake -----------------------------


@pytest.fixture(scope="module")
def seed():
    return load_seed()


def test_fake_decisions_are_marked_and_semantic_is_labelled_mock(seed):
    scenarios = {s.id: s for s in load_scenarios(seed=seed)}
    s = scenarios["intent-mismatch"]
    client = FakeGuardClient(s, seed)
    from agent.harness import Harness
    from agent.scripted_agent import ScriptedAgent

    Harness(client, ScriptedAgent(s.proposals()), agent_id=s.agent_id,
            session_id="x", user_task=s.user_task).step()
    d = client._decisions["d1"]
    assert d.explanation.startswith(FAKE_NOTICE)
    assert d.semantic.source is SemanticSource.MOCK and d.semantic.provider == "mock:misaligned"
    assert d.deterministic_decision.value == "APPROVE" and d.decision.value == "REVIEW"
    assert "raised the decision from APPROVE to REVIEW" in d.explanation


def test_fake_skips_semantic_when_the_step_has_no_mock_mode(seed):
    s = next(x for x in load_scenarios(seed=seed) if x.id == "safe-action")
    client = FakeGuardClient(s, seed)
    d = client.evaluate(s.proposals()[0], _trusted(s))
    assert d.semantic.status is SemanticStatus.SKIPPED


def _trusted(s):
    from app.contracts import TrustedContext

    return TrustedContext(agent_id=s.agent_id, session_id="x", user_task=s.user_task)


def test_fake_refuses_unapproved_double_and_unknown_execution(seed):
    s = next(x for x in load_scenarios(seed=seed) if x.id == "bulk-delete")
    client = FakeGuardClient(s, seed)
    d = client.evaluate(s.proposals()[0], _trusted(s))
    assert not client.execute(d.decision_id).ok  # review not approved
    assert not client.execute("d99").ok  # unknown
    client.approve(d.decision_id)
    assert client.execute(d.decision_id).ok
    assert not client.execute(d.decision_id).ok  # single use
    assert client.executed == [d.decision_id]


def test_fake_rejects_a_proposal_that_is_not_in_the_file(seed):
    from app.contracts import ActionProposal

    s = next(x for x in load_scenarios(seed=seed) if x.id == "safe-action")
    client = FakeGuardClient(s, seed)
    with pytest.raises(RuntimeError):
        client.evaluate(ActionProposal(**proposal_dict("t.other")), _trusted(s))


def test_a_block_can_never_be_approved_in_the_fake(seed):
    s = next(x for x in load_scenarios(seed=seed) if x.id == "prompt-injection")
    client = FakeGuardClient(s, seed)
    from agent.harness import Harness
    from agent.scripted_agent import ScriptedAgent

    h = Harness(client, ScriptedAgent(s.proposals()), agent_id=s.agent_id,
                session_id="x", user_task=s.user_task)
    h.step()
    h.step()
    d = client._decisions["d2"]
    assert d.status is DecisionStatus.BLOCKED
    with pytest.raises(RuntimeError):
        client.approve("d2")
    assert not client.execute("d2").ok
