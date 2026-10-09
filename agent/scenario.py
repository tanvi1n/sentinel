"""Generic scenario loading.

Scenario JSON files describe a demo run: who the agent is, what the user asked,
and an ordered list of requests with the outcome each is expected to get. They
carry no guard logic. The expectations are used only by tests and the runner,
never by the guard. External content (pages, messages) is not stored here: it
comes from the backend's mock world when a tool runs.

Nothing here knows any tool or domain: tool names and argument names are data
inside the files.
"""

import re
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from agent.scripted_agent import AgentRequest
from app.contracts.decision import DecisionLifecycle, DecisionOutcome
from app.semantic.mock import MOCK_MODES

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = ROOT / "scenarios"

_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# The lifecycle a freshly evaluated decision has, by outcome (backend contract).
_LIFECYCLE_FOR = {
    DecisionOutcome.APPROVE: DecisionLifecycle.EVALUATED,
    DecisionOutcome.REVIEW: DecisionLifecycle.PENDING_REVIEW,
    DecisionOutcome.BLOCK: DecisionLifecycle.BLOCKED,
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScenarioError(Exception):
    """A scenario file is missing, malformed or inconsistent."""


class StepExpectation(StrictModel):
    """What the run is expected to produce. Used by tests; never fed to the guard."""

    outcome: DecisionOutcome
    lifecycle: DecisionLifecycle

    @model_validator(mode="after")
    def _lifecycle_matches_outcome(self) -> Self:
        if self.lifecycle is not _LIFECYCLE_FOR[self.outcome]:
            raise ValueError(
                f"lifecycle {self.lifecycle.value} does not follow outcome {self.outcome.value}"
            )
        return self


class ScenarioStep(StrictModel):
    request: AgentRequest
    auto_execute: bool  # whether the harness may auto-execute this step
    expect: StepExpectation
    mock_semantic: str | None = None  # semantic mock variant to use in mock mode
    # Mock variants under which the same outcome must still hold (BLOCK only).
    invariant_mock_modes: list[str] = Field(default_factory=list)
    human_review: Literal["approve", "reject"] | None = None  # the demo reviewer's action
    undo_expected: bool = False
    # Text expected in the content the harness records once this step has run.
    expects_observed: str | None = None

    @field_validator("mock_semantic")
    @classmethod
    def _known_mock_mode(cls, v: str | None) -> str | None:
        if v is not None and v not in MOCK_MODES:
            raise ValueError(f"unknown mock semantic mode: {v!r}")
        return v

    @field_validator("invariant_mock_modes")
    @classmethod
    def _known_invariant_modes(cls, v: list[str]) -> list[str]:
        for mode in v:
            if mode not in MOCK_MODES:
                raise ValueError(f"unknown mock semantic mode: {mode!r}")
        return v

    @property
    def executes(self) -> bool:
        return self.auto_execute or self.human_review == "approve"

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        outcome = self.expect.outcome
        if self.auto_execute and outcome is not DecisionOutcome.APPROVE:
            raise ValueError("only an APPROVE step may auto-execute")
        if self.human_review is not None and outcome is not DecisionOutcome.REVIEW:
            raise ValueError("a human review only applies to a REVIEW step")
        if self.invariant_mock_modes and outcome is not DecisionOutcome.BLOCK:
            raise ValueError("invariant_mock_modes only applies to a BLOCK step")
        if self.undo_expected and not self.executes:
            raise ValueError("undo is only expected for a step that executes")
        if self.expects_observed is not None and not self.executes:
            raise ValueError("expects_observed needs a step that executes")
        return self


class Scenario(StrictModel):
    id: str
    title: str
    description: str
    agent_id: str
    user_task: str
    expected_verdict: DecisionOutcome  # the headline outcome; equals the last step's
    steps: list[ScenarioStep] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError("id must be a lowercase slug")
        return v

    @field_validator("title", "description", "agent_id", "user_task")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v

    @model_validator(mode="after")
    def _verdict_matches_last_step(self) -> Self:
        if self.steps[-1].expect.outcome is not self.expected_verdict:
            raise ValueError("expected_verdict must equal the last step's outcome")
        return self

    @property
    def expects_human_review(self) -> bool:
        return any(s.human_review is not None for s in self.steps)

    @property
    def expects_human_approval(self) -> bool:
        return any(s.human_review == "approve" for s in self.steps)

    @property
    def expects_undo(self) -> bool:
        return any(s.undo_expected for s in self.steps)

    def requests(self) -> list[AgentRequest]:
        """The ordered requests, ready for ScriptedAgent."""
        return [s.request.model_copy(deep=True) for s in self.steps]


# ---- scenarios --------------------------------------------------------------


def _brief(exc: Exception) -> str:
    return str(exc) if isinstance(exc, ValidationError) else type(exc).__name__


def load_scenario(path: Path | str) -> Scenario:
    path = Path(path)
    try:
        return Scenario.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise ScenarioError(f"bad scenario {path.name}: {_brief(exc)}") from exc


def load_scenarios(directory: Path | str = SCENARIOS_DIR) -> list[Scenario]:
    """All scenarios, in file-name order. Duplicate ids are an error."""
    directory = Path(directory)
    scenarios: list[Scenario] = []
    seen: dict[str, str] = {}
    for path in sorted(directory.glob("*.json")):
        scenario = load_scenario(path)
        if scenario.id in seen:
            raise ScenarioError(
                f"duplicate scenario id {scenario.id!r} in {path.name} and {seen[scenario.id]}"
            )
        seen[scenario.id] = path.name
        scenarios.append(scenario)
    return scenarios
