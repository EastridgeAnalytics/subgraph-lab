"""Wave 0d: the contracts load, validate, and match their exported JSON schemas."""
from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from pydantic import ValidationError

from lab.contracts import (
    ROUTES,
    TOOLS,
    FactorFile,
    Policy,
    PullSpec,
    Recipe,
    RunManifest,
    SearchSpec,
    staged_errors,
)
from lab.contracts.manifest import Route, StepStatus
from tools.export_schemas import OUT, render

REPO = Path(__file__).resolve().parent.parent


def test_schemas_on_disk_match_the_models():
    rendered = render()
    on_disk = {p.name: p.read_text(encoding="utf-8") for p in OUT.glob("*.json")}
    assert set(on_disk) == set(rendered), "run `uv run python tools/export_schemas.py`"
    stale = [n for n, text in rendered.items() if on_disk[n] != text]
    assert not stale, f"schemas out of date: {stale}; a contract change is a decision on 08"


def test_nine_tools_and_the_routes():
    assert set(TOOLS) == {"egress", "run_status", "search_slice", "score", "preview_push", "apply_push",
                          "explain_alert", "describe_alert", "graph_diff"}  # run_status: decision 38
    paths = {p for _, p, _, _ in ROUTES}
    assert "/api/runs/{run_id}/push" in paths and "/api/slice/estimate" in paths


def test_recipe_round_trips_through_yaml(tmp_path):
    r = Recipe(name="t", search=SearchSpec(text="money in and out within a day", k=10))
    p = tmp_path / "r.yaml"
    p.write_text(r.dump_yaml(), encoding="utf-8")
    assert Recipe.load(p) == r


def test_recipe_rules():
    with pytest.raises(ValidationError):
        PullSpec(mode="cypher")
    with pytest.raises(ValidationError):
        SearchSpec()


def test_factor_file_and_policy_load():
    ff = FactorFile.load(REPO / "factors" / "factors.yaml")
    assert {f.id for f in ff.factors} >= {"shared_device_5plus", "velocity_anomaly", "tgn"}
    assert len(ff.version) == 12
    assert Policy.load(REPO / "factors" / "policy.yaml").alert_threshold == 0.6


def test_factor_validation():
    with pytest.raises(ValidationError):
        FactorFile(version="1", factors=[{"id": "x", "name": "x", "kind": "cypher", "weight": 1}])
    with pytest.raises(ValidationError):
        FactorFile(version="1", factors=[{"id": "Bad-Id", "name": "x", "kind": "model", "source": "s",
                                          "weight": 1}])


def test_staged_table_contract():
    ok = pl.DataFrame({"account_id": ["A1", "A2"], "risk_score": [0.1, 0.9],
                       "risk_embedding": [[0.1, 0.2], [0.3, 0.4]], "risk_at": ["2026-11-12T10:00:00Z"] * 2})
    assert staged_errors(ok, "account_id") == []
    bad = pl.DataFrame({"account_id": ["A1", "A1"], "score": [0.1, 0.2]})
    errs = staged_errors(bad, "account_id")
    assert any("duplicate" in e for e in errs) and any("risk_" in e for e in errs)
    assert staged_errors(ok, "nope")


def test_manifest_records_steps_and_fallbacks(tmp_path):
    m = RunManifest(run_id="r1", recipe_hash="h")
    m.start("pull")
    m.fallback("pull", Route.BOLT, "Query API unreachable")
    m.finish("pull", {"nodes": 5})
    m.save(tmp_path / "m.json")
    back = RunManifest.load(tmp_path / "m.json")
    assert back.step("pull").status == StepStatus.DONE and back.step("pull").counts == {"nodes": 5}
    assert back.routes["pull"] == Route.BOLT and back.fallbacks[0].reason
