"""Reading a run without getting in the worker's way.

The worker rewrites a run's `manifest.json` after every step: it writes `manifest.tmp` and replaces the old file. On
Windows that replace fails with "Access is denied" when anything has the old file open at that instant, even a reader
that opened it with share-delete (tried in wave 2d). The Run screen polls the manifest every second, so now and then a
plain poll would make the pipeline fail at `manifest.save()`. Two things keep that from happening here:

- `RunView` is what a read-only route needs of a run: its id, its paths and its recipe. It never opens the manifest.
  Only the manifest routes, the run list and the supervisor read it, and each holds it for one `read_text`.
- `keep_saving(run)` makes the `Run` the worker (or a request) is writing through try its save again when the replace is
  refused, and `read_text` tries again when an open is refused during one. A reader holds the file for well under a
  millisecond, so the second try lands.

`RunManifest.save` is the one place that could carry the retry itself (it is a contract, so it is the director's).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from lab.common.store import RunPaths, run_paths
from lab.contracts.manifest import RunManifest
from lab.contracts.recipe import Recipe

if TYPE_CHECKING:
    from lab.runs import Run

SAVE_TRIES = 12
SAVE_PAUSE = 0.025  # seconds; grows with each try


def read_text(path: Path) -> str:
    """The file's text. While the worker replaces the manifest, opening it can be refused for a moment: try again."""
    for attempt in range(SAVE_TRIES):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError:
            if attempt == SAVE_TRIES - 1:
                raise
            time.sleep(SAVE_PAUSE * (attempt + 1))
    raise AssertionError("unreachable")


def read_manifest(path: Path) -> RunManifest:
    return RunManifest.model_validate_json(read_text(path))


def keep_saving(run: Run) -> Run:
    """`run.save()` that tries again, a few times, when the manifest cannot be replaced because a reader has it open."""
    original = run.save

    def save() -> None:
        for attempt in range(SAVE_TRIES):
            try:
                return original()
            except PermissionError:
                if attempt == SAVE_TRIES - 1:
                    raise
                time.sleep(SAVE_PAUSE * (attempt + 1))

    run.save = save  # type: ignore[method-assign]
    return run


class RunView:
    """A run as the read-only routes see it: `id`, `paths` and `recipe`."""

    def __init__(self, run_id: str, data_dir: Path | None = None):
        self.id = run_id
        self.paths: RunPaths = run_paths(run_id, data_dir)
        if not self.paths.manifest.exists():
            raise FileNotFoundError(f"no run {run_id}")
        self.recipe = Recipe.model_validate(yaml.safe_load(read_text(self.paths.recipe)) or {})
