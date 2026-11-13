"""Track 1e: staging results into the push store. All of it runs offline: stage writes only the run's push store, and
the first test pins that down by making any call to `relate` or to the master graph fail."""
from __future__ import annotations

import datetime as dt
import json

import polars as pl
import pytest

from lab.common.ids import alert_key, community_key
from lab.common.store import open_store, rows
from lab.contracts.manifest import PushEntity, StepStatus
from lab.roundtrip.stage import NothingStaged, StagedError, push_store_counts, stage_disposition
from tests.push_helpers import account_frame, alert_frame, community_frame

KEYS = ["A0001", "A0002", "A0003"]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Staging never reaches the master graph or runs `relate`."""
    def boom(*a, **k):
        raise AssertionError("stage must not call relate or the master graph")

    monkeypatch.setattr("subprocess.run", boom)  # how `relate_cli` runs the binary
    monkeypatch.setattr("neo4j.GraphDatabase.driver", boom)  # how `neo4j_session` reaches the master graph


def staged(run, query: str, params: dict | None = None) -> list[dict]:
    return rows(run.push_store(), query, params)


def err(df: pl.DataFrame, run, kind: str = "account", key: str = "account_id") -> list[str]:
    with pytest.raises(StagedError) as e:
        run.stage(df, key=key, kind=kind)
    return e.value.errors


# ---- accounts ---------------------------------------------------------------------------------------------------
def test_accounts_land_with_risk_columns_run_id_and_time(fixture_run):
    df = account_frame(KEYS)
    assert fixture_run.stage(df, kind="account") == 3
    got = staged(fixture_run, "MATCH (a:Account) RETURN properties(a) AS p ORDER BY a.account_id")
    assert [g["p"]["account_id"] for g in got] == KEYS
    p = got[0]["p"]
    assert p["risk_score"] == 0.9 and p["risk_score_model"] == 0.45
    assert p["risk_run_id"] == fixture_run.id
    assert p["risk_at"].endswith("Z") and len(p["risk_at"]) == 20  # UTC ISO-8601 string, decision 23
    assert json.loads(p["risk_factors"])[0]["id"] == "shared_device_5plus"
    assert isinstance(p["risk_embedding"], list) and all(isinstance(x, float) for x in p["risk_embedding"])
    assert p["risk_embedding"] == [0.1, 0.1, 0.1] and got[2]["p"]["risk_embedding"] == [0.3, 0.3, 0.3]


def test_staged_run_id_and_time_kept_when_the_frame_gives_them(fixture_run):
    df = account_frame(KEYS[:1]).with_columns(
        pl.lit(fixture_run.id).alias("risk_run_id"), pl.lit("2026-10-01T00:00:00Z").alias("risk_at"))
    fixture_run.stage(df, kind="account")
    p = staged(fixture_run, "MATCH (a:Account) RETURN properties(a) AS p")[0]["p"]
    assert p["risk_at"] == "2026-10-01T00:00:00Z"


def test_a_frame_cannot_stage_under_another_runs_id(fixture_run):
    df = account_frame(KEYS[:1]).with_columns(pl.lit("run-someone-else").alias("risk_run_id"))
    assert any("this run's id" in e for e in err(df, fixture_run))
    assert not fixture_run.paths.push_store.exists()  # nothing was written


def test_the_push_store_gets_a_uniqueness_constraint_per_label(fixture_run):
    fixture_run.stage(account_frame(KEYS), kind="account")
    found = {(r["labelsOrTypes"][0], r["properties"][0]) for r in staged(fixture_run, "SHOW CONSTRAINTS")}
    assert found == {("Account", "account_id"), ("Alert", "alert_key"), ("RiskCommunity", "community_key"),
                     ("LabRun", "run_id")}


def test_staging_twice_changes_nothing(fixture_run):
    df = account_frame(KEYS)
    fixture_run.stage(df, kind="account")
    first = staged(fixture_run, "MATCH (a:Account) RETURN properties(a) AS p ORDER BY a.account_id")
    fixture_run.stage(df.drop("risk_embedding"), kind="account")  # fewer columns: the vectors stay
    again = staged(fixture_run, "MATCH (a:Account) RETURN properties(a) AS p ORDER BY a.account_id")
    assert [g["p"]["risk_embedding"] for g in again] == [g["p"]["risk_embedding"] for g in first]
    assert len(again) == 3


def test_a_null_is_left_out_not_written(fixture_run):
    df = account_frame(KEYS[:2])
    fixture_run.stage(df, kind="account")
    fixture_run.stage(pl.DataFrame({"account_id": KEYS[:2], "risk_score": [None, 0.2]}), kind="account")
    got = {r["k"]: r["s"] for r in staged(fixture_run, "MATCH (a:Account) RETURN a.account_id AS k, a.risk_score AS s")}
    assert got == {"A0001": 0.9, "A0002": 0.2}  # a null did not erase the earlier score


def test_account_validation_names_every_problem(fixture_run):
    bad = pl.DataFrame({"account_id": ["A1", "A1", None], "score": [1, 2, 3], "risk_ok": [0.1, 0.2, 0.3]})
    errors = err(bad, fixture_run)
    assert any("duplicate" in e for e in errors) and any("nulls" in e for e in errors)
    assert any("'score' does not start with 'risk_'" in e for e in errors)
    assert any("no key column" in e for e in err(pl.DataFrame({"x": [1]}), fixture_run))


def test_a_datetime_column_is_refused_with_the_reason(fixture_run):
    now = dt.datetime(2026, 11, 12, 21, 45, tzinfo=dt.UTC)
    df = pl.DataFrame({"account_id": KEYS[:1], "risk_at": [now]})
    errors = err(df, fixture_run)
    assert len(errors) == 1 and "UTC ISO-8601 strings" in errors[0] and "'risk_at'" in errors[0]
    for temporal in (dt.date(2026, 11, 12), dt.datetime(2026, 11, 12, 21, 45)):
        errors = err(pl.DataFrame({"account_id": KEYS[:1], "risk_x": [temporal]}), fixture_run)
        assert any("UTC ISO-8601" in e for e in errors)
    obj = pl.DataFrame({"account_id": KEYS[:1], "risk_x": [now]}, schema={"account_id": pl.String, "risk_x": pl.Object})
    assert any("UTC ISO-8601" in e for e in err(obj, fixture_run))
    assert not fixture_run.paths.push_store.exists()


def test_nan_and_infinity_are_refused(fixture_run):
    bad = pl.DataFrame({"account_id": KEYS[:2], "risk_score": [float("nan"), 0.1]})
    assert any("NaN" in e for e in err(bad, fixture_run))
    vec = pl.DataFrame({"account_id": KEYS[:1], "risk_embedding": [[0.1, float("inf")]]})
    assert any("NaN, infinite or null" in e for e in err(vec, fixture_run))


# ---- alerts and communities --------------------------------------------------------------------------------------
def test_alerts_link_to_their_account_and_get_run_id_and_time(fixture_run):
    ak = alert_key(fixture_run.id, "A0001", "2026-10-05")
    assert fixture_run.stage(alert_frame([(ak, "A0001")]), key="alert_key", kind="alert") == 1
    got = staged(fixture_run, "MATCH (al:Alert)-[:ALERT_ON]->(a:Account) RETURN properties(al) AS p, a.account_id AS k")
    assert got[0]["k"] == "A0001"
    p = got[0]["p"]
    assert p["alert_key"] == ak and p["run_id"] == fixture_run.id and p["created_at"].endswith("Z")
    assert p["account_key"] == "A0001" and p["score"] == 0.8


def test_the_facade_key_is_accepted_for_alerts(fixture_run):
    """`Run.stage(df, kind="alert")` passes the account key by default; the alert key column is fixed."""
    assert fixture_run.stage(alert_frame([("k1", "A0001")]), kind="alert") == 1
    with pytest.raises(StagedError, match="keyed by 'alert_key'"):
        fixture_run.stage(alert_frame([("k2", "A0001")]), key="something_else", kind="alert")


def test_maps_and_lists_are_stored_as_json_strings(fixture_run):
    df = alert_frame([("k1", "A0001")]).drop("evidence").with_columns(
        pl.Series("evidence", [{"members": 3, "shared": [{"kind": "device", "n": 3}]}]),
        pl.Series("summary", [["a", "b"]]),
    )
    fixture_run.stage(df, kind="alert")
    p = staged(fixture_run, "MATCH (al:Alert) RETURN properties(al) AS p")[0]["p"]
    assert json.loads(p["evidence"]) == {"members": 3, "shared": [{"kind": "device", "n": 3}]}
    assert json.loads(p["summary"]) == ["a", "b"]


def test_alert_validation(fixture_run):
    good = alert_frame([("k1", "A0001")])
    assert any("needs a 'account_key'" in e for e in err(good.drop("account_key"), fixture_run, "alert", "alert_key"))
    unknown = good.with_columns(pl.lit(1).alias("surprise"))
    assert any("'surprise' is not an alert property" in e for e in err(unknown, fixture_run, "alert", "alert_key"))
    dup = alert_frame([("k1", "A0001"), ("k1", "A0002")])
    assert any("duplicate" in e for e in err(dup, fixture_run, "alert", "alert_key"))
    when = good.with_columns(pl.lit(dt.datetime(2026, 11, 12, tzinfo=dt.UTC)).alias("created_at"))
    assert any("UTC ISO-8601" in e for e in err(when, fixture_run, "alert", "alert_key"))
    nested = good.with_columns(pl.Series("evidence", [{"at": dt.datetime(2026, 11, 12, tzinfo=dt.UTC)}]).alias("evidence"))
    assert any("UTC ISO-8601" in e for e in err(nested, fixture_run, "alert", "alert_key"))
    assert not fixture_run.paths.push_store.exists()


def test_communities_link_members_and_mark_each_account(fixture_run):
    ck = community_key(fixture_run.id, KEYS)
    assert fixture_run.stage(community_frame(ck, KEYS), kind="community") == 1
    c = staged(fixture_run, "MATCH (c:RiskCommunity) RETURN properties(c) AS p")[0]["p"]
    assert c["community_key"] == ck and c["run_id"] == fixture_run.id and c["size"] == 3
    assert json.loads(c["score_parts"]) == {"shared": 0.5} and c["embedding"] == [0.5, 0.5, 0.5]
    assert "member_keys" not in c
    links = staged(fixture_run, "MATCH (a:Account)-[:IN_RISK_COMMUNITY]->(c:RiskCommunity) "
                                "RETURN a.account_id AS k, a.risk_community_key AS ck ORDER BY k")
    assert links == [{"k": k, "ck": ck} for k in KEYS]


def test_community_validation(fixture_run):
    good = community_frame("c1", KEYS)
    assert any("needs a 'member_keys'" in e for e in err(good.drop("member_keys"), fixture_run, "community", "community_key"))
    empty = good.with_columns(pl.Series("member_keys", [[]], dtype=pl.List(pl.String)))
    assert any("no members" in e for e in err(empty, fixture_run, "community", "community_key"))
    flat = good.with_columns(pl.lit("A0001").alias("member_keys"))
    assert any("list of account keys" in e for e in err(flat, fixture_run, "community", "community_key"))
    odd = good.with_columns(pl.lit(1).alias("surprise"))
    assert any("not an community property" in e for e in err(odd, fixture_run, "community", "community_key"))


# ---- dispositions ------------------------------------------------------------------------------------------------
def test_dispositions_are_staged_on_accounts_alerts_and_communities(fixture_run):
    fixture_run.stage(account_frame(KEYS[:1]), kind="account")
    fixture_run.stage(alert_frame([("k1", "A0001")]), kind="alert")
    fixture_run.stage(community_frame("c1", KEYS), kind="community")
    stage_disposition(fixture_run, "account", "A0001", "confirmed", "tim")
    stage_disposition(fixture_run, "account", "A0777", "needs_more", "shayna")  # not staged yet: MERGEd by key
    stage_disposition(fixture_run, "alert", "k1", "false_positive", "laura")
    stage_disposition(fixture_run, "community", "c1", "confirmed", "joe")
    accounts = {r["k"]: r["p"] for r in staged(fixture_run, "MATCH (a:Account) RETURN a.account_id AS k, properties(a) AS p")}
    assert accounts["A0001"]["risk_disposition"] == "confirmed" and accounts["A0001"]["risk_disposition_by"] == "tim"
    assert accounts["A0001"]["risk_score"] == 0.9  # the score is still there
    assert accounts["A0777"]["risk_disposition"] == "needs_more"
    assert accounts["A0001"]["risk_disposition_at"].endswith("Z")
    al = staged(fixture_run, "MATCH (n:Alert) RETURN properties(n) AS p")[0]["p"]
    assert (al["risk_disposition"], al["risk_disposition_by"]) == ("false_positive", "laura")
    co = staged(fixture_run, "MATCH (n:RiskCommunity) RETURN properties(n) AS p")[0]["p"]
    assert (co["risk_disposition"], co["risk_disposition_by"]) == ("confirmed", "joe")
    assert push_store_counts(fixture_run.push_store(), fixture_run.recipe.graph)["dispositions"] == 4


def test_a_bad_disposition_is_refused(fixture_run):
    with pytest.raises(ValueError, match="disposition must be one of"):
        stage_disposition(fixture_run, "account", "A0001", "guilty", "tim")
    with pytest.raises(ValueError, match="who made it"):
        stage_disposition(fixture_run, "account", "A0001", "confirmed", " ")
    fixture_run.stage(account_frame(KEYS[:1]), kind="account")
    with pytest.raises(ValueError, match="no staged alert 'nope'"):
        stage_disposition(fixture_run, "alert", "nope", "confirmed", "tim")
    with pytest.raises(ValueError, match="no staged community 'nope'"):
        stage_disposition(fixture_run, "community", "nope", "confirmed", "tim")


# ---- the copies and the manifest -----------------------------------------------------------------------------------
def test_every_staged_frame_is_copied_to_parquet(fixture_run):
    fixture_run.stage(account_frame(KEYS), kind="account")
    fixture_run.stage(account_frame(KEYS[:1]), kind="account")
    fixture_run.stage(alert_frame([("k1", "A0001")]), kind="alert")
    fixture_run.stage(community_frame("c1", KEYS), kind="community")
    names = sorted(p.name for p in fixture_run.paths.staged.iterdir())
    assert names == ["account-1.parquet", "account-2.parquet", "alert-1.parquet", "community-1.parquet"]
    first = pl.read_parquet(fixture_run.paths.staged / "account-1.parquet")
    assert first.height == 3 and first["risk_embedding"].to_list()[0] == [0.1, 0.1, 0.1]
    assert {"risk_run_id", "risk_at"} <= set(first.columns)  # what was staged, run id and time included
    assert pl.read_parquet(fixture_run.paths.staged / "community-1.parquet")["member_keys"].to_list() == [KEYS]


def test_the_manifest_records_the_stage_counts(fixture_run):
    fixture_run.stage(account_frame(KEYS), kind="account")
    fixture_run.stage(alert_frame([("k1", "A0001"), ("k2", "A0002")]), kind="alert")
    fixture_run.stage(community_frame("c1", KEYS), kind="community")
    stage_disposition(fixture_run, "account", "A0001", "confirmed", "tim")
    step = fixture_run.manifest.step("stage")
    assert step.status is StepStatus.DONE
    assert step.counts == {"accounts": 3, "alerts": 2, "communities": 1, "members": 3, "dispositions": 1}
    on_disk = type(fixture_run.manifest).load(fixture_run.paths.manifest)
    assert on_disk.step("stage").counts == step.counts


def test_staging_after_a_half_finished_push_drops_its_record(fixture_run):
    """What landed was a different push once the rows change, so the next push starts over."""
    fixture_run.manifest.push = [PushEntity(entity="Account", landed=True), PushEntity(entity="Alert", landed=False)]
    fixture_run.stage(account_frame(KEYS), kind="account")
    assert fixture_run.manifest.push == []
    done = [PushEntity(entity="Account", landed=True), PushEntity(entity="LabRun", landed=True)]
    fixture_run.manifest.push = list(done)
    stage_disposition(fixture_run, "account", "A0001", "confirmed", "tim")
    assert fixture_run.manifest.push == done  # a finished push stays on the record


def test_staging_never_touches_the_run_store(fixture_run):
    before = rows(fixture_run.store(), "MATCH (n) RETURN count(n) AS n")[0]["n"]
    fixture_run.stage(account_frame(["A0001", "A0002"]), kind="account")
    assert rows(fixture_run.store(), "MATCH (n) RETURN count(n) AS n")[0]["n"] == before
    assert rows(fixture_run.store(), "MATCH (a:Account) WHERE a.risk_score IS NOT NULL RETURN count(a) AS n")[0]["n"] == 0
    fixture_run.close()
    conn = open_store(fixture_run.paths.push_store, read_only=True)
    try:
        assert rows(conn, "MATCH (n) RETURN count(n) AS n")[0]["n"] == 2
    finally:
        conn.close()


def test_an_empty_push_store_counts_as_nothing_staged(fixture_run):
    assert not fixture_run.paths.push_store.exists()
    with pytest.raises(NothingStaged):
        fixture_run.preview_push()
