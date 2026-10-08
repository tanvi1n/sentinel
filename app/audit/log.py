"""
audit/log.py
============
AuditLog — in-memory append-only audit log.

Every significant lifecycle event is recorded here.
The log is never modified after writing — only appended.

Thread-safe: uses a threading.Lock.

For the demo, the log is in-memory. It can be optionally persisted
to a JSONL file (but we don't commit logs per .gitignore).

Owned by: Person 2
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from app.contracts.audit import AuditEntry, AuditEvent


class AuditLog:
    """
    In-memory append-only audit log.

    Usage:
        log = AuditLog()
        log.record(AuditEvent.EVALUATED, decision_id=..., tool=..., ...)
        entries = log.get_all()
    """

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []
        self._lock = threading.Lock()

    def record(
        self,
        event: AuditEvent,
        *,
        decision_id: str | None = None,
        agent_id: str | None = None,
        tool: str | None = None,
        outcome: str | None = None,
        lifecycle: str | None = None,
        message: str = "",
        metadata: dict | None = None,
        latency_ms: float | None = None,
    ) -> AuditEntry:
        """
        Append a new audit entry.

        Returns the created entry.
        """
        entry = AuditEntry(
            entry_id=str(uuid.uuid4()),
            event=event,
            decision_id=decision_id,
            agent_id=agent_id,
            tool=tool,
            outcome=outcome,
            lifecycle=lifecycle,
            message=message,
            metadata=metadata or {},
            latency_ms=latency_ms,
            timestamp=datetime.now(timezone.utc),
        )
        with self._lock:
            self._entries.append(entry)
        return entry

    def get_all(self) -> list[AuditEntry]:
        """Return all audit entries (chronological order)."""
        with self._lock:
            return list(self._entries)

    def get_for_decision(self, decision_id: str) -> list[AuditEntry]:
        """Return all entries for a given decision_id."""
        with self._lock:
            return [e for e in self._entries if e.decision_id == decision_id]

    def get_recent(self, n: int = 50) -> list[AuditEntry]:
        """Return the n most recent entries."""
        with self._lock:
            return list(self._entries[-n:])

    def clear(self) -> None:
        """Clear all audit entries (for tests / demo reset)."""
        with self._lock:
            self._entries.clear()

    def count(self) -> int:
        with self._lock:
            return len(self._entries)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_audit_log: AuditLog | None = None


def get_audit_log() -> AuditLog:
    global _audit_log
    if _audit_log is None:
        _audit_log = AuditLog()
    return _audit_log


def reset_audit_log() -> AuditLog:
    global _audit_log
    _audit_log = AuditLog()
    return _audit_log
