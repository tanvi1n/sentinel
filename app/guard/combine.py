"""
guard/combine.py
================
combine() — merges deterministic and semantic findings into a final
GuardDecision outcome.

CRITICAL INVARIANT:
    Deterministic BLOCK can NEVER become REVIEW or APPROVE.

Semantic findings are ADVISORY:
  - SUSPICIOUS / UNSAFE  → can escalate APPROVE → REVIEW
  - SAFE                 → no effect
  - INVALID/TIMEOUT/UNAVAILABLE → SEMANTIC_REQUIRED_UNAVAILABLE rule fires
                                   in rules.py (so handled before combine)
  - Semantic can NEVER lower a BLOCK
  - Semantic can NEVER lower a REVIEW to APPROVE

Risk threshold override:
  - If deterministic outcome is APPROVE but risk_score crosses a threshold,
    the threshold rule overrides to REVIEW.

The combine() function is the single place where all guardrail signals
converge into a final outcome. Keep it narrow and explicit.

Owned by: Person 2
"""

from __future__ import annotations

from app.contracts.decision import DecisionOutcome
from app.contracts.semantic import SemanticOutcome, SemanticResult
from app.guard.risk import RiskResult
from app.guard.rules import PolicyConfig, RulesResult

# Outcomes that semantic can escalate FROM (cannot go below APPROVE)
_ESCALATABLE_FROM = {DecisionOutcome.APPROVE}

# Semantic outcomes that trigger escalation
_ESCALATING_SEMANTIC_OUTCOMES = {
    SemanticOutcome.SUSPICIOUS,
    SemanticOutcome.UNSAFE,
}

# Risk outcomes from the policy threshold table
_OUTCOME_PRIORITY: dict[DecisionOutcome, int] = {
    DecisionOutcome.BLOCK:   3,
    DecisionOutcome.REVIEW:  2,
    DecisionOutcome.APPROVE: 1,
}


def _threshold_outcome(
    risk_score: float,
    policy: PolicyConfig,
) -> DecisionOutcome:
    """
    Map a risk score to an outcome using the policy's risk threshold table.
    Returns the first matching outcome.
    """
    for entry in policy.get_risk_thresholds():
        if risk_score >= entry["threshold"]:
            return DecisionOutcome(entry["outcome"])
    return DecisionOutcome.APPROVE


def combine(
    rules_result: RulesResult,
    risk_result: RiskResult,
    semantic_result: SemanticResult | None,
    policy: PolicyConfig | None = None,
) -> tuple[DecisionOutcome, list[str]]:
    """
    Combine deterministic rules, risk, and semantic results into a final outcome.

    Parameters:
      rules_result    : output of RulesEngine.evaluate()
      risk_result     : output of RiskCalculator.calculate()
      semantic_result : output of semantic provider (may be None)
      policy          : PolicyConfig (for risk thresholds and escalation config)

    Returns:
      (final_outcome, reasoning_notes)

    INVARIANT: if rules_result.outcome == BLOCK, returns BLOCK — always.
    """
    if policy is None:
        policy = PolicyConfig()

    notes: list[str] = []
    deterministic_outcome = rules_result.outcome

    # --- Step 1: Start from the deterministic outcome --------------------
    outcome = deterministic_outcome
    notes.append(f"deterministic_rules → {outcome.value}")

    # --- Step 2: BLOCK is terminal — no further logic --------------------
    if outcome == DecisionOutcome.BLOCK:
        notes.append("BLOCK is terminal — semantic and risk cannot override")
        return DecisionOutcome.BLOCK, notes

    # --- Step 3: Apply risk threshold ------------------------------------
    threshold_outcome = _threshold_outcome(risk_result.score, policy)
    if _OUTCOME_PRIORITY[threshold_outcome] > _OUTCOME_PRIORITY[outcome]:
        outcome = threshold_outcome
        notes.append(
            f"risk_threshold({risk_result.score:.3f}) → {threshold_outcome.value}"
        )

    # BLOCK from risk is terminal too
    if outcome == DecisionOutcome.BLOCK:
        notes.append("BLOCK from risk threshold is terminal")
        return DecisionOutcome.BLOCK, notes

    # --- Step 4: Apply semantic escalation (advisory only) ----------------
    if semantic_result is not None:
        sem_outcome = semantic_result.outcome
        escalation_cfg = policy.get_semantic_escalation().get(sem_outcome.value, {})
        can_escalate = escalation_cfg.get("can_escalate_approve_to_review", False)

        if can_escalate and outcome == DecisionOutcome.APPROVE:
            outcome = DecisionOutcome.REVIEW
            notes.append(
                f"semantic({sem_outcome.value}) escalated APPROVE → REVIEW"
            )
        else:
            notes.append(
                f"semantic({sem_outcome.value}) — no escalation applied "
                f"(outcome already {outcome.value})"
            )
    else:
        notes.append("no semantic result — no escalation")

    # --- Final invariant check (paranoia) ---------------------------------
    # Semantic must never lower the deterministic outcome
    if _OUTCOME_PRIORITY[outcome] < _OUTCOME_PRIORITY[deterministic_outcome]:
        # This should never happen given the logic above, but be explicit
        outcome = deterministic_outcome
        notes.append("INVARIANT ENFORCED: semantic tried to lower deterministic outcome")

    return outcome, notes
