"""The NetworkX adapter: the slice as a weighted account graph for communities, or the raw property graph."""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import polars as pl

from lab.adapters import _read
from lab.common.log import get_logger
from lab.settings import get_settings

if TYPE_CHECKING:
    import networkx as nx

    from lab.runs import Run

log = get_logger("adapters.nx")

PAYMENT = "payment"  # the `kinds` entry for a payment link; identifiers use their `IdentifierLink.kind`


def to_networkx(
    run: Run, keys: list[str] | None = None, how: Literal["communities", "raw"] = "communities"
) -> nx.Graph | nx.MultiDiGraph:
    """The slice as a NetworkX graph. `keys` are the slice's account keys; None is the whole run store.

    `how="communities"`: an undirected `nx.Graph` whose nodes are the account keys, every account in scope even
    with no link. Two accounts are linked by a payment between them, either way, however many (adds
    `weight_payment` once), and by each identifier they share (adds that kind's weight once: `weight_device`,
    `weight_email`, `weight_address`). An edge has `weight`, the sum, and `kinds`, the sorted list of what linked
    the pair. An identifier shared by more than `shared_max` accounts in scope links none of them; the count of
    those is `G.graph["skipped_identifiers"]`. An identifier kind with no weight setting (phone, other) links
    nothing and is named in `G.graph["unweighted_kinds"]`.

    `how="raw"`: an `nx.MultiDiGraph` of the slice as the store holds it, built from the cursor's `.graph()` as
    in Relate's skill reference: nodes keyed by Relate's node id with `labels` and their properties, edges with
    `type` and theirs. It holds the accounts, the payments between them, and the identifier nodes with their links.
    """
    if how == "communities":
        return _communities(run, keys)
    if how == "raw":
        return _raw(run, keys)
    raise ValueError(f"how must be 'communities' or 'raw', not {how!r}")


def _communities(run: Run, keys: list[str] | None) -> nx.Graph:
    import networkx as nx

    s = get_settings()
    weights = {"device": s.weight_device, "email": s.weight_email, "address": s.weight_address}
    ident_key = ["kind", "label", "identifier"]

    acc = _read.accounts(run, keys)
    links = _read.identifiers(run, keys)
    unweighted = sorted(set(links["kind"]) - set(weights))
    if unweighted:
        log.warning("to_networkx: no link weight for identifier kinds %s; they link nothing", unweighted)
    links = links.filter(pl.col("kind").is_in(list(weights)))

    # Each identifier links the accounts that share it, unless too many do.
    sizes = links.group_by(ident_key).len()
    skipped = sizes.filter(pl.col("len") > s.shared_max).height
    shared = links.join(sizes.filter(pl.col("len").is_between(2, s.shared_max)).select(ident_key), on=ident_key)
    by_identifier = (
        shared.join(shared, on=ident_key, suffix="_b")
        .filter(pl.col("account") < pl.col("account_b"))
        .select(u="account", v="account_b", kind="kind")
        .with_columns(w=pl.col("kind").replace_strict(weights, return_dtype=pl.Float64))
    )

    # A payment links its two accounts once, whichever way it went and however many there were.
    pairs = _read.payments(run, keys, detail=False).filter(pl.col("src") != pl.col("dst"))
    by_payment = (
        pairs.select(
            u=pl.when(pl.col("src") < pl.col("dst")).then(pl.col("src")).otherwise(pl.col("dst")),
            v=pl.when(pl.col("src") < pl.col("dst")).then(pl.col("dst")).otherwise(pl.col("src")),
        )
        .unique()
        .with_columns(kind=pl.lit(PAYMENT), w=pl.lit(s.weight_payment, dtype=pl.Float64))
    )

    edges = (
        pl.concat([by_identifier.select("u", "v", "kind", "w"), by_payment.select("u", "v", "kind", "w")])
        .group_by("u", "v")
        .agg(weight=pl.col("w").sum(), kinds=pl.col("kind").unique().sort())
        .sort("u", "v")
    )

    G = nx.Graph(skipped_identifiers=skipped)
    if unweighted:
        G.graph["unweighted_kinds"] = unweighted
    G.add_nodes_from(acc["key"])
    G.add_edges_from((u, v, {"weight": w, "kinds": k}) for u, v, w, k in edges.iter_rows())
    return G


def _raw(run: Run, keys: list[str] | None) -> nx.MultiDiGraph:
    import networkx as nx

    g = run.recipe.graph
    A, key = _read.q(g.account_label), _read.q(g.account_key)
    keyset = None if keys is None else set(keys)
    store = run.store()
    G = nx.MultiDiGraph()
    for batch in _read.key_batches(keys):
        params = {} if batch is None else {"keys": batch}
        where = "" if batch is None else f"WHERE a.{key} IN $keys"
        # The accounts of this batch with the payments they send, then each kind of identifier link.
        queries = [f"MATCH (a:{A}) {where} OPTIONAL MATCH (a)-[r:{_read.q(g.payment_type)}]->(x:{A}) RETURN a, r, x"] + [
            f"MATCH (a:{A})-[r:{_read.q(link.rel_type)}]->(x:{_read.q(link.label)}) {where} RETURN a, r, x"
            for link in g.identifiers
        ]
        for i, query in enumerate(queries):
            rg = store.execute(query, params).graph()
            # A payment to an account outside the slice is not in the slice (the lookup stays small: see _read).
            keep = {n.id for n in rg.nodes if i > 0 or keyset is None or n.properties.get(g.account_key) in keyset}
            G.add_nodes_from((n.id, {"labels": n.labels, **n.properties}) for n in rg.nodes if n.id in keep)
            G.add_edges_from(
                (r.start_node, r.end_node, {"type": r.type, **r.properties})
                for r in rg.relationships
                if r.start_node in keep and r.end_node in keep
            )
    return G
