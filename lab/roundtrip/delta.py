"""The lab's own delta (page 01, "Review"): what a push would change on the master graph.

`relate push --dry-run` prints a plan, but its "created" column is the plan, not a diff against the remote. The delta
fills that gap by reading the master graph over Bolt, read transactions only:

- `new_alerts`, `new_communities`: staged keys that are not on the master graph yet (all of them, `key_batch` at a time);
- `score_changes`: for up to `delta_sample` accounts spread evenly over the sorted keys, the remote `risk_score` against
  the staged one, bucketed by the size of the change (`new` when the remote has no score);
- `missing_remote_keys`: sampled accounts the master graph does not have. A keyed merge would create them with only their
  `risk_*` properties, so this number deserves a look before a confirm;
- counts from the push store: accounts scored, vectors, dispositions; and a bytes estimate from a sample of staged rows.

Every number here is a count or an estimate over the push store and the sample. Nothing in it is measured against Aura yet
(to measure).
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import numpy as np
import polars as pl

from lab.common.arrow import query_frame
from lab.common.log import get_logger
from lab.common.neo4j import neo4j_session
from lab.common.store import rows
from lab.contracts.api import Delta
from lab.contracts.graph import ALERT_KEY, ALERT_LABEL, COMMUNITY_KEY, COMMUNITY_LABEL, GraphMapping
from lab.roundtrip.queries import ident
from lab.roundtrip.stage import NothingStaged, has_staged, push_store_counts
from lab.settings import get_settings

if TYPE_CHECKING:
    from lab.runs import Run

log = get_logger("roundtrip.delta")

NEW = "new"
BUCKETS = ("0.0-0.1", "0.1-0.2", "0.2-0.5", "0.5-1.0")  # the size of a score's move; lower edge in, upper edge out


class DeltaError(RuntimeError):
    """The master graph could not be read over Bolt, so there is no delta to show."""


def bucket(change: float) -> str:
    """The bucket for a score that moved by `change`. Rounded so that 0.7 against 0.6 is a 0.1 move, not 0.0999..."""
    d = round(abs(change), 9)
    if d < 0.1:
        return BUCKETS[0]
    if d < 0.2:
        return BUCKETS[1]
    if d < 0.5:
        return BUCKETS[2]
    return BUCKETS[3]


def sample_positions(n: int, sample: int) -> list[int]:
    """Up to `sample` row numbers spread evenly over `n` sorted rows, the same ones every time."""
    if n <= sample:
        return list(range(n))
    return sorted({int(i) for i in np.linspace(0, n - 1, num=sample).round()})


@contextmanager
def _session(session, settings) -> Iterator[Any]:
    if session is not None:
        yield session
    else:
        with neo4j_session(settings) as s:
            yield s


def read_remote(session, query: str, **params) -> list[dict]:
    """A read transaction: the driver refuses a write in it, so the delta cannot change the master graph."""

    def work(tx):
        return [r.data() for r in tx.run(query, **params)]

    return session.execute_read(work)


def _chunks(items: list, size: int) -> Iterator[list]:
    size = max(1, size)
    for i in range(0, len(items), size):
        yield items[i : i + size]


def missing_on_master(session, label: str, key: str, keys: list, batch: int) -> list:
    """The keys in `keys` that no node labelled `label` has on the master graph, in their given order."""
    found: set = set()
    query = f"MATCH (n:{ident(label)}) WHERE n.{ident(key)} IN $keys RETURN n.{ident(key)} AS k"
    for part in _chunks(keys, batch):
        found.update(r["k"] for r in read_remote(session, query, keys=part))
    return [k for k in keys if k not in found]


def _missing_keys(session, label: str, key: str, keys: list, batch: int) -> int:
    """How many of `keys` no node labelled `label` has on the master graph."""
    return len(missing_on_master(session, label, key, keys, batch))


def _bytes_estimate(conn, mapping: GraphMapping, counts: dict[str, int], sample_rows: int) -> int:
    """Bytes a push would send: for each label, the staged row size averaged over `sample_rows` rows (the delta's own
    sample size), times the rows; each relationship row costs about two keys and a little framing."""
    total = 0.0
    key_bytes = 0.0
    for label, key, n in (
        (mapping.account_label, mapping.account_key, counts["accounts"]),
        (ALERT_LABEL, ALERT_KEY, counts["alerts"]),
        (COMMUNITY_LABEL, COMMUNITY_KEY, counts["communities"]),
    ):
        if not n:
            continue
        sample = rows(conn, f"MATCH (n:{ident(label)}) RETURN n.{ident(key)} AS k, properties(n) AS p LIMIT {max(1, sample_rows)}")
        sizes = [len(json.dumps({"key": {key: r["k"]}, "props": {k: v for k, v in r["p"].items() if k != key}},
                                separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8"))
                 for r in sample]
        avg = sum(sizes) / max(len(sizes), 1)
        total += (avg + 1) * n
        key_bytes = max(key_bytes, sum(len(str(r["k"])) + 6 for r in sample) / max(len(sample), 1))
    total += counts["relationships"] * (2 * key_bytes + 24)
    return int(total)


def compute(run: Run, *, session=None) -> Delta:
    """The delta for the run's push store against the master graph. Reads only.

    `session` is an open Bolt session, when the caller has one (the review shares its own); otherwise one is opened.
    Raises `NothingStaged` for an empty push store and `DeltaError` when the master graph cannot be read.
    """
    settings = get_settings()
    mapping = run.recipe.graph
    if not run.paths.push_store.exists():
        raise NothingStaged(f"nothing is staged for {run.id}: stage a results table first")
    conn = run.push_store()
    counts = push_store_counts(conn, mapping)
    if not has_staged(counts):
        raise NothingStaged(f"nothing is staged for {run.id}: stage a results table first")

    frame = query_frame(
        conn,
        f"MATCH (a:{ident(mapping.account_label)}) RETURN a.{ident(mapping.account_key)} AS key, "
        "a.risk_score AS score ORDER BY key",
    )
    sampled = frame.select(pl.all().gather(sample_positions(frame.height, settings.delta_sample)))
    alert_keys = [r["k"] for r in rows(conn, f"MATCH (n:{ident(ALERT_LABEL)}) RETURN n.{ident(ALERT_KEY)} AS k")]
    community_keys = [
        r["k"] for r in rows(conn, f"MATCH (n:{ident(COMMUNITY_LABEL)}) RETURN n.{ident(COMMUNITY_KEY)} AS k")
    ]
    bytes_estimate = _bytes_estimate(conn, mapping, counts, settings.delta_sample)

    changes = dict.fromkeys((NEW, *BUCKETS), 0)
    missing = 0
    with _session(session, settings) as s:
        try:  # the driver connects on first use, so a refused or unauthorised master graph shows up here
            new_alerts = _missing_keys(s, ALERT_LABEL, ALERT_KEY, alert_keys, settings.key_batch)
            new_communities = _missing_keys(s, COMMUNITY_LABEL, COMMUNITY_KEY, community_keys, settings.key_batch)
            query = (
                f"UNWIND $keys AS k OPTIONAL MATCH (a:{ident(mapping.account_label)} {{{ident(mapping.account_key)}: k}}) "
                "RETURN k AS key, a IS NOT NULL AS found, a.risk_score AS score"
            )
            local = dict(zip(sampled["key"].to_list(), sampled["score"].to_list(), strict=True))
            for part in _chunks(list(local), settings.key_batch):
                for r in read_remote(s, query, keys=part):
                    mine = local[r["key"]]
                    if not r["found"]:
                        missing += 1
                    if mine is None:
                        continue
                    changes[NEW if r["score"] is None else bucket(float(mine) - float(r["score"]))] += 1
        except Exception as e:
            raise DeltaError(f"could not read the master graph over Bolt: {type(e).__name__}: {e}"[:400]) from e

    return Delta(
        new_alerts=new_alerts, new_communities=new_communities, accounts_scored=counts["accounts_scored"],
        score_changes=changes, missing_remote_keys=missing, sampled=sampled.height, vectors=counts["vectors"],
        dispositions=counts["dispositions"], bytes_estimate=bytes_estimate,
    )
