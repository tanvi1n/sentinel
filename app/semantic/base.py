"""Provider-agnostic semantic layer base.

Holds the reasoner interface, the single canonical SemanticContext builder and
the canonical serialization / replay key. It knows nothing about any tool or
domain, and it never sees or produces a guard decision: providers only return
a ProviderResult, and combine() (guard side) decides what to do with it.
"""

import copy
import hashlib
import json
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from app.semantic.models import (
    ProviderResult,
    SemanticContext,
    SemanticFlaggedArg,
    SemanticObservedItem,
    SemanticSource,
    SemanticStatus,
)

# Observed text is truncated to this many characters when shown to the model.
MAX_OBSERVED_CHARS = 1500


class SemanticReasoner(ABC):
    """The only interface the rest of Sentinel sees.

    Callers use analyze(). It never raises: providers implement _analyze(),
    and analyze() turns any exception or malformed return into a ProviderResult
    with a non-valid status. Subclasses may not override analyze().
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "analyze" in cls.__dict__:
            raise TypeError(
                f"{cls.__name__} must implement _analyze(), not override analyze()"
            )

    @abstractmethod
    def _analyze(self, context: SemanticContext) -> ProviderResult:
        """Provider logic. May raise; analyze() contains the failure."""

    def analyze(self, context: SemanticContext) -> ProviderResult:
        try:
            result = self._analyze(context)
        except TimeoutError:
            return _failure(SemanticStatus.TIMEOUT, "TimeoutError")
        except Exception as exc:  # noqa: BLE001 - contract: never raise
            # Only the exception type is reported; messages may carry secrets.
            return _failure(SemanticStatus.UNAVAILABLE, type(exc).__name__)
        if not isinstance(result, ProviderResult):
            return _failure(
                SemanticStatus.INVALID, "provider returned a non-ProviderResult"
            )
        return result


def _failure(status: SemanticStatus, error: str) -> ProviderResult:
    return ProviderResult(status=status, source=SemanticSource.NONE, error=error)


def skipped_result() -> ProviderResult:
    """Semantic reasoning intentionally not run (the tool is marked never)."""
    return ProviderResult(status=SemanticStatus.SKIPPED, source=SemanticSource.NONE)


def build_semantic_context(
    *,
    tool_id: str,
    tool_description: str,
    arguments: dict[str, Any],
    user_task: str,
    justification: str = "",
    observed_content: Sequence[SemanticObservedItem] = (),
    flagged_args: Sequence[SemanticFlaggedArg] = (),
) -> SemanticContext:
    """The single place a SemanticContext is built.

    Takes only trusted inputs: the tool id and description from the registry,
    the normalized arguments, the user's task, content recorded by the trusted
    side and the flags raised by the origin check. The agent's own claims about
    risk or permission never enter here.

    Observed text is truncated, and both lists are sorted so that equivalent
    inputs always produce the same context (and so the same replay key).
    Labels and item ids are preserved. Nothing else is added, so the
    deterministic verdict and the session history cannot reach the model.
    """
    observed = sorted(
        (
            SemanticObservedItem(
                item_id=item.item_id,
                label=item.label,
                text=item.text[:MAX_OBSERVED_CHARS],
            )
            for item in observed_content
        ),
        key=lambda i: (i.item_id, i.label.value, i.text),
    )
    flagged = sorted(
        (
            SemanticFlaggedArg(
                arg=flag.arg, origin_label=flag.origin_label, item_id=flag.item_id
            )
            for flag in flagged_args
        ),
        key=lambda f: (f.arg, f.item_id or "", f.origin_label.value),
    )
    return SemanticContext(
        tool_id=tool_id,
        tool_description=tool_description,
        canonical_args=copy.deepcopy(arguments),
        user_task=user_task,
        justification=justification,
        observed_content=observed,
        flagged_args=flagged,
    )


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8 characters kept."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_context_json(context: SemanticContext) -> str:
    """Canonical text of a context; equal contexts give equal text."""
    return canonical_json(context.model_dump(mode="json"))


def replay_key(context: SemanticContext, prompt_version: str) -> str:
    """Stable SHA-256 hex key for the replay cache.

    Built from the canonical context and the prompt version only: no
    timestamps, ids or Python hash(), so it is the same across runs and
    machines, and it changes when either input changes.
    """
    payload = canonical_json(
        {"prompt_version": prompt_version, "context": context.model_dump(mode="json")}
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
