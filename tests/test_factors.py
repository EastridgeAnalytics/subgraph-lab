"""Track 2b: the risk factors on the fixture graph (rings R1 and R2, look-alike L1) and the loaders for each kind."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import polars as pl
import pytest
from pydantic import ValidationError

from lab.contracts.factors import FACTOR_RESULT_COLUMNS, FactorFile
from lab.contracts.recipe import Recipe
from lab.scoring import FactorRun, run_factors
from lab.scoring.factors import RESULT_SCHEMA
from lab.settings import reload_settings
from tests.fixtures.make_fixture import L1, R1, R2

REPO = Path(__file__).resolve().parent.parent
FACTORS = REPO / "factors"
RULES = ["shared_device_5plus", "new_accounts_shared_identifier", "just_under_threshold",
         "in_and_out_within_a_day", "fan_in_new_accounts"]


def fired(fr: FactorRun, factor_id: str) -> set[str]:
    return set(fr.results.filter(pl.col("factor_id") == factor_id).filter(pl.col("score") > 0)["key"])


def status(fr: FactorRun) -> dict:
    return {s.id: s for s in fr.status}


def write_factors(tmp_path: Path, yaml_text: str, **queries: str) -> Path:
    """A factor file in its own folder, with the .cypher files it names."""
    folder = tmp_path / "my_factors"
    folder.mkdir()
    for name, text in queries.items():
        (folder / f"{name}.cypher").write_text(text, encoding="utf-8")
    path = folder / "factors.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    return path


def use_factor_file(monkeypatch, path: Path) -> None:
    monkeypatch.setenv("LAB_FACTORS_FILE", str(path))
    reload_settings()


# ---- python factors the tests point the factor file at -----------------------------------------------------------
def ok_factor(run, factor, keys):
    """The first three accounts, score 0.5, no evidence. It ignores `keys`; the loader applies the slice."""
    return run.table("MATCH (a:Account) RETURN a.account_id AS key, 0.5 AS score ORDER BY key LIMIT 3")


def boom_factor(run, factor, keys):
    raise RuntimeError("the forecast library fell over")


def not_a_frame(run, factor, keys):
    return [("A0001", 0.5)]


def no_score_column(run, factor, keys):
    return pl.DataFrame({"key": ["A0001"], "value": [1.0]})


def out_of_range(run, factor, keys):
    return pl.DataFrame({"key": ["A0001"], "score": [1.5]})


# ---- the shipped file --------------------------------------------------------------------------------------------
def test_the_shipped_factor_file_has_the_five_rules_and_the_three_originals():
    ff = FactorFile.load(FACTORS / "factors.yaml")
    by_id = {f.id: f for f in ff.factors}
    assert {"shared_device_5plus", "velocity_anomaly", "tgn"} <= set(by_id)
    cypher = [f for f in ff.factors if f.kind == "cypher"]
    assert sorted(f.id for f in cypher) == sorted(RULES)
    for f in cypher:
        assert f.name and f.description and f.weight > 0
        assert f.threshold and f.threshold > 0, f.id
        text = (FACTORS / f.query).read_text(encoding="utf-8")
        assert "$threshold" in text and "$keys" in text, f"{f.id} must take $threshold and $keys"
        assert "customer" in text.lower(), f"{f.id} should say the file is the customer's to edit"
    assert by_id["velocity_anomaly"].kind == "python" and by_id["tgn"].kind == "model"


def test_a_factor_file_that_breaks_the_schema_raises():
    with pytest.raises(ValidationError):
        FactorFile(version="1", factors=[
            {"id": "a", "name": "a", "kind": "cypher", "query": "a.cypher", "weight": 1},
            {"id": "a", "name": "again", "kind": "cypher", "query": "a.cypher", "weight": 1},
        ])


# ---- the five rules on the fixture --------------------------------------------------------------------------------
@pytest.fixture
def shipped(fixture_run) -> FactorRun:
    return run_factors(fixture_run)


def test_shared_device_fires_on_the_ring_and_not_on_the_family(shipped):
    assert fired(shipped, "shared_device_5plus") == set(R1)  # six on D-R1; the family of four stays under five
    assert not fired(shipped, "shared_device_5plus") & set(L1)


def test_new_accounts_sharing_an_identifier_fires_on_both_rings_and_not_on_the_family(shipped):
    hit = fired(shipped, "new_accounts_shared_identifier")
    assert hit == set(R1) | set(R2)  # R1 by device and address, R2 by email; the family is old
    evidence = {r["key"]: json.loads(r["evidence"]) for r in shipped.results.filter(
        pl.col("factor_id") == "new_accounts_shared_identifier").iter_rows(named=True)}
    assert evidence[R1[0]]["evidence_ids"] == ["D-R1", "AD-R1"]
    assert evidence[R2[0]]["evidence_ids"] == ["E-R2"] and evidence[R2[0]]["value"] == 8


def test_just_under_threshold_fires_on_the_structuring_ring(shipped):
    hit = fired(shipped, "just_under_threshold")
    assert set(R2) <= hit  # every feeder paid four times just under 10,000 and the collector received 28
    assert not hit & (set(R1) | set(L1))


def test_in_and_out_within_a_day_fires_on_the_mule_ring(shipped):
    hit = fired(shipped, "in_and_out_within_a_day")
    assert set(R1) <= hit  # each member passed money on within the hour, three times
    assert not hit & set(L1)


def test_fan_in_from_new_accounts_fires_on_the_collector_only(shipped):
    assert fired(shipped, "fan_in_new_accounts") == {R2[-1]}  # seven accounts under 30 days old paid it


def test_every_factor_scores_in_zero_to_one_with_json_evidence(shipped):
    r = shipped.results
    assert r.columns == ["factor_id", *FACTOR_RESULT_COLUMNS]
    assert r.schema == pl.Schema(RESULT_SCHEMA)
    assert r["score"].min() >= 0 and r["score"].max() <= 1
    assert r.select(pl.struct("factor_id", "key").n_unique()).item() == r.height  # one row per factor and account
    for text in r["evidence"]:
        body = json.loads(text)
        assert isinstance(body, dict)
    fired_rules = r.filter(pl.col("factor_id").is_in(RULES))
    for text in fired_rules["evidence"]:
        body = json.loads(text)
        assert body["evidence_ids"] and body["value"] >= body["threshold"]


def test_run_factors_reports_a_status_per_factor(shipped):
    s = status(shipped)
    assert list(s) == [f.id for f in shipped.file.factors]
    assert all(s[i].status == "ran" and s[i].accounts > 0 for i in RULES)
    assert s["velocity_anomaly"].status == "skipped" and "lab.scoring.velocity" in s["velocity_anomaly"].reason
    assert s["tgn"].status == "skipped" and "no model output" in s["tgn"].reason and "examples/tgn" in s["tgn"].reason
    assert shipped.counts() == {"factors": 7, "factors_ran": 5, "factors_skipped": 2, "factors_failed": 0,
                                "factor_rows": shipped.results.height}
    assert "skipped tgn" in shipped.note() and "skipped velocity_anomaly" in shipped.note()
    assert shipped.version == FactorFile.load(FACTORS / "factors.yaml").version


# ---- the slice ----------------------------------------------------------------------------------------------------
def test_keys_restrict_the_factors_to_the_slice(fixture_run, shipped):
    slice_keys = [*R1[:2], R2[-1], L1[0]]
    fr = run_factors(fixture_run, slice_keys)
    assert set(fr.results["key"]) <= set(slice_keys)
    assert fired(fr, "shared_device_5plus") == set(R1[:2])
    assert fired(fr, "fan_in_new_accounts") == {R2[-1]}
    same = shipped.results.filter(pl.col("key").is_in(slice_keys)).sort("factor_id", "key")
    assert fr.results.sort("factor_id", "key").equals(same)


def test_an_empty_slice_runs_every_factor_and_finds_nothing(fixture_run):
    fr = run_factors(fixture_run, [])
    assert fr.results.height == 0
    assert [s.status for s in fr.status if s.id in RULES] == ["ran"] * 5


def test_a_long_key_list_goes_in_batches(fixture_run, monkeypatch):
    monkeypatch.setenv("LAB_KEY_BATCH", "3")
    reload_settings()
    keys = [*R1, *R2, *L1, "A0001", "A0002"]
    batched = run_factors(fixture_run, keys)
    monkeypatch.setenv("LAB_KEY_BATCH", "500")
    reload_settings()
    whole = run_factors(fixture_run, keys)
    assert batched.results.sort("factor_id", "key").equals(whole.results.sort("factor_id", "key"))
    assert fired(batched, "shared_device_5plus") == set(R1)


# ---- A10: an entry in the file is in the scores, with no code change -----------------------------------------------
BIG_PAYMENT = """// accounts with a payment of 4,500 or more in or out
MATCH (a:Account)-[t:TX]-(:Account)
WHERE ($keys IS NULL OR a.account_id IN $keys) AND t.amount >= 4500
WITH a, t ORDER BY t.tx_id
WITH a, collect(DISTINCT t.tx_id) AS ids
WHERE size(ids) >= $threshold
RETURN a.account_id AS key, size(ids) AS value, ids[0..3] AS evidence_ids
"""


def test_a_new_entry_shows_up_on_the_next_run_with_no_code_change(fixture_run, tmp_path, monkeypatch):
    """A10, on the factor step: a copy of the shipped file, plus one entry and one .cypher file."""
    folder = tmp_path / "copy"
    shutil.copytree(FACTORS, folder)
    (folder / "big_payment.cypher").write_text(BIG_PAYMENT, encoding="utf-8")
    entry = ("- id: big_payment\n  name: A payment of 4,500 or more\n  kind: cypher\n  query: big_payment.cypher\n"
             "  threshold: 2\n  weight: 0.1\n")
    path = folder / "factors.yaml"
    before = run_factors(fixture_run)
    assert "big_payment" not in before.results["factor_id"].unique().to_list()
    path.write_text(path.read_text(encoding="utf-8") + entry, encoding="utf-8")
    use_factor_file(monkeypatch, path)
    after = run_factors(fixture_run)
    assert status(after)["big_payment"].status == "ran"
    hit = fired(after, "big_payment")
    assert hit and set(R1) & hit  # the ring's cycle payments are 3,800 to 4,900; money in from outsiders too
    assert after.version != before.version  # a new entry is a new factor file version
    old = {i: fired(after, i) for i in RULES}
    assert old == {i: fired(before, i) for i in RULES}  # the others did not move


def test_a_recipe_can_name_its_own_factor_file(fixture_run, tmp_path):
    path = write_factors(tmp_path, "- id: big_payment\n  name: Big\n  kind: cypher\n  query: big_payment.cypher\n"
                                   "  threshold: 2\n  weight: 1\n", big_payment=BIG_PAYMENT)
    fixture_run.recipe = Recipe(factors_file=str(path))
    fr = run_factors(fixture_run)
    assert [s.id for s in fr.status] == ["big_payment"] and fr.path == path


# ---- a cypher factor's own shapes ----------------------------------------------------------------------------------
def test_a_query_can_return_the_score_itself(fixture_run, tmp_path, monkeypatch):
    path = write_factors(tmp_path, "- id: half\n  name: Half\n  kind: cypher\n  query: half.cypher\n  weight: 1\n",
                         half="MATCH (a:Account) WHERE a.account_id IN ['A0001', 'A0002'] "
                              "RETURN a.account_id AS key, 0.5 AS score, ['x'] AS evidence_ids")
    use_factor_file(monkeypatch, path)
    fr = run_factors(fixture_run)  # no threshold needed
    assert status(fr)["half"].status == "ran"
    assert fr.results["score"].to_list() == [0.5, 0.5]
    assert json.loads(fr.results["evidence"][0]) == {"evidence_ids": ["x"]}


def test_one_account_returned_twice_keeps_the_highest_value_and_both_evidence_ids(fixture_run, tmp_path, monkeypatch):
    path = write_factors(tmp_path, "- id: twice\n  name: Twice\n  kind: cypher\n  query: twice.cypher\n"
                                   "  threshold: 4\n  weight: 1\n",
                         twice="UNWIND [['A0001', 2, 'a'], ['A0001', 6, 'b'], ['A0002', 1, 'c']] AS r "
                               "RETURN r[0] AS key, r[1] AS value, [r[2]] AS evidence_ids")
    use_factor_file(monkeypatch, path)
    fr = run_factors(fixture_run)
    rows = {r["key"]: r for r in fr.results.iter_rows(named=True)}
    assert rows["A0001"]["score"] == 1.0 and rows["A0002"]["score"] == 0.25
    assert json.loads(rows["A0001"]["evidence"]) == {"evidence_ids": ["a", "b"], "threshold": 4.0, "value": 6}


def test_a_rule_that_fires_on_nothing_ran_and_found_nothing(fixture_run, tmp_path, monkeypatch):
    path = write_factors(
        tmp_path, "- {id: never, name: Never, kind: cypher, query: shared_device.cypher, threshold: 1000, weight: 1}",
        shared_device=(FACTORS / "shared_device.cypher").read_text(encoding="utf-8"))
    use_factor_file(monkeypatch, path)
    fr = run_factors(fixture_run)
    assert status(fr)["never"].status == "ran" and status(fr)["never"].accounts == 0
    assert fr.results.height == 0 and fr.results.schema == pl.Schema(RESULT_SCHEMA)


def test_a_broken_query_fails_alone_and_says_why(fixture_run, tmp_path, monkeypatch):
    path = write_factors(tmp_path, "\n".join([
        "- {id: syntax, name: Syntax, kind: cypher, query: syntax.cypher, threshold: 1, weight: 1}",
        "- {id: nofile, name: No file, kind: cypher, query: missing.cypher, threshold: 1, weight: 1}",
        "- {id: nothreshold, name: No threshold, kind: cypher, query: ok.cypher, weight: 1}",
        "- {id: fine, name: Fine, kind: cypher, query: ok.cypher, threshold: 1, weight: 1}",
    ]), syntax="MATCH (a:Account RETURN a", ok="MATCH (a:Account {account_id: 'A0001'}) "
                                              "RETURN a.account_id AS key, 1 AS value, [] AS evidence_ids")
    use_factor_file(monkeypatch, path)
    fr = run_factors(fixture_run)
    s = status(fr)
    assert [s[i].status for i in ("syntax", "nofile", "nothreshold", "fine")] == ["failed", "failed", "failed", "ran"]
    assert s["syntax"].reason and "missing.cypher" in s["nofile"].reason and "threshold" in s["nothreshold"].reason
    assert fr.results["factor_id"].unique().to_list() == ["fine"]
    assert fr.counts()["factors_failed"] == 3


# ---- python factors ---------------------------------------------------------------------------------------------
def test_python_factors_run_or_say_why_not(fixture_run, tmp_path, monkeypatch):
    mod = "tests.test_factors"
    path = write_factors(tmp_path, "\n".join([
        f"- {{id: ok, name: Ok, kind: python, function: '{mod}:ok_factor', weight: 1}}",
        "- {id: nomodule, name: No module, kind: python, function: 'no_such_pkg.velocity:score', weight: 1}",
        f"- {{id: nofunction, name: No function, kind: python, function: '{mod}:nope', weight: 1}}",
        f"- {{id: boom, name: Boom, kind: python, function: '{mod}:boom_factor', weight: 1}}",
        f"- {{id: notframe, name: Not a frame, kind: python, function: '{mod}:not_a_frame', weight: 1}}",
        f"- {{id: noscore, name: No score, kind: python, function: '{mod}:no_score_column', weight: 1}}",
        f"- {{id: range, name: Out of range, kind: python, function: '{mod}:out_of_range', weight: 1}}",
        "- {id: badspec, name: Bad spec, kind: python, function: 'no_colon_here', weight: 1}",
    ]))
    use_factor_file(monkeypatch, path)
    fr = run_factors(fixture_run)
    s = status(fr)
    assert s["ok"].status == "ran" and s["ok"].accounts == 3
    assert fr.results.filter(pl.col("factor_id") == "ok")["score"].to_list() == [0.5] * 3
    assert json.loads(fr.results["evidence"][0]) == {"function": f"{mod}:ok_factor"}  # none given, so where it came from
    assert s["nomodule"].status == "skipped" and "no_such_pkg.velocity" in s["nomodule"].reason
    assert s["nofunction"].status == "skipped" and "nope" in s["nofunction"].reason
    assert s["boom"].status == "failed" and "fell over" in s["boom"].reason
    for failed in ("notframe", "noscore", "range", "badspec"):
        assert s[failed].status == "failed" and s[failed].reason, failed
    assert fr.results["factor_id"].unique().to_list() == ["ok"]


def test_a_python_factor_only_scores_the_slice(fixture_run, tmp_path, monkeypatch):
    path = write_factors(tmp_path, "- {id: ok, name: Ok, kind: python, function: 'tests.test_factors:ok_factor', "
                                   "weight: 1}")
    use_factor_file(monkeypatch, path)
    everyone = run_factors(fixture_run).results["key"].to_list()
    assert len(everyone) == 3
    only = run_factors(fixture_run, [everyone[0]]).results["key"].to_list()
    assert only == [everyone[0]]


# ---- model factors ---------------------------------------------------------------------------------------------
def write_model(run, factor_id: str, df: pl.DataFrame) -> None:
    folder = run.paths.artifacts / "models"
    folder.mkdir(parents=True, exist_ok=True)
    df.write_parquet(folder / f"{factor_id}.parquet")


def test_a_model_factor_reads_the_examples_parquet(fixture_run):
    write_model(fixture_run, "tgn", pl.DataFrame({
        "key": [*R1, "A0001"], "score": [0.9] * 6 + [0.1], "evidence": ['{"rank": 1}'] + [None] * 6,
    }))
    fr = run_factors(fixture_run)
    assert status(fr)["tgn"].status == "ran" and status(fr)["tgn"].accounts == 7
    tgn = {r["key"]: r for r in fr.results.filter(pl.col("factor_id") == "tgn").iter_rows(named=True)}
    assert tgn[R1[0]]["score"] == 0.9 and json.loads(tgn[R1[0]]["evidence"]) == {"rank": 1}
    assert json.loads(tgn[R1[1]]["evidence"]) == {"source": "examples/tgn"}  # no evidence column value: the source
    assert status(fr)["velocity_anomaly"].status == "skipped"  # the other missing factor is still said


def test_a_missing_model_output_is_skipped_with_its_reason(fixture_run):
    fr = run_factors(fixture_run)
    s = status(fr)["tgn"]
    assert s.status == "skipped" and s.accounts == 0
    assert s.reason == "no model output at artifacts/models/tgn.parquet; run examples/tgn first"
    assert "tgn" not in fr.results["factor_id"].to_list()


def test_a_model_output_off_the_contract_fails_and_a_stray_account_is_ignored(fixture_run):
    write_model(fixture_run, "tgn", pl.DataFrame({"key": ["A0001"], "score": [1.7]}))
    assert status(run_factors(fixture_run))["tgn"].status == "failed"
    write_model(fixture_run, "tgn", pl.DataFrame({"account": ["A0001"], "score": [0.7]}))
    assert "key" in status(run_factors(fixture_run))["tgn"].reason
    write_model(fixture_run, "tgn", pl.DataFrame({"key": ["A0001", "A0001"], "score": [0.2, 0.7]}))
    fr = run_factors(fixture_run, ["A0001", "A0002"])
    assert fr.results.filter(pl.col("factor_id") == "tgn")["score"].to_list() == [0.7]  # twice: the highest
