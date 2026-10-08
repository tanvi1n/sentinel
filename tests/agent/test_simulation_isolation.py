"""The fake client and runner are simulation only; production code never uses them."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _imports(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            for a in node.names:
                yield a.name


def test_nothing_under_app_imports_the_agent_package():
    for path in (ROOT / "app").rglob("*.py"):
        for m in _imports(path):
            assert m != "agent" and not m.startswith("agent."), f"{path} imports {m}"


def test_harness_and_agent_never_read_scenario_expectations():
    for name in ("harness.py", "scripted_agent.py"):
        src = (ROOT / "agent" / name).read_text(encoding="utf-8")
        assert "agent.scenario" not in src and "StepExpectation" not in src


def test_only_schema_fake_client_and_runner_touch_expectations():
    users = []
    for path in list((ROOT / "agent").glob("*.py")) + list((ROOT / "scripts").glob("*.py")):
        if ".expect" in path.read_text(encoding="utf-8"):
            users.append(path.name)
    assert sorted(users) == ["fake_client.py", "run_scenario.py", "scenario.py"]


def test_fake_client_is_marked_as_fake():
    src = (ROOT / "agent" / "fake_client.py").read_text(encoding="utf-8")
    assert "FAKE CLIENT" in src and "never be used as one" in src
    assert "class FakeGuardClient" in src and "class GuardClient" not in src
