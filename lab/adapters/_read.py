"""Cypher to Polars frames: how every adapter reads the run store.

Labels, relationship types and property names come from the recipe's `GraphMapping`, never from here. A slice is a
list of account keys; each reader takes `keys` (None means the whole run store).

A long key list does not go into one `IN $keys`: in Relate 1.0.1 the lookup slows with the square of the list
(an account lookup took 0.02 s for 500 keys and 4.6 s for 5,000, in track 1c's check on a synthetic store of 20,000
accounts and 100,000 payments; to re-measure on the 1M seed). The readers send `KEY_BATCH` keys at a time,
restrict the account that a relationship leaves from, and keep only the ones that land inside the slice
in Polars, where a semi join is linear.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

import polars as pl

from lab.contracts.graph import GraphMapping, IdentifierLink

if TYPE_CHECKING:
    from lab.runs import Run

KEY_BATCH = 500  # keys per Cypher statement; see the module note


def q(name: str) -> str:
    """A label, type or property name as a Cypher identifier."""
    return "`" + name.replace("`", "``") + "`"


def key_batches(keys: list[str] | None) -> Iterator[list[str] | None]:
    """The key lists to send, one statement each: the distinct keys in batches, or one None for no restriction."""
    if keys is None:
        yield None
        return
    distinct = list(dict.fromkeys(keys))
    for i in range(0, len(distinct), KEY_BATCH):
        yield distinct[i : i + KEY_BATCH]


def _read(run: Run, query: str, keys: list[str] | None, columns: list[str]) -> pl.DataFrame:
    """The statement run once per batch of keys and stacked. `query` filters on `$keys` when `keys` is given.

    A statement that returns no rows hands back columns with no type, so the stack lets those take the type of
    the others.
    """
    frames = [run.table(query, {} if batch is None else {"keys": batch}) for batch in key_batches(keys)]
    if not frames:  # keys == []
        return pl.DataFrame({c: pl.Series(c, [], dtype=pl.Null) for c in columns})
    return pl.concat(frames, how="vertical_relaxed")


def _where(var: str, key: str, keys: list[str] | None) -> str:
    return "" if keys is None else f"WHERE {var}.{q(key)} IN $keys"


def inside(df: pl.DataFrame, col: str, keys: list[str] | None) -> pl.DataFrame:
    """The rows whose `col` is one of the slice's keys; every row when there is no slice."""
    if keys is None:
        return df
    return df.join(pl.DataFrame({col: pl.Series(list(dict.fromkeys(keys)), dtype=pl.Utf8)}), on=col, how="semi")


def accounts(run: Run, keys: list[str] | None = None) -> pl.DataFrame:
    """One column, `key` (str): the accounts in scope, sorted. A key that is not in the store is not returned."""
    g: GraphMapping = run.recipe.graph
    query = f"MATCH (a:{q(g.account_label)}) {_where('a', g.account_key, keys)} RETURN a.{q(g.account_key)} AS key"
    df = _read(run, query, keys, ["key"])
    return df.select(pl.col("key").cast(pl.Utf8)).drop_nulls().unique().sort("key")


def payments(run: Run, keys: list[str] | None = None, *, detail: bool = True) -> pl.DataFrame:
    """The payments between accounts in scope: `src`, `dst` and, with `detail`, `tx_id`, `amount` (f64), `ts`.

    Without `detail` the frame is the distinct (src, dst) pairs only, which is all a graph needs, and a long
    payment history collapses to its pairs before it crosses into Python.
    """
    g = run.recipe.graph
    s, d = f"s.{q(g.account_key)}", f"d.{q(g.account_key)}"
    match = f"MATCH (s:{q(g.account_label)})-[t:{q(g.payment_type)}]->(d:{q(g.account_label)})"
    if detail:
        cols = ["src", "dst", "tx_id", "amount", "ts"]
        ret = (f"RETURN {s} AS src, {d} AS dst, t.{q(g.payment_key)} AS tx_id, "
               f"t.{q(g.payment_amount)} AS amount, t.{q(g.payment_time)} AS ts")
    else:
        cols = ["src", "dst"]
        ret = f"RETURN DISTINCT {s} AS src, {d} AS dst"
    df = _read(run, f"{match} {_where('s', g.account_key, keys)} {ret}", keys, cols)
    df = df.with_columns(pl.col("src").cast(pl.Utf8), pl.col("dst").cast(pl.Utf8))
    if detail:
        df = df.with_columns(pl.col("tx_id").cast(pl.Utf8), pl.col("amount").cast(pl.Float64))
    return inside(df, "dst", keys)


def outflow(run: Run, keys: list[str] | None = None) -> pl.DataFrame:
    """Every payment out of an account in scope, wherever it went: `unique_id`, `ts`, `amount` (f64)."""
    g = run.recipe.graph
    query = (f"MATCH (s:{q(g.account_label)})-[t:{q(g.payment_type)}]->() {_where('s', g.account_key, keys)} "
             f"RETURN s.{q(g.account_key)} AS unique_id, t.{q(g.payment_time)} AS ts, t.{q(g.payment_amount)} AS amount")
    df = _read(run, query, keys, ["unique_id", "ts", "amount"])
    return df.with_columns(pl.col("unique_id").cast(pl.Utf8), pl.col("amount").cast(pl.Float64))


def identifiers(run: Run, keys: list[str] | None = None) -> pl.DataFrame:
    """The account-to-identifier links of every mapped kind: `account`, `identifier`, `label`, `kind` (all str).

    One row per account and identifier node. `label` and `identifier` together name the node.
    """
    g = run.recipe.graph
    frames = [_identifier_links(run, g, link, keys) for link in g.identifiers]
    if not frames:
        return pl.DataFrame(schema={"account": pl.Utf8, "identifier": pl.Utf8, "label": pl.Utf8, "kind": pl.Utf8})
    return pl.concat(frames).unique().sort("account", "label", "identifier")


def _identifier_links(run: Run, g: GraphMapping, link: IdentifierLink, keys: list[str] | None) -> pl.DataFrame:
    query = (f"MATCH (a:{q(g.account_label)})-[:{q(link.rel_type)}]->(i:{q(link.label)}) "
             f"{_where('a', g.account_key, keys)} "
             f"RETURN DISTINCT a.{q(g.account_key)} AS account, i.{q(link.key)} AS identifier")
    df = _read(run, query, keys, ["account", "identifier"])
    return df.select(
        pl.col("account").cast(pl.Utf8),
        pl.col("identifier").cast(pl.Utf8),
        pl.lit(link.label).alias("label"),
        pl.lit(link.kind).alias("kind"),
    ).drop_nulls()


def utc_naive(ts: pl.Series) -> pl.Series:
    """A time column as a naive datetime in UTC. A naive one is taken to be UTC already."""
    dtype = ts.dtype
    if dtype == pl.Date:
        return ts.cast(pl.Datetime("us"))
    if isinstance(dtype, pl.Datetime):
        if dtype.time_zone is not None:
            return ts.dt.convert_time_zone("UTC").dt.replace_time_zone(None)
        return ts
    if dtype == pl.Null:  # no rows
        return ts.cast(pl.Datetime("us"))
    raise TypeError(f"payment time must be a date or datetime property, got {dtype}")
