"""Record real Qualcomm (LIVE) semantic results for replay.

  python scripts/record_replay.py --from-scenarios                  # all scenario files
  python scripts/record_replay.py --from-scenarios scenarios/<f>.json
  python scripts/record_replay.py --contexts contexts.json          # explicit model inputs
  ... [--out replay/cache.jsonl]

`--from-scenarios` runs the scenarios through the real harness and guard in live
mode and records every semantic answer on the way, so the recorded keys are
exactly the ones production looks up. `--contexts` takes a JSON list of
SemanticContext objects instead.

Only VALID results that really came from a LIVE call are written; anything else
(mock, replay, unavailable, invalid) is refused and reported, and the exit code
is 1. Without working Qualcomm access everything is refused and nothing is written.
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.semantic import factory as factory_module  # noqa: E402
from app.semantic.factory import SemanticFactory  # noqa: E402
from app.semantic.models import SemanticContext  # noqa: E402
from app.semantic.replay import RecordingReasoner, record_contexts  # noqa: E402

DEFAULT_OUT = ROOT / "replay" / "cache.jsonl"


def _live_factory() -> SemanticFactory:
    factory = SemanticFactory()
    factory.set_mode("live")  # never mock, never replay: only real calls are recorded
    return factory


def record_from_contexts(path: Path, out: Path) -> int:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        contexts = [SemanticContext.model_validate(item) for item in raw]
    except (OSError, ValueError) as exc:
        print(f"cannot read contexts: {type(exc).__name__}", file=sys.stderr)
        return 2
    factory = _live_factory()
    summary = record_contexts(contexts, factory.get_reasoner(), out, factory.describe()["prompt_version"])
    refused = [item["reason"] for item in summary["refused"]]
    return _report(summary["recorded"], summary["already_recorded"], refused, out)


def record_from_scenarios(paths: list[Path], out: Path) -> int:
    spec = importlib.util.spec_from_file_location("run_scenario", ROOT / "scripts" / "run_scenario.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    from agent.scenario import SCENARIOS_DIR, ScenarioError, load_scenario
    from app.guard.client import get_guard_client
    from app.semantic.bridge import advise

    try:
        scenarios = [load_scenario(p) for p in (paths or sorted(SCENARIOS_DIR.glob("*.json")))]
    except ScenarioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    factory = _live_factory()
    recorder = RecordingReasoner(factory.get_reasoner(), out, factory.describe()["prompt_version"])

    def advisor(proposal, provenance, mock_mode=None):
        return advise(proposal, provenance, reasoner=recorder)

    client = get_guard_client()
    previous = factory_module._default
    factory_module._default = factory
    try:
        for scenario in scenarios:
            report = runner.play(scenario, client, advisor=advisor)
            print(f"{scenario.id}: " + ", ".join(f"{s.tool} {s.outcome}" for s in report.steps))
    finally:
        factory_module._default = previous
    return _report(recorder.recorded, recorder.already_recorded, recorder.refused, out)


def _report(recorded: int, already: int, refused: list[str], out: Path) -> int:
    print(f"recorded {recorded}, already recorded {already}, refused {len(refused)} -> {out}")
    for reason in refused:
        print(f"  refused: {reason}", file=sys.stderr)
    return 1 if refused else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--contexts", type=Path)
    source.add_argument("--from-scenarios", nargs="*", type=Path)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")  # the local, git-ignored .env, if present
    except ImportError:
        pass

    if args.contexts is not None:
        return record_from_contexts(args.contexts, args.out)
    return record_from_scenarios(args.from_scenarios, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
