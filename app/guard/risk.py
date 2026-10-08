"""
guard/risk.py
=============
Deterministic risk calculator.

Computes a normalized risk score in [0.0, 1.0] for a canonical action
based on tool metadata and argument characteristics.

Design: base_risk in tools.yaml already encodes the tool's inherent risk
(mutating, reversibility, high_impact). The calculator adds MODIFIERS for
dynamic factors that depend on the specific argument values:
  - payment amount (proportional)
  - external content provenance
  - semantic advisory modifier (capped)

This avoids double-counting base_risk with metadata-derived modifiers.

The final risk score feeds the policy's risk_threshold table.
Deterministic rule violations (BLOCK/REVIEW) from rules.py take precedence
over risk-score-based decisions.

Owned by: Person 2
"""

from __future__ import annotations

from dataclasses import dataclass

from app.contracts.proposal import ProvenanceLabel
from app.guard.registry import ToolDefinition, ToolRegistry, get_registry

# Risk label thresholds
_RISK_LABELS: list[tuple[float, str]] = [
    (0.85, "CRITICAL"),
    (0.65, "HIGH"),
    (0.40, "MEDIUM"),
    (0.0,  "LOW"),
]


def _risk_label(score: float) -> str:
    """Map a risk score to a human-readable label."""
    for threshold, label in _RISK_LABELS:
        if score >= threshold:
            return label
    return "LOW"


@dataclass
class RiskResult:
    """Result of the risk calculation."""
    score: float       # normalized [0.0, 1.0]
    label: str         # CRITICAL | HIGH | MEDIUM | LOW
    factors: list[str] # list of factors that contributed to this score


class RiskCalculator:
    """
    Computes deterministic risk scores.

    Starts from the tool's calibrated base_risk, then adds dynamic modifiers
    based on the specific argument values in this proposal.
    """

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self._registry = registry or get_registry()

    def calculate(
        self,
        canonical: object,              # CanonicalAction
        semantic_outcome: str | None = None,
    ) -> RiskResult:
        """
        Calculate the risk score for a canonical action.

        Parameters:
          canonical       : CanonicalAction from intake.py
          semantic_outcome: optional semantic outcome for risk modifiers

        Returns a RiskResult with score, label, and contributing factors.
        """
        tool_def: ToolDefinition = self._registry.get(canonical.tool)  # type: ignore[attr-defined]
        args: dict = canonical.arguments  # type: ignore[attr-defined]
        arg_prov: dict = canonical.arg_provenance  # type: ignore[attr-defined]

        score = tool_def.base_risk
        factors: list[str] = [f"base_risk({tool_def.id})={tool_def.base_risk:.2f}"]

        # --- Payment amount modifiers (proportional to configured limits) -
        if tool_def.id == "payment_transfer":
            amount = float(args.get("amount", 0))
            if amount > 25000:
                delta = 0.30
                score = min(1.0, score + delta)
                factors.append(f"+{delta:.2f} payment_very_large(>{25000})")
            elif amount > 5000:
                delta = 0.15
                score = min(1.0, score + delta)
                factors.append(f"+{delta:.2f} payment_medium(>{5000})")
            elif amount > 1000:
                delta = 0.05
                score = min(1.0, score + delta)
                factors.append(f"+{delta:.2f} payment_small(>{1000})")

        # --- External content provenance modifier -------------------------
        has_external_prov = any(
            p == ProvenanceLabel.EXTERNAL for p in arg_prov.values()
        )
        if has_external_prov:
            delta = 0.15
            score = min(1.0, score + delta)
            factors.append(f"+{delta:.2f} external_content_provenance")

        # --- Semantic risk modifier (advisory, capped at +0.15) -----------
        if semantic_outcome == "SUSPICIOUS":
            delta = 0.10
            score = min(1.0, score + delta)
            factors.append(f"+{delta:.2f} semantic_suspicious")
        elif semantic_outcome == "UNSAFE":
            delta = 0.15
            score = min(1.0, score + delta)
            factors.append(f"+{delta:.2f} semantic_unsafe")

        # Clamp to [0, 1]
        score = max(0.0, min(1.0, round(score, 4)))
        label = _risk_label(score)

        return RiskResult(score=score, label=label, factors=factors)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_calculator: RiskCalculator | None = None


def get_risk_calculator(registry: ToolRegistry | None = None) -> RiskCalculator:
    global _calculator
    if _calculator is None:
        _calculator = RiskCalculator(registry)
    return _calculator


def reset_risk_calculator(registry: ToolRegistry | None = None) -> RiskCalculator:
    global _calculator
    _calculator = RiskCalculator(registry)
    return _calculator
