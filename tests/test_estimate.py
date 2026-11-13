"""Track 2a: the estimate. Search mode picks its run (decision 31); schema and Cypher modes count on the master graph."""
from __future__ import annotations

import polars as pl
import pytest

from lab.common.neo4j import master_rows
from lab.common.store import estimate_memory
from lab.contracts.api import SliceEstimate, SliceRequest
from lab.contracts.graph import GraphMapping
from lab.contracts.recipe import PullSpec, Recipe, SearchSpec
from lab.roundtrip.queries import schema_statements
from lab.runs import Run
from lab.settings import reload_settings
from lab.slice import estimate as estimate_module
from lab.slice.estimate import estimate, estimate_pull
from lab.slice.search import FirstPassNotRun
from tests import test_first_pass as shared
from tests.fixtures.graph_loader import GRAPH
from tests.test_first_pass import R1

# One run with its first pass done, built once for the whole session (defined in test_first_pass.py).
first_passed = shared.first_passed


@pytest.fixture
def restore_settings(monkeypatch):
    yield monkeypatch
    monkeypatch.undo()
    reload_settings()


def make_run(data_dir, run_id: str, first_pass_done: bool) -> Run:
    run = Run.create(Recipe(), data_dir=data_dir, run_id=run_id)
    if first_pass_done:
        run.manifest.finish("first_pass", {"accounts": 1})
        run.save()
    return run


# ---- search mode: which run ------------------------------------------------------------------------------------
def test_search_with_no_run_says_to_pull_a_schema_slice_first(data_dir):
    est = estimate(SliceRequest(search=SearchSpec(text="new accounts", hops=3)))
    assert est.mode == "search" and est.hops == 3
    assert (est.nodes, est.relationships, est.memory_bytes, est.hits) == (0, 0, 0, [])
    assert not est.over_cap and "pull a schema slice first" in est.note


def test_a_search_with_no_hops_asked_for_is_one_account_step(data_dir, first_passed, monkeypatch):
    """Decision 41: LAB_SEARCH_HOPS defaults to 1, and the estimate says so with or without a run to search."""
    assert estimate(SliceRequest(search=SearchSpec(text="new accounts"))).hops == 1
    run = first_passed[0]
    monkeypatch.setenv("LAB_DATA_DIR", str(run.paths.root.parent.parent))
    reload_settings()
    try:
        unset = estimate(SliceRequest(run_id=run.id, search=SearchSpec(account="A0011", k=1)))
        one = estimate(SliceRequest(run_id=run.id, search=SearchSpec(account="A0011", k=1, hops=1)))
        zero = estimate(SliceRequest(run_id=run.id, search=SearchSpec(account="A0011", k=1, hops=0)))
        assert unset.hops == 1 and (unset.nodes, unset.relationships) == (one.nodes, one.relationships)
        assert zero.nodes < unset.nodes  # the default reaches one step past the hit
    finally:
        monkeypatch.undo()
        reload_settings()


def test_search_uses_the_newest_run_whose_first_pass_is_done(data_dir, monkeypatch):
    make_run(data_dir, "run-20261101T000000Z-aaaaaa", True)
    newest_done = make_run(data_dir, "run-20261102T000000Z-bbbbbb", True)
    make_run(data_dir, "run-20261103T000000Z-cccccc", False)  # the newest run, but not searchable yet
    seen = []

    def stub(run, spec):
        seen.append((run.id, spec))
        return SliceEstimate(mode="search", nodes=1, relationships=0, memory_bytes=1, over_cap=False, cap=9)

    monkeypatch.setattr(estimate_module, "search_slice", stub)
    spec = SearchSpec(account="A0001")
    assert estimate(SliceRequest(search=spec)).nodes == 1
    assert seen == [(newest_done.id, spec)]


def test_a_named_run_is_the_one_searched_and_must_have_its_first_pass(data_dir):
    make_run(data_dir, "run-20261101T000000Z-aaaaaa", True)
    make_run(data_dir, "run-20261103T000000Z-cccccc", False)
    with pytest.raises(FirstPassNotRun, match="run-20261103T000000Z-cccccc"):
        estimate(SliceRequest(run_id="run-20261103T000000Z-cccccc", search=SearchSpec(account="A0001")))
    with pytest.raises(FileNotFoundError):
        estimate(SliceRequest(run_id="run-nope", search=SearchSpec(account="A0001")))


def test_the_search_in_the_recipe_counts_when_the_request_has_none(data_dir, monkeypatch):
    make_run(data_dir, "run-20261101T000000Z-aaaaaa", True)
    seen = []
    monkeypatch.setattr(estimate_module, "search_slice", lambda run, spec: seen.append(spec) or SliceEstimate(
        mode="search", nodes=0, relationships=0, memory_bytes=0, over_cap=False, cap=1))
    in_recipe, override = SearchSpec(account="A0001"), SearchSpec(account="A0002")
    estimate(SliceRequest(recipe=Recipe(search=in_recipe)))
    estimate(SliceRequest(recipe=Recipe(search=in_recipe), search=override))
    assert seen == [in_recipe, override]


def test_a_real_search_estimate_through_the_request(first_passed, monkeypatch):
    run = first_passed[0]
    monkeypatch.setenv("LAB_DATA_DIR", str(run.paths.root.parent.parent))
    reload_settings()
    try:
        for request in (SliceRequest(run_id=run.id, search=SearchSpec(account="A0011", k=10, hops=1)),
                        SliceRequest(search=SearchSpec(account="A0011", k=10, hops=1))):  # newest done run
            est = estimate(request)
            assert est.mode == "search" and len(est.hits) == 10 and est.hits[0].key == "A0011"
            assert set(R1) <= {h.key for h in est.hits}
            assert est.memory_bytes == estimate_memory(est.nodes, est.relationships) > 0
    finally:
        monkeypatch.undo()
        reload_settings()


# ---- schema and Cypher modes: routing and refusals that need no master graph -----------------------------------------
def test_pull_requests_go_to_estimate_pull(monkeypatch):
    seen = []

    def stub(spec, mapping):
        seen.append((spec, mapping))
        return SliceEstimate(mode=spec.mode, nodes=5, relationships=4, memory_bytes=3, over_cap=False, cap=2)

    monkeypatch.setattr(estimate_module, "estimate_pull", stub)
    mapping = GraphMapping(account_label="Customer")
    override = PullSpec(since="2026-10-01T00:00:00Z")
    assert estimate(SliceRequest()).mode == "schema"
    estimate(SliceRequest(recipe=Recipe(graph=mapping), pull=override))
    assert seen[0] == (PullSpec(), GraphMapping())
    assert seen[1] == (override, mapping)


def test_a_pull_the_mapping_does_not_know_is_refused_before_the_master_is_touched():
    with pytest.raises(ValueError, match="does not know"):
        estimate_pull(PullSpec(labels=["Nope"]), GraphMapping())
    with pytest.raises(ValueError, match="JSON scalars"):
        estimate_pull(PullSpec(mode="cypher", query="MATCH (a) RETURN a", params={"x": [1, 2]}), GraphMapping())


# ---- the master graph (live) --------------------------------------------------------------------------------------
def fixture_counts(since: str | None = None, until: str | None = None, kinds=("device", "email", "address")):
    """What a pull of the fixture window brings, counted from the fixture's CSV files."""
    tx = pl.read_csv(GRAPH / "tx.csv", infer_schema=False)
    if since:
        tx = tx.filter(pl.col("ts") >= since)
    if until:
        tx = tx.filter(pl.col("ts") < until)
    accounts = set(tx["src"]) | set(tx["dst"])
    links = identifiers = 0
    for kind, file, col in (("device", "uses_device.csv", "device_id"), ("email", "has_email.csv", "email_id"),
                            ("address", "has_address.csv", "address_id")):
        if kind in kinds:
            df = pl.read_csv(GRAPH / file, infer_schema=False).filter(pl.col("account_id").is_in(accounts))
            links += df.height
            identifiers += df[col].n_unique()
    return len(accounts), tx.height, identifiers, links


@pytest.mark.live
def test_schema_mode_counts_the_whole_fixture(live_neo4j):
    est = estimate_pull(PullSpec(), GraphMapping())
    accounts, payments, identifiers, links = fixture_counts()
    assert (accounts, payments) == (200, 2000)
    assert est.mode == "schema" and not est.over_cap and est.hits == [] and est.hops is None
    assert est.nodes == accounts + identifiers and est.relationships == payments + links
    assert est.memory_bytes == estimate_memory(est.nodes, est.relationships)
    assert "200 accounts, 2,000 payments" in est.note


@pytest.mark.live
def test_a_window_reduces_the_counts(live_neo4j):
    whole = estimate_pull(PullSpec(), GraphMapping())
    spec = PullSpec(since="2026-10-01T00:00:00Z", until="2026-10-15T00:00:00Z")
    est = estimate_pull(spec, GraphMapping())
    accounts, payments, identifiers, links = fixture_counts(spec.since, spec.until)
    assert 0 < payments < 2000 and 0 < accounts < 200
    assert est.nodes == accounts + identifiers < whole.nodes
    assert est.relationships == payments + links < whole.relationships
    assert f"{accounts:,} accounts, {payments:,} payments from {spec.since} and before {spec.until}" in est.note
    only_since = estimate_pull(PullSpec(since=spec.since), GraphMapping())
    assert only_since.relationships == sum(fixture_counts(spec.since)[1::2])


@pytest.mark.live
def test_types_narrow_the_identifier_links(live_neo4j):
    est = estimate_pull(PullSpec(types=["TX", "USES_DEVICE"]), GraphMapping())
    accounts, payments, identifiers, links = fixture_counts(kinds=("device",))
    assert est.nodes == accounts + identifiers and est.relationships == payments + links == 2000 + 200


@pytest.mark.live
def test_cypher_mode_counts_rows_and_says_so(live_neo4j):
    spec = PullSpec(mode="cypher", query="MATCH (a:Account)-[t:TX]->(b:Account) RETURN a, t, b")
    est = estimate_pull(spec, GraphMapping())
    accounts, payments, _, _ = fixture_counts()
    assert est.mode == "cypher" and est.relationships == payments and est.nodes == accounts
    assert "2,000 rows" in est.note and "read-only" in est.note
    assert est.memory_bytes == estimate_memory(accounts, payments)
    with_params = PullSpec(mode="cypher", params={"min": 4000},
                           query="MATCH (a:Account)-[t:TX]->(b:Account) WHERE t.amount > $min RETURN a, t, b")
    assert 0 < estimate_pull(with_params, GraphMapping()).relationships < payments


@pytest.mark.live
def test_the_pulls_own_statements_count_the_same_in_cypher_mode(live_neo4j):
    """Cypher mode over the two statements schema mode would send gives the schema counts (nodes shared by both
    statements count once)."""
    window = {"since": "2026-10-01T00:00:00Z"}
    stmts = schema_statements(GraphMapping(), PullSpec(**window))
    by_schema = estimate_pull(PullSpec(**window), GraphMapping())
    by_cypher = estimate_pull(PullSpec(mode="cypher", query=stmts.query, params=stmts.params), GraphMapping())
    assert (by_cypher.nodes, by_cypher.relationships) == (by_schema.nodes, by_schema.relationships)


@pytest.mark.live
def test_a_query_that_writes_is_refused_and_writes_nothing(live_neo4j):
    spec = PullSpec(mode="cypher", query="CREATE (n:EstimateProbe {x: 1}) RETURN n")
    with pytest.raises(ValueError, match="Writing in read access mode not allowed"):  # the server's own refusal
        estimate_pull(spec, GraphMapping())
    assert master_rows("MATCH (n:EstimateProbe) RETURN count(n) AS n") == [{"n": 0}]
    with pytest.raises(ValueError, match="read access mode"):
        estimate_pull(PullSpec(mode="cypher", query="MATCH (a:Account) SET a.probe = 1 RETURN a"), GraphMapping())
    assert master_rows("MATCH (n) WHERE n.probe IS NOT NULL RETURN count(n) AS n") == [{"n": 0}]


@pytest.mark.live
def test_a_query_with_no_graph_elements_counts_none(live_neo4j):
    est = estimate_pull(PullSpec(mode="cypher", query="MATCH (a:Account) RETURN count(a) AS n"), GraphMapping())
    assert (est.nodes, est.relationships) == (0, 0) and "1 rows" in est.note


@pytest.mark.live
def test_a_pull_is_never_refused_for_the_search_cap(live_neo4j, restore_settings):
    restore_settings.setenv("LAB_SLICE_CAP", "100")
    reload_settings()
    est = estimate_pull(PullSpec(), GraphMapping())
    assert est.nodes > 100 and not est.over_cap and est.cap == 100 and est.memory_bytes > 0


@pytest.mark.live
def test_estimate_routes_a_pull_request_to_the_master_graph(live_neo4j):
    spec = PullSpec(until="2026-10-02T00:00:00Z")
    assert estimate(SliceRequest(pull=spec)) == estimate_pull(spec, GraphMapping())
    assert estimate(SliceRequest(recipe=Recipe(pull=spec))) == estimate_pull(spec, GraphMapping())
