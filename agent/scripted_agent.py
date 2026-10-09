"""Scripted agent simulator: untrusted, thin, replaceable.

It hands out pre-written requests one at a time and nothing else. A request is
only a tool and its arguments. The agent holds no agent id, session, user task,
provenance, permission, risk or confirmation state, and it imports nothing from
the application, so it cannot reach anything trusted. Whatever it returns is a
claim, to be checked by the guard.
"""

import copy
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentRequest(BaseModel):
    """What an untrusted agent may ask for. Unknown fields are refused."""

    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ScriptedAgent:
    def __init__(self, steps: Iterable[Mapping[str, Any] | AgentRequest]) -> None:
        # Validated up front: a step carrying an agent id, session id, user task,
        # context, provenance, risk, permission or confirmation is rejected.
        self._queue: list[AgentRequest] = [self._to_request(s) for s in steps]
        self._next = 0

    @staticmethod
    def _to_request(step: Mapping[str, Any] | AgentRequest) -> AgentRequest:
        if isinstance(step, AgentRequest):
            return AgentRequest.model_validate(step.model_dump())
        if isinstance(step, Mapping):
            return AgentRequest.model_validate(dict(step))
        raise TypeError("a step must be a mapping or an AgentRequest")

    @property
    def remaining(self) -> int:
        return len(self._queue) - self._next

    def propose(self) -> AgentRequest | None:
        """The next request, or None when the script is finished."""
        if self._next >= len(self._queue):
            return None
        request = self._queue[self._next]
        self._next += 1
        return copy.deepcopy(request)
