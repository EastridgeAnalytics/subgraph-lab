"""Arrow to Polars, and a query straight to a Polars frame."""
from __future__ import annotations

import polars as pl
import pyarrow as pa


def arrow_to_polars(table: pa.Table) -> pl.DataFrame:
    return pl.from_arrow(table)  # type: ignore[return-value]


def query_frame(conn, query: str, params: dict | None = None) -> pl.DataFrame:
    """Run a Cypher statement on a Relate connection and return a Polars frame through Arrow."""
    return arrow_to_polars(conn.execute(query, params or {}).to_arrow())
