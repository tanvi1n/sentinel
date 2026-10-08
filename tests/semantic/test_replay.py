import json

import pytest

from app.contracts import (
    SemanticFindings,
    SemanticResult,
    SemanticSource,
    SemanticStatus,
)
from app.semantic.base import replay_key
from app.semantic.mock import MockReasoner
from app.semantic.replay import ReplayReasoner, build_record
from tests.semantic.helpers import make_ctx, observed

FINDINGS = SemanticFindings(
    intent_alignment="MISALIGNED",
    injection_suspected=True,
    ambiguity="LOW",
    rationale="recorded rationale",
)


def live_result(provider="qualcomm:test-model") -> SemanticResult:
    return SemanticResult(
        status=SemanticStatus.VALID,
        source=SemanticSource.LIVE,
        provider=provider,
        latency_ms=1234,
        findings=FINDINGS,
    )


def write(path, *records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def test_hit_returns_recorded_findings_labelled_replay(tmp_path):
    ctx = make_ctx()
    rec = build_record(ctx, "v1", live_result(), recorded_at="2026-10-09T10:00:00+00:00")
    r = ReplayReasoner(write(tmp_path / "c.jsonl", rec), "v1").analyze(ctx)
    assert r.status is SemanticStatus.VALID and r.source is SemanticSource.REPLAY
    assert r.provider == "replay:qualcomm:test-model"
    assert r.recorded_at == "2026-10-09T10:00:00+00:00"
    assert r.prompt_version == "v1"
    assert r.findings == FINDINGS


def test_miss_is_unavailable_and_invents_nothing(tmp_path):
    rec = build_record(make_ctx(), "v1", live_result())
    reasoner = ReplayReasoner(write(tmp_path / "c.jsonl", rec), "v1")
    r = reasoner.analyze(make_ctx(user_task="something else"))
    assert r.status is SemanticStatus.UNAVAILABLE
    assert r.source is SemanticSource.NONE
    assert r.findings is None and r.error == "replay miss" and r.recorded_at is None


def test_missing_file_and_empty_file_are_misses(tmp_path):
    ctx = make_ctx()
    assert ReplayReasoner(tmp_path / "nope.jsonl", "v1").analyze(ctx).error == "replay miss"
    (tmp_path / "e.jsonl").write_text("", encoding="utf-8")
    empty = ReplayReasoner(tmp_path / "e.jsonl", "v1").analyze(ctx)
    assert empty.status is SemanticStatus.UNAVAILABLE


def test_prompt_version_and_label_changes_miss(tmp_path):
    ctx = make_ctx(observed=[observed(label="WEBSITE")])
    path = write(tmp_path / "c.jsonl", build_record(ctx, "v1", live_result()))
    assert ReplayReasoner(path, "v1").analyze(ctx).status is SemanticStatus.VALID
    assert ReplayReasoner(path, "v2").analyze(ctx).status is SemanticStatus.UNAVAILABLE
    relabelled = make_ctx(observed=[observed(label="EMAIL")])
    assert ReplayReasoner(path, "v1").analyze(relabelled).status is SemanticStatus.UNAVAILABLE


def test_keys_are_deterministic():
    assert replay_key(make_ctx(), "v1") == replay_key(make_ctx(), "v1")
    assert build_record(make_ctx(), "v1", live_result())["key"] == replay_key(make_ctx(), "v1")


def test_build_record_refuses_non_live_results():
    ctx = make_ctx()
    with pytest.raises(ValueError):
        build_record(ctx, "v1", MockReasoner("aligned").analyze(ctx))  # MOCK
    with pytest.raises(ValueError):
        build_record(ctx, "v1", MockReasoner("unavailable").analyze(ctx))
    replay = SemanticResult(
        status="VALID", source="REPLAY", provider="replay:x", findings=FINDINGS
    )
    with pytest.raises(ValueError):
        build_record(ctx, "v1", replay)  # a replay of a replay is not a recording


def test_loader_ignores_mock_and_unavailable_records(tmp_path):
    ctx = make_ctx()
    good = build_record(ctx, "v1", live_result())
    forged_mock = {**good, "source": "MOCK"}
    mock_provider = {**good, "provider": "mock:aligned"}
    no_source = {k: v for k, v in good.items() if k != "source"}
    unavailable = {**good, "source": "NONE", "findings": None}

    for bad in (forged_mock, mock_provider, no_source, unavailable):
        reasoner = ReplayReasoner(write(tmp_path / "c.jsonl", bad), "v1")
        assert len(reasoner) == 0 and reasoner.rejected == 1
        assert reasoner.analyze(ctx).status is SemanticStatus.UNAVAILABLE


def test_loader_skips_garbage_lines_but_keeps_good_ones(tmp_path):
    ctx = make_ctx()
    path = tmp_path / "c.jsonl"
    path.write_text(
        "not json\n[1,2]\n"
        + json.dumps({"key": "k"})
        + "\n"
        + json.dumps(build_record(ctx, "v1", live_result()))
        + "\n",
        encoding="utf-8",
    )
    reasoner = ReplayReasoner(path, "v1")
    assert len(reasoner) == 1 and reasoner.rejected == 3
    assert reasoner.analyze(ctx).source is SemanticSource.REPLAY


def test_replay_is_deterministic(tmp_path):
    ctx = make_ctx()
    path = write(tmp_path / "c.jsonl", build_record(ctx, "v1", live_result()))
    assert ReplayReasoner(path, "v1").analyze(ctx) == ReplayReasoner(path, "v1").analyze(ctx)
