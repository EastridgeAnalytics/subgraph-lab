"""Track 2d: the lab's eight MCP tools, served at /mcp.

The SDK's own client talks to the real app over streamable HTTP: the app runs under uvicorn on a free loopback port in a
thread of the test process (the in-process transport the SDK offers skips the HTTP surface this track is about). The
lab's code runs for real; the stand-ins are the push tests' (`FakeRelate`, `FakeSession`) and patched pipeline steps
where a test is about a tool and not the step.
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time
from types import SimpleNamespace

import httpx
import polars as pl
import pytest
import uvicorn
from mcp import Client

from lab.api.app import create_app
from lab.api.ops import LabState
from lab.api.worker import Worker
from lab.contracts import api as contract
from lab.contracts import tools as tool_contract
from lab.contracts.evidence import Comparison, Evidence, Money
from lab.contracts.manifest import Route, RunManifest, StepStatus
from lab.contracts.recipe import Recipe
from lab.contracts.tools import TOOLS
from lab.roundtrip.pull import PullResult
from lab.runs import Run
from tests.push_helpers import (
    FakeRelate,
    FakeSession,
    account_frame,
    alert_frame,
    community_frame,
    patch_master,
)

KEYS = ["A0001", "A0002", "A0003"]
ABSENT = "run-20261112T214500Z-1a2b3c"


def wait_until(check, timeout: float = 20.0, what: str = "the condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


# ---- the app, served ---------------------------------------------------------------------------------------------
@pytest.fixture
def served(data_dir):
    state = LabState(worker=Worker())
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(state=state, supervise=False), host="127.0.0.1", port=port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_until(lambda: server.started, what="uvicorn to start")
    yield SimpleNamespace(url=f"http://127.0.0.1:{port}/mcp", state=state, port=port)
    server.should_exit = True
    thread.join(15)


def call(served, name: str, **arguments):
    async def go():
        async with Client(served.url) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(go())


def text(result) -> str:
    return " ".join(block.text for block in result.content)


def evidence(subject: str, key: str, members: list[str]) -> str:
    return Evidence(
        subject=subject, key=key, run_id="r", member_count=len(members), member_keys=members, score=0.8,
        score_parts={"member_risk": 0.5}, shared_identifiers=[], money=Money(inflow=1.0, outflow=1.0, internal=1.0, cycles=1),
        factors=[], top_members=[], comparison=Comparison(communities_in_slice=1, score_rank=1, median_size=3, median_score=0.8),
    ).model_dump_json()


def staged(data_dir, *, scores: float = 0.9, keys: list[str] = KEYS, alerts: int = 2) -> Run:
    """A run with accounts, alerts and one community staged, as `score` would leave it. The fixture store stands in for the
    pulled one: only the push store is touched."""
    run = Run.create(Recipe(), data_dir=data_dir)
    run.stage(account_frame(keys, score=scores), kind="account")
    run.stage(alert_frame([(f"alert-{k}", k) for k in keys[:alerts]], score=scores), kind="alert")
    run.stage(community_frame(f"community-{run.id[-6:]}", keys, score=scores), kind="community")
    run.close()
    return run


@pytest.fixture
def relate(monkeypatch) -> FakeRelate:
    fake = FakeRelate()
    monkeypatch.setattr("lab.roundtrip.push.relate_cli", fake)
    return fake


@pytest.fixture
def master(monkeypatch) -> FakeSession:
    return patch_master(monkeypatch, FakeSession())


# ---- the tools -------------------------------------------------------------------------------------------------
def test_the_nine_tools_are_listed_with_the_contracts_schemas(served):
    async def go():
        async with Client(served.url) as client:
            return (await client.list_tools()).tools

    tools = {t.name: t for t in asyncio.run(go())}
    assert set(tools) == set(TOOLS) and len(tools) == 9
    for name, (model_in, model_out, description) in TOOLS.items():
        tool = tools[name]
        assert tool.description == description
        want_in, want_out = model_in.model_json_schema(), model_out.model_json_schema()
        assert set(tool.input_schema["properties"]) == set(want_in["properties"]), name  # the fields, not a wrapper
        assert set(tool.input_schema.get("required", [])) == set(want_in.get("required", [])), name
        assert tool.output_schema is not None and set(tool.output_schema["properties"]) == set(want_out["properties"]), name


def test_apply_push_is_refused_without_confirm_and_nothing_runs(served, data_dir, monkeypatch):
    run = staged(data_dir)

    def boom(*a, **k):
        raise AssertionError("nothing may run before the confirm check")

    for target in ("lab.roundtrip.push.relate_cli", "lab.roundtrip.push.neo4j_session", "lab.roundtrip.push.credentials_file",
                   "subprocess.run"):
        monkeypatch.setattr(target, boom)
    before = run.paths.manifest.read_bytes()
    for arguments in ({"run_id": run.id}, {"run_id": run.id, "confirm": False}):
        result = call(served, "apply_push", **arguments)
        assert result.is_error and "confirm=true" in text(result) and "Nothing was sent" in text(result)
    assert run.paths.manifest.read_bytes() == before
    assert RunManifest.load(run.paths.manifest).step("push").status is StepStatus.PENDING


def test_apply_push_with_confirm_pushes_and_answers_with_its_model(served, data_dir, relate, master):
    run = staged(data_dir)
    assert not call(served, "preview_push", run_id=run.id).is_error  # decision 37: the review comes first
    result = call(served, "apply_push", run_id=run.id, confirm=True)
    assert not result.is_error, text(result)
    pushed = contract.PushResult.model_validate(result.structured_content)
    assert pushed.pushed and pushed.run_id == run.id and "LabRun" in {e.entity for e in pushed.entities}
    assert RunManifest.load(run.paths.manifest).step("push").status is StepStatus.DONE


def test_an_agent_that_never_previews_cannot_push(served, data_dir, relate, master):
    """Decision 37: confirm is not enough; the agent must preview first (A2 and A5)."""
    run = staged(data_dir)
    assert RunManifest.load(run.paths.manifest).step("review").status is StepStatus.PENDING
    result = call(served, "apply_push", run_id=run.id, confirm=True)
    assert result.is_error and "review" in text(result) and relate.real_pushes == []


def test_preview_push_is_the_plan_and_the_delta_and_sends_nothing(served, data_dir, relate, master):
    run = staged(data_dir)
    result = call(served, "preview_push", run_id=run.id)
    review = contract.Review.model_validate(result.structured_content)
    assert review.run_id == run.id and {e.entity for e in review.plan} >= {"Account", "Alert", "RiskCommunity"}
    assert review.delta.accounts_scored == 3 and relate.real_pushes == [] and master.writes() == []


def test_a_bad_run_is_an_error_an_agent_can_read(served):
    for name, arguments in (("preview_push", {"run_id": ABSENT}), ("preview_push", {"run_id": "../../x"}),
                            ("score", {"run_id": ABSENT}), ("explain_alert", {"run_id": ABSENT, "key": "k"})):
        result = call(served, name, **arguments)
        assert result.is_error, name
    assert "no run" in text(call(served, "preview_push", run_id=ABSENT))
    assert "run-20261112T214500Z" in text(call(served, "preview_push", run_id="../../x"))
    assert call(served, "preview_push").is_error  # a missing argument is refused too


def wait_for_step(served, run_id: str, step: str, timeout: float = 20.0) -> RunManifest:
    """Poll run_status the way an agent does (decision 38) until `step` is done or failed."""
    seen: dict = {}

    def done() -> bool:
        seen["m"] = RunManifest.model_validate(call(served, "run_status", run_id=run_id).structured_content)
        return seen["m"].step(step).status in (StepStatus.DONE, StepStatus.FAILED)

    wait_until(done, timeout=timeout, what=f"{step} on {run_id}")
    return seen["m"]


@pytest.fixture
def working(served):
    """The served app with its worker running, as `serve()` has it (the fixture builds it with supervise=False)."""
    served.state.worker.start()
    yield served
    served.state.worker.stop()


def test_score_queues_the_step_answers_at_once_and_run_status_follows_it(working, data_dir, monkeypatch):
    served = working
    run = Run.create(Recipe(models=["communities"]), data_dir=data_dir)
    assert "has not been pulled" in text(call(served, "score", run_id=run.id))
    run.manifest.finish("pull")
    run.save()

    def score(self):
        self.manifest.finish("score", {"accounts": 7})
        self.save()
        return {"accounts": 7, "alerts": 2, "communities": 1}

    monkeypatch.setattr(Run, "score", score)
    result = call(served, "score", run_id=run.id)
    out = tool_contract.ScoreOut.model_validate(result.structured_content)
    assert out.status == "queued" and out.run_id == run.id and out.accounts_scored is None
    manifest = wait_for_step(served, run.id, "score")
    assert manifest.step("score").status is StepStatus.DONE and manifest.step("score").counts == {"accounts": 7}
    assert manifest.step("review").status is StepStatus.PENDING  # it stopped after score, as asked
    assert served.state.worker.snapshot()["held"] == []


def test_a_run_the_worker_has_is_not_written_to_by_a_tool(served, data_dir):
    run = staged(data_dir)
    run.manifest.finish("pull")
    run.save()
    with served.state.worker.exclusive(run.id):
        for name, arguments in (("preview_push", {}), ("apply_push", {"confirm": True}), ("score", {})):
            result = call(served, name, run_id=run.id, **arguments)
            assert result.is_error and "in use" in text(result), name


def test_egress_pulls_and_runs_the_first_pass_when_the_recipe_asks(working, data_dir, monkeypatch):
    served = working
    calls: list[str] = []

    def pull(self):
        calls.append("pull")
        return PullResult(route=Route.RELATE, nodes=5, relationships=7, constraints=0, seconds=0.1)

    def first_pass(self):
        calls.append("first_pass")
        self.manifest.finish("first_pass", {})
        self.save()
        return {}

    def pull_done(self):
        result = pull(self)
        self.manifest.finish("pull", {"nodes": 5, "relationships": 7})
        self.save()
        return result

    monkeypatch.setattr(Run, "pull", pull_done)
    monkeypatch.setattr(Run, "first_pass", first_pass)
    result = call(served, "egress", recipe={"models": ["node2vec", "communities"]})
    out = tool_contract.EgressOut.model_validate(result.structured_content)
    assert out.status == "queued" and Run(out.run_id).id == out.run_id  # decision 38: answered at once
    manifest = wait_for_step(served, out.run_id, "first_pass")
    assert calls == ["pull", "first_pass"] and manifest.step("pull").counts == {"nodes": 5, "relationships": 7}
    assert manifest.step("score").status is StepStatus.PENDING  # it stopped after the first pass, as asked
    calls.clear()
    second = call(served, "egress", recipe={"models": ["communities"]}, pull={"mode": "schema", "labels": ["Account"]})
    wait_for_step(served, second.structured_content["run_id"], "pull")
    assert calls == ["pull"]  # nothing in this recipe needs embeddings
    saved = Recipe.load(Run(second.structured_content["run_id"]).paths.recipe)
    assert saved.pull.labels == ["Account"]  # the pull argument overrides the recipe's


def test_search_slice_takes_a_run_and_a_search_and_answers_with_the_estimate(served, data_dir, monkeypatch):
    run = staged(data_dir)
    seen = []
    est = contract.SliceEstimate(mode="search", hits=[contract.Hit(key="A0001", rrf=0.03, found_by=["vector"])], hops=1,
                                 nodes=3, relationships=2, memory_bytes=100, over_cap=False, cap=10)
    monkeypatch.setattr("lab.slice.search.search_slice", lambda r, spec: seen.append((r.id, spec)) or est)
    result = call(served, "search_slice", run_id=run.id, search={"text": "new accounts", "k": 5, "hops": 1})
    assert contract.SliceEstimate.model_validate(result.structured_content) == est
    assert seen[0][0] == run.id and seen[0][1].text == "new accounts" and seen[0][1].k == 5
    assert call(served, "search_slice", run_id=run.id, search={}).is_error  # a search needs text or an account


# ---- explain, describe, diff -------------------------------------------------------------------------------------
def test_explain_alert_returns_an_evidence_object_or_says_not_yet(served, data_dir):
    run = staged(data_dir)
    # as scoring stages them today: the alert's evidence is scoring's own shape, not the checked object
    result = call(served, "explain_alert", run_id=run.id, key="alert-A0001")
    assert result.is_error and "no evidence object for this alert yet" in text(result) and "track 3b" in text(result)
    # once an evidence object is staged (track 3b), it comes back, found by alert key or by account key
    good = alert_frame([("alert-A0001", "A0001")]).with_columns(
        pl.lit(evidence("alert", "alert-A0001", ["A0001"])).alias("evidence"), pl.lit("Could indicate shared hardware.").alias("summary"),
        pl.lit("agent").alias("summary_source"), pl.lit("m1").alias("summary_model"), pl.lit("f" * 64).alias("evidence_hash"))
    run.stage(good, kind="alert")
    run.close()
    for key in ("alert-A0001", "A0001"):
        result = call(served, "explain_alert", run_id=run.id, key=key)
        assert not result.is_error, text(result)
        out = tool_contract.ExplainOut.model_validate(result.structured_content)
        assert out.evidence.key == "alert-A0001" and out.evidence.subject == "alert"
        assert out.summary.text.startswith("Could indicate") and out.summary.source == "agent" and out.summary.model == "m1"
    community = f"community-{run.id[-6:]}"
    run.stage(community_frame(community, KEYS).with_columns(pl.lit(evidence("community", community, KEYS)).alias("evidence")),
              kind="community")
    run.close()
    out = tool_contract.ExplainOut.model_validate(call(served, "explain_alert", run_id=run.id, key=community).structured_content)
    assert out.evidence.subject == "community" and out.evidence.member_count == 3 and out.summary is None
    nothing = call(served, "explain_alert", run_id=run.id, key="no-such-key")
    assert nothing.is_error and "no alert, community or alerted account" in text(nothing)
    assert "no alert" in text(call(served, "explain_alert", run_id=run.id, key="A0003"))  # scored, but no alert


def test_describe_alert_stores_nothing_until_the_checks_exist(served, data_dir, monkeypatch):
    import lab.summaries

    run = staged(data_dir)
    before = run.paths.manifest.read_bytes(), sorted(p.name for p in run.paths.staged.iterdir())
    result = call(served, "describe_alert", run_id=run.id, key="alert-A0001", text="Fraud ring of 1,000,000 accounts.")
    assert result.is_error and "not available yet" in text(result) and "Nothing was stored" in text(result)
    assert (run.paths.manifest.read_bytes(), sorted(p.name for p in run.paths.staged.iterdir())) == before
    # the seam: when track 3b provides `lab.summaries.describe(run, key, text)`, the tool hands the text to it
    from lab.contracts.evidence import Summary

    def describe(run, key, text):
        return tool_contract.DescribeOut(stored=True, summary=Summary(key=key, text=text, source="agent", evidence_hash="h"))

    monkeypatch.setattr(lab.summaries, "describe", describe, raising=False)
    out = tool_contract.DescribeOut.model_validate(
        call(served, "describe_alert", run_id=run.id, key="alert-A0001", text="Could indicate shared hardware.").structured_content)
    assert out.stored and out.summary.key == "alert-A0001" and out.summary.source == "agent"


def test_graph_diff_says_what_changed_between_two_runs(served, data_dir):
    a = staged(data_dir, scores=0.5, keys=["A0001", "A0002", "A0003"], alerts=2)
    time.sleep(1.1)
    b = staged(data_dir, scores=0.9, keys=["A0002", "A0003", "A0004"], alerts=3)
    out = tool_contract.GraphDiffOut.model_validate(call(served, "graph_diff", run_a=a.id, run_b=b.id).structured_content)
    assert out.accounts_both == 2  # A0002 and A0003
    assert out.score_changes["0.2-0.5"] == 2 and sum(out.score_changes.values()) == 2  # each moved by 0.4
    assert (out.alerts_new, out.alerts_gone) == (2, 1)  # alerted in b only: A0003, A0004. in a only: A0001
    assert (out.communities_a, out.communities_b) == (1, 1)
    same = tool_contract.GraphDiffOut.model_validate(call(served, "graph_diff", run_a=a.id, run_b=a.id).structured_content)
    assert same.accounts_both == 3 and same.alerts_new == same.alerts_gone == 0 and same.score_changes["0.0-0.1"] == 3


# ---- the endpoint ----------------------------------------------------------------------------------------------
def test_the_endpoint_answers_only_to_the_loopback_host(served):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    accept = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    elsewhere = httpx.post(served.url, json=body, headers={**accept, "Host": "evil.example"})
    assert elsewhere.status_code == 421
    web_page = httpx.post(served.url, json=body, headers={**accept, "Origin": "https://evil.example"})
    assert web_page.status_code == 403
    assert httpx.get(f"http://127.0.0.1:{served.port}/api/health").json()["ok"] is True  # the API shares the server


# ---- live: A5, the whole loop through the tools --------------------------------------------------------------------
@pytest.mark.live
def test_mcp_live_an_agent_pulls_searches_scores_previews_and_pushes_and_apply_needs_confirm(live_neo4j, served, data_dir):
    from lab.common.neo4j import master_rows
    from tests.push_helpers import wipe_lab

    def labruns(run_id: str) -> int:
        return master_rows("MATCH (l:LabRun {run_id: $r}) RETURN count(l) AS n", {"r": run_id})[0]["n"]

    try:
        served.state.worker.start()
        egress = tool_contract.EgressOut.model_validate(call(
            served, "egress", recipe={"name": "mcp-live", "models": ["node2vec", "communities"]}).structured_content)
        assert egress.status == "queued"
        run_id = egress.run_id
        pulled = wait_for_step(served, run_id, "first_pass", timeout=900)
        assert pulled.step("pull").counts.get("nodes", 0) > 0 and pulled.routes["pull"] in ("relate", "bolt")
        hits = tool_contract.SliceEstimate.model_validate(call(
            served, "search_slice", run_id=run_id,
            search={"text": "money in and out within a day across new accounts", "k": 10, "hops": 1}).structured_content)
        assert hits.mode == "search" and hits.hits and not hits.over_cap
        scored = tool_contract.ScoreOut.model_validate(call(served, "score", run_id=run_id).structured_content)
        assert scored.status == "queued"
        done = wait_for_step(served, run_id, "score", timeout=900)
        assert done.step("score").status is StepStatus.DONE and done.step("score").counts["accounts"] > 0
        review = contract.Review.model_validate(call(served, "preview_push", run_id=run_id).structured_content)
        assert {e.entity for e in review.plan} >= {"Account"} and labruns(run_id) == 0
        refused = call(served, "apply_push", run_id=run_id)
        assert refused.is_error and "confirm=true" in text(refused) and labruns(run_id) == 0
        pushed = contract.PushResult.model_validate(call(served, "apply_push", run_id=run_id, confirm=True).structured_content)
        assert pushed.pushed and labruns(run_id) == 1
    finally:
        wipe_lab()
