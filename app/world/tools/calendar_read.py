"""
world/tools/calendar_read.py
============================
CalendarReadTool — reads calendar events from the simulated world.

Read-only. No undo needed.
"""

from __future__ import annotations

from typing import Any

from app.world.tools.base import BaseTool, ExecutionResult
from app.world.state import WorldStore


class CalendarReadTool(BaseTool):
    """Read calendar events."""

    tool_id = "calendar_read"
    undo_supported = False

    def _execute_impl(self, arguments: dict[str, Any]) -> ExecutionResult:
        state = self._world.state
        events = state.calendar_events

        # Optional date range filtering
        start_date = arguments.get("start_date")
        end_date = arguments.get("end_date")

        if start_date:
            events = [e for e in events if e.start >= start_date]
        if end_date:
            events = [e for e in events if e.end <= end_date + "T23:59:59"]

        result = [
            {
                "id": e.id,
                "title": e.title,
                "start": e.start,
                "end": e.end,
                "location": e.location,
                "description": e.description,
                "attendees": e.attendees,
            }
            for e in events
        ]

        return ExecutionResult(
            success=True,
            output=result,
            label=f"Found {len(result)} calendar event(s).",
            metadata={"event_count": len(result)},
        )
