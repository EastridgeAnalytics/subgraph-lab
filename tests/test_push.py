"""Track 1e: review, push, resume, the Bolt fallback and retraction.

The unit tests run the real lab code against a real push store, with stand-ins for `relate push` (`FakeRelate`, which
prints reports in the shape wave 1 recorded) and for the master graph's Bolt session. The `live` tests run the real
`relate` against the test Neo4j, which `live_neo4j` loads with the fixture graph.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab.common.relate_cli import CliResult
from lab.common.store import rows
from lab.contracts.manifest import Route, RunManifest, StepStatus
from lab.roundtrip import ledger
from lab.roundtrip.push import OWN_CONSTRAINTS, PushNotConfirmed, apply, preview
from lab.roundtrip.retract import RetractNotConfirmed, retract
from lab.roundtrip.stage import NothingStaged, stage_disposition
from lab.runs import Run
from lab.settings import get_settings, reload_settings
from tests.push_helpers import (
    FakeRelate,
    FakeSession,
    account_frame,
    alert_frame,
    community_frame,
    customer_state,
    lab_snapshot,
    own_constraints_on_master,
    patch_master,
    wipe_lab,
)

SAMPLES = json.loads((Path(__file__).parent / "fixtures" / "relate_push_reports.json").read_text(encoding="utf-8"))
KEYS = ["A0001", "A0002", "A0003"]
ALL_ENTITIES = ["Account", "Alert", "RiskCommunity", "(Alert)-[:ALERT_ON]->(Account)",
                "(Account)-[:IN_RISK_COMMUNITY]->(RiskCommunity)", "LabRun"]


def sample(name: str) -> CliResult:
    s = SAMPLES[name]
    return CliResult(s["returncode"], s["stdout"], s["stderr"], json.loads(s["stdout"]) if s["stdout"] else None)


def stage_all(run: Run, keys: list[str] = KEYS) -> None:
    run.stage(account_frame(keys), kind="account")
    run.stage(alert_frame([(f"alert-{k}", k) for k in keys[:2]]), kind="alert")
    run.stage(community_frame("community-1", keys), kind="community")


def landed(result) -> dict[str, bool]:
    return {e.entity: e.landed for e in result.entities}


def reloaded(run: Run) -> RunManifest:
    return RunManifest.load(run.paths.manifest)


@pytest.fixture
def relate(monkeypatch) -> FakeRelate:
    fake = FakeRelate()
    monkeypatch.setattr("lab.roundtrip.push.relate_cli", fake)
    return fake


@pytest.fixture
def master(monkeypatch) -> FakeSession:
    return patch_master(monkeypatch, FakeSession())


# ---- no confirm, no push ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("confirm", [False, None, 1, "yes", "true", 0])
def test_apply_without_confirm_raises_before_anything_runs(fixture_run, monkeypatch, confirm):
    stage_all(fixture_run)
    before = fixture_run.manifest.model_dump()

    def boom(*a, **k):
        raise AssertionError("nothing may run before the confirm check")

    monkeypatch.setattr("lab.roundtrip.push.relate_cli", boom)
    monkeypatch.setattr("lab.roundtrip.push.neo4j_session", boom)
    monkeypatch.setattr("lab.roundtrip.push.credentials_file", boom)
    monkeypatch.setattr("subprocess.run", boom)
    with pytest.raises(PushNotConfirmed, match="not confirmed"):
        apply(fixture_run, confirm)
    assert fixture_run.manifest.model_dump() == before
    assert fixture_run.manifest.step("push").status is StepStatus.PENDING
    assert reloaded(fixture_run).step("push").status is StepStatus.PENDING


def test_the_facade_refuses_without_confirm_even_with_nothing_staged(fixture_run, monkeypatch):
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail("ran relate"))
    with pytest.raises(PushNotConfirmed):
        fixture_run.apply_push()
    with pytest.raises(PushNotConfirmed):
        fixture_run.apply_push(confirm=False)


def test_retract_without_confirm_raises_before_anything_runs(fixture_run, monkeypatch):
    monkeypatch.setattr("lab.roundtrip.retract.neo4j_session", lambda *a, **k: pytest.fail("opened Bolt"))
    for confirm in (False, None, 1, "yes"):
        with pytest.raises(RetractNotConfirmed):
            retract(fixture_run, confirm)


def test_nothing_staged_is_an_error_for_review_and_push(fixture_run, relate, master):
    with pytest.raises(NothingStaged):
        preview(fixture_run)
    with pytest.raises(NothingStaged):
        apply(fixture_run, confirm=True)
    assert relate.calls == [] and master.statements == []


# ---- reading relate's reports -----------------------------------------------------------------------------------
def test_parse_a_recorded_dry_run():
    r = sample("dry_run")
    plan = ledger.parse_report(r.json, batch_size=5000)
    assert [e.entity for e in plan] == ["Account", "Alert", "RiskCommunity", "(Alert)-[:ALERT_ON]->(Account)",
                                        "(Account)-[:IN_RISK_COMMUNITY]->(RiskCommunity)"]
    assert [e.kind for e in plan] == ["node", "node", "node", "relationship", "relationship"]
    assert [e.total for e in plan] == [2, 1, 1, 1, 1]
    assert [e.batches for e in plan] == [1, 1, 1, 1, 1]  # planned
    assert not any(e.landed for e in plan)  # a dry run lands nothing
    assert ledger.blockers(plan) == []


def test_parse_a_recorded_success():
    r = sample("success")
    out = ledger.parse_report(r.json, batch_size=5000, returncode=r.returncode)
    assert all(e.landed and e.error is None for e in out) and len(out) == 5
    assert out[0].entity == "Account" and out[0].created == 0 and out[0].updated == 2  # both accounts were there
    assert [e.created for e in out[1:]] == [1, 1, 1, 1]  # the alert, the community and their links are new
    again = sample("success_again")
    out = ledger.parse_report(again.json, batch_size=5000, returncode=again.returncode)
    assert all(e.landed for e in out) and sum(e.created for e in out) == 0 and sum(e.updated for e in out) == 6


def test_parse_a_recorded_failed_batch():
    r = sample("failed_first_alert")
    failure = ledger.parse_failure(r.error_line())
    assert failure.entity == "Alert" and failure.batch == 1 and failure.rows == (0, 1)
    assert "HTTP 400" in failure.message
    out = {e.entity: e for e in ledger.parse_report(r.json, batch_size=5000, returncode=1, failure=failure)}
    assert out["Account"].landed and out["Account"].updated == 2
    assert not out["Alert"].landed and "HTTP 400" in out["Alert"].error
    assert out["Alert"].created == 0  # relate printed the plan there, not what happened
    assert not any(e.landed for name, e in out.items() if name != "Account")


def test_an_entity_with_batches_left_is_not_landed():
    """Batch size 1: Alert sent one batch of three and stopped at the second."""
    r = sample("failed_second_batch")
    failure = ledger.parse_failure(r.error_line())
    assert failure.entity == "Alert" and failure.batch == 2 and failure.rows == (1, 2)
    out = {e.entity: e for e in ledger.parse_report(r.json, batch_size=1, returncode=1, failure=failure)}
    assert out["Account"].landed and out["Account"].batches == 2
    assert out["Alert"].batches == 1 and out["Alert"].total == 3 and not out["Alert"].landed
    # without the failure line, the batch count alone says the same thing
    out = {e.entity: e for e in ledger.parse_report(r.json, batch_size=1, returncode=1)}
    assert out["Account"].landed and not out["Alert"].landed and not out["RiskCommunity"].landed


def test_parse_a_pre_read_failure_and_keep_credentials_out_of_it():
    r = sample("unreachable")
    failure = ledger.parse_failure(r.error_line())
    assert failure.entity == "Account" and "pre-read of Account" in failure.message
    out = ledger.parse_report(r.json, batch_size=5000, returncode=1, failure=failure)
    assert [e.entity for e in out] == ["Account", "(dangling)"] and not any(e.landed for e in out)
    line = "Error: push failed: batch 1 of Alert (rows 0..1) failed: HTTP request to https://neo4j:hunter2@host/x failed"
    assert "hunter2" not in ledger.parse_failure(line).message
    assert ledger.parse_failure("") is None
    assert ledger.parse_failure("Error: something else").entity is None


def test_rows_relate_would_skip_block_the_push():
    report = {"dryRun": True, "entities": [
        {"entity": "Account", "kind": "node", "total": 3, "created": 3, "batches": 1,
         "skipped": {"dangling": 0, "policy": 0, "remoteNewer": 0, "unkeyed": 3}},
    ]}
    plan = ledger.parse_report(report, batch_size=5000)
    assert plan[0].skipped == 3 and ledger.blockers(plan)
    real = {**report, "dryRun": False}
    out = ledger.parse_report(real, batch_size=5000)  # a clean exit, but the rows were not sent
    assert not out[0].landed and "no merge key" in out[0].error


def test_rel_types_come_from_entity_names():
    assert ledger.rel_type_of("(Alert)-[:ALERT_ON]->(Account)") == "ALERT_ON"
    assert ledger.rel_type_of("(Account)-[:IN_RISK_COMMUNITY]->(RiskCommunity)") == "IN_RISK_COMMUNITY"
    assert ledger.rel_type_of("Account") is None


# ---- review -----------------------------------------------------------------------------------------------------
def test_preview_shows_the_plan_and_the_delta_and_writes_nothing(fixture_run, relate, master):
    stage_all(fixture_run)
    remote = {"A0001": 0.85, "A0002": None}  # A0003 is not on the master graph

    def answer(q, p):
        if "OPTIONAL MATCH" in q:
            return [{"key": k, "found": k in remote, "score": remote.get(k)} for k in p["keys"]]
        if "SHOW CONSTRAINTS" in q:
            return []
        return []

    master.answer = answer
    review = preview(fixture_run)
    assert review.run_id == fixture_run.id
    names = {e.entity: e.total for e in review.plan}
    assert names == {"Account": 3, "Alert": 2, "RiskCommunity": 1, "(Alert)-[:ALERT_ON]->(Account)": 2,
                     "(Account)-[:IN_RISK_COMMUNITY]->(RiskCommunity)": 3}
    assert not any(e.landed for e in review.plan)
    d = review.delta
    assert (d.new_alerts, d.new_communities, d.accounts_scored, d.vectors, d.dispositions) == (2, 1, 3, 4, 0)  # vectors: three accounts and one community
    assert d.sampled == 3 and d.missing_remote_keys == 1
    assert d.score_changes == {"new": 2, "0.0-0.1": 1, "0.1-0.2": 0, "0.2-0.5": 0, "0.5-1.0": 0}
    assert d.bytes_estimate > 0
    # the plan text is relate's dry run, then what the lab adds
    assert "dry run: nothing was sent" in review.plan_text
    assert "Alert.alert_key, RiskCommunity.community_key, LabRun.run_id" in review.plan_text
    assert "Warning: 1 of 3 sampled accounts are not on the master graph" in review.plan_text
    # two dry runs (json and text), both labelled, nothing real, and the master graph only read
    assert len(relate.dry_runs) == 2 and relate.real_pushes == []
    assert all("--labels" in c and c[c.index("--labels") + 1] == "Account,Alert,RiskCommunity" for c in relate.dry_runs)
    assert master.writes() == []
    step = reloaded(fixture_run).step("review")
    assert step.status is StepStatus.DONE and step.counts["entities"] == 5 and step.counts["new_alerts"] == 2
    assert reloaded(fixture_run).step("push").status is StepStatus.PENDING


def test_preview_says_what_a_stopped_push_would_resume(fixture_run, relate, master):
    stage_all(fixture_run)
    relate.fail_at = "Alert"
    apply(fixture_run, confirm=True)
    relate.fail_at = None
    review = preview(fixture_run)
    assert "An earlier push of this run stopped. Landed: Account. Not landed: Alert" in review.plan_text
    assert [e.landed for e in fixture_run.manifest.push][:2] == [True, False]  # a review does not touch the record


# ---- push -------------------------------------------------------------------------------------------------------
def test_apply_pushes_in_order_and_writes_labrun_last(fixture_run, relate, master):
    stage_all(fixture_run)
    result = apply(fixture_run, confirm=True)
    assert result.pushed and not result.resumed and result.run_id == fixture_run.id
    assert list(landed(result)) == ALL_ENTITIES and all(landed(result).values())
    assert "by relate" in result.message and "LabRun" in result.message

    # a dry run first, then the lab's own constraints, then one push of every label, then LabRun alone
    assert len(relate.dry_runs) == 1 and len(relate.real_pushes) == 2
    first, last = relate.real_pushes
    assert first[first.index("--labels") + 1] == "Account,Alert,RiskCommunity"
    assert last[last.index("--labels") + 1] == "LabRun"
    assert "--batch-size" in first and first[first.index("--batch-size") + 1] == "5000"
    assert all("--dry-run" not in c for c in relate.real_pushes)
    assert relate.calls.index(relate.dry_runs[0]) < relate.calls.index(first)
    # the push read the push store, never the run store (decision 22)
    assert all(c[c.index("--database") + 1].endswith("push") for c in relate.calls)

    # the constraints: the lab's own labels only, IF NOT EXISTS, and they came before any push
    creates = [q for q, _ in master.statements if q.startswith("CREATE")]
    assert creates == [
        "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Alert) REQUIRE n.alert_key IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (n:RiskCommunity) REQUIRE n.community_key IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (n:LabRun) REQUIRE n.run_id IS UNIQUE",
    ]
    assert len(master.writes()) == 3  # nothing else was written over Bolt: relate carried the rows

    m = reloaded(fixture_run)
    assert m.step("push").status is StepStatus.DONE and m.status is StepStatus.DONE and m.finished_at
    assert m.routes["push"] is Route.RELATE and m.fallbacks == []
    assert [e.entity for e in m.push] == ALL_ENTITIES and all(e.landed for e in m.push)
    assert m.step("push").counts["resumed"] == 0
    assert m.step("push").counts["reviewed"] == 0 and "no review on record" in m.step("push").note

    lab = rows(fixture_run.push_store(), "MATCH (l:LabRun) RETURN properties(l) AS p")[0]["p"]
    assert lab["run_id"] == fixture_run.id and lab["recipe_hash"] == m.recipe_hash and lab["route_push"] == "relate"
    assert lab["started_at"].endswith("Z") and lab["finished_at"].endswith("Z")
    counts = json.loads(lab["counts"])
    assert counts["accounts_scored"] == 3 and counts["alerts"] == 2 and counts["communities"] == 1
    assert counts["vectors"] == 4  # three account embeddings and one community embedding


def test_the_manifest_says_whether_a_review_came_first(fixture_run, relate, master):
    stage_all(fixture_run)
    preview(fixture_run)
    apply(fixture_run, confirm=True)
    step = reloaded(fixture_run).step("push")
    assert step.counts["reviewed"] == 1 and "review on record" in step.note and "no review" not in step.note


def test_own_constraints_are_the_only_ones_the_lab_creates():
    assert OWN_CONSTRAINTS == (("Alert", "alert_key"), ("RiskCommunity", "community_key"), ("LabRun", "run_id"))


def test_a_failed_batch_is_recorded_and_the_next_apply_resumes(fixture_run, relate, master):
    stage_all(fixture_run)
    relate.fail_at = "Alert"
    result = apply(fixture_run, confirm=True)
    assert not result.pushed and not result.resumed
    assert landed(result) == {"Account": True, "Alert": False, "RiskCommunity": False,
                              "(Alert)-[:ALERT_ON]->(Account)": False,
                              "(Account)-[:IN_RISK_COMMUNITY]->(RiskCommunity)": False, "LabRun": False}
    assert "HTTP 400" in result.message and "Landed: Account." in result.message and "resume" in result.message
    alert = next(e for e in result.entities if e.entity == "Alert")
    assert "HTTP 400" in alert.error
    m = reloaded(fixture_run)
    assert m.step("push").status is StepStatus.FAILED and "HTTP 400" in m.step("push").note
    assert m.fallbacks == []  # a bad request is not an unreachable server: no Bolt
    assert len(relate.real_pushes) == 1  # LabRun was not pushed
    assert [e.landed for e in m.push] == [True, False, False, False, False, False]

    relate.fail_at = None  # the property is fixed
    result = apply(fixture_run, confirm=True)
    assert result.pushed and result.resumed
    assert all(landed(result).values()) and list(landed(result)) == ALL_ENTITIES
    # only what had not landed was sent: Alert and RiskCommunity, then their links, then LabRun
    resume = relate.real_pushes[1:]
    assert [c[c.index("--labels") + 1] if "--labels" in c else "types:" + c[c.index("--types") + 1] for c in resume] == [
        "Alert,RiskCommunity", "types:ALERT_ON,IN_RISK_COMMUNITY", "LabRun"]
    assert "Resumed" in result.message
    m = reloaded(fixture_run)
    assert m.step("push").status is StepStatus.DONE and m.status is StepStatus.DONE and m.step("push").counts["resumed"] == 1


def test_a_resume_after_labrun_alone_failed_sends_only_labrun(fixture_run, relate, master):
    stage_all(fixture_run)
    relate.fail_at = "LabRun"
    result = apply(fixture_run, confirm=True)
    assert not result.pushed
    assert landed(result)["Account"] and landed(result)["(Alert)-[:ALERT_ON]->(Account)"] and not landed(result)["LabRun"]
    relate.fail_at = None
    result = apply(fixture_run, confirm=True)
    assert result.pushed and result.resumed
    pushes = relate.real_pushes
    assert [c[c.index("--labels") + 1] for c in pushes[-1:]] == ["LabRun"] and len(pushes) == 3  # fresh, LabRun, LabRun


def test_staging_between_a_failure_and_a_retry_starts_the_push_over(fixture_run, relate, master):
    stage_all(fixture_run)
    relate.fail_at = "Alert"
    apply(fixture_run, confirm=True)
    stage_disposition(fixture_run, "account", "A0001", "confirmed", "tim")  # rows changed: what landed is stale
    relate.fail_at = None
    result = apply(fixture_run, confirm=True)
    assert result.pushed and not result.resumed
    assert relate.real_pushes[-2][relate.real_pushes[-2].index("--labels") + 1] == "Account,Alert,RiskCommunity"


def test_pushing_the_same_run_twice_is_a_full_push_that_keeps_labrun_as_it_was(fixture_run, relate, master):
    stage_all(fixture_run)
    first = apply(fixture_run, confirm=True)
    lab1 = rows(fixture_run.push_store(), "MATCH (l:LabRun) RETURN properties(l) AS p")[0]["p"]
    second = apply(fixture_run, confirm=True)
    lab2 = rows(fixture_run.push_store(), "MATCH (l:LabRun) RETURN properties(l) AS p")[0]["p"]
    assert first.pushed and second.pushed and not second.resumed
    assert lab1 == lab2  # same counts, same hashes: finished_at stays
    assert len(relate.real_pushes) == 4
    # new rows staged after the first push change the counts, so LabRun gets a new finished_at
    stage_disposition(fixture_run, "account", "A0001", "confirmed", "tim")
    apply(fixture_run, confirm=True)
    lab3 = rows(fixture_run.push_store(), "MATCH (l:LabRun) RETURN properties(l) AS p")[0]["p"]
    assert json.loads(lab3["counts"])["dispositions"] == 1 and lab3["counts"] != lab1["counts"]


def test_a_plan_that_skips_rows_stops_before_anything_is_written(fixture_run, relate, master, monkeypatch):
    stage_all(fixture_run)
    original = relate._entities

    def unkeyed(args):
        out = original(args)
        out[0]["skipped"]["unkeyed"] = out[0]["total"]
        return out

    monkeypatch.setattr(relate, "_entities", unkeyed)
    result = apply(fixture_run, confirm=True)
    assert not result.pushed and "would skip" in result.message
    assert master.statements == [] and relate.real_pushes == []


def test_apply_never_raises_past_the_api_once_confirmed(fixture_run, monkeypatch, master):
    stage_all(fixture_run)

    def boom(*a, **k):
        raise FileNotFoundError("relate binary 'relate' not found")

    monkeypatch.setattr("lab.roundtrip.push.relate_cli", boom)
    result = apply(fixture_run, confirm=True)
    assert not result.pushed and "not found" in result.message
    assert reloaded(fixture_run).step("push").status is StepStatus.FAILED


def test_the_master_graph_unreachable_over_bolt_is_a_failed_push(fixture_run, relate, monkeypatch):
    stage_all(fixture_run)

    class Down(FakeSession):
        def execute_read(self, fn, *a):
            raise ConnectionError("refused")

    patch_master(monkeypatch, Down())
    result = apply(fixture_run, confirm=True)
    assert not result.pushed and "could not make the lab's own uniqueness constraints" in result.message
    assert relate.real_pushes == []  # nothing went to the master graph


# ---- the Bolt fallback -------------------------------------------------------------------------------------------
def test_an_unreachable_query_api_falls_back_to_bolt_with_the_reason(fixture_run, relate, master, monkeypatch):
    monkeypatch.setattr(get_settings(), "push_batch", 2)
    stage_all(fixture_run)
    relate.unreachable = True
    result = apply(fixture_run, confirm=True)
    assert result.pushed and all(landed(result).values()) and list(landed(result)) == ALL_ENTITIES
    m = reloaded(fixture_run)
    assert m.routes["push"] is Route.BOLT
    assert len(m.fallbacks) == 1 and m.fallbacks[0].step == "push" and m.fallbacks[0].route is Route.BOLT
    assert "Query API unreachable" in m.fallbacks[0].reason and "connection refused" in m.fallbacks[0].reason
    assert "by bolt" in result.message

    writes = [(q, p) for q, p in master.writes() if not q.startswith("CREATE CONSTRAINT")]
    merges = [q for q, _ in writes]
    # keyed, UNWIND ... MERGE ... SET +=, batches of push_batch (2), in the order of the push, LabRun last
    assert merges[0].startswith("UNWIND $rows AS r MERGE (n:Account {account_id: r.key}) SET n += r.props")
    assert all(len(p["rows"]) <= 2 for _, p in writes if "rows" in p)
    account_batches = [p for q, p in writes if "(n:Account" in q]
    assert [len(p["rows"]) for p in account_batches] == [2, 1]
    assert {r["key"] for p in account_batches for r in p["rows"]} == set(KEYS)
    row = account_batches[0]["rows"][0]
    assert row["props"]["risk_score"] == 0.9 and "account_id" not in row["props"]
    patterns = [("LabRun", "MERGE (l:LabRun"), ("ALERT_ON", "-[:ALERT_ON]->"), ("IN_RISK_COMMUNITY", "-[:IN_RISK_COMMUNITY]->"),
                ("Account", "MERGE (n:Account"), ("Alert", "MERGE (n:Alert"), ("RiskCommunity", "MERGE (n:RiskCommunity")]
    order = [next(name for name, text in patterns if text in q) for q in merges]
    assert order[0] == "Account" and order[-1] == "LabRun"
    assert order.index("Alert") < order.index("ALERT_ON") and order.index("RiskCommunity") < order.index("IN_RISK_COMMUNITY")
    assert merges[-1].startswith("MERGE (l:LabRun {run_id: $id}) SET l += $props")
    assert writes[-1][1]["props"]["route_push"] == "bolt"
    assert "ALERT_ON" in " ".join(merges) and len(relate.real_pushes) == 1  # one relate attempt, then Bolt


def test_a_bad_request_does_not_fall_back(fixture_run, relate, master):
    stage_all(fixture_run)
    relate.fail_at = "Account"
    result = apply(fixture_run, confirm=True)
    assert not result.pushed
    assert reloaded(fixture_run).fallbacks == []
    assert [q for q, _ in master.writes() if not q.startswith("CREATE CONSTRAINT")] == []


# ---- retract ----------------------------------------------------------------------------------------------------
def test_retract_deletes_only_lab_nodes_and_removes_only_risk_properties(fixture_run, relate, master, monkeypatch):
    stage_all(fixture_run)
    apply(fixture_run, confirm=True)
    session = FakeSession(lambda q, p: (
        [{"k": k} for k in KEYS] if "RETURN a.account_id AS k" in q
        else [{"n": "risk_score"}, {"n": "risk_run_id"}] if "STARTS WITH $prefix" in q
        else [{"c": 0}]
    ))
    patch_master(monkeypatch, session)
    result = retract(fixture_run, confirm=True)
    assert result.accounts_cleaned == 3 and result.properties == ["risk_run_id", "risk_score"]
    statements = [q for q, _ in session.writes()]
    removes = [q for q in statements if "REMOVE" in q]
    assert removes == ["UNWIND $keys AS k MATCH (a:Account {account_id: k}) REMOVE a.risk_run_id, a.risk_score"]
    deletes = [q for q in statements if "DELETE" in q]
    assert len(deletes) == 3 and all("DETACH DELETE n" in q and "n.run_id = $run" in q for q in deletes)
    assert {q.split("(n:")[1].split(")")[0] for q in deletes} == {"Alert", "RiskCommunity", "LabRun"}  # no customer label
    assert all(p.get("run") == fixture_run.id for q, p in session.statements if "$run" in q)
    m = reloaded(fixture_run)
    assert m.push == [] and "push" not in m.routes and m.step("push").status is StepStatus.PENDING
    assert "retracted" in m.step("push").note


# ---- live: the real relate against the test Neo4j ------------------------------------------------------------------
@pytest.fixture
def lab(live_neo4j, data_dir):
    """A run to push, and a master graph that holds nothing of the lab's before and after."""
    from lab.contracts.recipe import Recipe

    wipe_lab()
    run = Run.create(Recipe(), data_dir=data_dir)
    yield run
    run.close()
    wipe_lab()


def master_keys() -> list[str]:
    from lab.common.neo4j import master_rows

    return [r["k"] for r in master_rows("MATCH (a:Account) RETURN a.account_id AS k ORDER BY k LIMIT 4")]


@pytest.mark.live
def test_live_stage_preview_apply(lab):
    keys = master_keys()
    stage_all(lab, keys[:3])
    stage_disposition(lab, "account", keys[0], "confirmed", "tim")
    before_customer, before_lab = customer_state(), lab_snapshot()

    review = preview(lab)
    assert {e.entity: e.total for e in review.plan} == {
        "Account": 3, "Alert": 2, "RiskCommunity": 1, "(Alert)-[:ALERT_ON]->(Account)": 2,
        "(Account)-[:IN_RISK_COMMUNITY]->(RiskCommunity)": 3}
    d = review.delta
    assert (d.new_alerts, d.new_communities, d.accounts_scored, d.dispositions) == (2, 1, 3, 1)
    assert d.vectors == 4 and d.sampled == 3 and d.missing_remote_keys == 0 and d.bytes_estimate > 0
    assert d.score_changes["new"] == 3 and sum(d.score_changes.values()) == 3
    assert "UNWIND $rows AS row MERGE" in review.plan_text and "dry run" in review.plan_text
    assert "Alert.alert_key, RiskCommunity.community_key, LabRun.run_id" in review.plan_text
    # the master graph is exactly as it was: no rows, no constraints
    assert customer_state() == before_customer and lab_snapshot() == before_lab
    assert own_constraints_on_master() == set()

    result = apply(lab, confirm=True)
    assert result.pushed and not result.resumed and all(landed(result).values())
    assert list(landed(result)) == ALL_ENTITIES
    snap = lab_snapshot()
    assert len(snap["Alert"]) == 2 and len(snap["RiskCommunity"]) == 1 and len(snap["LabRun"]) == 1
    assert snap["ALERT_ON"] == 2 and snap["IN_RISK_COMMUNITY"] == 3
    labrun = snap["LabRun"][0]
    assert labrun["run_id"] == lab.id and labrun["route_push"] == "relate" and labrun["recipe_hash"]
    assert json.loads(labrun["counts"])["accounts_scored"] == 3
    assert set(snap["risk"]) == set(keys[:3])
    first = snap["risk"][keys[0]]
    assert first["risk_run_id"] == lab.id and first["risk_score"] == 0.9 and first["risk_disposition"] == "confirmed"
    assert first["risk_disposition_by"] == "tim" and first["risk_embedding"] == [0.1, 0.1, 0.1]
    assert first["risk_community_key"] == "community-1"
    assert own_constraints_on_master() == set(OWN_CONSTRAINTS)
    # customer nodes and relationships are untouched
    assert customer_state() == before_customer
    # the delta now knows the alerts and the community are there
    after = preview(lab).delta
    assert (after.new_alerts, after.new_communities) == (0, 0)
    assert after.score_changes["0.0-0.1"] == 3 and after.score_changes["new"] == 0


@pytest.mark.live
def test_live_a3_pushing_the_same_run_twice_changes_nothing(lab):
    keys = master_keys()
    stage_all(lab, keys[:3])
    first = apply(lab, confirm=True)
    snap, customer = lab_snapshot(), customer_state()
    second = apply(lab, confirm=True)
    assert first.pushed and second.pushed and not second.resumed
    assert lab_snapshot() == snap and customer_state() == customer
    assert sum(e.created for e in second.entities) == 0  # relate merged every row into what was there


@pytest.mark.live
def test_live_a6_a_failing_batch_resumes_and_finishes(lab):
    keys = master_keys()
    stage_all(lab, keys[:3])
    # the known failure: a zoned datetime in the push store makes relate 1.0.1 fail with HTTP 400
    lab.push_store().execute(
        "MATCH (a:Alert {alert_key: $k}) SET a.created_at = datetime('2026-11-12T00:00:00Z')", {"k": f"alert-{keys[0]}"})
    result = apply(lab, confirm=True)
    assert not result.pushed and not result.resumed
    assert landed(result)["Account"] and not landed(result)["Alert"] and not landed(result)["LabRun"]
    assert "HTTP 400" in result.message
    m = reloaded(lab)
    assert m.step("push").status is StepStatus.FAILED and m.fallbacks == []
    snap = lab_snapshot()
    assert set(snap["risk"]) == set(keys[:3]) and snap["Alert"] == [] and snap["LabRun"] == []  # accounts landed first

    lab.push_store().execute(
        "MATCH (a:Alert {alert_key: $k}) SET a.created_at = '2026-11-12T00:00:00Z'", {"k": f"alert-{keys[0]}"})
    result = apply(lab, confirm=True)
    assert result.pushed and result.resumed and all(landed(result).values())
    snap = lab_snapshot()
    assert len(snap["Alert"]) == 2 and len(snap["RiskCommunity"]) == 1 and len(snap["LabRun"]) == 1
    assert snap["ALERT_ON"] == 2 and snap["IN_RISK_COMMUNITY"] == 3
    assert reloaded(lab).step("push").status is StepStatus.DONE


@pytest.mark.live
def test_live_a5_part_confirm_is_required_and_retract_removes_exactly_the_run(lab):
    from lab.common.neo4j import neo4j_session

    keys = master_keys()
    other = keys[3]
    with neo4j_session() as s:  # another run's results, which retract must leave alone
        s.run("MATCH (a:Account {account_id: $k}) SET a.risk_run_id = 'run-other', a.risk_score = 0.123", k=other).consume()
        s.run("CREATE (:Alert {alert_key: 'other-alert', run_id: 'run-other'})").consume()
    customer = customer_state()
    stage_all(lab, keys[:3])
    with pytest.raises(PushNotConfirmed):
        apply(lab, confirm=False)
    with pytest.raises(PushNotConfirmed):
        apply(lab)
    assert len(lab_snapshot()["risk"]) == 1 and lab_snapshot()["LabRun"] == []  # only the other run's, nothing of ours

    apply(lab, confirm=True)
    assert len(lab_snapshot()["risk"]) == 4
    with pytest.raises(RetractNotConfirmed):
        retract(lab)
    assert len(lab_snapshot()["Alert"]) == 3  # still there

    result = retract(lab, confirm=True)
    assert (result.alerts, result.communities, result.lab_runs, result.accounts_cleaned) == (2, 1, 1, 3)
    assert "risk_score" in result.properties and "risk_embedding" in result.properties
    snap = lab_snapshot()
    assert snap["risk"] == {other: {"risk_run_id": "run-other", "risk_score": 0.123}}
    assert [a["alert_key"] for a in snap["Alert"]] == ["other-alert"]
    assert snap["RiskCommunity"] == [] and snap["LabRun"] == [] and snap["ALERT_ON"] == 0 and snap["IN_RISK_COMMUNITY"] == 0
    assert customer_state() == customer  # every customer node and relationship, property for property
    m = reloaded(lab)
    assert m.push == [] and m.step("push").status is StepStatus.PENDING
    # and the run can push again
    assert apply(lab, confirm=True).pushed and len(lab_snapshot()["Alert"]) == 3


@pytest.mark.live
def test_live_an_unreachable_query_api_pushes_over_bolt(lab):
    import os

    keys = master_keys()
    stage_all(lab, keys[:3])
    original = os.environ["NEO4J_QUERY_URI"]
    os.environ["NEO4J_QUERY_URI"] = "http://localhost:1"  # Bolt still works; the Query API does not answer
    reload_settings()
    try:
        result = apply(lab, confirm=True)
    finally:
        os.environ["NEO4J_QUERY_URI"] = original
        reload_settings()
    assert result.pushed and all(landed(result).values())
    m = reloaded(lab)
    assert m.routes["push"] is Route.BOLT and m.fallbacks[0].step == "push"
    assert "Query API unreachable" in m.fallbacks[0].reason
    snap = lab_snapshot()
    assert len(snap["Alert"]) == 2 and len(snap["RiskCommunity"]) == 1 and snap["LabRun"][0]["run_id"] == lab.id
    assert snap["LabRun"][0]["route_push"] == "bolt" and snap["ALERT_ON"] == 2 and snap["IN_RISK_COMMUNITY"] == 3
    assert set(snap["risk"]) == set(keys[:3]) and snap["risk"][keys[0]]["risk_embedding"] == [0.1, 0.1, 0.1]
    # the same rows relate would have sent: a push over relate afterwards changes nothing but the route on LabRun
    assert apply(lab, confirm=True).pushed and reloaded(lab).routes["push"] is Route.RELATE
    again = lab_snapshot()
    assert again["Alert"] == snap["Alert"] and again["RiskCommunity"] == snap["RiskCommunity"]
    assert again["risk"] == snap["risk"] and again["ALERT_ON"] == 2 and again["IN_RISK_COMMUNITY"] == 3
    assert again["LabRun"][0]["route_push"] == "relate"


@pytest.mark.live
def test_live_scores_alone_and_a_disposition_alone_push_without_alerts_or_communities(lab):
    keys = master_keys()
    lab.stage(account_frame(keys[:2]), kind="account")
    review = preview(lab)
    assert [e.entity for e in review.plan] == ["Account"] and review.delta.new_alerts == 0
    result = apply(lab, confirm=True)
    assert result.pushed and [e.entity for e in result.entities] == ["Account", "LabRun"] and all(landed(result).values())
    assert set(lab_snapshot()["risk"]) == set(keys[:2]) and lab_snapshot()["Alert"] == []

    stage_disposition(lab, "account", keys[0], "false_positive", "laura")
    stage_disposition(lab, "account", keys[3], "needs_more", "laura")  # never scored by this run
    result = apply(lab, confirm=True)
    assert result.pushed and not result.resumed
    risk = lab_snapshot()["risk"]
    assert risk[keys[0]]["risk_disposition"] == "false_positive" and risk[keys[0]]["risk_score"] == 0.9
    assert risk[keys[3]] == {k: v for k, v in risk[keys[3]].items() if k.startswith("risk_disposition")}
    assert risk[keys[3]]["risk_disposition_by"] == "laura"


@pytest.mark.live
def test_live_a_key_the_master_graph_lacks_is_flagged_by_the_review_and_created_bare_by_the_push(lab):
    """Known behaviour, pinned: a keyed merge creates a node it cannot find. The review says so beforehand."""
    from lab.common.neo4j import neo4j_session

    keys = master_keys()
    lab.stage(account_frame([keys[0], "ZZ-ghost"]), kind="account")
    review = preview(lab)
    assert review.delta.missing_remote_keys == 1
    assert "Warning: 1 of 2 sampled accounts are not on the master graph" in review.plan_text
    apply(lab, confirm=True)
    with neo4j_session() as s:
        ghost = s.run("MATCH (a:Account {account_id: 'ZZ-ghost'}) RETURN properties(a) AS p").single()["p"]
    assert set(ghost) == {"account_id", "risk_score", "risk_score_model", "risk_factors", "risk_embedding", "risk_run_id",
                          "risk_at"}
