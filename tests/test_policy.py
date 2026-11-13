"""Track 2b: the policy arithmetic on hand-made factor frames, and the alerts it decides."""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from lab.common import alert_key
from lab.contracts.factors import FactorFile, Policy
from lab.contracts.graph import GraphMapping
from lab.contracts.recipe import Recipe
from lab.runs import Run
from lab.scoring import FactorRun, FactorStatus, account_scores, alerts, load_policy
from lab.scoring.factors import RESULT_SCHEMA
from lab.settings import reload_settings
from tests.fixtures.make_fixture import R1

REPO = Path(__file__).resolve().parent.parent
FACTORS = [
    {"id": "dev", "name": "Shared device", "kind": "cypher", "query": "dev.cypher", "weight": 0.30, "threshold": 5},
    {"id": "vel", "name": "Velocity", "kind": "python", "function": "m:f", "weight": 0.20},
    {"id": "tgn", "name": "TGN", "kind": "model", "source": "examples/tgn", "weight": 0.50},
]
ACCOUNTS = ["X", "Y", "Z", "W"]


def factor_run(rows: list[tuple], factors: list[dict] | None = None, keys: list[str] | None = None,
               skipped: tuple[str, ...] = ()) -> FactorRun:
    """rows are (factor_id, key, score, evidence)."""
    ff = FactorFile(version="t", factors=factors if factors is not None else FACTORS)
    status = [FactorStatus(f.id, f.kind, "skipped" if f.id in skipped else "ran") for f in ff.factors]
    return FactorRun(path=Path("factors.yaml"), file=ff, results=pl.DataFrame(rows, schema=RESULT_SCHEMA, orient="row"),
                     status=status, keys=keys)


@pytest.fixture
def run(data_dir) -> Run:
    """A run with no store: these tests name their accounts."""
    r = Run.create(Recipe(), data_dir=data_dir)
    yield r
    r.close()


def by_key(df: pl.DataFrame, key: str = "account_id") -> dict[str, dict]:
    return {r[key]: r for r in df.iter_rows(named=True)}


ROWS = [
    ("dev", "X", 1.0, '{"evidence_ids":["D1"],"value":6}'), ("vel", "X", 0.5, "{}"), ("tgn", "X", 0.8, "{}"),
    ("dev", "Y", 1.0, '{"evidence_ids":["D2"],"value":5}'),
    ("tgn", "W", 1.0, "{}"),
]


# ---- the arithmetic ----------------------------------------------------------------------------------------------
def test_weighted_means_per_side_and_overall(run):
    df = account_scores(run, factor_run(ROWS), Policy(), accounts=ACCOUNTS)
    s = by_key(df)
    # X: rules (0.3*1 + 0.2*0.5) / 0.5 = 0.8, model 0.8, overall (0.4 + 0.4) / 1.0
    assert s["X"]["risk_score_rules"] == pytest.approx(0.8)
    assert s["X"]["risk_score_model"] == pytest.approx(0.8)
    assert s["X"]["risk_score"] == pytest.approx(0.8)
    # the overall score is the two sides' means weighted by each side's total weight
    for r in s.values():
        assert r["risk_score"] == pytest.approx((0.5 * r["risk_score_rules"] + 0.5 * r["risk_score_model"]) / 1.0)


def test_a_missing_factor_counts_zero(run):
    s = by_key(account_scores(run, factor_run(ROWS), Policy(), accounts=ACCOUNTS))
    # Y has only the device: rules 0.3 / 0.5 = 0.6, the model side is missing so 0, overall 0.3 / 1.0
    assert s["Y"]["risk_score_rules"] == pytest.approx(0.6)
    assert s["Y"]["risk_score_model"] == 0.0
    assert s["Y"]["risk_score"] == pytest.approx(0.3)
    # W has only the model: rules 0, model 1.0, overall 0.5 / 1.0
    assert (s["W"]["risk_score_rules"], s["W"]["risk_score_model"]) == (0.0, pytest.approx(1.0))
    assert s["W"]["risk_score"] == pytest.approx(0.5)
    # Z has nothing, and still gets its row
    assert (s["Z"]["risk_score"], s["Z"]["risk_score_rules"], s["Z"]["risk_score_model"]) == (0.0, 0.0, 0.0)


def test_a_skipped_factor_keeps_its_weight_in_the_divisor(run):
    """tgn is skipped, as it is when no example has written it: X's rules are 0.8, its overall score is 0.4."""
    rows = [r for r in ROWS if r[0] != "tgn"]
    s = by_key(account_scores(run, factor_run(rows, skipped=("tgn",)), Policy(), accounts=ACCOUNTS))
    assert s["X"]["risk_score_rules"] == pytest.approx(0.8)
    assert s["X"]["risk_score_model"] == 0.0
    assert s["X"]["risk_score"] == pytest.approx(0.4)


def test_risk_factors_lists_every_factor_in_file_order(run):
    s = by_key(account_scores(run, factor_run(ROWS), Policy(), accounts=ACCOUNTS))
    assert json.loads(s["X"]["risk_factors"]) == [
        {"id": "dev", "score": 1.0, "weight": 0.3}, {"id": "vel", "score": 0.5, "weight": 0.2},
        {"id": "tgn", "score": 0.8, "weight": 0.5}]
    assert [f["score"] for f in json.loads(s["Y"]["risk_factors"])] == [1.0, 0.0, 0.0]  # absent is 0, still listed
    assert [f["score"] for f in json.loads(s["Z"]["risk_factors"])] == [0.0, 0.0, 0.0]


def test_a_file_with_no_model_factor_has_a_zero_model_side(run):
    rules_only = [f for f in FACTORS if f["kind"] != "model"]
    s = by_key(account_scores(run, factor_run([r for r in ROWS if r[0] != "tgn"], rules_only), Policy(),
                              accounts=ACCOUNTS))
    assert s["X"]["risk_score_model"] == 0.0
    assert s["X"]["risk_score"] == pytest.approx(s["X"]["risk_score_rules"]) == pytest.approx(0.8)


def test_a_file_with_no_factors_scores_everyone_zero(run):
    df = account_scores(run, factor_run([], factors=[]), Policy(), accounts=ACCOUNTS)
    assert df["risk_score"].to_list() == [0.0] * 4 and set(df["risk_factors"]) == {"[]"}


def test_the_frame_is_the_staged_shape_with_the_mappings_key(data_dir):
    run = Run.create(Recipe(graph=GraphMapping(account_key="acct")), data_dir=data_dir)
    df = account_scores(run, factor_run(ROWS), Policy(), accounts=ACCOUNTS)
    assert df.columns == ["acct", "risk_score", "risk_score_model", "risk_score_rules", "risk_factors"]
    assert df.schema == pl.Schema({"acct": pl.Utf8, "risk_score": pl.Float64, "risk_score_model": pl.Float64,
                                   "risk_score_rules": pl.Float64, "risk_factors": pl.Utf8})
    assert df["risk_score"].to_list() == sorted(df["risk_score"].to_list(), reverse=True)  # strongest first
    run.close()


def test_a_factor_row_for_an_account_not_in_scope_is_dropped_not_added(run):
    rows = [*ROWS, ("dev", "GHOST", 1.0, "{}")]
    df = account_scores(run, factor_run(rows), Policy(), accounts=ACCOUNTS)
    assert sorted(df["account_id"]) == sorted(ACCOUNTS)  # a staged key that is not in Aura would create an account


def test_the_default_scope_is_the_run_store_or_the_slice(fixture_run):
    rows = [("dev", R1[0], 1.0, "{}"), ("dev", "GHOST", 1.0, "{}")]
    everyone = account_scores(fixture_run, factor_run(rows), Policy())
    assert everyone.height == 200 and "GHOST" not in everyone["account_id"].to_list()
    sliced = account_scores(fixture_run, factor_run(rows, keys=[R1[0], R1[1]]), Policy())
    assert sorted(sliced["account_id"]) == [R1[0], R1[1]]
    assert by_key(sliced)[R1[0]]["risk_score"] == pytest.approx(0.3)


# ---- alerts ----------------------------------------------------------------------------------------------------
def scores_at(*pairs: tuple[str, float]) -> pl.DataFrame:
    return pl.DataFrame({
        "account_id": [k for k, _ in pairs], "risk_score": [s for _, s in pairs],
        "risk_score_model": [0.0] * len(pairs), "risk_score_rules": [s for _, s in pairs],
        "risk_factors": ['[{"id":"dev","score":1.0,"weight":0.3},{"id":"vel","score":0.0,"weight":0.2}]'] * len(pairs),
    })


def test_an_account_at_or_above_the_threshold_gets_an_alert(run):
    policy = Policy(alert_threshold=0.6, window="P7D")
    out = alerts(run, scores_at(("A", 0.61), ("B", 0.6), ("C", 0.59), ("D", 0.0)), policy)
    assert out["account_key"].to_list() == ["A", "B"]  # at the threshold counts; strongest first
    assert out.columns == ["alert_key", "account_key", "window", "score", "evidence"]
    assert out["window"].to_list() == ["P7D", "P7D"] and out["score"].to_list() == [0.61, 0.6]
    assert out["alert_key"].to_list() == [alert_key(run.id, "A", "P7D"), alert_key(run.id, "B", "P7D")]


def test_a_score_a_float_hair_under_the_threshold_still_alerts(run):
    assert alerts(run, scores_at(("A", 0.6 - 1e-12)), Policy(alert_threshold=0.6)).height == 1
    assert alerts(run, scores_at(("A", 0.6 - 1e-6)), Policy(alert_threshold=0.6)).height == 0


def test_no_alerts_is_a_typed_empty_frame(run):
    out = alerts(run, scores_at(("A", 0.1)), Policy(alert_threshold=0.9))
    assert out.height == 0 and out.schema == pl.Schema({
        "alert_key": pl.Utf8, "account_key": pl.Utf8, "window": pl.Utf8, "score": pl.Float64, "evidence": pl.Utf8})


def test_alert_evidence_names_the_factors_that_fired_with_their_ids(run):
    fr = factor_run(ROWS)
    scores = account_scores(run, fr, Policy(), accounts=ACCOUNTS)
    out = alerts(run, scores, Policy(alert_threshold=0.6), fr)
    assert out["account_key"].to_list() == ["X"]
    body = json.loads(out["evidence"][0])
    assert body["account_key"] == "X" and body["window"] == "P30D" and body["alert_threshold"] == 0.6
    assert body["score"] == pytest.approx(0.8)
    assert body["risk_score_rules"] == pytest.approx(0.8) and body["risk_score_model"] == pytest.approx(0.8)
    # strongest contribution first: tgn 0.8 * 0.5, then dev 1.0 * 0.3, then vel 0.5 * 0.2
    assert [f["id"] for f in body["factors"]] == ["tgn", "dev", "vel"]
    dev = body["factors"][1]
    assert dev == {"id": "dev", "name": "Shared device", "kind": "cypher", "score": 1.0, "weight": 0.3,
                   "evidence": {"evidence_ids": ["D1"], "value": 6}}


def test_a_factor_that_did_not_fire_is_left_out_of_the_evidence(run):
    fr = factor_run([r for r in ROWS if r[1] != "X" or r[0] == "dev"])  # X fires only the device
    scores = account_scores(run, fr, Policy(), accounts=ACCOUNTS)
    out = alerts(run, scores, Policy(alert_threshold=0.2), fr).filter(pl.col("account_key") == "X")
    assert [f["id"] for f in json.loads(out["evidence"][0])["factors"]] == ["dev"]


def test_alerts_without_a_factor_run_carry_id_score_and_weight_only(run):
    body = json.loads(alerts(run, scores_at(("A", 0.9)), Policy())["evidence"][0])
    assert body["factors"] == [{"id": "dev", "score": 1.0, "weight": 0.3}]


# ---- the policy file -------------------------------------------------------------------------------------------
def test_the_policy_comes_from_the_settings_file_or_the_recipe(run, tmp_path, monkeypatch):
    assert load_policy(run).alert_threshold == Policy.load(REPO / "factors" / "policy.yaml").alert_threshold
    mine = tmp_path / "policy.yaml"
    mine.write_text('version: "2"\nalert_threshold: 0.35\nwindow: P7D\n', encoding="utf-8")
    monkeypatch.setenv("LAB_POLICY_FILE", str(mine))
    reload_settings()
    assert (load_policy(run).alert_threshold, load_policy(run).window) == (0.35, "P7D")
    other = tmp_path / "other.yaml"
    other.write_text("alert_threshold: 0.9\n", encoding="utf-8")
    run.recipe = Recipe(policy_file=str(other))
    assert load_policy(run).alert_threshold == 0.9
