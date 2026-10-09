"""Record from the scenarios (network boundary faked), then replay them through the real guard.

This is the path the team will use once Qualcomm access exists: the recording
run goes through the same harness and guard, so the replay keys match exactly.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from agent.scenario import load_scenarios
from app.guard.client import GuardClient
from app.semantic import factory as factory_module
from app.semantic.factory import SemanticFactory
from app.semantic.mock import MockReasoner
from app.semantic.replay import RecordingReasoner, ReplayReasoner

ROOT = Path(__file__).resolve().parents[2]
SECRET = "sk-replay-repro-SECRET"
REPLY = json.dumps({"intent_alignment": "MISALIGNED", "injection_suspected": False,
                    "ambiguity": "LOW", "rationale": "recorded answer"})


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def live_env(tmp_path):
    return {"QUALCOMM_API_KEY": SECRET, "QUALCOMM_BASE_URL": "https://x.invalid",
            "QUALCOMM_MODEL": "test-model", "SENTINEL_REPLAY_PATH": str(tmp_path / "rec.jsonl")}


def test_record_then_replay_reproduces_the_semantic_results(fresh_service, tmp_path, monkeypatch):
    recorder_script = load("record_replay")
    runner = load("run_scenario")
    out = tmp_path / "rec.jsonl"

    def fake_live_factory():
        f = SemanticFactory(live_env(tmp_path), call_model=lambda prompt, timeout: REPLY)
        f.set_mode("live")
        return f

    monkeypatch.setattr(recorder_script, "_live_factory", fake_live_factory)
    assert recorder_script.record_from_scenarios([], out) == 0

    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    # one record per step whose tool requires a semantic check
    assert len(lines) == 3
    assert {r["source"] for r in lines} == {"LIVE"}
    assert SECRET not in out.read_text(encoding="utf-8")

    # Replay, with no live provider at all: the recorded answers come back, labelled REPLAY.
    replay_factory = SemanticFactory({"SENTINEL_REPLAY_PATH": str(out)})
    replay_factory.set_mode("replay")
    monkeypatch.setattr(factory_module, "_default", replay_factory)
    client = GuardClient(fresh_service)
    for scenario in load_scenarios():
        report = runner.play(scenario, client)
        assert report.ok, report.problems
        for row in report.steps:
            assert row.semantic in ("not required/-", "UNSAFE/REPLAY"), (scenario.id, row.semantic)


def test_recording_twice_does_not_duplicate(fresh_service, tmp_path, monkeypatch):
    recorder_script = load("record_replay")

    def fake_live_factory():
        f = SemanticFactory(live_env(tmp_path), call_model=lambda prompt, timeout: REPLY)
        f.set_mode("live")
        return f

    monkeypatch.setattr(recorder_script, "_live_factory", fake_live_factory)
    out = tmp_path / "rec.jsonl"
    recorder_script.record_from_scenarios([], out)
    recorder_script.record_from_scenarios([], out)
    assert len(out.read_text(encoding="utf-8").splitlines()) == 3


def test_without_live_access_nothing_is_recorded(fresh_service, tmp_path, capsys):
    recorder_script = load("record_replay")
    out = tmp_path / "rec.jsonl"
    # the real (stub) call: configured but unimplemented -> every answer refused
    real = recorder_script._live_factory
    recorder_script._live_factory = lambda: _configured_stub(tmp_path)
    try:
        assert recorder_script.record_from_scenarios([], out) == 1
    finally:
        recorder_script._live_factory = real
    assert not out.exists()
    assert "refused 3" in capsys.readouterr().out


def _configured_stub(tmp_path):
    f = SemanticFactory(live_env(tmp_path))
    f.set_mode("live")
    return f


@pytest.mark.parametrize("mode", ["aligned", "unavailable"])
def test_recording_reasoner_never_writes_mock_results(tmp_path, mode):
    from tests.semantic.helpers import make_ctx

    out = tmp_path / "rec.jsonl"
    rec = RecordingReasoner(MockReasoner(mode), out, "v1")
    result = rec.analyze(make_ctx())
    assert result.provider == f"mock:{mode}"  # the answer is passed through unchanged
    assert rec.recorded == 0 and len(rec.refused) == 1 and not out.exists()
    assert ReplayReasoner(out, "v1").analyze(make_ctx()).error == "replay miss"
