"""
contracts/errors.py
===================
Structured exception hierarchy for Sentinel.

Never silently swallow errors. Use specific exception types so that
callers can handle them precisely.

Owned by: Person 2
"""

from __future__ import annotations


class SentinelError(Exception):
    """Base class for all Sentinel errors."""


# ---------------------------------------------------------------------------
# Intake / validation errors
# ---------------------------------------------------------------------------

class InvalidProposal(SentinelError):
    """The ActionProposal is malformed or carries forbidden fields."""


class UnknownTool(SentinelError):
    """The requested tool is not registered."""


class UnknownArgument(SentinelError):
    """The proposal contains an argument not declared for this tool."""


class InvalidArgument(SentinelError):
    """An argument value has the wrong type or violates a constraint."""


class OversizedValue(SentinelError):
    """An argument value exceeds the allowed maximum size."""


class PathTraversalError(SentinelError):
    """An argument contains a path traversal pattern (e.g. ../)."""


# ---------------------------------------------------------------------------
# Authorization / policy errors
# ---------------------------------------------------------------------------

class UnauthorizedAction(SentinelError):
    """The agent does not have the required capability for this tool."""


# ---------------------------------------------------------------------------
# Decision lifecycle errors
# ---------------------------------------------------------------------------

class DecisionNotFound(SentinelError):
    """No stored decision matches the given decision_id."""


class InvalidLifecycleTransition(SentinelError):
    """The requested lifecycle state transition is not valid."""


class BlockedDecision(SentinelError):
    """Attempted to approve or execute a BLOCKED decision."""


class RejectedDecision(SentinelError):
    """Attempted to execute a REJECTED decision."""


class AlreadyExecuted(SentinelError):
    """Attempted to execute a decision that has already been executed."""


class ReviewRequired(SentinelError):
    """Attempted to execute a REVIEW decision without prior approval."""


# ---------------------------------------------------------------------------
# Execution errors
# ---------------------------------------------------------------------------

class IntegrityError(SentinelError):
    """The stored action has been tampered with (integrity hash mismatch)."""


class RevalidationFailed(SentinelError):
    """Execution-time revalidation detected the action is no longer safe."""


class ToolExecutionError(SentinelError):
    """An error occurred during tool execution."""


class UndoUnavailable(SentinelError):
    """The tool or action does not support undo."""


class AlreadyUndone(SentinelError):
    """Attempted to undo an action that has already been undone."""
