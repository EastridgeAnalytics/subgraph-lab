"""TGN: PyG's own example (examples/tgn.py in PyTorch Geometric), adapted to score accounts (page 03).

What changed from PyG's version: the data is the run's payments from `to_temporal` instead of the JODIE
Wikipedia edits, with no train, validation and test split and no AP or AUC (the lab makes no accuracy claim
on the score). It is seeded, CPU only and single-threaded, so the same seed repeats a run. The two modules
are PyG's with their last lines folded, and `step` holds the lines PyG writes out in both train() and
test(). After the last epoch the events are replayed in time order and each outgoing payment is scored by
how surprised the model was (one minus its predicted link probability). An account's score is the rank of
its mean surprise over the most recent part of the window: "more surprising than this share of the scored
accounts". The ending hands the scores to `run.stage(df)` and `run.push()`.

    uv run python -m examples.tgn.example
"""
import polars as pl
import torch
from torch_geometric.loader import TemporalDataLoader
from torch_geometric.nn import TransformerConv
from torch_geometric.nn.models.tgn import IdentityMessage, LastAggregator, LastNeighborLoader, TGNMemory

from lab.common import canonical_json, get_logger
from lab.settings import get_settings

log = get_logger("examples.tgn")

MEMORY_DIM = TIME_DIM = EMBEDDING_DIM = 100  # PyG: 100
EPOCHS = 5  # PyG: 50
BATCH_SIZE = 100  # PyG: 200; smaller, so a small slice still gets enough updates
LEARNING_RATE = 0.005  # PyG: 0.0001; higher, for a few epochs instead of fifty
NEIGHBORS = 10  # PyG: 10, the last neighbours each node keeps
RECENT = 0.25  # not in PyG: the latest share of the time window whose payments are scored


class GraphAttentionEmbedding(torch.nn.Module):
    def __init__(self, in_channels, out_channels, msg_dim, time_enc):
        super().__init__()
        self.time_enc = time_enc
        self.conv = TransformerConv(in_channels, out_channels // 2, heads=2, dropout=0.1,
                                    edge_dim=msg_dim + time_enc.out_channels)

    def forward(self, x, last_update, edge_index, t, msg):
        rel_t = last_update[edge_index[0]] - t
        edge_attr = torch.cat([self.time_enc(rel_t.to(x.dtype)), msg], dim=-1)
        return self.conv(x, edge_index, edge_attr)


class LinkPredictor(torch.nn.Module):
    def __init__(self, in_channels):
        super().__init__()
        self.lin_src = torch.nn.Linear(in_channels, in_channels)
        self.lin_dst = torch.nn.Linear(in_channels, in_channels)
        self.lin_final = torch.nn.Linear(in_channels, 1)

    def forward(self, z_src, z_dst):
        return self.lin_final((self.lin_src(z_src) + self.lin_dst(z_dst)).relu())


def surprise(data, num_nodes: int, seed: int) -> torch.Tensor:
    """Train on the events in time order, replay them, and return one surprise in [0, 1] per event."""
    torch.manual_seed(seed)
    dim = data.msg.size(-1)
    memory = TGNMemory(num_nodes, dim, MEMORY_DIM, TIME_DIM, IdentityMessage(dim, MEMORY_DIM, TIME_DIM),
                       LastAggregator())
    gnn = GraphAttentionEmbedding(MEMORY_DIM, EMBEDDING_DIM, dim, memory.time_enc)
    link_pred = LinkPredictor(EMBEDDING_DIM)
    # PyG joins three sets of parameters (memory and gnn share the time encoder); a ModuleList does the same
    optimizer = torch.optim.Adam(torch.nn.ModuleList([memory, gnn, link_pred]).parameters(), lr=LEARNING_RATE)
    criterion = torch.nn.BCEWithLogitsLoss()
    neighbor_loader = LastNeighborLoader(num_nodes, size=NEIGHBORS)
    assoc = torch.empty(num_nodes, dtype=torch.long)  # helper vector: global node index -> row of z

    def step(batch, *neg_dst):  # PyG's train() and test() share these lines: one logit set per destination
        n_id = torch.cat([batch.src, batch.dst, *neg_dst]).unique()  # all nodes involved in the batch
        n_id, edge_index, e_id = neighbor_loader(n_id)
        assoc[n_id] = torch.arange(n_id.size(0))
        z = gnn(*memory(n_id), edge_index, data.t[e_id], data.msg[e_id])  # updated memory of all those nodes
        out = [link_pred(z[assoc[batch.src]], z[assoc[dst]]) for dst in (batch.dst, *neg_dst)]
        memory.update_state(batch.src, batch.dst, batch.t, batch.msg)  # update with the ground-truth state
        neighbor_loader.insert(batch.src, batch.dst)
        return out

    for epoch in range(1, EPOCHS + 1):
        memory.train(), gnn.train(), link_pred.train()
        memory.reset_state(), neighbor_loader.reset_state()  # a fresh memory and an empty graph
        total_loss = 0.0
        for batch in TemporalDataLoader(data, BATCH_SIZE, neg_sampling_ratio=1.0):
            optimizer.zero_grad()
            pos_out, neg_out = step(batch, batch.neg_dst)
            loss = criterion(pos_out, torch.ones_like(pos_out)) + criterion(neg_out, torch.zeros_like(neg_out))
            loss.backward()
            optimizer.step()
            memory.detach()
            total_loss += loss.item() * batch.num_events
        log.info("tgn epoch %d: loss %.4f", epoch, total_loss / data.num_events)

    memory.eval(), gnn.eval(), link_pred.eval()
    memory.reset_state(), neighbor_loader.reset_state()
    with torch.no_grad():  # PyG's test(); the replay keeps one number per event: 1 - the predicted probability
        return torch.cat([step(b)[0] for b in TemporalDataLoader(data, BATCH_SIZE)]).neg().sigmoid().view(-1)


def run_model(run, keys: list[str] | None = None) -> int:
    """Write `artifacts/models/tgn.parquet` (key, score, evidence) for the slice `keys`; return its rows.

    The score is the mid-rank percentile of the account's mean surprise: in (0, 1), and ties share a rank. An
    account with no outgoing payment in the latest `RECENT` of the window has no row: the model had no say.
    """
    graph = run.to_temporal(keys=keys)
    data = graph.data
    threads = torch.get_num_threads()
    torch.set_num_threads(1)  # floats added in parallel have no fixed order, so a seed alone would not repeat
    try:
        s = surprise(data, len(graph.keys), get_settings().seed) if data.num_events else torch.empty(0)
    finally:
        torch.set_num_threads(threads)
    events = pl.DataFrame({"src": data.src.numpy(), "t": data.t.numpy(), "s": s.numpy()})
    recent = pl.col("t") >= pl.col("t").min() + (1 - RECENT) * (pl.col("t").max() - pl.col("t").min())
    mean = events.filter(recent).group_by("src").agg(n=pl.len(), mean=pl.col("s").mean())
    scores = pl.DataFrame({
        "key": [graph.keys[i] for i in mean["src"]],
        "score": (mean["mean"].rank("average") - 0.5) / mean.height,
        "evidence": [canonical_json({"events": n, "mean_surprise": round(m, 4)})
                     for n, m in zip(mean["n"], mean["mean"], strict=True)],
    }, schema={"key": pl.Utf8, "score": pl.Float64, "evidence": pl.Utf8}).sort("key")
    path = run.paths.artifacts / "models" / "tgn.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    scores.write_parquet(path)
    return scores.height


if __name__ == "__main__":
    import lab
    from lab.slice.search import current_slice

    run = lab.run()
    run_model(run, current_slice(run))
    df = pl.read_parquet(run.paths.artifacts / "models" / "tgn.parquet")
    run.stage(df.select(pl.col("key").alias(run.recipe.graph.account_key), pl.col("score").alias("risk_tgn")))
    print(run.push())
