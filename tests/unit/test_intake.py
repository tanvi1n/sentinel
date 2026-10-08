"""
tests/unit/test_intake.py
=========================
Unit tests for intake.py — validation, canonicalization, integrity hashing.

Covers spec items:
  J. malformed proposal
  K. unknown tool
  L. unknown argument
  M. wrong type
  N. oversized value
  O. path traversal
  (missing required args)
  (integrity hash correctness)
"""

from __future__ import annotations

import pytest

from app.contracts.decision import CanonicalAction
from app.contracts.errors import (
    InvalidArgument,
    InvalidProposal,
    OversizedValue,
    PathTraversalError,
    UnknownArgument,
    UnknownTool,
)
from app.contracts.proposal import ActionProposal, ProvenanceLabel
from app.guard.intake import _compute_integrity_hash, process_proposal, verify_integrity
from app.guard.registry import get_registry


# ---------------------------------------------------------------------------
# K. Unknown tool
# ---------------------------------------------------------------------------


def test_unknown_tool_rejected():
    """Unknown tool must raise UnknownTool."""
    p = ActionProposal(agent_id="demo-agent", tool="nonexistent_tool", arguments={})
    with pytest.raises(UnknownTool, match="not registered"):
        process_proposal(p)


def test_known_tool_accepted():
    """All six registered tools must be accepted."""
    registry = get_registry()
    for tool_id in registry.tool_ids():
        # Build a minimal valid proposal for each tool
        tool_def = registry.get(tool_id)
        required = {
            name: _default_value(spec.type)
            for name, spec in tool_def.arguments.items()
            if spec.required
        }
        p = ActionProposal(agent_id="demo-agent", tool=tool_id, arguments=required)
        canonical = process_proposal(p)
        assert canonical.tool == tool_id


def _default_value(arg_type: str):
    defaults = {"string": "test", "integer": 1, "number": 1.0, "boolean": False}
    return defaults.get(arg_type, "test")


# ---------------------------------------------------------------------------
# L. Unknown argument
# ---------------------------------------------------------------------------


def test_unknown_argument_rejected():
    """Argument not declared in registry must raise UnknownArgument."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="calendar_read",
        arguments={"unknown_param": "value"},
    )
    with pytest.raises(UnknownArgument, match="Unknown argument"):
        process_proposal(p)


def test_multiple_unknown_arguments_rejected():
    """Multiple unknown arguments should all be reported."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="calendar_read",
        arguments={"bad1": "x", "bad2": "y"},
    )
    with pytest.raises(UnknownArgument):
        process_proposal(p)


def test_known_arguments_accepted():
    """Declared arguments pass without error."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="calendar_read",
        arguments={"start_date": "2026-10-01", "end_date": "2026-10-31"},
    )
    canonical = process_proposal(p)
    assert canonical.arguments["start_date"] == "2026-10-01"
    assert canonical.arguments["end_date"] == "2026-10-31"


# ---------------------------------------------------------------------------
# M. Wrong argument type
# ---------------------------------------------------------------------------


def test_wrong_type_string_for_number():
    """Passing a non-numeric string for a 'number' argument raises InvalidArgument."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": "not_a_number", "recipient": "bob"},
    )
    with pytest.raises(InvalidArgument, match="must be a number"):
        process_proposal(p)


def test_wrong_type_string_for_integer():
    """Passing a string for an 'integer' argument raises InvalidArgument."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="email_read",
        arguments={"limit": "ten"},
    )
    with pytest.raises(InvalidArgument, match="must be an integer"):
        process_proposal(p)


def test_wrong_type_string_for_boolean():
    """Passing a string for a 'boolean' argument raises InvalidArgument."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="file_delete",
        arguments={"path": "/home/user/file.txt", "permanent": "yes"},
    )
    with pytest.raises(InvalidArgument, match="must be a boolean"):
        process_proposal(p)


def test_number_accepts_int():
    """Integer literal is acceptable for a 'number' type argument."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 1000, "recipient": "bob"},
    )
    canonical = process_proposal(p)
    assert canonical.arguments["amount"] == 1000.0
    assert isinstance(canonical.arguments["amount"], float)


# ---------------------------------------------------------------------------
# N. Oversized value
# ---------------------------------------------------------------------------


def test_oversized_string_rejected():
    """String value exceeding max_length raises OversizedValue."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="calendar_read",
        # start_date has max_length=32
        arguments={"start_date": "x" * 100},
    )
    with pytest.raises(OversizedValue, match="exceeds maximum length"):
        process_proposal(p)


def test_max_length_boundary_accepted():
    """String at exactly max_length boundary is accepted."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="calendar_read",
        arguments={"start_date": "2" * 32},  # exactly 32 chars
    )
    canonical = process_proposal(p)
    assert len(canonical.arguments["start_date"]) == 32


def test_max_length_boundary_plus_one_rejected():
    """String one character over max_length is rejected."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="calendar_read",
        arguments={"start_date": "2" * 33},  # 33 chars, max is 32
    )
    with pytest.raises(OversizedValue):
        process_proposal(p)


# ---------------------------------------------------------------------------
# O. Path traversal
# ---------------------------------------------------------------------------


def test_path_traversal_dot_dot_slash():
    """../  in path argument must raise PathTraversalError."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="file_delete",
        arguments={"path": "../etc/passwd"},
    )
    with pytest.raises(PathTraversalError, match="path traversal"):
        process_proposal(p)


def test_path_traversal_slash_dot_dot():
    """/..\\ in path argument must raise PathTraversalError."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="file_delete",
        arguments={"path": "/home/user/../../etc/passwd"},
    )
    with pytest.raises(PathTraversalError):
        process_proposal(p)


def test_path_traversal_in_non_path_arg():
    """Path traversal in any string argument must be rejected."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="web_fetch",
        arguments={"url": "https://example.com/../etc/shadow"},
    )
    with pytest.raises(PathTraversalError):
        process_proposal(p)


def test_clean_path_accepted():
    """Absolute path without traversal is accepted."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="file_delete",
        arguments={"path": "/home/user/documents/report.pdf"},
    )
    canonical = process_proposal(p)
    assert canonical.arguments["path"] == "/home/user/documents/report.pdf"


# ---------------------------------------------------------------------------
# Missing required arguments
# ---------------------------------------------------------------------------


def test_missing_required_argument():
    """Missing required 'recipient' for payment_transfer raises InvalidArgument."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 1000},  # missing 'recipient'
    )
    with pytest.raises(InvalidArgument, match="Missing required"):
        process_proposal(p)


def test_missing_required_url_for_web_fetch():
    """Missing required 'url' for web_fetch raises InvalidArgument."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="web_fetch",
        arguments={},
    )
    with pytest.raises(InvalidArgument, match="Missing required"):
        process_proposal(p)


def test_all_required_args_present():
    """All required args present → accepted."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 100.0, "recipient": "alice"},
    )
    canonical = process_proposal(p)
    assert canonical.tool == "payment_transfer"


# ---------------------------------------------------------------------------
# Canonicalization and integrity hash
# ---------------------------------------------------------------------------


def test_canonical_action_produced():
    """process_proposal() returns a CanonicalAction with all expected fields."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="calendar_read",
        arguments={"start_date": "2026-10-01"},
    )
    canonical = process_proposal(p)
    assert isinstance(canonical, CanonicalAction)
    assert canonical.tool == "calendar_read"
    assert canonical.agent_id == "demo-agent"
    assert canonical.arguments == {"start_date": "2026-10-01"}
    assert len(canonical.integrity_hash) == 64  # SHA-256 hex


def test_integrity_hash_is_stable():
    """Same input produces identical hash on repeated calls."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 100.0, "recipient": "bob"},
    )
    c1 = process_proposal(p)
    c2 = process_proposal(p)
    assert c1.integrity_hash == c2.integrity_hash


def test_integrity_hash_changes_on_tool_change():
    """Changing tool produces a different hash."""
    h1 = _compute_integrity_hash("a", "calendar_read", {}, {})
    h2 = _compute_integrity_hash("a", "email_read", {}, {})
    assert h1 != h2


def test_integrity_hash_changes_on_amount_change():
    """Changing amount produces a different hash."""
    h1 = _compute_integrity_hash("a", "payment_transfer", {"amount": 100.0, "recipient": "bob"}, {})
    h2 = _compute_integrity_hash("a", "payment_transfer", {"amount": 100001.0, "recipient": "bob"}, {})
    assert h1 != h2


def test_verify_integrity_passes_for_valid_canonical():
    """verify_integrity() returns True for an untampered canonical action."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 1000.0, "recipient": "alice"},
    )
    canonical = process_proposal(p)
    assert verify_integrity(canonical) is True


def test_verify_integrity_fails_after_tampering():
    """verify_integrity() returns False when arguments are modified post-hashing."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 1000.0, "recipient": "alice"},
    )
    canonical = process_proposal(p)
    # Tamper: change amount
    tampered = canonical.model_copy(
        update={"arguments": {"amount": 999999.0, "recipient": "alice"}}
    )
    assert verify_integrity(tampered) is False


def test_verify_integrity_fails_after_tool_change():
    """verify_integrity() fails if tool is changed."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="calendar_read",
        arguments={},
    )
    canonical = process_proposal(p)
    tampered = canonical.model_copy(update={"tool": "payment_transfer"})
    assert verify_integrity(tampered) is False


def test_verify_integrity_fails_after_provenance_change():
    """verify_integrity() fails if provenance is tampered with."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 100.0, "recipient": "bob"},
    )
    canonical = process_proposal(p)
    # Tamper provenance
    tampered = canonical.model_copy(
        update={"arg_provenance": {"amount": "external_content", "recipient": "agent_internal"}}
    )
    assert verify_integrity(tampered) is False


# ---------------------------------------------------------------------------
# Provenance normalization
# ---------------------------------------------------------------------------


def test_provenance_defaults_to_agent_internal():
    """Arguments without harness provenance default to agent_internal."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 100.0, "recipient": "bob"},
    )
    canonical = process_proposal(p)
    assert canonical.arg_provenance["amount"] == ProvenanceLabel.AGENT_INTERNAL
    assert canonical.arg_provenance["recipient"] == ProvenanceLabel.AGENT_INTERNAL


def test_harness_provenance_preserved():
    """Harness-supplied provenance is preserved in canonical action."""
    p = ActionProposal(
        agent_id="demo-agent",
        tool="payment_transfer",
        arguments={"amount": 100.0, "recipient": "bob"},
        arg_provenance={"recipient": ProvenanceLabel.EXTERNAL},
    )
    canonical = process_proposal(p)
    assert canonical.arg_provenance["recipient"] == ProvenanceLabel.EXTERNAL
    assert canonical.arg_provenance["amount"] == ProvenanceLabel.AGENT_INTERNAL
