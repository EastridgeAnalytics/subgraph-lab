"""The account template summary (page 02): a sentence of fixed phrases, built from the account's own data.

The first pass writes it to `lab_summary` and the full-text index `account_summary` takes it, so a search can match the
phrases. It is the template, not a written summary: no model, no key, the same text every run. Every number in it
is read from the run store. The phrases:

| Phrase | When |
|---|---|
| `new account` | opened no more than `LAB_NEW_ACCOUNT_DAYS` before the newest payment in the store |
| `money in and out within a day (N times)` | The `in_and_out_within_a_day` factor fires on the account: N outgoing payments each came within 24 hours after an incoming payment of about the same amount (`lab/scoring/signals.py` runs the factor's own query, so this and the factor and the community's `speed` agree) |
| `shares a device with N other accounts` | one clause per identifier kind: device, email, address, phone, or the label |
| `shares a device with 50 or more other accounts` | the same, when an identifier is shared by more than `LAB_SHARED_MAX` |
| `N payments just under 10,000` | payments, in or out, from 95 percent of 10,000 up to but not including it |
| `N payments in, M out` | always |

Full-text has no stemming, so the words are fixed: "account" and "accounts" are different terms to the index.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from lab.adapters import reads
from lab.scoring import signals
from lab.settings import get_settings

if TYPE_CHECKING:
    from lab.runs import Run

# The phrases' thresholds are settings (page 07): LAB_NEW_ACCOUNT_DAYS, LAB_ROUND_THRESHOLD, LAB_JUST_UNDER. "Money in and
# out within a day" is the factor's own definition (`signals.in_and_out_within_a_day`), so its 24 hours live in the factor's query.
NEW_ACCOUNT = "new account"
PASS_THROUGH = "money in and out within a day"


def account_summaries(run: Run, keys: list[str] | None = None) -> pl.DataFrame:
    """One row per account in scope: `key` and `summary`, sorted by key. `keys` None is the whole run store.

    Payments and shared identifiers are counted among the accounts in scope, so a slice's summaries describe the
    slice. The first pass runs on the whole store.
    """
    g = run.recipe.graph
    acc = signals.account_opened(run, keys)
    pay = reads.payments(run, keys, detail=True)
    pay = pay.with_columns(ts=reads.utc_naive(pay["ts"]))

    new = signals.new_accounts(acc, pay)
    n_in = _count(pay, "dst", "n_in")
    n_out = _count(pay, "src", "n_out")
    passes = signals.in_and_out_within_a_day(run, keys).rename({"count": "passes"})
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
            clauses.append(f"{r['under']} {_plural(r['under'], 'payment')} just under {get_settings().round_threshold:,.0f}")
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


def _count(pay: pl.DataFrame, side: str, name: str) -> pl.DataFrame:
    return pay.group_by(side).len(name=name).rename({side: "key"})


def _just_under(pay: pl.DataFrame) -> pl.DataFrame:
    """`key` and `under`: the payments, in or out, just under the round threshold, per account."""
    top = get_settings().round_threshold
    low = top * (1 - get_settings().just_under)
    near = pay.filter((pl.col("amount") >= low) & (pl.col("amount") < top))
    both = pl.concat([near.select(key="src"), near.select(key="dst")])
    return both.group_by("key").len(name="under")


def _shared(run: Run, keys: list[str] | None) -> dict[str, list[str]]:
    """account key -> its "shares a device with N other accounts" clauses, in the mapping's order."""
    g = run.recipe.graph
    cap = get_settings().shared_max
    links = reads.identifiers(run, keys)
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
