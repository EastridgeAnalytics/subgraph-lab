"""A run: one pass of the loop, its two stores, its manifest. The one object examples, the API and the tools use.

The facade below fixes the function each track provides. A track implements its function under the name given;
the facade never grows a second way to do the same job.

| Facade method | Implemented in | Track |
|---|---|---|
| `pull()` | `lab.roundtrip.pull.pull(run) -> PullResult` | 1b |
| `stage(df, key, kind)` | `lab.roundtrip.stage.stage(run, df, key, kind) -> int` | 1e |
| `preview_push()` | `lab.roundtrip.push.preview(run) -> Review` | 1e |
| `apply_push(confirm)` | `lab.roundtrip.push.apply(run, confirm) -> PushResult` | 1e |
| `to_pyg()` and the rest | `lab.adapters.<name>(run, ...)` | 1c |
| `first_pass()` | `lab.slice.first_pass.first_pass(run) -> dict[str, int]` | 2a |
| `search(spec)` | `lab.slice.search.search_slice(run, spec) -> SliceEstimate` | 2a |
| `score()` | `lab.scoring.pipeline.score(run) -> dict[str, int]` | 2b, 3a, 3b |
"""
from __future__ import annotations

import os
from pathlib import Path

import polars as pl

from lab.common.arrow import query_frame
from lab.common.ids import content_hash
from lab.common.ids import run_id as new_run_id
from lab.common.store import RunPaths, list_runs, open_store, run_paths
from lab.contracts.manifest import RunManifest
from lab.contracts.recipe import Recipe, SearchSpec

_OPEN: dict[str, object] = {}  # one writable connection per store path per process (Relate allows one writer)


class Run:
    def __init__(self, run_id: str, data_dir: Path | None = None):
        self.id = run_id
        self.paths: RunPaths = run_paths(run_id, data_dir)
        if not self.paths.manifest.exists():
            raise FileNotFoundError(f"no run {run_id} under {self.paths.root.parent}")
        self.manifest = RunManifest.load(self.paths.manifest)
        self.recipe = Recipe.load(self.paths.recipe)

    # ---- lifecycle -------------------------------------------------------------------------------------
    @classmethod
    def create(cls, recipe: Recipe | None = None, data_dir: Path | None = None, run_id: str | None = None) -> Run:
        recipe = recipe or Recipe()
        rid = run_id or new_run_id()
        paths = run_paths(rid, data_dir)
        paths.root.mkdir(parents=True, exist_ok=False)
        paths.staged.mkdir()
        paths.artifacts.mkdir()
        paths.recipe.write_text(recipe.dump_yaml(), encoding="utf-8")
        RunManifest(run_id=rid, recipe_hash=content_hash(recipe.model_dump(mode="json"))).save(paths.manifest)
        return cls(rid, data_dir)

    @classmethod
    def current(cls, data_dir: Path | None = None) -> Run:
        """LAB_RUN_ID if set, else the newest run."""
        rid = os.environ.get("LAB_RUN_ID")
        if rid:
            return cls(rid, data_dir)
        runs = list_runs(data_dir)
        if not runs:
            raise FileNotFoundError("no runs yet; start one from the app, the API, or Run.create()")
        return cls(runs[0], data_dir)

    def save(self) -> None:
        self.manifest.save(self.paths.manifest)

    def reload(self) -> None:
        self.manifest = RunManifest.load(self.paths.manifest)

    # ---- stores ----------------------------------------------------------------------------------------
    def store(self, read_only: bool = False):
        """The run store (what `relate pull` filled). Writable by the lab only."""
        return self._open(self.paths.store, read_only)

    def push_store(self):
        """The push store (decision 22)."""
        return self._open(self.paths.push_store, False)

    def _open(self, path: Path, read_only: bool):
        if read_only:
            return open_store(path, read_only=True)
        key = str(path.resolve())
        if key not in _OPEN:
            _OPEN[key] = open_store(path)
        return _OPEN[key]

    def close(self) -> None:
        for path in (self.paths.store, self.paths.push_store):
            conn = _OPEN.pop(str(path.resolve()), None)
            if conn is not None:
                conn.close()

    def table(self, query: str, params: dict | None = None) -> pl.DataFrame:
        """Any Cypher on the run store, as a Polars frame through Arrow. The table adapter."""
        return query_frame(self.store(), query, params)

    # ---- the tracks' functions ---------------------------------------------------------------------------
    def pull(self):
        from lab.roundtrip.pull import pull

        return pull(self)

    def first_pass(self):
        from lab.slice.first_pass import first_pass

        return first_pass(self)

    def search(self, spec: SearchSpec | None = None):
        from lab.slice.search import search_slice

        return search_slice(self, spec or self.recipe.search)

    def to_pyg(self, **kw):
        from lab.adapters import to_pyg

        return to_pyg(self, **kw)

    def to_temporal(self, **kw):
        from lab.adapters import to_temporal

        return to_temporal(self, **kw)

    def to_networkx(self, **kw):
        from lab.adapters import to_networkx

        return to_networkx(self, **kw)

    def to_series(self, **kw):
        from lab.adapters import to_series

        return to_series(self, **kw)

    def score(self):
        from lab.scoring.pipeline import score

        return score(self)

    def stage(self, df: pl.DataFrame, key: str | None = None, kind: str = "account") -> int:
        from lab.roundtrip.stage import stage

        return stage(self, df, key or self.recipe.graph.account_key, kind)

    def preview_push(self):
        from lab.roundtrip.push import preview

        return preview(self)

    def apply_push(self, confirm: bool = False):
        from lab.roundtrip.push import apply

        return apply(self, confirm)

    def push(self, confirm: bool = False):
        """The examples' last line. Without confirm it is the preview: the dry run plus the delta."""
        return self.apply_push(confirm=True) if confirm else self.preview_push()


def run(run_id: str | None = None, data_dir: Path | None = None) -> Run:
    """`lab.run()`: the run named, or LAB_RUN_ID, or the newest."""
    return Run(run_id, data_dir) if run_id else Run.current(data_dir)
