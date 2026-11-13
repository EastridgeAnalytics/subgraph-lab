"""The staged results table: one format in.

A table with a key column, plus `risk_*` columns. Anything that can produce it can reach the master graph.
"""
from __future__ import annotations

from enum import StrEnum

import polars as pl

from lab.contracts.graph import RISK_PREFIX


class StagedKind(StrEnum):
    ACCOUNT = "account"
    ALERT = "alert"
    COMMUNITY = "community"


_ALLOWED = (pl.Float64, pl.Float32, pl.Int64, pl.Int32, pl.Utf8, pl.String, pl.Boolean)


def staged_errors(df: pl.DataFrame, key: str) -> list[str]:
    """Every way `df` breaks the staged-table contract. Empty means it is valid.

    The key column must exist, be non-null and unique. Every other column starts with `risk_` and holds a float,
    an integer, a string, a boolean, or a list of floats. Timestamps are strings (decision 23).
    """
    errors: list[str] = []
    if key not in df.columns:
        return [f"no key column {key!r}; columns are {df.columns}"]
    if df[key].null_count():
        errors.append(f"key column {key!r} has {df[key].null_count()} nulls")
    if df[key].n_unique() != df.height:
        errors.append(f"key column {key!r} has {df.height - df[key].n_unique()} duplicate keys")
    for name, dtype in df.schema.items():
        if name == key:
            continue
        if not name.startswith(RISK_PREFIX):
            errors.append(f"column {name!r} does not start with {RISK_PREFIX!r}")
            continue
        ok = dtype in _ALLOWED or (isinstance(dtype, pl.List) and dtype.inner in (pl.Float64, pl.Float32))
        if not ok:
            errors.append(f"column {name!r} has type {dtype}; use a number, string, boolean or list of floats")
    return errors
