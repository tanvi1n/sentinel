"""
guard/engine.py
===============
GuardEngine — orchestrates the full evaluation pipeline.

Pipeline:
  ActionProposal
    → intake.process_proposal()   [validate + canonicalize + hash]
    → rules.evaluate()            [deterministic rules]
    → risk.calculate()            [risk score]
    → combine()                   [merge deterministic + semantic]
    → GuardDecision               [final verdict]

The engine does NOT:
  - Store decisions (that's the service's job)
  - Execute tools (that's the gateway's job)
  - Call the semantic layer directly (the service provides the result)

The engine takes an optional SemanticResult. If provided, combine() uses
it. The engine works correctly without any semantic result (MockReasoner
provides deterministic results for development).

Owned by: Person 2
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.contracts.decision import (
    CanonicalAction,
    DecisionLifecycle,
    DecisionOutcome,
    GuardDecision,
)
from app.contracts.proposal import ActionProposal
from app.contracts.semantic import SemanticResult
from app.guard.combine import combine
from app.guard.intake import process_proposal
from app.guard.registry import ToolRegistry, get_registry
from app.guard.risk import RiskCalculator, get_risk_calculator
from app.guard.rules import PolicyConfig, RulesEngine, SessionContext, get_rules_engine
from app.guard.store import DecisionStore, get_decision_store


class GuardEngine:
    """
    Orchestrates the guardrail evaluation pipeline.

    Usage:
        engine = GuardEngine()
        decision = engine.evaluate(proposal, semantic_result=mock_result)
    """

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        policy: PolicyConfig | None = None,
        rules_engine: RulesEngine | None = None,
        risk_calculator: RiskCalculator | None = None,
    ) -> None:
        self._registry = registry or get_registry()
        self._policy = policy or PolicyConfig()
        self._rules = rules_engine or get_rules_engine(self._policy, self._registry)
        self._risk = risk_calculator or get_risk_calculator(self._registry)

    def evaluate(
        self,
        proposal: ActionProposal,
        semantic_result: SemanticResult | None = None,
        session_ctx: SessionContext | None = None,
    ) -> GuardDecision:
        """
        Evaluate an ActionProposal and return a GuardDecision.

        This does NOT store the decision. Call store.save(decision) after this.

        Raises:
          InvalidProposal    : malformed proposal
          UnknownTool        : tool not registered
          UnknownArgument    : unknown argument
          InvalidArgument    : argument type/constraint violation
          OversizedValue     : argument too large
          PathTraversalError : path traversal in arguments
        """
        # --- Phase 1: Intake (validate + canonicalize + hash) --------------
        canonical: CanonicalAction = process_proposal(proposal, self._registry)

        # --- Phase 2: Deterministic rules ----------------------------------
        semantic_outcome = semantic_result.outcome.value if semantic_result else None
        rules_result = self._rules.evaluate(canonical, session_ctx, semantic_outcome)

        # --- Phase 3: Risk calculation -------------------------------------
        risk_result = self._risk.calculate(canonical, semantic_outcome)

        # --- Phase 4: Combine into final outcome ---------------------------
        final_outcome, combine_notes = combine(
            rules_result=rules_result,
            risk_result=risk_result,
            semantic_result=semantic_result,
            policy=self._policy,
        )

        # --- Phase 5: Determine lifecycle ----------------------------------
        if final_outcome == DecisionOutcome.BLOCK:
            lifecycle = DecisionLifecycle.BLOCKED
        elif final_outcome == DecisionOutcome.REVIEW:
            lifecycle = DecisionLifecycle.PENDING_REVIEW
        else:
            lifecycle = DecisionLifecycle.EVALUATED

        # --- Phase 6: Build GuardDecision ----------------------------------
        decision_id = str(uuid.uuid4())
        tool_def = self._registry.get(canonical.tool)

        decision = GuardDecision(
            decision_id=decision_id,
            outcome=final_outcome,
            lifecycle=lifecycle,
            canonical_action=canonical,
            risk_score=risk_result.score,
            risk_label=risk_result.label,
            violations=rules_result.violations,
            rules_fired=rules_result.rules_fired,
            semantic_outcome=semantic_result.outcome.value if semantic_result else None,
            semantic_reason=semantic_result.reason if semantic_result else None,
            semantic_provider=semantic_result.provider if semantic_result else None,
            is_reversible=tool_def.reversible,
            evaluated_at=datetime.now(timezone.utc),
        )

        return decision

    def revalidate(
        self,
        decision: GuardDecision,
        session_ctx: SessionContext | None = None,
    ) -> tuple[bool, str]:
        """
        Revalidate a stored decision at execution time.

        IMPORTANT: Does NOT call the semantic layer.
        Deterministic checks only.

        Returns:
          (True, "ok")             if still valid
          (False, reason_message)  if now blocked

        This handles cases like:
          - policy limit changed since approval
          - capability revoked since approval
        """
        try:
            # Re-run rules engine only (no semantic — that's the spec requirement)
            rules_result = self._rules.evaluate(
                decision.canonical_action,
                session_ctx,
                semantic_outcome=None,  # no semantic on revalidation
            )
        except Exception as exc:
            return False, f"Revalidation error: {exc}"

        if rules_result.outcome == DecisionOutcome.BLOCK:
            reasons = "; ".join(v.message for v in rules_result.violations)
            return False, f"Revalidation BLOCKED: {reasons}"

        # Also re-check risk against current policy thresholds
        from app.guard.combine import _threshold_outcome
        threshold_outcome = _threshold_outcome(decision.risk_score, self._policy)
        if threshold_outcome == DecisionOutcome.BLOCK:
            return (
                False,
                f"Revalidation BLOCKED: risk score {decision.risk_score:.3f} "
                f"now exceeds BLOCK threshold under current policy.",
            )

        return True, "ok"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_engine: GuardEngine | None = None


def get_guard_engine() -> GuardEngine:
    global _engine
    if _engine is None:
        _engine = GuardEngine()
    return _engine


def reset_guard_engine(
    registry: ToolRegistry | None = None,
    policy: PolicyConfig | None = None,
) -> GuardEngine:
    global _engine
    _engine = GuardEngine(registry=registry, policy=policy)
    return _engine
