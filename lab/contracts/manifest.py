"""The run manifest: one JSON file per run that says what ran, by which route, and what landed."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field


class Route(StrEnum):
    RELATE = "relate"
    BOLT = "bolt"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


STEP_ORDER = (
    "pull", "indexes", "first_pass", "slice", "models", "score", "communities", "summaries", "stage", "review",
    "push",
)


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Step(BaseModel):
    name: str
    status: StepStatus = StepStatus.PENDING
    started_at: str | None = None
    finished_at: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    note: str | None = None


class Fallback(BaseModel):
    """A fallback that ran. Recorded, never silent (page 97, lesson three)."""

    step: str
    route: Route
    reason: str
    at: str = Field(default_factory=now_iso)


class PushEntity(BaseModel):
    """One entity line of a `relate push --json` report, or of the Bolt fallback's equivalent."""

    entity: str
    kind: str = "node"
    total: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    batches: int = 0
    landed: bool = False
    error: str | None = None


class RunManifest(BaseModel):
    run_id: str
    recipe_hash: str
    created_at: str = Field(default_factory=now_iso)
    finished_at: str | None = None
    status: StepStatus = StepStatus.PENDING
    routes: dict[str, Route] = Field(default_factory=dict)
    fallbacks: list[Fallback] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=lambda: [Step(name=n) for n in STEP_ORDER])
    push: list[PushEntity] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    model_hash: str | None = None
    factor_version: str | None = None

    def step(self, name: str) -> Step:
        for s in self.steps:
            if s.name == name:
                return s
        s = Step(name=name)
        self.steps.append(s)
        return s

    def start(self, name: str) -> Step:
        s = self.step(name)
        s.status, s.started_at, s.finished_at = StepStatus.RUNNING, now_iso(), None
        self.status = StepStatus.RUNNING
        return s

    def finish(self, name: str, counts: dict[str, int] | None = None, note: str | None = None) -> Step:
        s = self.step(name)
        s.status, s.finished_at = StepStatus.DONE, now_iso()
        if counts:
            s.counts.update(counts)
        if note:
            s.note = note
        return s

    def fail(self, name: str, note: str) -> Step:
        s = self.step(name)
        s.status, s.finished_at, s.note = StepStatus.FAILED, now_iso(), note
        self.status = StepStatus.FAILED
        return s

    def fallback(self, step: str, route: Route, reason: str) -> None:
        self.routes[step] = route
        self.fallbacks.append(Fallback(step=step, route=route, reason=reason))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> RunManifest:
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))
