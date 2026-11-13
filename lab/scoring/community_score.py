"""The community score (page 04, level three; decision 36): a community's score from its members and its shape.

`score_communities(run, communities, scores, policy)` gives each community five parts, each in [0, 1], named as the
policy's `community.weights`. The score is their weighted mean with those weights, so it is in [0, 1] too. Every
number is read from the run store or from the account scores; nothing is guessed.

| Part | How it is worked out |
|---|---|
| `member_risk` | The average of two numbers: the share of members at or above the policy's `alert_threshold`, and the members' mean `risk_score` (an account with no score counts 0). The share says how many; the mean says how hot. `MEMBER_RISK_SHARE` sets the mix |
| `shared_identifiers` | The share of members that share a device, email or address with another member. An identifier used by more than `LAB_SHARED_MAX` accounts of the slice counts for nothing, as in the account graph |
| `cycles` | The share of the money paid between members that moves along a payment that lies on a cycle of at most `CYCLE_MAX_LENGTH` accounts (a pays b, b pays a is a cycle of two): money that comes back to where it started |
| `new_accounts` | The share of members opened within `LAB_NEW_ACCOUNT_DAYS` of the newest payment in the slice |
| `speed` | The share of members that sent a payment out within `LAB_PASS_THROUGH_HOURS` after one came in |

The last two are the first pass's own definitions (`lab/slice/summaries.py`): a member is flagged here for what its
template summary already says. A community smaller than the policy's `min_size` is not scored, and an account may be
in only one community. A part the policy gives no weight is still worked out and shown in `score_parts`, and adds
nothing to the score; a weight that names no part is an error.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

import networkx as nx
import polars as pl

from lab.adapters import _read
from lab.common import canonical_json, community_key, get_logger, iso_utc
from lab.contracts.factors import Policy
from lab.scoring.policy import _EDGE
from lab.settings import get_settings
from lab.slice.summaries import _accounts, _new_accounts, _pass_through

if TYPE_CHECKING:
    from lab.runs import Run

log = get_logger("scoring.community_score")

PARTS = ("member_risk", "shared_identifiers", "cycles", "new_accounts", "speed")

# Two numbers of the score that no policy or setting holds yet, named here so they can be moved or promoted.
MEMBER_RISK_SHARE = 0.5  # the mix in `member_risk`: 0.5 is the plain average of the share and the mean
CYCLE_MAX_LENGTH = 6  # the longest payment cycle looked for, in accounts: the fixture's ring has six

SCHEMA = {
    "community_key": pl.Utf8,
    "member_keys": pl.List(pl.Utf8),
    "score": pl.Float64,
    "size": pl.Int64,
    "score_parts": pl.Utf8,
    "run_id": pl.Utf8,
    "created_at": pl.Utf8,
}


def score_communities(
    run: Run, communities: Iterable[Iterable[str]], scores: pl.DataFrame, policy: Policy
) -> pl.DataFrame:
    """One row per community at or above `policy.community.min_size`, best score first: the staged columns.

    `communities` are sets of account keys, disjoint (`find_communities` makes them so). `scores` is the account
    scores of the slice, with the mapping's account key and `risk_score`; its accounts, with every member, are the
    scope that payments and identifiers are read for. Columns: `community_key` (`community_key(run.id, members)`),
    `member_keys` (sorted), `score`, `size`, `score_parts` (a JSON string of the five parts), `run_id`, `created_at`.
    """
    cp = policy.community
    unknown = sorted(set(cp.weights) - set(PARTS))
    if unknown:
        raise ValueError(f"community.weights names {unknown}; the parts are {list(PARTS)}")
    groups = sorted(
        ({str(m) for m in c} for c in communities if len({str(m) for m in c}) >= cp.min_size),
        key=lambda c: min(c),
    )
    if not groups:
        return pl.DataFrame(schema=SCHEMA)
    member_of = {m: i for i, g in enumerate(groups) for m in g}
    if len(member_of) != sum(len(g) for g in groups):
        raise ValueError("an account is in two communities; a community's members must not overlap")

    key = run.recipe.graph.account_key
    scope = list(dict.fromkeys([*scores[key].to_list(), *member_of]))
    parts = {
        "member_risk": _member_risk(scores, key, groups, policy.alert_threshold),
        **_shape_parts(run, scope, groups, member_of),
    }

    total = sum(cp.weights.values())
    now = iso_utc()
    rows = []
    for i, members in enumerate(groups):
        mine = {p: _unit(parts[p][i]) for p in PARTS}
        score = sum(w * mine[p] for p, w in cp.weights.items()) / total if total > 0 else 0.0
        rows.append({
            "community_key": community_key(run.id, members), "member_keys": sorted(members), "score": _unit(score),
            "size": len(members), "score_parts": canonical_json(mine), "run_id": run.id, "created_at": now,
        })
    return pl.DataFrame(rows, schema=SCHEMA).sort(
        pl.col("score"), pl.col("size"), pl.col("community_key"), descending=[True, True, False]
    )


def _unit(x: float) -> float:
    """A part is in [0, 1]; float noise just outside it is rounding."""
    return min(1.0, max(0.0, float(x)))


def _member_risk(scores: pl.DataFrame, key: str, groups: list[set[str]], alert_threshold: float) -> list[float]:
    risk = dict(zip(scores[key], scores["risk_score"].fill_null(0.0), strict=True))
    out = []
    for members in groups:
        values = [risk.get(m, 0.0) for m in members]
        share = sum(v >= alert_threshold - _EDGE for v in values) / len(values)
        mean = sum(values) / len(values)
        out.append(MEMBER_RISK_SHARE * share + (1 - MEMBER_RISK_SHARE) * mean)
    return out


def _shape_parts(run: Run, scope: list[str], groups: list[set[str]], member_of: dict[str, int]) -> dict[str, list[float]]:
    """The four parts that come from the run store: identifiers, cycles, new accounts, speed."""
    pay = _read.payments(run, scope, detail=True)
    pay = pay.with_columns(ts=_read.utc_naive(pay["ts"]))
    acc = _accounts(run, scope)
    as_of = pay["ts"].max() if pay.height else None
    new = _new_accounts(acc, as_of)
    fast = set(_pass_through(pay)["key"]) if pay.height else set()
    n = [len(g) for g in groups]

    covered = _identifier_cover(_read.identifiers(run, scope), member_of)
    on_cycles = _cycle_share(pay, member_of, len(groups))
    return {
        "shared_identifiers": [covered.get(i, 0) / n[i] for i in range(len(groups))],
        "cycles": on_cycles,
        "new_accounts": [len(g & new) / n[i] for i, g in enumerate(groups)],
        "speed": [len(g & fast) / n[i] for i, g in enumerate(groups)],
    }


def _identifier_cover(links: pl.DataFrame, member_of: dict[str, int]) -> dict[int, int]:
    """Community index -> how many of its members share an identifier with another member."""
    if links.is_empty() or not member_of:
        return {}
    node = ["kind", "label", "identifier"]
    sizes = links.group_by(node).len(name="used_by")
    usable = links.join(sizes.filter(pl.col("used_by") <= get_settings().shared_max), on=node)
    community = pl.DataFrame({"account": list(member_of), "community": list(member_of.values())})
    inside = usable.join(community, on="account")
    shared = inside.group_by("community", *node).agg(members=pl.col("account").n_unique()).filter(pl.col("members") >= 2)
    covered = inside.join(shared.select("community", *node), on=["community", *node])
    counts = covered.group_by("community").agg(n=pl.col("account").n_unique())
    return dict(counts.iter_rows())


def _cycle_share(pay: pl.DataFrame, member_of: dict[str, int], count: int) -> list[float]:
    """Per community, the share of the money paid between its members that moves along an edge on a short cycle.

    A payment from u to v lies on a cycle of at most L accounts exactly when v reaches u in at most L - 1 payments.
    That is a bounded breadth-first search from each payee, so the work stays small however many cycles there are.
    """
    out = [0.0] * count
    if pay.is_empty():
        return out
    community = pl.DataFrame({"account": list(member_of), "community": list(member_of.values())})
    inside = (
        pay.filter(pl.col("src") != pl.col("dst"))
        .join(community.rename({"account": "src", "community": "c_src"}), on="src")
        .join(community.rename({"account": "dst", "community": "c_dst"}), on="dst")
        .filter(pl.col("c_src") == pl.col("c_dst"))
        .group_by("c_src", "src", "dst")
        .agg(money=pl.col("amount").fill_null(0.0).sum())
    )
    reach = CYCLE_MAX_LENGTH - 1
    for (index,), edges in inside.partition_by("c_src", as_dict=True).items():
        money = edges["money"].sum()
        if money <= 0:
            continue
        D = nx.DiGraph()
        D.add_edges_from(zip(edges["src"], edges["dst"], strict=True))
        back: dict[str, dict[str, int]] = {}  # payee -> the accounts it reaches within `reach` payments
        on_cycle = 0.0
        for u, v, m in edges.select("src", "dst", "money").iter_rows():
            if v not in back:
                back[v] = nx.single_source_shortest_path_length(D, v, cutoff=reach)
            if u in back[v]:
                on_cycle += m
        out[index] = on_cycle / money
    return out
