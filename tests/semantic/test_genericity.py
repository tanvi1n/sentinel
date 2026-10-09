"""Genericity and layering for the whole app/semantic/ package."""

import ast
from pathlib import Path

import pytest

SEM = Path(__file__).resolve().parents[2] / "app" / "semantic"
FILES = sorted(p for p in SEM.rglob("*") if p.suffix in {".py", ".txt"} and "__pycache__" not in p.parts)
BANNED = [
    "email", "calendar", "payment", "website", "invoice", "inbox", "transfer",
    "delete", "web.fetch", "file.delete", "email.send", "calendar.read",
    "payment.transfer", "scenario",
]
FORBIDDEN_IMPORTS = ("app.guard", "app.api", "app.world", "agent", "fastapi", "static")


def test_files_found():
    names = {p.name for p in FILES}
    assert {"base.py", "mock.py", "replay.py", "qualcomm.py", "factory.py", "models.py", "bridge.py", "v1.txt"} <= names


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_no_domain_tool_or_scenario_vocabulary(path):
    src = path.read_text(encoding="utf-8").lower()
    for word in BANNED:
        assert word not in src, f"{path.name} mentions {word!r}"


# The bridge is the one module that adapts to the backend, so it may import the
# contracts and the guard's intake and registry (and nothing else from the guard).
BRIDGE_ALLOWED = {"app.contracts.proposal", "app.contracts.semantic",
                  "app.guard.intake", "app.guard.registry"}


@pytest.mark.parametrize("path", [p for p in FILES if p.suffix == ".py"], ids=lambda p: p.name)
def test_no_forbidden_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        elif isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        for m in mods:
            if path.name == "bridge.py" and m in BRIDGE_ALLOWED:
                continue
            for bad in FORBIDDEN_IMPORTS:
                assert m != bad and not m.startswith(bad + "."), f"{path.name} imports {m}"
            if path.name != "bridge.py":
                assert not m.startswith("app.contracts"), f"{path.name} imports {m}"


def test_providers_never_name_a_guard_decision():
    # Semantic code only produces results; it must not name guard verdicts.
    for path in FILES:
        if path.suffix != ".py":
            continue
        src = path.read_text(encoding="utf-8")
        assert "Decision." not in src and "GuardDecision" not in src, path.name
