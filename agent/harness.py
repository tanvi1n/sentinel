"""Trusted harness: the only caller of the guard client for a scripted run.

The harness owns the agent id, session id, user task and the content observed
during the run. It takes a request from the untrusted agent, builds the
backend's ActionProposal itself, works out which arguments came from observed
external content, asks the semantic layer for its advisory result, and calls the
guard. It auto-executes only a clean APPROVE; a REVIEW or BLOCK stops the run.
The agent cannot hand the harness a trusted field.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from agent.provenance import origin_labels
from agent.scripted_agent import AgentRequest
from app.contracts.decision import DecisionLifecycle, DecisionOutcome, GuardDecision
from app.contracts.proposal import ActionProposal, ProposalContext
from app.contracts.semantic import SemanticResult
from app.semantic.bridge import advise

class GuardClientProtocol(Protocol):
    """The part of app.guard.client.GuardClient the harness uses."""

    def evaluate(
        self,
        proposal: ActionProposal,
        semantic_result: SemanticResult | None = None,
        session_id: str | None = None,
        harness_provenance: dict[str, str] | None = None,
    ) -> GuardDecision: ...

    def execute(self, decision_id: str, session_id: str | None = None) -> dict: ...


class RequestSource(Protocol):
    def propose(self) -> AgentRequest | None: ...


Advisor = Callable[..., SemanticResult | None]


class HarnessError(Exception):
    """Misuse of the harness, or a guard reply it cannot trust."""


class HarnessHalted(HarnessError):
    """The run stopped at a REVIEW or BLOCK and is not accepting new steps."""


@dataclass(frozen=True)
class StepResult:
    request: AgentRequest
    decision: GuardDecision
    execution: dict | None = None


def _may_auto_execute(d: GuardDecision) -> bool:
    return d.outcome is DecisionOutcome.APPROVE and d.lifecycle is DecisionLifecycle.EVALUATED


class Harness:
    def __init__(
        self,
        client: GuardClientProtocol,
        agent: RequestSource,
        *,
        agent_id: str,
        session_id: str,
        user_task: str,
        advisor: Advisor = advise,
    ) -> None:
        self._client = client
        self._agent = agent
        self._agent_id = agent_id
        self._session_id = session_id
        self._user_task = user_task
        self._advisor = advisor
        self._observed: dict[str, str] = {}
        self._pending_review: set[str] = set()
        self._halted = False

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def observed_content(self) -> dict[str, str]:
        return dict(self._observed)

    def step(
        self, *, may_auto_execute: bool = True, mock_semantic: str | None = None
    ) -> StepResult | None:
        """Ask the agent for its next request and evaluate it.

        Returns None when the agent has nothing more to propose.
        `may_auto_execute=False` evaluates without ever executing.
        `mock_semantic` picks a mock variant and only matters in mock mode.
        """
        if self._halted:
            raise HarnessHalted("run is stopped; a human must act first")
        raw = self._agent.propose()
        if raw is None:
            return None
        if not isinstance(raw, AgentRequest):
            raise HarnessError("the agent may only return an AgentRequest")
        # Rebuilt from the two allowed fields; every other field is ours.
        request = AgentRequest(tool=raw.tool, arguments=dict(raw.arguments))
        proposal = ActionProposal(
            agent_id=self._agent_id,
            tool=request.tool,
            arguments=dict(request.arguments),
            context=ProposalContext(
                user_task=self._user_task,
                session_id=self._session_id,
                observed_external_content=dict(self._observed),
            ),
        )
        provenance = origin_labels(request.arguments, self._user_task, self._observed) or None

        semantic = self._advisor(proposal, provenance, mock_mode=mock_semantic)
        decision = self._client.evaluate(
            proposal, semantic, session_id=self._session_id, harness_provenance=provenance
        )
        if not isinstance(decision, GuardDecision):
            self._halted = True
            raise HarnessError("the guard returned something other than a GuardDecision")

        if may_auto_execute and _may_auto_execute(decision):
            execution = self._client.execute(decision.decision_id, self._session_id)
            self._record_output(execution)
            if not execution.get("success", False):
                self._halted = True
            return StepResult(request, decision, execution)

        # REVIEW, BLOCK, or an APPROVE the script did not allow to auto-run: stop.
        self._halted = True
        if decision.lifecycle is DecisionLifecycle.PENDING_REVIEW:
            self._pending_review.add(decision.decision_id)
        return StepResult(request, decision)

    def execute(self, decision_id: str) -> dict:
        """Run a decision a human has approved. The gateway enforces approval.

        Only decisions this harness saw come back as PENDING_REVIEW qualify; a
        blocked or unknown id is refused here without calling the guard.
        """
        if decision_id not in self._pending_review:
            raise HarnessError("not a pending review decision of this run")
        execution = self._client.execute(decision_id, self._session_id)
        if execution.get("success", False):
            self._pending_review.discard(decision_id)
            self._record_output(execution)
            self._halted = False
        return execution

    def _record_output(self, execution: dict) -> None:
        """Record external content a tool returned, as the trusted side sees it."""
        meta = execution.get("metadata") or {}
        output = execution.get("output")
        if meta.get("is_external_content") and isinstance(output, dict):
            source = str(meta.get("url") or output.get("url") or f"observed-{len(self._observed) + 1}")
            self._observed[source] = str(output.get("content", ""))
