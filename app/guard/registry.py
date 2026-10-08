"""
guard/registry.py
=================
ToolRegistry — loads tool definitions from config/tools.yaml and provides
lookup/validation APIs used throughout the guardrail engine.

The registry is the single source of truth for tool metadata.
The engine must NOT hardcode tool-specific behavior; all domain logic
flows from the registry.

Owned by: Person 2
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml

from app.contracts.errors import UnknownTool

# Default path relative to project root
_DEFAULT_TOOLS_YAML = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "tools.yaml"
)


# ---------------------------------------------------------------------------
# Argument spec
# ---------------------------------------------------------------------------

@dataclass
class ArgumentSpec:
    """Specification for a single tool argument."""
    name: str
    type: str                     # "string" | "integer" | "number" | "boolean"
    required: bool = False
    max_length: int | None = None
    min_value: float | None = None
    max_value: float | None = None
    description: str = ""


# ---------------------------------------------------------------------------
# ToolDefinition
# ---------------------------------------------------------------------------

@dataclass
class ToolDefinition:
    """
    Full definition of a tool loaded from tools.yaml.

    This is the authoritative metadata record used by:
      - intake.py  (argument validation)
      - rules.py   (authorization, policy)
      - risk.py    (base risk, reversibility)
      - engine.py  (orchestration)
    """
    id: str
    description: str
    capability: str
    mutating: bool
    read_only: bool
    reversible: bool
    undo_supported: bool
    high_impact: bool
    requires_semantic: bool
    base_risk: float
    arguments: dict[str, ArgumentSpec] = field(default_factory=dict)

    @property
    def known_argument_names(self) -> set[str]:
        return set(self.arguments.keys())

    @property
    def required_argument_names(self) -> set[str]:
        return {name for name, spec in self.arguments.items() if spec.required}


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """
    Loads and indexes all tool definitions from tools.yaml.

    Usage:
        registry = ToolRegistry()
        tool = registry.get("payment_transfer")   # raises UnknownTool if not found
        all_tools = registry.all_tools()
    """

    def __init__(self, tools_yaml_path: str | None = None) -> None:
        path = tools_yaml_path or _DEFAULT_TOOLS_YAML
        self._tools: dict[str, ToolDefinition] = {}
        self._load(path)

    def _load(self, path: str) -> None:
        """Parse tools.yaml and populate the internal index."""
        abs_path = os.path.abspath(path)
        with open(abs_path, "r") as f:
            data = yaml.safe_load(f)

        for entry in data.get("tools", []):
            tool_id = entry["id"]
            # Build argument specs
            arg_specs: dict[str, ArgumentSpec] = {}
            for arg_name, arg_data in (entry.get("arguments") or {}).items():
                arg_specs[arg_name] = ArgumentSpec(
                    name=arg_name,
                    type=arg_data.get("type", "string"),
                    required=arg_data.get("required", False),
                    max_length=arg_data.get("max_length"),
                    min_value=arg_data.get("min_value"),
                    max_value=arg_data.get("max_value"),
                    description=arg_data.get("description", ""),
                )

            self._tools[tool_id] = ToolDefinition(
                id=tool_id,
                description=entry.get("description", ""),
                capability=entry["capability"],
                mutating=entry.get("mutating", False),
                read_only=entry.get("read_only", True),
                reversible=entry.get("reversible", True),
                undo_supported=entry.get("undo_supported", False),
                high_impact=entry.get("high_impact", False),
                requires_semantic=entry.get("requires_semantic", False),
                base_risk=float(entry.get("base_risk", 0.1)),
                arguments=arg_specs,
            )

    def get(self, tool_id: str) -> ToolDefinition:
        """Return ToolDefinition for the given tool_id, or raise UnknownTool."""
        try:
            return self._tools[tool_id]
        except KeyError:
            raise UnknownTool(f"Tool '{tool_id}' is not registered.")

    def exists(self, tool_id: str) -> bool:
        """Return True if the tool is registered."""
        return tool_id in self._tools

    def all_tools(self) -> list[ToolDefinition]:
        """Return all registered tool definitions."""
        return list(self._tools.values())

    def tool_ids(self) -> list[str]:
        """Return all registered tool IDs."""
        return list(self._tools.keys())


# ---------------------------------------------------------------------------
# Module-level singleton (loaded once at import time)
# ---------------------------------------------------------------------------

_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    """Return the module-level ToolRegistry singleton."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


def reset_registry(tools_yaml_path: str | None = None) -> ToolRegistry:
    """
    Reload the registry (for tests or custom paths).
    Returns the new singleton.
    """
    global _registry
    _registry = ToolRegistry(tools_yaml_path)
    return _registry
