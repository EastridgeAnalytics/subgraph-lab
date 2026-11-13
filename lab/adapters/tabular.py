"""The table adapter: any Cypher on the run store, as a Polars frame through Arrow."""
from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from lab.runs import Run


def table(run: Run, query: str, params: dict | None = None) -> pl.DataFrame:
    """The same as `run.table`: Relate hands the result over as Arrow, so pandas, Polars and DuckDB read it as is."""
    return run.table(query, params)
