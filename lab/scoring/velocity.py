"""The velocity factor (pages 03 and 04): an account's recent outflow against its own forecast.

`factors/factors.yaml` names it as `velocity_anomaly`, `lab.scoring.velocity:score`. It is a `python` factor, so
`run_factors` calls `score(run, factor, keys)` and takes back `key`, `score` and `evidence`.

How it works, in four steps:

1. `to_series` gives each account's outflow per day over one shared window. The window ends on the last day any
   payment in the run store was made, not on the slice's last day, so an account scores the same on any slice that
   holds it. Each account's series is cut to start at its own first payment out: the days before an account first
   paid are not its pattern, and Croston reads leading zeros as one very long gap between payments.
2. The last `LAB_VELOCITY_HOLDOUT_DAYS` of the window are held out. Every account with enough history is fitted on the days before
   them, all in one `StatsForecast(...).forecast(...)` call, never one call per account.
3. The model is Croston's classic method (`CrostonClassic`), made for series that are mostly zero: it forecasts a
   payment's usual size divided by the usual gap between payments, with a fixed smoothing of 0.1, so there is nothing
   to fit and nothing to tune per account. Outflows here are intermittent (an account pays on a few days and is
   silent on the rest) and a flat daily level is all it claims. The comparison is the total over the held-out days,
   a whole number of weeks, so a weekly rhythm cancels out and the model does not need to know about weekdays.
4. The score is the share of the recent outflow the account's own history does not explain:
   `1 - forecast_total / actual_total`, or 0 when the actual is at or below the forecast. Twice the forecast scores
   0.5, four times 0.75, ten times 0.9; an account that was silent in its forecast and paid in the window scores 1.
   It needs no threshold. The factor's `threshold`, if the file sets one, is not read.

An account has no row, which counts 0 in the policy, when its own history is too short to be a pattern: fewer than
`LAB_VELOCITY_MIN_HISTORY_DAYS` days between its first payment out and the start of the held-out days, or fewer than
`LAB_VELOCITY_MIN_ACTIVE_DAYS` days on which it paid in that time. A new account that starts paying inside the held-out days has
no forecast to be measured against; the factors that look at new accounts are for that.

The evidence is one JSON object: the model, the held-out days (their number and the first and last date), the
forecast total, the actual total and their ratio (null when the forecast is zero).

    uv run python -m examples.velocity.example
"""
from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import polars as pl
from statsforecast import StatsForecast
from statsforecast.models import CrostonClassic

from lab.adapters import reads, to_series
from lab.common import canonical_json
from lab.settings import get_settings

if TYPE_CHECKING:
    from lab.contracts.factors import Factor
    from lab.runs import Run

MODEL = "CrostonClassic"  # the days held out and the history an account needs are settings (page 07): LAB_VELOCITY_*

_EMPTY = {"key": pl.Utf8, "score": pl.Float64, "evidence": pl.Utf8}


def score(run: Run, factor: Factor | None = None, keys: list[str] | None = None) -> pl.DataFrame:
    """One row per account with enough history: `key`, `score` in [0, 1], `evidence` (a JSON string).

    `keys` is the slice (None is every account in the run store). `factor` is the factor-file entry; nothing of it
    is read, so the example can call this without one.
    """
    s = get_settings()
    holdout_days = s.velocity_holdout_days
    series = to_series(run, keys=keys)
    if series.is_empty():
        return pl.DataFrame(schema=_EMPTY)
    if keys is not None:
        series = _to_the_end_of_the_run(run, series)
    last = series["ds"].max()
    cutoff = last - timedelta(days=holdout_days - 1)  # the first held-out day

    # each account's own life: from its first payment out. `first` is that day.
    first = series.filter(pl.col("y") > 0).group_by("unique_id").agg(first=pl.col("ds").min())
    own = series.join(first, on="unique_id").filter(pl.col("ds") >= pl.col("first"))
    history = own.filter(pl.col("ds") < cutoff)
    enough = (
        history.group_by("unique_id")
        .agg(days=pl.len(), active=(pl.col("y") > 0).sum())
        .filter((pl.col("days") >= s.velocity_min_history_days) & (pl.col("active") >= s.velocity_min_active_days))
    )
    if enough.is_empty():
        return pl.DataFrame(schema=_EMPTY)
    history = history.join(enough.select("unique_id"), on="unique_id").select("unique_id", "ds", "y")

    forecast = (
        StatsForecast(models=[CrostonClassic(alias="forecast")], freq="1d", n_jobs=1)
        .forecast(df=history, h=holdout_days)
        .group_by("unique_id")
        .agg(forecast_total=pl.col("forecast").sum().clip(lower_bound=0.0))
    )
    actual = (
        own.filter(pl.col("ds") >= cutoff).group_by("unique_id").agg(actual_total=pl.col("y").sum())
    )
    both = forecast.join(actual, on="unique_id", how="left").with_columns(pl.col("actual_total").fill_null(0.0))
    both = both.with_columns(
        score=pl.when(pl.col("actual_total") > pl.col("forecast_total"))
        .then(1.0 - pl.col("forecast_total") / pl.col("actual_total"))  # actual > forecast >= 0, so never a zero divisor
        .otherwise(0.0),
        ratio=pl.when(pl.col("forecast_total") > 0).then(pl.col("actual_total") / pl.col("forecast_total")),
    ).sort("unique_id")

    window = {"holdout_days": holdout_days, "holdout_from": cutoff.date().isoformat(),
              "holdout_to": last.date().isoformat(), "model": MODEL}
    evidence = [
        canonical_json({**window, "forecast_total": round(f, 2), "actual_total": round(a, 2),
                        "ratio": None if r is None else round(r, 3)})
        for f, a, r in both.select("forecast_total", "actual_total", "ratio").iter_rows()
    ]
    return pl.DataFrame({"key": both["unique_id"], "score": both["score"], "evidence": evidence}, schema=_EMPTY)


def _to_the_end_of_the_run(run: Run, series: pl.DataFrame) -> pl.DataFrame:
    """Zeros from the slice's last day to the last day of any payment in the run store, for every series."""
    g = run.recipe.graph
    latest = run.table(f"MATCH ()-[t:{reads.q(g.payment_type)}]->() RETURN max(t.{reads.q(g.payment_time)}) AS last")["last"]
    if latest.is_null().all():
        return series
    end, last = series["ds"].max(), reads.utc_naive(latest).dt.truncate("1d").item()
    if last <= end:
        return series
    days = pl.datetime_range(end + timedelta(days=1), last, interval="1d", eager=True).cast(pl.Datetime("us")).alias("ds")
    pad = pl.DataFrame({"unique_id": series["unique_id"].unique()}).join(days.to_frame(), how="cross")
    return pl.concat([series, pad.with_columns(y=pl.lit(0.0)).select(series.columns)]).sort("unique_id", "ds")
