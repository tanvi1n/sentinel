"""
contracts package — shared data contracts for SENTINEL.

Import from here for convenience:
    from app.contracts import ActionProposal, GuardDecision, ...
"""

from app.contracts.audit import AuditEntry, AuditEvent
from app.contracts.decision import (
    CanonicalAction,
    DecisionLifecycle,
    DecisionOutcome,
    GuardDecision,
    VALID_TRANSITIONS,
    Violation,
)
from app.contracts.errors import (
    AlreadyExecuted,
    AlreadyUndone,
    BlockedDecision,
    DecisionNotFound,
    IntegrityError,
    InvalidArgument,
    InvalidLifecycleTransition,
    InvalidProposal,
    OversizedValue,
    PathTraversalError,
    RevalidationFailed,
    RejectedDecision,
    ReviewRequired,
    SentinelError,
    ToolExecutionError,
    UnauthorizedAction,
    UndoUnavailable,
    UnknownArgument,
    UnknownTool,
)
from app.contracts.proposal import ActionProposal, ProposalContext, ProvenanceLabel
from app.contracts.semantic import SemanticOutcome, SemanticResult
from app.world.tools.base import ExecutionResult

__all__ = [
    # proposal
    "ActionProposal",
    "ProposalContext",
    "ProvenanceLabel",
    # decision
    "CanonicalAction",
    "DecisionLifecycle",
    "DecisionOutcome",
    "ExecutionResult",
    "GuardDecision",
    "VALID_TRANSITIONS",
    "Violation",
    # semantic
    "SemanticOutcome",
    "SemanticResult",
    # audit
    "AuditEntry",
    "AuditEvent",
    # errors
    "AlreadyExecuted",
    "AlreadyUndone",
    "BlockedDecision",
    "DecisionNotFound",
    "IntegrityError",
    "InvalidArgument",
    "InvalidLifecycleTransition",
    "InvalidProposal",
    "OversizedValue",
    "PathTraversalError",
    "RevalidationFailed",
    "RejectedDecision",
    "ReviewRequired",
    "SentinelError",
    "ToolExecutionError",
    "UnauthorizedAction",
    "UndoUnavailable",
    "UnknownArgument",
    "UnknownTool",
]
