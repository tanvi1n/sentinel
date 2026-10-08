"""
guard/intake.py
===============
Intake — validate, canonicalize, and integrity-hash an ActionProposal.

Responsibilities:
  1. Validate the proposal structure (already done by Pydantic on proposal.py)
  2. Validate tool identity (must be in registry)
  3. Validate argument names (no unknown args)
  4. Validate argument types and constraints
  5. Reject oversized values
  6. Reject path traversal attempts
  7. Reject missing required arguments
  8. Produce a CanonicalAction with a stable representation
  9. Compute and embed a SHA-256 integrity hash

The integrity hash is computed from:
  canonical tool + arguments (sorted) + agent_id + arg_provenance

This hash is stored in the decision and verified at execution time.
Any tampering will cause hash mismatch → execution refused.

Owned by: Person 2
"""

from __future__ import annotations

import hashlib
import json
import re

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
from app.guard.registry import ToolDefinition, ToolRegistry, get_registry

# Maximum length for string arguments that don't have their own max_length
_DEFAULT_MAX_STRING_LENGTH = 4096

# Path traversal patterns to reject
_PATH_TRAVERSAL_PATTERNS = re.compile(r"\.\.[/\\]|[/\\]\.\.")


def _check_path_traversal(value: str, arg_name: str) -> None:
    """Raise PathTraversalError if value contains path traversal sequences."""
    if _PATH_TRAVERSAL_PATTERNS.search(value):
        raise PathTraversalError(
            f"Argument '{arg_name}' contains a path traversal pattern."
        )


def _validate_argument(
    arg_name: str,
    raw_value: object,
    spec,  # ArgumentSpec
) -> object:
    """
    Validate a single argument value against its spec.

    Returns the (possibly coerced) value.
    Raises InvalidArgument, OversizedValue, or PathTraversalError on failure.
    """
    arg_type = spec.type

    # --- Type coercion and validation --------------------------------------
    if arg_type == "string":
        if not isinstance(raw_value, str):
            # Try coercion for simple numeric → string if the spec is string
            raw_value = str(raw_value)
        value: object = raw_value
        max_len = spec.max_length or _DEFAULT_MAX_STRING_LENGTH
        if len(value) > max_len:  # type: ignore[arg-type]
            raise OversizedValue(
                f"Argument '{arg_name}' exceeds maximum length of {max_len}."
            )
        _check_path_traversal(str(value), arg_name)

    elif arg_type == "integer":
        if not isinstance(raw_value, (int, float)):
            raise InvalidArgument(
                f"Argument '{arg_name}' must be an integer, got {type(raw_value).__name__}."
            )
        value = int(raw_value)
        if spec.min_value is not None and value < spec.min_value:
            raise InvalidArgument(
                f"Argument '{arg_name}' is below minimum value {spec.min_value}."
            )
        if spec.max_value is not None and value > spec.max_value:
            raise InvalidArgument(
                f"Argument '{arg_name}' exceeds maximum value {spec.max_value}."
            )

    elif arg_type == "number":
        if not isinstance(raw_value, (int, float)):
            raise InvalidArgument(
                f"Argument '{arg_name}' must be a number, got {type(raw_value).__name__}."
            )
        value = float(raw_value)
        if spec.min_value is not None and value < spec.min_value:
            raise InvalidArgument(
                f"Argument '{arg_name}' is below minimum value {spec.min_value}."
            )
        if spec.max_value is not None and value > spec.max_value:
            raise InvalidArgument(
                f"Argument '{arg_name}' exceeds maximum value {spec.max_value}."
            )

    elif arg_type == "boolean":
        if not isinstance(raw_value, bool):
            raise InvalidArgument(
                f"Argument '{arg_name}' must be a boolean, got {type(raw_value).__name__}."
            )
        value = bool(raw_value)

    else:
        # Unknown type in registry — treat as string
        value = raw_value

    return value


def _compute_integrity_hash(
    agent_id: str,
    tool_id: str,
    arguments: dict,
    arg_provenance: dict,
) -> str:
    """
    Compute a deterministic SHA-256 hash of the canonical action.

    The hash input is a JSON-serialized object with sorted keys.
    This makes it stable and tamper-detectable.
    """
    payload = {
        "agent_id": agent_id,
        "tool": tool_id,
        "arguments": dict(sorted(arguments.items())),
        "arg_provenance": dict(sorted(arg_provenance.items())),
    }
    canonical_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical_bytes).hexdigest()


def process_proposal(
    proposal: ActionProposal,
    registry: ToolRegistry | None = None,
) -> CanonicalAction:
    """
    Validate and canonicalize an ActionProposal.

    Returns a CanonicalAction with an embedded integrity hash.

    Raises:
      InvalidProposal    : proposal structure is invalid
      UnknownTool        : tool not in registry
      UnknownArgument    : proposal contains an argument not declared for the tool
      InvalidArgument    : argument has wrong type or violates a constraint
      OversizedValue     : argument value is too large
      PathTraversalError : argument contains path traversal
    """
    if registry is None:
        registry = get_registry()

    # --- 1. Validate tool identity -----------------------------------------
    if not registry.exists(proposal.tool):
        raise UnknownTool(f"Tool '{proposal.tool}' is not registered.")

    tool_def: ToolDefinition = registry.get(proposal.tool)

    # --- 2. Validate agent_id not empty (already enforced by Pydantic) -----
    if not proposal.agent_id.strip():
        raise InvalidProposal("agent_id must not be blank.")

    # --- 3. Check for unknown arguments ------------------------------------
    known_args = tool_def.known_argument_names
    provided_args = set(proposal.arguments.keys())
    unknown = provided_args - known_args
    if unknown:
        raise UnknownArgument(
            f"Unknown argument(s) for tool '{proposal.tool}': {sorted(unknown)}. "
            f"Allowed arguments: {sorted(known_args)}."
        )

    # --- 4. Check for missing required arguments ---------------------------
    required_args = tool_def.required_argument_names
    missing = required_args - provided_args
    if missing:
        raise InvalidArgument(
            f"Missing required argument(s) for tool '{proposal.tool}': {sorted(missing)}."
        )

    # --- 5. Validate and normalize each argument ---------------------------
    validated_args: dict[str, object] = {}
    for arg_name, raw_value in proposal.arguments.items():
        spec = tool_def.arguments[arg_name]
        validated_args[arg_name] = _validate_argument(arg_name, raw_value, spec)

    # --- 6. Normalize arg_provenance (only keep known arg names) -----------
    normalized_provenance: dict[str, str] = {}
    for arg_name in validated_args:
        prov = proposal.arg_provenance.get(arg_name, ProvenanceLabel.AGENT_INTERNAL)
        normalized_provenance[arg_name] = prov

    # --- 7. Compute integrity hash -----------------------------------------
    integrity_hash = _compute_integrity_hash(
        agent_id=proposal.agent_id,
        tool_id=proposal.tool,
        arguments=validated_args,
        arg_provenance=normalized_provenance,
    )

    return CanonicalAction(
        agent_id=proposal.agent_id,
        tool=proposal.tool,
        arguments=validated_args,
        arg_provenance=normalized_provenance,
        integrity_hash=integrity_hash,
    )


def verify_integrity(canonical: CanonicalAction) -> bool:
    """
    Verify that a stored CanonicalAction's integrity hash is still valid.

    Returns True if the hash matches; False if the action was tampered with.
    """
    expected_hash = _compute_integrity_hash(
        agent_id=canonical.agent_id,
        tool_id=canonical.tool,
        arguments=canonical.arguments,
        arg_provenance=canonical.arg_provenance,
    )
    return canonical.integrity_hash == expected_hash
