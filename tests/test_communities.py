"""Track 3a: communities on the fixture graph, the run store half, `attach_communities`, and ring recall (A11).

The fixture has 200 accounts, 2,000 payments, the mule ring R1 (six accounts on one device and address), the
structuring ring R2 (eight accounts on one email) and the family L1 (four accounts on one laptop and address).
`run.stage` is patched where a test needs to see what would be staged; one test runs the real stage.
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
    embed_communities,
    find_communities,
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
    assert sizes(find_communities(fixture_run)) == [200]  # the one component is under the limit: no Louvain
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
        "members": 6, "together": 6, "share": 1.0, "community_size": 6, "community_score": None,
        "whole": True, "mostly_whole": True,
    }
    assert got["lookalikes_in_high_scoring_community"] is None and got["alert_threshold"] is None  # no scores given
    print(f"\nring recall on the fixture, {got['whole']} of {got['rings']} rings whole: {json.dumps(got['by_ring'])}")


def table(*groups: tuple[set[str] | list[str], float]) -> pl.DataFrame:
    return pl.DataFrame({"member_keys": [sorted(g) for g, _ in groups], "score": [s for _, s in groups]})


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


def test_attach_communities_stages_scores_and_stores_the_communities(fixture_run, staged):
    embed(fixture_run, {m: [1.0, 2.0, 3.0] for m in R1[:3]})
    scores = scores_frame(fixture_run, hot=set(R1))
    out, counts = attach_communities(fixture_run, scores, None)

    # the staged table: what stage accepts, keyed by community_key
    df, key = staged["community"]
    assert key == "community_key" and df.height == counts["communities"] == 9
    assert set(df.columns) - {"member_keys"} <= set(COMMUNITY_PROPS)
    assert {"community_key", "member_keys", "score", "size", "score_parts", "run_id", "created_at", "embedding"} <= set(df.columns)
    assert df.schema["member_keys"] == pl.List(pl.Utf8) and df["community_key"].n_unique() == df.height
    assert (df["size"] == df["member_keys"].list.len()).all() and set(df["run_id"]) == {fixture_run.id}
    ring = df.filter(pl.col("community_key") == community_key(fixture_run.id, R1)).row(0, named=True)
    assert ring["member_keys"] == sorted(R1) and ring["embedding"] == pytest.approx([1.0, 2.0, 3.0])
    assert df.filter(pl.col("community_key") == community_key(fixture_run.id, R2))["embedding"][0] is None

    # the counts, and the scores with the community each account is in
    assert counts == {"communities": 9, "community_members": 200, "largest": 52}
    assert out.columns == [*scores.columns, "risk_community_key"] and out.height == scores.height
    by_account = dict(zip(out[KEY], out["risk_community_key"], strict=True))
    assert {by_account[a] for a in R1} == {community_key(fixture_run.id, R1)}
    assert set(by_account.values()) == set(df["community_key"])

    # the run store: a RiskCommunity node per community with its embedding, and a link from every member
    nodes = fixture_run.table("MATCH (c:RiskCommunity) RETURN c.community_key AS k, c.score AS score, c.size AS size, "
                              "c.run_id AS run_id, c.embedding AS embedding, c.summary AS summary")
    assert nodes.height == 9 and set(nodes["k"]) == set(df["community_key"]) and set(nodes["run_id"]) == {fixture_run.id}
    assert nodes["summary"].null_count() == 9  # track 3b writes it
    assert nodes["embedding"].null_count() == 8
    assert nodes.filter(pl.col("k") == ring["community_key"])["embedding"][0].to_list() == pytest.approx([1.0, 2.0, 3.0])
    assert dict(zip(nodes["k"], nodes["score"], strict=True)) == pytest.approx(dict(zip(df["community_key"], df["score"], strict=True)))
    links = fixture_run.table("MATCH (a:Account)-[:IN_RISK_COMMUNITY]->(c:RiskCommunity) "
                              "RETURN a.account_id AS a, c.community_key AS c")
    assert links.height == 200 and dict(zip(links["a"], links["c"], strict=True)) == by_account

    # the file for track 3b, and the manifest on disk
    kept = pl.read_parquet(fixture_run.paths.artifacts / COMMUNITIES_FILE)
    assert kept["community_key"].to_list() == df["community_key"].to_list() and kept["member_keys"].dtype == pl.List(pl.Utf8)
    step = reread(fixture_run).manifest.step("communities")
    assert step.status == StepStatus.DONE and step.counts == counts and step.started_at and step.finished_at


def test_attach_communities_drops_a_community_under_min_size_and_nulls_who_is_in_none(fixture_run, staged):
    keys = [*R1, R2[0], R2[1]]  # the two R2 accounts link to each other (one email) and are under min_size 3
    out, counts = attach_communities(fixture_run, scores_frame(fixture_run, keys=keys), keys)
    assert counts == {"communities": 1, "community_members": 6, "largest": 6}
    assert staged["community"][0].height == 1
    assert out.filter(pl.col(KEY).is_in(R2[:2]))["risk_community_key"].null_count() == 2
    assert out.schema["risk_community_key"] == pl.Utf8
    assert "under min_size 3" in reread(fixture_run).manifest.step("communities").note


def test_attach_communities_on_a_slice_of_nothing(fixture_run, staged):
    empty = scores_frame(fixture_run, keys=[])
    out, counts = attach_communities(fixture_run, empty, [])
    assert counts == {"communities": 0, "community_members": 0, "largest": 0}
    assert out.height == 0 and out.schema["risk_community_key"] == pl.Utf8
    assert staged["community"][0].height == 0 and "embedding" not in staged["community"][0].columns
    assert fixture_run.table("MATCH (c:RiskCommunity) RETURN count(c) AS n")["n"][0] == 0


def test_a_second_attach_leaves_the_run_store_with_this_result_only(fixture_run, staged):
    scores = scores_frame(fixture_run)
    attach_communities(fixture_run, scores, None)
    attach_communities(fixture_run, scores, None)  # the same slice twice changes nothing
    assert fixture_run.table("MATCH (c:RiskCommunity) RETURN count(c) AS n")["n"][0] == 9
    keys = [*R1, *L1]
    attach_communities(fixture_run, scores_frame(fixture_run, keys=keys), keys)  # a smaller slice replaces it
    assert fixture_run.table("MATCH (c:RiskCommunity) RETURN count(c) AS n")["n"][0] == 2
    assert fixture_run.table("MATCH (:Account)-[r:IN_RISK_COMMUNITY]->() RETURN count(r) AS n")["n"][0] == 10


def test_vectors_are_staged_only_when_the_recipe_writes_them_back(fixture_run, staged):
    embed(fixture_run, {m: [1.0, 2.0, 3.0] for m in R1})
    scores = scores_frame(fixture_run)
    attach_communities(fixture_run, scores, None)
    assert "embedding" in staged["community"][0].columns
    fixture_run.recipe.write_back.vectors = False
    attach_communities(fixture_run, scores, None)
    assert "embedding" not in staged["community"][0].columns
    assert fixture_run.table("MATCH (c:RiskCommunity) WHERE c.embedding IS NOT NULL RETURN count(c) AS n")["n"][0] == 1
    # no account has an embedding yet (the first pass has not run): there is nothing to stage
    fixture_run.recipe.write_back.vectors = True
    fixture_run.store().execute("MATCH (a:Account) REMOVE a.risk_embedding")
    attach_communities(fixture_run, scores, None)
    assert "embedding" not in staged["community"][0].columns


def test_a_failed_stage_marks_the_communities_step_failed(fixture_run, monkeypatch):
    def refuse(df, key=None, kind="account"):
        raise ValueError("stage said no")

    monkeypatch.setattr(fixture_run, "stage", refuse)
    with pytest.raises(ValueError, match="stage said no"):
        attach_communities(fixture_run, scores_frame(fixture_run), None)
    step = reread(fixture_run).manifest.step("communities")
    assert step.status == StepStatus.FAILED and "stage said no" in step.note


def test_the_real_stage_takes_the_community_table(fixture_run):
    embed(fixture_run, {m: [1.0, 2.0, 3.0] for m in R1})
    out, counts = attach_communities(fixture_run, scores_frame(fixture_run, hot=set(R1)), None)
    pushed = rows(fixture_run.push_store(), "MATCH (c:RiskCommunity) RETURN properties(c) AS p ORDER BY c.size, c.community_key")
    assert len(pushed) == counts["communities"] == 9
    top = next(r["p"] for r in pushed if r["p"]["community_key"] == community_key(fixture_run.id, R1))
    assert top["size"] == 6 and top["run_id"] == fixture_run.id and top["created_at"].endswith("Z")
    assert top["embedding"] == pytest.approx([1.0, 2.0, 3.0]) and json.loads(top["score_parts"])["cycles"] == 1.0
    assert "embedding" not in next(r["p"] for r in pushed if r["p"]["community_key"] == community_key(fixture_run.id, R2))
    stamped = rows(fixture_run.push_store(), "MATCH (a:Account) WHERE a.risk_community_key IS NOT NULL RETURN count(a) AS n")
    assert stamped[0]["n"] == 200
    assert reread(fixture_run).manifest.step("stage").counts["communities"] == 9


def test_write_communities_with_nothing_removes_what_was_there(fixture_run, staged):
    attach_communities(fixture_run, scores_frame(fixture_run), None)
    empty = pl.DataFrame(schema={"community_key": pl.Utf8, "member_keys": pl.List(pl.Utf8), "score": pl.Float64,
                                 "size": pl.Int64, "run_id": pl.Utf8})
    assert write_communities(fixture_run, empty) == 0
    assert fixture_run.table("MATCH (c:RiskCommunity) RETURN count(c) AS n")["n"][0] == 0


# ---- the whole step ------------------------------------------------------------------------------------------------
def test_score_runs_communities_between_the_account_scores_and_the_stage(fixture_run, staged, truth):
    counts = score(fixture_run)
    accounts, _ = staged["account"]
    found, _ = staged["community"]
    assert staged_errors(accounts, KEY) == []  # risk_community_key is a valid account column
    assert counts["communities"] == found.height == 9 and counts["community_members"] == 200
    assert accounts["risk_community_key"].null_count() == 0 and set(accounts["risk_community_key"]) == set(found["community_key"])
    # the community of R1 outscores the one of L1, the look-alike family, as the accounts do
    got = ring_recall(found, truth, alert_threshold=0.6)
    assert got["by_ring"]["R1"]["community_score"] > got["by_lookalike"]["L1"]["community_score"]
    assert got["whole"] == 2 and got["lookalikes_in_high_scoring_community"] == 0
    run = reread(fixture_run)
    assert run.manifest.step("communities").status == StepStatus.DONE
    assert run.manifest.step("score").counts["communities"] == 9
    assert (run.paths.artifacts / COMMUNITIES_FILE).exists()
    print(f"\nA11 on the fixture: {json.dumps({k: v for k, v in got.items() if not k.startswith('by_')})}")
