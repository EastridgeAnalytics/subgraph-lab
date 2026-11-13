"""Velocity: Nixtla's statsforecast quick start (one series per `unique_id`, `StatsForecast(models, freq).forecast`),
adapted to score accounts (page 03).

What changed from Nixtla's version: the series are each account's daily outflow from `to_series`, not the M4 sample;
the last 14 days are held out and forecast with `CrostonClassic` (a model for series that are mostly zero) from each
account's own history, and instead of plotting the forecast against the data, the total of the held-out days is
compared with the forecast total. An account's score is the share of its recent outflow that its own history does not
explain. The forecasting is `lab.scoring.velocity.score`, which is also the `velocity_anomaly` factor in
`factors/factors.yaml`; this file is the example's ending: stage the score as `risk_velocity` and preview the push.
It is the same number the factor gives, so the score is not worked out twice (decision 34).

    uv run python -m examples.velocity.example
"""
import polars as pl


def risk_velocity(run, keys: list[str] | None = None) -> pl.DataFrame:
    """The staged table: the account key and `risk_velocity`, for each account with enough history to forecast."""
    from lab.scoring.velocity import score

    scored = score(run, keys=keys)
    return scored.select(pl.col("key").alias(run.recipe.graph.account_key), pl.col("score").alias("risk_velocity"))


if __name__ == "__main__":
    import lab
    from lab.slice.search import current_slice

    run = lab.run()
    run.stage(risk_velocity(run, current_slice(run)))
    print(run.push())
