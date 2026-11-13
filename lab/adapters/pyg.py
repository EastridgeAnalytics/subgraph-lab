"""The PyTorch Geometric adapter: the slice as a `Data` object (page 03)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import numpy as np
import polars as pl

from lab.adapters import reads

if TYPE_CHECKING:
    from torch_geometric.data import Data

    from lab.runs import Run


@dataclass
class PygGraph:
    """`data` plus the maps back to the store. Index `i` of `data` is `node_keys[i]`."""

    data: Data
    node_keys: list[str]  # "Label:key" per node index
    node_labels: list[str]  # the label per node index
    account_index: dict[str, int]  # account key -> node index


def to_pyg(run: Run, keys: list[str] | None = None, nodes: Literal["all", "accounts"] = "all") -> PygGraph:
    """The slice as an undirected PyG graph, with no features: Node2Vec and GraphSAGE take the structure.

    `keys` are the slice's account keys; None is the whole run store. Accounts come first, sorted by key, so an
    account's index is the same here and in `to_temporal`. Then, with `nodes="all"`, the identifier nodes the
    slice's accounts link to (device, email, address and the rest of the recipe's mapping), sorted by label and
    key, with an edge from each account to each of its identifiers. Payments between accounts in scope are edges
    in both modes, one per pair however many payments there are. An identifier no account in scope links to is
    not a node. `edge_index` is long, [2, E], with both directions of every edge.
    """
    if nodes not in ("all", "accounts"):
        raise ValueError(f"nodes must be 'all' or 'accounts', not {nodes!r}")
    import torch
    from torch_geometric.data import Data
    from torch_geometric.utils import to_undirected

    g = run.recipe.graph
    acc = _indexed(reads.accounts(run, keys))
    n_acc = acc.height
    node_keys = [f"{g.account_label}:{k}" for k in acc["key"]]
    node_labels = [g.account_label] * n_acc

    pairs = reads.payments(run, keys, detail=False)
    edges = [
        pairs.join(acc.rename({"key": "src", "idx": "s"}), on="src")
        .join(acc.rename({"key": "dst", "idx": "d"}), on="dst")
        .select(pl.col("s").alias("src"), pl.col("d").alias("dst"))
    ]

    if nodes == "all":
        links = reads.identifiers(run, keys)
        ident = _indexed(links.select("label", "identifier").unique().sort("label", "identifier"), offset=n_acc)
        node_keys += [f"{lab}:{k}" for lab, k in zip(ident["label"], ident["identifier"], strict=True)]
        node_labels += ident["label"].to_list()
        edges.append(
            links.join(acc.rename({"key": "account", "idx": "src"}), on="account")
            .join(ident.rename({"idx": "dst"}), on=["label", "identifier"])
            .select("src", "dst")
        )

    both = pl.concat(edges)
    num_nodes = len(node_keys)
    edge_index = torch.from_numpy(np.stack([both["src"].to_numpy(), both["dst"].to_numpy()]))
    edge_index = to_undirected(edge_index, num_nodes=num_nodes)  # both directions, duplicates merged
    return PygGraph(
        data=Data(edge_index=edge_index, num_nodes=num_nodes),
        node_keys=node_keys,
        node_labels=node_labels,
        account_index=dict(zip(acc["key"], acc["idx"], strict=True)),
    )


def _indexed(df: pl.DataFrame, offset: int = 0) -> pl.DataFrame:
    """The frame with an Int64 column `idx` counting rows from `offset`."""
    return df.with_row_index("idx", offset=offset).with_columns(pl.col("idx").cast(pl.Int64))
