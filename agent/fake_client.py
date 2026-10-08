"""A stand-in guard client for running scenarios before the real one exists.

It runs no rules and no model. Each decision is read from the scenario step's
own expectation, so it can only show that the scenario file and the harness fit
together. Everything it returns is marked as fake. It is not a GuardClient
implementation and must never be used as one.
"""

from agent.scenario import Scenario, SeedContent
from app.contracts import (
    ActionProposal,
    Axis,
    CanonicalAction,
    DecisionStatus,
    ExecutionResult,
    GuardDecision,
    RiskLevel,
    SemanticResult,
    ToolFactsView,
    TrustedContext,
)
from app.reporting.explanation import render
from app.semantic.base import build_semantic_context, skipped_result
from app.semantic.mock import MockReasoner

FAKE_NOTICE = "[FAKE CLIENT: decision taken from the scenario file; no rules or model ran]"
_PASS = Axis(status="PASS", reason="fake client")


class FakeGuardClient:
    def __init__(self, scenario: Scenario, seed: SeedContent) -> None:
        self._scenario = scenario
        self._seed = seed
        self._decisions: dict[str, GuardDecision] = {}
        self._approved: set[str] = set()
        self._executed: set[str] = set()
        self.evaluated: list[tuple[ActionProposal, TrustedContext]] = []
        self.executed: list[str] = []

    def evaluate(self, proposal: ActionProposal, trusted: TrustedContext) -> GuardDecision:
        index = len(self.evaluated)
        if index >= len(self._scenario.steps):
            raise RuntimeError("more proposals than scenario steps")
        step = self._scenario.steps[index]
        if proposal != step.proposal:
            raise RuntimeError(f"proposal {index + 1} differs from the scenario file")
        self.evaluated.append((proposal, trusted))

        expect = step.expect
        det = expect.deterministic_decision or expect.decision
        action = CanonicalAction(
            tool_id=proposal.tool, args=proposal.args,
            justification=proposal.justification, agent_id=trusted.agent_id,
            session_id=trusted.session_id, user_task=trusted.user_task,
        )
        semantic = self._semantic(step.mock_semantic, action)
        decision = GuardDecision(
            decision_id=f"d{index + 1}",
            decision=expect.decision,
            deterministic_decision=det,
            status=expect.status,
            executable=expect.status is DecisionStatus.APPROVED,
            risk_level=RiskLevel.LOW,
            safe=_PASS, authorized=_PASS, explainable=_PASS, reversible=_PASS,
            canonical_action=action,
            tool_facts=ToolFactsView(
                description="fake", operation="fake", resource="fake",
                external_effect=False, effective_reversibility="fake",
            ),
            action_hash="fake",
            semantic=semantic,
            explanation=FAKE_NOTICE + "\n"
            + render(decision=expect.decision, deterministic_decision=det,
                     canonical_action=action, semantic=semantic),
            created_at="fake",
        )
        self._decisions[decision.decision_id] = decision
        return decision

    def approve(self, decision_id: str) -> None:
        """Simulate the human reviewer's approval (the real one is an admin route)."""
        if self._decisions[decision_id].status is not DecisionStatus.PENDING_REVIEW:
            raise RuntimeError("only a pending review can be approved")
        self._approved.add(decision_id)

    def execute(self, decision_id: str) -> ExecutionResult:
        decision = self._decisions.get(decision_id)
        runnable = decision is not None and (
            decision.status is DecisionStatus.APPROVED or decision_id in self._approved
        )
        if not runnable or decision_id in self._executed:
            return ExecutionResult(
                decision_id=decision_id, status=DecisionStatus.PENDING_REVIEW, ok=False,
                refused_reason="fake client: not approved, or already executed",
            )
        self._executed.add(decision_id)
        self.executed.append(decision_id)
        step = self._scenario.steps[int(decision_id[1:]) - 1]
        text = None
        if step.reads_seed and step.expected_output_label:
            text = self._seed.text(step.reads_seed)
        return ExecutionResult(
            decision_id=decision_id, status=DecisionStatus.EXECUTED, ok=True,
            output_text=text, output_label=step.expected_output_label,
            undo_available=step.undo_expected,
        )

    @staticmethod
    def _semantic(mock_mode: str | None, action: CanonicalAction) -> SemanticResult:
        if mock_mode is None:
            return skipped_result()
        # A labelled MOCK result, never presented as a real model answer.
        context = build_semantic_context(action, "fake client")
        return MockReasoner(mock_mode).analyze(context)
