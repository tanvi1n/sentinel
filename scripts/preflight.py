"""Local developer preflight: checks what can be checked without the backend.

  python scripts/preflight.py            # quick checks
  python scripts/preflight.py --tests    # also run the pytest suite

Prints PASS / WARN / FAIL per check. The exit code is 1 only if a required
check FAILs; a WARN means something is optional or not integrated yet. It reads
and changes nothing, makes no network call, and never prints configuration
values.
"""

import argparse
import importlib
import importlib.util
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MIN_PYTHON = (3, 11)
REQUIRED_DIRS = [
    "app/contracts", "app/semantic", "app/reporting", "app/guard", "app/api", "app/world",
    "config", "agent", "scenarios", "scripts", "tests",
]
REQUIRED_FILES = [
    "requirements.txt", "pytest.ini", "app/semantic/prompts/v1.txt",
    "config/tools.yaml", "config/policies.yaml",
]
# Needed by the code in this repository.
REQUIRED_PACKAGES = ["pydantic", "pytest", "yaml", "fastapi"]
# Listed in requirements.txt; not needed to run the code or tests today.
LATER_PACKAGES = ["uvicorn", "httpx", "dotenv"]
MODULES = [
    "app.contracts", "app.guard.client", "app.api.main",
    "app.semantic.models", "app.semantic.base", "app.semantic.mock", "app.semantic.replay",
    "app.semantic.qualcomm", "app.semantic.factory", "app.semantic.bridge",
    "app.reporting.explanation",
    "agent.scripted_agent", "agent.harness", "agent.scenario",
]
BACKEND_DIRS = ["app/guard", "app/api", "app/world"]


@dataclass(frozen=True)
class Result:
    level: str  # PASS, WARN or FAIL
    name: str
    detail: str = ""


def _ok(name, detail=""):
    return Result("PASS", name, detail)


def _warn(name, detail=""):
    return Result("WARN", name, detail)


def _fail(name, detail=""):
    return Result("FAIL", name, detail)


def check_python() -> Result:
    v = sys.version_info
    text = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= MIN_PYTHON:
        return _ok("python", f"{text} (>= {MIN_PYTHON[0]}.{MIN_PYTHON[1]} required)")
    return _fail("python", f"{text}; {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer is required")


def check_packages() -> list[Result]:
    results = []
    for name in REQUIRED_PACKAGES + LATER_PACKAGES:
        required = name in REQUIRED_PACKAGES
        try:
            mod = importlib.import_module(name)
        except ImportError:
            hint = "pip install -r requirements.txt"
            results.append(
                _fail(f"package {name}", hint) if required
                else _warn(f"package {name}", f"not installed (not needed for the tests); {hint}")
            )
            continue
        version = str(getattr(mod, "VERSION", None) or getattr(mod, "__version__", ""))
        if name == "pydantic" and not version.startswith("2."):
            results.append(_fail("package pydantic", f"version {version}; Pydantic v2 is required"))
        else:
            results.append(_ok(f"package {name}", version))
    return results


def check_layout(root: Path) -> list[Result]:
    results = []
    for rel in REQUIRED_DIRS:
        ok = (root / rel).is_dir()
        results.append(_ok(f"dir {rel}") if ok else _fail(f"dir {rel}", "missing"))
    for rel in REQUIRED_FILES:
        ok = (root / rel).is_file()
        results.append(_ok(f"file {rel}") if ok else _fail(f"file {rel}", "missing"))
    # .env is never read here. .env.example is checked for leftover values only;
    # its values are never printed.
    example = root / ".env.example"
    if not example.is_file():
        results.append(_warn("file .env.example", "not found"))
    else:
        filled = [
            line.split("=", 1)[0].strip()
            for line in example.read_text(encoding="utf-8").splitlines()
            if "=" in line and not line.lstrip().startswith("#")
            and "KEY" in line.split("=", 1)[0].upper() and line.split("=", 1)[1].strip()
        ]
        results.append(_fail("file .env.example", f"secret placeholder(s) filled in: {filled}")
                       if filled else _ok("file .env.example", "no secret values"))
    gitignore = root / ".gitignore"
    lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.is_file() else []
    if ".env" in (line.strip() for line in lines):
        results.append(_ok(".gitignore", "ignores .env"))
    else:
        results.append(_warn(".gitignore", "missing, or does not ignore .env"))
    return results


def check_backend(root: Path) -> list[Result]:
    results = []
    for rel in BACKEND_DIRS:
        d = root / rel
        modules = [p for p in d.glob("*.py") if p.name != "__init__.py"] if d.is_dir() else []
        if modules:
            results.append(_ok(f"backend {rel}", f"{len(modules)} module(s) present"))
        else:
            results.append(_warn(f"backend {rel}", "no modules found"))
    return results


def check_imports() -> list[Result]:
    results = []
    for name in MODULES:
        try:
            importlib.import_module(name)
            results.append(_ok(f"import {name}"))
        except Exception as exc:  # noqa: BLE001 - report, do not crash
            results.append(_fail(f"import {name}", type(exc).__name__))
    return results


def check_scenarios(root: Path) -> list[Result]:
    try:
        from agent.scenario import load_scenarios

        scenarios = load_scenarios(root / "scenarios")
    except Exception as exc:  # noqa: BLE001
        return [_fail("scenario files", f"{type(exc).__name__}: {exc}".splitlines()[0])]
    if not scenarios:
        return [_fail("scenario files", "none found")]
    results = [_ok("scenario files", f"{len(scenarios)} loaded")]
    try:
        # Runs them against the real backend in mock mode. This resets the
        # in-memory backend state, which is fine for a command-line check.
        spec = importlib.util.spec_from_file_location("run_scenario", ROOT / "scripts" / "run_scenario.py")
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        with runner.semantic_mode("mock"):
            failed = [s.id for s in scenarios if not runner.run_scenario(s, out=lambda *_: None)]
        detail = f"{len(scenarios)} scenario(s) through the harness, semantic layer and real guard (mock)"
        results.append(_ok("scenario runner", detail) if not failed
                       else _fail("scenario runner", f"mismatch in: {', '.join(failed)}"))
    except Exception as exc:  # noqa: BLE001
        results.append(_fail("scenario runner", type(exc).__name__))
    return results


def check_semantic(root: Path) -> list[Result]:
    results = []
    try:
        from app.semantic.factory import SemanticFactory
        from app.semantic.qualcomm import default_call_model

        info = SemanticFactory().describe()
        results.append(_ok("semantic mode", f"{info['mode']} (provider {info['provider']})"))
        try:
            default_call_model("", 1.0)
            results.append(_ok("qualcomm call_model", "implemented"))
        except NotImplementedError:
            results.append(_warn("qualcomm call_model", "not implemented; LIVE is unavailable"))
    except Exception as exc:  # noqa: BLE001
        results.append(_fail("semantic factory", type(exc).__name__))
    cache = root / "replay" / "cache.jsonl"
    if cache.is_file():
        results.append(_ok("replay cache", f"{len(cache.read_text(encoding='utf-8').splitlines())} line(s)"))
    else:
        results.append(_warn("replay cache", "replay/cache.jsonl not recorded yet"))
    return results


def check_tests(root: Path, run: bool) -> Result:
    if not run:
        return _warn("test suite", "not run (use --tests)")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=root, capture_output=True, text=True,
    )
    tail = (proc.stdout.strip().splitlines() or ["no output"])[-1]
    return _ok("test suite", tail) if proc.returncode == 0 else _fail("test suite", tail)


def run_checks(root: Path = ROOT, run_tests: bool = False) -> list[Result]:
    root = Path(root)
    results = [check_python()]
    results += check_packages()
    results += check_layout(root)
    results += check_backend(root)
    results += check_imports()
    results += check_scenarios(root)
    results += check_semantic(root)
    results.append(check_tests(root, run_tests))
    return results


def exit_code(results: list[Result]) -> int:
    return 1 if any(r.level == "FAIL" for r in results) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tests", action="store_true", help="also run the pytest suite")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    print("Sentinel preflight")
    results = run_checks(args.root, run_tests=args.tests)
    for r in results:
        print(f"{r.level:<5} {r.name}" + (f": {r.detail}" if r.detail else ""))
    counts = {lvl: sum(r.level == lvl for r in results) for lvl in ("PASS", "WARN", "FAIL")}
    print(f"\n{counts['PASS']} PASS, {counts['WARN']} WARN, {counts['FAIL']} FAIL")
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
