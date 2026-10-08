import pytest

from app.contracts import (
    Ambiguity,
    IntentAlignment,
    SemanticResult,
    SemanticSource,
    SemanticStatus,
)
from app.semantic.mock import MOCK_MODES, MockReasoner
from tests.semantic.helpers import make_ctx


def run(mode):
    return MockReasoner(mode).analyze(make_ctx())


def test_modes_are_the_specified_seven():
    assert set(MOCK_MODES) == {
        "aligned", "misaligned", "injection", "compromised",
        "timeout", "invalid", "unavailable",
    }


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        MockReasoner("bogus")


@pytest.mark.parametrize(
    "mode,alignment,injection",
    [
        ("aligned", IntentAlignment.ALIGNED, False),
        ("misaligned", IntentAlignment.MISALIGNED, False),
        ("injection", IntentAlignment.MISALIGNED, True),
        ("compromised", IntentAlignment.ALIGNED, False),
    ],
)
def test_valid_modes(mode, alignment, injection):
    r = run(mode)
    assert isinstance(r, SemanticResult)
    assert r.status is SemanticStatus.VALID and r.source is SemanticSource.MOCK
    assert r.provider == f"mock:{mode}"
    assert r.findings.intent_alignment is alignment
    assert r.findings.injection_suspected is injection
    assert r.findings.ambiguity is Ambiguity.LOW
    assert r.error is None


@pytest.mark.parametrize(
    "mode,status",
    [
        ("timeout", SemanticStatus.TIMEOUT),
        ("invalid", SemanticStatus.INVALID),
        ("unavailable", SemanticStatus.UNAVAILABLE),
    ],
)
def test_failure_modes(mode, status):
    r = run(mode)
    assert r.status is status
    assert r.findings is None
    assert r.source is SemanticSource.NONE  # nothing produced a result
    assert r.provider == f"mock:{mode}"
    assert r.error.startswith("[MOCK]")


@pytest.mark.parametrize("mode", MOCK_MODES)
def test_every_text_starts_with_mock_marker(mode):
    r = run(mode)
    text = r.findings.rationale if r.findings else r.error
    assert text.startswith("[MOCK]")


def test_compromised_looks_clean_but_is_labelled():
    r = run("compromised")
    assert r.findings.intent_alignment is IntentAlignment.ALIGNED
    assert not r.findings.injection_suspected
    assert "ompromised" in r.findings.rationale


@pytest.mark.parametrize("mode", MOCK_MODES)
def test_deterministic_and_context_independent(mode):
    a = MockReasoner(mode).analyze(make_ctx(user_task="one", a=1))
    b = MockReasoner(mode).analyze(make_ctx(user_task="completely different", z=9))
    assert a == b
    assert MockReasoner(mode).analyze(make_ctx()) == MockReasoner(mode).analyze(make_ctx())


def test_result_carries_no_decision_field():
    dumped = run("compromised").model_dump()
    assert "decision" not in dumped and "BLOCK" not in str(dumped)
