"""Track 2d: the `relate serve --read-only=live` the lab supervises (decision 21).

Most tests patch `subprocess.Popen` and check what would have been started, stopped and recorded. One starts the real
binary on a copy of the fixture store, on free ports, and talks to it.
"""
from __future__ import annotations

import json
import socket
import time
import urllib.request

import pytest

from lab.api import relate_server
from lab.api.relate_server import CONTAINER_PORTS, RelateServer, current_run_id, serve_ports
from lab.common.relate_cli import relate_bin
from lab.contracts.recipe import Recipe
from lab.runs import Run
from lab.settings import get_settings, reload_settings


def wait_until(check, timeout: float = 20.0, what: str = "the condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeProc:
    """Enough of a `Popen` for the supervisor: it is alive until it is terminated or told to die."""

    def __init__(self, cmd, **kwargs):
        self.cmd, self.kwargs, self.pid = cmd, kwargs, 4000 + len(FakePopen.started)
        self.code: int | None = None
        self.terminated = False
        FakePopen.started.append(self)

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated, self.code = True, 1

    def kill(self):
        self.code = 1

    def wait(self, timeout=None):
        return self.code

    def die(self, code: int = 2):
        self.code = code
        self.kwargs["stdout"].write(b"Error: address already in use\n")
        self.kwargs["stdout"].flush()


class FakePopen:
    started: list[FakeProc] = []

    def __call__(self, cmd, **kwargs):
        return FakeProc(cmd, **kwargs)


@pytest.fixture
def popen():
    FakePopen.started = []
    return FakePopen()


def pulled(data_dir, name: str | None = None) -> Run:
    """A run whose pull is done, with a store folder (empty: the fake never opens it)."""
    run = Run.create(Recipe(), data_dir=data_dir, run_id=name)
    run.paths.store.mkdir()
    run.manifest.finish("pull")
    run.save()
    return run


def arg(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


# ---- which run is current ---------------------------------------------------------------------------------------
def test_the_current_run_is_the_newest_whose_pull_is_done(data_dir):
    assert current_run_id(data_dir) is None
    Run.create(Recipe(), data_dir=data_dir, run_id="run-20261112T100000Z-aaaaaa")  # pull still pending
    assert current_run_id(data_dir) is None
    done = pulled(data_dir, "run-20261112T110000Z-bbbbbb")
    Run.create(Recipe(), data_dir=data_dir, run_id="run-20261112T120000Z-cccccc")  # newer, but not pulled
    assert current_run_id(data_dir) == done.id
    newest = pulled(data_dir, "run-20261112T130000Z-dddddd")
    assert current_run_id(data_dir) == newest.id


def test_a_run_whose_pull_is_marked_done_but_has_no_store_is_not_served(data_dir):
    run = Run.create(Recipe(), data_dir=data_dir)
    run.manifest.finish("pull")
    run.save()
    assert current_run_id(data_dir) is None


# ---- start, restart, stop ---------------------------------------------------------------------------------------
def test_it_serves_the_current_run_read_only_live_with_every_flag(data_dir, popen):
    run = pulled(data_dir)
    s = get_settings()
    server = RelateServer(popen=popen)
    server.ensure()
    [proc] = popen.started
    assert proc.cmd[0] == relate_bin() and proc.cmd[1] == "serve"
    assert arg(proc.cmd, "--database") == str(run.paths.store.resolve())
    assert "--read-only=live" in proc.cmd and arg(proc.cmd, "--auth") == "none"
    assert arg(proc.cmd, "--host") == "127.0.0.1"
    assert (arg(proc.cmd, "--bolt"), arg(proc.cmd, "--http"), arg(proc.cmd, "--mcp")) == (
        str(s.relate_bolt_port), str(s.relate_http_port), str(s.relate_mcp_port))
    assert proc.kwargs["env"]["DO_NOT_TRACK"] == "1"
    record = json.loads((data_dir / "relate-serve.json").read_text(encoding="utf-8"))
    assert record["run_id"] == run.id and record["store"] == str(run.paths.store.resolve()) and record["pid"] == proc.pid
    assert server.status()["serving"]["run_id"] == run.id and server.status()["error"] is None
    server.ensure()
    assert len(popen.started) == 1  # looking again changes nothing
    server.stop()


def test_inside_the_container_it_listens_on_the_container_ports(data_dir, popen, monkeypatch):
    monkeypatch.setenv("LAB_RELATE_HOST", "0.0.0.0")
    monkeypatch.setenv("LAB_RELATE_BOLT_PORT", "9999")  # a host port: not what the container listens on
    reload_settings()
    pulled(data_dir)
    assert serve_ports()["lab"] == 8000 and serve_ports()["bolt"] == 7687
    server = RelateServer(popen=popen)
    server.ensure()
    cmd = popen.started[0].cmd
    assert arg(cmd, "--host") == "0.0.0.0"
    assert (arg(cmd, "--bolt"), arg(cmd, "--http"), arg(cmd, "--mcp")) == tuple(str(CONTAINER_PORTS[k]) for k in ("bolt", "http", "mcp"))
    server.stop()


def test_a_newer_run_restarts_it_on_that_store(data_dir, popen):
    old = pulled(data_dir, "run-20261112T100000Z-aaaaaa")
    server = RelateServer(popen=popen)
    server.ensure()
    first = popen.started[0]
    new = pulled(data_dir, "run-20261112T110000Z-bbbbbb")
    server.ensure()
    assert first.terminated and len(popen.started) == 2
    assert arg(popen.started[1].cmd, "--database") == str(new.paths.store.resolve())
    assert json.loads((data_dir / "relate-serve.json").read_text(encoding="utf-8"))["run_id"] == new.id
    assert old.id != new.id
    server.stop()


def test_stop_stops_the_server_and_clears_the_record(data_dir, popen):
    pulled(data_dir)
    server = RelateServer(popen=popen)
    server.ensure()
    server.stop()
    assert popen.started[0].terminated
    assert not (data_dir / "relate-serve.json").exists() and server.status()["serving"] is None
    server.stop()  # twice is fine


def test_the_supervisor_notices_a_new_run_on_its_own(data_dir, popen):
    server = RelateServer(popen=popen, poll_seconds=0.05)
    server.start()
    try:
        time.sleep(0.2)
        assert popen.started == []  # nothing pulled yet: nothing to serve
        run = pulled(data_dir)
        wait_until(lambda: popen.started, what="the server to start")
        assert arg(popen.started[0].cmd, "--database") == str(run.paths.store.resolve())
    finally:
        server.stop()
    assert popen.started[0].terminated


def test_a_server_that_dies_is_reported_and_not_restarted_in_a_hot_loop(data_dir, popen):
    pulled(data_dir)
    server = RelateServer(popen=popen, retry_seconds=60)
    server.ensure()
    popen.started[0].die()
    server.ensure()
    status = server.status()
    assert status["serving"] is None and "exited with code 2" in status["error"] and "address already in use" in status["error"]
    server.ensure()
    assert len(popen.started) == 1  # it failed a moment ago; the same attempt would fail the same way
    again = RelateServer(popen=popen, retry_seconds=0)
    again.ensure()
    popen.started[-1].die()
    again.ensure()
    again.ensure()
    assert len(popen.started) >= 2  # with no wait it tries again
    server.stop()
    again.stop()


def test_a_missing_binary_is_a_status_not_a_crash(data_dir, popen, monkeypatch):
    monkeypatch.setenv("LAB_RELATE_BIN", "no-such-relate-binary")
    monkeypatch.setattr(relate_server, "_version", None)
    reload_settings()
    pulled(data_dir)
    server = RelateServer(popen=popen)
    server.ensure()
    status = server.status()
    assert popen.started == [] and status["binary"] is False and status["serving"] is None
    assert "not found" in status["error"]
    server.stop()


# ---- the real thing ---------------------------------------------------------------------------------------------
def query(http: int, statement: str) -> list:
    req = urllib.request.Request(
        f"http://127.0.0.1:{http}/db/neo4j/query/v2", data=json.dumps({"statement": statement}).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())["data"]["values"]


def test_a_real_relate_serve_answers_and_follows_the_labs_writes(fixture_run, monkeypatch):
    bolt, http, mcp = free_port(), free_port(), free_port()
    monkeypatch.setenv("LAB_RELATE_BOLT_PORT", str(bolt))
    monkeypatch.setenv("LAB_RELATE_HTTP_PORT", str(http))
    monkeypatch.setenv("LAB_RELATE_MCP_PORT", str(mcp))
    reload_settings()
    fixture_run.manifest.finish("pull")
    fixture_run.save()
    server = RelateServer()
    server.ensure()
    try:
        def discovery():
            try:
                return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{http}/", timeout=2).read())
            except OSError:
                return None

        doc = wait_until(discovery, what="relate serve to answer on its HTTP surface")
        assert doc["neo4j_version"] and doc["bolt_direct"].endswith(str(bolt))
        assert query(http, "MATCH (a:Account) RETURN count(a)") == [[200]]
        # the lab writes, and the served store follows (--read-only=live)
        conn = fixture_run.store()
        conn.execute("CREATE (:Marker {n: 1})")
        fixture_run.close()
        wait_until(lambda: query(http, "MATCH (m:Marker) RETURN count(m)") == [[1]], what="the served store to follow the lab")
        status = server.status()
        assert status["serving"]["run_id"] == fixture_run.id and status["serving"]["http"] == http
        # and it is read-only: a write through it is refused
        with pytest.raises(OSError):
            query(http, "CREATE (:Nope)")
    finally:
        server.stop()
    with pytest.raises(OSError):
        urllib.request.urlopen(f"http://127.0.0.1:{http}/", timeout=2)
    assert not (fixture_run.paths.root.parent.parent / "relate-serve.json").exists()
