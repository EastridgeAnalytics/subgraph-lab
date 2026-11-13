"""The first pass (page 02): every account gets an embedding and a template summary, so the run store can be searched.

Node2Vec comes from PyG's own example (`examples/node2vec`, decision 34) and runs on the slice graph, accounts and
their identifiers, on the CPU. The summary comes from `lab.slice.summaries`. Both are written on the account in the
run store: `risk_embedding` (pushed back later) and `lab_summary` (stays here). The vector and full-text indexes
already exist (`create_indexes` ran after the pull) and Relate indexes the values as they land.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from lab.adapters import to_pyg
from lab.common.log import get_logger
from lab.contracts.graph import EMBEDDING_PROP, SUMMARY_PROP
from lab.roundtrip.loader import load_rows
from lab.roundtrip.queries import ident
from lab.settings import get_settings
from lab.slice.summaries import account_summaries

if TYPE_CHECKING:
    from lab.runs import Run

log = get_logger("slice.first_pass")


def first_pass(run: Run) -> dict[str, int]:
    """Embed and summarize every account in the run store; record the `first_pass` step; return its counts.

    Running it again replaces both properties. A failure marks the step failed and raises.
    """
    # The one training loop lives in the example; imported here so that `lab.slice` loads without torch.
    from examples.node2vec import embed

    settings = get_settings()
    run.reload()
    manifest = run.manifest
    manifest.start("first_pass")
    run.save()
    started = time.perf_counter()
    try:
        graph = to_pyg(run, nodes="all")
        if not graph.account_index:
            raise ValueError("the run store has no accounts; pull a slice before the first pass")
        z = embed(graph, settings.embedding_dim, settings.seed)
        summaries = dict(account_summaries(run).iter_rows())
        rows = [
            {"key": key, "v": z[i].tolist(), "s": summaries[key]}
            for key, i in graph.account_index.items()
        ]
        g = run.recipe.graph
        stats = load_rows(
            run.store(),
            f"UNWIND $rows AS r MATCH (a:{ident(g.account_label)} {{{ident(g.account_key)}: r.key}}) "
            f"SET a.{ident(EMBEDDING_PROP)} = r.v, a.{ident(SUMMARY_PROP)} = r.s",
            rows,
        )
    except Exception as e:
        manifest.fail("first_pass", f"{type(e).__name__}: {e}"[:500])
        run.save()
        raise
    seconds = time.perf_counter() - started
    counts = {
        "accounts": len(rows),
        "embeddings": len(rows),
        "summaries": len(rows),
        "graph_nodes": graph.data.num_nodes,
        "graph_edges": graph.data.edge_index.shape[1] // 2,  # edge_index holds both directions
        "embedding_dim": settings.embedding_dim,
        "write_batches": stats.batches,
    }
    manifest.finish("first_pass", counts, note=f"{seconds:.1f}s")
    run.save()
    log.info("first pass: %d accounts embedded and summarized in %.1fs", len(rows), seconds)
    return counts
