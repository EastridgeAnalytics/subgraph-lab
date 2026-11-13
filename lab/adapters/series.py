"""The series adapter: outflow per account per day, in Nixtla's long format (page 03)."""
from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from lab.adapters import _read

if TYPE_CHECKING:
    from lab.runs import Run

SCHEMA = {"unique_id": pl.Utf8, "ds": pl.Datetime("us"), "y": pl.Float64}


def to_series(run: Run, keys: list[str] | None = None, freq: str = "1d") -> pl.DataFrame:
    """Each account's outflow per period, as `unique_id` (account key), `ds` (naive UTC datetime) and `y` (float).

    `keys` are the slice's account keys; None is the whole run store. The outflow of an account in scope is every
    payment out of it, to an account in scope or not: it is the account's own pattern a forecast is measured
    against. `freq` is a Polars interval ("1d", "1h", "1w"); the default is a day. Every series runs over the same
    window, from the first period with a payment to the last, with zero for a period with none, so the frame is
    regular and statsforecast takes it as it is. An account with no payment out has no series. A payment with no
    time is left out; a missing amount counts as zero.
    """
    pay = _read.outflow(run, keys).filter(pl.col("ts").is_not_null())
    if pay.is_empty():
        return pl.DataFrame(schema=SCHEMA)
    pay = pay.with_columns(ds=_read.utc_naive(pay["ts"]).dt.truncate(freq))
    per_period = pay.group_by("unique_id", "ds").agg(y=pl.col("amount").fill_null(0.0).sum())
    window = pl.datetime_range(pay["ds"].min(), pay["ds"].max(), interval=freq, eager=True).alias("ds")
    grid = pl.DataFrame({"unique_id": per_period["unique_id"].unique()}).join(window.to_frame(), how="cross")
    return (
        grid.join(per_period, on=["unique_id", "ds"], how="left")
        .with_columns(pl.col("y").fill_null(0.0))
        .select(pl.col("unique_id"), pl.col("ds").cast(pl.Datetime("us")), pl.col("y").cast(pl.Float64))
        .sort("unique_id", "ds")
    )
