import ast
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.scripted_agent import AgentRequest, ScriptedAgent

AGENT_PY = Path(__file__).resolve().parents[2] / "agent" / "scripted_agent.py"


def step(tool="t.one", **arguments):
    return {"tool": tool, "arguments": arguments}


def test_returns_only_requests_in_order_then_none():
    agent = ScriptedAgent([step("a"), step("b")])
    assert agent.remaining == 2
    first, second = agent.propose(), agent.propose()
    assert type(first) is AgentRequest and type(second) is AgentRequest
    assert (first.tool, second.tool) == ("a", "b")
    assert agent.propose() is None and agent.remaining == 0


def test_accepts_request_objects_and_does_not_alias():
    original = AgentRequest(tool="t", arguments={"a": [1]})
    out = ScriptedAgent([original]).propose()
    assert out == original
    out.arguments["a"].append(2)
    assert original.arguments["a"] == [1]


@pytest.mark.parametrize(
    "field",
    [
        "agent_id", "session_id", "user_task", "context", "arg_provenance",
        "observed_content", "observed_external_content", "user_confirmation", "confirmed",
        "risk", "risk_level", "permissions", "role", "reversibility", "decision", "outcome",
    ],
)
def test_a_step_cannot_carry_trusted_or_claimed_fields(field):
    with pytest.raises(ValidationError):
        ScriptedAgent([{**step(), field: "x"}])


def test_malformed_steps_rejected_at_construction():
    with pytest.raises(ValidationError):
        ScriptedAgent([{"arguments": {}}])  # no tool
    with pytest.raises(ValidationError):
        ScriptedAgent([{"tool": ""}])
    with pytest.raises(TypeError):
        ScriptedAgent(["not a step"])


def test_public_surface_is_request_only():
    assert {n for n in dir(ScriptedAgent) if not n.startswith("_")} == {"propose", "remaining"}


# ---- import boundary and genericity -------------------------------------------


def _imports(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            for a in node.names:
                yield a.name


def test_agent_imports_nothing_from_the_application():
    for m in _imports(AGENT_PY):
        assert m != "app" and not m.startswith("app."), m
        assert not m.startswith("agent"), m
        for bad in ("fastapi", "httpx", "requests", "selenium", "playwright", "langgraph", "langchain"):
            assert not m.startswith(bad), m


def test_no_trusted_side_vocabulary():
    src = AGENT_PY.read_text(encoding="utf-8").lower()
    for word in ("guardclient", "registry", "world", "harness", "ledger", "gateway"):
        assert word not in src, word


def test_no_domain_vocabulary():
    src = AGENT_PY.read_text(encoding="utf-8").lower()
    for word in ("email", "calendar", "payment", "website", "invoice", "inbox", "transfer",
                 "delete", "scenario"):
        assert word not in src, word
    assert not re.search(r"\bfile\b", src)
