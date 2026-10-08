"""
guard/gateway.py
================
ExecutionGateway — the ONLY path from decision to tool execution.

The agent NEVER calls tools directly.
The architecture is:
    Agent
      ↓ ActionProposal
    GuardClient / Service
      ↓ GuardDecision
    ExecutionGateway          ← YOU ARE HERE
      ↓ (revalidation + integrity check)
    ToolExecutor
      ↓
    Tool
      ↓
    WorldState

Gateway responsibilities:
  1. Verify integrity hash (detect tampering)
  2. Revalidate with current policies (no semantic)
  3. Transition lifecycle to EXECUTING
  4. Execute via ToolExecutor
  5. Transition lifecycle to EXECUTED or EXECUTION_FAILED
  6. Update session context if needed
  7. Record audit entry

Undo path:
  1. Verify decision is in EXECUTED state
  2. Verify tool supports undo
  3. Call executor.undo()
  4. Transition to UNDONE

Owned by: Person 2
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from app.contracts.audit import AuditEvent
from app.contracts.decision import DecisionLifecycle, GuardDecision
from app.contracts.errors import (
    IntegrityError,
    RevalidationFailed,
    ToolExecutionError,
    UndoUnavailable,
    AlreadyUndone,
)
from app.guard.engine import GuardEngine, get_guard_engine
from app.guard.rules import SessionContext
from app.guard.store import DecisionStore, get_decision_store
from app.audit.log import AuditLog, get_audit_log
from app.world.executor import ToolExecutor, get_tool_executor
from app.world.tools.base import ExecutionResult


class ExecutionGateway:
    """
    Trusted gateway: the only path from an approved decision to actual tool execution.
    """

    def __init__(
        self,
        store: DecisionStore | None = None,
        engine: GuardEngine | None = None,
        executor: ToolExecutor | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self._store = store or get_decision_store()
        self._engine = engine or get_guard_engine()
        self._executor = executor or get_tool_executor()
        self._audit = audit or get_audit_log()

    def execute(
        self,
        decision_id: str,
        session_ctx: SessionContext | None = None,
    ) -> ExecutionResult:
        """
        Execute the action associated with a stored decision.

        This is the enforcement point for:
          - Integrity verification
          - Execution-time revalidation
          - Lifecycle state management

        Raises:
          BlockedDecision         : decision is BLOCKED
          RejectedDecision        : decision was REJECTED
          AlreadyExecuted         : already executed
          ReviewRequired          : needs human approval first
          IntegrityError          : stored action was tampered with
          RevalidationFailed      : action is no longer safe
          ToolExecutionError      : tool raised an error
        """
        # --- Step 1: Check decision state and integrity ----------------------
        # assert_executable() raises IntegrityError if the canonical action
        # has been tampered with. We catch it here to record the audit event
        # before re-raising — critical for security accountability.
        try:
            decision = self._store.assert_executable(decision_id)
        except IntegrityError as exc:
            # Try to get whatever we can from the store for the audit entry
            try:
                raw = self._store.get(decision_id)
                _agent_id = raw.canonical_action.agent_id
                _tool = raw.canonical_action.tool
                _outcome = raw.outcome.value
                _lifecycle = raw.lifecycle.value
            except Exception:
                _agent_id = _tool = _outcome = _lifecycle = None

            self._audit.record(
                AuditEvent.INTEGRITY_FAILED,
                decision_id=decision_id,
                agent_id=_agent_id,
                tool=_tool,
                outcome=_outcome,
                lifecycle=DecisionLifecycle.INTEGRITY_FAILED.value,
                message=f"Integrity check FAILED — stored action was tampered: {exc}",
                metadata={"error": str(exc)},
            )
            # Update lifecycle to INTEGRITY_FAILED if possible
            try:
                self._store.update_lifecycle(
                    decision_id, DecisionLifecycle.INTEGRITY_FAILED
                )
            except Exception:
                pass  # best-effort — don't mask the original error
            raise

        # --- Step 2: Execution-time revalidation (deterministic, no semantic) ---
        start_time = time.monotonic()
        is_valid, reason = self._engine.revalidate(decision, session_ctx)
        latency_ms = (time.monotonic() - start_time) * 1000

        if not is_valid:
            # Update decision lifecycle
            self._store.update_lifecycle(
                decision_id,
                DecisionLifecycle.REVALIDATION_FAILED,
            )
            self._audit.record(
                AuditEvent.REVALIDATION_FAILED,
                decision_id=decision_id,
                agent_id=decision.canonical_action.agent_id,
                tool=decision.canonical_action.tool,
                outcome=decision.outcome.value,
                lifecycle=DecisionLifecycle.REVALIDATION_FAILED.value,
                message=f"Revalidation failed: {reason}",
                latency_ms=latency_ms,
            )
            raise RevalidationFailed(reason)

        self._audit.record(
            AuditEvent.REVALIDATION_PASSED,
            decision_id=decision_id,
            agent_id=decision.canonical_action.agent_id,
            tool=decision.canonical_action.tool,
            outcome=decision.outcome.value,
            message="Revalidation passed.",
            latency_ms=latency_ms,
        )

        # --- Step 3: Transition to EXECUTING ---------------------------------
        decision = self._store.update_lifecycle(
            decision_id, DecisionLifecycle.EXECUTING
        )
        self._audit.record(
            AuditEvent.EXECUTING,
            decision_id=decision_id,
            agent_id=decision.canonical_action.agent_id,
            tool=decision.canonical_action.tool,
            outcome=decision.outcome.value,
            lifecycle=DecisionLifecycle.EXECUTING.value,
            message="Execution started.",
        )

        # --- Step 4: Execute via trusted executor ----------------------------
        canonical = decision.canonical_action
        try:
            exec_start = time.monotonic()
            result = self._executor.execute(
                tool_id=canonical.tool,
                arguments=canonical.arguments,
                decision_id=decision_id,
            )
            exec_latency = (time.monotonic() - exec_start) * 1000
        except ToolExecutionError as exc:
            self._store.update_lifecycle(
                decision_id, DecisionLifecycle.EXECUTION_FAILED
            )
            self._audit.record(
                AuditEvent.EXECUTION_FAILED,
                decision_id=decision_id,
                agent_id=canonical.agent_id,
                tool=canonical.tool,
                outcome=decision.outcome.value,
                lifecycle=DecisionLifecycle.EXECUTION_FAILED.value,
                message=f"Tool execution failed: {exc}",
            )
            raise

        # --- Step 5: Transition to EXECUTED ----------------------------------
        self._store.update_lifecycle(
            decision_id,
            DecisionLifecycle.EXECUTED,
            executed_at=datetime.now(timezone.utc),
            execution_result=result.to_dict(),
        )
        self._audit.record(
            AuditEvent.EXECUTED,
            decision_id=decision_id,
            agent_id=canonical.agent_id,
            tool=canonical.tool,
            outcome=decision.outcome.value,
            lifecycle=DecisionLifecycle.EXECUTED.value,
            message=result.label,
            metadata=result.metadata,
            latency_ms=exec_latency,
        )

        return result

    def undo(
        self,
        decision_id: str,
    ) -> ExecutionResult:
        """
        Undo a previously executed action.

        Only works for tools that support undo.
        Restores world to the exact state before execution.

        Raises:
          DecisionNotFound     : decision doesn't exist
          UndoUnavailable      : tool doesn't support undo
          AlreadyUndone        : already undone
          ToolExecutionError   : undo operation failed
        """
        decision = self._store.get(decision_id)
        canonical = decision.canonical_action

        if decision.lifecycle == DecisionLifecycle.UNDONE:
            raise AlreadyUndone(
                f"Decision '{decision_id}' has already been undone."
            )

        if decision.lifecycle != DecisionLifecycle.EXECUTED:
            raise UndoUnavailable(
                f"Can only undo an EXECUTED decision. "
                f"Current lifecycle: {decision.lifecycle.value}."
            )

        self._audit.record(
            AuditEvent.UNDO_REQUESTED,
            decision_id=decision_id,
            agent_id=canonical.agent_id,
            tool=canonical.tool,
            outcome=decision.outcome.value,
            message="Undo requested.",
        )

        try:
            result = self._executor.undo(
                tool_id=canonical.tool,
                arguments=canonical.arguments,
                decision_id=decision_id,
            )
        except (UndoUnavailable, AlreadyUndone) as exc:
            self._audit.record(
                AuditEvent.UNDO_FAILED,
                decision_id=decision_id,
                agent_id=canonical.agent_id,
                tool=canonical.tool,
                message=str(exc),
            )
            raise
        except ToolExecutionError as exc:
            self._audit.record(
                AuditEvent.UNDO_FAILED,
                decision_id=decision_id,
                agent_id=canonical.agent_id,
                tool=canonical.tool,
                message=f"Undo execution error: {exc}",
            )
            raise

        # Transition to UNDONE
        self._store.update_lifecycle(decision_id, DecisionLifecycle.UNDONE)
        self._audit.record(
            AuditEvent.UNDONE,
            decision_id=decision_id,
            agent_id=canonical.agent_id,
            tool=canonical.tool,
            outcome=decision.outcome.value,
            lifecycle=DecisionLifecycle.UNDONE.value,
            message=result.label,
        )

        return result


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_gateway: ExecutionGateway | None = None


def get_execution_gateway() -> ExecutionGateway:
    global _gateway
    if _gateway is None:
        _gateway = ExecutionGateway()
    return _gateway


def reset_execution_gateway(
    store: DecisionStore | None = None,
    engine: GuardEngine | None = None,
    executor: ToolExecutor | None = None,
    audit: AuditLog | None = None,
) -> ExecutionGateway:
    global _gateway
    _gateway = ExecutionGateway(store, engine, executor, audit)
    return _gateway
