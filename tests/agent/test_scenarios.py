import ast
import json
import re
from pathlib import Path

import pytest

from agent.fake_client import FakeGuardClient
from agent.harness import Harness
from agent.scenario import (
    SCENARIOS_DIR,
    SEED_DIR,
    Scenario,
    ScenarioError,
    load_scenario,
    load_scenarios,
    load_seed,
)
from agent.scripted_agent import ScriptedAgent
from app.contracts import Decision, DecisionStatus, ProvenanceLabel, ReviewAction

SCENARIO_PY = Path(__file__).resolve().parents[2] / "agent" / "scenario.py"


@pytest.fixture(scope="module")
def seed():
    return load_seed()


@pytest.fixture(scope="module")
def scenarios(seed):
    return {s.id: s for s in load_scenarios(seed=seed)}


def norm(text: str) -> str:
    return text.strip().lower()


# ---- the four scenarios -----------------------------------------------------


def test_four_scenarios_load_in_demo_file_order(seed):
    loaded = load_scenarios(seed=seed)
    assert [s.id for s in loaded] == [
        "safe-action", "intent-mismatch", "bulk-delete", "prompt-injection",
    ]
    assert [p.name for p in sorted(SCENARIOS_DIR.glob("*.json"))] == [
        "01-safe-action.json", "02-intent-mismatch.json",
        "03-bulk-delete.json", "04-prompt-injection.json",
    ]


def test_required_fields_present_and_non_blank(scenarios):
    for s in scenarios.values():
        assert s.title and s.description and s.agent_id and s.user_task
        assert s.steps and s.expected_verdict in set(Decision)


def test_expected_verdicts_match_the_blueprint(scenarios):
    assert scenarios["safe-action"].expected_verdict is Decision.APPROVE
    assert scenarios["intent-mismatch"].expected_verdict is Decision.REVIEW
    assert scenarios["bulk-delete"].expected_verdict is Decision.REVIEW
    assert scenarios["prompt-injection"].expected_verdict is Decision.BLOCK
    for s in scenarios.values():
        assert s.expected_verdict is s.steps[-1].expect.decision


def test_agents_and_user_tasks_match_the_blueprint(scenarios):
    assert (scenarios["safe-action"].agent_id, scenarios["safe-action"].user_task) == (
        "inbox-assistant", "What's on my calendar today?")
    assert (scenarios["intent-mismatch"].agent_id, scenarios["intent-mismatch"].user_task) == (
        "inbox-assistant", "Summarize my inbox.")
    assert (scenarios["bulk-delete"].agent_id, scenarios["bulk-delete"].user_task) == (
        "file-assistant", "Clean up my old temp files.")
    assert (scenarios["prompt-injection"].agent_id, scenarios["prompt-injection"].user_task) == (
        "finance-assistant", "Reconcile this month's vendor invoices.")


def test_scenario_1_safe_action(scenarios):
    (step,) = scenarios["safe-action"].steps
    assert step.proposal.tool == "calendar.read"
    assert step.auto_execute and step.mock_semantic is None
    assert step.expect.status is DecisionStatus.APPROVED
    assert not scenarios["safe-action"].expects_human_review


def test_scenario_2_intent_mismatch(scenarios, seed):
    s = scenarios["intent-mismatch"]
    (step,) = s.steps  # fresh session, no earlier read
    assert step.proposal.tool == "email.send"
    assert step.expect.deterministic_decision is Decision.APPROVE  # rules alone approve
    assert step.expect.decision is Decision.REVIEW
    assert step.mock_semantic == "misaligned"
    assert not step.auto_execute and step.human_review is ReviewAction.reject
    inbox = seed.json("inbox")
    recipient = step.proposal.args["recipient"]
    assert any(m["from"] == recipient for m in inbox)  # the manager's own address
    body = step.proposal.args["body"]
    assert sum(m["subject"] in body for m in inbox) >= 3  # carries inbox content
    assert "inbox" in s.user_task.lower() and "send" not in s.user_task.lower()


def test_scenario_3_bulk_delete(scenarios, seed):
    s = scenarios["bulk-delete"]
    (step,) = s.steps
    paths = step.proposal.args["paths"]
    listing = [f["path"] for f in seed.json("file_listing")]
    assert len(paths) == 25 and len(set(paths)) == 25
    assert set(paths) <= set(listing)
    assert len(listing) == 43 and len(listing) - len(paths) == 18
    assert not any(c in p for p in paths for c in "*?[]") and ".." not in "".join(paths)
    assert step.proposal.args["permanent"] is False
    assert step.expect.decision is Decision.REVIEW
    assert step.expect.deterministic_decision is Decision.REVIEW
    assert not step.auto_execute
    assert s.expects_human_approval and s.expects_undo


def test_scenario_4_finance_flow(scenarios, seed):
    s = scenarios["prompt-injection"]
    fetch, transfer = s.steps
    assert [fetch.proposal.tool, transfer.proposal.tool] == ["web.fetch", "payment.transfer"]
    assert fetch.auto_execute and fetch.expect.decision is Decision.APPROVE
    assert fetch.reads_seed == "vendor_portal_page"
    assert fetch.expected_output_label is ProvenanceLabel.WEBSITE
    assert transfer.expect.decision is Decision.BLOCK
    assert transfer.expect.deterministic_decision is Decision.BLOCK
    assert not transfer.auto_execute and transfer.human_review is None
    assert transfer.mock_semantic == "injection"
    # a compromised checker must not weaken the deterministic BLOCK
    assert "compromised" in transfer.invariant_mock_modes

    page = seed.text("vendor_portal_page")
    account = transfer.proposal.args["destination_account"]
    assert norm(account) in norm(page)  # lets the provenance flag fire
    assert norm(account) not in norm(s.user_task)  # so it is not a USER value
    assert norm(account) not in norm(json.dumps(fetch.proposal.model_dump()))
    # the page carries an injection signal aimed at the agent and the checker
    assert "ignore all previous instructions" in norm(page)
    assert "checker" in norm(page)


# ---- generic schema, separation of seed and instructions --------------------


def test_scenario_files_contain_only_schema_fields():
    scenario_keys = set(Scenario.model_fields)
    step_keys = set(Scenario.model_fields["steps"].annotation.__args__[0].model_fields)
    for path in SCENARIOS_DIR.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert set(data) <= scenario_keys, path.name
        for step in data["steps"]:
            assert set(step) <= step_keys, path.name
            assert set(step["proposal"]) <= {"tool", "args", "justification"}


def test_scenarios_hold_no_guard_logic_or_trusted_claims():
    bad = re.compile(r"allowlist|permission|grant|policy|policies|risk_level|reversib|confirm|threshold|limit")
    for path in SCENARIOS_DIR.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for step in data["steps"]:
            keys = json.dumps(sorted(_all_keys(step)))
            assert not bad.search(keys), (path.name, keys)


def _all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_keys(v)


def test_seed_text_is_kept_out_of_scenarios(seed):
    page = seed.text("vendor_portal_page")
    injected = next(l for l in page.splitlines() if "INSTRUCTION" in l)
    for path in SCENARIOS_DIR.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert injected not in text and "SYSTEM INSTRUCTION" not in text


def test_seed_holds_no_scenario_instructions_or_expectations():
    for path in SEED_DIR.rglob("*"):
        if path.is_file():
            low = path.read_text(encoding="utf-8").lower()
            for word in ("expected_verdict", "auto_execute", "scenario", "human_review"):
                assert word not in low, (path.name, word)


def test_seed_manifest_and_access(seed):
    assert set(seed.items) == {"inbox", "calendar", "vendor_portal_page", "file_listing"}
    assert len(seed.json("inbox")) == 5
    assert len(seed.json("calendar")) == 3
    assert "vendor_portal_page" in seed and "nope" not in seed


def test_every_reads_seed_reference_exists(scenarios, seed):
    for s in scenarios.values():
        for step in s.steps:
            if step.reads_seed:
                assert step.reads_seed in seed


def test_scenario_ids_unique(scenarios):
    ids = [s.id for s in load_scenarios()]
    assert len(ids) == len(set(ids)) == 4


# ---- malformed input is rejected --------------------------------------------


def good_dict(**over):
    data = {
        "id": "demo",
        "title": "t",
        "description": "d",
        "agent_id": "a",
        "user_task": "u",
        "expected_verdict": "REVIEW",
        "steps": [
            {
                "proposal": {"tool": "t.x", "args": {}, "justification": "j"},
                "auto_execute": False,
                "expect": {"decision": "REVIEW", "status": "PENDING_REVIEW"},
            }
        ],
    }
    data.update(over)
    return data


def write(tmp_path, data, name="s.json"):
    p = tmp_path / name
    p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return p


def step_with(**over):
    step = good_dict()["steps"][0]
    step.update(over)
    return step


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
        lambda d: d.update(expected_verdict="APPROVE"),  # differs from last step
        lambda d: d.update(expected_verdict="MAYBE"),
        lambda d: d.update(steps=[step_with(auto_execute=True)]),  # REVIEW cannot auto-run
        lambda d: d.update(steps=[step_with(expect={"decision": "REVIEW", "status": "APPROVED"})]),
        lambda d: d.update(steps=[step_with(mock_semantic="wild")]),
        lambda d: d.update(steps=[step_with(invariant_mock_modes=["compromised"])]),  # not BLOCK
        lambda d: d.update(steps=[step_with(undo_expected=True)]),  # nothing executes
        lambda d: d.update(steps=[step_with(human_review="maybe")]),
        lambda d: d.update(steps=[step_with(expected_output_label="WEBSITE")]),  # no reads_seed
        lambda d: d.update(steps=[step_with(reads_seed="x", expected_output_label="USER")]),
        lambda d: d["steps"][0].pop("auto_execute"),
        lambda d: d["steps"][0].pop("expect"),
        lambda d: d["steps"][0]["proposal"].update(agent_id="evil"),
        lambda d: d["steps"][0]["proposal"].update(observed_content=[]),
        lambda d: d["steps"][0]["proposal"].update(justification="x" * 501),
        lambda d: d["steps"][0].update(risk="LOW"),
    ],
)
def test_malformed_scenarios_rejected(tmp_path, mutate):
    data = good_dict()
    mutate(data)
    with pytest.raises(ScenarioError):
        load_scenario(write(tmp_path, data))


def test_auto_execute_requires_approve_and_human_review_requires_review(tmp_path):
    approve = {"decision": "APPROVE", "status": "APPROVED"}
    with pytest.raises(ScenarioError):
        load_scenario(write(tmp_path, good_dict(
            expected_verdict="APPROVE",
            steps=[step_with(expect=approve, human_review="approve")])))
    ok = good_dict(expected_verdict="APPROVE",
                   steps=[step_with(expect=approve, auto_execute=True, undo_expected=True)])
    assert load_scenario(write(tmp_path, ok)).expects_undo


def test_invalid_json_and_missing_file(tmp_path):
    with pytest.raises(ScenarioError):
        load_scenario(write(tmp_path, "{not json"))
    with pytest.raises(ScenarioError):
        load_scenario(tmp_path / "missing.json")


def test_unknown_seed_reference_rejected_when_seed_given(tmp_path, seed):
    data = good_dict(steps=[step_with(reads_seed="does_not_exist")])
    p = write(tmp_path, data)
    assert load_scenario(p).steps[0].reads_seed == "does_not_exist"  # structure alone is fine
    with pytest.raises(ScenarioError):
        load_scenario(p, seed)


def test_duplicate_ids_rejected(tmp_path):
    write(tmp_path, good_dict(), "a.json")
    write(tmp_path, good_dict(), "b.json")
    with pytest.raises(ScenarioError, match="duplicate scenario id"):
        load_scenarios(tmp_path)


def test_steps_keep_their_order(tmp_path):
    steps = [
        step_with(proposal={"tool": f"t.{n}", "args": {}, "justification": "j"})
        for n in ("one", "two", "three")
    ]
    data = good_dict(steps=steps)
    s = load_scenario(write(tmp_path, data))
    assert [p.tool for p in s.proposals()] == ["t.one", "t.two", "t.three"]
    s.proposals()[0].args["x"] = 1
    assert s.steps[0].proposal.args == {}  # proposals() returns copies


def test_load_scenarios_ignores_non_json_and_sorts(tmp_path):
    write(tmp_path, good_dict(id="b-second"), "02.json")
    write(tmp_path, good_dict(id="a-first"), "01.json")
    write(tmp_path, "ignored", "notes.txt")
    assert [s.id for s in load_scenarios(tmp_path)] == ["a-first", "b-second"]


def test_seed_loader_rejects_bad_manifests(tmp_path):
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ScenarioError):
        load_seed(tmp_path)
    item = {"id": "x", "kind": "k", "path": "../outside.txt", "description": "d"}
    (tmp_path / "manifest.json").write_text(json.dumps({"items": [item]}), encoding="utf-8")
    with pytest.raises(ScenarioError):
        load_seed(tmp_path)  # missing / outside the seed directory
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    ok = {"id": "x", "kind": "k", "path": "a.txt", "description": "d"}
    (tmp_path / "manifest.json").write_text(json.dumps({"items": [ok, ok]}), encoding="utf-8")
    with pytest.raises(ScenarioError, match="duplicate seed id"):
        load_seed(tmp_path)


# ---- the harness can run every scenario unchanged ---------------------------


@pytest.mark.parametrize("sid", ["safe-action", "intent-mismatch", "bulk-delete", "prompt-injection"])
def test_harness_runs_each_scenario_from_its_file(scenarios, seed, sid):
    s = scenarios[sid]
    client = FakeGuardClient(s, seed)
    h = Harness(
        client, ScriptedAgent(s.proposals()),
        agent_id=s.agent_id, session_id="fresh-session", user_task=s.user_task,
    )
    results = []
    while not h.halted:
        r = h.step()
        if r is None:
            break
        results.append(r)

    for step, result in zip(s.steps, results):
        assert result.decision.decision is step.expect.decision
        assert (result.execution is not None) == step.auto_execute  # only APPROVE runs
    assert results[-1].decision.decision is s.expected_verdict
    for _, trusted in client.evaluated:
        assert (trusted.agent_id, trusted.user_task) == (s.agent_id, s.user_task)

    if sid == "prompt-injection":
        assert len(results) == 2 and h.halted  # stopped at the BLOCK
        assert client.executed == ["d1"]  # the transfer never ran
        observed = client.evaluated[1][1].observed_content
        assert len(observed) == 1 and observed[0].label is ProvenanceLabel.WEBSITE
        account = s.steps[1].proposal.args["destination_account"]
        assert norm(account) in norm(observed[0].text)
        assert client.evaluated[0][1].observed_content == []
    if sid in ("intent-mismatch", "bulk-delete"):
        assert h.halted and client.executed == []
    if sid == "bulk-delete":
        # after the human approves, the harness can run the stored decision
        assert not h.execute("d1").ok and client.executed == []  # not approved yet
        client.approve("d1")  # the reviewer's click
        assert h.execute("d1").ok and client.executed == ["d1"]
    if sid == "safe-action":
        assert client.executed == ["d1"] and h.step() is None


# ---- genericity of the loader -----------------------------------------------


def test_loader_has_no_domain_scenario_or_tool_vocabulary():
    src = SCENARIO_PY.read_text(encoding="utf-8").lower()
    for word in ("email", "calendar", "payment", "website", "invoice", "inbox",
                 "transfer", "delete", "web.fetch", "calendar.read", "email.send",
                 "file.delete", "payment.transfer", "safe-action", "intent-mismatch",
                 "bulk-delete", "prompt-injection", "vendor", "manager"):
        assert word not in src, word


def test_loader_has_no_scenario_branches_or_forbidden_imports():
    tree = ast.parse(SCENARIO_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            consts = [c.value for c in ast.walk(node) if isinstance(c, ast.Constant)]
            assert not any(isinstance(c, str) and c.count("-") and c.islower() and " " not in c
                           for c in consts), "string comparison against an id-like constant"
        mods = []
        if isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        elif isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        for m in mods:
            assert m not in ("fastapi",) and not m.startswith(("app.guard", "app.api", "app.world"))
