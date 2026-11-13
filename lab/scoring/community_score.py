"""The community score (page 04, level three; decision 36): a community's score from its members and its shape.

`score_communities(run, communities, scores, policy)` gives each community five parts, each in [0, 1], named as the
policy's `community.weights`. The policy's `community.combine` says how they make the score, which is in [0, 1] either
way: `noisy_or` (decision 45, shipped) is 1 - Π(1 - wᵢ·partᵢ), so a part that is 0 changes nothing and each part alone
can raise the score up to its weight; `weighted_mean` (decision 36) divides by the total weight, so an absent part pulls
the score down. Every number is read from the run store or from the account scores; nothing is guessed.

| Part | How it is worked out |
|---|---|
| `member_risk` | The mix of two numbers: the share of members at or above the policy's `alert_threshold`, and the members' mean `risk_score` (an account with no score counts 0). The share says how many; the mean says how hot. The policy's `community.member_risk_share` sets the mix (0.5 is the plain average) |
| `shared_identifiers` | The share of members that share a device, email or address with another member. An identifier used by more than `LAB_SHARED_MAX` accounts of the slice counts for nothing, as it does in forming the communities |
| `cycles` | The share of the money paid between members that moves along a payment pair that lies on a time-respecting cycle of at most `LAB_CYCLE_MAX_LENGTH` accounts (`signals.payment_cycles`: each payment after the one before it; a pays b, then b pays a is a cycle of two): money that comes back to where it started, in the order it moved. Cycles are counted up to `LAB_CYCLE_COUNT_CAP` per community |
| `new_accounts` | The share of members opened within `LAB_NEW_ACCOUNT_DAYS` of the newest payment in the slice (`signals.new_accounts`) |
| `speed` | The share of members the `in_and_out_within_a_day` factor fires on (`signals.in_and_out_within_a_day`): money in and out of similar size within a day, by the factor's own query and threshold |

The last two are the first pass's own definitions: a member is flagged here for what its template summary already says,
and the three of them, the factor, the phrase and this part, share one definition. A community smaller than the policy's
`min_size` is not scored, and an account may be in only one community. A part the policy gives no weight is still worked
out and shown in `score_parts`, and adds nothing to the score; a weight that names no part is an error.

`score_parts` also names how the community formed (`formed_by_identifier`: 1 for a community made of accounts that share
an identifier, 0 for one made of payments, fast money included), so a reader of the staged node can tell the kinds apart. It is not a part of
the score and takes no weight. The same fact is the `formed` column of `communities.parquet`, which is not staged.
"""
from __future__ import annotations

from collections.abc import Iterable
from math import fsum
from typing import TYPE_CHECKING

import polars as pl

from lab.adapters import reads
from lab.common import canonical_json, community_key, get_logger, iso_utc
from lab.contracts.factors import Policy
from lab.scoring import signals
from lab.scoring.communities import IDENTIFIER, PAYMENT
from lab.scoring.factors import FactorRun
from lab.scoring.policy import _EDGE
from lab.settings import get_settings

if TYPE_CHECKING:
    from lab.runs import Run

log = get_logger("scoring.community_score")

PARTS = ("member_risk", "shared_identifiers", "cycles", "new_accounts", "speed")
FORMED_PART = "formed_by_identifier"  # in `score_parts` only: not a part of the score, and it takes no weight

SCHEMA = {
    "community_key": pl.Utf8,
    "member_keys": pl.List(pl.Utf8),
    "score": pl.Float64,
    "size": pl.Int64,
    "score_parts": pl.Utf8,
    "run_id": pl.Utf8,
    "created_at": pl.Utf8,
    "formed": pl.Utf8,  # "identifier" or "payment": in communities.parquet, not a staged column (`for_stage` drops it)
}


def score_communities(
    run: Run, communities: Iterable[Iterable[str]], scores: pl.DataFrame, policy: Policy,
    factor_run: FactorRun | None = None,
) -> pl.DataFrame:
    """One row per community at or above `policy.community.min_size`, best score first: the staged columns, and `formed`.

    `communities` are sets of account keys, disjoint (`find_communities` makes them so, as `Community` sets that say
    how each formed; a plain set is taken to have formed by payment). `scores` is the account scores of the slice, with
    the mapping's account key and `risk_score`; its accounts, with every member, are the scope that payments and
    identifiers are read for. `factor_run`, when scoring has one, supplies the `in_and_out_within_a_day` factor's rows so
    the query is not run twice (`signals.in_and_out_within_a_day`). Columns: `community_key` (`community_key(run.id,
    members)`), `member_keys` (sorted), `score`, `size`, `score_parts` (a JSON string of the five parts and
    `formed_by_identifier`), `run_id`, `created_at`, `formed`.
    """
    cp = policy.community
    unknown = sorted(set(cp.weights) - set(PARTS))
    if unknown:
        raise ValueError(f"community.weights names {unknown}; the parts are {list(PARTS)}")
    if cp.combine == "noisy_or":
        bad = [f"{p} ({w:g})" for p, w in cp.weights.items() if not 0 <= w <= 1]
        if bad:
            raise ValueError(f"community.combine: noisy_or needs every weight in [0, 1]; out of range: {', '.join(bad)}")
    kept = [({str(m) for m in c}, getattr(c, "formed", PAYMENT)) for c in communities]
    kept = sorted((k for k in kept if len(k[0]) >= cp.min_size), key=lambda k: min(k[0]))
    if not kept:
        return pl.DataFrame(schema=SCHEMA)
    groups = [members for members, _ in kept]
    member_of = {m: i for i, g in enumerate(groups) for m in g}
    if len(member_of) != sum(len(g) for g in groups):
        raise ValueError("an account is in two communities; a community's members must not overlap")

    key = run.recipe.graph.account_key
    scope = list(dict.fromkeys([*scores[key].to_list(), *member_of]))
    parts = {
        "member_risk": _member_risk(scores, key, groups, policy.alert_threshold, cp.member_risk_share),
        **_shape_parts(run, scope, groups, member_of, factor_run),
    }

    total = sum(cp.weights.values())
    now = iso_utc()
    rows = []
    for i, (members, formed) in enumerate(kept):
        mine = {p: _unit(parts[p][i]) for p in PARTS}
        score = _combine(cp.combine, cp.weights, mine, total)
        rows.append({
            "community_key": community_key(run.id, members), "member_keys": sorted(members), "score": _unit(score),
            "size": len(members), "score_parts": canonical_json({**mine, FORMED_PART: float(formed == IDENTIFIER)}),
            "run_id": run.id, "created_at": now, "formed": formed,
        })
    return pl.DataFrame(rows, schema=SCHEMA).sort(
        pl.col("score"), pl.col("size"), pl.col("community_key"), descending=[True, True, False]
    )


def _combine(combine: str, weights: dict[str, float], parts: dict[str, float], total: float) -> float:
    if combine == "noisy_or":
        left = 1.0
        for p, w in weights.items():
            left *= 1.0 - w * parts[p]
        return 1.0 - left
    return sum(w * parts[p] for p, w in weights.items()) / total if total > 0 else 0.0


def _unit(x: float) -> float:
    """A part is in [0, 1]; float noise just outside it is rounding."""
    return min(1.0, max(0.0, float(x)))


def _member_risk(
    scores: pl.DataFrame, key: str, groups: list[set[str]], alert_threshold: float, member_risk_share: float
) -> list[float]:
    risk = dict(zip(scores[key], scores["risk_score"].fill_null(0.0), strict=True))
    out = []
    for members in groups:
        values = [risk.get(m, 0.0) for m in members]
        share = sum(v >= alert_threshold - _EDGE for v in values) / len(values)
        mean = fsum(values) / len(values)  # the same to the last digit whatever order a set walks its members in
        out.append(member_risk_share * share + (1 - member_risk_share) * mean)
    return out


def _shape_parts(
    run: Run, scope: list[str], groups: list[set[str]], member_of: dict[str, int], factor_run: FactorRun | None
) -> dict[str, list[float]]:
    """The four parts that come from the run store: identifiers, cycles, new accounts, speed."""
    pay = reads.payments(run, scope, detail=True)
    pay = pay.with_columns(ts=reads.utc_naive(pay["ts"]))
    new = signals.new_accounts(signals.account_opened(run, scope), pay)
    fast = set(signals.in_and_out_within_a_day(run, scope, factor_run)["key"])
    n = [len(g) for g in groups]

    covered = _identifier_cover(reads.identifiers(run, scope), member_of)
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
    """Per community, the share of the money paid between its members that moves between two accounts that lie on a
    time-respecting cycle together: along a payment pair that is one hop of a cycle `signals.payment_cycles` found."""
    out = [0.0] * count
    cycles = signals.cycles_by_group(pay, member_of)
    if not cycles:
        return out
    community = pl.DataFrame({"account": list(member_of), "community": list(member_of.values())})
    inside = (
        pay.filter(pl.col("src") != pl.col("dst"))
        .join(community.rename({"account": "src", "community": "c_src"}), on="src")
        .join(community.rename({"account": "dst", "community": "c_dst"}), on="dst")
        .filter(pl.col("c_src") == pl.col("c_dst"))
        .select("c_src", "src", "dst", pl.col("amount").fill_null(0.0))
    )
    paid: dict[tuple[int, str, str], list[float]] = {}
    for g, a, b, amount in inside.iter_rows():
        paid.setdefault((g, a, b), []).append(amount)
    hops = {(g, a, b) for g, found in cycles.items() for c in found for a, b in zip(c, (*c[1:], c[0]), strict=True)}
    # exact sums, so the share is the same however the payments came in
    total: dict[int, list[float]] = {}
    on_cycle: dict[int, list[float]] = {}
    for (g, a, b), amounts in paid.items():
        money = fsum(amounts)
        total.setdefault(g, []).append(money)
        if (g, a, b) in hops:
            on_cycle.setdefault(g, []).append(money)
    for g, moneys in on_cycle.items():
        if (everything := fsum(total[g])) > 0:
            out[g] = fsum(moneys) / everything
    return out
