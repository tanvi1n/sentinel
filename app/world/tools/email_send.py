"""
world/tools/email_send.py
=========================
EmailSendTool — sends an email in the simulated world.

Mutating. Not reversible (once sent, stays sent).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.world.state import Email
from app.world.tools.base import BaseTool, ExecutionResult


class EmailSendTool(BaseTool):
    """Send an email."""

    tool_id = "email_send"
    undo_supported = False

    def _execute_impl(self, arguments: dict[str, Any]) -> ExecutionResult:
        state = self._world.state

        to_field = arguments.get("to", "")
        subject = arguments.get("subject", "")
        body = arguments.get("body", "")
        cc_field = arguments.get("cc", "")

        to_list = [addr.strip() for addr in to_field.split(",") if addr.strip()]
        cc_list = [addr.strip() for addr in cc_field.split(",") if addr.strip()] if cc_field else []

        email = Email(
            id=f"email-sent-{uuid.uuid4().hex[:8]}",
            sender="user@example.com",
            to=to_list,
            subject=subject,
            body=body,
            folder="sent",
            cc=cc_list,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        state.sent_emails.append(email)

        return ExecutionResult(
            success=True,
            output={
                "email_id": email.id,
                "to": to_list,
                "subject": subject,
                "sent_at": email.timestamp,
            },
            label=f"Email sent to {', '.join(to_list)}.",
            metadata={
                "recipient_count": len(to_list),
                "has_cc": bool(cc_list),
            },
        )
