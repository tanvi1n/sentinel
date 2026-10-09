import ast
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent.scenario import SCENARIOS_DIR, load_scenario, load_scenarios
from app.guard.client import GuardClient

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_scenario.py"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("run_scenario", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Empty QUALCOMM_* values: a developer's local .env cannot change these runs
# (python-dotenv never overrides a variable that is already set).
CLI_ENV = {
    **{k: v for k, v in os.environ.items() if k.upper() in ("PATH", "SYSTEMROOT")},
    "QUALCOMM_API_KEY": "", "QUALCOMM_BASE_URL": "", "QUALCOMM_MODEL": "",
    "SENTINEL_REPLAY_PATH": "replay/none-for-tests.jsonl",
}


def run_cli(*args):
    return subprocess.run([sys.executable, "-I", str(RUNNER), *args],
                          capture_output=True, text=True, cwd=ROOT, env=CLI_ENV)


# ---- the real backend, in process ---------------------------------------------------


@pytest.mark.parametrize("path", sorted(SCENARIOS_DIR.glob("*.json")), ids=lambda p: p.name)
def test_each_scenario_passes_against_the_real_guard(runner, fresh_service, path):
    lines = []
    assert runner.run_scenario(load_scenario(path), GuardClient(fresh_service), out=lines.append), \
        "\n".join(lines)


def test_report_matches_the_expected_outcomes(runner, fresh_service):
    client = GuardClient(fresh_service)
    by_id = {s.id: runner.play(s, client) for s in load_scenarios()}
    assert all(r.ok for r in by_id.values()), [p for r in by_id.values() for p in r.problems]
    assert [s.outcome for s in by_id["safe-action"].steps] == ["APPROVE"]
    assert [s.outcome for s in by_id["intent-mismatch"].steps] == ["REVIEW"]
    assert [s.outcome for s in by_id["bulk-delete"].steps] == ["REVIEW"]
    assert [s.outcome for s in by_id["prompt-injection"].steps] == ["APPROVE", "BLOCK"]
    assert by_id["prompt-injection"].steps[0].ran and not by_id["prompt-injection"].steps[1].ran
    assert "undone, world restored" in by_id["bulk-delete"].steps[0].notes


def test_the_runner_reports_a_wrong_expectation_instead_of_hiding_it(runner, fresh_service, tmp_path):
    import json

    data = json.loads((SCENARIOS_DIR / "01-safe-action.json").read_text(encoding="utf-8"))
    data["id"] = "wrong"
    data["expected_verdict"] = "REVIEW"
    data["steps"][0].update(auto_execute=False, expect={"outcome": "REVIEW", "lifecycle": "PENDING_REVIEW"})
    p = tmp_path / "wrong.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    lines = []
    assert not runner.run_scenario(load_scenario(p), GuardClient(fresh_service), out=lines.append)
    assert any("PROBLEM" in line for line in lines)


@pytest.mark.parametrize("mode", ["replay", "live", "auto"])
def test_without_a_provider_the_runner_fails_closed_and_says_so(runner, fresh_service, mode):
    s = next(x for x in load_scenarios() if x.id == "intent-mismatch")
    with runner.semantic_mode(mode):
        report = runner.play(s, GuardClient(fresh_service))
    row = report.steps[0]
    assert row.outcome == "REVIEW" and row.semantic.startswith("UNAVAILABLE")
    assert not row.semantic.endswith("/MOCK")


def test_semantic_mode_is_restored(runner):
    from app.semantic import factory

    before = factory.describe()["mode"]
    with runner.semantic_mode("auto"):
        assert factory.describe()["mode"] == "auto"
    assert factory.describe()["mode"] == before


# ---- the command line ---------------------------------------------------------------


def test_cli_runs_every_scenario():
    proc = run_cli()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count("OK (expected verdict") == 4 and "PROBLEM" not in proc.stdout
    assert "semantic mode: mock" in proc.stdout


@pytest.mark.parametrize("path", sorted(SCENARIOS_DIR.glob("*.json")), ids=lambda p: p.name)
def test_cli_runs_a_single_file(path):
    proc = run_cli(str(path))
    assert proc.returncode == 0 and proc.stdout.count("== ") == 1


def test_cli_bad_path_exits_nonzero(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    assert run_cli(str(bad)).returncode == 2
    assert run_cli(str(tmp_path / "missing.json")).returncode == 2


def test_cli_without_a_provider_fails_closed_and_still_meets_every_expectation():
    # No LIVE call and no replay cache: required checks come back UNAVAILABLE,
    # which the guard turns into REVIEW, so no expected outcome is ever weakened.
    proc = run_cli("--semantic-mode", "auto")
    assert proc.returncode == 0, proc.stdout
    assert "UNAVAILABLE/NONE" in proc.stdout and "/MOCK" not in proc.stdout


def test_runner_has_no_scenario_specific_logic():
    src = RUNNER.read_text(encoding="utf-8")
    for token in ("safe-action", "intent-mismatch", "bulk-delete", "prompt-injection",
                  "calendar_read", "email_send", "file_delete", "payment_transfer", "web_fetch"):
        assert token not in src
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Compare):
            consts = [c.value for c in ast.walk(node) if isinstance(c, ast.Constant)]
            assert not any(isinstance(c, str) and "-" in c for c in consts)


def test_explain_prints_each_decision_explanation():
    proc = run_cli("--explain", str(SCENARIOS_DIR / "04-prompt-injection.json"))
    assert proc.returncode == 0
    assert "| BLOCK - the action is prohibited and cannot run." in proc.stdout
    assert "| Semantic findings are advisory and did not affect this block." in proc.stdout
