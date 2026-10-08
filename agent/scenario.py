"""Generic scenario and seed-content loading.

Scenario JSON files are the single source of truth for a demo run: who the
agent is, what the user asked, and an ordered list of proposals with the
outcome each one is expected to get. They carry no guard logic. Seed content is
kept in a separate directory with a manifest; a scenario only refers to it by
id, so untrusted external text never sits inside trusted scenario instructions.

Nothing here knows any tool or domain: tool names and argument names are data
inside the files.
"""

import json
import re
from pathlib import Path
from typing import Self

from pydantic import Field, ValidationError, field_validator, model_validator

from app.contracts import (
    ActionProposal,
    Decision,
    DecisionStatus,
    ProvenanceLabel,
    ReviewAction,
    StrictModel,
)
from app.semantic.mock import MOCK_MODES

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = ROOT / "scenarios"
SEED_DIR = ROOT / "seed_content"

_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# The status a freshly evaluated decision has, by decision.
_STATUS_FOR = {
    Decision.APPROVE: DecisionStatus.APPROVED,
    Decision.REVIEW: DecisionStatus.PENDING_REVIEW,
    Decision.BLOCK: DecisionStatus.BLOCKED,
}


class ScenarioError(Exception):
    """A scenario or seed file is missing, malformed or inconsistent."""


class StepExpectation(StrictModel):
    """What the run is expected to produce. Used by tests; never fed to the guard."""

    decision: Decision
    status: DecisionStatus
    deterministic_decision: Decision | None = None  # "rules alone", for display

    @model_validator(mode="after")
    def _status_matches_decision(self) -> Self:
        if self.status is not _STATUS_FOR[self.decision]:
            raise ValueError(
                f"status {self.status.value} does not follow decision {self.decision.value}"
            )
        return self


class ScenarioStep(StrictModel):
    proposal: ActionProposal
    auto_execute: bool  # whether the harness may auto-execute this step
    expect: StepExpectation
    mock_semantic: str | None = None  # semantic mock mode to use in mock mode
    # Mock modes under which the same decision must still hold (BLOCK only).
    invariant_mock_modes: list[str] = Field(default_factory=list)
    human_review: ReviewAction | None = None  # the human action the demo performs
    undo_expected: bool = False
    reads_seed: str | None = None  # seed id the step's read is expected to surface
    expected_output_label: ProvenanceLabel | None = None

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

    @field_validator("expected_output_label")
    @classmethod
    def _label_not_user(cls, v: ProvenanceLabel | None) -> ProvenanceLabel | None:
        if v is ProvenanceLabel.USER:
            raise ValueError("observed content is never labelled USER")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        decision = self.expect.decision
        if self.auto_execute and decision is not Decision.APPROVE:
            raise ValueError("only an APPROVE step may auto-execute")
        if self.human_review is not None and decision is not Decision.REVIEW:
            raise ValueError("a human review only applies to a REVIEW step")
        if self.invariant_mock_modes and decision is not Decision.BLOCK:
            raise ValueError("invariant_mock_modes only applies to a BLOCK step")
        if self.undo_expected and not (
            self.auto_execute or self.human_review is ReviewAction.approve
        ):
            raise ValueError("undo is only expected for a step that executes")
        if self.expected_output_label is not None and self.reads_seed is None:
            raise ValueError("expected_output_label needs reads_seed")
        return self


class Scenario(StrictModel):
    id: str
    title: str
    description: str
    agent_id: str
    user_task: str
    expected_verdict: Decision  # the headline outcome; equals the last step's
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
        if self.steps[-1].expect.decision is not self.expected_verdict:
            raise ValueError("expected_verdict must equal the last step's decision")
        return self

    @property
    def expects_human_review(self) -> bool:
        return any(s.human_review is not None for s in self.steps)

    @property
    def expects_human_approval(self) -> bool:
        return any(s.human_review is ReviewAction.approve for s in self.steps)

    @property
    def expects_undo(self) -> bool:
        return any(s.undo_expected for s in self.steps)

    def proposals(self) -> list[ActionProposal]:
        """The ordered proposals, ready for ScriptedAgent."""
        return [s.proposal.model_copy(deep=True) for s in self.steps]


# ---- seed content -----------------------------------------------------------


class SeedItem(StrictModel):
    id: str
    kind: str
    path: str  # relative to the seed directory
    description: str


class SeedManifest(StrictModel):
    items: list[SeedItem]


class SeedContent:
    """Read-only access to seed documents by id. All of it is untrusted data."""

    def __init__(self, directory: Path, items: dict[str, SeedItem]) -> None:
        self.directory = directory
        self.items = items

    def __contains__(self, seed_id: str) -> bool:
        return seed_id in self.items

    def text(self, seed_id: str) -> str:
        item = self.items[seed_id]
        return (self.directory / item.path).read_text(encoding="utf-8")

    def json(self, seed_id: str):
        return json.loads(self.text(seed_id))


def load_seed(directory: Path | str = SEED_DIR) -> SeedContent:
    directory = Path(directory)
    manifest_path = directory / "manifest.json"
    try:
        manifest = SeedManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise ScenarioError(f"bad seed manifest {manifest_path}: {_brief(exc)}") from exc
    items: dict[str, SeedItem] = {}
    for item in manifest.items:
        if item.id in items:
            raise ScenarioError(f"duplicate seed id: {item.id}")
        target = (directory / item.path).resolve()
        if directory.resolve() not in target.parents or not target.is_file():
            raise ScenarioError(f"seed file for {item.id} is missing or outside the directory")
        items[item.id] = item
    return SeedContent(directory, items)


# ---- scenarios --------------------------------------------------------------


def _brief(exc: Exception) -> str:
    return str(exc) if isinstance(exc, ValidationError) else type(exc).__name__


def load_scenario(path: Path | str, seed: SeedContent | None = None) -> Scenario:
    path = Path(path)
    try:
        scenario = Scenario.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise ScenarioError(f"bad scenario {path.name}: {_brief(exc)}") from exc
    if seed is not None:
        for step in scenario.steps:
            if step.reads_seed is not None and step.reads_seed not in seed:
                raise ScenarioError(
                    f"{path.name}: unknown seed id {step.reads_seed!r}"
                )
    return scenario


def load_scenarios(
    directory: Path | str = SCENARIOS_DIR, seed: SeedContent | None = None
) -> list[Scenario]:
    """All scenarios, in file-name order. Duplicate ids are an error."""
    directory = Path(directory)
    scenarios: list[Scenario] = []
    seen: dict[str, str] = {}
    for path in sorted(directory.glob("*.json")):
        scenario = load_scenario(path, seed)
        if scenario.id in seen:
            raise ScenarioError(
                f"duplicate scenario id {scenario.id!r} in {path.name} and {seen[scenario.id]}"
            )
        seen[scenario.id] = path.name
        scenarios.append(scenario)
    return scenarios
