"""The shared signals (page 04): the definitions the first pass, the community score, the evidence and the example all use.

One place for each job (the director's review, section 3). A caller that needs one of these calls it here and keeps no
copy of its own.

| Signal | Definition | Who calls it |
|---|---|---|
| `new_accounts` | An account opened within `LAB_NEW_ACCOUNT_DAYS` of the newest payment among the payments it is given | the first pass's summary phrase (`lab/slice/summaries.py`), the community's `new_accounts` part |
| `in_and_out_within_a_day` | The `in_and_out_within_a_day` factor's own query and threshold (`factors/in_and_out_within_a_day.cypher`: an outgoing payment within 24 hours after an incoming one, within 20 percent of its amount), run through the factor runner's reader, per account, with how many outgoing payments matched. Nothing here restates the 24 hours or the 20 percent | the first pass's summary phrase, the community's `speed` part |
| `payment_cycles` | A loop of at most `LAB_CYCLE_MAX_LENGTH` accounts, each paying the next and the last paying the first, whose payments run forward in time: each payment comes strictly after the one before it. Distinct loops are counted by their accounts (two accounts that pay each other once or fifty times are one cycle), up to `LAB_CYCLE_COUNT_CAP` | the community's `cycles` part, the evidence's cycle count, the NetworkX example |

A cycle with its payments in the wrong order, such as a to b on 3 October, b to c on 2 October and c to a on 1 October, is
not money that came back; it does not count. A loop is looked for from every account, so a loop counts when any of its
rotations runs forward in time. A payment between the same two accounts at the same instant is not after the other.
"""
from __future__ import annotations

import json
from bisect import bisect_right
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import networkx as nx
import polars as pl

from lab.adapters import reads
from lab.common import get_logger
from lab.roundtrip.queries import ident
from lab.scoring.factors import FactorRun, load_factor_file, run_cypher
from lab.settings import get_settings

if TYPE_CHECKING:
    from lab.runs import Run

log = get_logger("scoring.signals")

IN_AND_OUT_FACTOR = "in_and_out_within_a_day"  # the id of the factor whose query defines "money in and out within a day"
IN_AND_OUT_SCHEMA = {"key": pl.Utf8, "count": pl.Int64}


# ---- new accounts ----------------------------------------------------------------------------------------------
def account_opened(run: Run, keys: list[str] | None = None) -> pl.DataFrame:
    """`key` and `opened` (a naive UTC datetime, null when the mapping has no opened property) of each account in scope."""
    g = run.recipe.graph
    ret = f"a.{ident(g.account_key)} AS key"
    if g.account_opened:
        ret += f", a.{ident(g.account_opened)} AS opened"
    where = "" if keys is None else f"WHERE a.{ident(g.account_key)} IN $keys"
    query = f"MATCH (a:{ident(g.account_label)}) {where} RETURN {ret}"
    frames = [run.table(query, {} if b is None else {"keys": b}) for b in reads.key_batches(keys)]
    if not frames:  # keys == []
        return pl.DataFrame(schema={"key": pl.Utf8, "opened": pl.Datetime("us")})
    df = pl.concat(frames, how="vertical_relaxed")
    if "opened" not in df.columns:
        df = df.with_columns(opened=pl.lit(None, dtype=pl.Datetime("us")))
    df = df.select(pl.col("key").cast(pl.Utf8), opened=reads.utc_naive(df["opened"]))
    return df.drop_nulls("key").unique("key").sort("key")


def new_accounts(opened: pl.DataFrame, pay: pl.DataFrame) -> set[str]:
    """The accounts of `opened` (`account_opened`) that were opened within `LAB_NEW_ACCOUNT_DAYS` of the newest payment in
    `pay` (a frame with `ts`, naive UTC: the payments of the same scope). No payments: none are new."""
    as_of: datetime | None = pay["ts"].max() if pay.height else None
    if as_of is None:
        return set()
    cutoff = as_of - timedelta(days=get_settings().new_account_days)
    return set(opened.filter(pl.col("opened") >= cutoff)["key"])


# ---- money in and out within a day -----------------------------------------------------------------------------
def in_and_out_within_a_day(run: Run, keys: list[str] | None = None, factor_run: FactorRun | None = None) -> pl.DataFrame:
    """`key` and `count` for each account the `in_and_out_within_a_day` factor fires on: how many outgoing payments matched
    an incoming one. `keys` is the scope (None is every account in the run store).

    The query is the factor's, run through the factor runner's own reader with the factor's threshold, so the accounts are
    the factor's accounts. Give `factor_run` when scoring and its rows are used as they are: that is the same query
    already run on the same accounts, and it is not run twice. With a `factor_run` that did not run the factor (it was
    skipped or failed, and its note says why) there are no accounts. A factor file with no such factor, or one that is not
    a Cypher factor, gives none and says so in the log: the definition is the factor's, and there is no second one here.
    A query that returns a `score` and no `value` counts each account once.
    """
    if factor_run is not None:
        rows = factor_run.results.filter(pl.col("factor_id") == IN_AND_OUT_FACTOR).select("key", "evidence")
    else:
        path, factors = load_factor_file(run)
        factor = next((f for f in factors.factors if f.id == IN_AND_OUT_FACTOR), None)
        if factor is None or factor.kind != "cypher":
            log.warning("no Cypher factor %s in %s: nothing counts as money in and out within a day", IN_AND_OUT_FACTOR, path)
            return pl.DataFrame(schema=IN_AND_OUT_SCHEMA)
        rows = run_cypher(run, factor, path.parent, None if keys is None else list(dict.fromkeys(keys)))
    counts = [_matched(evidence) for evidence in rows["evidence"]]
    return pl.DataFrame({"key": rows["key"], "count": counts}, schema=IN_AND_OUT_SCHEMA).sort("key")


def _matched(evidence: str | None) -> int:
    """The `value` a factor's evidence JSON carries (outgoing payments that matched), 1 when it has none."""
    try:
        body: Any = json.loads(evidence) if evidence else None
    except ValueError:
        return 1
    value = body.get("value") if isinstance(body, dict) else None
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) and value >= 1 else 1


# ---- payment cycles --------------------------------------------------------------------------------------------
def payment_cycles(pay: pl.DataFrame, max_length: int | None = None, cap: int | None = None) -> list[tuple[str, ...]]:
    """The time-respecting payment cycles among `pay` (`src`, `dst`, `ts`), each distinct one once.

    A cycle is a tuple of account keys that starts at its lowest key: `("a", "b", "c")` is a pays b, b pays c, c pays a,
    each payment after the one before it. It has at most `max_length` accounts (`LAB_CYCLE_MAX_LENGTH`) and no account
    twice. At most `cap` cycles are found (`LAB_CYCLE_COUNT_CAP`); a list that long means "or more". The cycles come out in
    the same order for the same payments.

    The search starts from every account and, from there, takes the earliest payment that follows the one before it:
    the earliest choice leaves the most room for the rest, so it finds a cycle exactly when one exists. Only the
    payments inside one strongly connected group can be on a cycle, so the search is kept inside those.
    """
    s = get_settings()
    max_length = s.cycle_max_length if max_length is None else max_length
    cap = s.cycle_count_cap if cap is None else cap
    if pay.is_empty() or max_length < 2 or cap < 1:
        return []
    edges = (
        pay.filter(pl.col("src") != pl.col("dst"))
        .select("src", "dst", us=pl.col("ts").dt.epoch("us"))
        .sort("us")
        .group_by("src", "dst", maintain_order=True)
        .agg(pl.col("us"))
    )
    graph = nx.DiGraph()
    graph.add_edges_from(zip(edges["src"], edges["dst"], strict=True))
    group = {a: i for i, part in enumerate(nx.strongly_connected_components(graph)) if len(part) > 1 for a in part}
    adj: dict[str, dict[str, list[int]]] = {}
    for src, dst, times in edges.iter_rows():
        if src in group and group.get(dst) == group[src]:
            adj.setdefault(src, {})[dst] = times
    order = {a: sorted(nexts) for a, nexts in adj.items()}
    found: dict[tuple[str, ...], None] = {}

    def walk(path: tuple[str, ...], after: int) -> bool:
        """Extend `path` by a payment after time `after`; False once `cap` cycles are found."""
        here = path[-1]
        for nxt in order.get(here, ()):
            times = adj[here][nxt]
            i = bisect_right(times, after)
            if i == len(times):
                continue
            if nxt == path[0]:  # the money is back where it started
                found.setdefault(_rotated(path), None)
                if len(found) >= cap:
                    return False
            elif nxt not in path and len(path) < max_length:
                if not walk((*path, nxt), times[i]):
                    return False
        return True

    for start in sorted(adj):
        for first in order[start]:
            if not walk((start, first), adj[start][first][0]):
                return list(found)
    return list(found)


def _rotated(path: tuple[str, ...]) -> tuple[str, ...]:
    i = path.index(min(path))
    return path[i:] + path[:i]


def cycles_by_group(pay: pl.DataFrame, group_of: Mapping[str, int]) -> dict[int, list[tuple[str, ...]]]:
    """`payment_cycles` inside each group: `pay` (`src`, `dst`, `ts`) is cut to the payments between two accounts of the
    same group (`group_of`: account key to group number), and each group's cycles are found among them. A group with no
    cycle is not in the answer."""
    if pay.is_empty() or not group_of:
        return {}
    groups = pl.DataFrame({"account": list(group_of), "group": list(group_of.values())}, schema={"account": pl.Utf8, "group": pl.Int64})
    inside = (
        pay.select("src", "dst", "ts")
        .filter(pl.col("src") != pl.col("dst"))
        .join(groups.rename({"account": "src", "group": "g_src"}), on="src")
        .join(groups.rename({"account": "dst", "group": "g_dst"}), on="dst")
        .filter(pl.col("g_src") == pl.col("g_dst"))
    )
    out: dict[int, list[tuple[str, ...]]] = {}
    for (g,), edges in inside.partition_by("g_src", as_dict=True).items():
        cycles = payment_cycles(edges)
        if cycles:
            out[int(g)] = cycles
    return out
