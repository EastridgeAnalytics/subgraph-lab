"""Track 3a and 5b: communities on the fixture graph, the run store half, `attach_communities`, and ring recall (A11).

The fixture has 200 accounts, 2,000 payments, the mule ring R1 (six accounts on one device and address), the
structuring ring R2 (eight accounts on one email) and the family L1 (four accounts on one laptop and address).
Communities start from shared identifiers (decision 42): R1, R2 and L1 are each one identifier community of exactly
their members, and Louvain then runs on the payments of the rest. `run.stage` is patched where a test needs to see what
would be staged; one test runs the real stage.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import networkx as nx
import polars as pl
import pytest

from lab.adapters import to_networkx
from lab.common import community_key
from lab.common.store import rows
from lab.contracts.graph import COMMUNITY_PROPS
from lab.contracts.manifest import StepStatus
from lab.contracts.staged import staged_errors
from lab.roundtrip.loader import load_rows
from lab.runs import Run
from lab.scoring import score
from lab.scoring.communities import (
    COMMUNITIES_FILE,
    IDENTIFIER,
    PAYMENT,
    Community,
    embed_communities,
    find_communities,
    for_stage,
    identifier_groups,
    payment_graph,
    ring_recall,
    split_graph,
    write_communities,
)
from lab.scoring.pipeline import attach_communities
from lab.settings import reload_settings
from tests.fixtures.make_fixture import L1, R1, R2

REPO = Path(__file__).resolve().parent.parent
KEY = "account_id"


@pytest.fixture
def restore_settings(monkeypatch):
    """Settings a test sets through the environment are forgotten when it ends."""
    yield monkeypatch
    monkeypatch.undo()
    reload_settings()


@pytest.fixture
def staged(fixture_run, monkeypatch) -> dict[str, tuple[pl.DataFrame, str]]:
    """What `run.stage(df, key, kind)` was called with, by kind."""
    calls: dict[str, tuple[pl.DataFrame, str]] = {}

    def fake_stage(df, key=None, kind="account"):
        calls[kind] = (df, key)
        return df.height

    monkeypatch.setattr(fixture_run, "stage", fake_stage)
    return calls


def reread(run: Run) -> Run:
    """The run as the manifest on disk says it is, not as this process holds it."""
    return Run(run.id, run.paths.root.parent.parent)


def scores_frame(run: Run, hot: set[str] = frozenset(), keys: list[str] | None = None) -> pl.DataFrame:
    """Account scores made by hand: 0.9 for `hot`, 0 for the rest, for `keys` (default every account)."""
    acc = keys if keys is not None else run.table("MATCH (a:Account) RETURN a.account_id AS k ORDER BY k")["k"].to_list()
    return pl.DataFrame(
        {KEY: pl.Series(acc, dtype=pl.Utf8), "risk_score": pl.Series([0.9 if k in hot else 0.0 for k in acc], dtype=pl.Float64)}
    )


def embed(run: Run, vectors: dict[str, list[float]]) -> None:
    """Give accounts a `risk_embedding` in the run store, as the first pass would."""
    data = [{"key": k, "v": v} for k, v in vectors.items()]
    load_rows(run.store(), "UNWIND $rows AS r MATCH (a:Account {account_id: r.key}) SET a.risk_embedding = r.v", data)


# ---- small graphs --------------------------------------------------------------------------------------------------
def chain_of_cliques() -> nx.Graph:
    """Three cliques of five (heavy links) joined in a chain by light ones: one component of 15, and one loner."""
    G = nx.Graph()
    for k in range(3):
        nodes = [f"c{k}n{i}" for i in range(5)]
        G.add_edges_from((a, b, {"weight": 3.0}) for i, a in enumerate(nodes) for b in nodes[i + 1:])
    G.add_edge("c0n0", "c1n0", weight=0.5)
    G.add_edge("c1n1", "c2n0", weight=0.5)
    G.add_node("lonely")
    return G


def ring_of_cliques() -> nx.Graph:
    G = nx.relabel_nodes(nx.ring_of_cliques(12, 4), lambda n: f"n{n:03d}")
    nx.set_edge_attributes(G, 1.0, "weight")
    return G


def sizes(groups: list[set[str]]) -> list[int]:
    return sorted(len(g) for g in groups)


# ---- finding -------------------------------------------------------------------------------------------------------
def test_each_planted_group_lands_in_one_community(fixture_run):
    groups = find_communities(fixture_run)
    print(f"\nfixture communities: {len(groups)}, sizes {[len(g) for g in groups]}")
    for name, members in (("R1", R1), ("R2", R2), ("L1", L1)):
        hosts = [g for g in groups if g & set(members)]
        assert len(hosts) == 1 and set(members) <= hosts[0], f"{name} was split or lost"
    assert any(set(R1) == g for g in groups)  # at the shipped settings the ring is its own community


def test_communities_never_overlap_and_are_never_one_account(fixture_run):
    groups = find_communities(fixture_run)
    flat = [m for g in groups for m in g]
    assert len(flat) == len(set(flat)) and all(len(g) >= 2 for g in groups)
    assert [(-len(g), min(g)) for g in groups] == sorted((-len(g), min(g)) for g in groups)  # biggest first
    assert set(flat) == set(to_networkx(fixture_run).nodes)  # every account of the fixture is linked to someone


def test_the_planted_groups_are_identifier_communities_of_exactly_their_members(fixture_run):
    """Decision 42: R1, R2 and the look-alike L1 each share an identifier, so each is a community of its own members and
    nothing else, however much its accounts pay the rest."""
    groups = find_communities(fixture_run)
    by_members = {frozenset(g): g for g in groups}
    for name, members in (("R1", R1), ("R2", R2), ("L1", L1)):
        assert frozenset(members) in by_members, f"{name} is not a community of exactly its members"
        assert by_members[frozenset(members)].formed == IDENTIFIER


def test_a_payment_community_forms_from_the_rest(fixture_run):
    groups = find_communities(fixture_run)
    first = [g for g in groups if g.formed == IDENTIFIER]
    rest = [g for g in groups if g.formed == PAYMENT]
    assert sorted(len(g) for g in first) == [4, 6, 8]  # the family, the mule ring, the structuring ring, and no other
    assert len(rest) > 1 and all(len(g) >= 2 for g in rest)
    taken = set().union(*first)
    assert not taken & set().union(*rest)  # Louvain only sees the accounts the first step left
    assert taken | set().union(*rest) == set(to_networkx(fixture_run).nodes)  # between them, every linked account


def test_a_community_says_how_it_formed_and_equals_the_plain_set(fixture_run):
    groups = find_communities(fixture_run)
    ring = next(g for g in groups if g == set(R1))
    assert isinstance(ring, Community) and ring == set(R1) and ring == frozenset(R1) and ring.formed == IDENTIFIER
    assert Community({"a", "b"}).formed == PAYMENT and Community({"a", "b"}, IDENTIFIER).formed == IDENTIFIER
    with pytest.raises(ValueError, match="forms by"):
        Community({"a", "b"}, "vibes")
    assert [g.formed for g in find_communities(fixture_run, keys=[*R1, *L1])] == [IDENTIFIER, IDENTIFIER]


def test_an_identifier_shared_by_more_than_the_maximum_links_nobody(fixture_run, restore_settings):
    restore_settings.setenv("LAB_SHARED_MAX", "5")  # the ring's device and address are used by six, R2's email by eight
    reload_settings()
    groups = find_communities(fixture_run)
    assert {frozenset(g) for g in groups if g.formed == IDENTIFIER} == {frozenset(L1)}  # the family's four are within the maximum
    assert all(g.formed == PAYMENT for g in groups if g & set(R1) or g & set(R2))  # the others' accounts go to the payments step


def test_an_identifier_component_under_min_size_is_not_a_community_and_goes_to_payments(fixture_run):
    groups = find_communities(fixture_run, min_size=5)  # the family of four is under it
    assert {frozenset(g) for g in groups if g.formed == IDENTIFIER} == {frozenset(R1), frozenset(R2)}
    assert not any(g.formed == IDENTIFIER and g & set(L1) for g in groups)
    assert {m for g in groups for m in g} >= set(L1)  # the family's accounts are not lost: they are in the pool
    assert [g for g in find_communities(fixture_run, min_size=9) if g.formed == IDENTIFIER] == []


def test_identifier_groups_are_the_components_of_the_shared_identifier_graph():
    links = pl.DataFrame({
        "account": ["a", "b", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k"],
        "identifier": ["D1", "D1", "E1", "E1", "D2", "D2", "A9", "A9", "A9", "A9", "X", "Y"],
        "label": ["Device", "Device", "Email", "Email", "Device", "Device", "Address", "Address", "Address", "Address", "Email", "Email"],
        "kind": ["device", "device", "email", "email", "device", "device", "address", "address", "address", "address", "email", "email"],
    })
    # a-b share a device and b-c an email, so a, b, c are one component; d-e a pair; four accounts on one address; j and k alone
    groups, too_shared = identifier_groups(links, shared_max=4, min_size=3)
    assert groups == [{"f", "g", "h", "i"}, {"a", "b", "c"}] and too_shared == 0
    groups, too_shared = identifier_groups(links, shared_max=3, min_size=2)
    assert groups == [{"a", "b", "c"}, {"d", "e"}] and too_shared == 1  # the address of four is too shared to link
    assert identifier_groups(links.head(0), shared_max=4, min_size=2) == ([], 0)


def test_the_payment_graph_links_each_paying_pair_once(fixture_run):
    G = payment_graph(fixture_run, [*R1, *R2])
    assert set(G.nodes) == {*R1, *R2} and all(d["weight"] == 1.0 for _, _, d in G.edges(data=True))
    assert G.has_edge(R1[0], R1[1])  # the ring pays round the loop
    assert not G.has_edge(R2[0], R2[1])  # the eight pay one collector, not each other
    assert payment_graph(fixture_run, []).number_of_nodes() == 0


def test_a_slice_limits_the_communities(fixture_run):
    groups = find_communities(fixture_run, keys=[*R1, *L1])
    assert groups == [set(R1), set(L1)]


def test_accounts_with_no_link_are_not_communities(fixture_run):
    assert find_communities(fixture_run, keys=[R1[0], R2[0]]) == []  # nothing links them
    assert find_communities(fixture_run, keys=[]) == []
    G = chain_of_cliques()
    assert "lonely" not in set().union(*split_graph(G, component_min=30, resolution=1.0, seed=42))


def test_the_same_seed_gives_the_same_communities(fixture_run):
    assert find_communities(fixture_run) == find_communities(fixture_run)
    G = ring_of_cliques()
    assert split_graph(G, 10, 0.5, 7) == split_graph(G, 10, 0.5, 7)


HASH_SCRIPT = """
import json, random, networkx as nx
from lab.scoring.communities import split_graph
rnd = random.Random(3)
G = nx.Graph()
for prefix, n in (("big", 80), ("small", 24)):
    names = [f"{prefix}-{i:03d}" for i in range(n)]
    for i, a in enumerate(names):
        for j in range(i + 1, n):
            near = i // 6 == j // 6
            if near and rnd.random() < 0.8 or not near and rnd.random() < 0.03:
                G.add_edge(a, names[j], weight=rnd.choice([1.0, 1.5, 2.0, 3.0]))
    for i in range(0, n - 6, 6):
        G.add_edge(names[i], names[i + 6], weight=0.5)
print(json.dumps([sorted(c) for c in split_graph(G, 10, 1.0, 42)]))
"""


def test_python_hash_randomisation_does_not_change_the_communities():
    """Louvain on a subgraph view walks its nodes in set order, which changes with Python's string hashing from one
    process to the next, and the split follows it. `split_graph` hands Louvain sorted nodes, so it does not.

    Two components, the small one under half the graph: that is the case where a view walks a set. With the sorted
    hand-off taken out, hash seeds 1 and 3 give different communities on this graph.
    """
    out = []
    for hash_seed in ("1", "3"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed, "DO_NOT_TRACK": "1"}
        done = subprocess.run(
            [sys.executable, "-c", HASH_SCRIPT], cwd=REPO, env=env, capture_output=True, text=True, check=True
        )
        out.append(done.stdout.strip())
    assert out[0] and out[0] == out[1]


def test_component_min_decides_whether_louvain_runs():
    G = chain_of_cliques()
    assert sizes(split_graph(G, component_min=30, resolution=1.0, seed=42)) == [15]  # small enough: left whole
    assert sizes(split_graph(G, component_min=15, resolution=1.0, seed=42)) == [15]  # "bigger than", not "as big as"
    assert sizes(split_graph(G, component_min=14, resolution=1.0, seed=42)) == [5, 5, 5]


def test_resolution_changes_the_split():
    G = ring_of_cliques()  # twelve cliques of four in a ring: the classic case where low resolution merges neighbours
    assert sizes(split_graph(G, component_min=10, resolution=1.0, seed=42)) == [4] * 12
    assert sizes(split_graph(G, component_min=10, resolution=0.5, seed=42)) == [8] * 6
    assert sizes(split_graph(G, component_min=10, resolution=0.1, seed=42)) == [16] * 3


def test_the_settings_reach_the_split(fixture_run, restore_settings):
    default = find_communities(fixture_run)
    restore_settings.setenv("LAB_COMPONENT_MIN", "1000")
    reload_settings()
    assert sizes(find_communities(fixture_run)) == [4, 6, 8, 182]  # the three identifier groups, the 182 left whole: no Louvain
    restore_settings.delenv("LAB_COMPONENT_MIN")
    restore_settings.setenv("LAB_LOUVAIN_RESOLUTION", "0.05")
    reload_settings()
    low = find_communities(fixture_run)
    restore_settings.setenv("LAB_LOUVAIN_RESOLUTION", "2.0")
    reload_settings()
    high = find_communities(fixture_run)
    assert len(low) < len(default) < len(high)
    for groups in (low, high):  # the planted groups stay together at either end
        assert all(any(set(m) <= g for g in groups) for m in (R1, R2, L1))


# ---- A11: ring recall ----------------------------------------------------------------------------------------------
def test_ring_recall_on_the_fixture(fixture_run, truth):
    groups = find_communities(fixture_run)
    got = ring_recall(groups, truth)
    assert (got["rings"], got["whole"], got["mostly_whole"], got["lookalikes"]) == (2, 2, 2, 1)
    assert got["by_ring"]["R1"] == {
        "members": 6, "together": 6, "share": 1.0, "community_size": 6, "community_score": None, "formed": IDENTIFIER,
        "whole": True, "mostly_whole": True,
    }
    assert got["rings_by_formed"] == {IDENTIFIER: {"rings": 2, "whole": 2, "mostly_whole": 2}}
    assert got["by_lookalike"]["L1"]["formed"] == IDENTIFIER
    assert got["lookalikes_in_high_scoring_community"] is None and got["alert_threshold"] is None  # no scores given
    print(f"\nring recall on the fixture, {got['whole']} of {got['rings']} rings whole: {json.dumps(got['by_ring'])}")


def table(*groups: tuple[set[str] | list[str], float]) -> pl.DataFrame:
    return pl.DataFrame({"member_keys": [sorted(g) for g, _ in groups], "score": [s for _, s in groups]})


def test_ring_recall_says_which_kind_of_community_holds_each_ring(truth):
    df = table((R1, 0.9), (R2[:5], 0.2), (L1, 0.3)).with_columns(formed=pl.Series([IDENTIFIER, PAYMENT, IDENTIFIER]))
    got = ring_recall(df, truth, alert_threshold=0.6)
    assert (got["by_ring"]["R1"]["formed"], got["by_ring"]["R2"]["formed"]) == (IDENTIFIER, PAYMENT)
    assert got["rings_by_formed"] == {IDENTIFIER: {"rings": 1, "whole": 1, "mostly_whole": 1},
                                      PAYMENT: {"rings": 1, "whole": 0, "mostly_whole": 1}}
    plain = ring_recall([{*R1}, {*R2}], truth)  # plain sets do not say
    assert plain["by_ring"]["R1"]["formed"] is None and plain["rings_by_formed"] == {"None": {"rings": 2, "whole": 2, "mostly_whole": 2}}


def test_ring_recall_counts_split_rings_and_high_scoring_look_alikes(truth):
    split = table((R1[:4], 0.9), (R1[4:], 0.2), (R2, 0.5), (L1, 0.7))
    got = ring_recall(split, truth, alert_threshold=0.6)
    assert (got["rings"], got["whole"], got["mostly_whole"]) == (2, 1, 2)  # R1: four of six together, R2 whole
    assert got["by_ring"]["R1"]["together"] == 4 and got["by_ring"]["R1"]["share"] == pytest.approx(4 / 6)
    assert got["by_ring"]["R1"]["whole"] is False and got["by_ring"]["R1"]["community_score"] == 0.9
    assert got["lookalikes_in_high_scoring_community"] == 1 and got["alert_threshold"] == 0.6
    assert got["by_lookalike"]["L1"]["high"] is True

    half = table((R1[:3], 0.9), (R1[3:], 0.8), (R2, 0.5), (L1, 0.6))  # three and three is not most
    got = ring_recall(half, truth, alert_threshold=0.6)
    assert (got["whole"], got["mostly_whole"]) == (1, 1)
    assert got["lookalikes_in_high_scoring_community"] == 1  # at the threshold counts
    assert ring_recall(half, truth, alert_threshold=0.6, mostly=0.4)["mostly_whole"] == 2  # the share is a knob

    apart = table((R1[:2], 0.9), (R2, 0.5), (L1[:2], 0.1), (L1[2:], 0.9))  # R1 mostly in no community at all
    got = ring_recall(apart, truth, alert_threshold=0.6)
    assert got["by_ring"]["R1"]["together"] == 2 and got["mostly_whole"] == 1
    assert got["by_lookalike"]["L1"]["community_score"] == 0.9  # a tie between the halves goes to the higher score
    assert ring_recall(table(), truth, alert_threshold=0.6)["whole"] == 0


def test_ring_recall_takes_the_alert_threshold_from_the_policy_file(fixture_run, truth):
    got = ring_recall(table((R1, 0.9), (L1, 0.59)), truth)
    assert got["alert_threshold"] == 0.6 and got["lookalikes_in_high_scoring_community"] == 0


# ---- the run store half --------------------------------------------------------------------------------------------
def test_a_community_embedding_is_the_mean_of_its_members(fixture_run):
    embed(fixture_run, {R1[0]: [1.0, 0.0, 2.0], R1[1]: [3.0, 2.0, 0.0], L1[0]: [4.0, 4.0, 4.0]})  # R2 has none
    df = pl.DataFrame({"member_keys": [R1, R2, L1]})
    got = embed_communities(fixture_run, df)["embedding"].to_list()
    assert got[0] == pytest.approx([2.0, 1.0, 1.0])  # only the two members that have one count
    assert got[1] is None
    assert got[2] == pytest.approx([4.0, 4.0, 4.0])
    assert embed_communities(fixture_run, pl.DataFrame({"member_keys": [R2]}))["embedding"].dtype == pl.List(pl.Float64)


def test_attach_communities_scores_and_stores_the_communities_and_stages_nothing(fixture_run, staged):
    embed(fixture_run, {m: [1.0, 2.0, 3.0] for m in R1[:3]})
    scores = scores_frame(fixture_run, hot=set(R1))
    out, counts = attach_communities(fixture_run, scores, None)
    assert staged == {}  # `score` stages the communities once, after their summaries: not here

    # the file for track 3b: the staged columns, how each community formed, and the embedding
    kept = pl.read_parquet(fixture_run.paths.artifacts / COMMUNITIES_FILE)
    assert kept.height == counts["communities"] == 10
    assert {"community_key", "member_keys", "score", "size", "score_parts", "run_id", "created_at", "formed", "embedding"} <= set(kept.columns)
    assert kept.schema["member_keys"] == pl.List(pl.Utf8) and kept["community_key"].n_unique() == kept.height
    assert (kept["size"] == kept["member_keys"].list.len()).all() and set(kept["run_id"]) == {fixture_run.id}
    assert set(kept["formed"]) == {IDENTIFIER, PAYMENT} and kept.filter(pl.col("formed") == IDENTIFIER).height == 3
    ring = kept.filter(pl.col("community_key") == community_key(fixture_run.id, R1)).row(0, named=True)
    assert ring["member_keys"] == sorted(R1) and ring["embedding"] == pytest.approx([1.0, 2.0, 3.0]) and ring["formed"] == IDENTIFIER
    assert json.loads(ring["score_parts"])["formed_by_identifier"] == 1.0
    assert json.loads(kept.filter(pl.col("formed") == PAYMENT)["score_parts"][0])["formed_by_identifier"] == 0.0
    assert kept.filter(pl.col("community_key") == community_key(fixture_run.id, R2))["embedding"][0] is None

    # the counts, and the scores with the community each account is in
    assert counts == {"communities": 10, "community_members": 200, "largest": 39, "by_identifier": 3, "by_payment": 7}
    assert out.columns == [*scores.columns, "risk_community_key"] and out.height == scores.height
    by_account = dict(zip(out[KEY], out["risk_community_key"], strict=True))
    assert {by_account[a] for a in R1} == {community_key(fixture_run.id, R1)}
    assert set(by_account.values()) == set(kept["community_key"])

    # the run store: a RiskCommunity node per community with its embedding, and a link from every member
    nodes = fixture_run.table("MATCH (c:RiskCommunity) RETURN c.community_key AS k, c.score AS score, c.size AS size, "
                              "c.run_id AS run_id, c.embedding AS embedding, c.summary AS summary")
    assert nodes.height == 10 and set(nodes["k"]) == set(kept["community_key"]) and set(nodes["run_id"]) == {fixture_run.id}
    assert nodes["summary"].null_count() == 10  # track 3b writes it
    assert nodes["embedding"].null_count() == 9
    assert nodes.filter(pl.col("k") == ring["community_key"])["embedding"][0].to_list() == pytest.approx([1.0, 2.0, 3.0])
    assert dict(zip(nodes["k"], nodes["score"], strict=True)) == pytest.approx(dict(zip(kept["community_key"], kept["score"], strict=True)))
    links = fixture_run.table("MATCH (a:Account)-[:IN_RISK_COMMUNITY]->(c:RiskCommunity) "
                              "RETURN a.account_id AS a, c.community_key AS c")
    assert links.height == 200 and dict(zip(links["a"], links["c"], strict=True)) == by_account

    step = reread(fixture_run).manifest.step("communities")
    assert step.status == StepStatus.DONE and step.counts == counts and step.started_at and step.finished_at


def test_what_stage_takes_leaves_out_formed_and_the_embedding_only_when_it_should(fixture_run):
    embed(fixture_run, {m: [1.0, 2.0, 3.0] for m in R1[:3]})
    attach_communities(fixture_run, scores_frame(fixture_run), None)
    kept = pl.read_parquet(fixture_run.paths.artifacts / COMMUNITIES_FILE)
    ready = for_stage(fixture_run, kept)
    assert "formed" not in ready.columns and "embedding" in ready.columns  # `formed` is not a staged column
    assert set(ready.columns) - {"member_keys"} <= set(COMMUNITY_PROPS)
    fixture_run.recipe.write_back.vectors = False
    assert not {"formed", "embedding"} & set(for_stage(fixture_run, kept).columns)
    fixture_run.recipe.write_back.vectors = True
    assert "embedding" not in for_stage(fixture_run, kept.with_columns(embedding=pl.lit(None, dtype=pl.List(pl.Float64)))).columns


def test_attach_communities_drops_a_community_under_min_size_and_nulls_who_is_in_none(fixture_run, staged):
    keys = [*R1, R2[0], R2[1]]  # the two R2 accounts link to each other (one email) and are under min_size 3
    out, counts = attach_communities(fixture_run, scores_frame(fixture_run, keys=keys), keys)
    assert counts == {"communities": 1, "community_members": 6, "largest": 6, "by_identifier": 1, "by_payment": 0}
    assert out.filter(pl.col(KEY).is_in(R2[:2]))["risk_community_key"].null_count() == 2
    assert out.schema["risk_community_key"] == pl.Utf8
    assert "under min_size 3" in reread(fixture_run).manifest.step("communities").note


def test_attach_communities_on_a_slice_of_nothing(fixture_run, staged):
    empty = scores_frame(fixture_run, keys=[])
    out, counts = attach_communities(fixture_run, empty, [])
    assert counts == {"communities": 0, "community_members": 0, "largest": 0, "by_identifier": 0, "by_payment": 0}
    assert out.height == 0 and out.schema["risk_community_key"] == pl.Utf8
    assert fixture_run.table("MATCH (c:RiskCommunity) RETURN count(c) AS n")["n"][0] == 0


def test_a_second_attach_leaves_the_run_store_with_this_result_only(fixture_run, staged):
    scores = scores_frame(fixture_run)
    attach_communities(fixture_run, scores, None)
    attach_communities(fixture_run, scores, None)  # the same slice twice changes nothing
    assert fixture_run.table("MATCH (c:RiskCommunity) RETURN count(c) AS n")["n"][0] == 10
    keys = [*R1, *L1]
    attach_communities(fixture_run, scores_frame(fixture_run, keys=keys), keys)  # a smaller slice replaces it
    assert fixture_run.table("MATCH (c:RiskCommunity) RETURN count(c) AS n")["n"][0] == 2
    assert fixture_run.table("MATCH (:Account)-[r:IN_RISK_COMMUNITY]->() RETURN count(r) AS n")["n"][0] == 10


def test_a_failure_marks_the_communities_step_failed(fixture_run, monkeypatch):
    import lab.scoring.communities as communities

    def refuse(run, df):
        raise ValueError("the store said no")

    monkeypatch.setattr(communities, "write_communities", refuse)
    with pytest.raises(ValueError, match="the store said no"):
        attach_communities(fixture_run, scores_frame(fixture_run), None)
    step = reread(fixture_run).manifest.step("communities")
    assert step.status == StepStatus.FAILED and "the store said no" in step.note


def test_the_real_stage_takes_the_community_table(fixture_run):
    embed(fixture_run, {m: [1.0, 2.0, 3.0] for m in R1})
    out, counts = attach_communities(fixture_run, scores_frame(fixture_run, hot=set(R1)), None)
    kept = pl.read_parquet(fixture_run.paths.artifacts / COMMUNITIES_FILE)
    fixture_run.stage(for_stage(fixture_run, kept), "community_key", "community")
    pushed = rows(fixture_run.push_store(), "MATCH (c:RiskCommunity) RETURN properties(c) AS p ORDER BY c.size, c.community_key")
    assert len(pushed) == counts["communities"] == 10
    top = next(r["p"] for r in pushed if r["p"]["community_key"] == community_key(fixture_run.id, R1))
    assert top["size"] == 6 and top["run_id"] == fixture_run.id and top["created_at"].endswith("Z")
    assert top["embedding"] == pytest.approx([1.0, 2.0, 3.0]) and json.loads(top["score_parts"])["cycles"] == 1.0
    assert json.loads(top["score_parts"])["formed_by_identifier"] == 1.0 and "formed" not in top  # a part, not a column
    assert "embedding" not in next(r["p"] for r in pushed if r["p"]["community_key"] == community_key(fixture_run.id, R2))
    stamped = rows(fixture_run.push_store(), "MATCH (a:Account) WHERE a.risk_community_key IS NOT NULL RETURN count(a) AS n")
    assert stamped[0]["n"] == 200
    assert reread(fixture_run).manifest.step("stage").counts["communities"] == 10


def test_write_communities_with_nothing_removes_what_was_there(fixture_run, staged):
    attach_communities(fixture_run, scores_frame(fixture_run), None)
    empty = pl.DataFrame(schema={"community_key": pl.Utf8, "member_keys": pl.List(pl.Utf8), "score": pl.Float64,
                                 "size": pl.Int64, "run_id": pl.Utf8})
    assert write_communities(fixture_run, empty) == 0
    assert fixture_run.table("MATCH (c:RiskCommunity) RETURN count(c) AS n")["n"][0] == 0


# ---- the whole step ------------------------------------------------------------------------------------------------
def test_score_runs_communities_between_the_account_scores_and_the_stage(fixture_run, monkeypatch, truth):
    calls: list[tuple[str, pl.DataFrame]] = []

    def fake_stage(df, key=None, kind="account"):
        calls.append((kind, df))
        return df.height

    monkeypatch.setattr(fixture_run, "stage", fake_stage)
    counts = score(fixture_run)
    assert [k for k, _ in calls] == ["account", "community", "alert"]  # each table once: the manifest counts them once
    staged = {k: (df, None) for k, df in calls}
    accounts, _ = staged["account"]
    found, _ = staged["community"]
    assert staged_errors(accounts, KEY) == []  # risk_community_key is a valid account column
    assert set(found.columns) - {"member_keys"} <= set(COMMUNITY_PROPS) and "formed" not in found.columns
    assert counts["communities"] == found.height == 10 and counts["community_members"] == 200
    assert counts["by_identifier"] == 3 and counts["by_payment"] == 7
    assert accounts["risk_community_key"].null_count() == 0 and set(accounts["risk_community_key"]) == set(found["community_key"])
    # the community of R1 outscores the one of L1, the look-alike family, as the accounts do
    got = ring_recall(pl.read_parquet(fixture_run.paths.artifacts / COMMUNITIES_FILE), truth, alert_threshold=0.6)
    assert got["by_ring"]["R1"]["community_score"] > got["by_lookalike"]["L1"]["community_score"]
    assert got["whole"] == 2 and got["lookalikes_in_high_scoring_community"] == 0  # L1, the family, scores below the alert line
    assert got["rings_by_formed"] == {IDENTIFIER: {"rings": 2, "whole": 2, "mostly_whole": 2}}
    assert got["by_lookalike"]["L1"]["community_score"] < 0.6 < got["by_ring"]["R1"]["community_score"]
    run = reread(fixture_run)
    assert run.manifest.step("communities").status == StepStatus.DONE
    assert run.manifest.step("score").counts["communities"] == 10
    assert (run.paths.artifacts / COMMUNITIES_FILE).exists()
    print(f"\nA11 on the fixture: {json.dumps({k: v for k, v in got.items() if not k.startswith('by_')})}")
