"""Run scenario files against the real backend, in process.

  python scripts/run_scenario.py                         # every file in scenarios/
  python scripts/run_scenario.py scenarios/<file>.json   # chosen files
  python scripts/run_scenario.py --semantic-mode auto    # mock (default), replay, live, auto
  python scripts/run_scenario.py --explain               # also print each decision's explanation

Each scenario runs through the trusted harness, the semantic layer, the real
GuardClient (rules, combine, store, gateway) and the mock world. The state is
reset before each run. A "simulated human" plays the reviewer where the file
says so. Exit code 0 only if every step matched the file's expectations.

With the mock semantic mode the file picks the mock variant per step. In other
modes the variant is ignored and the real provider answers (or fails closed),
so expectations that depend on a mock answer may then differ; that is reported
honestly rather than hidden.
"""

import argparse
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.harness import Harness  # noqa: E402
from agent.scenario import SCENARIOS_DIR, Scenario, ScenarioError, load_scenario  # noqa: E402
from agent.scripted_agent import ScriptedAgent  # noqa: E402
from app.guard.client import GuardClient, get_guard_client  # noqa: E402
from app.reporting.explanation import render_decision  # noqa: E402
from app.semantic import factory  # noqa: E402
from app.world.state import get_world_store  # noqa: E402


@dataclass
class StepReport:
    tool: str
    outcome: str
    lifecycle: str
    semantic: str
    ran: bool = False
    notes: list[str] = field(default_factory=list)
    explanation: str = ""


@dataclass
class Report:
    scenario: Scenario
    steps: list[StepReport] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


@contextmanager
def semantic_mode(mode: str | None):
    """Switch the semantic mode for a block and restore it afterwards."""
    previous = factory.describe()["mode"]
    if mode:
        factory.set_mode(mode)
    try:
        yield
    finally:
        factory.set_mode(previous)


def play(
    scenario: Scenario,
    client: GuardClient,
    override: tuple[int, str] | None = None,
    advisor=None,
) -> Report:
    """Run one scenario from a clean state.

    `override` = (step index, mock variant). `advisor` replaces the semantic
    bridge's `advise` (the replay recorder wraps it); by default the real one.
    """
    client.reset()
    report = Report(scenario)
    extra = {"advisor": advisor} if advisor is not None else {}
    harness = Harness(
        client, ScriptedAgent(scenario.requests()),
        agent_id=scenario.agent_id, session_id=f"cli-{scenario.id}", user_task=scenario.user_task,
        **extra,
    )

    def problem(number: int, text: str) -> None:
        report.problems.append(f"step {number}: {text}")

    for index, step in enumerate(scenario.steps):
        number = index + 1
        mock = override[1] if override and override[0] == index else step.mock_semantic
        world_before = get_world_store().summary()
        result = harness.step(may_auto_execute=step.auto_execute, mock_semantic=mock)
        if result is None:
            problem(number, "the agent had no request")
            break
        d = result.decision
        row = StepReport(
            tool=result.request.tool, outcome=d.outcome.value, lifecycle=d.lifecycle.value,
            semantic=f"{d.semantic_outcome or 'not required'}/{d.semantic_provider or '-'}",
            ran=result.execution is not None,
            explanation=render_decision(d),
        )
        report.steps.append(row)
        if d.outcome is not step.expect.outcome or d.lifecycle is not step.expect.lifecycle:
            problem(number, f"expected {step.expect.outcome.value}/{step.expect.lifecycle.value}, "
                            f"got {d.outcome.value}/{d.lifecycle.value}")
        if row.ran != step.auto_execute:
            problem(number, f"auto-execute expected {step.auto_execute}, ran {row.ran}")
        execution = result.execution

        if step.human_review == "approve":
            client.approve(d.decision_id, "simulated-human")
            execution = harness.execute(d.decision_id)
            row.ran = bool(execution.get("success"))
            row.notes.append("simulated human approved")
            if not row.ran:
                problem(number, "execution after approval failed")
        elif step.human_review == "reject":
            client.reject(d.decision_id, "simulated-human", "scenario reviewer rejects")
            row.notes.append("simulated human rejected")
            try:
                harness.execute(d.decision_id)
                problem(number, "a rejected decision was executed")
            except Exception:  # noqa: BLE001 - refusal is the expected result
                row.notes.append("execution refused")

        if step.expects_observed is not None:
            seen = " ".join(harness.observed_content.values())
            if step.expects_observed not in seen:
                problem(number, f"observed content lacks {step.expects_observed!r}")
            else:
                row.notes.append("content recorded by the harness")

        if step.undo_expected and row.ran:
            undone = client.undo(d.decision_id)
            same = get_world_store().summary() == world_before
            row.notes.append("undone, world restored" if same else "undone, world differs")
            if not undone.get("success") or not same:
                problem(number, "undo did not restore the exact prior world")
        if harness.halted and index < len(scenario.steps) - 1 and step.human_review != "approve":
            break

    if report.steps and report.steps[-1].outcome != scenario.expected_verdict.value:
        report.problems.append(
            f"final outcome {report.steps[-1].outcome}, expected {scenario.expected_verdict.value}"
        )
    return report


def check_invariants(scenario: Scenario, client: GuardClient) -> list[str]:
    """Re-run BLOCK steps under the alternative mock variants the file lists."""
    problems = []
    for index, step in enumerate(scenario.steps):
        for mode in step.invariant_mock_modes:
            again = play(scenario, client, override=(index, mode))
            if len(again.steps) <= index or again.steps[index].outcome != step.expect.outcome.value:
                got = again.steps[index].outcome if len(again.steps) > index else "no result"
                problems.append(
                    f"step {index + 1} under mock '{mode}': expected "
                    f"{step.expect.outcome.value}, got {got}"
                )
    return problems


def run_scenario(
    scenario: Scenario, client: GuardClient | None = None, out=print, explain: bool = False
) -> bool:
    client = client or get_guard_client()
    report = play(scenario, client)
    out(f"== {scenario.id}: {scenario.title}")
    out(f"   agent {scenario.agent_id} | user task: {scenario.user_task}")
    for i, row in enumerate(report.steps, start=1):
        extra = ("; " + "; ".join(row.notes)) if row.notes else ""
        out(f"   step {i}: {row.tool} -> {row.outcome} ({row.lifecycle}), semantic {row.semantic}"
            f"{', ran' if row.ran else ''}{extra}")
        if explain:
            for line in row.explanation.splitlines():
                out(f"      | {line}")
    problems = list(report.problems)
    if factory.describe()["mode"] == "mock":
        problems += check_invariants(scenario, client)
        if any(s.invariant_mock_modes for s in scenario.steps):
            out("   decision unchanged under the alternative mock variants")
    for p in problems:
        out(f"   PROBLEM {p}")
    out(f"   {'OK' if not problems else 'FAILED'} (expected verdict {scenario.expected_verdict.value})")
    return not problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scenarios", nargs="*", type=Path)
    parser.add_argument("--semantic-mode", choices=factory.MODES, default="mock")
    parser.add_argument("--explain", action="store_true", help="print each decision's explanation")
    args = parser.parse_args(argv)
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")  # the local, git-ignored .env, if present; never overrides
    except ImportError:
        pass
    try:
        paths = args.scenarios or sorted(SCENARIOS_DIR.glob("*.json"))
        scenarios = [load_scenario(p) for p in paths]
    except ScenarioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    with semantic_mode(args.semantic_mode):
        print(f"semantic mode: {factory.describe()['mode']} ({factory.describe()['provider']})\n")
        results = [run_scenario(s, explain=args.explain) for s in scenarios]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
