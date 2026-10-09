import ast
import json
import re
from pathlib import Path

import pytest

from agent.scenario import (
    SCENARIOS_DIR,
    Scenario,
    ScenarioError,
    load_scenario,
    load_scenarios,
)
from app.contracts.decision import DecisionLifecycle, DecisionOutcome
from app.contracts.proposal import ActionProposal
from app.guard.intake import process_proposal

SCENARIO_PY = Path(__file__).resolve().parents[2] / "agent" / "scenario.py"
IDS = ["safe-action", "intent-mismatch", "bulk-delete", "prompt-injection"]


@pytest.fixture(scope="module")
def scenarios():
    return {s.id: s for s in load_scenarios()}


# ---- the four scenarios -------------------------------------------------------


def test_four_scenarios_load_in_demo_file_order():
    assert [s.id for s in load_scenarios()] == IDS
    assert [p.name for p in sorted(SCENARIOS_DIR.glob("*.json"))] == [
        "01-safe-action.json", "02-intent-mismatch.json",
        "03-bulk-delete.json", "04-prompt-injection.json",
    ]


def test_required_fields_present_and_non_blank(scenarios):
    for s in scenarios.values():
        assert s.title and s.description and s.agent_id and s.user_task and s.steps
        assert s.expected_verdict is s.steps[-1].expect.outcome


def test_expected_verdicts(scenarios):
    assert {i: scenarios[i].expected_verdict.value for i in IDS} == {
        "safe-action": "APPROVE", "intent-mismatch": "REVIEW",
        "bulk-delete": "REVIEW", "prompt-injection": "BLOCK",
    }


def test_every_request_is_valid_for_the_backend_registry(scenarios):
    """Scenario data must match the backend's tool schemas (names and arguments)."""
    for s in scenarios.values():
        for step in s.steps:
            canonical = process_proposal(ActionProposal(
                agent_id=s.agent_id, tool=step.request.tool, arguments=step.request.arguments))
            assert canonical.tool == step.request.tool


def test_scenario_1_safe_action(scenarios):
    (step,) = scenarios["safe-action"].steps
    assert step.request.tool == "calendar_read" and step.auto_execute
    assert step.expect.lifecycle is DecisionLifecycle.EVALUATED and step.mock_semantic is None


def test_scenario_2_intent_mismatch(scenarios):
    s = scenarios["intent-mismatch"]
    (step,) = s.steps  # fresh session, no earlier read
    assert step.request.tool == "email_send" and not step.auto_execute
    assert step.mock_semantic == "misaligned" and step.human_review == "reject"
    assert "inbox" in s.user_task.lower() and "send" not in s.user_task.lower()
    assert "Q3 planning review" in step.request.arguments["body"]


def test_scenario_3_delete_review_execute_undo(scenarios):
    s = scenarios["bulk-delete"]
    (step,) = s.steps
    assert step.request.tool == "file_delete" and step.request.arguments["permanent"] is False
    assert not step.auto_execute and s.expects_human_approval and s.expects_undo
    assert step.expect.outcome is DecisionOutcome.REVIEW


def test_scenario_4_finance_flow(scenarios):
    s = scenarios["prompt-injection"]
    fetch, transfer = s.steps
    assert [fetch.request.tool, transfer.request.tool] == ["web_fetch", "payment_transfer"]
    assert fetch.auto_execute and fetch.expect.outcome is DecisionOutcome.APPROVE
    assert fetch.expects_observed == transfer.request.arguments["recipient"]
    assert transfer.expect.outcome is DecisionOutcome.BLOCK and not transfer.auto_execute
    assert transfer.mock_semantic == "injection"
    assert "compromised" in transfer.invariant_mock_modes  # a weak checker cannot unblock
    assert transfer.request.arguments["recipient"] not in s.user_task  # so it is not a USER value


# ---- generic schema -------------------------------------------------------------


def test_scenario_files_contain_only_schema_fields():
    scenario_keys = set(Scenario.model_fields)
    step_keys = set(Scenario.model_fields["steps"].annotation.__args__[0].model_fields)
    for path in SCENARIOS_DIR.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert set(data) <= scenario_keys, path.name
        for step in data["steps"]:
            assert set(step) <= step_keys, path.name
            assert set(step["request"]) <= {"tool", "arguments"}


def test_scenarios_hold_no_guard_logic_or_trusted_claims():
    bad = re.compile(r"allowlist|permission|grant|policy|policies|risk|reversib|confirm|threshold|limit|provenance")
    for path in SCENARIOS_DIR.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for step in data["steps"]:
            assert not bad.search(" ".join(sorted(_all_keys(step)))), path.name


def _all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_keys(v)


def test_scenario_ids_unique():
    ids = [s.id for s in load_scenarios()]
    assert len(ids) == len(set(ids)) == 4


# ---- malformed input is rejected ------------------------------------------------


def good_dict(**over):
    data = {
        "id": "demo", "title": "t", "description": "d", "agent_id": "a", "user_task": "u",
        "expected_verdict": "REVIEW",
        "steps": [{
            "request": {"tool": "t_x", "arguments": {}},
            "auto_execute": False,
            "expect": {"outcome": "REVIEW", "lifecycle": "PENDING_REVIEW"},
        }],
    }
    data.update(over)
    return data


def step_with(**over):
    step = good_dict()["steps"][0]
    step.update(over)
    return step


def write(tmp_path, data, name="s.json"):
    p = tmp_path / name
    p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return p


APPROVE = {"outcome": "APPROVE", "lifecycle": "EVALUATED"}


def test_good_scenario_loads(tmp_path):
    assert load_scenario(write(tmp_path, good_dict())).id == "demo"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("id"),
        lambda d: d.pop("steps"),
        lambda d: d.pop("user_task"),
        lambda d: d.pop("expected_verdict"),
        lambda d: d.update(extra="x"),
        lambda d: d.update(id="Not A Slug"),
        lambda d: d.update(title="  "),
        lambda d: d.update(steps=[]),
        lambda d: d.update(expected_verdict="APPROVE"),  # differs from the last step
        lambda d: d.update(expected_verdict="MAYBE"),
        lambda d: d.update(steps=[step_with(auto_execute=True)]),  # REVIEW cannot auto-run
        lambda d: d.update(steps=[step_with(expect={"outcome": "REVIEW", "lifecycle": "EVALUATED"})]),
        lambda d: d.update(steps=[step_with(mock_semantic="wild")]),
        lambda d: d.update(steps=[step_with(invariant_mock_modes=["compromised"])]),  # not BLOCK
        lambda d: d.update(steps=[step_with(invariant_mock_modes=["wild"],
                                            expect={"outcome": "BLOCK", "lifecycle": "BLOCKED"})]),
        lambda d: d.update(steps=[step_with(undo_expected=True)]),  # nothing executes
        lambda d: d.update(steps=[step_with(expects_observed="x")]),  # nothing executes
        lambda d: d.update(steps=[step_with(human_review="maybe")]),
        lambda d: d["steps"][0].pop("auto_execute"),
        lambda d: d["steps"][0].pop("expect"),
        lambda d: d["steps"][0]["request"].update(agent_id="evil"),
        lambda d: d["steps"][0]["request"].update(context={}),
        lambda d: d["steps"][0]["request"].update(arg_provenance={"a": "user_task"}),
        lambda d: d["steps"][0].update(risk="LOW"),
    ],
)
def test_malformed_scenarios_rejected(tmp_path, mutate):
    data = good_dict()
    mutate(data)
    with pytest.raises(ScenarioError):
        load_scenario(write(tmp_path, data))


def test_human_review_needs_a_review_step(tmp_path):
    with pytest.raises(ScenarioError):
        load_scenario(write(tmp_path, good_dict(
            expected_verdict="APPROVE", steps=[step_with(expect=APPROVE, human_review="approve")])))
    ok = good_dict(expected_verdict="APPROVE",
                   steps=[step_with(expect=APPROVE, auto_execute=True, undo_expected=True)])
    assert load_scenario(write(tmp_path, ok)).expects_undo


def test_invalid_json_and_missing_file(tmp_path):
    with pytest.raises(ScenarioError):
        load_scenario(write(tmp_path, "{not json"))
    with pytest.raises(ScenarioError):
        load_scenario(tmp_path / "missing.json")


def test_duplicate_ids_rejected(tmp_path):
    write(tmp_path, good_dict(), "a.json")
    write(tmp_path, good_dict(), "b.json")
    with pytest.raises(ScenarioError, match="duplicate scenario id"):
        load_scenarios(tmp_path)


def test_steps_keep_their_order(tmp_path):
    steps = [step_with(request={"tool": f"t_{n}", "arguments": {}}) for n in ("one", "two", "three")]
    s = load_scenario(write(tmp_path, good_dict(steps=steps)))
    assert [r.tool for r in s.requests()] == ["t_one", "t_two", "t_three"]
    s.requests()[0].arguments["x"] = 1
    assert s.steps[0].request.arguments == {}  # requests() returns copies


def test_load_scenarios_ignores_non_json_and_sorts(tmp_path):
    write(tmp_path, good_dict(id="b-second"), "02.json")
    write(tmp_path, good_dict(id="a-first"), "01.json")
    write(tmp_path, "ignored", "notes.txt")
    assert [s.id for s in load_scenarios(tmp_path)] == ["a-first", "b-second"]


# ---- external content is not stored in scenario files ------------------------------


def test_scenarios_carry_no_external_page_text():
    from app.world.state import WorldStore

    pages = WorldStore().state.web_cache.values()
    for path in SCENARIOS_DIR.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert not any(page in text for page in pages), path.name


def test_expected_observed_text_exists_in_the_backend_world():
    from app.world.state import WorldStore

    pages = " ".join(WorldStore().state.web_cache.values())
    for s in load_scenarios():
        for step in s.steps:
            if step.expects_observed:
                assert step.expects_observed in pages, s.id


# ---- genericity of the loader --------------------------------------------------------


def test_loader_has_no_domain_scenario_or_tool_vocabulary():
    src = SCENARIO_PY.read_text(encoding="utf-8").lower()
    for word in ("email", "calendar", "payment", "website", "invoice", "inbox", "transfer",
                 "delete", "web_fetch", "calendar_read", "email_send", "file_delete",
                 "payment_transfer", "safe-action", "intent-mismatch", "bulk-delete",
                 "prompt-injection", "vendor", "manager"):
        assert word not in src, word


def test_loader_has_no_scenario_branches_or_guard_imports():
    for node in ast.walk(ast.parse(SCENARIO_PY.read_text(encoding="utf-8"))):
        mods = []
        if isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        elif isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        for m in mods:
            assert not m.startswith(("fastapi", "app.guard", "app.api", "app.world")), m
