"""Shared enums and the strict base model. Imports nothing from the app."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base for every contract: unknown fields are refused."""

    model_config = ConfigDict(extra="forbid")


_SEVERITY_ORDER = ("APPROVE", "REVIEW", "BLOCK")  # least to most severe
_NO_STR_ORDER = "Decision orders only against Decision, never against str"


class Decision(StrEnum):
    """Ordered by severity: BLOCK > REVIEW > APPROVE.

    Comparison operators use severity, not the string value, so max() and
    sorted() pick the most severe decision.
    """

    APPROVE = "APPROVE"
    REVIEW = "REVIEW"
    BLOCK = "BLOCK"

    @property
    def severity(self) -> int:
        return _SEVERITY_ORDER.index(self.value)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Decision):
            raise TypeError(_NO_STR_ORDER)
        return self.severity < other.severity

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Decision):
            raise TypeError(_NO_STR_ORDER)
        return self.severity <= other.severity

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Decision):
            raise TypeError(_NO_STR_ORDER)
        return self.severity > other.severity

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Decision):
            raise TypeError(_NO_STR_ORDER)
        return self.severity >= other.severity


class DecisionStatus(StrEnum):
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    EXECUTED = "EXECUTED"
    UNDONE = "UNDONE"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AxisStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class ProvenanceLabel(StrEnum):
    USER = "USER"
    EMAIL = "EMAIL"
    WEBSITE = "WEBSITE"
    TOOL_OUTPUT = "TOOL_OUTPUT"
    OTHER_EXTERNAL = "OTHER_EXTERNAL"


class SemanticStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    SKIPPED = "SKIPPED"


class SemanticSource(StrEnum):
    LIVE = "LIVE"
    REPLAY = "REPLAY"
    MOCK = "MOCK"
    NONE = "NONE"


class IntentAlignment(StrEnum):
    ALIGNED = "ALIGNED"
    SUSPICIOUS = "SUSPICIOUS"
    MISALIGNED = "MISALIGNED"


class Ambiguity(StrEnum):
    LOW = "LOW"
    HIGH = "HIGH"


class FindingSource(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    SEMANTIC = "SEMANTIC"


class ReviewAction(StrEnum):
    """Lower case on the wire, to avoid confusion with Decision."""

    approve = "approve"
    reject = "reject"
