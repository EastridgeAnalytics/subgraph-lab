"""Track 1e: the lab's own delta. The unit tests read a stand-in for the master graph; the live tests read the test Neo4j."""
from __future__ import annotations

import polars as pl
import pytest

from lab.common.neo4j import neo4j_session
from lab.contracts.recipe import Recipe
from lab.roundtrip.delta import BUCKETS, DeltaError, bucket, compute, sample_positions
from lab.roundtrip.stage import NothingStaged, stage_disposition
from lab.runs import Run
from lab.settings import get_settings
from tests.push_helpers import (
    FakeSession,
    account_frame,
    alert_frame,
    community_frame,
    customer_state,
    lab_snapshot,
    patch_master,
    wipe_lab,
)


# ---- the pieces --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("change,expected", [
    (0.0, "0.0-0.1"), (0.05, "0.0-0.1"), (0.0999, "0.0-0.1"), (0.1, "0.1-0.2"), (0.7 - 0.6, "0.1-0.2"),
    (0.1999, "0.1-0.2"), (0.2, "0.2-0.5"), (-0.3, "0.2-0.5"), (0.4999, "0.2-0.5"), (0.5, "0.5-1.0"),
    (-0.9, "0.5-1.0"), (1.0, "0.5-1.0"),
])
def test_buckets_by_the_size_of_the_change(change, expected):
    assert bucket(change) == expected
    assert expected in BUCKETS


def test_the_sample_is_spread_over_the_keys_and_always_the_same():
    assert sample_positions(3, 1000) == [0, 1, 2]
    picked = sample_positions(100, 5)
    assert len(picked) == 5 and picked[0] == 0 and picked[-1] == 99 and picked == sorted(set(picked))
    assert sample_positions(100, 5) == picked
    assert len(sample_positions(1_000_000, 1000)) == 1000
    assert sample_positions(0, 10) == []


# ---- against a stand-in master graph -------------------------------------------------------------------------------
def remote_answer(scores: dict, alerts: set = frozenset(), communities: set = frozenset()):
    def answer(q, p):
        if "OPTIONAL MATCH" in q:
            return [{"key": k, "found": k in scores, "score": scores.get(k)} for k in p["keys"]]
        if "(n:Alert)" in q:
            return [{"k": k} for k in p["keys"] if k in alerts]
        if "(n:RiskCommunity)" in q:
            return [{"k": k} for k in p["keys"] if k in communities]
        return []

    return answer


@pytest.fixture
def staged(fixture_run):
    keys = [f"A{n:04d}" for n in range(1, 11)]
    fixture_run.stage(account_frame(keys, score=0.5), kind="account")
    fixture_run.stage(alert_frame([("al-1", "A0001"), ("al-2", "A0002"), ("al-3", "A0003")]), kind="alert")
    fixture_run.stage(community_frame("co-1", keys[:4]), kind="community")
    return fixture_run


def test_the_delta_buckets_scores_and_counts_what_is_new(staged, monkeypatch):
    # moves of 0.05, 0.15, 0.3 and 0.6; A0005 is on the master graph with no score; A0007 to A0010 are not there at all
    scores = {"A0001": 0.45, "A0002": 0.35, "A0003": 0.2, "A0004": -0.1, "A0005": None, "A0006": 0.5}
    session = patch_master(monkeypatch, FakeSession(remote_answer(scores, alerts={"al-1"}, communities={"co-1"})))
    d = compute(staged)
    assert d.score_changes == {"new": 5, "0.0-0.1": 2, "0.1-0.2": 1, "0.2-0.5": 1, "0.5-1.0": 1}
    assert d.missing_remote_keys == 4 and d.sampled == 10 and d.accounts_scored == 10
    assert (d.new_alerts, d.new_communities) == (2, 0)
    assert d.vectors == 11 and d.dispositions == 0 and d.bytes_estimate > 0
    # reads only: every statement ran in a read transaction, and nothing wrote
    assert set(session.modes) == {"read"} and session.writes() == []


def test_keys_go_to_the_master_graph_key_batch_at_a_time(staged, monkeypatch):
    monkeypatch.setattr(get_settings(), "key_batch", 4)
    session = patch_master(monkeypatch, FakeSession(remote_answer({})))
    compute(staged)
    sent = [len(p["keys"]) for q, p in session.statements if "OPTIONAL MATCH" in q]
    assert sent == [4, 4, 2]  # ten accounts
    assert [len(p["keys"]) for q, p in session.statements if "(n:Alert)" in q] == [3]
    monkeypatch.setattr(get_settings(), "key_batch", 2)
    session = patch_master(monkeypatch, FakeSession(remote_answer({})))
    compute(staged)
    assert [len(p["keys"]) for q, p in session.statements if "(n:Alert)" in q] == [2, 1]


def test_only_delta_sample_accounts_are_read_and_they_are_spread_out(staged, monkeypatch):
    monkeypatch.setattr(get_settings(), "delta_sample", 3)
    session = patch_master(monkeypatch, FakeSession(remote_answer({})))
    d = compute(staged)
    sent = [k for q, p in session.statements if "OPTIONAL MATCH" in q for k in p["keys"]]
    assert d.sampled == 3 and sent == ["A0001", "A0005", "A0010"] and d.accounts_scored == 10  # counts cover them all
    assert d.missing_remote_keys == 3 and d.score_changes["new"] == 3


def test_an_account_with_no_score_of_its_own_counts_as_missing_but_is_not_bucketed(fixture_run, monkeypatch):
    fixture_run.stage(account_frame(["A0001"]), kind="account")
    fixture_run.stage(alert_frame([("al-1", "A0099")]), kind="alert")  # A0099 gets a bare account in the push store
    patch_master(monkeypatch, FakeSession(remote_answer({"A0001": 0.9})))
    d = compute(fixture_run)
    assert d.sampled == 2 and d.missing_remote_keys == 1 and d.accounts_scored == 1
    assert d.score_changes == {"new": 0, "0.0-0.1": 1, "0.1-0.2": 0, "0.2-0.5": 0, "0.5-1.0": 0}


def test_the_delta_needs_something_staged_and_a_master_graph_it_can_read(fixture_run, monkeypatch):
    patch_master(monkeypatch, FakeSession())
    with pytest.raises(NothingStaged):
        compute(fixture_run)
    fixture_run.stage(account_frame(["A0001"]), kind="account")

    class Down(FakeSession):
        def execute_read(self, fn, *a):
            raise ConnectionError("Couldn't connect to localhost:1")

    patch_master(monkeypatch, Down())
    with pytest.raises(DeltaError, match="could not read the master graph over Bolt: ConnectionError"):
        compute(fixture_run)


def test_dispositions_and_vectors_are_counted_from_the_push_store(fixture_run, monkeypatch):
    fixture_run.stage(account_frame(["A0001", "A0002"], dim=4).drop("risk_score"), kind="account")
    stage_disposition(fixture_run, "account", "A0001", "confirmed", "tim")
    stage_disposition(fixture_run, "account", "A0003", "needs_more", "tim")
    patch_master(monkeypatch, FakeSession(remote_answer({})))
    d = compute(fixture_run)
    assert (d.dispositions, d.vectors, d.accounts_scored) == (2, 2, 0)
    assert d.score_changes == dict.fromkeys(("new", *BUCKETS), 0)  # nothing scored, nothing to bucket


def test_the_bytes_estimate_grows_with_the_rows(fixture_run, monkeypatch):
    patch_master(monkeypatch, FakeSession(remote_answer({})))
    fixture_run.stage(account_frame([f"A{n:04d}" for n in range(10)]), kind="account")
    small = compute(fixture_run).bytes_estimate
    fixture_run.stage(account_frame([f"B{n:04d}" for n in range(100)]), kind="account")
    assert compute(fixture_run).bytes_estimate > small * 5


# ---- live ------------------------------------------------------------------------------------------------------
@pytest.fixture
def lab(live_neo4j, data_dir):
    wipe_lab()
    run = Run.create(Recipe(), data_dir=data_dir)
    yield run
    run.close()
    wipe_lab()


def fixture_keys(n: int) -> list[str]:
    with neo4j_session() as s:
        return [r["k"] for r in s.run("MATCH (a:Account) RETURN a.account_id AS k ORDER BY k LIMIT $n", n=n)]


@pytest.mark.live
def test_live_delta_buckets_the_change_and_never_writes(lab, monkeypatch):
    keys = fixture_keys(6)
    remote = {keys[0]: 0.50, keys[1]: 0.50, keys[2]: 0.50, keys[3]: 0.10, keys[4]: None}
    with neo4j_session() as s:
        for k, score in remote.items():
            if score is not None:
                s.run("MATCH (a:Account {account_id: $k}) SET a.risk_score = $s", k=k, s=score).consume()
    staged_scores = [0.55, 0.65, 0.80, 0.70, 0.40, 0.30]  # moves of 0.05, 0.15, 0.30, 0.60; then two accounts with no score
    frame = pl.DataFrame({"account_id": [*keys, "ZZ-not-on-the-master-graph"], "risk_score": [*staged_scores, 0.9]})
    lab.stage(frame, kind="account")
    lab.stage(alert_frame([("live-al-1", keys[0]), ("live-al-2", keys[1])]), kind="alert")
    before_customer, before_lab = customer_state(), lab_snapshot()

    d = compute(lab)
    assert d.score_changes == {"new": 3, "0.0-0.1": 1, "0.1-0.2": 1, "0.2-0.5": 1, "0.5-1.0": 1}
    assert d.missing_remote_keys == 1 and d.sampled == 7 and d.accounts_scored == 7
    assert (d.new_alerts, d.new_communities) == (2, 0) and d.bytes_estimate > 0

    monkeypatch.setattr(get_settings(), "delta_sample", 3)
    monkeypatch.setattr(get_settings(), "key_batch", 2)
    small = compute(lab)
    assert small.sampled == 3 and small.accounts_scored == 7  # the counts still cover every staged account
    assert sum(small.score_changes.values()) == 3
    assert customer_state() == before_customer and lab_snapshot() == before_lab  # nothing was written
