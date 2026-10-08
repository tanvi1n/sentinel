"""
tests/unit/test_risk.py
=======================
Unit tests for RiskCalculator:
  - Calibrated base risk for each tool
  - Dynamic payment amount modifiers
  - External content provenance modifier
  - Semantic advisory risk modifiers
  - Risk label mapping (LOW, MEDIUM, HIGH, CRITICAL)
  - Clamping to [0.0, 1.0]
"""

from __future__ import annotations

import pytest

from app.contracts.proposal import ActionProposal, ProvenanceLabel
from app.guard.intake import process_proposal
from app.guard.registry import get_registry
from app.guard.risk import RiskCalculator, _risk_label


def test_base_risk_values():
    """Verify calibrated base risk values match tools.yaml definitions."""
    calc = RiskCalculator()
    reg = get_registry()

    expected_base_risks = {
        "calendar_read": 0.1,
        "email_read": 0.2,
        "web_fetch": 0.3,
        "payment_transfer": 0.3,
        "file_delete": 0.45,
        "email_send": 0.5,
    }

    for tool_id, expected_risk in expected_base_risks.items():
        assert reg.get(tool_id).base_risk == expected_risk
        canonical = process_proposal(
            ActionProposal(
                agent_id="demo-agent",
                tool=tool_id,
                arguments={"url": "https://example.com"} if tool_id == "web_fetch"
                else {"path": "/tmp/a"} if tool_id == "file_delete"
                else {"amount": 50.0, "recipient": "bob"} if tool_id == "payment_transfer"
                else {"to": "bob@example.com", "subject": "a", "body": "b"} if tool_id == "email_send"
                else {},
            )
        )
        res = calc.calculate(canonical)
        # For payment 50.0, no modifier is added, so score == base_risk
        assert res.score == expected_risk


def test_payment_amount_modifiers():
    """Verify payment amount increases risk score proportionally."""
    calc = RiskCalculator()

    # Base: 0.30
    p_small = process_proposal(
        ActionProposal(
            agent_id="demo-agent",
            tool="payment_transfer",
            arguments={"amount": 500.0, "recipient": "bob"},
        )
    )
    assert calc.calculate(p_small).score == pytest.approx(0.30)

    # > 1000 adds +0.05 -> 0.35
    p_1500 = process_proposal(
        ActionProposal(
            agent_id="demo-agent",
            tool="payment_transfer",
            arguments={"amount": 1500.0, "recipient": "bob"},
        )
    )
    assert calc.calculate(p_1500).score == pytest.approx(0.35)

    # > 5000 adds +0.15 -> 0.45
    p_10000 = process_proposal(
        ActionProposal(
            agent_id="demo-agent",
            tool="payment_transfer",
            arguments={"amount": 10000.0, "recipient": "bob"},
        )
    )
    assert calc.calculate(p_10000).score == pytest.approx(0.45)

    # > 25000 adds +0.30 -> 0.60
    p_50000 = process_proposal(
        ActionProposal(
            agent_id="demo-agent",
            tool="payment_transfer",
            arguments={"amount": 50000.0, "recipient": "bob"},
        )
    )
    assert calc.calculate(p_50000).score == pytest.approx(0.60)


def test_external_provenance_risk_modifier():
    """Arguments with external provenance add +0.15 to risk score."""
    calc = RiskCalculator()
    p = process_proposal(
        ActionProposal(
            agent_id="demo-agent",
            tool="email_send",
            arguments={"to": "bob@example.com", "subject": "Hi", "body": "Hello"},
            arg_provenance={"body": ProvenanceLabel.EXTERNAL},
        )
    )
    # email_send base 0.50 + external 0.15 = 0.65
    res = calc.calculate(p)
    assert res.score == pytest.approx(0.65)
    assert any("external_content_provenance" in f for f in res.factors)


def test_semantic_advisory_risk_modifier():
    """Semantic outcome adds advisory risk modifier (SUSPICIOUS: +0.10, UNSAFE: +0.15)."""
    calc = RiskCalculator()
    p = process_proposal(
        ActionProposal(
            agent_id="demo-agent",
            tool="calendar_read",
            arguments={},
        )
    )
    # calendar_read base 0.10 + SUSPICIOUS 0.10 = 0.20
    res_suspicious = calc.calculate(p, semantic_outcome="SUSPICIOUS")
    assert res_suspicious.score == pytest.approx(0.20)

    # calendar_read base 0.10 + UNSAFE 0.15 = 0.25
    res_unsafe = calc.calculate(p, semantic_outcome="UNSAFE")
    assert res_unsafe.score == pytest.approx(0.25)


def test_risk_labels_and_clamping():
    """Risk labels map correctly across the score spectrum and clamp at 1.0."""
    assert _risk_label(0.1) == "LOW"
    assert _risk_label(0.39) == "LOW"
    assert _risk_label(0.40) == "MEDIUM"
    assert _risk_label(0.64) == "MEDIUM"
    assert _risk_label(0.65) == "HIGH"
    assert _risk_label(0.84) == "HIGH"
    assert _risk_label(0.85) == "CRITICAL"
    assert _risk_label(1.0) == "CRITICAL"
