"""
guard/store.py
==============
DecisionStore — trusted in-memory store for GuardDecision records.

The store is the lifecycle record of every evaluated action.
Execution MUST retrieve the canonical action from the store —
it must NOT trust the original request again.

Thread safety: a threading.Lock guards all mutations.
This is a demo-appropriate in-memory store. No external database needed.

Owned by: Person 2
"""

from __future__ import annotations

import threading
from typing import Iterator

from app.contracts.decision import DecisionLifecycle, DecisionOutcome, GuardDecision
from app.contracts.errors import (
    AlreadyExecuted,
    BlockedDecision,
    DecisionNotFound,
    IntegrityError,
    InvalidLifecycleTransition,
    RejectedDecision,
    ReviewRequired,
)
from app.guard.intake import verify_integrity


class DecisionStore:
    """
    In-memory store for GuardDecision records.

    Responsibilities:
      - Store decisions indexed by decision_id
      - Enforce valid lifecycle transitions
      - Verify integrity hashes before execution
      - Provide query interface for service/gateway layers
    """

    def __init__(self) -> None:
        self._decisions: dict[str, GuardDecision] = {}
        self._lock = threading.Lock()

    # -------------------------------------------------------------------------
    # Write operations
    # -------------------------------------------------------------------------

    def save(self, decision: GuardDecision) -> GuardDecision:
        """Save or overwrite a decision record."""
        with self._lock:
            self._decisions[decision.decision_id] = decision
        return decision

    def update_lifecycle(
        self,
        decision_id: str,
        new_lifecycle: DecisionLifecycle,
        **kwargs,
    ) -> GuardDecision:
        """
        Transition a decision to a new lifecycle state.

        Validates the transition against VALID_TRANSITIONS.
        Additional fields (reviewer_id, execution_result, etc.) can be
        passed as kwargs and will be set on the updated decision.
        """
        from app.contracts.decision import VALID_TRANSITIONS

        with self._lock:
            decision = self._get_or_raise(decision_id)

            # Validate the transition
            # We look up by (current_lifecycle, implied_action)
            # Since we're calling this with the target lifecycle directly,
            # we verify that at least one valid transition leads there.
            valid_targets = {
                target
                for (src, _action), target in VALID_TRANSITIONS.items()
                if src == decision.lifecycle
            }
            if new_lifecycle not in valid_targets:
                raise InvalidLifecycleTransition(
                    f"Cannot transition decision '{decision_id}' from "
                    f"'{decision.lifecycle.value}' to '{new_lifecycle.value}'. "
                    f"Valid next states: {[s.value for s in valid_targets]}."
                )

            # Build updated decision (Pydantic models are immutable by default —
            # use model_copy with update)
            updates = {"lifecycle": new_lifecycle, **kwargs}
            updated = decision.model_copy(update=updates)
            self._decisions[decision_id] = updated
            return updated

    # -------------------------------------------------------------------------
    # Read operations
    # -------------------------------------------------------------------------

    def get(self, decision_id: str) -> GuardDecision:
        """Return a stored decision, raising DecisionNotFound if absent."""
        with self._lock:
            return self._get_or_raise(decision_id)

    def list_all(self) -> list[GuardDecision]:
        """Return all stored decisions (most recent last)."""
        with self._lock:
            return list(self._decisions.values())

    def list_pending_review(self) -> list[GuardDecision]:
        """Return decisions awaiting human review."""
        with self._lock:
            return [
                d for d in self._decisions.values()
                if d.lifecycle == DecisionLifecycle.PENDING_REVIEW
            ]

    # -------------------------------------------------------------------------
    # Pre-execution checks (called by gateway before execution)
    # -------------------------------------------------------------------------

    def assert_executable(self, decision_id: str) -> GuardDecision:
        """
        Assert that a decision is in a state that allows execution.

        Raises specific errors for each invalid state so the API can
        return meaningful error responses.

        Checks:
          1. Decision exists
          2. Not BLOCKED
          3. Not REJECTED
          4. Not already EXECUTED / EXECUTING / UNDONE
          5. If outcome is REVIEW, must be APPROVED first
          6. Integrity hash must be valid
        """
        with self._lock:
            decision = self._get_or_raise(decision_id)

        # Check outcome
        if decision.outcome == DecisionOutcome.BLOCK:
            raise BlockedDecision(
                f"Decision '{decision_id}' is BLOCKED and cannot be executed."
            )

        # Check lifecycle
        if decision.lifecycle == DecisionLifecycle.REJECTED:
            raise RejectedDecision(
                f"Decision '{decision_id}' was REJECTED and cannot be executed."
            )

        if decision.lifecycle in (
            DecisionLifecycle.EXECUTED,
            DecisionLifecycle.EXECUTING,
            DecisionLifecycle.UNDONE,
        ):
            raise AlreadyExecuted(
                f"Decision '{decision_id}' has already been executed "
                f"(lifecycle: {decision.lifecycle.value})."
            )

        # REVIEW requires approval
        if (
            decision.outcome == DecisionOutcome.REVIEW
            and decision.lifecycle not in (
                DecisionLifecycle.APPROVED,
            )
        ):
            raise ReviewRequired(
                f"Decision '{decision_id}' requires human approval before execution. "
                f"Current lifecycle: {decision.lifecycle.value}."
            )

        # Integrity check
        if not verify_integrity(decision.canonical_action):
            raise IntegrityError(
                f"Integrity check failed for decision '{decision_id}'. "
                f"The stored canonical action has been tampered with."
            )

        return decision

    # -------------------------------------------------------------------------
    # Admin / reset
    # -------------------------------------------------------------------------

    def clear(self) -> None:
        """Clear all stored decisions (for tests / demo reset)."""
        with self._lock:
            self._decisions.clear()

    def count(self) -> int:
        with self._lock:
            return len(self._decisions)

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _get_or_raise(self, decision_id: str) -> GuardDecision:
        """Must be called with lock held."""
        try:
            return self._decisions[decision_id]
        except KeyError:
            raise DecisionNotFound(f"No decision found with id '{decision_id}'.")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: DecisionStore | None = None


def get_decision_store() -> DecisionStore:
    """Return the module-level DecisionStore singleton."""
    global _store
    if _store is None:
        _store = DecisionStore()
    return _store


def reset_decision_store() -> DecisionStore:
    """Create a fresh store (for tests / demo reset)."""
    global _store
    _store = DecisionStore()
    return _store
