"""Track 3a and 5b: the community score, part by part, on hand-made communities of the fixture graph.

R1 is the mule ring: six accounts opened in the last weeks on one device and one address, money in from outsiders,
then three fast rounds through the ring. R2 is structuring: eight accounts on one email paying one collector. L1 is a
family of four on one laptop, older accounts, small payments among themselves. Account scores are made by hand, except
where a test says it runs the factors.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import polars as pl
import pytest

from lab.common import community_key
from lab.contracts.factors import CommunityPolicy, Policy
from lab.scoring import FactorRun, account_scores, load_policy, run_factors
from lab.scoring.communities import IDENTIFIER, PAYMENT, Community, find_communities
from lab.scoring.community_score import FORMED_PART, PARTS, SCHEMA, score_communities
from lab.settings import reload_settings
from tests.fixtures.make_fixture import L1, R1, R2

KEY = "account_id"
REPO = Path(__file__).resolve().parent.parent
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
        assert list(json.loads(r["score_parts"])) == sorted([*PARTS, FORMED_PART])  # canonical JSON: the five parts and how it formed
        assert r["formed"] == PAYMENT  # plain sets are taken to have formed by payment


def test_every_part_and_the_score_are_in_zero_to_one(fixture_run, accounts):
    groups = find_communities(fixture_run)
    risk = {a: 0.9 for a in [*R1, *R2]} | {a: 1.0 for a in L1}
    df = score_communities(fixture_run, groups, scores_frame(accounts, risk), load_policy(fixture_run))
    assert df.height == len(groups) == 10 and df["size"].sum() == 200
    for r in df.iter_rows(named=True):
        parts = json.loads(r["score_parts"])
        assert set(parts) == {*PARTS, FORMED_PART} and all(0.0 <= v <= 1.0 for v in parts.values())
        assert 0.0 <= r["score"] <= 1.0
    assert df["score"].to_list() == sorted(df["score"].to_list(), reverse=True)  # best first


# ---- the parts -----------------------------------------------------------------------------------------------------
def test_member_risk_blends_the_share_at_the_threshold_with_the_mean(fixture_run, accounts):
    policy = load_policy(fixture_run)  # alert_threshold 0.6
    risk = {R1[0]: 0.6, R1[1]: 0.9, R1[2]: 0.59, R1[3]: 0.0, R1[4]: 0.3, R1[5]: 0.0}
    got = parts_of(score_communities(fixture_run, [set(R1)], scores_frame(accounts, risk), policy), R1)
    share, mean = 2 / 6, sum(risk.values()) / 6  # 0.6 is at the threshold and counts; 0.59 is not
    assert got["member_risk"] == pytest.approx(0.5 * share + 0.5 * mean)
    assert policy.community.member_risk_share == 0.5  # "the plain average", read from the policy file's default
    hot = {a: 1.0 for a in R1}
    assert parts_of(score_communities(fixture_run, [set(R1)], scores_frame(accounts, hot), policy), R1)["member_risk"] == 1.0
    assert parts_of(score_communities(fixture_run, [set(R1)], scores_frame(accounts), policy), R1)["member_risk"] == 0.0


def test_the_policy_sets_the_mix_in_member_risk(fixture_run, accounts):
    risk = {R1[0]: 0.6, R1[1]: 0.9, R1[2]: 0.59, R1[3]: 0.0, R1[4]: 0.3, R1[5]: 0.0}
    share, mean = 2 / 6, sum(risk.values()) / 6
    for mix in (0.0, 0.25, 1.0):
        policy = Policy(community=CommunityPolicy(min_size=3, member_risk_share=mix))
        got = parts_of(score_communities(fixture_run, [set(R1)], scores_frame(accounts, risk), policy), R1)
        assert got["member_risk"] == pytest.approx(mix * share + (1 - mix) * mean)


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


def test_cycles_stops_at_the_bound(fixture_run, accounts, restore_settings):
    policy = load_policy(fixture_run)
    assert parts_of(score_communities(fixture_run, [set(R1)], scores_frame(accounts), policy), R1)["cycles"] == 1.0  # six accounts
    restore_settings.setenv("LAB_CYCLE_MAX_LENGTH", "5")  # the fixture's ring has six
    reload_settings()
    assert parts_of(score_communities(fixture_run, [set(R1)], scores_frame(accounts), policy), R1)["cycles"] == 0.0
    restore_settings.setenv("LAB_CYCLE_MAX_LENGTH", "6")
    reload_settings()
    assert parts_of(score_communities(fixture_run, [set(R1)], scores_frame(accounts), policy), R1)["cycles"] == 1.0


def test_cycles_count_only_when_each_payment_comes_after_the_one_before(fixture_run, accounts):
    """The ring's payments run forward round the loop. Turn the ring's own payments round in time, so each comes before the
    one it pays on to, and the same six accounts with the same money no longer have a cycle."""
    policy = load_policy(fixture_run)
    assert parts_of(score_communities(fixture_run, [set(R1)], scores_frame(accounts), policy), R1)["cycles"] == 1.0
    ring = fixture_run.table(
        "MATCH (a:Account)-[t:TX]->(b:Account) WHERE a.account_id IN $ring AND b.account_id IN $ring "
        "RETURN t.tx_id AS id, t.ts AS ts", {"ring": R1})
    first, last = ring["ts"].min(), ring["ts"].max()
    turned = [{"id": i, "ts": (first + (last - t)).strftime("%Y-%m-%dT%H:%M:%SZ")} for i, t in ring.iter_rows()]
    fixture_run.store().execute(
        "UNWIND $rows AS r MATCH (:Account)-[t:TX {tx_id: r.id}]->(:Account) SET t.ts = datetime(r.ts)", {"rows": turned})
    got = parts_of(score_communities(fixture_run, [set(R1)], scores_frame(accounts), policy), R1)
    assert got["cycles"] < 1.0  # the money still moves round the ring, in the wrong order for a cycle


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


def test_speed_is_the_share_of_members_the_in_and_out_factor_fires_on(fixture_run, accounts):
    df = score_communities(fixture_run, [set(R1), set(R2), set(L1)], scores_frame(accounts), load_policy(fixture_run))
    assert parts_of(df, R1)["speed"] == 1.0  # each member sends on within hours of being paid, in a similar amount
    assert parts_of(df, R2)["speed"] == 0.0  # the collector pays out one lump sum, not a sum like each payment in
    assert parts_of(df, L1)["speed"] == 0.0


def test_speed_takes_the_factors_threshold_and_the_factors_rows(fixture_run, accounts, tmp_path, restore_settings):
    """One definition: the factor's query and its threshold. Raise the threshold past what the ring's members match (three
    outgoing payments each) and `speed` falls to nothing; the factor and the part agree because they are one query."""
    folder = tmp_path / "factors"
    shutil.copytree(REPO / "factors", folder)
    text = (folder / "factors.yaml").read_text(encoding="utf-8")
    start = text.index("- id: in_and_out_within_a_day")
    stop = text.index("- id: just_under_threshold")
    block = text[start:stop].replace("threshold: 3", "threshold: 4")
    assert block != text[start:stop]
    (folder / "factors.yaml").write_text(text[:start] + block + text[stop:], encoding="utf-8")
    restore_settings.setenv("LAB_FACTORS_FILE", str(folder / "factors.yaml"))
    reload_settings()
    policy = load_policy(fixture_run)
    assert parts_of(score_communities(fixture_run, [set(R1)], scores_frame(accounts), policy), R1)["speed"] == 0.0
    fired = run_factors(fixture_run, None).results.filter(pl.col("factor_id") == "in_and_out_within_a_day")
    assert fired.filter(pl.col("key").is_in(R1)).height == 0


def test_a_factor_run_is_used_and_gives_the_same_parts(fixture_run, accounts):
    policy = load_policy(fixture_run)
    factor_run = run_factors(fixture_run, None)
    groups = find_communities(fixture_run)
    own = score_communities(fixture_run, groups, scores_frame(accounts), policy)
    given = score_communities(fixture_run, groups, scores_frame(accounts), policy, factor_run)
    assert own.drop("created_at").equals(given.drop("created_at"))
    # the rows of the factor run are what count: a factor run that fired on nobody says nobody is fast
    none = FactorRun(path=factor_run.path, file=factor_run.file, results=factor_run.results.head(0), status=[], keys=None)
    assert parts_of(score_communities(fixture_run, [set(R1)], scores_frame(accounts), policy, none), R1)["speed"] == 0.0


def test_the_planted_rings_and_the_look_alike_by_the_real_account_scores(fixture_run):
    """R1 and R2 are identifier communities of their own; L1, the family, is one too, and scores below the alert line."""
    policy = load_policy(fixture_run)
    factor_run = run_factors(fixture_run, None)
    scores = account_scores(fixture_run, factor_run, policy)
    groups = find_communities(fixture_run)
    df = score_communities(fixture_run, groups, scores, policy, factor_run)
    by = {frozenset(m): r for m, r in zip(df["member_keys"], df.iter_rows(named=True), strict=True)}
    r1, r2, l1 = by[frozenset(R1)], by[frozenset(R2)], by[frozenset(L1)]
    assert (r1["formed"], r2["formed"], l1["formed"]) == (IDENTIFIER, IDENTIFIER, IDENTIFIER)
    assert r1["score"] >= policy.alert_threshold > l1["score"]  # the family has the shared laptop and the money back and forth, no more
    print(f"\nfixture community scores: R1 {r1['score']:.3f}, R2 {r2['score']:.3f}, L1 {l1['score']:.3f}; threshold {policy.alert_threshold}")


def test_a_community_says_how_it_formed_in_a_part_and_a_column(fixture_run, accounts):
    policy = load_policy(fixture_run)
    df = score_communities(fixture_run, [Community(R1, IDENTIFIER), Community(NORMAL, PAYMENT), set(L1)], scores_frame(accounts), policy)
    by = {r["size"]: r for r in df.iter_rows(named=True)}
    assert (by[6]["formed"], by[3]["formed"], by[4]["formed"]) == (IDENTIFIER, PAYMENT, PAYMENT)  # a plain set: by payment
    assert json.loads(by[6]["score_parts"])[FORMED_PART] == 1.0 and json.loads(by[3]["score_parts"])[FORMED_PART] == 0.0
    assert df.schema == SCHEMA and "formed" not in policy.community.weights
    only_formed = Policy(community=CommunityPolicy(min_size=3, weights={FORMED_PART: 1.0}))
    with pytest.raises(ValueError, match="the parts are"):  # it is not a part of the score and takes no weight
        score_communities(fixture_run, [set(R1)], scores_frame(accounts), only_formed)


def test_the_planted_ring_outscores_the_look_alike_and_an_ordinary_group(fixture_run, accounts):
    risk = {a: 0.8 for a in R1}  # the account scores flag the ring, as the shipped factors do
    df = score_communities(fixture_run, [set(R1), set(L1), set(NORMAL)], scores_frame(accounts, risk), load_policy(fixture_run))
    by_size = {r["size"]: r["score"] for r in df.iter_rows(named=True)}
    assert df["member_keys"].to_list()[0] == sorted(R1) and by_size[6] > by_size[4] > by_size[3]


# ---- the score -----------------------------------------------------------------------------------------------------
def test_the_shipped_score_is_the_noisy_or_of_the_parts_with_the_policys_weights(fixture_run, accounts):
    policy = load_policy(fixture_run)
    assert policy.community.combine == "noisy_or"  # decision 45
    w = policy.community.weights
    risk = {a: 0.7 for a in R1[:4]}
    df = score_communities(fixture_run, [set(R1), set(R2), set(L1)], scores_frame(accounts, risk), policy)
    for r in df.iter_rows(named=True):
        parts = json.loads(r["score_parts"])
        left = 1.0
        for p in PARTS:
            left *= 1 - w[p] * parts[p]
        assert r["score"] == pytest.approx(1 - left)
    ring = parts_of(df, R1)
    assert df.filter(pl.col("size") == 6)["score"][0] == pytest.approx(
        1 - (1 - 0.65 * ring["member_risk"]) * (1 - 0.2 * ring["shared_identifiers"]) * (1 - 0.2 * ring["cycles"])
        * (1 - 0.2 * ring["new_accounts"]) * (1 - 0.2 * ring["speed"])
    )


def test_the_weighted_mean_is_still_there_when_the_policy_asks_for_it(fixture_run, accounts):
    w = {"member_risk": 0.35, "shared_identifiers": 0.2, "cycles": 0.15, "new_accounts": 0.15, "speed": 0.15}
    policy = Policy(community=CommunityPolicy(combine="weighted_mean", min_size=3, weights=w))
    df = score_communities(fixture_run, [set(R1), set(R2)], scores_frame(accounts, {a: 0.7 for a in R1[:4]}), policy)
    for r in df.iter_rows(named=True):
        parts = json.loads(r["score_parts"])
        assert r["score"] == pytest.approx(sum(w[p] * parts[p] for p in PARTS) / sum(w.values()))


def test_under_noisy_or_a_part_at_zero_changes_nothing_and_a_weight_over_one_is_an_error(fixture_run, accounts):
    scores = scores_frame(accounts, {a: 0.9 for a in R1})
    base = {"member_risk": 0.65, "cycles": 0.2}
    one = score_communities(fixture_run, [set(R1)], scores, Policy(community=CommunityPolicy(combine="noisy_or", weights=base)))
    parts = parts_of(one, R1)
    assert one["score"][0] == pytest.approx(1 - (1 - 0.65 * parts["member_risk"]) * (1 - 0.2 * parts["cycles"]))
    # adding a part's weight can only raise the score, and a part at 0 leaves it where it was
    more = score_communities(fixture_run, [set(R1)], scores,
                             Policy(community=CommunityPolicy(combine="noisy_or", weights={**base, "speed": 0.2})))
    assert more["score"][0] >= one["score"][0] - 1e-12
    with pytest.raises(ValueError, match=r"noisy_or needs every weight in \[0, 1\]; out of range: cycles \(1.5\)"):
        score_communities(fixture_run, [set(R1)], scores,
                          Policy(community=CommunityPolicy(combine="noisy_or", weights={"cycles": 1.5})))


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
