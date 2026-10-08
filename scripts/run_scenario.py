"""Run scenario files through the harness with the FAKE guard client.

  python scripts/run_scenario.py scenarios/<file>.json [more files]
  python scripts/run_scenario.py            # every file in scenarios/

This checks that a scenario file and the harness fit together. The decisions
come from the scenario's own expectations: no rules and no model run, so a pass
here says nothing about the guard itself. Exit code 0 only if every step went
as the file says.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.fake_client import FakeGuardClient  # noqa: E402
from agent.harness import Harness  # noqa: E402
from agent.scenario import (  # noqa: E402
    SCENARIOS_DIR,
    Scenario,
    ScenarioError,
    SeedContent,
    load_scenario,
    load_seed,
)
from agent.scripted_agent import ScriptedAgent  # noqa: E402
from app.contracts import ReviewAction  # noqa: E402


def run_scenario(scenario: Scenario, seed: SeedContent, out=print) -> bool:
    """Run one scenario; return True if every step matched its expectation."""
    client = FakeGuardClient(scenario, seed)
    harness = Harness(
        client, ScriptedAgent(scenario.proposals()),
        agent_id=scenario.agent_id, session_id="cli-session", user_task=scenario.user_task,
    )
    out(f"== {scenario.id}: {scenario.title}")
    out(f"   agent {scenario.agent_id} | user task: {scenario.user_task}")
    ok = True
    for number, step in enumerate(scenario.steps, start=1):
        result = harness.step()
        if result is None:
            out(f"   step {number}: the agent had no proposal")
            return False
        d = result.decision
        matched = (
            d.decision is step.expect.decision
            and d.status is step.expect.status
            and (result.execution is not None) == step.auto_execute
        )
        out(f"   step {number}: {result.proposal.tool} -> {d.decision.value} ({d.status.value})"
            f"{' [ran]' if result.execution else ''}{'' if matched else '  MISMATCH'}")
        ok &= matched

        if step.human_review is ReviewAction.approve:
            client.approve(d.decision_id)  # stands in for the reviewer's click
            ran = harness.execute(d.decision_id)
            out(f"            simulated human approve -> {ran.status.value}"
                f"{' (undo expected, not run here)' if step.undo_expected else ''}")
            ok &= ran.ok
        elif step.human_review is ReviewAction.reject:
            out("            simulated human reject -> run ends")
        if harness.halted and number < len(scenario.steps):
            out(f"            stopped; {len(scenario.steps) - number} step(s) not run")
            break
    out(f"   observed content recorded: {len(harness.observed_content)} item(s)")
    out(f"   {'OK' if ok else 'FAILED'} (expected verdict {scenario.expected_verdict.value})")
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scenarios", nargs="*", type=Path)
    args = parser.parse_args(argv)
    print("FAKE CLIENT: decisions come from the scenario files; no rules or model run.\n")
    try:
        seed = load_seed()
        paths = args.scenarios or sorted(SCENARIOS_DIR.glob("*.json"))
        scenarios = [load_scenario(p, seed) for p in paths]
    except ScenarioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    results = [run_scenario(s, seed) for s in scenarios]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
