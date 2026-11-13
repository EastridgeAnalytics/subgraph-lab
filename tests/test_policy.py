"""Track 2b and wave 5a: the policy arithmetic on hand-made factor frames (the weighted mean, and the noisy-or of
decision 39), the recipe's choice of factors, and the alerts it decides."""
from __future__ import annotations

import json
import random
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
WM = Policy(combine="weighted_mean")
NOR = Policy(combine="noisy_or")


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
def test_the_weighted_mean_per_side_and_overall(run):
    df = account_scores(run, factor_run(ROWS), WM, accounts=ACCOUNTS)
    s = by_key(df)
    # X: rules (0.3*1 + 0.2*0.5) / 0.5 = 0.8, model 0.8, overall (0.4 + 0.4) / 1.0
    assert s["X"]["risk_score_rules"] == pytest.approx(0.8)
    assert s["X"]["risk_score_model"] == pytest.approx(0.8)
    assert s["X"]["risk_score"] == pytest.approx(0.8)
    # the overall score is the two sides' means weighted by each side's total weight
    for r in s.values():
        assert r["risk_score"] == pytest.approx((0.5 * r["risk_score_rules"] + 0.5 * r["risk_score_model"]) / 1.0)


def test_a_missing_factor_counts_zero(run):
    s = by_key(account_scores(run, factor_run(ROWS), WM, accounts=ACCOUNTS))
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
    s = by_key(account_scores(run, factor_run(rows, skipped=("tgn",)), WM, accounts=ACCOUNTS))
    assert s["X"]["risk_score_rules"] == pytest.approx(0.8)
    assert s["X"]["risk_score_model"] == 0.0
    assert s["X"]["risk_score"] == pytest.approx(0.4)


def test_risk_factors_lists_every_factor_in_file_order(run):
    s = by_key(account_scores(run, factor_run(ROWS), WM, accounts=ACCOUNTS))
    assert json.loads(s["X"]["risk_factors"]) == [
        {"id": "dev", "score": 1.0, "weight": 0.3}, {"id": "vel", "score": 0.5, "weight": 0.2},
        {"id": "tgn", "score": 0.8, "weight": 0.5}]
    assert [f["score"] for f in json.loads(s["Y"]["risk_factors"])] == [1.0, 0.0, 0.0]  # absent is 0, still listed
    assert [f["score"] for f in json.loads(s["Z"]["risk_factors"])] == [0.0, 0.0, 0.0]


def test_a_file_with_no_model_factor_has_a_zero_model_side(run):
    rules_only = [f for f in FACTORS if f["kind"] != "model"]
    s = by_key(account_scores(run, factor_run([r for r in ROWS if r[0] != "tgn"], rules_only), WM,
                              accounts=ACCOUNTS))
    assert s["X"]["risk_score_model"] == 0.0
    assert s["X"]["risk_score"] == pytest.approx(s["X"]["risk_score_rules"]) == pytest.approx(0.8)


def test_a_file_with_no_factors_scores_everyone_zero(run):
    df = account_scores(run, factor_run([], factors=[]), WM, accounts=ACCOUNTS)
    assert df["risk_score"].to_list() == [0.0] * 4 and set(df["risk_factors"]) == {"[]"}


def test_the_frame_is_the_staged_shape_with_the_mappings_key(data_dir):
    run = Run.create(Recipe(graph=GraphMapping(account_key="acct")), data_dir=data_dir)
    df = account_scores(run, factor_run(ROWS), WM, accounts=ACCOUNTS)
    assert df.columns == ["acct", "risk_score", "risk_score_model", "risk_score_rules", "risk_factors"]
    assert df.schema == pl.Schema({"acct": pl.Utf8, "risk_score": pl.Float64, "risk_score_model": pl.Float64,
                                   "risk_score_rules": pl.Float64, "risk_factors": pl.Utf8})
    assert df["risk_score"].to_list() == sorted(df["risk_score"].to_list(), reverse=True)  # strongest first
    run.close()


def test_a_factor_row_for_an_account_not_in_scope_is_dropped_not_added(run):
    rows = [*ROWS, ("dev", "GHOST", 1.0, "{}")]
    df = account_scores(run, factor_run(rows), WM, accounts=ACCOUNTS)
    assert sorted(df["account_id"]) == sorted(ACCOUNTS)  # a staged key that is not in Aura would create an account


def test_the_default_scope_is_the_run_store_or_the_slice(fixture_run):
    rows = [("dev", R1[0], 1.0, "{}"), ("dev", "GHOST", 1.0, "{}")]
    everyone = account_scores(fixture_run, factor_run(rows), WM)
    assert everyone.height == 200 and "GHOST" not in everyone["account_id"].to_list()
    sliced = account_scores(fixture_run, factor_run(rows, keys=[R1[0], R1[1]]), WM)
    assert sorted(sliced["account_id"]) == [R1[0], R1[1]]
    assert by_key(sliced)[R1[0]]["risk_score"] == pytest.approx(0.3)


# ---- the noisy-or (decision 39) -----------------------------------------------------------------------------------
def test_noisy_or_with_one_factor_is_its_weight_times_its_score(run):
    s = by_key(account_scores(run, factor_run(ROWS), NOR, accounts=ACCOUNTS))
    # Y fires only the device: 1 - (1 - 0.3 * 1.0) = 0.3, and nothing on the model side
    assert (s["Y"]["risk_score_rules"], s["Y"]["risk_score_model"], s["Y"]["risk_score"]) == (
        pytest.approx(0.3), 0.0, pytest.approx(0.3))
    # W fires only the model: 0.5 * 1.0
    assert (s["W"]["risk_score_rules"], s["W"]["risk_score_model"], s["W"]["risk_score"]) == (
        0.0, pytest.approx(0.5), pytest.approx(0.5))
    # a partial score counts in proportion: 0.2 * 0.5
    only_vel = factor_run([("vel", "X", 0.5, "{}")])
    assert by_key(account_scores(run, only_vel, NOR, accounts=ACCOUNTS))["X"]["risk_score"] == pytest.approx(0.1)


def test_noisy_or_adds_two_independent_factors_and_each_side_is_its_own(run):
    s = by_key(account_scores(run, factor_run(ROWS), NOR, accounts=ACCOUNTS))["X"]
    # X: rules 1 - (1 - 0.3)(1 - 0.2 * 0.5) = 0.37, model 1 - (1 - 0.5 * 0.8) = 0.4, whole 1 - 0.7 * 0.9 * 0.6
    assert s["risk_score_rules"] == pytest.approx(0.37)
    assert s["risk_score_model"] == pytest.approx(0.4)
    assert s["risk_score"] == pytest.approx(1 - 0.7 * 0.9 * 0.6) == pytest.approx(0.622)
    assert s["risk_score"] == pytest.approx(1 - (1 - s["risk_score_rules"]) * (1 - s["risk_score_model"]))
    assert s["risk_score"] > max(s["risk_score_rules"], s["risk_score_model"])  # two signals add up


def test_noisy_or_a_factor_that_does_not_fire_changes_nothing(run):
    """Absent, skipped and not in the file are the same to the account: the score is what the firing factors make."""
    rows = [r for r in ROWS if r[0] == "dev"]
    both = by_key(account_scores(run, factor_run(rows), NOR, accounts=ACCOUNTS))
    skipped = by_key(account_scores(run, factor_run(rows, skipped=("vel", "tgn")), NOR, accounts=ACCOUNTS))
    alone = by_key(account_scores(run, factor_run(rows, factors=FACTORS[:1]), NOR, accounts=ACCOUNTS))
    for acct in ACCOUNTS:
        for col in ("risk_score", "risk_score_rules", "risk_score_model"):
            assert both[acct][col] == skipped[acct][col] == alone[acct][col]
    assert both["X"]["risk_score"] == pytest.approx(0.3)  # not divided by the 0.7 of weight that did not fire


def test_noisy_or_weight_zero_counts_nothing_and_weight_one_with_a_full_score_is_certain(run):
    factors = [{**FACTORS[0], "weight": 1.0}, {**FACTORS[1], "weight": 0.0}]
    rows = [("dev", "X", 1.0, "{}"), ("vel", "X", 1.0, "{}"), ("vel", "Y", 1.0, "{}")]
    s = by_key(account_scores(run, factor_run(rows, factors), NOR, accounts=ACCOUNTS))
    assert s["X"]["risk_score"] == 1.0 and s["Y"]["risk_score"] == 0.0


def test_noisy_or_refuses_a_weight_outside_zero_to_one_and_names_it(run):
    """Enforced, not clipped: a weight of 1.2 is not a number the user meant to be read as 1."""
    factors = [{**FACTORS[0], "weight": 1.2}, FACTORS[1], {**FACTORS[2], "weight": 3}]
    fr = factor_run(ROWS, factors)
    with pytest.raises(ValueError, match=r"\[0, 1\].*dev \(1\.2\).*tgn \(3\)"):
        account_scores(run, fr, NOR, accounts=ACCOUNTS)
    # the weighted mean has no such limit, and a factor the recipe did not choose is not checked
    assert by_key(account_scores(run, fr, WM, accounts=ACCOUNTS))["Y"]["risk_score"] > 0
    run.recipe = Recipe(factors=["vel"])
    assert account_scores(run, fr, NOR, accounts=ACCOUNTS).height == 4


def make_factor(i: int, kind: str, weight: float) -> dict:
    extra = {"cypher": {"query": f"f{i}.cypher", "threshold": 1}, "python": {"function": "m:f"},
             "model": {"source": "examples/x"}}[kind]
    return {"id": f"f{i}", "name": f"F{i}", "kind": kind, "weight": weight, **extra}


def test_a_new_factor_never_lowers_any_score(run):
    """The point of the noisy-or (R1): over random hand-made frames, adding a factor, with any weight in [0, 1] and
    rows for any accounts, leaves every account's three scores where they were or higher. So does a higher score."""
    rng = random.Random(39)
    accounts = [f"A{i}" for i in range(8)]
    for _ in range(150):
        kinds = [rng.choice(["cypher", "python", "model"]) for _ in range(rng.randint(1, 5))]
        factors = [make_factor(i, k, rng.random()) for i, k in enumerate(kinds)]
        rows = [(f["id"], a, rng.random(), "{}") for f in factors for a in accounts if rng.random() < 0.5]
        new = make_factor(len(factors), rng.choice(["cypher", "python", "model"]), rng.random())
        new_rows = [(new["id"], a, rng.random(), "{}") for a in accounts if rng.random() < 0.6]
        before = by_key(account_scores(run, factor_run(rows, factors), NOR, accounts=accounts))
        after = by_key(account_scores(run, factor_run(rows + new_rows, [*factors, new]), NOR, accounts=accounts))
        for a in accounts:
            for col in ("risk_score", "risk_score_rules", "risk_score_model"):
                assert after[a][col] >= before[a][col] - 1e-12, (col, a, new, before[a][col], after[a][col])
                assert 0.0 <= after[a][col] <= 1.0
        if rows:  # a higher score on one factor and one account never lowers anyone's
            first = rows[0]
            bumped = [(f, k, min(1.0, sc + 0.3), e) if (f, k) == first[:2] else (f, k, sc, e) for f, k, sc, e in rows]
            raised = by_key(account_scores(run, factor_run(bumped, factors), NOR, accounts=accounts))
            assert all(raised[a]["risk_score"] >= before[a]["risk_score"] - 1e-12 for a in accounts)


def test_the_shipped_policy_is_a_noisy_or_with_a_three_tier_weight_file(run):
    """factors.yaml and policy.yaml as they ship: a strong rule alerts alone, and the rest together cannot."""
    policy = Policy.load(REPO / "factors" / "policy.yaml")
    ff = FactorFile.load(REPO / "factors" / "factors.yaml")
    assert policy.combine == "noisy_or" and all(0 <= f.weight <= 1 for f in ff.factors)
    strong = {"new_accounts_shared_identifier", "in_and_out_within_a_day", "just_under_threshold"}
    assert strong <= {f.id for f in ff.factors}

    def score_of(fired: set[str]) -> float:
        rows = [(f.id, "X", 1.0, "{}") for f in ff.factors if f.id in fired]
        fr = FactorRun(path=Path("factors.yaml"), file=ff, results=pl.DataFrame(rows, schema=RESULT_SCHEMA, orient="row"),
                       status=[FactorStatus(f.id, f.kind, "ran") for f in ff.factors], keys=None)
        return by_key(account_scores(run, fr, policy, accounts=["X"]))["X"]["risk_score"]

    for rule in strong:
        assert score_of({rule}) >= policy.alert_threshold, rule
    support = {f.id for f in ff.factors} - strong
    assert score_of(support) < policy.alert_threshold  # a family's laptop, fan-in and two unproven models: no alert
    assert score_of(support | {"just_under_threshold"}) > score_of({"just_under_threshold"})  # but they raise it


def test_noisy_or_alerts_use_the_same_threshold_and_name_the_contributions(run):
    fr = factor_run(ROWS)
    scores = account_scores(run, fr, NOR, accounts=ACCOUNTS)
    out = alerts(run, scores, Policy(combine="noisy_or", alert_threshold=0.6), fr)
    assert out["account_key"].to_list() == ["X"]  # 0.622; W is 0.5 and Y is 0.3
    body = json.loads(out["evidence"][0])
    assert body["score"] == pytest.approx(0.622)
    assert [f["id"] for f in body["factors"]] == ["tgn", "dev", "vel"]  # weight * score: 0.4, 0.3, 0.1


# ---- the recipe chooses its factors (decision 40) --------------------------------------------------------------------
def test_only_the_recipes_factors_count_and_risk_factors_lists_just_them(run):
    run.recipe = Recipe(factors=["tgn", "dev"])  # the file's order wins, not the recipe's
    s = by_key(account_scores(run, factor_run(ROWS), NOR, accounts=ACCOUNTS))
    assert [f["id"] for f in json.loads(s["X"]["risk_factors"])] == ["dev", "tgn"]
    assert s["X"]["risk_score_rules"] == pytest.approx(0.3)  # vel is not chosen: not 0.37
    assert s["X"]["risk_score"] == pytest.approx(1 - 0.7 * 0.6)


def test_a_factor_that_is_not_chosen_leaves_the_weighted_means_divisor_but_a_skipped_one_stays(run):
    run.recipe = Recipe(factors=["dev", "vel"])
    s = by_key(account_scores(run, factor_run(ROWS), WM, accounts=ACCOUNTS))
    assert s["X"]["risk_score"] == pytest.approx((0.3 + 0.1) / 0.5)  # tgn's 0.5 is not in the divisor
    run.recipe = Recipe()
    skipped = by_key(account_scores(run, factor_run([r for r in ROWS if r[0] != "tgn"], skipped=("tgn",)), WM,
                                    accounts=ACCOUNTS))
    assert skipped["X"]["risk_score"] == pytest.approx((0.3 + 0.1) / 1.0)  # asked for and skipped: it still counts 0


def test_an_unknown_factor_id_in_the_recipe_is_an_error_that_names_it(run):
    run.recipe = Recipe(factors=["dev", "nope", "also_nope"])
    with pytest.raises(ValueError, match="nope, also_nope"):
        account_scores(run, factor_run(ROWS), NOR, accounts=ACCOUNTS)


def test_a_recipe_that_chooses_nothing_scores_everyone_zero(run):
    run.recipe = Recipe(factors=[])
    df = account_scores(run, factor_run(ROWS), NOR, accounts=ACCOUNTS)
    assert df["risk_score"].to_list() == [0.0] * 4 and set(df["risk_factors"]) == {"[]"}


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
    scores = account_scores(run, fr, WM, accounts=ACCOUNTS)
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
    scores = account_scores(run, fr, WM, accounts=ACCOUNTS)
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
