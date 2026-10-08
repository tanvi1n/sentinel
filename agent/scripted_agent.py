"""Scripted agent simulator: untrusted, thin, replaceable.

It hands out pre-written proposals one at a time and nothing else. It holds no
permissions, policy, risk, reversibility or confirmation state, assigns no
provenance, and cannot reach anything trusted: it imports only the proposal
contract. Whatever it returns is a claim, to be checked by the guard.
"""

import copy
from collections.abc import Iterable, Mapping
from typing import Any

from app.contracts import ActionProposal


class ScriptedAgent:
    def __init__(self, steps: Iterable[Mapping[str, Any] | ActionProposal]) -> None:
        # Validated up front. ActionProposal refuses unknown fields, so a step
        # cannot carry an agent id, session id, user task, risk, permission,
        # confirmation or observed content.
        self._queue: list[ActionProposal] = [self._to_proposal(s) for s in steps]
        self._next = 0

    @staticmethod
    def _to_proposal(step: Mapping[str, Any] | ActionProposal) -> ActionProposal:
        if isinstance(step, ActionProposal):
            return ActionProposal.model_validate(step.model_dump())
        if isinstance(step, Mapping):
            return ActionProposal.model_validate(dict(step))
        raise TypeError("a step must be a mapping or an ActionProposal")

    @property
    def remaining(self) -> int:
        return len(self._queue) - self._next

    def propose(self) -> ActionProposal | None:
        """The next proposal, or None when the script is finished."""
        if self._next >= len(self._queue):
            return None
        proposal = self._queue[self._next]
        self._next += 1
        return copy.deepcopy(proposal)
