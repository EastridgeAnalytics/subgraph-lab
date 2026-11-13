"""Track 3c: the velocity factor (page 04) and its example (page 03).

The fixture's ordinary payments are random pairs at random times, and its two planted rings are over before the
last 14 days of its window, so on the fixture the factor has no signal to find. These tests check the contract,
the arithmetic (holdout totals against the raw payments, the score against the ratio) and the wiring. The
behaviour that matters, an account whose outflow jumps against its own history, is checked on a hand-made store.
"""
from __future__ import annotations

import ast
import inspect
import json
import runpy
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from statsforecast import StatsForecast

import lab
from examples.velocity import example, risk_velocity
from lab.adapters import to_series
from lab.common.store import rows
from lab.contracts.factors import FACTOR_RESULT_COLUMNS
from lab.contracts.recipe import Recipe
from lab.runs import Run
from lab.scoring import load_factor_file, run_factors, velocity
from tests.fixtures.graph_loader import GRAPH
from tests.fixtures.make_fixture import R1, R2

SCHEMA = {"key": pl.Utf8, "score": pl.Float64, "evidence": pl.Utf8}
EVIDENCE = {"actual_total", "forecast_total", "holdout_days", "holdout_from", "holdout_to", "model", "ratio"}
DAY0 = datetime(2026, 1, 1, tzinfo=UTC)
LAST = 119  # the last day of the hand-made window; the held-out days are 106 to 119


def hand_made(data_dir, pays: dict[str, list[tuple[int, float]]]) -> Run:
    """A store of accounts that pay `SINK`: {account: [(day number, amount), ...]}, at noon on each day."""
    run = Run.create(Recipe(), data_dir=data_dir)
    conn = run.store()
    conn.execute("UNWIND $keys AS k CREATE (:Account {account_id: k})", {"keys": [*pays, "SINK"]})
    batch = [{"src": a, "id": f"{a}-{day}", "amount": amount,
              "ts": (DAY0 + timedelta(days=day, hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")}
             for a, ps in pays.items() for day, amount in ps]
    conn.execute("UNWIND $rows AS r MATCH (s:Account {account_id: r.src}), (d:Account {account_id: 'SINK'}) "
                 "CREATE (s)-[:TX {tx_id: r.id, amount: r.amount, ts: datetime(r.ts)}]->(d)", {"rows": batch})
    return run


def steady(first: int, last: int, amount: float = 100.0) -> list[tuple[int, float]]:
    return [(day, amount) for day in range(first, last + 1, 2)]


@pytest.fixture
def story(data_dir):
    """Six accounts over 120 days. The first three have a pattern to measure the last 14 days against."""
    run = hand_made(data_dir, {
        "STEADY": steady(1, LAST),  # 100 every second day, all the way
        "SPIKE": steady(1, 105) + [(day, 1000.0) for day in range(106, LAST + 1)],  # the same, then 1,000 a day
        "QUIET": steady(1, 105),  # the same, then nothing
        "THIN": [(0, 100.0), (50, 100.0), (112, 5000.0)],  # two payments of history: no pattern
        "NEW": [(day, 500.0) for day in range(110, LAST + 1)],  # first pays inside the held-out days: no history
        "SHORT": [(day, 100.0) for day in range(95, LAST + 1)],  # a daily payer, but only 11 days of history
    })
    yield run
    run.close()


def by_key(df: pl.DataFrame) -> dict[str, dict]:
    return {r["key"]: {"score": r["score"], **json.loads(r["evidence"])} for r in df.iter_rows(named=True)}


# ---- the contract -------------------------------------------------------------------------------------------------
def test_score_returns_the_factor_columns_with_scores_in_the_unit_interval_and_json_evidence(fixture_run):
    df = velocity.score(fixture_run)
    assert tuple(df.columns) == FACTOR_RESULT_COLUMNS and df.schema == SCHEMA
    assert df.height > 50 and df["key"].n_unique() == df.height
    assert df["score"].is_between(0.0, 1.0).all() and df["score"].null_count() == 0
    accounts = set(fixture_run.table("MATCH (a:Account) RETURN a.account_id AS k")["k"])
    assert set(df["key"]) <= accounts
    for text in df["evidence"]:
        body = json.loads(text)
        assert set(body) == EVIDENCE and body["model"] == "CrostonClassic" and body["holdout_days"] == 14
        assert body["forecast_total"] >= 0 and body["actual_total"] >= 0


def test_the_score_is_the_share_of_the_recent_outflow_the_forecast_does_not_explain(fixture_run):
    got = by_key(velocity.score(fixture_run))
    assert any(g["score"] > 0 for g in got.values()) and any(g["score"] == 0 for g in got.values())
    for g in got.values():
        want = 0.0 if g["ratio"] is None or g["ratio"] <= 1 else 1 - 1 / g["ratio"]
        assert g["score"] == pytest.approx(want, abs=2e-3)


def test_the_held_out_days_are_the_last_14_of_the_window_and_the_actual_is_the_raw_outflow(fixture_run):
    """Checked against tx.csv, not against the adapter: the evidence's actual total is what the account paid out
    on the last 14 days, wherever it went."""
    tx = pl.read_csv(GRAPH / "tx.csv").with_columns(day=pl.col("ts").str.to_datetime("%Y-%m-%dT%H:%M:%SZ").dt.date())
    last = tx["day"].max()
    first_held_out = last - timedelta(days=13)
    expected = (tx.filter(pl.col("day") >= first_held_out).group_by("src").agg(total=pl.col("amount").sum())
                .to_dict(as_series=False))
    expected = dict(zip(expected["src"], expected["total"], strict=True))
    got = by_key(velocity.score(fixture_run))
    assert got
    for key, g in got.items():
        assert g["holdout_from"] == first_held_out.isoformat() and g["holdout_to"] == last.isoformat()
        assert g["actual_total"] == pytest.approx(expected.get(key, 0.0), abs=0.006)


def test_the_slice_limits_the_accounts_and_the_factor_entry_is_not_needed(fixture_run):
    keys = [f"A{i:04d}" for i in range(1, 61)]
    sliced = velocity.score(fixture_run, None, keys)
    assert 0 < sliced.height <= len(keys) and set(sliced["key"]) <= set(keys)
    shipped = next(f for f in load_factor_file(fixture_run)[1].factors if f.id == "velocity_anomaly")
    assert velocity.score(fixture_run, shipped, keys).equals(sliced)  # the entry is passed, and nothing of it is read
    assert velocity.score(fixture_run, None, []).schema == SCHEMA and velocity.score(fixture_run, None, []).is_empty()


def test_an_account_scores_the_same_on_any_slice_that_holds_it(fixture_run):
    """The held-out days are the last days of the run, not of the slice. Take accounts that were all quiet in the
    last two weeks of the run: their own window would end earlier, and the held-out days would move."""
    tx = pl.read_csv(GRAPH / "tx.csv").with_columns(day=pl.col("ts").str.to_datetime("%Y-%m-%dT%H:%M:%SZ").dt.date())
    quiet = tx.group_by("src").agg(last=pl.col("day").max()).filter(pl.col("last") <= tx["day"].max() - timedelta(days=14))
    keys = quiet["src"].to_list()
    assert len(keys) > 20
    own_window_end = to_series(fixture_run, keys=keys)["ds"].max().date()
    assert own_window_end < tx["day"].max()  # so the slice's own last day is not the run's
    everyone = velocity.score(fixture_run).filter(pl.col("key").is_in(keys))
    sliced = velocity.score(fixture_run, None, keys)
    assert 0 < sliced.height == everyone.height
    assert sliced.sort("key").equals(everyone.sort("key"))
    assert by_key(sliced)[sliced["key"][0]]["holdout_to"] == tx["day"].max().isoformat()


def test_the_same_run_gives_the_same_scores(fixture_run):
    assert velocity.score(fixture_run).equals(velocity.score(fixture_run))


# ---- one call, vectorised ----------------------------------------------------------------------------------------
def test_one_statsforecast_call_covers_every_account(fixture_run, monkeypatch):
    calls = []

    class Counting(StatsForecast):
        def forecast(self, *args, **kwargs):
            calls.append((args, kwargs))
            return super().forecast(*args, **kwargs)

    monkeypatch.setattr(velocity, "StatsForecast", Counting)
    df = velocity.score(fixture_run)
    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs["h"] == velocity.HOLDOUT_DAYS == 14
    fitted_on = kwargs["df"]
    assert fitted_on["unique_id"].n_unique() == df.height > 50  # every scored account is in that one frame
    last = fitted_on["ds"].max()
    assert last == fitted_on.filter(pl.col("unique_id") == df["key"][0])["ds"].max()  # one shared end of history
    held_out_from = datetime.fromisoformat(json.loads(df["evidence"][0])["holdout_from"])
    assert last == held_out_from - timedelta(days=1)  # the model never sees a held-out day


# ---- the behaviour: a jump against the account's own history -------------------------------------------------------
def test_an_account_whose_outflow_jumps_scores_higher_than_a_steady_one(story):
    got = by_key(velocity.score(story))
    cutoff = (DAY0 + timedelta(days=LAST - 13)).date().isoformat()
    assert got["SPIKE"]["holdout_from"] == cutoff and got["SPIKE"]["holdout_to"] == (DAY0 + timedelta(days=LAST)).date().isoformat()
    assert got["SPIKE"]["actual_total"] == 14_000.0
    assert 600 < got["SPIKE"]["forecast_total"] < 800  # 100 every second day is about 50 a day, for 14 days
    assert got["SPIKE"]["ratio"] == pytest.approx(14_000 / got["SPIKE"]["forecast_total"], abs=0.01)
    assert got["SPIKE"]["score"] > 0.9
    assert got["STEADY"]["actual_total"] == 700.0 and got["STEADY"]["score"] < 0.1
    assert got["SPIKE"]["score"] > got["STEADY"]["score"] + 0.8
    # silence is not an anomaly: an account that stopped scores 0, with its forecast still in the evidence
    assert got["QUIET"]["score"] == 0.0 and got["QUIET"]["actual_total"] == 0.0 and got["QUIET"]["forecast_total"] > 0


def test_too_little_history_gets_no_row(story):
    got = by_key(velocity.score(story))
    assert set(got) == {"STEADY", "SPIKE", "QUIET"}
    assert "THIN" not in got  # fewer than MIN_ACTIVE_DAYS days with a payment before the held-out days
    assert "SHORT" not in got  # a payment every day, but fewer than MIN_HISTORY_DAYS days of history
    assert "NEW" not in got  # its first payment is inside the held-out days: no forecast to measure it against
    assert velocity.MIN_ACTIVE_DAYS == 3 and velocity.MIN_HISTORY_DAYS == 28


def test_the_history_starts_at_the_accounts_first_payment_not_at_the_window(data_dir):
    """Croston reads leading zeros as one long gap, so a newcomer would forecast near nothing and every payment would
    look like a jump. Two accounts with the same pattern, one of them 'opened' 60 days later, forecast alike."""
    run = hand_made(data_dir, {"OLD": steady(1, LAST), "LATER": steady(61, LAST)})
    try:
        got = by_key(velocity.score(run))
        assert set(got) == {"OLD", "LATER"}
        assert got["LATER"]["forecast_total"] == pytest.approx(got["OLD"]["forecast_total"], rel=0.05)
        assert got["LATER"]["score"] < 0.1
    finally:
        run.close()


def test_run_factors_reports_the_velocity_factor_as_ran(fixture_run):
    fr = run_factors(fixture_run)
    status = {s.id: s for s in fr.status}["velocity_anomaly"]
    assert status.status == "ran" and status.reason is None
    mine = fr.results.filter(pl.col("factor_id") == "velocity_anomaly")
    assert status.accounts == mine.height == velocity.score(fixture_run).height > 50
    assert mine["score"].is_between(0.0, 1.0).all() and all(json.loads(e)["holdout_days"] == 14 for e in mine["evidence"])
    assert "velocity_anomaly" not in (fr.note() or "")


def test_the_planted_rings_have_no_velocity_row_because_they_are_new(fixture_run):
    """Not a claim about quality: the rings' accounts opened in mid September and paid from 1 October, so before the
    last 14 days of the window (16 to 29 October) they have under 28 days of history. The report says so."""
    got = set(velocity.score(fixture_run)["key"])
    assert not (got & set(R1)) and not (got & set(R2))


# ---- the example ---------------------------------------------------------------------------------------------------
def test_the_velocity_example_is_importable_and_under_100_lines_of_code():
    import examples.velocity as pkg

    assert risk_velocity is pkg.risk_velocity is example.risk_velocity
    source = inspect.getsource(example)
    docstrings = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef) and ast.get_docstring(node, clean=False):
            first = node.body[0]
            docstrings.update(range(first.lineno, first.end_lineno + 1))
    code = [n for n, line in enumerate(source.splitlines(), 1)
            if line.strip() and not line.strip().startswith("#") and n not in docstrings]
    assert len(code) < 100, f"{len(code)} lines of code"


def test_the_main_block_stages_risk_velocity_and_previews_the_push(fixture_run, monkeypatch, capsys):
    """`python -m examples.velocity.example`: `score` on the slice, `run.stage(df)`, `run.push()` (a preview)."""
    run = fixture_run
    keys = [f"A{i:04d}" for i in range(1, 81)]
    (run.paths.artifacts / "slice.json").write_text(
        json.dumps({"estimate": {"over_cap": False}, "keys": keys}), encoding="utf-8")
    monkeypatch.setattr(lab, "run", lambda *a, **k: run)
    monkeypatch.setattr(Run, "push", lambda self, confirm=False: f"PREVIEW confirm={confirm}")  # the real one needs Aura
    runpy.run_path(example.__file__, run_name="__main__")
    assert capsys.readouterr().out.strip().endswith("PREVIEW confirm=False")
    scored = velocity.score(run, keys=keys)
    assert 0 < scored.height <= len(keys)
    query = "MATCH (a:Account) WHERE a.risk_velocity IS NOT NULL RETURN a.account_id AS k, a.risk_velocity AS v"
    staged = {r["k"]: r["v"] for r in rows(run.push_store(), query)}
    assert staged == pytest.approx(dict(zip(scored["key"], scored["score"], strict=True)))
