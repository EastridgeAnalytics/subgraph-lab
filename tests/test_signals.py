"""Track 5b: the shared signals (`lab/scoring/signals.py`): new accounts, money in and out within a day, payment cycles.

The cycle tests are on payments made by hand, so every order of time is known. The in-and-out tests run on the fixture
graph and compare the signal with the factor: they must be the same accounts, because they are one query.
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from lab.adapters import reads
from lab.scoring import FactorRun, run_factors
from lab.scoring.signals import (
    IN_AND_OUT_FACTOR,
    account_opened,
    cycles_by_group,
    fast_money_links,
    in_and_out_within_a_day,
    new_accounts,
    payment_cycles,
)
from lab.settings import reload_settings
from tests.fixtures.make_fixture import L1, R1, R2

REPO = Path(__file__).resolve().parent.parent
DAY0 = datetime(2026, 6, 1)


@pytest.fixture
def restore_settings(monkeypatch):
    yield monkeypatch
    monkeypatch.undo()
    reload_settings()


def pays(*edges: tuple[str, str, float]) -> pl.DataFrame:
    """Payments `(src, dst, day)`: `day` days (or part of one) after 1 June."""
    return pl.DataFrame(
        {"src": [e[0] for e in edges], "dst": [e[1] for e in edges], "ts": [DAY0 + timedelta(days=e[2]) for e in edges]},
        schema={"src": pl.Utf8, "dst": pl.Utf8, "ts": pl.Datetime("us")},
    )


# ---- cycles ----------------------------------------------------------------------------------------------------
def test_a_loop_whose_payments_run_forward_in_time_is_a_cycle():
    assert payment_cycles(pays(("a", "b", 1), ("b", "c", 2), ("c", "a", 3))) == [("a", "b", "c")]


def test_a_loop_whose_payments_run_backwards_in_time_is_not():
    assert payment_cycles(pays(("a", "b", 3), ("b", "c", 2), ("c", "a", 1))) == []  # the same three accounts, the same edges
    assert payment_cycles(pays(("a", "b", 2), ("b", "c", 1), ("c", "a", 3))) == []  # one payment out of turn is enough


def test_a_cycle_begins_where_its_earliest_payment_is_not_at_its_lowest_key():
    got = payment_cycles(pays(("b", "c", 1), ("c", "a", 2), ("a", "b", 3)))  # starts at b, and comes back to b
    assert got == [("a", "b", "c")]  # told from the lowest key, so the same cycle is the same tuple wherever it began


def test_a_later_payment_on_an_edge_can_be_the_one_that_fits():
    # a pays b on days 1 and 5; b pays c on day 3; c pays a on day 4: the day-1 payment starts it, and day 5 is not needed
    assert payment_cycles(pays(("a", "b", 1), ("a", "b", 5), ("b", "c", 3), ("c", "a", 4))) == [("a", "b", "c")]
    # only a day-5 payment out of a comes after c's: still a cycle, found from c
    assert payment_cycles(pays(("c", "a", 1), ("a", "b", 2), ("b", "c", 3))) == [("a", "b", "c")]


def test_two_payments_at_the_same_instant_are_not_one_after_the_other():
    assert payment_cycles(pays(("a", "b", 1), ("b", "c", 1), ("c", "a", 1))) == []
    assert payment_cycles(pays(("a", "b", 1), ("b", "a", 1))) == []


def test_two_accounts_that_pay_each_other_are_a_cycle_of_two_however_often():
    assert payment_cycles(pays(("a", "b", 1), ("b", "a", 2))) == [("a", "b")]
    assert payment_cycles(pays(("b", "a", 1), ("a", "b", 2))) == [("a", "b")]  # whichever paid first
    many = pays(*[("a", "b", i) for i in range(50)], *[("b", "a", i + 0.5) for i in range(50)])
    assert payment_cycles(many) == [("a", "b")]  # a hundred payments, one cycle


def test_money_that_moves_on_is_not_a_cycle():
    assert payment_cycles(pays(("a", "b", 1), ("b", "c", 2), ("c", "d", 3))) == []
    assert payment_cycles(pays(("s", "s", 1))) == []  # an account paying itself
    assert payment_cycles(pays()) == [] and payment_cycles(pl.DataFrame(schema={"src": pl.Utf8, "dst": pl.Utf8, "ts": pl.Datetime("us")})) == []


def test_loops_that_share_an_account_are_separate_cycles_and_none_repeats_an_account():
    figure_eight = pays(("a", "b", 1), ("b", "a", 2), ("a", "c", 3), ("c", "a", 4))
    assert payment_cycles(figure_eight) == [("a", "b"), ("a", "c")]  # not a loop of a, b, a, c: a is not visited twice


def test_the_length_bound_is_a_setting(restore_settings):
    ring = [f"r{i}" for i in range(7)]
    seven = pays(*[(ring[i], ring[(i + 1) % 7], i + 1) for i in range(7)])
    assert payment_cycles(seven) == []  # LAB_CYCLE_MAX_LENGTH is 6: seven accounts is one too many
    assert payment_cycles(seven, max_length=7) == [tuple(ring)]
    restore_settings.setenv("LAB_CYCLE_MAX_LENGTH", "7")
    reload_settings()
    assert payment_cycles(seven) == [tuple(ring)]
    restore_settings.setenv("LAB_CYCLE_MAX_LENGTH", "1")
    reload_settings()
    assert payment_cycles(pays(("a", "b", 1), ("b", "a", 2))) == []  # a loop is at least two accounts


def test_the_count_is_capped_by_a_setting(restore_settings):
    twelve = pays(*[(f"p{i:02d}", f"q{i:02d}", 1) for i in range(12)], *[(f"q{i:02d}", f"p{i:02d}", 2) for i in range(12)])
    assert len(payment_cycles(twelve)) == 12
    assert len(payment_cycles(twelve, cap=5)) == 5
    restore_settings.setenv("LAB_CYCLE_COUNT_CAP", "4")
    reload_settings()
    capped = payment_cycles(twelve)
    assert len(capped) == 4 and capped == payment_cycles(twelve)  # the same four every time


def test_the_cycles_do_not_depend_on_the_order_the_payments_arrive_in():
    edges = [("a", "b", 1), ("b", "c", 2), ("c", "a", 3), ("c", "d", 4), ("d", "b", 5), ("b", "d", 6), ("a", "d", 7), ("d", "a", 8)]
    forward, back = payment_cycles(pays(*edges)), payment_cycles(pays(*reversed(edges)))
    assert forward == back and ("a", "b", "c") in forward and ("a", "d") in forward


def test_cycles_are_found_inside_each_group_and_only_there():
    pay = pays(("a", "b", 1), ("b", "a", 2), ("c", "d", 1), ("d", "e", 2), ("e", "c", 3), ("a", "c", 4), ("c", "a", 5))
    group_of = {"a": 0, "b": 0, "c": 1, "d": 1, "e": 1}
    assert cycles_by_group(pay, group_of) == {0: [("a", "b")], 1: [("c", "d", "e")]}  # a and c pay each other across groups: no
    assert cycles_by_group(pay, {}) == {} and cycles_by_group(pays(), group_of) == {}
    assert cycles_by_group(pay, {"a": 0, "b": 1}) == {}  # a and b in different groups


# ---- new accounts ----------------------------------------------------------------------------------------------
def test_an_account_is_new_when_opened_within_the_days_of_the_newest_payment(restore_settings):
    opened = pl.DataFrame({
        "key": ["old", "edge", "new", "none"],
        "opened": [datetime(2026, 1, 1), datetime(2026, 8, 2), datetime(2026, 9, 20), None],
    }, schema={"key": pl.Utf8, "opened": pl.Datetime("us")})
    pay = pl.DataFrame({"ts": [datetime(2026, 9, 1), datetime(2026, 10, 1)]}, schema={"ts": pl.Datetime("us")})
    assert new_accounts(opened, pay) == {"edge", "new"}  # 60 days before 1 October is 2 August: that day counts
    restore_settings.setenv("LAB_NEW_ACCOUNT_DAYS", "20")
    reload_settings()
    assert new_accounts(opened, pay) == {"new"}
    assert new_accounts(opened, pay.head(0)) == set()  # no payments, no newest payment, nothing is new


def test_the_fixtures_new_accounts_are_the_rings_and_the_family_is_not(fixture_run):
    opened = account_opened(fixture_run)
    assert opened.height == 200 and opened.schema == {"key": pl.Utf8, "opened": pl.Datetime("us")}
    pay = fixture_run.table("MATCH (s:Account)-[t:TX]->(d:Account) RETURN t.ts AS ts")
    pay = pay.with_columns(ts=reads.utc_naive(pay["ts"]))
    new = new_accounts(opened, pay)
    assert set(R1) <= new and set(R2) <= new and not new & set(L1)
    assert account_opened(fixture_run, []).height == 0
    assert account_opened(fixture_run, R1[:2])["key"].to_list() == sorted(R1[:2])


# ---- money in and out within a day: the factor's own definition ---------------------------------------------------------
def factor_rows(run, keys=None) -> pl.DataFrame:
    return run_factors(run, keys).results.filter(pl.col("factor_id") == IN_AND_OUT_FACTOR)


def test_the_accounts_are_the_factors_accounts_and_the_counts_are_its_values(fixture_run):
    got = in_and_out_within_a_day(fixture_run)
    fired = factor_rows(fixture_run)
    assert got.schema == {"key": pl.Utf8, "count": pl.Int64} and got.height > 0
    assert got["key"].to_list() == sorted(fired["key"].to_list())  # the same accounts, nothing more or less
    values = {k: json.loads(e)["value"] for k, e in zip(fired["key"], fired["evidence"], strict=True)}
    assert dict(zip(got["key"], got["count"], strict=True)) == values
    assert set(R1) <= set(got["key"]) and not set(got["key"]) & set(R2 + L1)  # the ring passes money on; the others do not
    assert all(c >= 2 for c in got["count"])  # the factor's threshold is 2: one match is not enough


def test_a_factor_run_hands_over_its_rows_and_a_slice_limits_the_accounts(fixture_run):
    factor_run = run_factors(fixture_run, None)
    assert in_and_out_within_a_day(fixture_run, None, factor_run).equals(in_and_out_within_a_day(fixture_run))
    assert set(in_and_out_within_a_day(fixture_run, R1[:2])["key"]) == set(R1[:2])
    assert in_and_out_within_a_day(fixture_run, []).height == 0
    nobody = FactorRun(path=factor_run.path, file=factor_run.file, results=factor_run.results.head(0), status=[], keys=None)
    assert in_and_out_within_a_day(fixture_run, None, nobody).height == 0  # a factor that did not run counts nobody


def test_the_definition_is_in_the_factors_query_not_in_the_signal(fixture_run, tmp_path, restore_settings):
    """Narrow the factor's own query from 24 hours to one second and both the factor and the signal lose the ring."""
    folder = tmp_path / "factors"
    shutil.copytree(REPO / "factors", folder)
    query = folder / "in_and_out_within_a_day.cypher"
    text = query.read_text(encoding="utf-8")
    assert "<= 86400" in text
    query.write_text(text.replace("<= 86400", "<= 1"), encoding="utf-8")
    restore_settings.setenv("LAB_FACTORS_FILE", str(folder / "factors.yaml"))
    reload_settings()
    assert factor_rows(fixture_run).height == 0
    assert in_and_out_within_a_day(fixture_run).height == 0


def test_a_factor_file_without_the_factor_counts_nobody_and_says_so(fixture_run, tmp_path, restore_settings, caplog):
    folder = tmp_path / "factors"
    shutil.copytree(REPO / "factors", folder)
    text = (folder / "factors.yaml").read_text(encoding="utf-8")
    start = text.index("- id: in_and_out_within_a_day")
    stop = text.index("- id: just_under_threshold")
    (folder / "factors.yaml").write_text(text[:start] + text[stop:], encoding="utf-8")
    restore_settings.setenv("LAB_FACTORS_FILE", str(folder / "factors.yaml"))
    reload_settings()
    with caplog.at_level(logging.WARNING):
        got = in_and_out_within_a_day(fixture_run)
    assert got.height == 0 and got.schema == {"key": pl.Utf8, "count": pl.Int64}
    assert "in_and_out_within_a_day" in caplog.text


# ---- fast money (decision 44) --------------------------------------------------------------------------------------
def test_fast_money_links_join_only_accounts_the_factor_fired_on_by_the_payments_it_matched(fixture_run):
    fired = set(in_and_out_within_a_day(fixture_run)["key"])
    links = fast_money_links(fixture_run)
    assert links.columns == ["u", "v"] and links.height > 0
    linked = set(links["u"]) | set(links["v"])
    assert linked <= fired and set(R1) <= linked  # the ring forwards money within a day, hop by hop
    assert (links["u"] < links["v"]).all() and links.unique().height == links.height
    pay = reads.payments(fixture_run, sorted(linked))
    pairs = {tuple(sorted(p)) for p in pay.select("src", "dst").iter_rows()}
    assert {tuple(r) for r in links.iter_rows()} <= pairs  # every link is a payment between the two


def test_fast_money_links_follow_the_factor_run_and_the_scope(fixture_run):
    factor_run = run_factors(fixture_run, None)
    assert fast_money_links(fixture_run, None, factor_run).equals(fast_money_links(fixture_run))
    inside = fast_money_links(fixture_run, list(R1))
    assert set(inside["u"]) | set(inside["v"]) <= set(R1)
    assert fast_money_links(fixture_run, []).height == 0
    nobody = FactorRun(path=factor_run.path, file=factor_run.file, results=factor_run.results.clear(), status=[], keys=None)
    assert fast_money_links(fixture_run, None, nobody).height == 0
