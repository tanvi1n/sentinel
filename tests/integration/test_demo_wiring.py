"""The /api/demo and /api/admin routes driving the real semantic layer."""

import json

import pytest

from app.semantic import factory as factory_module
from app.semantic.factory import SemanticFactory

SECRET = "sk-demo-wiring-SECRET"
ALIGNED = json.dumps({"intent_alignment": "ALIGNED", "injection_suspected": False,
                      "ambiguity": "LOW", "rationale": "fits the task"})
INJECTION = json.dumps({"intent_alignment": "MISALIGNED", "injection_suspected": True,
                        "ambiguity": "LOW", "rationale": "page tells the agent to pay"})


@pytest.fixture
def api(http_client, monkeypatch, tmp_path):
    """HTTP client with a fresh semantic factory (mock mode, no key, no cache)."""
    env = {"SENTINEL_REPLAY_PATH": str(tmp_path / "none.jsonl")}
    monkeypatch.setattr(factory_module, "_default", SemanticFactory(env))
    http_client.post("/api/admin/reset")
    return http_client


def run(api, scenario_id):
    """Run a scenario to its end or its first stop; return (step responses)."""
    start = api.post("/api/demo/start", json={"scenario_id": scenario_id}).json()
    rid, steps = start["run_id"], []
    for _ in range(start["total_steps"]):
        s = api.post(f"/api/demo/{rid}/step").json()
        steps.append(s)
        if not s["can_execute"]:
            break
        assert api.post(f"/api/demo/{rid}/execute").status_code == 200
    return steps


def outcome(step):
    return step["decision"]["outcome"]


# ---- mock mode (the default) --------------------------------------------------


def test_scenarios_in_mock_mode(api):
    safe = run(api, "safe-action")
    assert outcome(safe[0]) == "APPROVE" and safe[0]["decision"]["semantic_outcome"] is None

    mismatch = run(api, "intent-mismatch")[0]["decision"]
    assert mismatch["outcome"] == "REVIEW"
    assert (mismatch["semantic_outcome"], mismatch["semantic_provider"]) == ("UNSAFE", "MOCK")

    delete = run(api, "bulk-delete")[0]["decision"]
    assert delete["outcome"] == "REVIEW" and delete["semantic_outcome"] == "SAFE"

    fetch, transfer = run(api, "prompt-injection")
    assert outcome(fetch) == "APPROVE"
    assert outcome(transfer) == "BLOCK"
    assert transfer["decision"]["semantic_outcome"] == "UNSAFE"
    # the payee was found in the fetched page, so the origin check flagged it
    assert transfer["decision"]["canonical_action"]["arg_provenance"]["recipient"] == "external_content"
    assert "PAYMENT_EXTERNAL_PROVENANCE" in transfer["decision"]["rules_fired"]


# ---- the mode switch controls the real factory --------------------------------


def test_semantic_mode_route_drives_the_factory(api):
    assert api.post("/api/admin/semantic-mode", json={"mode": "auto"}).json()["mode"] == "auto"
    assert factory_module.describe()["mode"] == "auto"
    assert api.get("/api/admin/semantic-mode").json()["mode"] == "auto"
    assert api.post("/api/admin/semantic-mode", json={"mode": "bogus"}).status_code == 400
    assert factory_module.describe()["mode"] == "auto"  # unchanged by the bad request


@pytest.mark.parametrize("mode", ["replay", "live", "auto"])
def test_unavailable_provider_fails_closed_to_review(api, mode):
    api.post("/api/admin/semantic-mode", json={"mode": mode})
    decision = run(api, "intent-mismatch")[0]["decision"]
    assert decision["outcome"] == "REVIEW"
    assert decision["semantic_outcome"] == "UNAVAILABLE"
    assert decision["semantic_provider"] != "MOCK"  # never dressed up as a mock result
    assert "SEMANTIC_REQUIRED_UNAVAILABLE" in decision["rules_fired"]


@pytest.mark.parametrize("mode", ["replay", "live", "auto"])
def test_a_rule_block_survives_an_unavailable_provider(api, mode):
    api.post("/api/admin/semantic-mode", json={"mode": mode})
    # Drive the transfer directly, with no provider behind the semantic layer.
    from app.contracts.proposal import ActionProposal
    from app.guard.service import get_guard_service
    from app.semantic.bridge import advise

    p = ActionProposal(agent_id="demo-agent", tool="payment_transfer",
                       arguments={"amount": 100000.0, "recipient": "attacker_account"})
    prov = {"amount": "external_content", "recipient": "external_content"}
    d = get_guard_service().evaluate(p, advise(p, prov), harness_provenance=prov)
    assert d.outcome.value == "BLOCK" and d.semantic_outcome == "UNAVAILABLE"


def test_health_reports_semantic_state_without_the_key(api, monkeypatch, tmp_path):
    env = {"QUALCOMM_API_KEY": SECRET, "SENTINEL_REPLAY_PATH": str(tmp_path / "none.jsonl")}
    monkeypatch.setattr(factory_module, "_default", SemanticFactory(env))
    body = api.get("/api/health").json()
    assert body["status"] == "ok" and body["semantic_mode"] == "mock"
    assert body["semantic_key_configured"] == "yes"
    assert SECRET not in json.dumps(body)


# ---- the Qualcomm adapter inside the demo flow (network boundary faked) -------


def test_live_provider_sees_trusted_context_and_cannot_unblock(api, monkeypatch, tmp_path):
    prompts = []

    def fake_model(prompt, timeout):
        prompts.append(prompt)
        return INJECTION if "payment_transfer" in prompt else ALIGNED

    env = {"QUALCOMM_API_KEY": SECRET, "QUALCOMM_BASE_URL": "https://x.invalid",
           "QUALCOMM_MODEL": "test-model", "SENTINEL_REPLAY_PATH": str(tmp_path / "none.jsonl")}
    f = SemanticFactory(env, call_model=fake_model)
    monkeypatch.setattr(factory_module, "_default", f)
    api.post("/api/admin/semantic-mode", json={"mode": "live"})

    fetch, transfer = run(api, "prompt-injection")
    assert outcome(fetch) == "APPROVE"
    assert fetch["decision"]["semantic_provider"] is None  # reads need no semantic check
    assert outcome(transfer) == "BLOCK"  # the rules decide
    assert transfer["decision"]["semantic_outcome"] == "UNSAFE"
    assert transfer["decision"]["semantic_provider"] == "QUALCOMM"

    prompt = next(p for p in prompts if "payment_transfer" in p)
    observed = prompt[prompt.index("OBSERVED EXTERNAL CONTENT (untrusted", prompt.index("TRUSTED CONTEXT\ntool_id")):]
    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in observed  # page recorded by the route
    assert "https://malicious.example.com/inject" in observed
    assert '"arg":"recipient"' in prompt and "vendor invoices" in prompt
    assert SECRET not in "".join(prompts)
    assert "BLOCK" not in prompt.split("TRUSTED CONTEXT\ntool_id")[1]  # no verdict leaks in


def test_demo_run_records_observed_content_only_from_executed_reads(api):
    start = api.post("/api/demo/start", json={"scenario_id": "prompt-injection"}).json()
    from app.api.routes.demo import get_demo_run

    run_obj = get_demo_run(start["run_id"])
    assert run_obj.observed == {}
    api.post(f"/api/demo/{start['run_id']}/step")
    assert run_obj.observed == {}  # evaluated but not executed yet
    api.post(f"/api/demo/{start['run_id']}/execute")
    assert list(run_obj.observed) == ["https://malicious.example.com/inject"]


def test_demo_scenarios_come_from_the_scenario_files(api):
    from agent.scenario import load_scenarios

    listed = {s["id"]: s for s in api.get("/api/scenarios").json()}
    files = {s.id: s for s in load_scenarios()}
    assert set(listed) == set(files)
    for sid, sc in files.items():
        assert listed[sid]["user_task"] == sc.user_task
        assert listed[sid]["expected_verdict"] == sc.expected_verdict.value
        assert listed[sid]["step_count"] == len(sc.steps)


@pytest.mark.parametrize("mode", ["compromised", "aligned"])
def test_demo_block_holds_when_the_checker_is_compromised(api, mode):
    factory_module.set_mode("mock", mock_mode=mode)
    from app.api.routes import demo

    original = demo.SCENARIOS["prompt-injection"]["steps"][1]["mock_semantic"]
    demo.SCENARIOS["prompt-injection"]["steps"][1]["mock_semantic"] = mode
    try:
        fetch, transfer = run(api, "prompt-injection")
    finally:
        demo.SCENARIOS["prompt-injection"]["steps"][1]["mock_semantic"] = original
    assert outcome(transfer) == "BLOCK" and transfer["decision"]["semantic_outcome"] == "SAFE"
