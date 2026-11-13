"""Communities (page 04, level three): who is grouped with whom inside the slice, and how to put the groups in the run store.

`find_communities(run, keys)` takes the slice as the account graph the NetworkX adapter builds (a payment links two
accounts, a shared device, email or address links them harder, `lab/adapters/nx.py`) and finds the groups in two
steps:

1. Connected components. A component with no more than `LAB_COMPONENT_MIN` accounts is one community as it stands.
2. Seeded Louvain (`LAB_SEED`, `LAB_LOUVAIN_RESOLUTION`), on the components that are bigger than that. Louvain is
   the only reason a component is ever cut.

An account with no link is not a community, and neither is a Louvain group of one. The result is the same for the
same seed, across runs and across Python processes: a component is handed to Louvain with its nodes and edges
sorted, so neither set order nor hash randomisation reaches it.

`ring_recall` is the A11 measurement: of the planted rings, how many came out whole, and how many look-alike groups
landed in a community that scores like an alert. It reports; it asserts nothing.

The store half. `embed_communities` gives each community the mean of its members' `risk_embedding`, and
`write_communities` puts every community into the run store as a `RiskCommunity` node (`embedding`, `score`, `size`,
`run_id`; `summary` is track 3b's) with `IN_RISK_COMMUNITY` links from its members, so "communities like this one" can
be a hybrid search over `community_embedding` and `community_summary` (page 02).
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import networkx as nx
import numpy as np
import polars as pl

from lab.adapters import _read, to_networkx
from lab.common import get_logger
from lab.contracts.factors import Policy
from lab.contracts.graph import COMMUNITY_KEY, COMMUNITY_LABEL, EMBEDDING_PROP, IN_COMMUNITY
from lab.roundtrip.indexes import COMMUNITY_EMBEDDING_PROP
from lab.roundtrip.loader import load_rows
from lab.roundtrip.queries import ident
from lab.scoring.policy import _EDGE
from lab.settings import get_settings

if TYPE_CHECKING:
    from lab.runs import Run

log = get_logger("scoring.communities")

COMMUNITIES_FILE = "communities.parquet"  # in the run's artifacts: every scored community with its members, for 3b


# ---- finding ---------------------------------------------------------------------------------------------------
def find_communities(run: Run, keys: list[str] | None = None) -> list[set[str]]:
    """The communities of the slice (`keys`; None is every account in the run store), as sets of account keys.

    Biggest first, ties by lowest member key. Every set has at least two accounts: it is not yet the policy's
    `min_size`, which `score_communities` applies.
    """
    G = to_networkx(run, keys=keys)
    groups = split_graph(G)
    log.info(
        "communities: %d accounts, %d links, %d communities (%d identifiers too shared to link)",
        G.number_of_nodes(), G.number_of_edges(), len(groups), G.graph.get("skipped_identifiers", 0),
    )
    return groups


def split_graph(
    G: nx.Graph, component_min: int | None = None, resolution: float | None = None, seed: int | None = None
) -> list[set[str]]:
    """Components, then Louvain on the ones bigger than `component_min`. The knobs default to the settings.

    `G` is the weighted graph of `to_networkx(how="communities")`: edges carry `weight`.
    """
    s = get_settings()
    component_min = s.component_min if component_min is None else component_min
    resolution = s.louvain_resolution if resolution is None else resolution
    seed = s.seed if seed is None else seed
    found: list[set[str]] = []
    for component in nx.connected_components(G):
        if len(component) < 2:
            continue  # an account with no link is not a community
        parts = _louvain(G, component, resolution, seed) if len(component) > component_min else [set(component)]
        found += [set(p) for p in parts if len(p) >= 2]
    return sorted(found, key=lambda c: (-len(c), min(c)))


def _louvain(G: nx.Graph, component: set[str], resolution: float, seed: int) -> list[set[str]]:
    """Seeded Louvain on one component, built with its nodes and edges in sorted order.

    A subgraph view walks its nodes in set order, which changes with Python's string hashing from one process to the
    next; Louvain shuffles the node list it is given, so the order it starts from has to be fixed for the seed to
    mean the same thing every time.
    """
    H = nx.Graph()
    H.add_nodes_from(sorted(component))
    edges = sorted((min(u, v), max(u, v), w) for u, v, w in G.subgraph(component).edges(data="weight"))
    H.add_edges_from((u, v, {"weight": w}) for u, v, w in edges)
    return nx.community.louvain_communities(H, weight="weight", resolution=resolution, seed=seed)


# ---- A11: ring recall ------------------------------------------------------------------------------------------
def ring_recall(
    communities: pl.DataFrame | Iterable[Iterable[str]],
    truth: pl.DataFrame,
    alert_threshold: float | None = None,
    mostly: float = 0.5,
) -> dict[str, Any]:
    """How the communities line up with the planted rings and look-alikes (A11). Reports; never raises on a miss.

    `communities` is the scored table (`member_keys` and, for the look-alike count, `score`: what
    `score_communities` returns and `communities.parquet` holds), or just the member sets `find_communities`
    returns. `truth` has `account_id`, `ring_id` and `lookalike_id` (empty or null for none), as the fixture's
    `ground_truth.csv`. A group's accounts that are in no community count as apart. Restrict `truth` to the slice
    first when the run searched one, or the accounts outside it count as missed.

    - `whole`: every member of the ring is in one community.
    - `mostly_whole`: more than `mostly` of the ring's members are in one community. It includes the whole ones.
    - `lookalikes_in_high_scoring_community`: look-alike groups whose biggest community scores at or above
      `alert_threshold` (the policy file's, when not given). None when the communities carry no score.

    A group's biggest community is the one holding most of its members; a tie goes to the higher score.
    """
    groups, scores = _as_groups(communities)
    scored = isinstance(communities, pl.DataFrame) and "score" in communities.columns
    if scored and alert_threshold is None:
        alert_threshold = _policy_threshold()

    rings = _truth_groups(truth, "ring_id")
    lookalikes = _truth_groups(truth, "lookalike_id")
    by_ring = {name: _together(members, groups, scores) for name, members in rings.items()}
    for r in by_ring.values():
        r["whole"] = r["together"] == r["members"]
        r["mostly_whole"] = r["share"] > mostly
    by_lookalike = {name: _together(members, groups, scores) for name, members in lookalikes.items()}
    for r in by_lookalike.values():
        score = r["community_score"]
        r["high"] = None if not scored else score is not None and score >= alert_threshold - _EDGE
    return {
        "rings": len(by_ring),
        "whole": sum(r["whole"] for r in by_ring.values()),
        "mostly_whole": sum(r["mostly_whole"] for r in by_ring.values()),
        "mostly_share": mostly,
        "by_ring": by_ring,
        "lookalikes": len(by_lookalike),
        "lookalikes_in_high_scoring_community": sum(bool(r["high"]) for r in by_lookalike.values()) if scored else None,
        "alert_threshold": alert_threshold if scored else None,
        "by_lookalike": by_lookalike,
    }


def _as_groups(communities: pl.DataFrame | Iterable[Iterable[str]]) -> tuple[list[set[str]], list[float | None]]:
    if isinstance(communities, pl.DataFrame):
        groups = [set(m) for m in communities["member_keys"].to_list()]
        scores = communities["score"].to_list() if "score" in communities.columns else [None] * len(groups)
        return groups, scores
    groups = [set(c) for c in communities]
    return groups, [None] * len(groups)


def _truth_groups(truth: pl.DataFrame, column: str) -> dict[str, set[str]]:
    named = truth.filter(pl.col(column).is_not_null() & (pl.col(column) != ""))
    out: dict[str, set[str]] = {}
    for account, name in zip(named["account_id"], named[column], strict=True):
        out.setdefault(name, set()).add(account)
    return dict(sorted(out.items()))


def _together(members: set[str], groups: list[set[str]], scores: list[float | None]) -> dict[str, Any]:
    """How many of `members` the biggest community holds, which one that is, and its size and score."""
    best, best_score, best_size = 0, None, 0
    for group, score in zip(groups, scores, strict=True):
        n = len(members & group)
        if n == 0:
            continue
        if n > best or (n == best and score is not None and (best_score is None or score > best_score)):
            best, best_score, best_size = n, score, len(group)
    return {
        "members": len(members), "together": best, "share": best / len(members),
        "community_size": best_size, "community_score": best_score,
    }


def _policy_threshold() -> float:
    return Policy.load(get_settings().policy_file).alert_threshold


# ---- the run store ---------------------------------------------------------------------------------------------
def embed_communities(run: Run, df: pl.DataFrame) -> pl.DataFrame:
    """`df` (one row per community, with `member_keys`) plus `embedding`: the mean of its members' `risk_embedding`.

    Only members that have one count. A community none of whose members has one gets null. The column is always
    `List(Float64)`, so an empty or embedding-less run still has the same shape.
    """
    g = run.recipe.graph
    members = sorted({m for keys in df["member_keys"].to_list() for m in keys})
    vectors: dict[str, np.ndarray] = {}
    for batch in _read.key_batches(members):
        found = run.table(
            f"MATCH (a:{ident(g.account_label)}) WHERE a.{ident(g.account_key)} IN $keys "
            f"AND a.{ident(EMBEDDING_PROP)} IS NOT NULL "
            f"RETURN a.{ident(g.account_key)} AS key, a.{ident(EMBEDDING_PROP)} AS v",
            {"keys": batch},
        )
        vectors.update({str(k): np.asarray(v, dtype=np.float64) for k, v in found.iter_rows()})
    means: list[list[float] | None] = []
    for keys in df["member_keys"].to_list():
        have = [vectors[m] for m in keys if m in vectors]
        means.append(np.mean(have, axis=0).tolist() if have else None)
    return df.with_columns(pl.Series("embedding", means, dtype=pl.List(pl.Float64)))


def for_stage(run: Run, df: pl.DataFrame) -> pl.DataFrame:
    """The table `run.stage(df, "community_key", "community")` takes: `df` without `embedding` when it should not go
    back, which is when the recipe does not write vectors back (`write_back.vectors`) or no member has one yet."""
    if run.recipe.write_back.vectors and df["embedding"].null_count() < df.height:
        return df
    return df.drop("embedding")


def add_community_keys(run: Run, scores: pl.DataFrame, df: pl.DataFrame) -> pl.DataFrame:
    """`scores` plus `risk_community_key`: the community each account is in, null for an account in none."""
    key = run.recipe.graph.account_key
    pairs = [
        (member, community)
        for community, members in zip(df["community_key"], df["member_keys"], strict=True)
        for member in members
    ]
    link = pl.DataFrame(
        {key: [m for m, _ in pairs], "risk_community_key": [c for _, c in pairs]},
        schema={key: pl.Utf8, "risk_community_key": pl.Utf8},
    )
    return scores.drop("risk_community_key", strict=False).join(link, on=key, how="left")


def write_communities(run: Run, df: pl.DataFrame) -> int:
    """Put every community of `df` into the run store as a `RiskCommunity` node, linked from its members.

    The nodes of an earlier `score` on this run are removed first, so the store holds this result only. Each node has
    `community_key`, `run_id`, `score`, `size` and, when it has one, `embedding`; `summary` is left for track 3b. The
    run store is the lab's working copy, not the master graph: nothing here reaches Aura. Returns the nodes written.
    """
    g = run.recipe.graph
    conn = run.store()
    label, key = ident(COMMUNITY_LABEL), ident(COMMUNITY_KEY)
    conn.execute(f"MATCH (c:{label}) DETACH DELETE c")
    if df.is_empty():
        return 0
    nodes, links = [], []
    for r in df.iter_rows(named=True):
        props = {"run_id": r["run_id"], "score": r["score"], "size": r["size"]}
        if r.get("embedding") is not None:
            props[COMMUNITY_EMBEDDING_PROP] = r["embedding"]
        nodes.append({"key": r[COMMUNITY_KEY], "props": props})
        links += [{"community": r[COMMUNITY_KEY], "member": m} for m in r["member_keys"]]
    load_rows(conn, f"UNWIND $rows AS r MERGE (c:{label} {{{key}: r.key}}) SET c += r.props", nodes)
    load_rows(
        conn,
        f"UNWIND $rows AS r MATCH (a:{ident(g.account_label)} {{{ident(g.account_key)}: r.member}}) "
        f"MATCH (c:{label} {{{key}: r.community}}) MERGE (a)-[:{ident(IN_COMMUNITY)}]->(c)",
        links,
    )
    return len(nodes)
