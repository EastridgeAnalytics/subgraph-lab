"""Where summaries land (page 05): the files `explain_alert` reads, the staged rows, the community nodes in the run store.

`summarise(run, found, factor_run)` is the step. It builds the evidence for every community and alert, saves each under
`artifacts/evidence/<key>.json`, writes a summary for the top `LAB_SUMMARY_COMMUNITIES` communities and the top
`LAB_SUMMARY_ALERTS` alerts (best score first, through `writers.Writer`: cache, API when a key is set, checks,
template), saves each under `artifacts/summaries/<key>.json`, and puts them where they are used:

| Where | What |
|---|---|
| `artifacts/evidence/<key>.json` | the `Evidence`, for every community and alert: `read_evidence(run, key)` |
| `artifacts/summaries/<key>.json` | the `Summary` (source, model, hash, checks, failed reason), for those that have one: `read_summary(run, key)` |
| the staged `Alert` and `RiskCommunity` rows | `evidence` (JSON), `evidence_hash` on every row; `summary`, `summary_source`, `summary_model` on the summarised ones |
| the run store's `RiskCommunity` nodes | `summary`, which `community_summary` indexes, so "communities like this one" finds it |

`describe(run, key, text)` is the agent's path: track 2d's `describe_alert` calls it. It checks the text an agent wrote
for an alert or community against that subject's saved evidence with the same checks as the API's, stores it with source
`agent` when it passes, and stores the template with `failed_reason` when it does not. Either way the summary is saved,
restaged, and (for a community) written to its run-store node, and the `Summary` is returned. `key` is an alert key, a
community key, or the key of an account that has an alert. It raises `LookupError` when the run has no such subject or no
evidence yet (run `score` first). The tool turns the returned `Summary` into `DescribeOut`: it is `stored` either way, and
`summary.source` and `summary.failed_reason` say which happened.

One thing staging cannot do is clear a property: a null is left out, not written. A row that is summarised again by a
writer with no model (the template, an agent) has `summary_model` removed from the push store here, so a stale model name
never sits beside source `agent` or `template`.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from lab.adapters import _read
from lab.common import alert_key as make_alert_key
from lab.common import canonical_json, get_logger, iso_utc
from lab.contracts.evidence import Evidence, Summary
from lab.contracts.graph import ALERT_KEY, ALERT_LABEL, COMMUNITY_KEY, COMMUNITY_LABEL
from lab.roundtrip.indexes import COMMUNITY_SUMMARY_PROP
from lab.roundtrip.loader import load_rows
from lab.roundtrip.queries import ident
from lab.scoring.communities import COMMUNITIES_FILE, for_stage
from lab.scoring.factors import FactorRun
from lab.scoring.policy import load_policy
from lab.settings import get_settings
from lab.summaries.checks import IsAccount
from lab.summaries.evidence import EvidenceContext, build_all
from lab.summaries.writers import Writer, Written, agent_summary

if TYPE_CHECKING:
    from lab.runs import Run

log = get_logger("summaries.store")

EVIDENCE_DIR = "evidence"
SUMMARIES_DIR = "summaries"


# ---- the files --------------------------------------------------------------------------------------------------
def evidence_path(run: Run, key: str) -> Path:
    return run.paths.artifacts / EVIDENCE_DIR / f"{key}.json"


def summary_path(run: Run, key: str) -> Path:
    return run.paths.artifacts / SUMMARIES_DIR / f"{key}.json"


def save_evidence(run: Run, evidence: Evidence) -> None:
    path = evidence_path(run, evidence.key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(evidence.model_dump(mode="json")), encoding="utf-8")


def read_evidence(run: Run, key: str) -> Evidence | None:
    """The saved evidence of an alert or community of this run, or None when `score` has not made it."""
    path = evidence_path(run, key)
    return Evidence.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None


def save_summary(run: Run, summary: Summary) -> None:
    path = summary_path(run, summary.key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(summary.model_dump_json(), encoding="utf-8")


def read_summary(run: Run, key: str) -> Summary | None:
    """The saved summary of an alert or community of this run, or None when it has none."""
    path = summary_path(run, key)
    return Summary.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None


# ---- the step ---------------------------------------------------------------------------------------------------
def summarise(
    run: Run, found: pl.DataFrame, factor_run: FactorRun, writer: Writer | None = None
) -> tuple[pl.DataFrame, dict[str, int], str]:
    """Evidence for every alert and community, a summary for the top ones, all stored. See the module docstring.

    `found` is the alerts (`alert_key`, `account_key`, `window`, `score`, `evidence`). Returns the alerts with
    `evidence` now the evidence object's JSON, plus `evidence_hash`, `summary`, `summary_source` and `summary_model`;
    the counts (`summaries`, `from_api`, `from_template`, `failed_checks`, `cached`); and a note for the manifest.
    """
    s = get_settings()
    ctx = EvidenceContext.load(run, factor_run, found)
    built = build_all(ctx)
    for evidence in built.values():
        save_evidence(run, evidence)

    accounts = set(ctx.scores["key"].to_list())
    writer = writer or Writer(settings=s, is_account=lambda tokens: accounts.intersection(tokens))
    top_communities = ctx.communities["community_key"].head(s.summary_communities).to_list()
    top_alerts = ctx.alerts["alert_key"].head(s.summary_alerts).to_list()
    written: dict[str, Written] = {}
    for key in (*top_communities, *top_alerts):
        written[key] = writer.write(built[key])
        save_summary(run, written[key].summary)
    summaries = {k: w.summary for k, w in written.items()}

    alerts = found.with_columns(*_columns(found["alert_key"].to_list(), built, summaries))
    alerts = alerts.with_columns(
        pl.Series("evidence", [canonical_json(built[k].model_dump(mode="json")) for k in alerts["alert_key"]], dtype=pl.Utf8)
    )
    stage_communities(run, ctx.communities, built, summaries)
    if alerts.height:
        run.stage(alerts, ALERT_KEY, "alert")
    _forget_models(run, summaries, built)
    write_community_text(run, {k: v.text for k, v in summaries.items() if built[k].subject == "community"})

    counts = {
        "summaries": len(written),
        "from_api": sum(w.summary.source == "api" for w in written.values()),
        "from_template": sum(w.summary.source == "template" for w in written.values()),
        "failed_checks": sum(w.failed_check for w in written.values()),
        "cached": sum(w.cached for w in written.values()),
    }
    note = f"written by the Anthropic API ({s.model})" if writer.uses_api else "template only: no ANTHROPIC_API_KEY set"
    log.info("%d summaries: %s", counts["summaries"], counts)
    return alerts, counts, note


def _columns(keys: list[str], built: dict[str, Evidence], summaries: dict[str, Summary]) -> list[pl.Series]:
    """`evidence_hash` for every key; the summary columns for the keys that have a summary, null for the rest."""
    def pick(f) -> list[str | None]:
        return [f(summaries[k]) if k in summaries else None for k in keys]

    return [
        pl.Series("evidence_hash", [built[k].hash() for k in keys], dtype=pl.Utf8),
        pl.Series("summary", pick(lambda x: x.text), dtype=pl.Utf8),
        pl.Series("summary_source", pick(lambda x: x.source), dtype=pl.Utf8),
        pl.Series("summary_model", pick(lambda x: x.model), dtype=pl.Utf8),
    ]


def stage_communities(
    run: Run, communities: pl.DataFrame, built: dict[str, Evidence], summaries: dict[str, Summary]
) -> int:
    """Restage these community rows (a second stage of a key updates it) with their evidence and summary columns.

    The rows are the ones `attach_communities` staged, same columns (`for_stage` leaves the embedding out when the recipe
    does not write vectors back), plus the new ones, so what was staged is still all there in the last stage.
    """
    if communities.is_empty():
        return 0
    keys = communities["community_key"].to_list()
    df = for_stage(run, communities).with_columns(
        *_columns(keys, built, summaries),
        pl.Series("evidence", [canonical_json(built[k].model_dump(mode="json")) for k in keys], dtype=pl.Utf8),
    )
    return run.stage(df, COMMUNITY_KEY, "community")


def write_community_text(run: Run, texts: dict[str, str]) -> int:
    """`summary` on each community's node in the run store, where `community_summary` indexes it."""
    if not texts:
        return 0
    label, key, prop = ident(COMMUNITY_LABEL), ident(COMMUNITY_KEY), ident(COMMUNITY_SUMMARY_PROP)
    rows = [{"key": k, "text": t} for k, t in texts.items()]
    load_rows(run.store(), f"UNWIND $rows AS r MATCH (c:{label} {{{key}: r.key}}) SET c.{prop} = r.text", rows)
    return len(rows)


def _forget_models(run: Run, summaries: dict[str, Summary], built: dict[str, Evidence]) -> None:
    """Remove `summary_model` from the push store for summaries that have no model (staging cannot clear a property)."""
    if not run.paths.push_store.exists():
        return
    for subject, label, key in (("community", COMMUNITY_LABEL, COMMUNITY_KEY), ("alert", ALERT_LABEL, ALERT_KEY)):
        rows = [{"key": k} for k, v in summaries.items() if v.model is None and built[k].subject == subject]
        if rows:
            load_rows(
                run.push_store(),
                f"UNWIND $rows AS r MATCH (n:{ident(label)} {{{ident(key)}: r.key}}) REMOVE n.summary_model",
                rows,
            )


# ---- the agent's path ---------------------------------------------------------------------------------------------
def describe(run: Run, key: str, text: str) -> Summary:
    """Check an agent's text for an alert or community and store it; the template stands in when it fails. See the
    module docstring: this is what `describe_alert` calls."""
    evidence = read_evidence(run, key)
    if evidence is None:  # an account key: the alert on that account
        evidence = read_evidence(run, make_alert_key(run.id, key, load_policy(run).window))
    if evidence is None:
        raise LookupError(f"run {run.id} has no evidence for {key!r}: it is not an alert or community of this run, or score has not run")
    summary = agent_summary(evidence, text, is_account=_store_accounts(run))
    save_summary(run, summary)
    if evidence.subject == "community":
        communities = pl.read_parquet(run.paths.artifacts / COMMUNITIES_FILE).filter(pl.col("community_key") == evidence.key)
        stage_communities(run, communities, {evidence.key: evidence}, {evidence.key: summary})
        write_community_text(run, {evidence.key: summary.text})
    else:
        run.stage(_alert_row(run, evidence, summary), ALERT_KEY, "alert")
    _forget_models(run, {evidence.key: summary}, {evidence.key: evidence})
    log.info("described %s %s: source %s", evidence.subject, evidence.key, summary.source)
    return summary


def _alert_row(run: Run, evidence: Evidence, summary: Summary) -> pl.DataFrame:
    """One alert row for a restage: the summary columns, and the `created_at` it was first staged with."""
    created = iso_utc()
    if run.paths.push_store.exists():
        found = run.push_store().execute(
            f"MATCH (n:{ident(ALERT_LABEL)} {{{ident(ALERT_KEY)}: $k}}) RETURN n.created_at AS c", {"k": evidence.key}
        ).data()
        created = found[0]["c"] if found and found[0]["c"] else created
    return pl.DataFrame({ALERT_KEY: [evidence.key], "account_key": [evidence.member_keys[0]], "created_at": [created]}).with_columns(
        *_columns([evidence.key], {evidence.key: evidence}, {evidence.key: summary})
    )


def _store_accounts(run: Run) -> IsAccount:
    """`is_account` for a run that is not in the middle of scoring: which of the words are account keys in the store."""
    g = run.recipe.graph

    def lookup(tokens):
        found: set[str] = set()
        for batch in _read.key_batches(sorted(tokens)):
            df = run.table(
                f"MATCH (a:{_read.q(g.account_label)}) WHERE a.{_read.q(g.account_key)} IN $keys "
                f"RETURN a.{_read.q(g.account_key)} AS key",
                {"keys": batch},
            )
            found |= {str(k) for k in df["key"].to_list()}
        return found

    return lookup
