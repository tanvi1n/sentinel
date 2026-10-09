"""Deterministic stand-in reasoner for development, tests and honest demos.

It ignores the context entirely: it knows no tool or domain, and it never
sees any guard decision. Its output is only ever a ProviderResult, and
every rationale and error starts with "[MOCK]".
"""

from app.semantic.models import (
    Ambiguity,
    IntentAlignment,
    SemanticContext,
    SemanticFindings,
    ProviderResult,
    SemanticSource,
    SemanticStatus,
)
from app.semantic.base import SemanticReasoner

MOCK_MODES = (
    "aligned",
    "misaligned",
    "injection",
    "compromised",
    "timeout",
    "invalid",
    "unavailable",
)

# mode -> (intent_alignment, injection_suspected, rationale)
_FINDINGS = {
    "aligned": (
        IntentAlignment.ALIGNED,
        False,
        "[MOCK] Scripted result: the action fits the user task.",
    ),
    "misaligned": (
        IntentAlignment.MISALIGNED,
        False,
        "[MOCK] Scripted result: the action does not fit the user task.",
    ),
    "injection": (
        IntentAlignment.MISALIGNED,
        True,
        "[MOCK] Scripted result: observed content tries to steer the agent.",
    ),
    # A compromised checker: reports everything as fine. Used to show that the
    # decision does not depend on the model being right.
    "compromised": (
        IntentAlignment.ALIGNED,
        False,
        "[MOCK] Compromised checker (scripted): reports aligned, no injection.",
    ),
}

# mode -> (status, error)
_FAILURES = {
    "timeout": (SemanticStatus.TIMEOUT, "[MOCK] simulated timeout"),
    "invalid": (SemanticStatus.INVALID, "[MOCK] simulated invalid output"),
    "unavailable": (SemanticStatus.UNAVAILABLE, "[MOCK] simulated unavailable"),
}


class MockReasoner(SemanticReasoner):
    def __init__(self, mode: str = "aligned") -> None:
        if mode not in MOCK_MODES:
            raise ValueError(f"unknown mock mode: {mode!r}")
        self.mode = mode

    def _analyze(self, context: SemanticContext) -> ProviderResult:
        provider = f"mock:{self.mode}"
        if self.mode in _FAILURES:
            status, error = _FAILURES[self.mode]
            # Nothing produced a result, so the source is NONE (contract note).
            return ProviderResult(
                status=status,
                source=SemanticSource.NONE,
                provider=provider,
                error=error,
            )
        alignment, injection, rationale = _FINDINGS[self.mode]
        return ProviderResult(
            status=SemanticStatus.VALID,
            source=SemanticSource.MOCK,
            provider=provider,
            findings=SemanticFindings(
                intent_alignment=alignment,
                injection_suspected=injection,
                ambiguity=Ambiguity.LOW,
                rationale=rationale,
            ),
        )
