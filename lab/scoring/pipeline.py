"""`score(run)`: the scoring step of a run (page 04). Tracks 2b, 3a and 3b build it in turn.

The order, one function per beat so a later track adds a body and not a branch:

1. `slice_keys`      the accounts in the current slice, or None for the whole run store
2. `run_factors`     every risk factor in the factor file, on the slice (`lab/scoring/factors.py`)
3. `account_scores`  the policy makes risk_score, risk_score_model, risk_score_rules, risk_factors (`policy.py`)
4. `alerts`          the accounts at or above the policy's threshold
5. `attach_communities`   level three, track 3a: communities, community scores, `risk_community_key`
6. `attach_summaries`     page 05, track 3b: the evidence object and the summary per alert and community
7. stage             `run.stage(scores, key, "account")` and `run.stage(alerts, "alert_key", "alert")`

The manifest step is `score`: its counts, the factor file's version on `manifest.factor_version`, and a note naming
every factor that was skipped or failed and why.
"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

import polars as pl

from lab.common import get_logger, iso_utc
from lab.scoring.factors import FactorRun, run_factors
from lab.scoring.policy import account_scores, alerts, load_policy

if TYPE_CHECKING:
    from lab.runs import Run

log = get_logger("scoring.pipeline")

FACTOR_RESULTS = "factor_results.parquet"  # in the run's artifacts: every factor's score and evidence per account


def score(run: Run) -> dict[str, int]:
    """Score the run's accounts and stage the account scores and the alerts. Returns the counts.

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
        scores, community_counts = attach_communities(run, scores, keys)
        found, summary_counts = attach_summaries(run, found, factor_run)
        scores, found = stamp(run, scores, found)
        _keep_factor_results(run, factor_run)
        key = run.recipe.graph.account_key
        run.stage(scores, key, "account")
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


def attach_communities(run: Run, scores: pl.DataFrame, keys: list[str] | None) -> tuple[pl.DataFrame, dict[str, int]]:
    """Track 3a joins here. Today it changes nothing.

    Takes the account scores and the slice; returns the scores, with `risk_community_key` added once communities
    exist, and the counts to put on the manifest step. It stages the `RiskCommunity` rows itself
    (`run.stage(df, "community_key", "community")`).
    """
    return scores, {}


def attach_summaries(run: Run, found: pl.DataFrame, factor_run: FactorRun) -> tuple[pl.DataFrame, dict[str, int]]:
    """Track 3b joins here. Today it changes nothing.

    Takes the alerts and the factor run, whose results hold every fired factor's evidence; returns the alerts with
    the summary columns on `ALERT_PROPS` filled in, and the counts for the manifest step.
    """
    return found, {}


def stamp(run: Run, scores: pl.DataFrame, found: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The run id and the time on every row written: `risk_run_id` and `risk_at`; `run_id` and `created_at`."""
    now = iso_utc()
    return (
        scores.with_columns(pl.lit(run.id).alias("risk_run_id"), pl.lit(now).alias("risk_at")),
        found.with_columns(pl.lit(run.id).alias("run_id"), pl.lit(now).alias("created_at")),
    )


def _keep_factor_results(run: Run, factor_run: FactorRun) -> None:
    """Every factor's score and evidence per account, for the evidence objects and the communities to read."""
    run.paths.artifacts.mkdir(parents=True, exist_ok=True)
    factor_run.results.write_parquet(run.paths.artifacts / FACTOR_RESULTS)


def _note(factor_run: FactorRun) -> str:
    note = f"factor version {factor_run.version}"
    problems = factor_run.note()
    return f"{note}; {problems}" if problems else note
