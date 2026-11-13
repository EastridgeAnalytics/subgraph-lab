"""Track 2a: the search slice. Hybrid search, rank fusion, hops, the estimate, the cap and the saved slice."""
from __future__ import annotations

import json

import pytest

from lab.common.store import estimate_memory
from lab.contracts.api import SliceEstimate
from lab.contracts.manifest import RunManifest, StepStatus
from lab.contracts.recipe import Recipe, SearchSpec
from lab.roundtrip.indexes import create_indexes
from lab.settings import get_settings, reload_settings
from lab.slice.search import FirstPassNotRun, SliceOverCap, current_slice, search_slice, terms
from tests import test_first_pass as shared
from tests.test_first_pass import R1, R2

# One run with its first pass done, built once for the whole session (defined in test_first_pass.py).
first_passed, run_of = shared.first_passed, shared.run_of

DEMO = "money in and out within a day across new accounts"


def small_cap(monkeypatch, cap: int):
    monkeypatch.setenv("LAB_SLICE_CAP", str(cap))
    reload_settings()


@pytest.fixture
def restore_settings(monkeypatch):
    yield monkeypatch
    monkeypatch.undo()
    reload_settings()


def test_search_before_the_first_pass_raises(fixture_run):
    create_indexes(fixture_run.store())
    with pytest.raises(FirstPassNotRun, match="first pass"):
        search_slice(fixture_run, SearchSpec(text=DEMO))
    with pytest.raises(FirstPassNotRun):
        fixture_run.search(SearchSpec(account="A0011"))
    assert current_slice(fixture_run) is None  # nothing saved, so the whole store


def test_the_demo_text_puts_the_planted_ring_at_the_top(run_of):
    est = search_slice(run_of, SearchSpec(text=DEMO))
    assert isinstance(est, SliceEstimate) and est.mode == "search"
    top10 = [h.key for h in est.hits[:10]]
    assert set(R1) <= set(top10), top10
    assert est.hits[0].key in R1


def test_text_search_keeps_to_k_hits_ranked_by_fused_score(run_of):
    est = search_slice(run_of, SearchSpec(text=DEMO, k=7))
    assert len(est.hits) == 7
    assert [h.rrf for h in est.hits] == sorted((h.rrf for h in est.hits), reverse=True)
    assert len({h.key for h in est.hits}) == 7
    default = search_slice(run_of, SearchSpec(text=DEMO))
    assert len(default.hits) == get_settings().search_k == 50


def test_rank_fusion_is_one_over_sixty_plus_rank_summed_across_the_lists(run_of):
    k = 20
    est = search_slice(run_of, SearchSpec(text=DEMO, k=k))
    one = {1 / (60 + r) for r in range(k)}
    for h in est.hits:
        assert 1 <= len(h.found_by) <= 2 and set(h.found_by) <= {"vector", "fulltext"}
        if len(h.found_by) == 1:
            assert any(h.rrf == pytest.approx(x) for x in one), h
        else:
            assert any(h.rrf == pytest.approx(x + y) for x in one for y in one), h
    both = [h for h in est.hits if len(h.found_by) == 2]
    assert both and est.hits[0].found_by == ["vector", "fulltext"]


def test_a_full_text_only_hit_is_marked_as_such(run_of):
    """Ten hits asked for from a query whose vector list disagrees with its word list: some hit has one source."""
    est = search_slice(run_of, SearchSpec(text="shares a device", k=10))
    assert {tuple(h.found_by) for h in est.hits} >= {("vector", "fulltext")}
    assert any(h.found_by in (["fulltext"], ["vector"]) for h in est.hits)


def test_search_by_account_returns_its_ring_mates(run_of):
    est = search_slice(run_of, SearchSpec(account="A0011"))
    keys = [h.key for h in est.hits]
    assert keys[0] == "A0011"
    assert set(R1) <= set(keys)
    assert len(set(keys[:10]) & set(R1)) >= 5  # the ring is the account's nearest neighbours, by words and by walks
    assert not set(keys[:10]) & set(R2)


def test_text_and_account_together_use_both(run_of):
    est = search_slice(run_of, SearchSpec(text="payments just under 10,000", account="A0011"))
    keys = [h.key for h in est.hits[:16]]
    assert len(set(keys) & set(R1)) >= 4 and len(set(keys) & set(R2)) >= 4


def test_the_hops_expansion_counts_what_the_adapters_will_build(run_of):
    """nodes = accounts in reach + their identifier nodes; relationships = payments between them + identifier links."""
    sizes = {}
    for hops in (0, 1, 2):
        est = search_slice(run_of, SearchSpec(account="A0011", k=1, hops=hops))
        keys = current_slice(run_of)
        assert est.hops == hops and [h.key for h in est.hits] == ["A0011"]
        ids = run_of.table(
            "MATCH (a:Account)-[r:USES_DEVICE|HAS_EMAIL|HAS_ADDRESS]->(x) WHERE a.account_id IN $k "
            "RETURN count(r) AS links, count(DISTINCT x) AS ids",
            {"k": keys},
        )
        pay = run_of.table(
            "MATCH (s:Account)-[t:TX]->(d:Account) WHERE s.account_id IN $k AND d.account_id IN $k RETURN count(t) AS n",
            {"k": keys},
        )["n"][0]
        assert est.nodes == len(keys) + ids["ids"][0]
        assert est.relationships == ids["links"][0] + pay
        assert est.memory_bytes == estimate_memory(est.nodes, est.relationships)
        sizes[hops] = (keys, est.nodes)
    assert sizes[0][0] == ["A0011"]
    one, two = set(sizes[1][0]), set(sizes[2][0])
    assert {"A0011", "A0012", "A0016"} <= one  # the ring's cycle: A0011 pays A0012 and is paid by A0016
    assert set(R1) <= one  # and the ring shares a device: an identifier is one step too (decision 41)
    assert one < two
    assert sizes[0][1] < sizes[1][1] < sizes[2][1]


ANY_ID = "USES_DEVICE|HAS_EMAIL|HAS_ADDRESS"


def payment_neighbours(run, keys) -> set[str]:
    df = run.table("MATCH (a:Account)-[:TX]-(b:Account) WHERE a.account_id IN $k RETURN DISTINCT b.account_id AS b",
                   {"k": sorted(keys)})
    return set(df["b"].to_list())


def identifier_mates(run, keys) -> set[str]:
    df = run.table(f"MATCH (a:Account)-[:{ANY_ID}]->(i)<-[:{ANY_ID}]-(b:Account) WHERE a.account_id IN $k "
                   "RETURN DISTINCT b.account_id AS b", {"k": sorted(keys)})
    return set(df["b"].to_list())


def slice_of(run, account: str, hops: int) -> set[str]:
    search_slice(run, SearchSpec(account=account, k=1, hops=hops))
    return set(current_slice(run))


def test_one_hop_is_one_account_step_by_a_payment_or_a_shared_identifier(run_of):
    """Decision 41: h 1 from a ring member is its payment counterparties (either way) and the accounts that share an
    identifier with it, and nothing two steps away."""
    one = slice_of(run_of, "A0011", 1)
    payers = payment_neighbours(run_of, {"A0011"})
    mates = identifier_mates(run_of, {"A0011"}) - {"A0011"}
    assert set(R1) - {"A0011"} <= mates  # the ring shares a device and an address
    assert {"A0012", "A0016"} <= payers  # the ring's cycle, one step either way
    assert one == {"A0011"} | payers | mates
    two_away = (payment_neighbours(run_of, one) | identifier_mates(run_of, one)) - one
    assert two_away  # the fixture has accounts two steps out, so the check below can fail
    assert not one & two_away
    assert slice_of(run_of, "A0011", 2) == one | two_away  # h 2 is one more step from everything h 1 reached


def test_an_identifier_shared_by_more_than_shared_max_accounts_is_no_step(run_of, restore_settings):
    """Six accounts share the ring's device and address. At shared_max 6 they are a step; at 5 they are not."""
    payers = payment_neighbours(run_of, {"A0011"})
    ring_only_by_identifier = set(R1) - {"A0011"} - payers
    assert ring_only_by_identifier  # some ring mate is not a payment counterparty, or the test proves nothing
    restore_settings.setenv("LAB_SHARED_MAX", "6")
    reload_settings()
    assert set(R1) <= slice_of(run_of, "A0011", 1)
    restore_settings.setenv("LAB_SHARED_MAX", "5")
    reload_settings()
    crowded = slice_of(run_of, "A0011", 1)
    assert crowded == {"A0011"} | payers  # its other identifiers (an email of its own) lead nowhere
    assert not ring_only_by_identifier & crowded
    # the ring is still reached, a step at a time, by the payments that go round it
    assert set(R1) <= slice_of(run_of, "A0011", 5)


def test_h_zero_is_the_hits_and_h_two_reaches_further(run_of):
    zero, one, two = (slice_of(run_of, "A0101", h) for h in (0, 1, 2))
    assert zero == {"A0101"} and zero < one < two


def test_the_hit_count_is_the_hits_returned(run_of):
    """The manifest's `hits`, the estimate's hit list and the saved slice say the same number, and no key is in it
    twice, however the two lists overlap (wave 2 saw 19 where the manifest said 20)."""
    for spec in (SearchSpec(text=DEMO, k=20), SearchSpec(account="A0011", k=20),
                 SearchSpec(text="payments just under 10,000", account="A0011", k=20)):
        est = search_slice(run_of, spec)
        keys = [h.key for h in est.hits]
        step = RunManifest.load(run_of.paths.manifest).step("slice")
        assert len(keys) == len(set(keys)) == 20 == step.counts["hits"]
        assert len(json.loads((run_of.paths.artifacts / "slice.json").read_text())["estimate"]["hits"]) == 20
        assert est.note is None


def test_fewer_hits_than_k_is_said(run_of):
    """Only 200 accounts exist, so k of 300 cannot be met: the hit list is shorter and the note says why."""
    est = search_slice(run_of, SearchSpec(text=DEMO, k=300, hops=0))
    assert len(est.hits) == 200 and len({h.key for h in est.hits}) == 200
    assert "200 accounts, fewer than the k of 300" in est.note
    assert RunManifest.load(run_of.paths.manifest).step("slice").counts["hits"] == 200


def test_over_cap_is_flagged_saved_without_keys_and_refused_by_current_slice(run_of, restore_settings):
    small_cap(restore_settings, 10)
    est = search_slice(run_of, SearchSpec(text=DEMO))
    assert est.over_cap and est.cap == 10 and est.nodes > 10
    assert "cap" in (est.note or "")
    assert RunManifest.load(run_of.paths.manifest).step("slice").status is StepStatus.FAILED
    with pytest.raises(SliceOverCap, match="lower k or hops"):
        current_slice(run_of)
    assert json.loads((run_of.paths.artifacts / "slice.json").read_text())["keys"] == []
    restore_settings.undo()
    reload_settings()
    again = search_slice(run_of, SearchSpec(text=DEMO, k=5, hops=0))
    assert not again.over_cap and again.cap == get_settings().slice_cap
    assert len(current_slice(run_of)) >= 5  # usable again: the keys are back


def test_the_expansion_stops_early_past_the_cap(run_of, restore_settings):
    small_cap(restore_settings, 30)
    est = search_slice(run_of, SearchSpec(account="A0011", hops=3))
    assert est.over_cap and "stopped expanding" in est.note
    assert est.nodes < 777  # not the whole store: it stopped at the round that passed the cap


def test_current_slice_round_trip(run_of):
    est = search_slice(run_of, SearchSpec(account="A0101", k=8, hops=1))
    keys = current_slice(run_of)
    assert keys == sorted(keys) and len(keys) == len(set(keys))
    assert {h.key for h in est.hits} <= set(keys)
    saved = json.loads((run_of.paths.artifacts / "slice.json").read_text())
    assert saved["keys"] == keys and saved["k"] == 8 and saved["hops"] == 1
    assert saved["search"]["account"] == "A0101"
    assert SliceEstimate.model_validate(saved["estimate"]) == est
    # the models pass it straight to an adapter
    from lab.adapters import to_pyg

    assert to_pyg(run_of, keys=keys).account_index.keys() == set(keys)


def test_the_manifest_records_the_slice_step(run_of):
    est = search_slice(run_of, SearchSpec(account="A0011", k=5, hops=1))
    step = RunManifest.load(run_of.paths.manifest).step("slice")
    assert step.status is StepStatus.DONE and step.note is None
    assert step.counts["hits"] == 5 and step.counts["hops"] == 1
    assert step.counts["nodes"] == est.nodes and step.counts["relationships"] == est.relationships
    assert step.counts["accounts"] == len(current_slice(run_of))


def test_k_and_hops_default_to_the_settings(run_of, restore_settings):
    restore_settings.setenv("LAB_SEARCH_K", "3")
    restore_settings.setenv("LAB_SEARCH_HOPS", "0")
    reload_settings()
    est = run_of.search(SearchSpec(text=DEMO))
    assert len(est.hits) == 3 and est.hops == 0


def test_the_recipe_search_is_what_run_search_uses(run_of):
    saved = run_of.recipe
    run_of.recipe = Recipe(search=SearchSpec(account="A0012", k=4, hops=0))
    try:
        hits = run_of.search().hits
    finally:
        run_of.recipe = saved
    assert hits[0].key == "A0012" and len(hits) == 4


def test_words_that_match_nothing_make_an_empty_slice(run_of):
    est = search_slice(run_of, SearchSpec(text="zzzzqqqq"))
    assert est.hits == [] and est.nodes == 0 and est.relationships == 0 and est.memory_bytes == 0
    assert "empty" in est.note
    assert current_slice(run_of) == []  # an empty slice, not the whole store


def test_bad_searches_say_what_is_wrong(run_of):
    with pytest.raises(ValueError, match="no account 'A9999'"):
        search_slice(run_of, SearchSpec(account="A9999"))
    with pytest.raises(ValueError, match="no words"):
        search_slice(run_of, SearchSpec(text="!!! ---"))
    with pytest.raises(ValueError, match="k must be"):
        search_slice(run_of, SearchSpec(text=DEMO, k=0))
    assert RunManifest.load(run_of.paths.manifest).step("first_pass").status is StepStatus.DONE


def test_words_cannot_be_search_operators():
    assert terms('Money AND (in OR out) NOT "a day" -x* a:b') == [
        "money", "and", "in", "or", "out", "not", "a", "day", "x", "b"]
    assert terms("   ") == []


def test_the_run_search_method_is_the_facade(run_of):
    assert [h.key for h in run_of.search(SearchSpec(account="A0011", k=3)).hits][0] == "A0011"
