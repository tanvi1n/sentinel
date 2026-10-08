"""Record LIVE results, save as JSONL, load through ReplayReasoner, same result."""

import json
import subprocess
import sys
from pathlib import Path

from app.contracts import SemanticSource, SemanticStatus
from app.semantic.mock import MockReasoner
from app.semantic.qualcomm import QualcommConfig, QualcommReasoner
from app.semantic.replay import ReplayReasoner, record_contexts
from tests.semantic.helpers import FAKE_KEY, FINDINGS_JSON, live_env, make_ctx, observed

ROOT = Path(__file__).resolve().parents[2]


def live_reasoner(reply=FINDINGS_JSON):
    return QualcommReasoner(QualcommConfig.from_env(live_env()), lambda prompt, timeout: reply)


def test_live_result_round_trips_through_the_cache(tmp_path):
    path = tmp_path / "replay" / "cache.jsonl"  # directory is created
    ctx = make_ctx(observed=[observed()])
    live = live_reasoner().analyze(ctx)
    assert live.source is SemanticSource.LIVE

    summary = record_contexts([ctx], live_reasoner(), path, "v1", recorded_at="2026-10-09T12:00:00+00:00")
    assert summary == {"recorded": 1, "already_recorded": 0, "refused": []}

    replayed = ReplayReasoner(path, "v1").analyze(ctx)
    assert replayed.status is SemanticStatus.VALID and replayed.source is SemanticSource.REPLAY
    assert replayed.findings == live.findings  # same semantic result
    assert replayed.provider == f"replay:{live.provider}"
    assert replayed.recorded_at == "2026-10-09T12:00:00+00:00"
    assert replayed.prompt_version == "v1"


def test_file_is_plain_jsonl_with_no_secrets(tmp_path):
    path = tmp_path / "c.jsonl"
    record_contexts([make_ctx(), make_ctx(user_task="second")], live_reasoner(), path, "v1")
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert len(lines) == 2 and text.endswith("\n")
    for line in lines:
        rec = json.loads(line)
        assert set(rec) == {"key", "recorded_at", "provider", "prompt_version", "source", "findings"}
        assert rec["source"] == "LIVE"
    assert FAKE_KEY not in text


def test_only_live_results_are_recorded(tmp_path):
    path = tmp_path / "c.jsonl"
    ctx = make_ctx()
    for reasoner in (MockReasoner("aligned"), MockReasoner("unavailable"),
                     live_reasoner("not json at all")):
        summary = record_contexts([ctx], reasoner, path, "v1")
        assert summary["recorded"] == 0 and len(summary["refused"]) == 1
    assert not path.exists() or path.read_text(encoding="utf-8") == ""
    assert ReplayReasoner(path, "v1").analyze(ctx).status is SemanticStatus.UNAVAILABLE


def test_replay_of_a_replay_is_not_recorded(tmp_path):
    path = tmp_path / "c.jsonl"
    ctx = make_ctx()
    record_contexts([ctx], live_reasoner(), path, "v1")
    other = tmp_path / "d.jsonl"
    summary = record_contexts([ctx], ReplayReasoner(path, "v1"), other, "v1")
    assert summary["recorded"] == 0 and len(summary["refused"]) == 1
    assert not other.exists()


def test_existing_and_duplicate_contexts_are_not_asked_again(tmp_path):
    path = tmp_path / "c.jsonl"
    ctx = make_ctx()
    asked = []

    def counting(prompt, timeout):
        asked.append(1)
        return FINDINGS_JSON

    reasoner = QualcommReasoner(QualcommConfig.from_env(live_env()), counting)
    assert record_contexts([ctx, make_ctx()], reasoner, path, "v1")["recorded"] == 1
    again = record_contexts([ctx], reasoner, path, "v1")
    assert again == {"recorded": 0, "already_recorded": 1, "refused": []}
    assert len(asked) == 1 and len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_recording_is_keyed_by_prompt_version(tmp_path):
    path = tmp_path / "c.jsonl"
    ctx = make_ctx()
    record_contexts([ctx], live_reasoner(), path, "v1")
    assert ReplayReasoner(path, "v2").analyze(ctx).status is SemanticStatus.UNAVAILABLE


def test_script_refuses_without_a_live_provider(tmp_path):
    contexts = tmp_path / "contexts.json"
    contexts.write_text(json.dumps([make_ctx().model_dump(mode="json")]), encoding="utf-8")
    out = tmp_path / "cache.jsonl"
    env = {"PATH": "", "SENTINEL_SEMANTIC_MODE": "mock", "SYSTEMROOT": "C:\\Windows"}
    proc = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts" / "record_replay.py"),
         "--contexts", str(contexts), "--out", str(out)],
        capture_output=True, text=True, env=env, cwd=tmp_path,
    )
    assert proc.returncode == 1
    assert "refused 1" in proc.stdout and "not configured" in proc.stderr
    assert not out.exists()


def test_script_rejects_bad_contexts_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts" / "record_replay.py"), "--contexts", str(bad)],
        capture_output=True, text=True, cwd=tmp_path,
    )
    assert proc.returncode == 2


def test_result_from_another_prompt_version_is_not_recorded(tmp_path):
    import pytest

    from app.semantic.replay import build_record

    ctx = make_ctx()
    live_v1 = live_reasoner().analyze(ctx)  # produced under prompt version v1
    assert live_v1.prompt_version == "v1"
    with pytest.raises(ValueError):
        build_record(ctx, "v2", live_v1)
    path = tmp_path / "c.jsonl"
    summary = record_contexts([ctx], live_reasoner(), path, "v2")
    assert summary["recorded"] == 0 and len(summary["refused"]) == 1
    assert not path.exists()
