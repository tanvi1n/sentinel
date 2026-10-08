"""
world/tools/file_delete.py
==========================
FileDeleteTool — deletes a file from the simulated filesystem.

Mutating. Supports undo (restores exact file content).
This tool is used in Scenario 3 (bulk delete + undo).
"""

from __future__ import annotations

from typing import Any

from app.contracts.errors import ToolExecutionError, UndoUnavailable, AlreadyUndone
from app.world.state import FileEntry
from app.world.tools.base import BaseTool, ExecutionResult


class FileDeleteTool(BaseTool):
    """Delete a file from the simulated filesystem."""

    tool_id = "file_delete"
    undo_supported = True  # undo restores the exact file

    def _execute_impl(self, arguments: dict[str, Any]) -> ExecutionResult:
        state = self._world.state
        path = arguments.get("path", "")
        permanent = arguments.get("permanent", False)

        if path not in state.files:
            raise ToolExecutionError(f"File not found: '{path}'")

        deleted_file = state.files.pop(path)

        return ExecutionResult(
            success=True,
            output={
                "path": path,
                "permanent": permanent,
                "deleted": True,
            },
            label=f"File deleted: {path}",
            metadata={
                "path": path,
                "file_size": deleted_file.size_bytes,
                "permanent": permanent,
            },
        )

    def _undo_impl(self, decision_id: str, arguments: dict[str, Any]) -> ExecutionResult:
        """
        Undo a file deletion by restoring from the world snapshot.

        The WorldStore's snapshot mechanism stores the pre-execution state.
        The gateway calls world_store.restore_snapshot(decision_id) which
        restores the entire world. This method is called after restoration
        to confirm the file is back.
        """
        state = self._world.state
        path = arguments.get("path", "")

        # After restore_snapshot(), the file should be back.
        if path not in state.files:
            raise ToolExecutionError(
                f"Undo failed: file '{path}' was not restored. "
                "Snapshot may have been lost."
            )

        return ExecutionResult(
            success=True,
            output={
                "path": path,
                "restored": True,
            },
            label=f"File restored: {path}",
            metadata={"path": path},
        )
