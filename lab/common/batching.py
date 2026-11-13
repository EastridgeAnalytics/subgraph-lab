"""Keys to Relate in batches. Relate 1.0.1's `IN $keys` slows with the square of the list (LAB_KEY_BATCH, page 07).
Every reader that sends a key list uses this; a second way of batching keys is a review finding."""
from __future__ import annotations

from collections.abc import Iterator

from lab.settings import get_settings


def key_batches(keys: list | None, size: int | None = None) -> Iterator[list | None]:
    """The key lists to send, one statement each: the distinct keys in batches of `size` (LAB_KEY_BATCH when not
    given), or one None for no restriction."""
    if keys is None:
        yield None
        return
    distinct = list(dict.fromkeys(keys))
    size = max(1, size or get_settings().key_batch)
    for i in range(0, len(distinct), size):
        yield distinct[i : i + size]
