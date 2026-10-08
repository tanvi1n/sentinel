"""Record real Qualcomm (LIVE) semantic results into the replay cache.

  python scripts/record_replay.py --contexts contexts.json [--out replay/cache.jsonl]

`contexts.json` is a JSON list of SemanticContext objects: the exact model
inputs, as built by build_semantic_context. Only VALID results that really came
from a LIVE call are written; anything else is refused and reported.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.contracts import SemanticContext  # noqa: E402
from app.semantic.factory import SemanticFactory  # noqa: E402
from app.semantic.replay import record_contexts  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--contexts", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "replay" / "cache.jsonl")
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    try:
        raw = json.loads(args.contexts.read_text(encoding="utf-8"))
        contexts = [SemanticContext.model_validate(item) for item in raw]
    except (OSError, ValueError) as exc:
        print(f"cannot read contexts: {type(exc).__name__}", file=sys.stderr)
        return 2

    factory = SemanticFactory()
    factory.set_mode("live")  # never mock, never replay: only real calls are recorded
    reasoner = factory.get_reasoner()
    summary = record_contexts(
        contexts, reasoner, args.out, factory.describe()["prompt_version"]
    )
    print(
        f"recorded {summary['recorded']}, already recorded {summary['already_recorded']}, "
        f"refused {len(summary['refused'])} -> {args.out}"
    )
    for item in summary["refused"]:
        print(f"  refused context {item['index']}: {item['reason']}", file=sys.stderr)
    return 1 if summary["refused"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
