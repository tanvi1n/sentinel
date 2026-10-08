"""
world/executor.py
=================
ToolExecutor — maps tool_id → tool instance and executes via the world.

This is called ONLY by the trusted gateway.
The agent never calls this directly.

Responsibilities:
  - Map tool IDs to tool instances
  - Take undo snapshots before execution (for undo-supported tools)
  - Execute the tool
  - Perform undo by restoring the snapshot

Owned by: Person 2
"""

from __future__ import annotations

from app.contracts.errors import ToolExecutionError, UndoUnavailable, AlreadyUndone
from app.world.state import WorldStore, get_world_store
from app.world.tools.base import BaseTool, ExecutionResult
from app.world.tools.calendar_read import CalendarReadTool
from app.world.tools.email_read import EmailReadTool
from app.world.tools.email_send import EmailSendTool
from app.world.tools.web_fetch import WebFetchTool
from app.world.tools.file_delete import FileDeleteTool
from app.world.tools.payment_transfer import PaymentTransferTool


class ToolExecutor:
    """
    Manages tool instances and coordinates execution with the world.

    The agent never touches this class directly.
    Only the trusted gateway calls executor.execute() and executor.undo().
    """

    def __init__(self, world_store: WorldStore | None = None) -> None:
        self._world = world_store or get_world_store()
        self._tools: dict[str, BaseTool] = self._build_tool_registry()
        # Track which decisions have been undone (to prevent double-undo)
        self._undone: set[str] = set()

    def _build_tool_registry(self) -> dict[str, BaseTool]:
        """Build the map of tool_id → tool instance."""
        tools = [
            CalendarReadTool(self._world),
            EmailReadTool(self._world),
            EmailSendTool(self._world),
            WebFetchTool(self._world),
            FileDeleteTool(self._world),
            PaymentTransferTool(self._world),
        ]
        return {t.tool_id: t for t in tools}

    def get_tool(self, tool_id: str) -> BaseTool:
        """Return the tool instance for the given tool_id."""
        try:
            return self._tools[tool_id]
        except KeyError:
            raise ToolExecutionError(f"No tool implementation for '{tool_id}'.")

    def execute(
        self,
        tool_id: str,
        arguments: dict,
        decision_id: str,
    ) -> ExecutionResult:
        """
        Execute a tool.

        For undo-supported tools, takes a snapshot BEFORE execution
        so undo can restore the exact prior state.
        """
        tool = self.get_tool(tool_id)

        # Take undo snapshot before mutating the world
        if tool.undo_supported:
            self._world.take_undo_snapshot(decision_id)

        try:
            result = tool.execute(arguments)
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError(
                f"Tool '{tool_id}' raised an unexpected error: {exc}"
            ) from exc

        return result

    def undo(
        self,
        tool_id: str,
        arguments: dict,
        decision_id: str,
    ) -> ExecutionResult:
        """
        Undo a previous execution.

        Strategy:
          1. Check the tool supports undo
          2. Check the snapshot exists (means execution happened)
          3. Check this decision hasn't already been undone
          4. Restore the world snapshot
          5. Call tool._undo_impl() to verify and confirm the restoration
        """
        tool = self.get_tool(tool_id)

        if not tool.undo_supported:
            raise UndoUnavailable(f"Tool '{tool_id}' does not support undo.")

        if decision_id in self._undone:
            raise AlreadyUndone(
                f"Decision '{decision_id}' has already been undone."
            )

        if not self._world.has_undo_snapshot(decision_id):
            raise UndoUnavailable(
                f"No undo snapshot found for decision '{decision_id}'. "
                "Either the tool was never executed or the snapshot was lost."
            )

        # Restore world to pre-execution state
        self._world.restore_snapshot(decision_id)

        # Mark as undone
        self._undone.add(decision_id)

        # Call the tool's undo_impl to verify restoration and build result
        return tool.undo(decision_id, arguments)

    def supports_undo(self, tool_id: str) -> bool:
        """Return True if the tool supports undo."""
        try:
            return self.get_tool(tool_id).undo_supported
        except ToolExecutionError:
            return False


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_executor: ToolExecutor | None = None


def get_tool_executor() -> ToolExecutor:
    global _executor
    if _executor is None:
        _executor = ToolExecutor()
    return _executor


def reset_tool_executor(world_store: WorldStore | None = None) -> ToolExecutor:
    global _executor
    _executor = ToolExecutor(world_store)
    return _executor
