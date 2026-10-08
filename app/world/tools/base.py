"""
world/tools/base.py
===================
BaseTool — abstract base class for all SENTINEL demo tools.

Every tool must:
  1. Implement execute() — operate on world state, return result
  2. Implement undo() if undo_supported — restore prior state exactly
  3. Declare tool_id matching the registry

The agent NEVER calls tools directly.
Tools are invoked exclusively through the trusted gateway.

Owned by: Person 2
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.contracts.errors import UndoUnavailable
from app.world.state import WorldStore


class ExecutionResult:
    """
    The structured result of a tool execution.

    Attributes:
      success    : whether the tool executed without error
      output     : tool-specific output data (shown to the agent/user)
      label      : a brief human-readable label for the result
      metadata   : additional structured data (for demos/logging)
    """

    def __init__(
        self,
        success: bool,
        output: Any = None,
        label: str = "",
        metadata: dict | None = None,
    ) -> None:
        self.success = success
        self.output = output
        self.label = label
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "output": self.output,
            "label": self.label,
            "metadata": self.metadata,
        }


class BaseTool(ABC):
    """
    Abstract base class for all SENTINEL tools.

    Subclasses must:
      - Set tool_id to match the registry entry
      - Implement _execute_impl() to perform the action
      - Implement _undo_impl() if undo_supported is True
    """

    tool_id: str = ""          # must match registry
    undo_supported: bool = False

    def __init__(self, world_store: WorldStore) -> None:
        self._world = world_store

    def execute(self, arguments: dict[str, Any]) -> ExecutionResult:
        """
        Execute the tool with the given (validated) arguments.

        Arguments have already been validated and canonicalized by intake.py.
        """
        return self._execute_impl(arguments)

    def undo(self, decision_id: str, arguments: dict[str, Any]) -> ExecutionResult:
        """
        Undo the effect of a previous execution.

        Only available if undo_supported == True.
        Raises UndoUnavailable otherwise.
        """
        if not self.undo_supported:
            raise UndoUnavailable(
                f"Tool '{self.tool_id}' does not support undo."
            )
        return self._undo_impl(decision_id, arguments)

    @abstractmethod
    def _execute_impl(self, arguments: dict[str, Any]) -> ExecutionResult:
        """Subclasses implement the actual tool logic here."""

    def _undo_impl(self, decision_id: str, arguments: dict[str, Any]) -> ExecutionResult:
        """
        Subclasses override this to implement undo.
        Default raises UndoUnavailable.
        """
        raise UndoUnavailable(f"Tool '{self.tool_id}' does not support undo.")
