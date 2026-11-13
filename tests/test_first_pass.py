"""Track 2a: the first pass (Node2Vec embedding and template summary on every account) and the Node2Vec example."""
from __future__ import annotations

import re
import shutil
import time

import pytest

from lab.contracts.graph import EMBEDDING_PROP, SUMMARY_PROP
from lab.contracts.manifest import RunManifest, StepStatus
from lab.contracts.recipe import Recipe
from lab.roundtrip.indexes import create_indexes
from lab.runs import Run
from lab.settings import get_settings, reload_settings
from lab.slice.first_pass import first_pass
from lab.slice.summaries import account_summaries

R1 = [f"A{i:04d}" for i in range(11, 17)]  # the planted mule ring
R2 = [f"A{i:04d}" for i in range(101, 109)]  # the planted structuring group
L1 = [f"A{i:04d}" for i in range(150, 154)]  # the look-alike family


@pytest.fixture(scope="session")
def first_passed(fixture_store_path, tmp_path_factory):
    """A run on the fixture store with its indexes and its first pass done, built once for the whole session.

    Returns (run, counts, seconds). Tests may search it; they must leave the first pass in place.
    """
    data = tmp_path_factory.mktemp("first_pass_data")
    run = Run.create(Recipe(), data_dir=data)
    shutil.copytree(fixture_store_path, run.paths.store)
    create_indexes(run.store())
    started = time.perf_counter()
    counts = first_pass(run)
    seconds = time.perf_counter() - started
    yield run, counts, seconds
    run.close()


@pytest.fixture
def run_of(first_passed):
    return first_passed[0]


def summaries(run) -> dict[str, str]:
    df = run.table(f"MATCH (a:Account) RETURN a.account_id AS key, a.{SUMMARY_PROP} AS s")
    return dict(df.iter_rows())


def test_first_pass_writes_an_embedding_and_a_summary_on_every_account(first_passed):
    run, counts, _ = first_passed
    dim = get_settings().embedding_dim
    df = run.table(f"MATCH (a:Account) RETURN a.account_id AS key, a.{EMBEDDING_PROP} AS v, a.{SUMMARY_PROP} AS s")
    assert df.height == 200
    assert all(v is not None and len(v) == dim for v in df["v"])
    assert all(s for s in df["s"])
    assert counts["accounts"] == counts["embeddings"] == counts["summaries"] == 200
    assert counts["embedding_dim"] == dim


def test_first_pass_records_its_manifest_step(first_passed):
    run, counts, _ = first_passed
    step = RunManifest.load(run.paths.manifest).step("first_pass")
    assert step.status is StepStatus.DONE
    assert step.finished_at and step.counts["accounts"] == 200
    assert step.counts["graph_nodes"] == counts["graph_nodes"] > 200  # accounts plus their identifiers


def test_an_r1_summary_says_new_account_and_shares_a_device(run_of):
    s = summaries(run_of)
    for key in R1:
        assert "new account" in s[key]
        assert "money in and out within a day" in s[key]
        assert f"shares a device with {len(R1) - 1} other accounts" in s[key]
        assert f"shares an address with {len(R1) - 1} other accounts" in s[key]


def test_an_r2_summary_says_email_and_just_under_10000(run_of):
    s = summaries(run_of)
    for key in R2:
        assert "new account" in s[key]
        assert f"shares an email with {len(R2) - 1} other accounts" in s[key]
        assert "payments just under 10,000" in s[key]
    assert "shares a device" not in s["A0101"]


def test_a_family_account_is_not_a_new_account(run_of):
    s = summaries(run_of)
    for key in L1:
        assert "new account" not in s[key]
        assert f"shares a device with {len(L1) - 1} other accounts" in s[key]
    ordinary = [k for k, v in s.items() if k not in R1 + R2]
    assert not any("new account" in s[k] for k in ordinary)


def test_every_number_in_a_summary_comes_from_the_data(run_of):
    """Recompute a few accounts' counts straight from the store and find them in the sentence."""
    s = summaries(run_of)
    for key in ("A0011", "A0108", "A0001", "A0150"):
        n_in = run_of.table("MATCH ()-[t:TX]->(:Account {account_id: $k}) RETURN count(t) AS n", {"k": key})["n"][0]
        n_out = run_of.table("MATCH (:Account {account_id: $k})-[t:TX]->() RETURN count(t) AS n", {"k": key})["n"][0]
        assert f"{n_in} payment{'' if n_in == 1 else 's'} in, {n_out} out" in s[key]
    # just under 10,000: 9,500 up to but not including 10,000, in either direction
    for key in ("A0101", "A0108", "A0011"):
        n = run_of.table(
            "MATCH (:Account {account_id: $k})-[t:TX]-(:Account) WHERE t.amount >= 9500.0 AND t.amount < 10000.0 "
            "RETURN count(t) AS n",
            {"k": key},
        )["n"][0]
        if n:
            assert f"{n} payment{'' if n == 1 else 's'} just under 10,000" in s[key]
        else:
            assert "just under" not in s[key]
    opened = run_of.table("MATCH (a:Account {account_id: 'A0011'}) RETURN a.opened AS o")["o"][0]
    assert f"opened {opened.isoformat()}" in s["A0011"]


CLAUSE = re.compile(
    r"new account"
    r"|money in and out within a day \(\d+ times?\)"
    r"|shares an? \w+ with (\d+ other accounts?|\d+ or more other accounts)"
    r"|\d+ payments? just under 10,000"
    r"|\d+ payments? in, \d+ out"
)


def test_a_summary_has_only_the_fixed_phrases(run_of):
    for key, text in summaries(run_of).items():
        head, _, body = text.partition(": ")
        assert re.fullmatch(rf"Account {key}( opened \d{{4}}-\d{{2}}-\d{{2}})?", head), head
        assert body.endswith("."), text
        for clause in body.removesuffix(".").split("; "):
            assert CLAUSE.fullmatch(clause), clause


def test_summaries_of_a_slice_count_only_the_slice(run_of):
    pair = account_summaries(run_of, ["A0011", "A0012"])
    assert pair.height == 2
    for text in pair["summary"]:
        assert "shares a device with 1 other account;" in text  # the other four ring members are out of scope
    assert account_summaries(run_of, []).height == 0


def test_an_identifier_shared_by_more_than_the_maximum_is_said_to_be_at_least(run_of, monkeypatch):
    monkeypatch.setenv("LAB_SHARED_MAX", "5")
    reload_settings()
    try:
        text = dict(account_summaries(run_of).iter_rows())
        assert "shares a device with 5 or more other accounts" in text["A0011"]  # six accounts share D-R1
        assert "shares a device with 3 other accounts" in text["A0150"]  # four share D-FAM: within the maximum
    finally:
        monkeypatch.undo()
        reload_settings()


def test_running_it_again_replaces_both(first_passed):
    run, _, _ = first_passed
    before = run.table(f"MATCH (a:Account) RETURN a.account_id AS key, a.{EMBEDDING_PROP} AS v ORDER BY key")
    run.store().execute(f"MATCH (a:Account {{account_id: 'A0011'}}) SET a.{SUMMARY_PROP} = 'stale', a.{EMBEDDING_PROP} = []")
    first_pass(run)
    assert "new account" in summaries(run)["A0011"]
    after = run.table(f"MATCH (a:Account) RETURN a.account_id AS key, a.{EMBEDDING_PROP} AS v ORDER BY key")
    assert after.height == 200
    assert after["v"].to_list() == before["v"].to_list()  # the same seed gives the same embeddings, so a re-run is a replace


def test_first_pass_on_an_empty_store_fails_and_says_so(data_dir):
    run = Run.create(Recipe(), data_dir=data_dir)
    try:
        run.store()  # creates an empty store
        with pytest.raises(Exception, match="no accounts|empty"):
            first_pass(run)
        assert RunManifest.load(run.paths.manifest).step("first_pass").status is StepStatus.FAILED
    finally:
        run.close()


# ---- the Node2Vec example --------------------------------------------------------------------------------------
def test_the_node2vec_example_is_importable_and_under_100_lines():
    import inspect

    import examples.node2vec as pkg
    from examples.node2vec import embed, example

    assert embed is pkg.embed is example.embed
    assert len(inspect.getsource(example).splitlines()) < 100


def test_embed_is_deterministic_for_a_seed(run_of):
    """The same seed on the same machine gives bit-identical embeddings; another seed gives different ones."""
    import torch

    from examples.node2vec import embed
    from lab.adapters import to_pyg

    graph = to_pyg(run_of, nodes="all")
    a = embed(graph, 8, seed=3, epochs=1)
    assert a.shape == (graph.data.num_nodes, 8)
    assert torch.equal(a, embed(graph, 8, seed=3, epochs=1))
    assert not torch.equal(a, embed(graph, 8, seed=4, epochs=1))


def test_the_first_pass_is_fast_on_the_fixture(first_passed):
    _, _, seconds = first_passed
    assert seconds < 60, f"first pass took {seconds:.1f}s on 200 accounts"
