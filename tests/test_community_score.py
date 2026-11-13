"""Track 3a: the community score, part by part, on hand-made communities of the fixture graph.

R1 is the mule ring: six accounts opened in the last weeks on one device and one address, money in from outsiders,
then three fast rounds through the ring. R2 is structuring: eight accounts on one email paying one collector. L1 is a
family of four on one laptop, older accounts, small payments among themselves. Account scores are made by hand.
"""
from __future__ import annotations

import json

import polars as pl
import pytest

import lab.scoring.community_score as community_score
from lab.common import community_key
from lab.contracts.factors import CommunityPolicy, Policy
from lab.scoring import load_policy
from lab.scoring.communities import find_communities
from lab.scoring.community_score import PARTS, SCHEMA, score_communities
from lab.settings import reload_settings
from tests.fixtures.make_fixture import L1, R1, R2

KEY = "account_id"
NORMAL = ["A0001", "A0002", "A0003"]  # three ordinary accounts: no shared identifier, nothing in common


@pytest.fixture
def restore_settings(monkeypatch):
    yield monkeypatch
    monkeypatch.undo()
    reload_settings()


@pytest.fixture
def accounts(fixture_run) -> list[str]:
    return fixture_run.table("MATCH (a:Account) RETURN a.account_id AS k ORDER BY k")["k"].to_list()


def scores_frame(accounts: list[str], risk: dict[str, float] | None = None) -> pl.DataFrame:
    risk = risk or {}
    return pl.DataFrame({KEY: accounts, "risk_score": [risk.get(a, 0.0) for a in accounts]})


def policy_with(**weights: float) -> Policy:
    return Policy(community=CommunityPolicy(min_size=3, weights=weights))


def parts_of(df: pl.DataFrame, members) -> dict[str, float]:
    """The five parts of the community whose members are exactly `members`."""
    row = df.filter(pl.col("member_keys").list.join(",") == ",".join(sorted(members)))
    assert row.height == 1
    return json.loads(row["score_parts"][0])


def test_the_table_has_the_staged_columns(fixture_run, accounts):
    df = score_communities(fixture_run, [set(R1), set(L1)], scores_frame(accounts), load_policy(fixture_run))
    assert df.schema == SCHEMA and df.height == 2
    for r in df.iter_rows(named=True):
        members = r["member_keys"]
        assert r["community_key"] == community_key(fixture_run.id, members)  # sha1(run id | lowest member key)
        assert members == sorted(members) and r["size"] == len(members) and r["run_id"] == fixture_run.id
        assert r["created_at"].endswith("Z") and len(r["created_at"]) == 20  # UTC ISO-8601 string
        assert list(json.loads(r["score_parts"])) == sorted(PARTS)  # canonical JSON: sorted keys, all five parts


def test_every_part_and_the_score_are_in_zero_to_one(fixture_run, accounts):
    groups = find_communities(fixture_run)
    risk = {a: 0.9 for a in [*R1, *R2]} | {a: 1.0 for a in L1}
    df = score_communities(fixture_run, groups, scores_frame(accounts, risk), load_policy(fixture_run))
    assert df.height == len(groups) == 9 and df["size"].sum() == 200
    for r in df.iter_rows(named=True):
        parts = json.loads(r["score_parts"])
        assert set(parts) == set(PARTS) and all(0.0 <= v <= 1.0 for v in parts.values())
        assert 0.0 <= r["score"] <= 1.0
    assert df["score"].to_list() == sorted(df["score"].to_list(), reverse=True)  # best first


# ---- the parts -----------------------------------------------------------------------------------------------------
def test_member_risk_blends_the_share_at_the_threshold_with_the_mean(fixture_run, accounts):
    policy = load_policy(fixture_run)  # alert_threshold 0.6
    risk = {R1[0]: 0.6, R1[1]: 0.9, R1[2]: 0.59, R1[3]: 0.0, R1[4]: 0.3, R1[5]: 0.0}
    got = parts_of(score_communities(fixture_run, [set(R1)], scores_frame(accounts, risk), policy), R1)
    share, mean = 2 / 6, sum(risk.values()) / 6  # 0.6 is at the threshold and counts; 0.59 is not
    assert got["member_risk"] == pytest.approx(0.5 * share + 0.5 * mean)
    assert community_score.MEMBER_RISK_SHARE == 0.5  # "the plain average", said on the page and here
    hot = {a: 1.0 for a in R1}
    assert parts_of(score_communities(fixture_run, [set(R1)], scores_frame(accounts, hot), policy), R1)["member_risk"] == 1.0
    assert parts_of(score_communities(fixture_run, [set(R1)], scores_frame(accounts), policy), R1)["member_risk"] == 0.0


def test_an_account_with_no_score_counts_zero(fixture_run, accounts):
    only_two = scores_frame([*R1[:2]], {R1[0]: 1.0, R1[1]: 1.0})  # four members are not in the scores at all
    got = parts_of(score_communities(fixture_run, [set(R1)], only_two, load_policy(fixture_run)), R1)
    assert got["member_risk"] == pytest.approx(0.5 * (2 / 6) + 0.5 * (2 / 6))


def test_shared_identifiers_is_the_share_of_members_that_share_one(fixture_run, accounts):
    df = score_communities(fixture_run, [set(R1), set(R2), set(L1), set(NORMAL)], scores_frame(accounts), load_policy(fixture_run))
    assert parts_of(df, R1)["shared_identifiers"] == 1.0  # one device and one address
    assert parts_of(df, R2)["shared_identifiers"] == 1.0  # one email
    assert parts_of(df, L1)["shared_identifiers"] == 1.0
    assert parts_of(df, NORMAL)["shared_identifiers"] == 0.0
    mixed = score_communities(fixture_run, [{*R1[:3], *NORMAL}], scores_frame(accounts), load_policy(fixture_run))
    assert parts_of(mixed, {*R1[:3], *NORMAL})["shared_identifiers"] == pytest.approx(3 / 6)


def test_an_identifier_too_widely_shared_counts_for_nothing_as_in_the_graph(fixture_run, accounts, restore_settings):
    restore_settings.setenv("LAB_SHARED_MAX", "5")  # the ring's device and address are used by six accounts
    reload_settings()
    df = score_communities(fixture_run, [set(R1), set(L1)], scores_frame(accounts), load_policy(fixture_run))
    assert parts_of(df, R1)["shared_identifiers"] == 0.0
    assert parts_of(df, L1)["shared_identifiers"] == 1.0  # four accounts: still under the limit


def test_cycles_finds_the_ring_and_not_a_collector(fixture_run, accounts):
    df = score_communities(fixture_run, [set(R1), set(R2), set(NORMAL)], scores_frame(accounts), load_policy(fixture_run))
    assert parts_of(df, R1)["cycles"] == 1.0  # every payment inside the ring is on the six-account cycle
    assert parts_of(df, R2)["cycles"] == 0.0  # seven accounts paying one collector, nothing comes back
    assert parts_of(df, NORMAL)["cycles"] == 0.0  # no payments between them at all


def test_cycles_is_the_share_of_the_money_on_cycle_edges(fixture_run, accounts):
    policy = load_policy(fixture_run)
    payer = fixture_run.table(
        "MATCH (p:Account)-[:TX]->(:Account {account_id: $r}) WHERE NOT p.account_id IN $ring "
        "RETURN DISTINCT p.account_id AS k ORDER BY k", {"r": R1[0], "ring": R1}
    )["k"][0]  # an outsider who pays the ring
    money = fixture_run.table(
        "MATCH (s:Account)-[t:TX]->(d:Account) WHERE s.account_id IN $m AND d.account_id IN $m "
        "RETURN s.account_id AS s, d.account_id AS d, t.amount AS amount", {"m": [*R1, payer]})
    into_ring = money.filter(pl.col("s") == payer)["amount"].sum()
    ring = money.filter(pl.col("s") != payer)["amount"].sum()
    got = parts_of(score_communities(fixture_run, [{*R1, payer}], scores_frame(accounts), policy), {*R1, payer})
    assert 0 < into_ring and got["cycles"] == pytest.approx(ring / (ring + into_ring))  # the payer's money is not on a cycle
    three = score_communities(fixture_run, [set(R1[:3])], scores_frame(accounts), policy)
    assert parts_of(three, set(R1[:3]))["cycles"] == 0.0  # the first three of the ring: no way back to the start


def test_cycles_stops_at_the_bound(fixture_run, accounts, monkeypatch):
    policy = load_policy(fixture_run)
    assert community_score.CYCLE_MAX_LENGTH == 6  # the fixture's ring has six accounts
    monkeypatch.setattr(community_score, "CYCLE_MAX_LENGTH", 5)
    assert parts_of(score_communities(fixture_run, [set(R1)], scores_frame(accounts), policy), R1)["cycles"] == 0.0
    monkeypatch.setattr(community_score, "CYCLE_MAX_LENGTH", 6)
    assert parts_of(score_communities(fixture_run, [set(R1)], scores_frame(accounts), policy), R1)["cycles"] == 1.0


def test_new_accounts_is_the_share_opened_near_the_newest_payment(fixture_run, accounts, restore_settings):
    df = score_communities(fixture_run, [set(R1), set(R2), set(L1), set(NORMAL)], scores_frame(accounts), load_policy(fixture_run))
    assert parts_of(df, R1)["new_accounts"] == 1.0
    assert parts_of(df, R2)["new_accounts"] == 1.0
    assert parts_of(df, L1)["new_accounts"] == 0.0
    assert parts_of(df, NORMAL)["new_accounts"] == 0.0
    mixed = score_communities(fixture_run, [{*R1[:2], *L1[:2]}], scores_frame(accounts), load_policy(fixture_run))
    assert parts_of(mixed, {*R1[:2], *L1[:2]})["new_accounts"] == 0.5
    restore_settings.setenv("LAB_NEW_ACCOUNT_DAYS", "1")  # nothing is that new
    reload_settings()
    short = score_communities(fixture_run, [set(R1)], scores_frame(accounts), load_policy(fixture_run))
    assert parts_of(short, R1)["new_accounts"] == 0.0


def test_speed_is_the_share_of_members_with_money_in_and_out_within_the_window(fixture_run, accounts, restore_settings):
    df = score_communities(fixture_run, [set(R1), set(R2)], scores_frame(accounts), load_policy(fixture_run))
    assert parts_of(df, R1)["speed"] == 1.0  # each member sends on within hours of being paid
    assert parts_of(df, R2)["speed"] == pytest.approx(1 / 8)  # only the collector receives and pays out the same day
    restore_settings.setenv("LAB_PASS_THROUGH_HOURS", "0")
    reload_settings()
    none = score_communities(fixture_run, [set(R1)], scores_frame(accounts), load_policy(fixture_run))
    assert parts_of(none, R1)["speed"] == 0.0


def test_the_planted_ring_outscores_the_look_alike_and_an_ordinary_group(fixture_run, accounts):
    risk = {a: 0.8 for a in R1}  # the account scores flag the ring, as the shipped factors do
    df = score_communities(fixture_run, [set(R1), set(L1), set(NORMAL)], scores_frame(accounts, risk), load_policy(fixture_run))
    by_size = {r["size"]: r["score"] for r in df.iter_rows(named=True)}
    assert df["member_keys"].to_list()[0] == sorted(R1) and by_size[6] > by_size[4] > by_size[3]


# ---- the score -----------------------------------------------------------------------------------------------------
def test_the_score_is_the_weighted_mean_of_the_parts_with_the_policys_weights(fixture_run, accounts):
    policy = load_policy(fixture_run)
    w = policy.community.weights
    risk = {a: 0.7 for a in R1[:4]}
    df = score_communities(fixture_run, [set(R1), set(R2), set(L1)], scores_frame(accounts, risk), policy)
    for r in df.iter_rows(named=True):
        parts = json.loads(r["score_parts"])
        assert r["score"] == pytest.approx(sum(w[p] * parts[p] for p in PARTS) / sum(w.values()))
    ring = parts_of(df, R1)
    assert df.filter(pl.col("size") == 6)["score"][0] == pytest.approx(
        0.35 * ring["member_risk"] + 0.2 * ring["shared_identifiers"] + 0.15 * (ring["cycles"] + ring["new_accounts"] + ring["speed"])
    )


def test_other_weights_make_another_score_and_they_need_not_add_to_one(fixture_run, accounts):
    scores = scores_frame(accounts, {a: 0.9 for a in R1[:3]})
    only_cycles = score_communities(fixture_run, [set(R1), set(R2)], scores, policy_with(cycles=1.0))
    assert dict(zip(only_cycles["size"], only_cycles["score"], strict=True)) == {6: 1.0, 8: 0.0}
    two_to_one = score_communities(fixture_run, [set(R1)], scores, policy_with(cycles=2.0, member_risk=1.0))
    parts = parts_of(two_to_one, R1)
    assert two_to_one["score"][0] == pytest.approx((2 * parts["cycles"] + parts["member_risk"]) / 3)
    assert parts["new_accounts"] == 1.0  # a part with no weight is still shown, and adds nothing


def test_a_weight_that_names_no_part_is_an_error(fixture_run, accounts):
    with pytest.raises(ValueError, match=r"community.weights names \['vibes'\]"):
        score_communities(fixture_run, [set(R1)], scores_frame(accounts), policy_with(cycles=1.0, vibes=2.0))


def test_a_policy_with_no_weight_scores_zero_and_keeps_the_parts(fixture_run, accounts):
    df = score_communities(fixture_run, [set(R1)], scores_frame(accounts), policy_with())
    assert df["score"][0] == 0.0 and parts_of(df, R1)["cycles"] == 1.0


def test_communities_under_min_size_are_not_scored(fixture_run, accounts):
    pair = {R2[0], R2[1]}
    groups = [set(R1), pair, {R2[2]}]
    scores = scores_frame(accounts)
    kept = score_communities(fixture_run, groups, scores, load_policy(fixture_run))  # min_size 3
    assert kept["size"].to_list() == [6]
    two = Policy(community=CommunityPolicy(min_size=2))
    assert sorted(score_communities(fixture_run, groups, scores, two)["size"].to_list()) == [2, 6]
    assert score_communities(fixture_run, groups, scores, Policy(community=CommunityPolicy(min_size=7))).schema == SCHEMA
    assert score_communities(fixture_run, [], scores, load_policy(fixture_run)).height == 0


def test_a_member_cannot_be_in_two_communities(fixture_run, accounts):
    with pytest.raises(ValueError, match="in two communities"):
        score_communities(fixture_run, [set(R1), {R1[0], *NORMAL}], scores_frame(accounts), load_policy(fixture_run))


def test_a_score_reads_the_scores_given_not_a_file(fixture_run, accounts):
    """The same communities with the account scores raised: only member_risk and the score move."""
    low = score_communities(fixture_run, [set(R1)], scores_frame(accounts), load_policy(fixture_run))
    high = score_communities(fixture_run, [set(R1)], scores_frame(accounts, {a: 1.0 for a in R1}), load_policy(fixture_run))
    a, b = parts_of(low, R1), parts_of(high, R1)
    assert {p for p in PARTS if a[p] != b[p]} == {"member_risk"} and high["score"][0] > low["score"][0]
