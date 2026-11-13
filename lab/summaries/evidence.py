"""The evidence object behind every summary (page 05, R16): one per community and one per alert.

`build_evidence(run, subject, key)` reads the run store (payments, shared identifiers), the account scores, the factor
results and `artifacts/communities.parquet`, and returns an `Evidence` that has passed its JSON schema. Keys and
totals only, never names. `build_all` builds many in one pass, which is what `attach_summaries` does; every number in
an object is worked out here, so a writer has nothing to invent.

| Part | Community | Alert (one member) |
|---|---|---|
| `member_keys`, `member_count` | the community's accounts, sorted | the alert's account |
| `score`, `score_parts` | the community score and its five parts | the account's `risk_score`; parts `rules` and `model` |
| `shared_identifiers` | identifiers two or more members use, with how many members do; at most 10, most shared first | identifiers the account shares with others, with how many accounts of the slice use each, this one included |
| `money` | `inflow` from accounts outside, `outflow` to accounts outside, `internal` between members; `cycles`; first and last payment and the span in hours | the same, with `internal` 0 and `cycles` 0 |
| `factors` | every factor that fired (score above 0) on any member: how many members, and up to 10 evidence ids | the factors that fired on the account, with `accounts` 1 |
| `top_members` | the five highest account scores, keys only | the account itself |
| `comparison` | `communities_in_slice`, `score_rank` among the communities (1 is highest, ties share a rank), the median community size and score | `communities_in_slice`; `score_rank` among the slice's alerts; the median community size; the median alert score |

Rules the numbers follow. An identifier used by more than `LAB_SHARED_MAX` accounts of the slice is left out, as the
community score leaves it out. `cycles` is the number of distinct simple cycles of at most `CYCLE_MAX_LENGTH`
accounts among the payments between members (a pays b and b pays a is one), counted up to `CYCLE_COUNT_CAP`, so a
community with that many reports the cap and the template says "or more". Money is summed over every payment that
touches a member, whoever the other account is. Scores and medians are rounded to 4 decimals, money and the span to 2.

The inputs are an `EvidenceContext`. `attach_summaries` hands it the factor run it already has; with none given it
reads `artifacts/factor_results.parquet` and recomputes the account scores from it with the factor file and policy,
which gives the scores `score` staged unless the files were edited after the run.
"""
from __future__ import annotations

import json
import statistics
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache
from itertools import islice
from typing import TYPE_CHECKING, Any

import jsonschema
import networkx as nx
import polars as pl

from lab.adapters import reads
from lab.common import alert_key as make_alert_key
from lab.contracts.evidence import Comparison, Evidence, FactorHit, MemberScore, Money, SharedIdentifier
from lab.scoring.communities import COMMUNITIES_FILE
from lab.scoring.community_score import CYCLE_MAX_LENGTH
from lab.scoring.community_score import SCHEMA as COMMUNITY_SCHEMA
from lab.scoring.factors import FactorRun, empty_results, load_factor_file
from lab.scoring.pipeline import FACTOR_RESULTS, slice_keys
from lab.scoring.policy import _EDGE, account_scores, load_policy
from lab.settings import get_settings

if TYPE_CHECKING:
    from lab.runs import Run

TOP_MEMBERS = 5
MAX_IDENTIFIERS = 10
MAX_EVIDENCE_IDS = 10
CYCLE_COUNT_CAP = 1000
SUBJECTS = ("community", "alert")

_PAY_SCHEMA = {"src": pl.Utf8, "dst": pl.Utf8, "amount": pl.Float64, "ts": pl.Datetime("us")}
_LINK_COLUMNS = ["account", "identifier", "label", "kind"]


# ---- the schema check --------------------------------------------------------------------------------------------
@cache
def _validator() -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(Evidence.model_json_schema())


def validate_evidence(evidence: Evidence) -> None:
    """Check the object against the contract's JSON schema. Raises `jsonschema.ValidationError`."""
    _validator().validate(evidence.model_dump(mode="json"))


# ---- what the evidence is built from ----------------------------------------------------------------------------
@dataclass
class EvidenceContext:
    """The run's results, read once, and the run-store reads that the objects share."""

    run: Run
    scores: pl.DataFrame  # key, risk_score, risk_score_rules, risk_score_model: every account of the slice
    results: pl.DataFrame  # factor_id, key, score, evidence: the factor results, long
    factor_names: dict[str, str]
    communities: pl.DataFrame  # communities.parquet
    alerts: pl.DataFrame  # alert_key, account_key, score, best first
    scope: list[str] | None  # the slice's account keys; None is every account in the run store
    _links: pl.DataFrame | None = field(default=None, repr=False)
    _pay: pl.DataFrame | None = field(default=None, repr=False)
    _pay_keys: frozenset[str] = field(default=frozenset(), repr=False)

    @classmethod
    def load(
        cls, run: Run, factor_run: FactorRun | None = None, alerts: pl.DataFrame | None = None
    ) -> EvidenceContext:
        """Read what `score` made. `factor_run` and `alerts` (alert_key, account_key, score) are what `score` already
        holds; without them they are read back from the run's artifacts and worked out again."""
        policy = load_policy(run)
        if factor_run is None:
            path, file = load_factor_file(run)
            kept = run.paths.artifacts / FACTOR_RESULTS
            results = pl.read_parquet(kept) if kept.exists() else empty_results()
            factor_run = FactorRun(path=path, file=file, results=results, status=[], keys=slice_keys(run))
        scores = account_scores(run, factor_run, policy).rename({run.recipe.graph.account_key: "key"}, strict=False)
        if alerts is None:
            hit = scores.filter(pl.col("risk_score") >= policy.alert_threshold - _EDGE)
            alerts = hit.select(
                pl.Series("alert_key", [make_alert_key(run.id, k, policy.window) for k in hit["key"]], dtype=pl.Utf8),
                pl.col("key").alias("account_key"),
                pl.col("risk_score").alias("score"),
            )
        kept_communities = run.paths.artifacts / COMMUNITIES_FILE
        communities = pl.read_parquet(kept_communities) if kept_communities.exists() else pl.DataFrame(schema=COMMUNITY_SCHEMA)
        return cls(
            run=run,
            scores=scores,
            results=factor_run.results,
            factor_names={f.id: f.name for f in factor_run.file.factors},
            communities=communities.sort(pl.col("score"), pl.col("community_key"), descending=[True, False]),
            alerts=alerts.select("alert_key", "account_key", "score").sort(
                pl.col("score"), pl.col("account_key"), descending=[True, False]
            ),
            scope=factor_run.keys,
        )

    @property
    def links(self) -> pl.DataFrame:
        """Account-to-identifier links of the slice with `used_by`, the accounts of the slice on that identifier."""
        if self._links is None:
            links = reads.identifiers(self.run, self.scope)
            keys = ["kind", "label", "identifier"]
            if links.is_empty():
                self._links = links.with_columns(used_by=pl.lit(0, dtype=pl.UInt32))
            else:
                self._links = links.join(links.group_by(keys).len(name="used_by"), on=keys)
        return self._links

    def payments_for(self, keys: Collection[str]) -> pl.DataFrame:
        """Every payment that touches one of `keys`: `src`, `dst`, `amount`, `ts` (naive UTC)."""
        wanted = frozenset(keys)
        if self._pay is None or not wanted <= self._pay_keys:
            self._pay, self._pay_keys = _touching(self.run, sorted(wanted)), wanted
        return self._pay


def _fetch(run: Run, query: str, keys: list[str]) -> pl.DataFrame:
    frames = [run.table(query, {"keys": batch}) for batch in reads.key_batches(keys)]
    if not frames:
        return pl.DataFrame(schema=_PAY_SCHEMA)
    df = pl.concat(frames, how="vertical_relaxed")
    return df.select(
        pl.col("src").cast(pl.Utf8), pl.col("dst").cast(pl.Utf8), pl.col("amount").cast(pl.Float64),
        ts=reads.utc_naive(df["ts"]),
    )


def _touching(run: Run, keys: list[str]) -> pl.DataFrame:
    """Payments out of `keys`, then payments into `keys` from accounts that are not in `keys`: each payment once."""
    g, q = run.recipe.graph, reads.q
    match = f"MATCH (s:{q(g.account_label)})-[t:{q(g.payment_type)}]->(d:{q(g.account_label)})"
    ret = (f"RETURN s.{q(g.account_key)} AS src, d.{q(g.account_key)} AS dst, "
           f"t.{q(g.payment_amount)} AS amount, t.{q(g.payment_time)} AS ts")
    out = _fetch(run, f"{match} WHERE s.{q(g.account_key)} IN $keys {ret}", keys)
    into = _fetch(run, f"{match} WHERE d.{q(g.account_key)} IN $keys {ret}", keys)
    return pl.concat([out, into.filter(~pl.col("src").is_in(keys))])


# ---- building ---------------------------------------------------------------------------------------------------
def build_evidence(run: Run, subject: str, key: str, context: EvidenceContext | None = None) -> Evidence:
    """The evidence for one community (`subject="community"`, its community key) or one alert (`"alert"`, its alert
    key), schema-checked. Pass `context` when building several, so the run's results are read once. Raises `KeyError`
    when the run has no such community or alert."""
    if subject not in SUBJECTS:
        raise ValueError(f"subject is one of {list(SUBJECTS)}, not {subject!r}")
    ctx = context or EvidenceContext.load(run)
    built = build_all(
        ctx, [key] if subject == "community" else [], [key] if subject == "alert" else []
    )
    if key not in built:
        raise KeyError(f"no {subject} {key!r} in run {run.id}")
    return built[key]


def build_all(
    ctx: EvidenceContext, community_keys: Collection[str] | None = None, alert_keys: Collection[str] | None = None
) -> dict[str, Evidence]:
    """Evidence by key for the named communities and alerts; None is every one of that kind."""
    comm, alerts = ctx.communities, ctx.alerts
    if community_keys is not None:
        comm = comm.filter(pl.col("community_key").is_in(list(community_keys)))
    if alert_keys is not None:
        alerts = alerts.filter(pl.col("alert_key").is_in(list(alert_keys)))
    community_groups = [
        (r["community_key"], list(r["member_keys"]), r["score"], {k: float(v) for k, v in json.loads(r["score_parts"]).items()})
        for r in comm.iter_rows(named=True)
    ]
    scored = ctx.scores.select("key", "risk_score_rules", "risk_score_model").rows_by_key("key", named=True, unique=True)
    alert_groups = [
        (r["alert_key"], [r["account_key"]], r["score"],
         {"rules": scored.get(r["account_key"], {}).get("risk_score_rules", 0.0),
          "model": scored.get(r["account_key"], {}).get("risk_score_model", 0.0)})
        for r in alerts.iter_rows(named=True)
    ]
    members = {m for _, ms, _, _ in (*community_groups, *alert_groups) for m in ms}
    pay = ctx.payments_for(members) if members else pl.DataFrame(schema=_PAY_SCHEMA)
    out = _assemble(ctx, "community", community_groups, pay)
    out.update(_assemble(ctx, "alert", alert_groups, pay))
    return out


def _assemble(
    ctx: EvidenceContext, subject: str, groups: list[tuple[str, list[str], float, dict[str, float]]], pay: pl.DataFrame
) -> dict[str, Evidence]:
    if not groups:
        return {}
    member_df = pl.DataFrame(
        {"account": [m for _, ms, _, _ in groups for m in ms], "g": [i for i, (_, ms, _, _) in enumerate(groups) for _ in ms]},
        schema={"account": pl.Utf8, "g": pl.Int64},
    )
    money = _money(pay, member_df)
    shared = _shared(ctx, subject, member_df)
    fired = _fired(ctx, member_df)
    top = _top_members(ctx, member_df)
    compare = _comparison(ctx, subject, [score for _, _, score, _ in groups])
    out: dict[str, Evidence] = {}
    for i, (key, members, score, parts) in enumerate(groups):
        evidence = Evidence(
            subject=subject,  # type: ignore[arg-type]
            key=key,
            run_id=ctx.run.id,
            member_count=len(members),
            member_keys=sorted(members),
            score=round(float(score), 4),
            score_parts={k: round(float(v), 4) for k, v in parts.items()},
            shared_identifiers=shared.get(i, []),
            money=money[i],
            factors=fired.get(i, []),
            top_members=top.get(i, []),
            comparison=compare[i],
        )
        validate_evidence(evidence)
        out[key] = evidence
    return out


# ---- the parts --------------------------------------------------------------------------------------------------
def _stamp(t: datetime | None) -> str | None:
    return None if t is None else t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _money(pay: pl.DataFrame, member_df: pl.DataFrame) -> dict[int, Money]:
    """Per group: what came in from outside, what went out to outside, what moved between members, the cycles."""
    p = (
        pay.filter(pl.col("src") != pl.col("dst"))
        .join(member_df.rename({"account": "src", "g": "g_src"}), on="src", how="left")
        .join(member_df.rename({"account": "dst", "g": "g_dst"}), on="dst", how="left")
        .with_columns(pl.col("g_src", "g_dst").fill_null(-1), pl.col("amount").fill_null(0.0))
    )
    inflow = _sum_by(p.filter((pl.col("g_dst") >= 0) & (pl.col("g_src") != pl.col("g_dst"))), "g_dst")
    outflow = _sum_by(p.filter((pl.col("g_src") >= 0) & (pl.col("g_src") != pl.col("g_dst"))), "g_src")
    inside = p.filter((pl.col("g_src") >= 0) & (pl.col("g_src") == pl.col("g_dst")))
    internal = _sum_by(inside, "g_src")
    touched = pl.concat([
        p.filter(pl.col("g_src") >= 0).select(g="g_src", ts="ts"),
        p.filter(pl.col("g_dst") >= 0).select(g="g_dst", ts="ts"),
    ]).group_by("g").agg(first=pl.col("ts").min(), last=pl.col("ts").max())
    span = {r["g"]: (r["first"], r["last"]) for r in touched.iter_rows(named=True)}
    cycles: dict[int, int] = {}
    for (g,), edges in inside.partition_by("g_src", as_dict=True).items():
        D = nx.DiGraph(set(zip(edges["src"], edges["dst"], strict=True)))
        cycles[g] = sum(1 for _ in islice(nx.simple_cycles(D, length_bound=CYCLE_MAX_LENGTH), CYCLE_COUNT_CAP))
    out = {}
    for g in range(int(member_df["g"].max()) + 1 if member_df.height else 0):
        first, last = span.get(g, (None, None))
        out[g] = Money(
            inflow=round(inflow.get(g, 0.0), 2), outflow=round(outflow.get(g, 0.0), 2),
            internal=round(internal.get(g, 0.0), 2), cycles=cycles.get(g, 0),
            first_at=_stamp(first), last_at=_stamp(last),
            span_hours=None if first is None or last is None else round((last - first).total_seconds() / 3600, 2),
        )
    return out


def _sum_by(df: pl.DataFrame, g: str) -> dict[int, float]:
    return dict(df.group_by(g).agg(pl.col("amount").sum()).iter_rows())


def _shared(ctx: EvidenceContext, subject: str, member_df: pl.DataFrame) -> dict[int, list[SharedIdentifier]]:
    links = ctx.links
    if links.is_empty() or member_df.is_empty():
        return {}
    usable = links.filter(pl.col("used_by") <= get_settings().shared_max)
    node = ["kind", "label", "identifier"]
    if subject == "community":
        inside = usable.join(member_df, left_on="account", right_on="account")
        found = inside.group_by("g", *node).agg(n=pl.col("account").n_unique()).filter(pl.col("n") >= 2)
    else:  # one member: how many accounts of the slice use the identifier, this one included
        found = usable.filter(pl.col("used_by") >= 2).join(member_df, on="account").select(
            "g", *node, n=pl.col("used_by").cast(pl.Int64)
        )
    noun = pl.when(pl.col("kind") == "other").then(pl.col("label").str.to_lowercase()).otherwise(pl.col("kind"))
    found = found.with_columns(noun=noun).sort("g", "n", "noun", "identifier", descending=[False, True, False, False])
    out: dict[int, list[SharedIdentifier]] = {}
    for r in found.iter_rows(named=True):
        mine = out.setdefault(r["g"], [])
        if len(mine) < MAX_IDENTIFIERS:
            mine.append(SharedIdentifier(kind=r["noun"], key=r["identifier"], members_sharing=int(r["n"])))
    return out


def _fired(ctx: EvidenceContext, member_df: pl.DataFrame) -> dict[int, list[FactorHit]]:
    """Per group, the factors that fired (score above 0) on its members, with how many and their evidence ids."""
    rows = (
        ctx.results.filter(pl.col("score") > 0)
        .join(member_df, left_on="key", right_on="account")
        .sort("g", "factor_id", "key")
    )
    counts: dict[tuple[int, str], set[str]] = {}
    ids: dict[tuple[int, str], list[str]] = {}
    for r in rows.iter_rows(named=True):
        slot = (r["g"], r["factor_id"])
        counts.setdefault(slot, set()).add(r["key"])
        mine = ids.setdefault(slot, [])
        for evidence_id in _evidence_ids(r["evidence"]):
            if evidence_id not in mine and len(mine) < MAX_EVIDENCE_IDS:
                mine.append(evidence_id)
    out: dict[int, list[FactorHit]] = {}
    for (g, factor_id), accounts in sorted(counts.items(), key=lambda kv: (kv[0][0], -len(kv[1]), kv[0][1])):
        out.setdefault(g, []).append(FactorHit(
            factor_id=factor_id, name=ctx.factor_names.get(factor_id, factor_id), accounts=len(accounts),
            evidence_ids=ids[(g, factor_id)],
        ))
    return out


def _evidence_ids(text: str | None) -> list[str]:
    """The `evidence_ids` of one factor's evidence JSON; none when it has none or is not an object."""
    try:
        body: Any = json.loads(text) if text else None
    except ValueError:
        return []
    found = body.get("evidence_ids") if isinstance(body, dict) else None
    return [str(i) for i in found if i is not None] if isinstance(found, list) else []


def _top_members(ctx: EvidenceContext, member_df: pl.DataFrame) -> dict[int, list[MemberScore]]:
    scored = (
        member_df.join(ctx.scores.select("key", "risk_score"), left_on="account", right_on="key", how="left")
        .with_columns(pl.col("risk_score").fill_null(0.0))
        .sort("g", "risk_score", "account", descending=[False, True, False])
    )
    out: dict[int, list[MemberScore]] = {}
    for r in scored.iter_rows(named=True):
        mine = out.setdefault(r["g"], [])
        if len(mine) < TOP_MEMBERS:
            mine.append(MemberScore(key=r["account"], score=round(float(r["risk_score"]), 4)))
    return out


def _comparison(ctx: EvidenceContext, subject: str, scores: list[float]) -> list[Comparison]:
    """How each subject stands against the rest of the slice (the table at the top says against what)."""
    communities = ctx.communities
    sizes = communities["size"].to_list()
    base = communities["score"].to_list() if subject == "community" else ctx.alerts["score"].to_list()
    return [
        Comparison(
            communities_in_slice=communities.height,
            score_rank=1 + sum(1 for b in base if b > s),
            median_size=round(float(statistics.median(sizes)), 4) if sizes else 0.0,
            median_score=round(float(statistics.median(base)), 4) if base else 0.0,
        )
        for s in scores
    ]
