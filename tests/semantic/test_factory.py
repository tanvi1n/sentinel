import json

import pytest

from app.contracts import SemanticFindings, SemanticResult, SemanticSource, SemanticStatus
from app.semantic import factory as factory_module
from app.semantic.factory import MODES, SemanticFactory
from app.semantic.replay import build_record
from tests.semantic.helpers import FAKE_KEY, FINDINGS_JSON, live_env, make_ctx


def env(tmp_path, **over):
    return live_env(SENTINEL_REPLAY_PATH=str(tmp_path / "cache.jsonl"), **over)


def record_replay(tmp_path, ctx, version="v1"):
    result = SemanticResult(
        status="VALID",
        source="LIVE",
        provider="qualcomm:rec",
        findings=SemanticFindings(
            intent_alignment="MISALIGNED",
            injection_suspected=False,
            ambiguity="LOW",
            rationale="recorded",
        ),
    )
    rec = build_record(ctx, version, result, recorded_at="2026-10-09T00:00:00+00:00")
    (tmp_path / "cache.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")


def ok_call(prompt, timeout):
    return FINDINGS_JSON


def down_call(prompt, timeout):
    raise ConnectionError("network down")


def garbage_call(prompt, timeout):
    return "garbage"


def timeout_call(prompt, timeout):
    raise TimeoutError()


def test_modes_listed():
    assert MODES == ("mock", "replay", "live", "auto")


def test_default_mode_is_mock():
    f = SemanticFactory({})
    assert f.describe()["mode"] == "mock"
    assert f.get_reasoner().analyze(make_ctx()).source is SemanticSource.MOCK


def test_mode_from_env_and_invalid_mode():
    assert SemanticFactory({"SENTINEL_SEMANTIC_MODE": "replay"}).describe()["mode"] == "replay"
    with pytest.raises(ValueError):
        SemanticFactory({"SENTINEL_SEMANTIC_MODE": "bogus"})
    f = SemanticFactory({})
    with pytest.raises(ValueError):
        f.set_mode("nope")
    with pytest.raises(ValueError):
        f.set_mode("mock", mock_mode="nope")


def test_mock_mode_with_mock_variant():
    f = SemanticFactory({})
    f.set_mode("mock", mock_mode="injection")
    r = f.get_reasoner().analyze(make_ctx())
    assert r.provider == "mock:injection" and r.findings.injection_suspected
    assert f.describe()["provider"] == "mock:injection"


def test_replay_mode_hit_and_miss(tmp_path):
    f = SemanticFactory(env(tmp_path))
    f.set_mode("replay")
    assert f.get_reasoner().analyze(make_ctx()).error == "replay miss"
    record_replay(tmp_path, make_ctx())
    r = f.get_reasoner().analyze(make_ctx())
    assert r.source is SemanticSource.REPLAY and r.provider == "replay:qualcomm:rec"


def test_live_mode_valid_and_no_silent_switch(tmp_path):
    record_replay(tmp_path, make_ctx())  # a replay hit exists; live must not use it
    f = SemanticFactory(env(tmp_path), call_model=ok_call)
    f.set_mode("live")
    assert f.get_reasoner().analyze(make_ctx()).source is SemanticSource.LIVE
    down = SemanticFactory(env(tmp_path), call_model=down_call)
    down.set_mode("live")
    r = down.get_reasoner().analyze(make_ctx())
    assert r.status is SemanticStatus.UNAVAILABLE and r.source is SemanticSource.NONE


def test_auto_prefers_live(tmp_path):
    record_replay(tmp_path, make_ctx())
    f = SemanticFactory(env(tmp_path), call_model=ok_call)
    f.set_mode("auto")
    assert f.get_reasoner().analyze(make_ctx()).source is SemanticSource.LIVE


def test_auto_live_down_falls_to_replay(tmp_path):
    record_replay(tmp_path, make_ctx())
    f = SemanticFactory(env(tmp_path), call_model=down_call)
    f.set_mode("auto")
    r = f.get_reasoner().analyze(make_ctx())
    assert r.status is SemanticStatus.VALID and r.source is SemanticSource.REPLAY


def test_auto_no_key_falls_to_replay(tmp_path):
    record_replay(tmp_path, make_ctx())
    f = SemanticFactory(env(tmp_path, QUALCOMM_API_KEY=""), call_model=ok_call)
    f.set_mode("auto")
    assert f.get_reasoner().analyze(make_ctx()).source is SemanticSource.REPLAY


def test_auto_both_fail_is_unavailable_never_mock(tmp_path):
    f = SemanticFactory(env(tmp_path), call_model=down_call)
    f.set_mode("auto", mock_mode="aligned")  # mock is configured but must not be used
    r = f.get_reasoner().analyze(make_ctx())
    assert r.status is SemanticStatus.UNAVAILABLE
    assert r.source is SemanticSource.NONE and r.findings is None
    assert "mock" not in (r.provider or "").lower()
    assert "live:" in r.error and "replay: replay miss" in r.error


@pytest.mark.parametrize("call", [down_call, garbage_call, timeout_call])
def test_auto_never_mock_across_failure_shapes(tmp_path, call):
    f = SemanticFactory(env(tmp_path), call_model=call)
    f.set_mode("auto")
    r = f.get_reasoner().analyze(make_ctx())
    assert r.source is not SemanticSource.MOCK
    assert r.status is SemanticStatus.UNAVAILABLE


def test_describe_never_exposes_key(tmp_path):
    f = SemanticFactory(env(tmp_path))
    d = f.describe()
    assert d["key_configured"] is True
    assert FAKE_KEY not in json.dumps(d)
    assert SemanticFactory({}).describe()["key_configured"] is False


def test_key_read_once_at_construction(tmp_path):
    e = env(tmp_path)
    f = SemanticFactory(e, call_model=ok_call)
    e["QUALCOMM_API_KEY"] = ""
    f.set_mode("live")
    assert f.describe()["key_configured"] is True
    assert f.get_reasoner().analyze(make_ctx()).source is SemanticSource.LIVE


def test_module_level_functions_use_default_factory(monkeypatch):
    monkeypatch.setattr(factory_module, "_default", SemanticFactory({}))
    factory_module.set_mode("mock", mock_mode="misaligned")
    assert factory_module.describe()["provider"] == "mock:misaligned"
    r = factory_module.get_reasoner().analyze(make_ctx())
    assert r.findings.intent_alignment.value == "MISALIGNED"
