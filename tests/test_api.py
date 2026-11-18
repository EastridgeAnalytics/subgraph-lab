"""Track 2d: the REST routes, the worker, the connection, and the built app, on the fixture graph.

The lab's own code runs for real. The stand-ins are the ones the push tests use for `relate push` and for the master
graph's Bolt session, and `lab.pipeline.resume` where a test is about the worker and not the pipeline. The `live` tests
at the end run the real `relate` against the test Neo4j.
"""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import polars as pl
import pytest
import yaml
from fastapi.testclient import TestClient

from lab.api import relate_server
from lab.api.app import create_app
from lab.api.connection import ConnectionState, check_connection
from lab.api.errors import ApiError
from lab.api.ops import LabState, check_run_id
from lab.api.worker import RunBusy, Worker
from lab.contracts import api as contract
from lab.contracts.api import ROUTES
from lab.contracts.evidence import Comparison, Evidence, Money
from lab.contracts.manifest import RunManifest, StepStatus
from lab.contracts.recipe import Recipe
from lab.runs import Run
from lab.settings import get_settings, reload_settings
from tests.fixtures.graph_loader import GRAPH
from tests.fixtures.make_fixture import R1
from tests.push_helpers import (
    FakeRelate,
    FakeSession,
    community_frame,
    patch_master,
)

REPO = Path(__file__).resolve().parent.parent
LOCAL = "http://127.0.0.1:8000"  # the host the MCP endpoint accepts
ABSENT = "run-20261112T214500Z-1a2b3c"  # shaped like a run id, not on disk


def wait_until(check, timeout: float = 20.0, what: str = "the condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


# ---- fixtures ------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def scored_template(tmp_path_factory, fixture_store_path) -> Path:
    """A run scored on the fixture graph, once for the module: its folder, which each test copies into its own data dir.
    The fixture store stands in for a pull."""
    mp = pytest.MonkeyPatch()
    data = tmp_path_factory.mktemp("scored") / "data"
    data.mkdir()
    mp.setenv("LAB_DATA_DIR", str(data))
    mp.setenv("LAB_FACTORS_FILE", str(REPO / "factors" / "factors.yaml"))
    mp.setenv("LAB_POLICY_FILE", str(REPO / "factors" / "policy.yaml"))
    reload_settings()
    try:
        run = Run.create(Recipe(models=["communities"]), data_dir=data)
        shutil.copytree(fixture_store_path, run.paths.store)
        run.manifest.finish("pull", {"nodes": 1})
        run.save()
        run.score()
        run.close()
        return run.paths.root
    finally:
        mp.undo()
        reload_settings()


@pytest.fixture
def scored_run(data_dir, scored_template) -> Run:
    shutil.copytree(scored_template, data_dir / "runs" / scored_template.name)
    run = Run(scored_template.name, data_dir)
    yield run
    run.close()


@pytest.fixture
def state(data_dir) -> LabState:
    return LabState(worker=Worker())


@pytest.fixture
def client(state) -> TestClient:
    return TestClient(create_app(state=state, supervise=False), base_url=LOCAL)


@pytest.fixture
def relate(monkeypatch) -> FakeRelate:
    fake = FakeRelate()
    monkeypatch.setattr("lab.roundtrip.push.relate_cli", fake)
    return fake


@pytest.fixture
def master(monkeypatch) -> FakeSession:
    return patch_master(monkeypatch, FakeSession())


def evidence_json(key: str, members: list[str], score: float = 0.8) -> str:
    return Evidence(
        subject="community", key=key, run_id="r", member_count=len(members), member_keys=members, score=score,
        score_parts={"member_risk": 0.5}, shared_identifiers=[], money=Money(inflow=1.0, outflow=1.0, internal=1.0, cycles=1),
        factors=[], top_members=[], comparison=Comparison(communities_in_slice=2, score_rank=1, median_size=6, median_score=0.5),
    ).model_dump_json()


def with_summary(df: pl.DataFrame, key: str, members: list[str], embedding: list[float] | None = None) -> pl.DataFrame:
    df = df.with_columns(
        pl.lit(evidence_json(key, members)).alias("evidence"), pl.lit("Six accounts on one device could indicate a ring."
                                                                       ).alias("summary"),
        pl.lit("template").alias("summary_source"), pl.lit("a" * 64).alias("evidence_hash"))
    if embedding is not None:
        df = df.with_columns(pl.Series("embedding", [embedding], dtype=pl.List(pl.Float64)))
    return df


# ---- the contract ----------------------------------------------------------------------------------------------
def test_every_route_in_the_contract_is_served_with_its_model(client):
    served = {(m, r.path): r for r in client.app.routes for m in (getattr(r, "methods", None) or ())}
    for method, path, request, response in ROUTES:
        route = served.get((method, path))
        assert route is not None, f"{method} {path} is not served"
        assert route.response_model is getattr(contract, response), f"{method} {path} answers the wrong model"
        if request:
            body = [p for p in route.dependant.body_params]
            assert body and body[0].field_info.annotation is getattr(contract, request), f"{method} {path} takes the wrong body"


def test_health_has_the_versions(client):
    from tools.install_relate import VERSION

    body = client.get("/api/health").json()
    assert body == {"ok": True, "version": "0.1.0", "relate": VERSION}


def test_health_says_so_when_the_relate_binary_is_missing(client, monkeypatch):
    monkeypatch.setenv("LAB_RELATE_BIN", "no-such-relate-binary")
    monkeypatch.setattr(relate_server, "_version", None)
    reload_settings()
    body = client.get("/api/health").json()
    assert body["ok"] is False and body["relate"].startswith("not found") and body["version"] == "0.1.0"
    assert client.get("/api/runs").status_code == 200  # the rest of the API still answers


# ---- run ids ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["x", "..", "run-1", "run-20261112T214500Z-", "run-20261112T214500Z-ABC", "../../etc",
                                 "run-20261112T214500Z-1a2b3c/..", "run-20261112T214500Z-1a2b3c\n", "", " "])
def test_a_malformed_run_id_is_refused_before_a_path_is_built(bad, monkeypatch):
    monkeypatch.setattr("lab.api.ops.run_paths", lambda *a, **k: pytest.fail("a path was built"))
    monkeypatch.setattr("lab.api.ops.Run", lambda *a, **k: pytest.fail("a run was opened"))
    with pytest.raises(ApiError) as e:
        check_run_id(bad)
    assert e.value.status == 400


def test_routes_refuse_a_malformed_id_and_say_404_for_a_missing_one(client, monkeypatch):
    monkeypatch.setattr("lab.api.ops.run_paths", lambda *a, **k: pytest.fail("a path was built"))
    for method, path in (("GET", "{}"), ("GET", "{}/review"), ("GET", "{}/code"), ("GET", "{}/timeline"),
                         ("GET", "{}/results/accounts"), ("GET", "{}/results/communities"),
                         ("GET", "{}/results/community/abc"), ("POST", "{}/resume")):
        r = client.request(method, "/api/runs/" + path.format("not-a-run"))
        assert r.status_code == 400 and "detail" in r.json(), (method, path)
    r = client.post("/api/runs/not-a-run/push", json={"confirm": True})
    assert r.status_code == 400
    r = client.post("/api/runs/not-a-run/dispositions", json={"kind": "account", "key": "A1", "disposition": "confirmed", "by": "t"})
    assert r.status_code == 400
    monkeypatch.undo()
    for path in ("", "/review", "/code", "/timeline", "/results/accounts", "/results/communities", "/results/community/abc"):
        r = client.get(f"/api/runs/{ABSENT}{path}")
        assert r.status_code == 404 and r.json()["detail"] == f"no run {ABSENT}", path
    assert client.post(f"/api/runs/{ABSENT}/resume").status_code == 404
    assert client.post(f"/api/runs/{ABSENT}/push", json={"confirm": True}).status_code == 404


def test_a_path_that_climbs_out_of_the_runs_folder_is_never_a_run(client):
    for tail in ("..%2F..%2Fsecret", "%2e%2e/%2e%2e/x", "run-20261112T214500Z-1a2b3c%2F..%2F..%2F"):
        r = client.get(f"/api/runs/{tail}")
        assert r.status_code in (400, 404), tail


# ---- runs and the worker ---------------------------------------------------------------------------------------
def test_starting_a_run_returns_its_id_at_once_and_queues_it(client, state):
    r = client.post("/api/runs", json={"recipe": {"name": "t", "models": ["communities"]}})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    assert state.worker.snapshot() == {"running": None, "queued": [run_id], "held": []}
    manifest = client.get(f"/api/runs/{run_id}").json()
    assert RunManifest.model_validate(manifest).run_id == run_id and manifest["status"] == "pending"
    assert Recipe.load(Run(run_id).paths.recipe).name == "t"
    assert [m["run_id"] for m in client.get("/api/runs").json()["runs"]] == [run_id]


def test_a_slice_run_lays_the_requests_pull_and_search_over_its_recipe(client):
    body = {"recipe": {"name": "s", "models": ["communities"]}, "pull": {"mode": "schema", "labels": ["Account"]},
            "search": {"text": "new accounts", "k": 5, "hops": 1}}
    run_id = client.post("/api/slice/run", json=body).json()["run_id"]
    recipe = Recipe.load(Run(run_id).paths.recipe)
    assert recipe.pull.labels == ["Account"] and recipe.search.text == "new accounts" and recipe.search.k == 5


def test_the_run_list_is_newest_first_and_survives_a_broken_run(client, state, data_dir):
    first = client.post("/api/runs", json={"recipe": {}}).json()["run_id"]
    time.sleep(1.1)  # run ids sort by the second
    second = client.post("/api/runs", json={"recipe": {}}).json()["run_id"]
    broken = data_dir / "runs" / "run-20200101T000000Z-bad000"
    broken.mkdir()
    (broken / "manifest.json").write_text("{not json", encoding="utf-8")
    assert [m["run_id"] for m in client.get("/api/runs").json()["runs"]] == [second, first]
    assert client.get("/api/runs/run-20200101T000000Z-bad000").status_code == 500


def test_resume_queues_the_run_once(client, state):
    run_id = client.post("/api/runs", json={"recipe": {}}).json()["run_id"]
    assert client.post(f"/api/runs/{run_id}/resume").json() == {"run_id": run_id}
    assert state.worker.snapshot()["queued"] == [run_id]  # already waiting: not queued twice


def test_the_worker_runs_what_is_queued_one_at_a_time(data_dir, monkeypatch):
    seen, running, overlap = [], [], []

    def resume(run):
        running.append(run.id)
        overlap.append(len(running))
        time.sleep(0.15)
        seen.append(run.id)
        running.remove(run.id)
        return run

    monkeypatch.setattr("lab.pipeline.resume", resume)
    state = LabState(worker=Worker())
    with TestClient(create_app(state=state, supervise=True), base_url=LOCAL) as c:
        ids = [c.post("/api/runs", json={"recipe": {}}).json()["run_id"] for _ in range(3)]
        wait_until(lambda: len(seen) == 3, what="three runs")
    assert seen == ids  # in the order queued
    assert max(overlap) == 1
    assert not state.worker.busy()


def test_a_failing_run_is_on_its_manifest_and_the_worker_goes_on(data_dir, fixture_store_path, monkeypatch):
    def prepared() -> Run:
        run = Run.create(Recipe(models=["communities"]), data_dir=data_dir)
        shutil.copytree(fixture_store_path, run.paths.store)
        run.manifest.finish("pull")
        run.save()
        return run

    bad, good = prepared(), prepared()

    def score(self):
        if self.id == bad.id:
            raise RuntimeError("factor file broken")
        self.manifest.finish("score", {"accounts": 1})
        self.save()
        return {}

    monkeypatch.setattr(Run, "score", score)
    monkeypatch.setattr(Run, "preview_push", lambda self: (self.manifest.finish("review"), self.save()))
    worker = Worker()
    worker.start()
    worker.submit(bad.id)
    worker.submit(good.id)
    wait_until(lambda: not worker.busy() and RunManifest.load(good.paths.manifest).step("review").status is StepStatus.DONE,
               what="both runs")
    failed = RunManifest.load(bad.paths.manifest)
    assert failed.step("score").status is StepStatus.FAILED and "factor file broken" in failed.step("score").note
    assert worker.stop() is True


def test_the_worker_stops_cleanly_and_leaves_queued_runs_pending(data_dir, monkeypatch):
    release, started, ran = threading.Event(), threading.Event(), []

    def resume(run):
        ran.append(run.id)
        started.set()
        release.wait(10)
        return run

    monkeypatch.setattr("lab.pipeline.resume", resume)
    worker = Worker()
    worker.start()
    a, b = (Run.create(Recipe(), data_dir=data_dir).id for _ in range(2))
    worker.submit(a)
    worker.submit(b)
    assert started.wait(10)
    assert worker.stop(timeout=0.2) is False  # a step cannot be interrupted: it says it is still going
    release.set()
    wait_until(lambda: not worker._thread.is_alive(), what="the thread to end")
    assert ran == [a]  # the queued run never started
    assert RunManifest.load(Run(b, data_dir).paths.manifest).status is StepStatus.PENDING


def test_a_run_in_use_refuses_a_second_claim(data_dir):
    worker = Worker()
    with worker.exclusive("run-a"):
        with pytest.raises(RunBusy, match="in use"):
            with worker.exclusive("run-a"):
                pass
        with pytest.raises(RunBusy):
            worker.submit("run-a")
        with worker.exclusive("run-b"):
            pass
    assert worker.submit("run-a") is True and worker.submit("run-a") is False
    with pytest.raises(RunBusy, match="queued"):
        with worker.exclusive("run-a"):
            pass


def test_a_step_left_running_by_a_stopped_lab_is_marked_failed(data_dir):
    run = Run.create(Recipe(), data_dir=data_dir)
    run.manifest.start("pull")
    run.save()
    other = Run.create(Recipe(), data_dir=data_dir)
    assert Worker().recover_orphans() == [run.id]
    manifest = RunManifest.load(run.paths.manifest)
    assert manifest.step("pull").status is StepStatus.FAILED and "resume the run" in manifest.step("pull").note
    assert RunManifest.load(other.paths.manifest).status is StepStatus.PENDING


def test_polling_a_run_never_makes_the_workers_save_fail(data_dir, client):
    """On Windows a poll of manifest.json can make the next `os.replace` fail, even with share-delete. The worker saves
    through `RunManifest.save`, which tries again, and the poll holds the file for far less time than the retries span."""
    run = Run.create(Recipe(), data_dir=data_dir)
    errors: list[Exception] = []
    done = threading.Event()

    def worker():
        try:
            for i in range(120):
                run.manifest.counts["n"] = i
                run.save()
        except Exception as e:  # noqa: BLE001
            errors.append(e)
        finally:
            done.set()

    def poller():
        while not done.is_set():
            r = client.get(f"/api/runs/{run.id}")
            if r.status_code != 200:
                errors.append(AssertionError(r.text))
                return
            client.get("/api/runs")

    threads = [threading.Thread(target=worker), *(threading.Thread(target=poller) for _ in range(3))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert errors == []
    assert RunManifest.load(run.paths.manifest).counts["n"] == 119


def test_the_read_only_routes_never_open_the_manifest(client, scored_run, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a read-only route opened the manifest")

    monkeypatch.setattr(RunManifest, "load", boom)
    monkeypatch.setattr("lab.api.ops.read_manifest", boom)
    monkeypatch.setattr("lab.api.view.read_manifest", boom)
    for path in ("results/accounts", "results/communities", "timeline", "code"):
        assert client.get(f"/api/runs/{scored_run.id}/{path}").status_code == 200, path


# ---- the slice -------------------------------------------------------------------------------------------------
def test_a_pull_estimate_is_the_estimate_functions_answer(client, monkeypatch):
    from lab.contracts.api import SliceEstimate

    want = SliceEstimate(mode="schema", nodes=10, relationships=20, memory_bytes=1, over_cap=False, cap=5)
    seen = []
    monkeypatch.setattr("lab.slice.estimate.estimate_pull", lambda spec, mapping: seen.append(spec) or want)
    r = client.post("/api/slice/estimate", json={"pull": {"mode": "schema", "labels": ["Account"]}})
    assert r.status_code == 200 and SliceEstimate.model_validate(r.json()) == want
    assert seen[0].labels == ["Account"]


def test_a_search_estimate_claims_the_run_it_will_write_to(client, state, scored_run, monkeypatch):
    from lab.contracts.api import SliceEstimate

    want = SliceEstimate(mode="search", nodes=3, relationships=2, memory_bytes=1, over_cap=False, cap=5)
    held = []
    monkeypatch.setattr("lab.slice.estimate.search_slice",
                        lambda run, spec: held.append((run.id, scored_run.id in state.worker.snapshot()["held"])) or want)
    scored_run.manifest.finish("first_pass")
    scored_run.save()
    r = client.post("/api/slice/estimate", json={"search": {"text": "new accounts"}})
    assert r.status_code == 200 and r.json()["mode"] == "search"
    assert held == [(scored_run.id, True)]  # the newest run with a first pass, held while it was searched
    with state.worker.exclusive(scored_run.id):
        r = client.post("/api/slice/estimate", json={"run_id": scored_run.id, "search": {"text": "x"}})
    assert r.status_code == 409 and "in use" in r.json()["detail"]
    assert client.post("/api/slice/estimate", json={"run_id": ABSENT, "search": {"text": "x"}}).status_code == 404


def test_a_search_with_no_run_to_search_says_to_pull_first(client, data_dir):
    body = client.post("/api/slice/estimate", json={"search": {"text": "new accounts"}}).json()
    assert body["mode"] == "search" and "pull a schema slice first" in body["note"]


# ---- the models ------------------------------------------------------------------------------------------------
def test_models_lists_the_four_models_and_every_factor(client):
    body = client.get("/api/models").json()
    models = contract.ModelsList.model_validate(body)
    assert [m.name for m in models.models if m.kind == "model"] == ["node2vec", "tgn", "communities", "velocity"]
    factors = {m.name: m for m in models.models if m.kind == "factor"}
    file = yaml.safe_load((REPO / "factors" / "factors.yaml").read_text(encoding="utf-8"))
    assert set(factors) == {f["id"] for f in file}
    shared = factors["shared_device_5plus"]
    assert shared.code_path.endswith("factors/shared_device.cypher") and shared.factor.weight == 0.20
    assert factors["tgn"].code_path == "examples/tgn"
    assert models.factor_version and models.policy["alert_threshold"] == 0.6 and models.policy["community"]["min_size"] == 3
    assert all(m.code_path and m.description for m in models.models)


@pytest.mark.parametrize("name, path, language, needle", [
    ("tgn", "examples/tgn/example.py", "python", "TGN"),
    ("communities", "examples/networkx/example.py", "python", "louvain"),
    ("shared_device_5plus", "factors/shared_device.cypher", "cypher", "MATCH"),
    ("velocity_anomaly", "lab/scoring/velocity.py", "python", "def score"),
])
def test_models_source_shows_each_entrys_code(client, name, path, language, needle):
    body = contract.SourceFile.model_validate(client.get(f"/api/models/{name}/source").json())
    assert (body.name, body.path, body.language) == (name, path, language)
    assert body.text == (REPO / path).read_text(encoding="utf-8") and needle.lower() in body.text.lower()


@pytest.mark.parametrize("name", ["no_such_factor", "..", "..%2F..%2Fpyproject.toml"])
def test_models_source_is_404_for_anything_else(client, name):
    r = client.get(f"/api/models/{name}/source")
    assert r.status_code == 404 and "detail" in r.json()


# ---- results ---------------------------------------------------------------------------------------------------
def test_accounts_are_paged_highest_score_first(client, scored_run):
    page = contract.AccountsPage.model_validate(client.get(f"/api/runs/{scored_run.id}/results/accounts").json())
    assert page.total == 200 and len(page.rows) == 100
    scores = [r.risk_score for r in page.rows]
    assert scores == sorted(scores, reverse=True)
    top = page.rows[0]
    assert top.alert_key and top.key in R1 + [f"A{i:04d}" for i in range(101, 109)]
    assert {f["id"] for f in top.factors} >= {"shared_device_5plus", "new_accounts_shared_identifier"}
    small = client.get(f"/api/runs/{scored_run.id}/results/accounts", params={"limit": 5, "offset": 2}).json()
    assert [r["key"] for r in small["rows"]] == [r.key for r in page.rows[2:7]] and small["total"] == 200
    assert client.get(f"/api/runs/{scored_run.id}/results/accounts", params={"limit": 0}).status_code == 422
    quiet = [r for r in page.rows if r.alert_key is None]
    assert quiet and all(r.disposition is None for r in quiet)


def test_communities_are_empty_until_there_are_some(client, fixture_run):
    fixture_run.stage(pl.DataFrame({"account_id": ["A0001"], "risk_score": [0.5]}), kind="account")
    fixture_run.close()  # staged accounts, no communities yet
    body = client.get(f"/api/runs/{fixture_run.id}/results/communities").json()
    assert contract.CommunitiesPage.model_validate(body).total == 0 and body["rows"] == []
    r = client.get(f"/api/runs/{fixture_run.id}/results/community/anything")
    assert r.status_code == 404 and "no communities are staged" in r.json()["detail"]


def test_staged_communities_page_open_and_find_their_likes(client, fixture_run):
    scored_run = fixture_run  # a run with no communities of its own, so these three are all there are
    members = list(R1)
    one = with_summary(community_frame("c-one", members, score=0.9), "c-one", members, [1.0, 0.0, 0.0])
    two = with_summary(community_frame("c-two", ["A0101", "A0102", "A0103"], score=0.5), "c-two", ["A0101", "A0102", "A0103"],
                       [0.9, 0.1, 0.0])
    three = with_summary(community_frame("c-three", ["A0150", "A0151", "A0152"], score=0.3), "c-three",
                         ["A0150", "A0151", "A0152"], [0.0, 1.0, 0.0])
    for frame in (one, two, three):
        scored_run.stage(frame, kind="community")
    scored_run.close()
    page = client.get(f"/api/runs/{scored_run.id}/results/communities").json()
    assert [r["key"] for r in page["rows"]] == ["c-one", "c-two", "c-three"] and page["total"] == 3
    assert page["rows"][0]["size"] == 6 and page["rows"][0]["summary"].startswith("Six accounts")
    detail = contract.CommunityDetail.model_validate(client.get(f"/api/runs/{scored_run.id}/results/community/c-one").json())
    assert detail.row.key == "c-one" and detail.evidence.member_count == 6 and detail.summary.source == "template"
    assert {n.id for n in detail.nodes if n.label == "Account"} == set(members)
    assert any(e.type == "TX" and e.props["payments"] >= 1 for e in detail.edges)  # the ring pays itself
    assert any(n.id == "Device:D-R1" for n in detail.nodes) and any(e.target == "Device:D-R1" for e in detail.edges)
    assert [r.key for r in detail.similar][:2] == ["c-two", "c-three"]  # nearest embedding first
    assert client.get(f"/api/runs/{scored_run.id}/results/community/nope").status_code == 404


def test_a_community_without_an_evidence_object_is_not_ready(client, scored_run):
    scored_run.stage(community_frame("c-bare", ["A0001", "A0002", "A0003"]), kind="community")
    scored_run.close()
    r = client.get(f"/api/runs/{scored_run.id}/results/community/c-bare")
    assert r.status_code == 409 and "evidence object" in r.json()["detail"]


# ---- dispositions ----------------------------------------------------------------------------------------------
def test_a_disposition_is_staged_and_shows_on_the_row(client, scored_run, state):
    top = client.get(f"/api/runs/{scored_run.id}/results/accounts").json()["rows"][0]
    body = {"kind": "account", "key": top["key"], "disposition": "false_positive", "by": "reviewer@example.com"}
    r = client.post(f"/api/runs/{scored_run.id}/dispositions", json=body)
    assert r.status_code == 200 and contract.DispositionResult.model_validate(r.json()) == contract.DispositionResult(ok=True, staged=True)
    row = client.get(f"/api/runs/{scored_run.id}/results/accounts").json()["rows"][0]
    assert row["key"] == top["key"] and row["disposition"] == "false_positive"
    scored_run.reload()
    assert scored_run.manifest.step("stage").counts["dispositions"] == 1
    # an alert's decision shows on its account
    other = client.get(f"/api/runs/{scored_run.id}/results/accounts").json()["rows"][1]
    client.post(f"/api/runs/{scored_run.id}/dispositions",
                json={"kind": "alert", "key": other["alert_key"], "disposition": "confirmed", "by": "r"})
    assert client.get(f"/api/runs/{scored_run.id}/results/accounts").json()["rows"][1]["disposition"] == "confirmed"
    assert state.worker.snapshot()["held"] == []  # the claim was let go


def test_a_disposition_needs_a_staged_alert_a_name_and_a_free_run(client, scored_run, state):
    url = f"/api/runs/{scored_run.id}/dispositions"
    r = client.post(url, json={"kind": "alert", "key": "no-such-alert", "disposition": "confirmed", "by": "t"})
    assert r.status_code == 404 and "no staged alert" in r.json()["detail"]
    r = client.post(url, json={"kind": "account", "key": "A0001", "disposition": "confirmed", "by": "  "})
    assert r.status_code == 422 and "who made it" in r.json()["detail"]
    r = client.post(url, json={"kind": "account", "key": "A0001", "disposition": "guilty", "by": "t"})
    assert r.status_code == 422
    with state.worker.exclusive(scored_run.id):
        r = client.post(url, json={"kind": "account", "key": "A0001", "disposition": "confirmed", "by": "t"})
    assert r.status_code == 409


# ---- review and push -------------------------------------------------------------------------------------------
def test_review_is_the_plan_and_the_delta(client, scored_run, relate, master):
    r = client.get(f"/api/runs/{scored_run.id}/review")
    assert r.status_code == 200
    review = contract.Review.model_validate(r.json())
    assert review.run_id == scored_run.id and {e.entity for e in review.plan} >= {"Account", "Alert"}
    assert review.delta.accounts_scored == 200 and "dry run" in review.plan_text
    assert master.writes() == [] and relate.real_pushes == []  # a review sends nothing


def test_a_review_with_nothing_staged_is_a_conflict(client, state, data_dir, relate, master):
    run = Run.create(Recipe(), data_dir=data_dir)
    r = client.get(f"/api/runs/{run.id}/review")
    assert r.status_code == 409 and "nothing is staged" in r.json()["detail"]


@pytest.mark.parametrize("body", [{}, {"confirm": False}])
def test_a_push_without_confirm_is_400_and_writes_nothing(client, scored_run, monkeypatch, body):
    def boom(*a, **k):
        raise AssertionError("nothing may run before the confirm check")

    for target in ("lab.roundtrip.push.relate_cli", "lab.roundtrip.push.neo4j_session", "lab.roundtrip.push.credentials_file",
                   "lab.common.neo4j.neo4j_driver", "subprocess.run", "subprocess.Popen"):
        monkeypatch.setattr(target, boom)
    before = scored_run.paths.manifest.read_bytes()
    r = client.post(f"/api/runs/{scored_run.id}/push", json=body)
    assert r.status_code == 400 and "not confirmed" in r.json()["detail"] and "Nothing was sent" in r.json()["detail"]
    assert scored_run.paths.manifest.read_bytes() == before


def test_a_confirmed_push_lands_every_entity_and_the_labrun(client, scored_run, relate, master):
    assert client.get(f"/api/runs/{scored_run.id}/review").status_code == 200  # decision 37: the review comes first
    r = client.post(f"/api/runs/{scored_run.id}/push", json={"confirm": True})
    assert r.status_code == 200, r.text
    result = contract.PushResult.model_validate(r.json())
    assert result.pushed and all(e.landed for e in result.entities) and "LabRun" in {e.entity for e in result.entities}
    assert len(relate.real_pushes) == 2  # everything, then the LabRun
    assert RunManifest.load(scored_run.paths.manifest).step("push").status is StepStatus.DONE


def test_a_push_without_a_fresh_review_is_refused(client, scored_run, relate, master):
    """Decision 37 (A2): no review, or something staged after it, is a 409 and nothing is sent."""
    r = client.post(f"/api/runs/{scored_run.id}/push", json={"confirm": True})
    assert r.status_code == 409 and "review" in r.json()["detail"] and relate.real_pushes == []
    assert client.get(f"/api/runs/{scored_run.id}/review").status_code == 200
    scored_run.reload()
    key = scored_run.table("MATCH (a:Account) RETURN a.account_id AS k LIMIT 1")["k"][0]
    scored_run.close()  # Relate connections belong to the thread that opened them; the API runs on another
    d = client.post(f"/api/runs/{scored_run.id}/dispositions",
                    json={"kind": "account", "key": key, "disposition": "needs_more", "by": "laura"})
    assert d.status_code == 200, d.text
    r = client.post(f"/api/runs/{scored_run.id}/push", json={"confirm": True})
    assert r.status_code == 409 and "after the review" in r.json()["detail"] and relate.real_pushes == []


# ---- the timeline ----------------------------------------------------------------------------------------------
def payments() -> pl.DataFrame:
    return pl.read_csv(GRAPH / "tx.csv").with_columns(
        pl.col("ts").str.to_datetime("%Y-%m-%dT%H:%M:%SZ", time_zone="UTC"))


def expected(cutoff: str | None) -> dict[str, tuple[int, int, float]]:
    df = payments()
    if cutoff:
        df = df.filter(pl.col("ts") <= pl.lit(cutoff).str.to_datetime("%Y-%m-%dT%H:%M:%SZ", time_zone="UTC"))
    out = {}
    for (day,), part in df.group_by(pl.col("ts").dt.date(), maintain_order=True):
        accounts = set(part["src"]) | set(part["dst"])
        out[f"{day}T00:00:00Z"] = (len(accounts), part.height, round(part["amount"].sum(), 2))
    return dict(sorted(out.items()))


def test_the_timeline_counts_all_two_thousand_payments(client, scored_run):
    t = contract.Timeline.model_validate(client.get(f"/api/runs/{scored_run.id}/timeline").json())
    assert sum(p.payments for p in t.points) == 2000 and t.as_of is None
    df = payments()
    assert t.start == df["ts"].min().strftime("%Y-%m-%dT%H:%M:%SZ") and t.end == df["ts"].max().strftime("%Y-%m-%dT%H:%M:%SZ")
    assert {p.at: (p.accounts, p.payments, round(p.amount, 2)) for p in t.points} == expected(None)


@pytest.mark.parametrize("as_of, cutoff", [("2026-06-15T12:00:00Z", "2026-06-15T12:00:00Z"),
                                           ("2026-07-01", "2026-07-01T23:59:59Z"),
                                           ("2026-10-02T00:00:00+00:00", "2026-10-02T00:00:00Z")])
def test_the_timeline_is_the_store_as_of_t(client, scored_run, as_of, cutoff):
    t = contract.Timeline.model_validate(client.get(f"/api/runs/{scored_run.id}/timeline", params={"as_of": as_of}).json())
    assert t.as_of == cutoff
    want = expected(cutoff)
    assert {p.at: (p.accounts, p.payments, round(p.amount, 2)) for p in t.points} == want
    assert sum(p.payments for p in t.points) == int(payments().filter(
        pl.col("ts") <= pl.lit(cutoff).str.to_datetime("%Y-%m-%dT%H:%M:%SZ", time_zone="UTC")).height)
    assert t.start.startswith("2026-06-01") and t.end.startswith("2026-10-29")  # the bounds do not move with as_of


def test_the_timeline_before_any_payment_is_empty_and_a_bad_date_is_422(client, scored_run, data_dir):
    t = client.get(f"/api/runs/{scored_run.id}/timeline", params={"as_of": "2020-01-01"}).json()
    assert t["points"] == [] and t["start"].startswith("2026-06-01")
    r = client.get(f"/api/runs/{scored_run.id}/timeline", params={"as_of": "yesterday"})
    assert r.status_code == 422 and "ISO-8601" in r.json()["detail"]
    fresh = Run.create(Recipe(), data_dir=data_dir)  # no store yet
    r = client.get(f"/api/runs/{fresh.id}/timeline")
    assert r.status_code == 409 and "no store yet" in r.json()["detail"]


# ---- show code -------------------------------------------------------------------------------------------------
def test_the_code_route_gives_the_recipe_and_the_python_that_runs_it(client, scored_run):
    code = contract.ShowCode.model_validate(client.get(f"/api/runs/{scored_run.id}/code").json())
    assert Recipe.model_validate(yaml.safe_load(code.recipe_yaml)) == scored_run.recipe
    assert code.recipe_yaml == scored_run.paths.recipe.read_text(encoding="utf-8")
    compile(code.python, "show_code", "exec")  # it is Python
    for line in ("run.pull()", "run.score()", "run.preview_push()"):
        assert line in code.python
    assert "run.first_pass()" not in code.python  # this recipe does not ask for it
    assert "# run.apply_push(confirm=True)" in code.python and "\nrun.apply_push" not in code.python


def test_the_code_route_follows_a_recipe_that_searches(client, data_dir):
    recipe = Recipe(search={"text": "new accounts"}, models=["node2vec", "tgn"])
    run = Run.create(recipe, data_dir=data_dir)
    python = client.get(f"/api/runs/{run.id}/code").json()["python"]
    steps = [line.split("(")[0] for line in python.splitlines() if line.startswith(("run.", "examples"))]
    assert steps == ["run.pull", "run.first_pass", "run.search", "examples.tgn.run_model", "run.score", "run.preview_push"]
    compile(python, "show_code", "exec")


# ---- the connection --------------------------------------------------------------------------------------------
def test_a_connection_test_never_echoes_the_password(client):
    r = client.post("/api/connect/test", json={"uri": "bolt://127.0.0.1:1", "username": "neo4j", "password": "hunter2-secret"})
    body = r.json()
    assert r.status_code == 200 and contract.ConnectResult.model_validate(body).ok is False
    assert "hunter2-secret" not in r.text and body["pull_route"] is None


def test_an_invalid_body_does_not_echo_what_was_sent(client):
    r = client.post("/api/connect/test", json={"uri": 5, "password": "hunter2-secret"})
    assert r.status_code == 422 and "hunter2-secret" not in r.text
    r = client.post("/api/connect/test", content='["hunter2-secret"]', headers={"content-type": "application/json"})
    assert r.status_code == 422 and "hunter2-secret" not in r.text


def test_a_driver_error_with_the_password_in_it_is_scrubbed(monkeypatch):
    from neo4j import GraphDatabase

    def explode(*a, **k):
        raise OSError("cannot reach neo4j://neo4j:hunter2-secret@host:7687 with hunter2-secret")

    monkeypatch.setattr(GraphDatabase, "driver", explode)
    result = check_connection(contract.ConnectTest(uri="bolt://elsewhere:7687", password="hunter2-secret"))
    assert not result.ok and "hunter2-secret" not in result.message and "***" in result.message


def test_pasted_credentials_are_used_in_memory_only_and_never_come_back(client, state, monkeypatch, tmp_path):
    from lab.contracts.api import ConnectResult

    ok = ConnectResult(ok=True, neo4j_version="2026.08.1", edition="community", query_uri="http://x:7474",
                       pull_route="relate", message="Connected.")
    monkeypatch.setattr("lab.api.app.check_connection", lambda body: ok)
    monkeypatch.setattr("lab.api.connection.check_connection", lambda *a, **k: ok)
    here = tmp_path / "cwd"
    here.mkdir()
    monkeypatch.chdir(here)  # a `.env` here would be read by `reload_settings`; nothing may be written
    try:
        r = client.post("/api/connect/use", json={"uri": "neo4j+s://abc.databases.neo4j.io", "username": "u",
                                                  "password": "hunter2-secret", "database": "prod"})
        assert r.status_code == 200 and r.json()["ok"] and "hunter2-secret" not in r.text
        s = get_settings()
        assert (s.neo4j_uri, s.neo4j_username, s.neo4j_password, s.neo4j_database) == (
            "neo4j+s://abc.databases.neo4j.io", "u", "hunter2-secret", "prod")
        assert s.query_uri == "https://abc.databases.neo4j.io"  # derived from the new URI, not the old one
        got = client.get("/api/connect")
        assert "hunter2-secret" not in got.text and "pasted into the Connect screen" in got.json()["message"]
        assert list(here.iterdir()) == []  # nothing on disk
        reload_settings()  # a new process reads .env again: the session's choice is gone with the old one
        assert get_settings().neo4j_password != "hunter2-secret" and "abc.databases" not in get_settings().neo4j_uri
    finally:
        state.connection.restore()


def test_a_failed_test_changes_nothing_and_a_busy_worker_blocks_the_switch(client, state, monkeypatch):
    before = get_settings().neo4j_uri
    r = client.post("/api/connect/use", json={"uri": "bolt://127.0.0.1:1", "password": "x"})
    assert r.status_code == 200 and r.json()["ok"] is False and get_settings().neo4j_uri == before
    assert not state.connection.active
    state.worker.submit("run-20261112T214500Z-1a2b3c")  # something is queued: a run is in progress
    r = client.post("/api/connect/use", json={"uri": "bolt://elsewhere:7687"})
    assert r.status_code == 409 and "mix two graphs" in r.json()["detail"] and get_settings().neo4j_uri == before


def test_restore_puts_back_what_the_settings_said(data_dir):
    state = ConnectionState()
    before = get_settings().neo4j_uri
    state.use(contract.ConnectTest(uri="bolt://other:7687", password="p"))
    assert get_settings().neo4j_uri == "bolt://other:7687" and state.active
    state.restore()
    assert get_settings().neo4j_uri == before and not state.active


# ---- the built app ---------------------------------------------------------------------------------------------
@pytest.fixture
def dist(tmp_path) -> Path:
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<html><div id=root></div></html>", encoding="utf-8")
    (d / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    return d


def test_the_app_is_served_at_the_root_with_a_single_page_fallback(state, dist):
    c = TestClient(create_app(state=state, supervise=False, app_dist=dist), base_url=LOCAL)
    assert "id=root" in c.get("/").text
    assert "id=root" in c.get("/slice").text and "id=root" in c.get("/results/deep/link").text  # the client router's paths
    assert c.get("/assets/app.js").text == "console.log(1)"
    assert c.get("/assets/missing.js").status_code == 404  # a missing file is not index.html
    assert c.get("/api/health").json()["ok"] is True  # API routes come first
    r = c.get("/api/no-such-route")
    assert r.status_code == 404 and "detail" in r.json() and "id=root" not in r.text
    assert c.get("/..%2fsecret.txt").text != "secret"
    assert c.get("/%2e%2e/secret.txt").text != "secret"


def test_the_built_app_in_the_repo_is_served(state):
    real = REPO / "app" / "dist"
    if not (real / "index.html").exists():
        pytest.skip("the app is not built")
    c = TestClient(create_app(state=state, supervise=False, app_dist=real), base_url=LOCAL)
    page = c.get("/")
    assert page.status_code == 200 and '<div id="root">' in page.text
    asset = next(real.glob("assets/*.js")).name
    assert c.get(f"/assets/{asset}").status_code == 200


def test_without_a_built_app_the_root_is_404_and_the_api_still_works(client):
    assert client.get("/").status_code == 404 and client.get("/api/health").status_code == 200


def test_serve_listens_where_the_settings_say(monkeypatch, data_dir):
    from lab.api import server

    seen = {}
    monkeypatch.setattr("uvicorn.run", lambda app, host, port, **k: seen.update(host=host, port=port))
    monkeypatch.setenv("LAB_PORT", "8123")
    reload_settings()
    assert server.serve() == 0 and seen == {"host": "127.0.0.1", "port": 8123}
    monkeypatch.setenv("LAB_RELATE_HOST", "0.0.0.0")  # what compose sets in the container: LAB_PORT is the host port there
    reload_settings()
    server.serve()
    assert seen == {"host": "0.0.0.0", "port": 8000}
    monkeypatch.setenv("LAB_APP_DIST", "/nowhere")
    reload_settings()
    assert server.app_dist() is None


# ---- live: the real relate and the test Neo4j --------------------------------------------------------------------
@pytest.mark.live
def test_api_live_connect_test_names_the_neo4j_and_the_pull_route(live_neo4j, client):
    body = client.post("/api/connect/test", json={}).json()
    assert body["ok"] is True and body["neo4j_version"].startswith("2026.") and body["pull_route"] == "relate"
    assert body["query_uri"] == live_neo4j.query_uri and live_neo4j.neo4j_password not in str(body)
    assert client.get("/api/connect").json()["ok"] is True
    bad = client.post("/api/connect/test", json={"password": "not-the-password"}).json()
    assert bad["ok"] is False and "rejected" in bad["message"] and bad["pull_route"] is None


@pytest.mark.live
def test_api_live_a_run_goes_to_its_review_and_a_confirmed_push_lands_a_labrun(live_neo4j, data_dir, monkeypatch):
    from lab.common.neo4j import master_rows
    from tests.push_helpers import wipe_lab

    state = LabState(worker=Worker())
    state.worker.start()
    client = TestClient(create_app(state=state, supervise=False), base_url=LOCAL)
    try:
        run_id = client.post("/api/runs", json={"recipe": {"name": "live", "models": ["communities"]}}).json()["run_id"]

        def finished():
            m = client.get(f"/api/runs/{run_id}").json()
            steps = {s["name"]: s["status"] for s in m["steps"]}
            if "failed" in steps.values():
                raise AssertionError(m)
            return steps["review"] == "done" and not state.worker.busy()

        wait_until(finished, timeout=240, what="the run to reach its review")
        manifest = RunManifest.model_validate(client.get(f"/api/runs/{run_id}").json())
        assert manifest.step("pull").status is StepStatus.DONE and manifest.step("push").status is StepStatus.PENDING
        review = contract.Review.model_validate(client.get(f"/api/runs/{run_id}/review").json())
        assert review.delta.accounts_scored == 200 and {e.entity for e in review.plan} >= {"Account", "Alert"}
        assert master_rows("MATCH (l:LabRun) RETURN count(l) AS n")[0]["n"] == 0  # a review writes nothing
        refused = client.post(f"/api/runs/{run_id}/push", json={"confirm": False})
        assert refused.status_code == 400
        assert master_rows("MATCH (l:LabRun) RETURN count(l) AS n")[0]["n"] == 0
        pushed = client.post(f"/api/runs/{run_id}/push", json={"confirm": True})
        assert pushed.status_code == 200 and pushed.json()["pushed"] is True, pushed.text
        assert master_rows("MATCH (l:LabRun {run_id: $r}) RETURN count(l) AS n", {"r": run_id})[0]["n"] == 1
    finally:
        state.worker.stop()
        wipe_lab()
