"""The auto-sizing batch loader.

One commit to a Relate store cannot carry a WAL record of 64 MB or more; a bigger one is refused whole with a
`DataError` and nothing is written (page 07, checked in wave 0). So the loader starts at `LAB_LOAD_START_ROWS`
rows, doubles after each success, and halves when Relate refuses, retrying the same rows. A refused commit wrote
nothing, so every row still loads exactly once. After the first refusal the size only goes down: the ceiling is
known, and probing it again would cost a refused commit each time.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from lab.common.log import get_logger
from lab.settings import get_settings

log = get_logger("roundtrip.loader")


class LoadError(RuntimeError):
    """A single row is bigger than Relate will commit."""


@dataclass
class LoadStats:
    rows: int = 0
    batches: int = 0
    largest_batch: int = 0
    refusals: int = 0
    size: int = 0  # the batch size in force at the end: pass it as `start` to the next call

    def add(self, other: LoadStats) -> None:
        self.rows += other.rows
        self.batches += other.batches
        self.largest_batch = max(self.largest_batch, other.largest_batch)
        self.refusals += other.refusals
        self.size = other.size


def is_wal_refusal(exc: BaseException) -> bool:
    """Relate's refusal of a commit that is too big: 'a single WAL record of at least 64 MB'."""
    text = str(exc)
    return "WAL record" in text and "64 MB" in text


def load_rows(conn, statement: str, rows: Iterable[dict], start: int | None = None) -> LoadStats:
    """Run `statement` (which takes `$rows`) over `rows` in batches that size themselves."""
    data: Sequence[dict] = rows if isinstance(rows, Sequence) else list(rows)
    size = max(1, start or get_settings().load_start_rows)
    stats = LoadStats(size=size)
    growing = True
    i = 0
    while i < len(data):
        batch = data[i : i + size]
        try:
            conn.execute(statement, {"rows": batch})
        except Exception as e:
            if not is_wal_refusal(e):
                raise
            stats.refusals += 1
            if len(batch) == 1:
                raise LoadError("one row is larger than Relate's 64 MB commit limit") from e
            size, growing = max(1, len(batch) // 2), False
            log.info("Relate refused a batch of %d rows (64 MB commit limit); retrying at %d", len(batch), size)
            continue
        i += len(batch)
        stats.rows += len(batch)
        stats.batches += 1
        stats.largest_batch = max(stats.largest_batch, len(batch))
        if growing:
            size *= 2
    stats.size = size
    return stats
