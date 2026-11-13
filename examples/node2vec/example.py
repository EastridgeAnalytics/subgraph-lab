"""Node2Vec: PyG's own example (examples/node2vec.py in PyTorch Geometric), adapted to the lab (page 03).

What changed from PyG's version: the graph is the slice instead of Cora, the width and seed come from the caller,
it is seeded and CPU only, it returns the embeddings instead of plotting them, and the ending hands them to
`run.stage(df)` and `run.push()`. The walks are shorter and fewer, the steps smaller and the learning rate higher,
so a few epochs on a small graph are enough (on the fixture, five epochs take about three seconds and put a
planted ring's members among each other's ten nearest accounts). The first pass (`lab/slice/first_pass.py`) calls
`embed`, so the training loop exists here and nowhere else.

    uv run python -m examples.node2vec.example
"""
from __future__ import annotations

import torch
from torch_geometric.nn import Node2Vec

EPOCHS = 5  # a few; PyG's example trains 100 on Cora. Time on the 1M seed: to measure


def embed(graph, dim: int, seed: int, epochs: int = EPOCHS) -> torch.Tensor:
    """One `dim`-wide embedding per node of `graph` (a `PygGraph` from `to_pyg`), in node index order.

    Same seed, same graph, same run on the same machine gives the same embeddings (random walks are drawn from the
    torch generator that `torch.manual_seed` sets).
    """
    torch.manual_seed(seed)
    model = Node2Vec(
        graph.data.edge_index,
        embedding_dim=dim,
        walk_length=10,  # PyG: 20
        context_size=5,  # PyG: 10
        walks_per_node=5,  # PyG: 10
        num_negative_samples=1,
        p=1.0,
        q=1.0,
        sparse=True,
        num_nodes=graph.data.num_nodes,
    )
    loader = model.loader(batch_size=32, shuffle=True, num_workers=0)  # PyG: 128; num_workers=0: seeded, and Windows
    optimizer = torch.optim.SparseAdam(list(model.parameters()), lr=0.1)  # PyG: 0.01

    model.train()
    for _ in range(epochs):
        for pos_rw, neg_rw in loader:
            optimizer.zero_grad()
            loss = model.loss(pos_rw, neg_rw)
            loss.backward()
            optimizer.step()
    model.eval()
    with torch.no_grad():
        return model().detach().clone()


if __name__ == "__main__":
    import polars as pl

    import lab
    from lab.settings import get_settings

    run = lab.run()
    graph = run.to_pyg(nodes="all")
    z = embed(graph, get_settings().embedding_dim, get_settings().seed)
    keys = list(graph.account_index)  # account keys; their rows come first in `z`
    rows = z[[graph.account_index[k] for k in keys]].tolist()
    run.stage(pl.DataFrame({run.recipe.graph.account_key: keys, "risk_embedding": rows}))
    print(run.push())
