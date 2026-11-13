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
