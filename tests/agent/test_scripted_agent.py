import ast
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.scripted_agent import ScriptedAgent
from app.contracts import ActionProposal
from tests.agent.helpers import proposal_dict

AGENT_PY = Path(__file__).resolve().parents[2] / "agent" / "scripted_agent.py"


def test_returns_only_action_proposals_in_order_then_none():
    agent = ScriptedAgent([proposal_dict("t.one"), proposal_dict("t.two")])
    assert agent.remaining == 2
    first, second = agent.propose(), agent.propose()
    assert type(first) is ActionProposal and type(second) is ActionProposal
    assert (first.tool, second.tool) == ("t.one", "t.two")
    assert agent.propose() is None and agent.remaining == 0


def test_accepts_proposal_objects_and_does_not_alias():
    original = ActionProposal(tool="t.x", args={"a": [1]}, justification="j")
    agent = ScriptedAgent([original])
    out = agent.propose()
    assert out == original
    out.args["a"].append(2)
    assert original.args["a"] == [1]


@pytest.mark.parametrize(
    "field",
    [
        "agent_id", "session_id", "user_task", "observed_content",
        "user_confirmation", "confirmed", "risk_level", "risk", "permissions",
        "role", "reversibility", "decision", "provenance", "label",
    ],
)
def test_script_step_cannot_carry_trusted_or_claimed_fields(field):
    step = {**proposal_dict(), field: "x"}
    with pytest.raises(ValidationError):
        ScriptedAgent([step])


def test_malformed_steps_rejected_at_construction():
    with pytest.raises(ValidationError):
        ScriptedAgent([{"tool": "t.x"}])  # args missing
    with pytest.raises(TypeError):
        ScriptedAgent(["not a step"])


def test_justification_cap_enforced():
    with pytest.raises(ValidationError):
        ScriptedAgent([{"tool": "t", "args": {}, "justification": "x" * 501}])


def test_public_surface_is_proposal_only():
    public = {n for n in dir(ScriptedAgent) if not n.startswith("_")}
    assert public == {"propose", "remaining"}


# ---- import boundary and genericity -----------------------------------------


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
        elif isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
    return out


def test_no_forbidden_imports():
    for m in _imports(AGENT_PY):
        if m == "app" or m.startswith("app."):
            assert m == "app.contracts", f"scripted_agent must not import {m}"
        assert not m.startswith("agent"), f"scripted_agent must not import {m}"
        for bad in ("fastapi", "httpx", "requests", "selenium", "playwright", "langgraph", "langchain"):
            assert not m.startswith(bad), m


def test_no_trusted_side_vocabulary():
    src = AGENT_PY.read_text(encoding="utf-8")
    for word in ("GuardClient", "registry", "world", "harness", "ledger", "gateway"):
        assert word.lower() not in src.lower(), word


def test_no_domain_vocabulary():
    src = AGENT_PY.read_text(encoding="utf-8").lower()
    for word in ("email", "calendar", "payment", "website", "invoice", "inbox",
                 "transfer", "delete", "scenario"):
        assert word not in src, word
    assert not re.search(r"\bfile\b", src)
