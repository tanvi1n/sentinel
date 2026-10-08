"""
guard/client.py
===============
GuardClient — the primary interface for Person 1 to interact with
the SENTINEL guardrail system.

Person 1 uses GuardClient to:
  1. Evaluate an ActionProposal with optional semantic result
  2. Submit human review decisions (approve/reject)
  3. Execute an approved action
  4. Undo an executed action
  5. Query decisions and audit log

Person 1 should only need to:
  from app.guard.client import GuardClient
  from app.contracts import ActionProposal, SemanticResult, GuardDecision

And then call the client methods. No internal engine imports needed.

INTERFACE CONTRACT (stable — do not change without consulting Person 1):
  evaluate(proposal, semantic_result) → GuardDecision
  approve(decision_id, reviewer_id)   → GuardDecision
  reject(decision_id, reviewer_id)    → GuardDecision
  execute(decision_id)                → dict (execution result)
  undo(decision_id)                   → dict (undo result)
  get_decision(decision_id)           → GuardDecision
  list_decisions()                    → list[GuardDecision]
  list_pending_review()               → list[GuardDecision]
  get_audit_log(n)                    → list[AuditEntry]
  reset()                             → None

Owned by: Person 2
"""

from __future__ import annotations

from app.contracts.audit import AuditEntry
from app.contracts.decision import GuardDecision
from app.contracts.proposal import ActionProposal
from app.contracts.semantic import SemanticResult
from app.guard.service import GuardService, get_guard_service


class GuardClient:
    """
    High-level client interface for the SENTINEL guardrail system.

    This is the stable interface that Person 1 (agent simulator, harness,
    scenario runner) uses to interact with Person 2's guardrail engine.

    All internal implementation details are hidden behind this interface.
    """

    def __init__(self, service: GuardService | None = None) -> None:
        self._service = service or get_guard_service()

    def evaluate(
        self,
        proposal: ActionProposal,
        semantic_result: SemanticResult | None = None,
        session_id: str | None = None,
        harness_provenance: dict[str, str] | None = None,
    ) -> GuardDecision:
        """
        Evaluate an ActionProposal.

        Parameters:
          proposal           : the agent's proposed action (untrusted)
          semantic_result    : optional result from semantic provider
          session_id         : optional session identifier for session-scoped rules
          harness_provenance : TRUSTED provenance annotations from the harness.
                               Maps argument name → ProvenanceLabel string, e.g.:
                                 {"recipient": "external_content",
                                  "amount": "external_content"}
                               This MUST be supplied by the trusted harness,
                               never by the agent. The HTTP API does not accept
                               this field — only this Python interface does.

        TRUST BOUNDARY:
          The agent submits only:  agent_id, tool, arguments, context
          The harness annotates:   harness_provenance, semantic_result
          Sentinel decides:        everything else

        Returns:
          GuardDecision with outcome APPROVE | REVIEW | BLOCK

        Example (Scenario 4 — prompt injection):
            proposal = ActionProposal(
                agent_id="demo-agent",
                tool="payment_transfer",
                arguments={"amount": 100000, "recipient": "attacker"},
            )
            # Harness knows the agent read external content and used it here:
            decision = client.evaluate(
                proposal,
                harness_provenance={
                    "amount": "external_content",
                    "recipient": "external_content",
                }
            )
            assert decision.outcome == "BLOCK"  # PAYMENT_EXTERNAL_PROVENANCE fires
        """
        return self._service.evaluate(
            proposal, semantic_result, session_id, harness_provenance
        )

    def approve(
        self,
        decision_id: str,
        reviewer_id: str = "human-reviewer",
    ) -> GuardDecision:
        """
        Approve a REVIEW decision (human action).

        A BLOCK decision cannot be approved — this will raise BlockedDecision.
        A REVIEW decision must be approved before execute() is called.

        Parameters:
          decision_id : the GuardDecision.decision_id to approve
          reviewer_id : identity of the human reviewer

        Returns the updated GuardDecision.
        """
        return self._service.approve(decision_id, reviewer_id)

    def reject(
        self,
        decision_id: str,
        reviewer_id: str = "human-reviewer",
        reason: str = "",
    ) -> GuardDecision:
        """
        Reject a REVIEW decision (human action).

        A rejected decision cannot be executed.
        """
        return self._service.reject(decision_id, reviewer_id, reason)

    def execute(
        self,
        decision_id: str,
        session_id: str | None = None,
    ) -> dict:
        """
        Execute an approved action.

        The gateway performs integrity verification and execution-time
        revalidation before executing. If either fails, execution is refused.

        Returns a dict with execution result (tool output).

        Raises:
          BlockedDecision      : cannot execute a BLOCK
          RejectedDecision     : cannot execute a REJECTED decision
          ReviewRequired       : REVIEW decision needs approval first
          IntegrityError       : stored action was tampered
          RevalidationFailed   : action is no longer safe under current policy
          ToolExecutionError   : tool raised an error
        """
        result = self._service.execute(decision_id, session_id)
        return result.to_dict()

    def undo(self, decision_id: str) -> dict:
        """
        Undo a previously executed action.

        Only available for tools with undo_supported=True (e.g. file_delete).
        Restores the world to the exact state before execution.

        Returns a dict with undo result.

        Raises:
          UndoUnavailable  : tool doesn't support undo
          AlreadyUndone    : already undone
        """
        result = self._service.undo(decision_id)
        return result.to_dict()

    def get_decision(self, decision_id: str) -> GuardDecision:
        """Return the stored GuardDecision for the given ID."""
        return self._service.get_decision(decision_id)

    def list_decisions(self) -> list[GuardDecision]:
        """Return all stored decisions."""
        return self._service.list_decisions()

    def list_pending_review(self) -> list[GuardDecision]:
        """Return decisions waiting for human review."""
        return self._service.list_pending_review()

    def get_audit_log(self, n: int | None = None) -> list[AuditEntry]:
        """Return audit log entries (most recent n, or all if n is None)."""
        return self._service.get_audit(n)

    def get_audit_for_decision(self, decision_id: str) -> list[AuditEntry]:
        """Return all audit entries for a specific decision."""
        return self._service.get_audit_for_decision(decision_id)

    def reset(self) -> None:
        """Reset all state for a fresh demo run."""
        self._service.reset()


# ---------------------------------------------------------------------------
# Default client instance
# ---------------------------------------------------------------------------

def get_guard_client() -> GuardClient:
    """Return a GuardClient backed by the module-level service singleton."""
    return GuardClient(get_guard_service())
