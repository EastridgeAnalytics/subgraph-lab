"""NetworkX: communities and money cycling (page 03), on the lab's own community code.

What changed from NetworkX's own examples (`louvain_communities`, `simple_cycles`): the graph is the slice from
`to_networkx`; the communities come from `lab.scoring.communities.split_graph`, which is where Louvain runs for the
lab (decision 34), not from a second copy here; and money cycling is looked for inside each community only, with
`nx.simple_cycles(length_bound=MAX_CYCLE)` on the directed graph of who paid whom. The ending stages how many
cycles each account lies on as `risk_cycles` and previews the push. An account no cycle passes through is staged
with 0: the slice was looked at.

A cycle here is a loop of at most `MAX_CYCLE` accounts where each pays the next and the last pays the first, so two
accounts that pay each other are a cycle of two. Payments between the same pair count once. A family that shares
money back and forth cycles as much as a mule ring does; the community score weighs it with the other parts.

    uv run python -m examples.networkx.example
"""
from collections import Counter

import networkx as nx
import polars as pl

from lab.adapters import to_networkx
from lab.scoring import communities

MAX_CYCLE = 6  # accounts in the longest loop looked for; the community score uses the same bound. To measure on 1M


def payment_graph(run, keys: list[str] | None = None) -> nx.DiGraph:
    """Who paid whom among the slice's accounts: a node per account key, one edge per paying pair, no self-loops."""
    g = run.recipe.graph
    raw = to_networkx(run, keys=keys, how="raw")
    name = {n: d[g.account_key] for n, d in raw.nodes(data=True) if g.account_label in d["labels"]}
    pays = nx.DiGraph()
    pays.add_edges_from((name[u], name[v]) for u, v, d in raw.edges(data=True) if d["type"] == g.payment_type and u != v)
    return pays


def cycles_by_account(run, keys: list[str] | None = None) -> pl.DataFrame:
    """`key` and `cycles` for every account in the slice: the loops of at most `MAX_CYCLE` accounts it lies on."""
    G = to_networkx(run, keys=keys)  # accounts linked by payments and shared identifiers, for the communities
    pays = payment_graph(run, keys)
    count: Counter[str] = Counter()
    for members in communities.split_graph(G):
        for loop in nx.simple_cycles(pays.subgraph(members), length_bound=MAX_CYCLE):
            count.update(loop)
    accounts = sorted(G.nodes)
    return pl.DataFrame({"key": accounts, "cycles": [count[a] for a in accounts]}, schema={"key": pl.Utf8, "cycles": pl.Int64})


if __name__ == "__main__":
    import lab
    from lab.slice.search import current_slice

    run = lab.run()
    df = cycles_by_account(run, current_slice(run))
    run.stage(df.select(pl.col("key").alias(run.recipe.graph.account_key), pl.col("cycles").alias("risk_cycles")))
    print(run.push())
