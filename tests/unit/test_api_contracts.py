"""
tests/unit/test_api_contracts.py
================================
HTTP API contract tests:
  - Trust boundary enforcement (HTTP rejects agent-supplied arg_provenance with 422)
  - Input validation errors return 400
  - Decision evaluation, review, execution, undo endpoints
  - Audit, world, tools, health, reset endpoints
  - Demo and scenario routes (/api/scenarios, /api/demo/*)
  - Admin routes (/api/admin/*)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# 1. Trust boundary: HTTP rejects arg_provenance
# ---------------------------------------------------------------------------

def test_http_evaluate_rejects_agent_supplied_provenance_with_422(http_client: TestClient):
    """
    HTTP POST /evaluate must return 422 Unprocessable Entity if caller
    attempts to send arg_provenance (trust boundary enforcement).
    """
    res = http_client.post(
        "/evaluate",
        json={
            "agent_id": "demo-agent",
            "tool": "payment_transfer",
            "arguments": {"amount": 100.0, "recipient": "bob"},
            "arg_provenance": {"recipient": "user_task"},  # FORBIDDEN over HTTP
        },
    )
    assert res.status_code == 422


def test_http_evaluate_rejects_unknown_tool_with_422(http_client: TestClient):
    """Unknown tool name returns 422 Unprocessable Entity."""
    res = http_client.post(
        "/evaluate",
        json={
            "agent_id": "demo-agent",
            "tool": "fake_tool",
            "arguments": {},
        },
    )
    assert res.status_code == 422
    assert "not registered" in res.json()["detail"]


def test_http_evaluate_rejects_path_traversal_with_422(http_client: TestClient):
    """Path traversal sequence returns 422 Unprocessable Entity."""
    res = http_client.post(
        "/evaluate",
        json={
            "agent_id": "demo-agent",
            "tool": "file_delete",
            "arguments": {"path": "../secret.txt"},
        },
    )
    assert res.status_code == 422
    assert "path traversal" in res.json()["detail"]


def test_http_evaluate_success(http_client: TestClient):
    """Valid proposal returns 200 and a complete GuardDecision object."""
    res = http_client.post(
        "/evaluate",
        json={
            "agent_id": "demo-agent",
            "tool": "calendar_read",
            "arguments": {},
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["outcome"] == "APPROVE"
    assert "decision_id" in data
    assert data["canonical_action"]["tool"] == "calendar_read"


# ---------------------------------------------------------------------------
# 2. Review endpoints: /review/approve, /review/reject, /review/pending
# ---------------------------------------------------------------------------

def test_http_review_approve_and_pending(http_client: TestClient):
    """Evaluate REVIEW proposal, check pending, approve via HTTP."""
    # Create a REVIEW decision (file_delete has base_risk 0.45 >= 0.40)
    eval_res = http_client.post(
        "/evaluate",
        json={
            "agent_id": "demo-agent",
            "tool": "file_delete",
            "arguments": {"path": "/home/user/documents/report_q4.pdf"},
        },
    )
    decision_id = eval_res.json()["decision_id"]
    assert eval_res.json()["outcome"] == "REVIEW"

    # Pending list should contain this decision
    pend_res = http_client.get("/review/pending")
    assert pend_res.status_code == 200
    pending_ids = [d["decision_id"] for d in pend_res.json()]
    assert decision_id in pending_ids

    # Approve
    app_res = http_client.post(
        "/review/approve",
        json={"decision_id": decision_id, "reviewer_id": "alice"},
    )
    assert app_res.status_code == 200
    assert app_res.json()["lifecycle"] == "APPROVED"


def test_http_review_cannot_approve_block(http_client: TestClient):
    """Attempting to approve a BLOCK decision returns 403."""
    eval_res = http_client.post(
        "/evaluate",
        json={
            "agent_id": "demo-agent",
            "tool": "payment_transfer",
            "arguments": {"amount": 50000.0, "recipient": "bob"},
        },
    )
    decision_id = eval_res.json()["decision_id"]
    assert eval_res.json()["outcome"] == "BLOCK"

    app_res = http_client.post(
        "/review/approve",
        json={"decision_id": decision_id},
    )
    assert app_res.status_code == 403


def test_http_review_cannot_approve_approve_decision(http_client: TestClient):
    """Attempting to approve an APPROVE decision returns 409."""
    eval_res = http_client.post(
        "/evaluate",
        json={
            "agent_id": "demo-agent",
            "tool": "calendar_read",
            "arguments": {},
        },
    )
    decision_id = eval_res.json()["decision_id"]

    app_res = http_client.post(
        "/review/approve",
        json={"decision_id": decision_id},
    )
    assert app_res.status_code == 409


def test_http_review_reject(http_client: TestClient):
    """Rejecting a REVIEW decision returns 200 and updates lifecycle."""
    eval_res = http_client.post(
        "/evaluate",
        json={
            "agent_id": "demo-agent",
            "tool": "file_delete",
            "arguments": {"path": "/home/user/documents/report_q4.pdf"},
        },
    )
    decision_id = eval_res.json()["decision_id"]

    rej_res = http_client.post(
        "/review/reject",
        json={"decision_id": decision_id, "reason": "Not allowed"},
    )
    assert rej_res.status_code == 200
    assert rej_res.json()["lifecycle"] == "REJECTED"


# ---------------------------------------------------------------------------
# 3. Execution & Undo endpoints: /execute, /undo
# ---------------------------------------------------------------------------

def test_http_execute_and_double_execute(http_client: TestClient):
    """Execute returns 200; executing again returns 409."""
    eval_res = http_client.post(
        "/evaluate",
        json={"agent_id": "demo-agent", "tool": "calendar_read", "arguments": {}},
    )
    decision_id = eval_res.json()["decision_id"]

    exec_res = http_client.post("/execute", json={"decision_id": decision_id})
    assert exec_res.status_code == 200
    assert exec_res.json()["success"] is True

    # Double execute
    exec_res2 = http_client.post("/execute", json={"decision_id": decision_id})
    assert exec_res2.status_code == 409


def test_http_undo_and_double_undo(http_client: TestClient):
    """Undo file delete returns 200; undoing again returns 409."""
    # Evaluate, approve, execute file_delete
    eval_res = http_client.post(
        "/evaluate",
        json={
            "agent_id": "demo-agent",
            "tool": "file_delete",
            "arguments": {"path": "/home/user/documents/report_q4.pdf"},
        },
    )
    decision_id = eval_res.json()["decision_id"]
    http_client.post("/review/approve", json={"decision_id": decision_id})
    http_client.post("/execute", json={"decision_id": decision_id})

    # Undo
    undo_res = http_client.post("/undo", json={"decision_id": decision_id})
    assert undo_res.status_code == 200
    assert undo_res.json()["success"] is True

    # Double undo
    undo_res2 = http_client.post("/undo", json={"decision_id": decision_id})
    assert undo_res2.status_code == 409


def test_http_undo_readonly_fails(http_client: TestClient):
    """Undoing read-only action returns 403."""
    eval_res = http_client.post(
        "/evaluate",
        json={"agent_id": "demo-agent", "tool": "calendar_read", "arguments": {}},
    )
    decision_id = eval_res.json()["decision_id"]
    http_client.post("/execute", json={"decision_id": decision_id})

    undo_res = http_client.post("/undo", json={"decision_id": decision_id})
    assert undo_res.status_code == 403


# ---------------------------------------------------------------------------
# 4. Decisions, Audit, World, Tools, Health, Reset endpoints
# ---------------------------------------------------------------------------

def test_http_decisions_endpoints(http_client: TestClient):
    """GET /decisions and GET /decisions/{id} return expected data."""
    eval_res = http_client.post(
        "/evaluate",
        json={"agent_id": "demo-agent", "tool": "calendar_read", "arguments": {}},
    )
    decision_id = eval_res.json()["decision_id"]

    list_res = http_client.get("/decisions")
    assert list_res.status_code == 200
    assert any(d["decision_id"] == decision_id for d in list_res.json())

    get_res = http_client.get(f"/decisions/{decision_id}")
    assert get_res.status_code == 200
    assert get_res.json()["decision_id"] == decision_id

    missing_res = http_client.get("/decisions/nonexistent-id")
    assert missing_res.status_code == 404


def test_http_audit_endpoints(http_client: TestClient):
    """GET /audit and GET /audit/decision/{id} return audit logs."""
    eval_res = http_client.post(
        "/evaluate",
        json={"agent_id": "demo-agent", "tool": "calendar_read", "arguments": {}},
    )
    decision_id = eval_res.json()["decision_id"]
    http_client.post("/execute", json={"decision_id": decision_id})

    audit_res = http_client.get("/audit")
    assert audit_res.status_code == 200
    assert len(audit_res.json()) > 0

    dec_audit_res = http_client.get(f"/audit/decision/{decision_id}")
    assert dec_audit_res.status_code == 200
    assert len(dec_audit_res.json()) >= 2


def test_http_world_and_tools_endpoints(http_client: TestClient):
    """GET /world and GET /tools return summary and tool registry."""
    world_res = http_client.get("/world")
    assert world_res.status_code == 200
    assert "emails_in_inbox" in world_res.json()

    tools_res = http_client.get("/tools")
    assert tools_res.status_code == 200
    tool_ids = [t["id"] for t in tools_res.json()]
    for expected in [
        "calendar_read", "email_read", "email_send",
        "web_fetch", "file_delete", "payment_transfer",
    ]:
        assert expected in tool_ids


def test_http_health_and_reset_endpoints(http_client: TestClient):
    """GET /health and POST /reset work as expected."""
    health_res = http_client.get("/health")
    assert health_res.status_code == 200
    assert health_res.json()["status"] == "ok"

    reset_res = http_client.post("/reset")
    assert reset_res.status_code == 200
    assert reset_res.json()["status"] == "reset"


# ---------------------------------------------------------------------------
# 5. Demo & Harness Routes (/api/scenarios, /api/demo/*)
# ---------------------------------------------------------------------------

def test_http_api_scenarios_listing(http_client: TestClient):
    """GET /api/scenarios returns all 4 scenarios."""
    res = http_client.get("/api/scenarios")
    assert res.status_code == 200
    scenarios = res.json()
    assert len(scenarios) == 4
    scenario_ids = [s["id"] for s in scenarios]
    assert "safe-action" in scenario_ids
    assert "intent-mismatch" in scenario_ids
    assert "bulk-delete" in scenario_ids
    assert "prompt-injection" in scenario_ids


def test_http_api_demo_run_flow(http_client: TestClient):
    """Start, step, and execute a demo run via /api/demo/*."""
    # 1. Start run
    start_res = http_client.post(
        "/api/demo/start",
        json={"scenario_id": "safe-action"},
    )
    assert start_res.status_code == 200
    run_id = start_res.json()["run_id"]
    assert start_res.json()["total_steps"] == 1

    # 2. Step
    step_res = http_client.post(f"/api/demo/{run_id}/step")
    assert step_res.status_code == 200
    step_data = step_res.json()
    assert step_data["can_execute"] is True
    assert step_data["decision"]["outcome"] == "APPROVE"

    # 3. Execute
    exec_res = http_client.post(f"/api/demo/{run_id}/execute")
    assert exec_res.status_code == 200
    assert exec_res.json()["execution_result"]["success"] is True
    assert exec_res.json()["status"] == "completed"


# ---------------------------------------------------------------------------
# 6. Admin Routes (/api/admin/*)
# ---------------------------------------------------------------------------

def test_http_api_admin_routes(http_client: TestClient):
    """Verify /api/admin/review, /api/admin/undo, /api/admin/reset, semantic-mode, policy-override."""
    # Admin reset
    reset_res = http_client.post("/api/admin/reset")
    assert reset_res.status_code == 200

    # Admin semantic-mode
    mode_set = http_client.post(
        "/api/admin/semantic-mode",
        json={"mode": "replay"},
    )
    assert mode_set.status_code == 200
    assert mode_set.json()["mode"] == "replay"

    mode_get = http_client.get("/api/admin/semantic-mode")
    assert mode_get.status_code == 200
    assert mode_get.json()["mode"] == "replay"

    # Reset semantic-mode back
    http_client.post("/api/admin/semantic-mode", json={"mode": "mock"})

    # Admin policy-override
    override_res = http_client.post(
        "/api/admin/policy-override",
        json={"block_threshold": 30000, "review_threshold": 7000},
    )
    assert override_res.status_code == 200
    assert "applied_overrides" in override_res.json()

    # Admin review & undo
    eval_res = http_client.post(
        "/evaluate",
        json={
            "agent_id": "demo-agent",
            "tool": "file_delete",
            "arguments": {"path": "/home/user/documents/report_q4.pdf"},
        },
    )
    dec_id = eval_res.json()["decision_id"]

    rev_res = http_client.post(
        "/api/admin/review",
        json={"decision_id": dec_id, "action": "approve", "reviewer_id": "admin"},
    )
    assert rev_res.status_code == 200
    assert rev_res.json()["lifecycle"] == "APPROVED"

    http_client.post("/execute", json={"decision_id": dec_id})

    undo_res = http_client.post(
        "/api/admin/undo",
        json={"decision_id": dec_id},
    )
    assert undo_res.status_code == 200
    assert undo_res.json()["success"] is True
