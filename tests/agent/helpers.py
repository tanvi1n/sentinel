"""Test-only fake client and builders. Not a production GuardClient."""

from app.contracts import (
    ActionProposal,
    ExecutionResult,
    GuardDecision,
    SemanticResult,
    TrustedContext,
)

AXIS = {"status": "PASS", "reason": "r"}


def make_decision(
    decision="APPROVE",
    status="APPROVED",
    executable=True,
    decision_id="d1",
    tool_id="t.act",
) -> GuardDecision:
    return GuardDecision(
        decision_id=decision_id,
        decision=decision,
        deterministic_decision=decision,
        status=status,
        executable=executable,
        risk_level="LOW",
        safe=AXIS,
        authorized=AXIS,
        explainable=AXIS,
        reversible=AXIS,
        canonical_action=dict(
            tool_id=tool_id,
            args={},
            justification="j",
            agent_id="a",
            session_id="s",
            user_task="u",
        ),
        tool_facts=dict(
            description="d",
            operation="o",
            resource="r",
            external_effect=False,
            effective_reversibility="pure read",
        ),
        action_hash="h",
        semantic=SemanticResult(status="SKIPPED", source="NONE"),
        explanation="e",
        created_at="2026-10-09T00:00:00Z",
    )


def make_execution(
    decision_id="d1", ok=True, status="EXECUTED", output_text=None, output_label=None
) -> ExecutionResult:
    return ExecutionResult(
        decision_id=decision_id,
        status=status,
        ok=ok,
        output_text=output_text,
        output_label=output_label,
    )


class RecordingClient:
    """Returns queued decisions/executions and records every call."""

    def __init__(self, decisions, executions=()):
        self.decisions = list(decisions)
        self.executions = list(executions)
        self.evaluated: list[tuple[ActionProposal, TrustedContext]] = []
        self.executed: list[str] = []

    def evaluate(self, proposal, trusted):
        self.evaluated.append((proposal, trusted))
        return self.decisions.pop(0)

    def execute(self, decision_id):
        self.executed.append(decision_id)
        if self.executions:
            return self.executions.pop(0)
        return make_execution(decision_id)


def proposal_dict(tool="t.act", **args):
    return {"tool": tool, "args": args, "justification": "j"}
