"""`score(run)`: the scoring step of a run (page 04). Tracks 2b, 3a and 3b build it in turn.

The order, one function per beat so a later track adds a body and not a branch:

1. `slice_keys`      the accounts in the current slice, or None for the whole run store
2. `run_factors`     every risk factor in the factor file, on the slice (`lab/scoring/factors.py`)
3. `account_scores`  the policy makes risk_score, risk_score_model, risk_score_rules, risk_factors (`policy.py`)
4. `alerts`          the accounts at or above the policy's threshold
5. `attach_communities`   level three, track 3a: communities, community scores, `risk_community_key`
6. `attach_summaries`     page 05, track 3b: the evidence object and the summary per alert and community
7. stage             accounts, communities and alerts, each once: `run.stage(scores, key, "account")`,
                     `run.stage(communities, "community_key", "community")`, `run.stage(alerts, "alert_key", "alert")`.
                     Staging fills `risk_run_id` and `risk_at` on the accounts, `run_id` and `created_at` on the rest

The manifest step is `score`: its counts, the factor file's version on `manifest.factor_version`, and a note naming
every factor that was skipped or failed and why.
"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

import polars as pl

from lab.common import get_logger
from lab.scoring.factors import FactorRun, run_factors
from lab.scoring.policy import account_scores, alerts, load_policy

if TYPE_CHECKING:
    from lab.runs import Run

log = get_logger("scoring.pipeline")

FACTOR_RESULTS = "factor_results.parquet"  # in the run's artifacts: every factor's score and evidence per account


def score(run: Run) -> dict[str, int]:
    """Score the run's accounts and stage the account scores, the communities and the alerts, each once. Returns the counts.

    A factor that cannot run does not stop the step: it counts 0, and the manifest note says which and why. A
    factor file that breaks its schema, or a failure anywhere else, marks the step failed and raises.
    """
    run.manifest.start("score")
    run.save()
    try:
        keys = slice_keys(run)
        factor_run = run_factors(run, keys)
        run.manifest.factor_version = factor_run.version
        run.save()
        policy = load_policy(run)
        scores = account_scores(run, factor_run, policy)
        found = alerts(run, scores, policy, factor_run)
        scores, community_counts = attach_communities(run, scores, keys, factor_run)
        found, community_rows, summary_counts = attach_summaries(run, found, factor_run)
        scores = attach_embeddings(run, scores)
        _keep_factor_results(run, factor_run)
        key = run.recipe.graph.account_key
        run.stage(scores, key, "account")
        run.stage(community_rows, "community_key", "community")
        run.stage(found, "alert_key", "alert")
    except Exception as e:
        run.manifest.fail("score", f"{type(e).__name__}: {e}")
        run.save()
        raise
    counts = {
        "accounts": scores.height, "alerts": found.height, **factor_run.counts(), **community_counts, **summary_counts,
    }
    run.manifest.finish("score", counts, note=_note(factor_run))
    run.save()
    log.info("scored %d accounts, %d alerts, factors %s", scores.height, found.height, factor_run.version)
    return counts


def slice_keys(run: Run) -> list[str] | None:
    """The account keys of the run's current slice, or None when there is no slice to apply (every account).

    `lab.slice.search.current_slice` is track 2a's and is imported here, at call time, because that module is built
    in parallel. Until it exists, or when it has no `current_slice`, the answer is None.
    """
    try:
        module = importlib.import_module("lab.slice.search")
    except ModuleNotFoundError as e:
        if e.name != "lab.slice.search":
            raise  # the module is there and needs something that is not
        return None
    current_slice = getattr(module, "current_slice", None)
    if current_slice is None:
        return None
    found = current_slice(run)
    if found is None:
        return None
    return [str(k) for k in (found.to_list() if isinstance(found, pl.Series) else found)]


def attach_communities(
    run: Run, scores: pl.DataFrame, keys: list[str] | None, factor_run: FactorRun | None = None
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Level three (page 04): find the slice's communities, score them, and say who is in which.

    Takes the account scores and the slice (and the factor run, so the community's `speed` part reads the factor's rows
    and does not run its query again); returns the scores with `risk_community_key` added (null for an account in no
    community) and the counts for the manifest step `communities`: the communities, their members, the largest, and how
    many formed by shared identifier and how many by payment. Communities under the policy's `min_size` are dropped. Each
    community goes into the run store with the mean of its members' embeddings, and `artifacts/communities.parquet` holds
    them with their members and how each `formed`, for track 3b. Nothing is staged here: `attach_summaries` adds the
    evidence and the summaries to the rows, and `score` stages them once.
    """
    from lab.scoring import communities as cm
    from lab.scoring.community_score import score_communities

    run.manifest.start("communities")
    run.save()
    try:
        policy = load_policy(run)
        groups = cm.find_communities(run, keys, policy.community.min_size)
        found = cm.embed_communities(run, score_communities(run, groups, scores, policy, factor_run))
        cm.write_communities(run, found)
        run.paths.artifacts.mkdir(parents=True, exist_ok=True)
        found.write_parquet(run.paths.artifacts / cm.COMMUNITIES_FILE)
        scores = cm.add_community_keys(run, scores, found)
    except Exception as e:
        run.manifest.fail("communities", f"{type(e).__name__}: {e}"[:500])
        run.save()
        raise
    by_identifier = int((found["formed"] == cm.IDENTIFIER).sum())
    counts = {
        "communities": found.height,
        "community_members": int(found["size"].sum() or 0),
        "largest": int(found["size"].max() or 0),
        "by_identifier": by_identifier,
        "by_payment": found.height - by_identifier,
    }
    note = f"{len(groups) - found.height} under min_size {policy.community.min_size}"
    run.manifest.finish("communities", counts, note=note)
    run.save()
    log.info("%d communities, %d members, largest %d; %d by identifier, %d by payment", *counts.values())
    return scores, counts


def attach_summaries(
    run: Run, found: pl.DataFrame, factor_run: FactorRun
) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, int]]:
    """Page 05, track 3b: the evidence object and the summary per alert and community (`lab/summaries/store.py`).

    Takes the alerts and the factor run, whose results hold every fired factor's evidence; returns the alerts with
    `evidence` (the evidence object's JSON), `evidence_hash` and, for the top `LAB_SUMMARY_ALERTS`, `summary`,
    `summary_source` and `summary_model`; the community rows with the same columns, as `run.stage` takes them; and the
    counts for the manifest step `summaries`. Nothing is staged here. Each summary goes onto the community's node in
    the run store. With no `ANTHROPIC_API_KEY` every summary is the template and nothing leaves the container.
    """
    from lab.summaries import summarise

    run.manifest.start("summaries")
    run.save()
    try:
        found, community_rows, counts, note = summarise(run, found, factor_run)
    except Exception as e:
        run.manifest.fail("summaries", f"{type(e).__name__}: {e}"[:500])
        run.save()
        raise
    run.manifest.finish("summaries", counts, note=note)
    run.save()
    return found, community_rows, counts


def attach_embeddings(run: Run, scores: pl.DataFrame) -> pl.DataFrame:
    """The first pass's embedding on each scored account, so it goes back with the scores (R11, `write_back.vectors`).

    Nothing is added when the recipe does not write vectors back or the first pass has not run.
    """
    from lab.contracts.graph import EMBEDDING_PROP
    from lab.roundtrip.queries import ident

    if not run.recipe.write_back.vectors or scores.is_empty():
        return scores
    m = run.recipe.graph
    emb = run.table(
        f"MATCH (a:{ident(m.account_label)}) WHERE a.{ident(EMBEDDING_PROP)} IS NOT NULL "
        f"RETURN a.{ident(m.account_key)} AS {ident(m.account_key)}, a.{ident(EMBEDDING_PROP)} AS {EMBEDDING_PROP}"
    )
    if emb.is_empty():
        return scores
    emb = emb.with_columns(pl.col(EMBEDDING_PROP).cast(pl.List(pl.Float64)))
    return scores.join(emb, on=m.account_key, how="left")


def _keep_factor_results(run: Run, factor_run: FactorRun) -> None:
    """Every factor's score and evidence per account, for the evidence objects and the communities to read."""
    run.paths.artifacts.mkdir(parents=True, exist_ok=True)
    factor_run.results.write_parquet(run.paths.artifacts / FACTOR_RESULTS)


def _note(factor_run: FactorRun) -> str:
    note = f"factor version {factor_run.version}"
    problems = factor_run.note()
    return f"{note}; {problems}" if problems else note
