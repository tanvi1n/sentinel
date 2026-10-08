"""Trusted harness: the only caller of the guard client.

The harness owns the agent id, session id, user task and the observed content
with its provenance labels. It takes an ActionProposal from the untrusted
agent, builds the trusted context itself, asks the guard to evaluate, and
auto-executes only a clean APPROVE. A REVIEW or BLOCK stops the run. The agent
has no way to hand the harness a trusted field.

The guard client is a two-method protocol so the harness can be exercised
without the backend. This module contains no client implementation.
"""

from dataclasses import dataclass
from typing import Protocol

from app.contracts import (
    ActionProposal,
    Decision,
    DecisionStatus,
    ExecutionResult,
    GuardDecision,
    ObservedItem,
    ProvenanceLabel,
    TrustedContext,
)


class GuardClientProtocol(Protocol):
    def evaluate(self, proposal: ActionProposal, trusted: TrustedContext) -> GuardDecision: ...

    def execute(self, decision_id: str) -> ExecutionResult: ...


class ProposalSource(Protocol):
    def propose(self) -> ActionProposal | None: ...


class HarnessError(Exception):
    """Misuse of the harness, or a guard reply it cannot trust."""


class HarnessHalted(HarnessError):
    """The run stopped at a REVIEW or BLOCK and is not accepting new steps."""


@dataclass(frozen=True)
class StepResult:
    proposal: ActionProposal
    decision: GuardDecision
    execution: ExecutionResult | None = None


def _may_auto_execute(d: GuardDecision) -> bool:
    return (
        d.decision is Decision.APPROVE
        and d.status is DecisionStatus.APPROVED
        and d.executable is True
    )


class Harness:
    def __init__(
        self,
        client: GuardClientProtocol,
        agent: ProposalSource,
        *,
        agent_id: str,
        session_id: str,
        user_task: str,
    ) -> None:
        self._client = client
        self._agent = agent
        self._agent_id = agent_id
        self._session_id = session_id
        self._user_task = user_task
        self._observed: list[ObservedItem] = []
        self._pending_review: dict[str, str] = {}  # decision id -> tool id
        self._halted = False

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def observed_content(self) -> tuple[ObservedItem, ...]:
        return tuple(self._observed)

    def step(self) -> StepResult | None:
        """Ask the agent for its next proposal and evaluate it.

        Returns None when the agent has nothing more to propose.
        """
        if self._halted:
            raise HarnessHalted("run is stopped; a human must act first")
        raw = self._agent.propose()
        if raw is None:
            return None
        if not isinstance(raw, ActionProposal):
            raise HarnessError("the agent may only return an ActionProposal")
        # Rebuild from the three allowed fields so nothing else rides along
        # (for example extra attributes on a subclass).
        proposal = ActionProposal(
            tool=raw.tool, args=dict(raw.args), justification=raw.justification
        )
        trusted = TrustedContext(
            agent_id=self._agent_id,
            session_id=self._session_id,
            user_task=self._user_task,
            observed_content=list(self._observed),
        )
        decision = self._client.evaluate(proposal, trusted)
        if not isinstance(decision, GuardDecision):
            self._halted = True
            raise HarnessError("the guard returned something other than a GuardDecision")

        if _may_auto_execute(decision):
            execution = self._client.execute(decision.decision_id)
            self._after_execution(decision, execution)
            if not execution.ok:
                self._halted = True
            return StepResult(proposal, decision, execution)

        # REVIEW, BLOCK or anything not cleanly executable: stop here.
        self._halted = True
        if decision.status is DecisionStatus.PENDING_REVIEW:
            self._pending_review[decision.decision_id] = decision.canonical_action.tool_id
        return StepResult(proposal, decision)

    def execute(self, decision_id: str) -> ExecutionResult:
        """Run a decision a human has approved. The gateway enforces approval.

        Only decisions this harness saw come back as PENDING_REVIEW qualify;
        a BLOCKED or unknown id is refused here without calling the guard.
        """
        if decision_id not in self._pending_review:
            raise HarnessError("not a pending review decision of this run")
        tool_id = self._pending_review[decision_id]
        execution = self._client.execute(decision_id)
        if execution.ok:
            del self._pending_review[decision_id]
            self._record_output(execution, tool_id)
            self._halted = False
        elif execution.status is DecisionStatus.BLOCKED:
            del self._pending_review[decision_id]
        return execution

    def _after_execution(self, decision: GuardDecision, execution: ExecutionResult) -> None:
        if execution.ok:
            self._record_output(execution, decision.canonical_action.tool_id)

    def _record_output(self, execution: ExecutionResult, tool_id: str) -> None:
        """Record what a tool returned as observed content, labelled by the
        trusted side (the label comes from the gateway, never from the agent)."""
        if execution.output_text is None:
            return
        label = execution.output_label
        if label is None or label is ProvenanceLabel.USER:
            label = ProvenanceLabel.OTHER_EXTERNAL  # unlabelled output is still external
        self._observed.append(
            ObservedItem(
                item_id=f"obs-{len(self._observed) + 1}",
                label=label,
                text=execution.output_text,
                source_tool=tool_id,
            )
        )
