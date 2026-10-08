"""
tests/conftest.py
=================
Shared fixtures for SENTINEL Person 2 test suite.

Every test that needs a clean state calls the fresh_* fixtures.
Fixtures use reset functions to create isolated instances — no shared
state between tests.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.audit.log import AuditLog, reset_audit_log
from app.contracts.proposal import ActionProposal, ProposalContext
from app.contracts.semantic import SemanticOutcome, SemanticResult
from app.guard.client import GuardClient
from app.guard.engine import GuardEngine, reset_guard_engine
from app.guard.gateway import reset_execution_gateway
from app.guard.rules import PolicyConfig, RulesEngine, reset_rules_engine
from app.guard.service import GuardService, reset_guard_service
from app.guard.store import DecisionStore, reset_decision_store
from app.world.executor import ToolExecutor, reset_tool_executor
from app.world.state import WorldStore, reset_world_store


# ---------------------------------------------------------------------------
# Core fixtures — fresh isolated instances per test
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_world() -> WorldStore:
    """A freshly seeded world store, reset per test."""
    return reset_world_store()


@pytest.fixture
def fresh_executor(fresh_world: WorldStore) -> ToolExecutor:
    """A fresh tool executor wired to fresh_world."""
    return reset_tool_executor(fresh_world)


@pytest.fixture
def fresh_store() -> DecisionStore:
    """A fresh empty decision store."""
    return reset_decision_store()


@pytest.fixture
def fresh_audit() -> AuditLog:
    """A fresh empty audit log."""
    return reset_audit_log()


@pytest.fixture
def fresh_engine() -> GuardEngine:
    """A fresh guard engine (resets singleton)."""
    return reset_guard_engine()


@pytest.fixture
def fresh_service(
    fresh_world: WorldStore,
    fresh_executor: ToolExecutor,
    fresh_store: DecisionStore,
    fresh_audit: AuditLog,
    fresh_engine: GuardEngine,
) -> GuardService:
    """
    A fully fresh GuardService with all dependencies reset and wired together.
    This is the primary fixture for service-level tests.
    """
    reset_execution_gateway(
        store=fresh_store,
        engine=fresh_engine,
        executor=fresh_executor,
        audit=fresh_audit,
    )
    svc = reset_guard_service()
    return svc


@pytest.fixture
def client(fresh_service: GuardService) -> GuardClient:
    """A GuardClient wired to a fresh service."""
    return GuardClient(fresh_service)


@pytest.fixture
def http_client(fresh_service: GuardService) -> TestClient:
    """FastAPI TestClient for API-level tests."""
    from app.api.main import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# Common proposal factories
# ---------------------------------------------------------------------------


@pytest.fixture
def calendar_proposal() -> ActionProposal:
    return ActionProposal(
        agent_id="demo-agent",
        tool="calendar_read",
        arguments={},
    )


@pytest.fixture
def payment_small() -> ActionProposal:
    """Payment below review threshold — should APPROVE."""
    return ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 500.0, "recipient": "bob"},
    )


@pytest.fixture
def payment_review() -> ActionProposal:
    """Payment above review threshold — should REVIEW."""
    return ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 10000.0, "recipient": "bob"},
    )


@pytest.fixture
def payment_block() -> ActionProposal:
    """Payment above block threshold — should BLOCK."""
    return ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 50000.0, "recipient": "bob"},
    )


@pytest.fixture
def file_delete_proposal() -> ActionProposal:
    return ActionProposal(
        agent_id="demo-agent",
        tool="file_delete",
        arguments={"path": "/home/user/documents/report_q4.pdf"},
    )


@pytest.fixture
def email_read_proposal() -> ActionProposal:
    return ActionProposal(
        agent_id="demo-agent",
        tool="email_read",
        arguments={},
    )


@pytest.fixture
def email_send_proposal() -> ActionProposal:
    return ActionProposal(
        agent_id="demo-agent",
        tool="email_send",
        arguments={
            "to": "external@example.com",
            "subject": "Hello",
            "body": "Test message",
        },
    )


# ---------------------------------------------------------------------------
# Common semantic results
# ---------------------------------------------------------------------------


@pytest.fixture
def sem_safe() -> SemanticResult:
    return SemanticResult(
        outcome=SemanticOutcome.SAFE, reason="no concerns", provider="MOCK"
    )


@pytest.fixture
def sem_suspicious() -> SemanticResult:
    return SemanticResult(
        outcome=SemanticOutcome.SUSPICIOUS,
        reason="action seems unrelated to user task",
        provider="MOCK",
    )


@pytest.fixture
def sem_unsafe() -> SemanticResult:
    return SemanticResult(
        outcome=SemanticOutcome.UNSAFE, reason="strongly objected", provider="MOCK"
    )


@pytest.fixture
def sem_timeout() -> SemanticResult:
    return SemanticResult(
        outcome=SemanticOutcome.TIMEOUT, reason="provider timed out", provider="MOCK"
    )


@pytest.fixture
def sem_invalid() -> SemanticResult:
    return SemanticResult(
        outcome=SemanticOutcome.INVALID, reason="could not reason", provider="MOCK"
    )


@pytest.fixture
def sem_unavailable() -> SemanticResult:
    return SemanticResult(
        outcome=SemanticOutcome.UNAVAILABLE,
        reason="provider not reachable",
        provider="MOCK",
    )
