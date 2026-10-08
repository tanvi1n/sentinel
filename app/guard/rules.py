"""
guard/rules.py
==============
Deterministic rules engine.

Evaluates a CanonicalAction against the policy rules loaded from
config/policies.yaml and the tool metadata from the registry.

This engine answers security questions that must NEVER be delegated to
the LLM/semantic layer:
  - Is this tool allowed?
  - Does the agent have the required capability?
  - Is the amount above a hard limit?
  - Is the action irreversible + high-impact?
  - Are arguments from untrusted external sources?
  - Does the session flow violate a rule?
  - Does an argument contain a path traversal?

Rules produce:
  - A list of Violation objects
  - A "strongest outcome" across all violations (BLOCK > REVIEW > APPROVE)
  - A list of rule IDs that fired

BLOCK is authoritative. No semantic result can lower it.

Owned by: Person 2
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml

from app.contracts.decision import DecisionOutcome, Violation
from app.contracts.proposal import ProvenanceLabel
from app.guard.registry import ToolDefinition, ToolRegistry, get_registry

_DEFAULT_POLICIES_YAML = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "policies.yaml"
)

# Outcome priority: higher = more severe
_OUTCOME_PRIORITY: dict[str, int] = {
    DecisionOutcome.BLOCK:   3,
    DecisionOutcome.REVIEW:  2,
    DecisionOutcome.APPROVE: 1,
}


# ---------------------------------------------------------------------------
# RuleResult
# ---------------------------------------------------------------------------

@dataclass
class RulesResult:
    """Aggregate result from all deterministic rules."""
    outcome: DecisionOutcome
    violations: list[Violation] = field(default_factory=list)
    rules_fired: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# PolicyConfig — loads policies.yaml once
# ---------------------------------------------------------------------------

class PolicyConfig:
    """Wraps the loaded policies.yaml data for easy access."""

    def __init__(self, policies_yaml_path: str | None = None) -> None:
        path = policies_yaml_path or _DEFAULT_POLICIES_YAML
        with open(os.path.abspath(path)) as f:
            self._data = yaml.safe_load(f)

    def get_agent_capabilities(self, agent_id: str) -> list[str]:
        """Return the list of capabilities granted to agent_id."""
        caps = self._data.get("agent_capabilities", {})
        # Exact match first, then __default__
        return list(caps.get(agent_id, caps.get("__default__", [])))

    def get_limits(self) -> dict[str, Any]:
        return self._data.get("limits", {})

    def get_rules(self) -> list[dict]:
        return self._data.get("rules", [])

    def get_semantic_escalation(self) -> dict[str, Any]:
        return self._data.get("semantic_escalation", {})

    def get_risk_thresholds(self) -> list[dict]:
        return self._data.get("risk_thresholds", [])


# ---------------------------------------------------------------------------
# SessionContext — minimal context passed in from the service layer
# ---------------------------------------------------------------------------

@dataclass
class SessionContext:
    """
    Trusted session state passed from the service layer to the rules engine.

    This context comes from the TRUSTED server side (not the agent).
    """
    # Set of tool IDs that have been *executed* (not just approved) in this session
    executed_tools: set[str] = field(default_factory=set)
    # Whether any email_read was successfully executed in this session
    private_read_executed: bool = False


# ---------------------------------------------------------------------------
# RulesEngine
# ---------------------------------------------------------------------------

class RulesEngine:
    """
    Evaluates deterministic policy rules against a canonical action.

    Loaded from:
      - config/policies.yaml   (rules, capability grants, limits)
      - ToolRegistry            (tool metadata, argument specs)

    The engine is generic — it must NOT contain hardcoded tool names
    in its core logic. Domain-specific behavior flows from config.
    """

    def __init__(
        self,
        policy_config: PolicyConfig | None = None,
        registry: ToolRegistry | None = None,
    ) -> None:
        self._policy = policy_config or PolicyConfig()
        self._registry = registry or get_registry()

    def evaluate(
        self,
        canonical: Any,        # CanonicalAction
        session_ctx: SessionContext | None = None,
        semantic_outcome: str | None = None,
    ) -> RulesResult:
        """
        Evaluate all deterministic rules.

        Parameters:
          canonical       : CanonicalAction (from intake.py)
          session_ctx     : trusted session context
          semantic_outcome: the semantic layer's outcome (for rules that reference it)

        Returns a RulesResult with the aggregate outcome and all violations.
        """
        session_ctx = session_ctx or SessionContext()
        violations: list[Violation] = []
        rules_fired: list[str] = []
        current_outcome = DecisionOutcome.APPROVE

        tool_def = self._registry.get(canonical.tool)
        limits = self._policy.get_limits()
        agent_caps = self._policy.get_agent_capabilities(canonical.agent_id)

        # ---- Helper to record a violation --------------------------------
        def _fire(rule_id: str, message: str, severity: str, outcome: DecisionOutcome) -> None:
            violations.append(Violation(
                rule_id=rule_id,
                message=message,
                severity=severity,
            ))
            rules_fired.append(rule_id)
            nonlocal current_outcome
            if _OUTCOME_PRIORITY[outcome] > _OUTCOME_PRIORITY[current_outcome]:
                current_outcome = outcome

        # ================================================================
        # RULE: UNAUTHORIZED_CAPABILITY
        # ================================================================
        if tool_def.capability not in agent_caps:
            _fire(
                "UNAUTHORIZED_CAPABILITY",
                f"Agent '{canonical.agent_id}' does not have capability "
                f"'{tool_def.capability}' required by tool '{canonical.tool}'.",
                "CRITICAL",
                DecisionOutcome.BLOCK,
            )
            # Authorization block is terminal — no need to run further rules.
            return RulesResult(
                outcome=current_outcome,
                violations=violations,
                rules_fired=rules_fired,
            )

        # ================================================================
        # RULE: PATH_TRAVERSAL
        # ================================================================
        # (Already caught by intake, but double-check here for defense-in-depth)
        import re
        _traversal_pat = re.compile(r"\.\.[/\\]|[/\\]\.\.")
        for arg_name, arg_value in canonical.arguments.items():
            if isinstance(arg_value, str) and _traversal_pat.search(arg_value):
                _fire(
                    "PATH_TRAVERSAL",
                    f"Argument '{arg_name}' contains a path traversal pattern.",
                    "CRITICAL",
                    DecisionOutcome.BLOCK,
                )

        # ================================================================
        # RULE: PERMANENT_DELETE
        # ================================================================
        file_limits = limits.get("file_delete", {})
        if (
            tool_def.id == "file_delete"
            and canonical.arguments.get("permanent") is True
            and not file_limits.get("allow_permanent", False)
        ):
            _fire(
                "PERMANENT_DELETE",
                "Permanent (hard) file deletion is blocked by policy.",
                "CRITICAL",
                DecisionOutcome.BLOCK,
            )

        # ================================================================
        # RULE: LARGE_PAYMENT (hard block)
        # ================================================================
        payment_limits = limits.get("payment", {})
        block_threshold = payment_limits.get("block_threshold", 25000)
        review_threshold = payment_limits.get("review_threshold", 5000)

        if tool_def.id == "payment_transfer":
            amount = float(canonical.arguments.get("amount", 0))
            if amount > block_threshold:
                _fire(
                    "LARGE_PAYMENT",
                    f"Payment amount {amount} exceeds the block threshold of {block_threshold}.",
                    "CRITICAL",
                    DecisionOutcome.BLOCK,
                )
            elif amount > review_threshold:
                _fire(
                    "PAYMENT_REVIEW",
                    f"Payment amount {amount} exceeds the review threshold of {review_threshold}.",
                    "HIGH",
                    DecisionOutcome.REVIEW,
                )

        # ================================================================
        # RULE: PAYMENT_EXTERNAL_PROVENANCE
        # ================================================================
        if tool_def.id == "payment_transfer":
            for prov_arg in ("recipient", "amount"):
                if canonical.arg_provenance.get(prov_arg) == ProvenanceLabel.EXTERNAL:
                    _fire(
                        "PAYMENT_EXTERNAL_PROVENANCE",
                        f"Payment argument '{prov_arg}' originates from external "
                        f"(untrusted) content — possible prompt injection.",
                        "CRITICAL",
                        DecisionOutcome.BLOCK,
                    )
                    break

        # ================================================================
        # RULE: EXTERNAL_CONTENT_MUTATION
        # ================================================================
        if tool_def.mutating:
            for arg_name, prov in canonical.arg_provenance.items():
                if prov == ProvenanceLabel.EXTERNAL:
                    _fire(
                        "EXTERNAL_CONTENT_MUTATION",
                        f"Mutating action '{tool_def.id}': argument '{arg_name}' "
                        f"originates from external (untrusted) content.",
                        "HIGH",
                        DecisionOutcome.REVIEW,
                    )
                    break   # one violation is enough per rule

        # ================================================================
        # RULE: IRREVERSIBLE_HIGH_IMPACT
        # Only fires when there is no more specific rule for this tool.
        # payment_transfer has its own LARGE_PAYMENT / PAYMENT_REVIEW rules.
        # ================================================================
        if (
            not tool_def.reversible
            and tool_def.high_impact
            and tool_def.id != "payment_transfer"   # payment has its own rules
        ):
            _fire(
                "IRREVERSIBLE_HIGH_IMPACT",
                f"Tool '{tool_def.id}' is irreversible and high-impact; "
                f"requires human review.",
                "HIGH",
                DecisionOutcome.REVIEW,
            )

        # ================================================================
        # RULE: SEND_AFTER_PRIVATE_READ (session flow rule)
        # ================================================================
        if tool_def.id == "email_send" and session_ctx.private_read_executed:
            _fire(
                "SEND_AFTER_PRIVATE_READ",
                "Email send action follows an email read in the same session; "
                "possible data exfiltration.",
                "HIGH",
                DecisionOutcome.REVIEW,
            )

        # ================================================================
        # RULE: SEMANTIC_REQUIRED_UNAVAILABLE
        # ================================================================
        if tool_def.requires_semantic and semantic_outcome in (
            "INVALID", "TIMEOUT", "UNAVAILABLE"
        ):
            _fire(
                "SEMANTIC_REQUIRED_UNAVAILABLE",
                f"Semantic check is required for tool '{tool_def.id}' but "
                f"semantic provider returned '{semantic_outcome}'.",
                "MEDIUM",
                DecisionOutcome.REVIEW,
            )

        return RulesResult(
            outcome=current_outcome,
            violations=violations,
            rules_fired=rules_fired,
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_rules_engine: RulesEngine | None = None


def get_rules_engine(
    policy_config: PolicyConfig | None = None,
    registry: ToolRegistry | None = None,
) -> RulesEngine:
    """Return the module-level RulesEngine singleton."""
    global _rules_engine
    if _rules_engine is None:
        _rules_engine = RulesEngine(policy_config, registry)
    return _rules_engine


def reset_rules_engine(
    policy_config: PolicyConfig | None = None,
    registry: ToolRegistry | None = None,
) -> RulesEngine:
    """Reload the rules engine (for tests)."""
    global _rules_engine
    _rules_engine = RulesEngine(policy_config, registry)
    return _rules_engine
