"""Chooses the semantic provider by mode.

Modes: mock, replay, live, auto. `auto` tries LIVE, then REPLAY, then reports
UNAVAILABLE. It never falls back to MOCK, and whatever answers keeps its own
truthful source label.
"""

import os
from collections.abc import Mapping

from app.semantic.models import (
    ProviderResult,
    SemanticContext,
    SemanticSource,
    SemanticStatus,
)
from app.semantic.base import SemanticReasoner
from app.semantic.mock import MOCK_MODES, MockReasoner
from app.semantic.qualcomm import ModelCall, QualcommConfig, QualcommReasoner, default_call_model
from app.semantic.replay import ReplayReasoner

MODES = ("mock", "replay", "live", "auto")
DEFAULT_MODE = "mock"
DEFAULT_REPLAY_PATH = "replay/cache.jsonl"


class _AutoReasoner(SemanticReasoner):
    """LIVE, then REPLAY, then UNAVAILABLE. Never MOCK."""

    def __init__(self, live: SemanticReasoner, replay: SemanticReasoner) -> None:
        self._live = live
        self._replay = replay

    def _analyze(self, context: SemanticContext) -> ProviderResult:
        first = self._live.analyze(context)
        if first.status is SemanticStatus.VALID:
            return first
        second = self._replay.analyze(context)
        if second.status is SemanticStatus.VALID:
            return second
        return ProviderResult(
            status=SemanticStatus.UNAVAILABLE,
            source=SemanticSource.NONE,
            error=f"live: {first.error or first.status.value}; "
            f"replay: {second.error or second.status.value}",
        )


class SemanticFactory:
    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        call_model: ModelCall = default_call_model,
    ) -> None:
        env = os.environ if env is None else env
        self._config = QualcommConfig.from_env(env)  # key read once, here
        self._call_model = call_model
        self._replay_path = env.get("SENTINEL_REPLAY_PATH") or DEFAULT_REPLAY_PATH
        self._mode = DEFAULT_MODE
        self._mock_mode = "aligned"
        self.set_mode(env.get("SENTINEL_SEMANTIC_MODE") or DEFAULT_MODE)

    def set_mode(self, mode: str, mock_mode: str | None = None) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown semantic mode: {mode!r}")
        if mock_mode is not None and mock_mode not in MOCK_MODES:
            raise ValueError(f"unknown mock mode: {mock_mode!r}")
        self._mode = mode
        if mock_mode is not None:
            self._mock_mode = mock_mode

    def get_reasoner(self, mock_mode: str | None = None) -> SemanticReasoner:
        """The reasoner for the current mode.

        `mock_mode` picks the mock variant for this call only, and applies only
        in mock mode; in any other mode it is ignored, so it can never turn a
        replay or live run into a mock one.
        """
        if self._mode == "mock":
            if mock_mode is not None and mock_mode not in MOCK_MODES:
                raise ValueError(f"unknown mock mode: {mock_mode!r}")
            return MockReasoner(mock_mode or self._mock_mode)
        if self._mode == "replay":
            return self._replay()
        if self._mode == "live":
            return self._live()
        return _AutoReasoner(self._live(), self._replay())

    def describe(self) -> dict:
        """For the health route. Never contains the key."""
        provider = {
            "mock": f"mock:{self._mock_mode}",
            "replay": "replay",
            "live": f"qualcomm:{self._config.model}" if self._config.model else "qualcomm",
            "auto": "auto (live, then replay)",
        }[self._mode]
        return {
            "mode": self._mode,
            "provider": provider,
            "key_configured": self._config.key_configured,
            "prompt_version": self._config.prompt_version,
        }

    def _live(self) -> QualcommReasoner:
        return QualcommReasoner(self._config, self._call_model)

    def _replay(self) -> ReplayReasoner:
        return ReplayReasoner(self._replay_path, self._config.prompt_version)


_default: SemanticFactory | None = None


def _default_factory() -> SemanticFactory:
    global _default
    if _default is None:
        _default = SemanticFactory()
    return _default


def get_reasoner(mock_mode: str | None = None) -> SemanticReasoner:
    return _default_factory().get_reasoner(mock_mode)


def set_mode(mode: str, mock_mode: str | None = None) -> None:
    _default_factory().set_mode(mode, mock_mode)


def describe() -> dict:
    return _default_factory().describe()
