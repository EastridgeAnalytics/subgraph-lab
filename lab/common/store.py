"""Opening Relate stores, and where a run's files live."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from lab.settings import get_settings

# Relate's documented sizes (page 07): bytes on disk per node and per relationship, and the served multiple.
BYTES_PER_NODE = 502
BYTES_PER_REL = 404
SERVED_FACTOR = 1.54  # the top of the documented 1.22 to 1.54 range, so estimates err high


def estimate_memory(nodes: int, rels: int) -> int:
    return int((nodes * BYTES_PER_NODE + rels * BYTES_PER_REL) * SERVED_FACTOR)


def open_store(path: Path | str, read_only: bool = False):
    """A Relate DB-API connection (decision 27). Autocommit is on. DO_NOT_TRACK is always set first."""
    os.environ["DO_NOT_TRACK"] = "1"
    import relate  # imported late so DO_NOT_TRACK is set before the native module loads

    return relate.connect(str(path), read_only=read_only)


def rows(conn, query: str, params: dict | None = None) -> list[dict]:
    """Run a statement and return its rows as dicts."""
    return conn.execute(query, params or {}).data()


@dataclass(frozen=True)
class RunPaths:
    run_id: str
    root: Path

    @property
    def store(self) -> Path:
        """The run store: what `relate pull` filled. Never pushed (decision 22)."""
        return self.root / "store"

    @property
    def push_store(self) -> Path:
        """The push store: keys, risk_* and the lab's own nodes only."""
        return self.root / "push"

    @property
    def manifest(self) -> Path:
        return self.root / "manifest.json"

    @property
    def recipe(self) -> Path:
        return self.root / "recipe.yaml"

    @property
    def staged(self) -> Path:
        """Parquet copies of every staged table, for review and for the app."""
        return self.root / "staged"

    @property
    def artifacts(self) -> Path:
        """Model outputs, evidence objects, summaries."""
        return self.root / "artifacts"


def run_paths(run_id: str, data_dir: Path | None = None) -> RunPaths:
    base = Path(data_dir or get_settings().data_dir)
    return RunPaths(run_id=run_id, root=base / "runs" / run_id)


def list_runs(data_dir: Path | None = None) -> list[str]:
    base = Path(data_dir or get_settings().data_dir) / "runs"
    if not base.exists():
        return []
    return sorted((p.name for p in base.iterdir() if (p / "manifest.json").exists()), reverse=True)


def summary_cache_path(data_dir: Path | None = None) -> Path:
    """Summaries are cached across runs on evidence hash plus model (page 05)."""
    return Path(data_dir or get_settings().data_dir) / "cache" / "summaries.jsonl"
