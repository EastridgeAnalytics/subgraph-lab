"""NetworkX: communities and money cycling (page 03), on the lab's own community and cycle code.

What changed from NetworkX's own examples (`louvain_communities`, `simple_cycles`): the communities come from
`lab.scoring.communities.find_communities`, which is where grouping and Louvain run for the lab (decision 34), not from a
second copy here; and money cycling is looked for inside each community only, by `lab.scoring.signals.cycles_by_group`,
which is where cycles are found for the lab (the community score and the evidence call it too). The ending stages how
many cycles each account lies on as `risk_cycles` and previews the push. An account no cycle passes through is staged
with 0: the slice was looked at.

A cycle here is a loop of at most `LAB_CYCLE_MAX_LENGTH` accounts where each pays the next and the last pays the first, and
each payment comes after the one before it, so two accounts that pay each other, one first and then the other, are a
cycle of two. The same loop paid many times counts once. A family that shares money back and forth cycles as much as a
mule ring does; the community score weighs it with the other parts.

    uv run python -m examples.networkx.example
"""
from collections import Counter

import polars as pl

from lab.adapters import reads
from lab.scoring import communities, signals


def cycles_by_account(run, keys: list[str] | None = None) -> pl.DataFrame:
    """`key` and `cycles` for every account in the slice: the cycles it lies on, inside its community."""
    groups = communities.find_communities(run, keys)
    pay = reads.payments(run, keys)
    pay = pay.with_columns(ts=reads.utc_naive(pay["ts"]))
    found = signals.cycles_by_group(pay, {account: i for i, members in enumerate(groups) for account in members})
    count = Counter(account for cycles in found.values() for loop in cycles for account in loop)
    accounts = reads.accounts(run, keys)["key"].to_list()
    return pl.DataFrame({"key": accounts, "cycles": [count[a] for a in accounts]}, schema={"key": pl.Utf8, "cycles": pl.Int64})


if __name__ == "__main__":
    import lab
    from lab.slice.search import current_slice

    run = lab.run()
    df = cycles_by_account(run, current_slice(run))
    run.stage(df.select(pl.col("key").alias(run.recipe.graph.account_key), pl.col("cycles").alias("risk_cycles")))
    print(run.push())
