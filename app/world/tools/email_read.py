"""
world/tools/email_read.py
=========================
EmailReadTool — reads emails from the simulated inbox.

Read-only. Private data — used in session-scoped rules (SEND_AFTER_PRIVATE_READ).
"""

from __future__ import annotations

from typing import Any

from app.world.tools.base import BaseTool, ExecutionResult


class EmailReadTool(BaseTool):
    """Read emails from the inbox."""

    tool_id = "email_read"
    undo_supported = False

    def _execute_impl(self, arguments: dict[str, Any]) -> ExecutionResult:
        state = self._world.state
        folder = arguments.get("folder", "inbox")
        query = arguments.get("query", "").lower()
        limit = int(arguments.get("limit", 10))

        emails = [e for e in state.emails if e.folder == folder]

        # Simple text search
        if query:
            emails = [
                e for e in emails
                if query in e.subject.lower() or query in e.body.lower()
            ]

        emails = emails[:limit]

        result = [
            {
                "id": e.id,
                "from": e.sender,
                "to": e.to,
                "subject": e.subject,
                "body": e.body,
                "folder": e.folder,
                "read": e.read,
                "timestamp": e.timestamp,
            }
            for e in emails
        ]

        # Mark as read
        read_ids = {e.id for e in emails}
        for email in state.emails:
            if email.id in read_ids:
                email.read = True

        return ExecutionResult(
            success=True,
            output=result,
            label=f"Read {len(result)} email(s) from {folder}.",
            metadata={
                "email_count": len(result),
                "folder": folder,
                "contains_private_data": True,  # flag for session tracking
            },
        )
