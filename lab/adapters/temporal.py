"""The temporal adapter: payments as a `TemporalData` event stream in the Temporal Graph Benchmark layout."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import polars as pl

from lab.adapters import reads
from lab.common.log import get_logger

if TYPE_CHECKING:
    from torch_geometric.data import TemporalData

    from lab.runs import Run

log = get_logger("adapters.temporal")


@dataclass
class TemporalGraph:
    """`data` plus the maps back to the store. Event `i` is payment `tx_ids[i]`; node `j` is account `keys[j]`."""

    data: TemporalData
    keys: list[str]  # node index -> account key (every account in scope, sorted, even one with no payments)
    tx_ids: list[str]  # event order -> payment key


def to_temporal(run: Run, keys: list[str] | None = None) -> TemporalGraph:
    """The payments between accounts in scope as the four arrays the Temporal Graph Benchmark uses.

    `src` and `dst` are long account indices, the same indices `to_pyg` gives the accounts; `t` is int64 epoch
    seconds (UTC); `msg` is float32, [E, 1], the log of one plus the amount. Events are sorted by `t` ascending,
    ties by payment key, so the order is the same on every read. A payment with no time cannot be placed in a
    stream and is left out (logged). A missing or negative amount counts as zero.

    PyG's `data.num_nodes` is the highest index plus one, which undercounts accounts that never pay or get paid
    at the end of the index; a model that needs every node takes `len(keys)`.
    """
    from torch_geometric.data import TemporalData

    acc = reads.accounts(run, keys).with_row_index("idx").with_columns(pl.col("idx").cast(pl.Int64))
    pay = reads.payments(run, keys)
    no_time = pay["ts"].null_count()
    if no_time:
        log.warning("to_temporal: %d of %d payments have no time and are left out", no_time, pay.height)
    pay = pay.filter(pl.col("ts").is_not_null())
    pay = (
        pay.with_columns(
            t=reads.utc_naive(pay["ts"]).dt.epoch("s"),
            m=pl.col("amount").fill_null(0.0).clip(lower_bound=0.0).log1p().cast(pl.Float32),
        )
        .join(acc.rename({"key": "src", "idx": "s"}), on="src")
        .join(acc.rename({"key": "dst", "idx": "d"}), on="dst")
        .sort("t", "tx_id")
    )
    data = TemporalData(
        src=_tensor(pay["s"]),
        dst=_tensor(pay["d"]),
        t=_tensor(pay["t"]),
        msg=_tensor(pay["m"]).reshape(-1, 1),
    )
    return TemporalGraph(data=data, keys=acc["key"].to_list(), tx_ids=pay["tx_id"].to_list())


def _tensor(col: pl.Series):
    """A frame column as a tensor of its own (Polars hands out read-only arrays, which torch will not take)."""
    import torch

    return torch.from_numpy(np.array(col.to_numpy()))
