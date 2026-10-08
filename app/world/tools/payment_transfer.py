"""
world/tools/payment_transfer.py
================================
PaymentTransferTool — transfers funds in the simulated payment world.

Mutating. Irreversible (no automatic undo for financial transfers).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.contracts.errors import ToolExecutionError
from app.world.tools.base import BaseTool, ExecutionResult


class PaymentTransferTool(BaseTool):
    """Transfer funds between accounts."""

    tool_id = "payment_transfer"
    undo_supported = False  # financial transfers are irreversible

    def _execute_impl(self, arguments: dict[str, Any]) -> ExecutionResult:
        state = self._world.state
        amount = float(arguments.get("amount", 0))
        recipient = arguments.get("recipient", "")
        currency = arguments.get("currency", "INR")
        note = arguments.get("note", "")

        # Use the primary account for demos
        account = state.payment_accounts.get("primary")
        if account is None:
            raise ToolExecutionError("No primary payment account found in world state.")

        if account.balance < amount:
            raise ToolExecutionError(
                f"Insufficient balance: ₹{account.balance:,.2f} available, "
                f"₹{amount:,.2f} requested."
            )

        # Execute the transfer
        account.balance -= amount
        txn_id = f"txn-{uuid.uuid4().hex[:12]}"
        txn = {
            "id": txn_id,
            "amount": amount,
            "recipient": recipient,
            "currency": currency,
            "note": note,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "balance_after": account.balance,
        }
        account.transactions.append(txn)

        return ExecutionResult(
            success=True,
            output={
                "transaction_id": txn_id,
                "amount": amount,
                "currency": currency,
                "recipient": recipient,
                "balance_after": account.balance,
            },
            label=f"Transfer of {currency} {amount:,.2f} to {recipient} completed.",
            metadata={
                "transaction_id": txn_id,
                "amount": amount,
                "currency": currency,
                "balance_after": account.balance,
            },
        )
