"""Track 2b: `score(run)` on the fixture graph, with `run.stage` patched to capture what would be staged."""
from __future__ import annotations

import json
import shutil
import sys
import types
from pathlib import Path

import polars as pl
import pytest

import lab.scoring.pipeline as pipeline
from lab.common import alert_key
from lab.contracts.factors import FactorFile
from lab.contracts.graph import ACCOUNT_RESULT_PROPS, ALERT_PROPS
from lab.contracts.manifest import StepStatus
from lab.contracts.recipe import Recipe
from lab.contracts.staged import staged_errors
from lab.runs import Run
from lab.scoring import score
from lab.settings import reload_settings
from tests.fixtures.make_fixture import L1, R1, R2

REPO = Path(__file__).resolve().parent.parent
KEY = "account_id"


@pytest.fixture
def staged(fixture_run, monkeypatch) -> dict[str, tuple[pl.DataFrame, str]]:
    """What `run.stage(df, key, kind)` was called with, by kind. Staging itself is track 1e's."""
    calls: dict[str, tuple[pl.DataFrame, str]] = {}

    def fake_stage(df, key=None, kind="account"):
        calls[kind] = (df, key)
        return df.height

    monkeypatch.setattr(fixture_run, "stage", fake_stage)
    return calls


def reread(run: Run) -> Run:
    """The run as the manifest on disk says it is, not as this process holds it."""
    return Run(run.id, run.paths.root.parent.parent)


def precision_recall(alerts: pl.DataFrame, truth: pl.DataFrame) -> tuple[float, float]:
    planted = set(truth.filter(pl.col("ring_id") != "")["account_id"])
    flagged = set(alerts["account_key"])
    hit = len(planted & flagged)
    return (hit / len(flagged) if flagged else 0.0), hit / len(planted)


def test_score_stages_the_account_scores_and_the_alerts(fixture_run, staged, truth):
    counts = fixture_run.score()  # the facade: lab.scoring.pipeline.score
    accounts, key = staged["account"]
    found, alert_key_col = staged["alert"]
    assert (key, alert_key_col) == (KEY, "alert_key")

    # the account frame is a valid staged table, one row per account, with the four scores and the stamp
    assert staged_errors(accounts, KEY) == []
    assert accounts.height == 200
    assert {"risk_score", "risk_score_model", "risk_score_rules", "risk_factors", "risk_run_id", "risk_at"} <= set(
        accounts.columns)
    assert set(accounts.columns) - {KEY} <= set(ACCOUNT_RESULT_PROPS)
    assert set(accounts["risk_run_id"]) == {fixture_run.id} and accounts["risk_at"][0].endswith("Z")
    assert len(json.loads(accounts["risk_factors"][0])) == 7  # every factor in the file, scored or not

    # the alert frame has the Alert node's columns, keyed by the documented hash
    assert {"alert_key", "account_key", "window", "score", "evidence", "run_id", "created_at"} <= set(found.columns)
    assert set(found.columns) <= set(ALERT_PROPS)
    assert found.height > 0 and found["alert_key"].n_unique() == found.height
    for r in found.iter_rows(named=True):
        assert r["alert_key"] == alert_key(fixture_run.id, r["account_key"], r["window"])
        assert r["run_id"] == fixture_run.id and json.loads(r["evidence"])["factors"]

    # R1 and R2 outscore L1, the look-alike
    scores = dict(zip(accounts[KEY], accounts["risk_score"], strict=True))
    assert min(scores[a] for a in [*R1, *R2]) > max(scores[a] for a in L1)

    assert counts["accounts"] == 200 and counts["alerts"] == found.height
    precision, recall = precision_recall(found, truth)
    print(f"\nfixture alerts at the shipped policy: {found.height} alerts, precision {precision:.2f}, recall {recall:.2f}")


def test_the_manifest_records_the_score_step_the_factor_version_and_what_was_skipped(fixture_run, staged):
    counts = score(fixture_run)
    run = reread(fixture_run)  # read back from disk, not from memory
    step = run.manifest.step("score")
    assert step.status == StepStatus.DONE and step.started_at and step.finished_at
    assert step.counts == counts
    assert counts["factors"] == 7 and counts["factors_ran"] == 6 and counts["factors_skipped"] == 1
    assert counts["factors_failed"] == 0
    version = FactorFile.load(REPO / "factors" / "factors.yaml").version
    assert run.manifest.factor_version == version
    # skipped is said, with the factor and the reason
    assert version in step.note
    assert "skipped velocity_anomaly" not in step.note  # it runs since 3c
    assert "skipped tgn: no model output at artifacts/models/tgn.parquet" in step.note
    assert (run.paths.artifacts / "factor_results.parquet").exists()


def test_ring_accounts_rank_above_the_look_alike_and_alerts_are_reported(fixture_run, staged, truth):
    score(fixture_run)
    accounts, _ = staged["account"]
    found, _ = staged["alert"]
    top = accounts.head(14)[KEY].to_list()  # the planted rings are 14 accounts
    planted = set(truth.filter(pl.col("ring_id") != "")["account_id"])
    assert len(set(top) & planted) >= 12  # without the model factors the rings still lead the ranking
    assert set(found["account_key"]) & set(R1)
    assert not set(found["account_key"]) & set(L1)


def test_a_model_factor_output_joins_the_score(fixture_run, staged):
    score(fixture_run)
    before = dict(zip(*staged["account"][0].select(KEY, "risk_score"), strict=True))
    folder = fixture_run.paths.artifacts / "models"
    folder.mkdir(parents=True)
    pl.DataFrame({"key": [*R1, *R2], "score": [1.0] * 14}).write_parquet(folder / "tgn.parquet")
    counts = score(fixture_run)
    after = dict(zip(*staged["account"][0].select(KEY, "risk_score"), strict=True))
    assert counts["factors_ran"] == 7 and counts["factors_skipped"] == 0
    weight = {f.id: f.weight for f in FactorFile.load(REPO / "factors" / "factors.yaml").factors}
    # the noisy-or: a model score of 1.0 at weight w leaves a share w of what was left, so it only adds
    assert after[R1[0]] == pytest.approx(1 - (1 - before[R1[0]]) * (1 - weight["tgn"]))
    assert after[R1[0]] > before[R1[0]]
    assert after["A0001"] == before["A0001"]  # no model score for it, so nothing moved
    model_side = dict(zip(*staged["account"][0].select(KEY, "risk_score_model"), strict=True))
    assert model_side[R1[0]] == pytest.approx(weight["tgn"]) and model_side["A0001"] == 0.0  # one model factor: w * 1.0


def test_the_shipped_policy_alerts_on_both_rings_and_not_on_the_look_alike(fixture_run, staged, truth):
    """Decision 39 on the fixture: the strong rules alert alone, and the family on one laptop does not."""
    score(fixture_run)
    found, _ = staged["alert"]
    precision, recall = precision_recall(found, truth)
    assert set(R1) | set(R2) <= set(found["account_key"]) and not set(found["account_key"]) & set(L1)
    assert precision >= 0.9 and recall >= 0.9


def test_a_recipe_that_chooses_its_factors_scores_with_those_only(fixture_run, staged):
    """Decision 40, through the whole step: the others are `not chosen`, count nothing and are not in `risk_factors`."""
    fixture_run.recipe = Recipe(factors=["just_under_threshold", "fan_in_new_accounts"])
    counts = score(fixture_run)
    assert counts["factors"] == 7 and counts["factors_ran"] == 2 and counts["factors_not_chosen"] == 5
    assert counts["factors_skipped"] == 0 and counts["factors_failed"] == 0
    accounts, _ = staged["account"]
    assert [f["id"] for f in json.loads(accounts["risk_factors"][0])] == ["just_under_threshold", "fan_in_new_accounts"]
    found, _ = staged["alert"]
    assert set(found["account_key"]) == set(R2)  # only structuring can alert when only its rules are chosen
    for r in found.iter_rows(named=True):
        assert {f["factor_id"] for f in json.loads(r["evidence"])["factors"]} <= {"just_under_threshold", "fan_in_new_accounts"}
    note = reread(fixture_run).manifest.step("score").note
    assert "tgn" not in note and "skipped" not in note  # not chosen is not a problem to report
    fr = pl.read_parquet(fixture_run.paths.artifacts / "factor_results.parquet")
    assert set(fr["factor_id"]) <= {"just_under_threshold", "fan_in_new_accounts"}


def test_an_unknown_factor_id_fails_the_step_and_names_it(fixture_run, staged):
    fixture_run.recipe = Recipe(factors=["shared_device_5plus", "nope"])
    with pytest.raises(ValueError, match="nope"):
        score(fixture_run)
    step = reread(fixture_run).manifest.step("score")
    assert step.status == StepStatus.FAILED and "nope" in step.note and staged == {}


# ---- the slice ---------------------------------------------------------------------------------------------------
def fake_slice_module(monkeypatch, current_slice) -> None:
    monkeypatch.setitem(sys.modules, "lab.slice.search", types.SimpleNamespace(current_slice=current_slice))


def test_the_slice_limits_who_is_scored(fixture_run, staged, monkeypatch):
    fake_slice_module(monkeypatch, lambda run: [*R1, *L1])
    counts = score(fixture_run)
    accounts, _ = staged["account"]
    assert sorted(accounts[KEY]) == sorted([*R1, *L1]) and counts["accounts"] == 10
    assert set(staged["alert"][0]["account_key"]) <= set(R1)


def test_a_slice_of_nothing_scores_nothing(fixture_run, staged, monkeypatch):
    fake_slice_module(monkeypatch, lambda run: [])
    counts = score(fixture_run)
    assert counts["accounts"] == 0 and counts["alerts"] == 0 and counts["factors_ran"] == 6
    assert staged["account"][0].height == 0 and staged["alert"][0].height == 0


def test_no_slice_means_every_account(fixture_run, staged, monkeypatch):
    fake_slice_module(monkeypatch, lambda run: None)  # the slicer says: no slice
    assert score(fixture_run)["accounts"] == 200
    monkeypatch.setitem(sys.modules, "lab.slice.search", types.SimpleNamespace())  # a module with no current_slice
    assert score(fixture_run)["accounts"] == 200
    monkeypatch.setitem(sys.modules, "lab.slice.search", None)  # no such module yet: track 2a builds it in parallel
    assert pipeline.slice_keys(fixture_run) is None and score(fixture_run)["accounts"] == 200


def test_a_slice_given_as_a_series_or_numbers_is_read_as_keys(fixture_run, monkeypatch):
    fake_slice_module(monkeypatch, lambda run: pl.Series("k", R1[:2]))
    assert pipeline.slice_keys(fixture_run) == R1[:2]
    fake_slice_module(monkeypatch, lambda run: (1, 2))
    assert pipeline.slice_keys(fixture_run) == ["1", "2"]


# ---- A10, through the whole step ------------------------------------------------------------------------------------
def test_a_new_factor_in_the_file_is_in_the_next_scores(fixture_run, staged, tmp_path, monkeypatch):
    """A10: one new .cypher file and one new entry, then score again. No code change."""
    folder = tmp_path / "copy"
    shutil.copytree(REPO / "factors", folder)
    score(fixture_run)
    before = staged["account"][0]
    assert "big_payment" not in before["risk_factors"][0]

    (folder / "big_payment.cypher").write_text(
        "MATCH (a:Account)-[t:TX]-(:Account)\nWHERE ($keys IS NULL OR a.account_id IN $keys) AND t.amount >= 9000\n"
        "WITH a, count(DISTINCT t) AS n WHERE n >= $threshold\n"
        "RETURN a.account_id AS key, n AS value, [] AS evidence_ids\n", encoding="utf-8")
    yaml_file = folder / "factors.yaml"
    yaml_file.write_text(yaml_file.read_text(encoding="utf-8") + (
        "- id: big_payment\n  name: Payments of 9,000 or more\n  kind: cypher\n  query: big_payment.cypher\n"
        "  threshold: 1\n  weight: 0.5\n"), encoding="utf-8")
    monkeypatch.setenv("LAB_FACTORS_FILE", str(yaml_file))
    reload_settings()
    counts = score(fixture_run)

    after = staged["account"][0]
    assert counts["factors"] == 8 and counts["factors_ran"] == 7
    listed = json.loads(after["risk_factors"][0])
    assert [f["id"] for f in listed][-1] == "big_payment" and listed[-1]["weight"] == 0.5
    hit = {r[KEY]: json.loads(r["risk_factors"])[-1]["score"] for r in after.iter_rows(named=True)}
    fired = {a for a, s in hit.items() if s > 0}
    assert set(R2) <= fired and not fired & set(R1)  # R2's payments are 9,500 and up; so are the collector's payouts
    run = reread(fixture_run)
    assert run.manifest.factor_version == FactorFile.load(yaml_file).version
    assert run.manifest.factor_version != FactorFile.load(REPO / "factors" / "factors.yaml").version


# ---- failure and the places later tracks join ------------------------------------------------------------------------
def test_a_factor_file_that_breaks_the_schema_fails_the_step_and_raises(fixture_run, staged, tmp_path, monkeypatch):
    bad = tmp_path / "factors.yaml"
    bad.write_text("- {id: x, name: X, kind: cypher, weight: 1}\n", encoding="utf-8")  # a cypher factor with no query
    monkeypatch.setenv("LAB_FACTORS_FILE", str(bad))
    reload_settings()
    with pytest.raises(ValueError, match="needs query"):
        score(fixture_run)
    step = reread(fixture_run).manifest.step("score")
    assert step.status == StepStatus.FAILED and "needs query" in step.note
    assert staged == {}  # nothing was staged


def test_a_failed_factor_does_not_stop_the_step_but_the_note_names_it(fixture_run, staged, tmp_path, monkeypatch):
    folder = tmp_path / "copy"
    shutil.copytree(REPO / "factors", folder)
    (folder / "broken.cypher").write_text("MATCH (a:Account RETURN a", encoding="utf-8")
    yaml_file = folder / "factors.yaml"
    yaml_file.write_text(yaml_file.read_text(encoding="utf-8") + (
        "- {id: broken, name: Broken, kind: cypher, query: broken.cypher, threshold: 1, weight: 0.1}\n"),
        encoding="utf-8")
    monkeypatch.setenv("LAB_FACTORS_FILE", str(yaml_file))
    reload_settings()
    counts = score(fixture_run)
    assert counts["factors_failed"] == 1 and counts["accounts"] == 200
    note = reread(fixture_run).manifest.step("score").note
    assert "failed broken:" in note


def test_communities_and_summaries_join_through_named_functions(fixture_run, staged, monkeypatch):
    """3a and 3b replace these two functions; the pipeline stages what they return and counts what they report."""
    seen = {}

    def communities(run, scores, keys):
        seen["communities"] = (scores.height, keys)
        return scores.with_columns(pl.lit("c1").alias("risk_community_key")), {"communities": 3}

    def summaries(run, found, factor_run):
        seen["summaries"] = (found.height, factor_run.version)
        return found.with_columns(pl.lit("template").alias("summary_source")), {"summaries": found.height}

    monkeypatch.setattr(pipeline, "attach_communities", communities)
    monkeypatch.setattr(pipeline, "attach_summaries", summaries)
    counts = score(fixture_run)
    assert seen["communities"] == (200, None) and seen["summaries"][0] == counts["alerts"]
    assert set(staged["account"][0]["risk_community_key"]) == {"c1"}
    assert set(staged["alert"][0]["summary_source"]) == {"template"}
    assert counts["communities"] == 3 and counts["summaries"] == counts["alerts"]
    assert reread(fixture_run).manifest.step("score").counts["communities"] == 3


def test_embeddings_go_back_with_the_scores_when_the_first_pass_ran(fixture_run, monkeypatch):
    from lab.contracts.graph import EMBEDDING_PROP
    from lab.contracts.staged import staged_errors
    from lab.scoring.pipeline import attach_embeddings

    fixture_run.store().execute(f"MATCH (a:Account) WHERE a.account_id IN ['A0011', 'A0012'] SET a.{EMBEDDING_PROP} = [0.5, 0.25]")
    scores = pl.DataFrame({"account_id": ["A0011", "A0012", "A0013"], "risk_score": [0.9, 0.8, 0.1]})
    out = attach_embeddings(fixture_run, scores)
    assert out.filter(pl.col("account_id") == "A0011")[EMBEDDING_PROP].to_list() == [[0.5, 0.25]]
    assert out.filter(pl.col("account_id") == "A0013")[EMBEDDING_PROP].to_list() == [None]
    assert staged_errors(out, "account_id") == []
    fixture_run.recipe.write_back.vectors = False
    assert EMBEDDING_PROP not in attach_embeddings(fixture_run, scores).columns
