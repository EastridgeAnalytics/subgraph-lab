"""The acceptance tests A1 to A11 (page 88), each as one runnable check.

    uv run pytest tests/acceptance                     the ones that run on the fixture
    uv run pytest tests/acceptance -m live             the ones that need the test Neo4j (compose --profile test)
    uv run python tests/acceptance/a1_walk.py --dir CLONE --record   A1, the timed walk from compose up (a script)
    uv run python tests/acceptance/check_measured.py                  A8, also run here as a test
    uv run python tests/acceptance/loop.py                            the whole loop, after every merge

A test here checks the requirement end to end with the real components. Where a narrower test of the same behaviour
lives beside its module, this one does not replace it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import polars as pl
import pytest

from lab.contracts.recipe import Recipe, SearchSpec
from lab.runs import Run
from lab.settings import reload_settings

REPO = Path(__file__).resolve().parents[2]
R1 = [f"A{i:04d}" for i in range(11, 17)]
R2 = [f"A{i:04d}" for i in range(101, 109)]


def scored(data_dir, fixture_store_path, recipe: Recipe | None = None) -> Run:
    """A fixture run after the first pass and the scoring: what a reviewer opens."""
    from lab.roundtrip.indexes import create_indexes

    run = Run.create(recipe or Recipe(models=["node2vec", "communities"]), data_dir=data_dir)
    shutil.copytree(fixture_store_path, run.paths.store)
    run.manifest.finish("pull", {})
    run.save()
    create_indexes(run.store(), mapping=run.recipe.graph)
    run.first_pass()
    run.score()
    return run


# ---- A2 · every push follows a dry run the user saw (decision 37) ----------------------------------------------------
@pytest.mark.live
def test_a2_every_push_follows_a_dry_run_the_user_saw(live_neo4j, data_dir):
    from fastapi.testclient import TestClient

    from lab.api.app import create_app
    from lab.api.ops import LabState
    from lab.api.worker import Worker
    from tests.push_helpers import account_frame, wipe_lab

    wipe_lab()
    run = Run.create(Recipe(), data_dir=data_dir)
    run.stage(account_frame(["A0001", "A0002", "A0003"]), kind="account")
    run.close()
    with TestClient(create_app(state=LabState(worker=Worker()), supervise=False), base_url="http://127.0.0.1") as c:
        refused = c.post(f"/api/runs/{run.id}/push", json={"confirm": True})
        assert refused.status_code == 409 and "review" in refused.json()["detail"]
        assert c.get(f"/api/runs/{run.id}/review").status_code == 200
        pushed = c.post(f"/api/runs/{run.id}/push", json={"confirm": True})
        assert pushed.status_code == 200 and pushed.json()["pushed"]
    wipe_lab()


# ---- A3 · pushing the same run twice changes nothing the second time -------------------------------------------------
@pytest.mark.live
def test_a3_the_same_push_twice_changes_nothing(live_neo4j, data_dir):
    from lab.roundtrip.push import apply
    from tests.push_helpers import account_frame, alert_frame, lab_snapshot, wipe_lab

    wipe_lab()
    run = Run.create(Recipe(), data_dir=data_dir)
    run.stage(account_frame(["A0011", "A0012"]), kind="account")
    run.stage(alert_frame([("al-1", "A0011")]), kind="alert")
    assert apply(run, confirm=True).pushed
    first = lab_snapshot()
    assert apply(run, confirm=True).pushed
    assert lab_snapshot() == first
    run.close()
    wipe_lab()


# ---- A4 · a search slice keeps to k hits and h hops, with the estimate first ------------------------------------------
def test_a4_a_search_slice_keeps_to_k_and_h_with_the_estimate_first(data_dir, fixture_store_path):
    from lab.contracts.api import SliceRequest
    from lab.roundtrip.indexes import create_indexes
    from lab.slice.estimate import estimate
    from lab.slice.search import current_slice

    run = Run.create(Recipe(), data_dir=data_dir)
    shutil.copytree(fixture_store_path, run.paths.store)
    create_indexes(run.store(), mapping=run.recipe.graph)
    run.first_pass()
    spec = SearchSpec(text="money in and out within a day across new accounts", k=5, hops=1)
    est = estimate(SliceRequest(run_id=run.id, search=spec))
    assert est.mode == "search" and 0 < len(est.hits) <= 5 and est.hops == 1
    assert est.nodes > 0 and est.memory_bytes > 0 and est.over_cap is False
    keys = current_slice(run)  # what the models will get, saved by the estimate
    assert keys and set(h.key for h in est.hits) <= set(keys)
    run.close()


# ---- A5 · an agent runs the loop through the tools; apply fails without confirm --------------------------------------
@pytest.mark.live
def test_a5_an_agent_runs_the_loop_and_apply_needs_confirm(live_neo4j, data_dir):
    """The functions the MCP tools call, in the order an agent calls them. tests/test_mcp.py runs the same over the
    MCP transport."""
    from lab.api import ops
    from lab.api.worker import Worker
    from lab.common.neo4j import master_rows
    from lab.contracts.manifest import StepStatus
    from lab.contracts.tools import EgressIn, SearchSliceIn
    from lab.roundtrip.push import PushNotConfirmed
    from tests.push_helpers import wipe_lab

    wipe_lab()
    state = ops.LabState(worker=Worker())
    state.worker.start()
    try:
        recipe = Recipe(name="a5", models=["node2vec", "communities"])
        out = ops.egress(state, EgressIn(recipe=recipe))
        m = ops.run_status(out.run_id, wait_seconds=240)
        assert m.step("first_pass").status is StepStatus.DONE, m.step("first_pass").note
        spec = SearchSpec(text="money in and out within a day across new accounts", k=10, hops=1)
        assert ops.search_slice(state, SearchSliceIn(run_id=out.run_id, search=spec)).hits
        ops.score(state, out.run_id)
        m = ops.run_status(out.run_id, wait_seconds=240)
        assert m.step("score").status is StepStatus.DONE, m.step("score").note
        assert ops.review(state, out.run_id).plan
        with pytest.raises(PushNotConfirmed):
            ops.push(state, out.run_id, confirm=False)
        assert ops.push(state, out.run_id, confirm=True).pushed
        assert master_rows("MATCH (r:LabRun {run_id: $id}) RETURN count(r) AS n", {"id": out.run_id})[0]["n"] == 1
    finally:
        state.worker.stop()
        wipe_lab()


# ---- A6 · a push with a failing batch resumes and finishes -----------------------------------------------------------
@pytest.mark.live
def test_a6_a_failed_batch_resumes_and_finishes(live_neo4j, data_dir):
    from lab.common.neo4j import master_rows
    from lab.roundtrip.push import apply
    from tests.push_helpers import account_frame, alert_frame, wipe_lab

    wipe_lab()
    run = Run.create(Recipe(), data_dir=data_dir)
    run.stage(account_frame(["A0011", "A0012"]), kind="account")
    run.stage(alert_frame([("al-1", "A0011"), ("al-2", "A0012")]), kind="alert")
    run.push_store().execute("MATCH (a:Alert {alert_key: 'al-2'}) SET a.broken = datetime()")  # Relate's known 400
    run.close()
    first = apply(run, confirm=True)
    assert first.pushed is False and any(not e.landed for e in first.entities)
    run.push_store().execute("MATCH (a:Alert {alert_key: 'al-2'}) SET a.broken = toString(a.broken)")
    run.close()
    second = apply(run, confirm=True)
    assert second.pushed and second.resumed
    assert master_rows("MATCH (a:Alert) WHERE a.alert_key IN ['al-1', 'al-2'] RETURN count(a) AS n")[0]["n"] == 2
    run.close()
    wipe_lab()


# ---- A7 · the small seed fits Aura Free --------------------------------------------------------------------------------
AURA_FREE_SMALLER = {"nodes": 50_000, "relationships": 175_000}  # the product page; the docs say 200k and 400k


def test_a7_the_small_seed_fits_aura_frees_smaller_published_limit(tmp_path):
    subprocess.run([sys.executable, "-m", "generator", "--size", "small", "--out", str(tmp_path)], check=True,
                   cwd=REPO, env={**os.environ, "DO_NOT_TRACK": "1"}, capture_output=True)
    counts = json.loads((tmp_path / "generator.json").read_text())["counts"]
    nodes, rels = counts["nodes"]["total"], counts["relationships"]["total"]
    assert nodes < AURA_FREE_SMALLER["nodes"] and rels < AURA_FREE_SMALLER["relationships"]


@pytest.mark.skipif(not os.environ.get("LAB_AURA_ACCEPTANCE"),
                    reason="needs an empty Aura Free instance in .env and LAB_AURA_ACCEPTANCE=1")
def test_a7_the_small_seed_loads_into_aura_free():  # pragma: no cover (runs when .env points at Aura)
    """`python -m generator.load` fills the Aura instance in .env with the small seed, the counts match
    generator.json, and a second run creates nothing (decision 52). It never wipes: an instance that holds anything
    but this seed is refused, so start from an empty one."""
    s = reload_settings()
    assert ".databases.neo4j.io" in s.neo4j_uri, "A7 needs NEO4J_URI in .env to be an Aura address"
    env = {**os.environ, "DO_NOT_TRACK": "1"}

    def load() -> dict:
        done = subprocess.run([sys.executable, "-m", "generator.load", "--size", "small"], cwd=REPO, env=env,
                              capture_output=True, text=True, timeout=1800)
        assert done.returncode == 0, done.stderr[-2000:]
        return json.loads(done.stdout.strip().splitlines()[-1].removeprefix("load: "))

    first = load()
    assert first["mismatch"] == {}
    assert sum(first["counts"].values()) == 39_565 + 150_000
    again = load()
    assert again["state"] == "seed only" and again["created"] == {"nodes": 0, "relationships": 0}


# ---- A8 · every number on the measured page names its run -------------------------------------------------------------
def test_a8_every_number_on_the_measured_page_names_its_run():
    from tests.acceptance.check_measured import PAGE, check

    if not PAGE.exists():
        pytest.skip("the measured page is written in wave 6")
    assert check() == []


def test_a8_the_check_catches_a_number_with_no_record_and_a_number_its_record_lacks(tmp_path, monkeypatch):
    from tests.acceptance import check_measured

    records = tmp_path / "measured"
    records.mkdir()
    (records / "20261115-pull-x.json").write_text(json.dumps({"runs": ["run-1"], "shown": {"time": "57 s"}}))
    monkeypatch.setattr(check_measured, "RECORDS", records)
    page = tmp_path / "98.md"
    page.write_text("| What | Value | Record |\n|---|---|---|\n| Pull at 100k | 57 s | `20261115-pull-x` |\n"
                    "| Push | 12 s | `20261115-pull-x` |\n| Memory | 3 GB | |\n| Aura | waiting for credentials | |\n",
                    encoding="utf-8")
    problems = check_measured.check(page)
    assert len(problems) == 2 and problems[0].startswith("Push: ['12']") and problems[1].startswith("Memory: numbers")


# ---- A9 · a summary with a planted number falls back to the template --------------------------------------------------
def test_a9_a_planted_number_sends_the_summary_to_the_template(data_dir, fixture_store_path):
    from lab.summaries import describe, read_evidence

    run = scored(data_dir, fixture_store_path)
    community = pl.read_parquet(run.paths.artifacts / "communities.parquet").sort("score", descending=True)
    key = community["community_key"][0]
    ev = read_evidence(run, key)
    assert ev is not None
    planted = (f"This community has {ev.member_count} accounts and moved 123,456,789.01 between them. "
               "A pattern like this could indicate accounts run together. "
               "An innocent explanation to rule out first is a household.")
    summary = describe(run, key, planted)
    assert summary.source == "template" and summary.failed_reason and "123,456,789.01" not in summary.text
    run.close()


# ---- A10 · a factor added to the YAML shows up on the next run, with no code change -----------------------------------
def test_a10_a_new_factor_needs_no_code_change(data_dir, fixture_store_path, tmp_path, monkeypatch):
    from lab.scoring.factors import run_factors

    factors = tmp_path / "factors"
    shutil.copytree(REPO / "factors", factors)
    (factors / "busy_device.cypher").write_text(
        "MATCH (a:Account)-[:USES_DEVICE]->(d:Device)<-[:USES_DEVICE]-(o:Account)\n"
        "WHERE $keys IS NULL OR a.account_id IN $keys\n"
        "WITH a, d, count(DISTINCT o) AS others WHERE others >= $threshold\n"
        "RETURN a.account_id AS key, others AS value, [d.device_id] AS evidence_ids\n", encoding="utf-8")
    with (factors / "factors.yaml").open("a", encoding="utf-8") as f:
        f.write("\n- id: busy_device\n  name: A device shared with three or more others\n  kind: cypher\n"
                "  query: busy_device.cypher\n  threshold: 3\n  weight: 0.05\n")
    monkeypatch.setenv("LAB_FACTORS_FILE", str(factors / "factors.yaml"))
    reload_settings()
    run = Run.create(Recipe(), data_dir=data_dir)
    shutil.copytree(fixture_store_path, run.paths.store)
    fr = run_factors(run, None)
    added = fr.results.filter(pl.col("factor_id") == "busy_device")
    assert set(R1) <= set(added["key"].to_list())  # six on one device: five others each
    run.close()


# ---- A11 · how many planted rings came out as one community, and how many look-alikes scored high ---------------------
def test_a11_ring_recall_is_reported(data_dir, fixture_store_path, truth):
    from lab.contracts.factors import Policy
    from lab.scoring.communities import ring_recall

    run = scored(data_dir, fixture_store_path)
    communities = pl.read_parquet(run.paths.artifacts / "communities.parquet")
    report = ring_recall(communities, truth, alert_threshold=Policy.load(REPO / "factors" / "policy.yaml").alert_threshold)
    assert report["rings"] == 2 and report["whole"] == 2, report  # both fixture rings land whole in one community each
    assert report["lookalikes"] == 1 and report["lookalikes_in_high_scoring_community"] is not None
    run.close()
