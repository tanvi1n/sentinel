"""The simulator and runner are test/demo infrastructure; production code never uses them."""

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


# The backend may read scenario data and use the pure origin check (the demo
# routes act as the trusted harness). It never imports the agent simulator, the
# harness or the runner.
APP_MAY_IMPORT = {"agent.scenario", "agent.provenance"}


def test_app_imports_only_scenario_data_and_the_origin_check_from_agent():
    for path in (ROOT / "app").rglob("*.py"):
        for m in _imports(path):
            if m == "agent" or m.startswith("agent."):
                assert m in APP_MAY_IMPORT, f"{path} imports {m}"


def test_provenance_helper_is_pure():
    allowed = {"re", "collections.abc", "typing", "app.contracts.proposal"}
    assert set(_imports(ROOT / "agent" / "provenance.py")) <= allowed


def test_the_backend_never_imports_scripts_or_scenarios():
    for path in (ROOT / "app").rglob("*.py"):
        for m in _imports(path):
            assert not m.startswith(("scripts", "scenarios")), f"{path} imports {m}"


def test_harness_and_agent_never_read_scenario_expectations():
    for name in ("harness.py", "scripted_agent.py", "provenance.py"):
        src = (ROOT / "agent" / name).read_text(encoding="utf-8")
        assert "agent.scenario" not in src and "StepExpectation" not in src


def test_only_schema_and_runner_touch_expectations():
    users = []
    for path in list((ROOT / "agent").glob("*.py")) + list((ROOT / "scripts").glob("*.py")):
        if ".expect" in path.read_text(encoding="utf-8"):
            users.append(path.name)
    assert sorted(users) == ["run_scenario.py", "scenario.py"]


def test_there_is_no_fake_guard_client_left():
    assert not (ROOT / "agent" / "fake_client.py").exists()
    for path in list((ROOT / "agent").glob("*.py")) + list((ROOT / "scripts").glob("*.py")):
        assert "FakeGuardClient" not in path.read_text(encoding="utf-8"), path.name
