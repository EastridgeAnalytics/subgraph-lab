"""The account template summary (page 02): a sentence of fixed phrases, built from the account's own data.

The first pass writes it to `lab_summary` and the full-text index `account_summary` takes it, so a search can match the
phrases. It is the template, not a written summary: no model, no key, the same text every run. Every number in it
is read from the run store. The phrases:

| Phrase | When |
|---|---|
| `new account` | opened no more than `NEW_ACCOUNT_DAYS` before the newest payment in the store |
| `money in and out within a day (N times)` | N payments out each came within 24 hours after a payment in |
| `shares a device with N other accounts` | one clause per identifier kind: device, email, address, phone, or the label |
| `shares a device with 50 or more other accounts` | the same, when an identifier is shared by more than `LAB_SHARED_MAX` |
| `N payments just under 10,000` | payments, in or out, from 95 percent of 10,000 up to but not including it |
| `N payments in, M out` | always |

Full-text has no stemming, so the words are fixed: "account" and "accounts" are different terms to the index.
"""
from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import polars as pl

from lab.adapters import _read
from lab.roundtrip.queries import ident
from lab.settings import get_settings

if TYPE_CHECKING:
    from lab.runs import Run

# The thresholds behind the phrases. Judgment numbers: to move into settings if the project owner wants them tuned.
NEW_ACCOUNT_DAYS = 60  # "new account": opened this many days or fewer before the newest payment in the store
PASS_THROUGH_HOURS = 24  # "money in and out within a day": an outgoing payment this soon after an incoming one
ROUND_THRESHOLD = 10_000  # "just under 10,000"
JUST_UNDER = 0.05  # just under: within this share below the round threshold (9,500 up to 10,000)

NEW_ACCOUNT = "new account"
PASS_THROUGH = "money in and out within a day"


def account_summaries(run: Run, keys: list[str] | None = None) -> pl.DataFrame:
    """One row per account in scope: `key` and `summary`, sorted by key. `keys` None is the whole run store.

    Payments and shared identifiers are counted among the accounts in scope, so a slice's summaries describe the
    slice. The first pass runs on the whole store.
    """
    g = run.recipe.graph
    acc = _accounts(run, keys)
    pay = _read.payments(run, keys, detail=True)
    pay = pay.with_columns(ts=_read.utc_naive(pay["ts"]))

    as_of = pay["ts"].max() if pay.height else None
    new = _new_accounts(acc, as_of)
    n_in = _count(pay, "dst", "n_in")
    n_out = _count(pay, "src", "n_out")
    passes = _pass_through(pay)
    under = _just_under(pay)
    shared = _shared(run, keys)

    table = (
        acc.join(n_in, on="key", how="left")
        .join(n_out, on="key", how="left")
        .join(passes, on="key", how="left")
        .join(under, on="key", how="left")
        .with_columns(pl.col("n_in", "n_out", "passes", "under").fill_null(0))
    )
    out = []
    for r in table.iter_rows(named=True):
        key = r["key"]
        clauses = []
        if key in new:
            clauses.append(NEW_ACCOUNT)
        if r["passes"]:
            clauses.append(f"{PASS_THROUGH} ({r['passes']} {_plural(r['passes'], 'time')})")
        clauses += shared.get(key, [])
        if r["under"]:
            clauses.append(f"{r['under']} {_plural(r['under'], 'payment')} just under {ROUND_THRESHOLD:,}")
        clauses.append(f"{r['n_in']} {_plural(r['n_in'], 'payment')} in, {r['n_out']} out")
        opened = f" opened {r['opened'].date().isoformat()}" if r["opened"] is not None else ""
        out.append((key, f"{g.account_label} {key}{opened}: " + "; ".join(clauses) + "."))
    return pl.DataFrame(out, schema={"key": pl.Utf8, "summary": pl.Utf8}, orient="row")


def summary_body(summary: str) -> str:
    """The clauses of a summary, without its head ("Account A0011 opened 2026-09-23:")."""
    return summary.split(": ", 1)[1] if ": " in summary else summary


# ---- the pieces ----------------------------------------------------------------------------------------------
def _plural(n: int, noun: str) -> str:
    return noun if n == 1 else noun + "s"


def _accounts(run: Run, keys: list[str] | None) -> pl.DataFrame:
    """`key` and `opened` (a naive UTC datetime, null when the mapping has no opened property) of each account."""
    g = run.recipe.graph
    ret = f"a.{ident(g.account_key)} AS key"
    if g.account_opened:
        ret += f", a.{ident(g.account_opened)} AS opened"
    where = "" if keys is None else f"WHERE a.{ident(g.account_key)} IN $keys"
    query = f"MATCH (a:{ident(g.account_label)}) {where} RETURN {ret}"
    frames = [run.table(query, {} if b is None else {"keys": b}) for b in _read.key_batches(keys)]
    if not frames:  # keys == []
        return pl.DataFrame(schema={"key": pl.Utf8, "opened": pl.Datetime("us")})
    df = pl.concat(frames, how="vertical_relaxed")
    if "opened" not in df.columns:
        df = df.with_columns(opened=pl.lit(None, dtype=pl.Datetime("us")))
    df = df.select(pl.col("key").cast(pl.Utf8), opened=_read.utc_naive(df["opened"]))
    return df.drop_nulls("key").unique("key").sort("key")


def _new_accounts(acc: pl.DataFrame, as_of) -> set[str]:
    if as_of is None:
        return set()
    cutoff = as_of - timedelta(days=NEW_ACCOUNT_DAYS)
    return set(acc.filter(pl.col("opened") >= cutoff)["key"])


def _count(pay: pl.DataFrame, side: str, name: str) -> pl.DataFrame:
    return pay.group_by(side).len(name=name).rename({side: "key"})


def _pass_through(pay: pl.DataFrame) -> pl.DataFrame:
    """`key` and `passes`: the payments out that came within 24 hours after a payment in, per account."""
    ins = pay.select(key="dst", ts_in="ts").sort("ts_in")
    outs = pay.select(key="src", ts="ts").sort("ts")
    matched = outs.join_asof(
        ins, left_on="ts", right_on="ts_in", by="key", strategy="backward",
        tolerance=timedelta(hours=PASS_THROUGH_HOURS), check_sortedness=False,  # both sorted by time above
    )
    return matched.filter(pl.col("ts_in").is_not_null()).group_by("key").len(name="passes")


def _just_under(pay: pl.DataFrame) -> pl.DataFrame:
    """`key` and `under`: the payments, in or out, just under the round threshold, per account."""
    low = ROUND_THRESHOLD * (1 - JUST_UNDER)
    near = pay.filter((pl.col("amount") >= low) & (pl.col("amount") < ROUND_THRESHOLD))
    both = pl.concat([near.select(key="src"), near.select(key="dst")])
    return both.group_by("key").len(name="under")


def _shared(run: Run, keys: list[str] | None) -> dict[str, list[str]]:
    """account key -> its "shares a device with N other accounts" clauses, in the mapping's order."""
    g = run.recipe.graph
    cap = get_settings().shared_max
    links = _read.identifiers(run, keys)
    if links.is_empty():
        return {}
    noun = pl.when(pl.col("kind") == "other").then(pl.col("label").str.to_lowercase()).otherwise(pl.col("kind"))
    links = links.with_columns(noun=noun)
    node = ["label", "identifier"]
    sizes = links.group_by(node).len(name="size")
    links = links.join(sizes, on=node)

    # Exact distinct counts for identifiers that few accounts share; a hub is only said to be at least `cap`.
    small = links.filter(pl.col("size") <= cap)
    pairs = (
        small.join(small, on=node, suffix="_b")
        .filter(pl.col("account") != pl.col("account_b"))
        .select("account", "noun", other="account_b")
        .unique()
        .group_by("account", "noun")
        .len(name="n")
    )
    hubs = links.filter(pl.col("size") > cap).select("account", "noun").unique().with_columns(hub=pl.lit(True))
    both = pairs.join(hubs, on=["account", "noun"], how="full", coalesce=True).with_columns(
        pl.col("n").fill_null(0), pl.col("hub").fill_null(False)
    )
    order = {n: i for i, n in enumerate(dict.fromkeys(
        (link.label.lower() if link.kind == "other" else link.kind) for link in g.identifiers))}
    both = both.with_columns(rank=pl.col("noun").replace_strict(order, default=len(order), return_dtype=pl.Int64))
    out: dict[str, list[str]] = {}
    for r in both.sort("account", "rank", "noun").iter_rows(named=True):
        article = "an" if r["noun"][0] in "aeiou" else "a"
        if r["hub"]:
            clause = f"shares {article} {r['noun']} with {cap} or more other accounts"
        elif r["n"]:
            clause = f"shares {article} {r['noun']} with {r['n']} other {_plural(r['n'], 'account')}"
        else:
            continue
        out.setdefault(r["account"], []).append(clause)
    return out
