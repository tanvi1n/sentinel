"""Replay of recorded real (LIVE) semantic results.

The cache is a JSONL file. Each line is one record:
  {"key", "recorded_at", "provider", "prompt_version", "source": "LIVE",
   "findings": {...}}
The key comes from replay_key(context, prompt_version). Only LIVE, VALID
results can be recorded or loaded; a miss is UNAVAILABLE and never invents
findings.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from app.contracts import (
    SemanticContext,
    SemanticFindings,
    SemanticResult,
    SemanticSource,
    SemanticStatus,
)
from app.semantic.base import SemanticReasoner, replay_key


def build_record(
    context: SemanticContext,
    prompt_version: str,
    result: SemanticResult,
    recorded_at: str | None = None,
) -> dict:
    """Make a cache record from a real result. Refuses anything not LIVE+VALID."""
    if (
        result.source is not SemanticSource.LIVE
        or result.status is not SemanticStatus.VALID
        or result.findings is None
    ):
        raise ValueError("only VALID results from a LIVE call can be recorded")
    if result.prompt_version is not None and result.prompt_version != prompt_version:
        # Never file a result under a prompt version that did not produce it.
        raise ValueError("result was produced with a different prompt version")
    return {
        "key": replay_key(context, prompt_version),
        "recorded_at": recorded_at or datetime.now(timezone.utc).isoformat(),
        "provider": result.provider or "unknown",
        "prompt_version": prompt_version,
        "source": SemanticSource.LIVE.value,
        "findings": result.findings.model_dump(mode="json"),
    }


def _parse_record(line: str) -> tuple[str, dict] | None:
    """Return (key, record) for an acceptable record line, else None."""
    try:
        rec = json.loads(line)
        if not isinstance(rec, dict):
            return None
        key, recorded_at = rec["key"], rec["recorded_at"]
        provider, version = rec["provider"], rec["prompt_version"]
        if not all(isinstance(v, str) for v in (key, recorded_at, provider, version)):
            return None
        # Real recordings only: anything not marked LIVE is not a recording.
        if rec.get("source") != SemanticSource.LIVE.value:
            return None
        if provider.lower().startswith("mock"):
            return None
        SemanticFindings.model_validate(rec["findings"])
    except (ValueError, KeyError, TypeError, ValidationError):
        return None
    return key, rec


class ReplayReasoner(SemanticReasoner):
    def __init__(self, path: str | Path, prompt_version: str) -> None:
        self.path = Path(path)
        self.prompt_version = prompt_version
        self.rejected = 0  # lines present in the file but not usable
        self._records: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return  # no cache: every lookup is a miss
        for line in text.splitlines():
            if not line.strip():
                continue
            parsed = _parse_record(line)
            if parsed is None:
                self.rejected += 1
            else:
                self._records[parsed[0]] = parsed[1]  # later line wins

    def __len__(self) -> int:
        return len(self._records)

    def _analyze(self, context: SemanticContext) -> SemanticResult:
        rec = self._records.get(replay_key(context, self.prompt_version))
        if rec is None:
            return SemanticResult(
                status=SemanticStatus.UNAVAILABLE,
                source=SemanticSource.NONE,
                error="replay miss",
            )
        return SemanticResult(
            status=SemanticStatus.VALID,
            source=SemanticSource.REPLAY,
            provider=f"replay:{rec['provider']}",
            recorded_at=rec["recorded_at"],
            prompt_version=rec["prompt_version"],
            findings=SemanticFindings.model_validate(rec["findings"]),
        )


def append_record(path: str | Path, record: dict) -> None:
    """Append one record as a JSONL line (keys sorted, so output is stable)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")


def record_contexts(
    contexts: list[SemanticContext],
    reasoner: SemanticReasoner,
    path: str | Path,
    prompt_version: str,
    recorded_at: str | None = None,
) -> dict:
    """Run each context through `reasoner` and record only real LIVE results.

    Anything else (a mock, a replay, a failure) is refused and reported, never
    written. A context already in the cache is skipped, not re-asked.
    """
    summary = {"recorded": 0, "already_recorded": 0, "refused": []}
    seen: set[str] = set()
    existing = ReplayReasoner(path, prompt_version)
    for index, context in enumerate(contexts):
        key = replay_key(context, prompt_version)
        if key in seen or existing.analyze(context).status is SemanticStatus.VALID:
            summary["already_recorded"] += 1
            continue
        result = reasoner.analyze(context)
        try:
            record = build_record(context, prompt_version, result, recorded_at)
        except ValueError as exc:
            reason = result.error or str(exc)
            summary["refused"].append({"index": index, "reason": reason})
            continue
        append_record(path, record)
        seen.add(key)
        summary["recorded"] += 1
    return summary
