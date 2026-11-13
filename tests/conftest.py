"""Shared fixtures. Every unit test runs on the fixture graph; `live` tests need compose."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

os.environ["DO_NOT_TRACK"] = "1"

from lab.contracts.recipe import Recipe  # noqa: E402
from lab.runs import Run  # noqa: E402
from lab.settings import reload_settings  # noqa: E402
from tests.fixtures.graph_loader import build_fixture_store, ground_truth  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def fixture_store_path(tmp_path_factory) -> Path:
    """The fixture graph as a closed Relate store, built once per session. Copy it; do not write to it."""
    path = tmp_path_factory.mktemp("fixture") / "store"
    build_fixture_store(path)
    return path


@pytest.fixture
def data_dir(tmp_path, monkeypatch) -> Path:
    """A fresh LAB_DATA_DIR for one test, with settings reloaded to see it."""
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setenv("LAB_DATA_DIR", str(d))
    monkeypatch.setenv("LAB_FACTORS_FILE", str(REPO / "factors" / "factors.yaml"))
    monkeypatch.setenv("LAB_POLICY_FILE", str(REPO / "factors" / "policy.yaml"))
    monkeypatch.delenv("LAB_RUN_ID", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    reload_settings()
    yield d
    reload_settings()


@pytest.fixture
def fixture_run(data_dir, fixture_store_path) -> Run:
    """A run whose store holds the fixture graph, as if `relate pull` had just filled it."""
    run = Run.create(Recipe(), data_dir=data_dir)
    shutil.copytree(fixture_store_path, run.paths.store)
    yield run
    run.close()


@pytest.fixture(scope="session")
def truth():
    return ground_truth()


LOCAL_HOSTS = ("localhost", "127.0.0.1", "neo4j")


@pytest.fixture(scope="session")
def live_neo4j():
    """The local Neo4j, wiped and loaded with the fixture graph. Live tests only.

    Refuses to touch anything but a local Neo4j: this deletes every node on the master graph it is pointed at.
    """
    from urllib.parse import urlparse

    from lab.common.neo4j import neo4j_session
    from lab.settings import get_settings
    from tests.fixtures.graph_loader import CONSTRAINTS, GRAPH, LOADS

    s = reload_settings()
    host = urlparse(s.neo4j_uri).hostname
    if host not in LOCAL_HOSTS:
        pytest.fail(f"live tests wipe the master graph; refusing NEO4J_URI host {host!r}")
    import polars as pl

    try:
        with neo4j_session(s) as session:
            session.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS").consume()
            for stmt in CONSTRAINTS:
                session.run(stmt.replace("CREATE CONSTRAINT FOR", "CREATE CONSTRAINT IF NOT EXISTS FOR")).consume()
            for name, stmt in LOADS:
                rows = pl.read_csv(GRAPH / name, infer_schema=False).to_dicts()
                session.run(stmt, rows=rows).consume()
    except Exception as e:  # noqa: BLE001
        pytest.fail(f"live tests need the local Neo4j (docker compose --profile local up -d --no-deps neo4j): {e}")
    return get_settings()
