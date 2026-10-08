"""
guard/service.py
================
GuardService — business logic layer.

Thin business logic that coordinates:
  - GuardEngine (evaluation)
  - DecisionStore (storage)
  - ExecutionGateway (execution)
  - AuditLog (audit)
  - SessionContext (session-scoped rules)

The API routes call the service. The service never contains HTTP logic.

API routes must be thin wrappers:
  POST /evaluate  → service.evaluate()
  POST /review    → service.review()
  POST /execute   → service.execute()
  POST /undo      → service.undo()
  GET  /audit     → service.get_audit()

Owned by: Person 2
"""

from __future__ import annotations

import time
import threading
from datetime import datetime, timezone

from app.contracts.audit import AuditEvent
from app.contracts.decision import (
    DecisionLifecycle,
    DecisionOutcome,
    GuardDecision,
    VALID_TRANSITIONS,
)
from app.contracts.errors import (
    BlockedDecision,
    DecisionNotFound,
    InvalidLifecycleTransition,
    RejectedDecision,
)
from app.contracts.proposal import ActionProposal
from app.contracts.semantic import SemanticResult
from app.guard.engine import GuardEngine, get_guard_engine
from app.guard.gateway import ExecutionGateway, get_execution_gateway
from app.guard.rules import SessionContext
from app.guard.store import DecisionStore, get_decision_store
from app.audit.log import AuditLog, AuditEntry, get_audit_log
from app.world.tools.base import ExecutionResult


class GuardService:
    """
    Orchestrates the complete evaluate → review → execute → undo lifecycle.

    This is the primary entry point for all Person 2 functionality.
    """

    def __init__(
        self,
        engine: GuardEngine | None = None,
        store: DecisionStore | None = None,
        gateway: ExecutionGateway | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self._engine = engine or get_guard_engine()
        self._store = store or get_decision_store()
        self._gateway = gateway or get_execution_gateway()
        self._audit = audit or get_audit_log()

        # Per-session context tracking
        # Maps session_id → SessionContext
        self._sessions: dict[str, SessionContext] = {}
        self._session_lock = threading.Lock()

    # -------------------------------------------------------------------------
    # Session management
    # -------------------------------------------------------------------------

    def _get_or_create_session(self, session_id: str | None) -> SessionContext:
        """Return or create the SessionContext for a session."""
        if not session_id:
            return SessionContext()
        with self._session_lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = SessionContext()
            return self._sessions[session_id]

    def _update_session_after_execution(
        self,
        session_id: str | None,
        tool_id: str,
    ) -> None:
        """Update session state after a successful execution."""
        if not session_id:
            return
        with self._session_lock:
            ctx = self._sessions.get(session_id)
            if ctx is None:
                return
            ctx.executed_tools.add(tool_id)
            if tool_id == "email_read":
                ctx.private_read_executed = True

    # -------------------------------------------------------------------------
    # evaluate()
    # -------------------------------------------------------------------------

    def evaluate(
        self,
        proposal: ActionProposal,
        semantic_result: SemanticResult | None = None,
        session_id: str | None = None,
        harness_provenance: dict[str, str] | None = None,
    ) -> GuardDecision:
        """
        Evaluate a proposal and store the resulting decision.

        Parameters:
          proposal           : agent's ActionProposal (untrusted)
          semantic_result    : optional result from Person 1's semantic layer
          session_id         : optional session ID for session-scoped rules
          harness_provenance : TRUSTED provenance map supplied by the harness.
                               Maps argument name → ProvenanceLabel string.
                               MUST NOT come from the agent — only the trusted
                               harness may supply this.

        The harness_provenance is injected into the proposal before evaluation.
        This is the correct trust boundary: the agent submits a raw proposal,
        the harness annotates which argument values came from which source
        (user_task, external_content, system, agent_internal).

        Returns the stored GuardDecision.
        """
        # Inject trusted harness provenance into the proposal if provided.
        # This replaces any agent-supplied arg_provenance (which should be empty).
        if harness_provenance:
            # model_copy preserves all other fields
            proposal = proposal.model_copy(
                update={"arg_provenance": harness_provenance}
            )

        session_ctx = self._get_or_create_session(
            session_id or proposal.context.session_id
        )

        start = time.monotonic()

        # Audit: proposal received
        self._audit.record(
            AuditEvent.ACTION_PROPOSED,
            agent_id=proposal.agent_id,
            tool=proposal.tool,
            message=f"Agent '{proposal.agent_id}' proposed '{proposal.tool}'.",
        )

        # Evaluate
        try:
            decision = self._engine.evaluate(proposal, semantic_result, session_ctx)
        except Exception as exc:
            self._audit.record(
                AuditEvent.INTAKE_REJECTED,
                agent_id=proposal.agent_id,
                tool=proposal.tool,
                message=f"Intake rejected: {exc}",
            )
            raise

        latency_ms = (time.monotonic() - start) * 1000

        # Store decision
        self._store.save(decision)

        # Audit: evaluation complete
        if decision.outcome == DecisionOutcome.BLOCK:
            event = AuditEvent.BLOCKED
        elif decision.outcome == DecisionOutcome.REVIEW:
            event = AuditEvent.REVIEW_REQUESTED
        else:
            event = AuditEvent.EVALUATED

        self._audit.record(
            event,
            decision_id=decision.decision_id,
            agent_id=proposal.agent_id,
            tool=proposal.tool,
            outcome=decision.outcome.value,
            lifecycle=decision.lifecycle.value,
            message=(
                f"Decision: {decision.outcome.value}. "
                f"Risk: {decision.risk_score:.2f} ({decision.risk_label}). "
                f"Rules fired: {decision.rules_fired}."
            ),
            metadata={
                "risk_score": decision.risk_score,
                "rules_fired": decision.rules_fired,
                "semantic_outcome": decision.semantic_outcome,
                "semantic_provider": decision.semantic_provider,
            },
            latency_ms=latency_ms,
        )

        return decision

    # -------------------------------------------------------------------------
    # review() — human approval / rejection
    # -------------------------------------------------------------------------

    def approve(
        self,
        decision_id: str,
        reviewer_id: str,
    ) -> GuardDecision:
        """
        Approve a REVIEW decision (human reviewer action).

        Human approval is ONLY valid when decision.outcome == REVIEW.

        APPROVE decisions proceed directly to execution without human approval.
        BLOCK decisions can never be approved.

        Raises:
          DecisionNotFound          : decision doesn't exist
          BlockedDecision           : cannot approve a BLOCK
          InvalidLifecycleTransition: not in a reviewable state, or outcome != REVIEW
        """
        decision = self._store.get(decision_id)

        # Hard rule: BLOCK cannot be approved
        if decision.outcome == DecisionOutcome.BLOCK:
            raise BlockedDecision(
                f"Decision '{decision_id}' is BLOCKED and cannot be approved."
            )

        # Hard rule: only REVIEW outcomes require and accept human approval.
        # APPROVE decisions execute directly — they must not be "approved" manually.
        if decision.outcome != DecisionOutcome.REVIEW:
            raise InvalidLifecycleTransition(
                f"Decision '{decision_id}' has outcome '{decision.outcome.value}' "
                f"and does not require human approval. "
                f"Only REVIEW decisions can be approved."
            )

        if decision.lifecycle not in (
            DecisionLifecycle.PENDING_REVIEW,
            DecisionLifecycle.EVALUATED,
        ):
            raise InvalidLifecycleTransition(
                f"Decision '{decision_id}' is in lifecycle state "
                f"'{decision.lifecycle.value}' and cannot be approved."
            )

        updated = self._store.update_lifecycle(
            decision_id,
            DecisionLifecycle.APPROVED,
            reviewer_id=reviewer_id,
            decided_at=datetime.now(timezone.utc),
        )

        self._audit.record(
            AuditEvent.APPROVED,
            decision_id=decision_id,
            agent_id=decision.canonical_action.agent_id,
            tool=decision.canonical_action.tool,
            outcome=decision.outcome.value,
            lifecycle=DecisionLifecycle.APPROVED.value,
            message=f"Approved by reviewer '{reviewer_id}'.",
            metadata={"reviewer_id": reviewer_id},
        )

        return updated

    def reject(
        self,
        decision_id: str,
        reviewer_id: str,
        reason: str = "",
    ) -> GuardDecision:
        """
        Reject a REVIEW decision (human reviewer action).

        A rejected decision cannot be executed.
        """
        decision = self._store.get(decision_id)

        if decision.lifecycle not in (
            DecisionLifecycle.PENDING_REVIEW,
            DecisionLifecycle.EVALUATED,
        ):
            raise InvalidLifecycleTransition(
                f"Decision '{decision_id}' is in state '{decision.lifecycle.value}' "
                f"and cannot be rejected."
            )

        updated = self._store.update_lifecycle(
            decision_id,
            DecisionLifecycle.REJECTED,
            reviewer_id=reviewer_id,
            decided_at=datetime.now(timezone.utc),
        )

        self._audit.record(
            AuditEvent.REJECTED,
            decision_id=decision_id,
            agent_id=decision.canonical_action.agent_id,
            tool=decision.canonical_action.tool,
            outcome=decision.outcome.value,
            lifecycle=DecisionLifecycle.REJECTED.value,
            message=f"Rejected by reviewer '{reviewer_id}'. Reason: {reason}",
            metadata={"reviewer_id": reviewer_id, "reason": reason},
        )

        return updated

    # -------------------------------------------------------------------------
    # execute()
    # -------------------------------------------------------------------------

    def execute(
        self,
        decision_id: str,
        session_id: str | None = None,
    ) -> ExecutionResult:
        """
        Execute an approved or directly-approved action.

        Delegates to the gateway which handles:
          - integrity verification
          - revalidation
          - lifecycle transitions
          - tool execution
          - audit
        """
        decision = self._store.get(decision_id)
        session_ctx = self._get_or_create_session(session_id)

        result = self._gateway.execute(decision_id, session_ctx)

        # Update session after successful execution
        self._update_session_after_execution(
            session_id, decision.canonical_action.tool
        )

        return result

    # -------------------------------------------------------------------------
    # undo()
    # -------------------------------------------------------------------------

    def undo(self, decision_id: str) -> ExecutionResult:
        """
        Undo a previously executed action.

        Delegates to the gateway which handles:
          - tool undo support check
          - world state restoration
          - lifecycle transition to UNDONE
          - audit
        """
        return self._gateway.undo(decision_id)

    # -------------------------------------------------------------------------
    # Query methods
    # -------------------------------------------------------------------------

    def get_decision(self, decision_id: str) -> GuardDecision:
        """Get a stored decision by ID."""
        return self._store.get(decision_id)

    def list_decisions(self) -> list[GuardDecision]:
        """List all stored decisions."""
        return self._store.list_all()

    def list_pending_review(self) -> list[GuardDecision]:
        """List decisions pending human review."""
        return self._store.list_pending_review()

    def get_audit(self, n: int | None = None) -> list[AuditEntry]:
        """Return audit log entries."""
        if n is not None:
            return self._audit.get_recent(n)
        return self._audit.get_all()

    def get_audit_for_decision(self, decision_id: str) -> list[AuditEntry]:
        """Return audit entries for a specific decision."""
        return self._audit.get_for_decision(decision_id)

    def reset(self) -> None:
        """Reset all state for a fresh demo run."""
        from app.guard.store import reset_decision_store
        from app.audit.log import reset_audit_log
        from app.world.state import reset_world_store
        from app.world.executor import reset_tool_executor
        from app.guard.gateway import reset_execution_gateway

        reset_decision_store()
        reset_audit_log()
        store = reset_world_store()
        executor = reset_tool_executor(store)
        reset_execution_gateway(
            store=get_decision_store(),
            engine=self._engine,
            executor=executor,
            audit=get_audit_log(),
        )
        # Re-point our references
        self._store = get_decision_store()
        self._audit = get_audit_log()
        self._gateway = get_execution_gateway()
        with self._session_lock:
            self._sessions.clear()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_service: GuardService | None = None


def get_guard_service() -> GuardService:
    global _service
    if _service is None:
        _service = GuardService()
    return _service


def reset_guard_service() -> GuardService:
    global _service
    _service = GuardService()
    return _service
