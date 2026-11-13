"""Track 2c: the TGN example (page 03) on the fixture graph, and the `tgn` model factor it feeds (page 04).

The fixture has 2,000 payments between 200 accounts. Its ordinary payments are random pairs, so there is nothing for
a link predictor to learn there, and the planted rings (R1, R2) are a few dozen events. These tests check the
contract and the wiring, not accuracy. `test_the_loop_learns_a_predictable_stream` is the one check that the
training loop learns anything at all, on a hand-made stream, because the fixture cannot show it.
"""
from __future__ import annotations

import ast
import inspect
import json
import runpy
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest
import torch
from torch_geometric.data import TemporalData

import lab
from examples.tgn import example, run_model
from lab.adapters import to_temporal
from lab.common.store import rows
from lab.contracts.recipe import Recipe
from lab.runs import Run
from lab.scoring import run_factors
from tests.fixtures.make_fixture import R1, R2

REPO = Path(__file__).resolve().parent.parent
SCHEMA = {"key": pl.Utf8, "score": pl.Float64, "evidence": pl.Utf8}
SLICE = R1 + R2 + [f"A{i:04d}" for i in range(1, 31)]  # 44 accounts, 76 payments between them


def model_file(run) -> pl.DataFrame:
    return pl.read_parquet(run.paths.artifacts / "models" / "tgn.parquet")


def recent_sources(run, keys=None) -> tuple[set[str], int]:
    """The accounts with an outgoing payment in the latest `RECENT` of the window, and how many such payments."""
    g = to_temporal(run, keys=keys)
    t, src = g.data.t.numpy(), g.data.src.numpy()
    recent = t >= t.min() + (1 - example.RECENT) * (t.max() - t.min())
    return {g.keys[s] for s in src[recent]}, int(recent.sum())


def write_slice(run) -> None:
    """What `lab.slice.search` leaves behind, and `current_slice(run)` reads."""
    (run.paths.artifacts / "slice.json").write_text(
        json.dumps({"estimate": {"over_cap": False}, "keys": SLICE}), encoding="utf-8")


@pytest.fixture(scope="module")
def trained(fixture_store_path, tmp_path_factory):
    """A run on the fixture store with the TGN example run on all of it, once for the module: (run, rows, seconds)."""
    run = Run.create(Recipe(factors_file=str(REPO / "factors" / "factors.yaml")),
                     data_dir=tmp_path_factory.mktemp("tgn_data"))
    shutil.copytree(fixture_store_path, run.paths.store)
    started = time.perf_counter()
    count = run_model(run)
    seconds = time.perf_counter() - started
    yield run, count, seconds
    run.close()


@pytest.fixture
def one_thread():
    """What `run_model` does around training, for a test that calls `surprise` itself: with parallel float sums
    in no fixed order a seed does not repeat a run, and a small model is no faster on ten threads."""
    threads = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(threads)


@pytest.fixture
def one_epoch(monkeypatch):
    """The tests that train again do it for one epoch: they check wiring and determinism, not learning."""
    monkeypatch.setattr(example, "EPOCHS", 1)


# ---- the example -----------------------------------------------------------------------------------------------
def test_the_tgn_example_is_importable_and_under_100_lines_of_code():
    import examples.tgn as pkg

    assert run_model is pkg.run_model is example.run_model
    source = inspect.getsource(example)
    docstrings = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef) and ast.get_docstring(node, clean=False):
            first = node.body[0]
            docstrings.update(range(first.lineno, first.end_lineno + 1))
    code = [n for n, line in enumerate(source.splitlines(), 1)
            if line.strip() and not line.strip().startswith("#") and n not in docstrings]
    assert len(code) < 100, f"{len(code)} lines of code"


def test_run_model_writes_one_row_per_scored_account(trained):
    run, count, _ = trained
    df = model_file(run)
    assert df.schema == SCHEMA
    assert count == df.height > 0
    assert df["key"].n_unique() == df.height
    assert df["score"].is_between(0.0, 1.0).all() and df["score"].null_count() == 0
    accounts = set(run.table("MATCH (a:Account) RETURN a.account_id AS k")["k"])
    assert set(df["key"]) <= accounts
    # exactly the accounts with an outgoing payment in the recent part of the window, and nobody else
    sources, events = recent_sources(run)
    assert set(df["key"]) == sources
    evidence = [json.loads(e) for e in df["evidence"]]
    assert sum(e["events"] for e in evidence) == events
    assert all(set(e) == {"events", "mean_surprise"} and 0.0 <= e["mean_surprise"] <= 1.0 for e in evidence)


def test_the_score_is_the_rank_of_the_mean_surprise(trained):
    """More surprising on average means a higher score, and a score is a share of the scored accounts."""
    df = model_file(trained[0])
    mean = [json.loads(e)["mean_surprise"] for e in df["evidence"]]
    score = df["score"].to_list()
    for i in range(len(mean)):
        for j in range(len(mean)):
            if mean[i] < mean[j]:
                assert score[i] <= score[j]
    n = df.height
    assert sorted(score)[0] >= 0.5 / n - 1e-12 and sorted(score)[-1] <= 1 - 0.5 / n + 1e-12
    assert sum(score) / n == pytest.approx(0.5)  # mid-rank percentiles of n accounts always average one half


def test_every_planted_ring_account_has_a_row(trained):
    """The rings are in October, inside the recent part of the window, so each of their accounts is scored. Where they
    rank is not asserted: the score is unsupervised and the fixture is tiny (the report says where they land)."""
    got = set(model_file(trained[0])["key"])
    assert set(R1) <= got and set(R2) <= got


def test_the_same_seed_gives_the_same_scores(fixture_run, one_epoch):
    run_model(fixture_run)
    first = model_file(fixture_run)
    run_model(fixture_run)
    assert model_file(fixture_run).equals(first)  # exactly: CPU, one thread, one seed, the same event order


def test_another_seed_gives_other_scores(fixture_run, one_epoch, monkeypatch):
    run_model(fixture_run, SLICE)
    first = model_file(fixture_run)
    monkeypatch.setattr(example, "get_settings", lambda: SimpleNamespace(seed=7))
    run_model(fixture_run, SLICE)
    other = model_file(fixture_run)
    assert other["key"].to_list() == first["key"].to_list()
    # the evidence, not the ranks: one epoch on 76 payments barely moves the model, and ranks can survive a new seed
    assert other["evidence"].to_list() != first["evidence"].to_list()


def test_with_keys_it_scores_only_those_accounts(fixture_run, one_epoch):
    count = run_model(fixture_run, SLICE)
    df = model_file(fixture_run)
    sources, events = recent_sources(fixture_run, SLICE)
    assert count == df.height == len(sources) > 0
    assert set(df["key"]) == sources <= set(SLICE)
    assert sum(json.loads(e)["events"] for e in df["evidence"]) == events
    assert df["score"].is_between(0.0, 1.0).all()


def test_an_empty_slice_writes_an_empty_file_and_the_factor_still_runs(fixture_run):
    assert run_model(fixture_run, []) == 0
    df = model_file(fixture_run)
    assert df.height == 0 and df.schema == SCHEMA
    status = {s.id: s for s in run_factors(fixture_run, []).status}
    assert status["tgn"].status == "ran" and status["tgn"].accounts == 0


def test_the_tgn_factor_ran(trained):
    run, count, _ = trained
    fr = run_factors(run)
    status = {s.id: s for s in fr.status}
    assert status["tgn"].status == "ran" and status["tgn"].accounts == count
    tgn = fr.results.filter(pl.col("factor_id") == "tgn")
    assert tgn.height == count and tgn["score"].is_between(0.0, 1.0).all()
    assert all(json.loads(e)["events"] >= 1 for e in tgn["evidence"])


def test_the_pipeline_models_step_finds_the_example_and_counts_its_accounts(fixture_run, one_epoch):
    from lab.pipeline import _models

    fixture_run.recipe = Recipe(models=["tgn"])
    write_slice(fixture_run)  # the step passes `current_slice(run)` as the keys
    counts = _models(fixture_run)
    assert counts == {"tgn_accounts": model_file(fixture_run).height} and counts["tgn_accounts"] > 0


def predictable_stream(rounds: int) -> TemporalData:
    """Each of 15 accounts always pays its own partner, once an hour, `rounds` times. Nothing like the fixture."""
    src = torch.arange(15).repeat(rounds)
    return TemporalData(src=src, dst=src + 15, t=torch.arange(src.numel()) * 3600,
                        msg=torch.full((src.numel(), 1), 5.0))


def test_surprise_is_one_number_per_event_in_the_unit_interval(one_thread, one_epoch):
    data = predictable_stream(20)
    s = example.surprise(data, 30, seed=42)
    assert s.shape == (data.num_events,) and 0.0 <= float(s.min()) and float(s.max()) <= 1.0


@pytest.mark.slow
def test_the_loop_learns_a_predictable_stream(one_thread, monkeypatch):
    """The fixture cannot show that the training loop learns: its ordinary payments are random pairs, and the loss
    stays near 2 ln 2 = 1.386 (random guessing). On a stream where the next payment is always predictable, a model
    that learns is far less surprised than the 0.5 of an untrained one. 15,000 payments, three epochs on one
    thread: the last quarter's mean surprise was 0.11, 0.008 and 0.06 for seeds 42, 1 and 2, in 28 to 42 seconds.
    (Five epochs on 1,500 payments did not learn on every seed, so this is the slow test, with more updates.)"""
    monkeypatch.setattr(example, "EPOCHS", 3)
    s = example.surprise(predictable_stream(1000), 30, seed=42)
    assert float(s[-len(s) // 4:].mean()) < 0.25


# ---- the ending: stage, then push ----------------------------------------------------------------------------------
def test_the_main_block_stages_risk_tgn_and_previews_the_push(fixture_run, monkeypatch, capsys):
    """`python -m examples.tgn.example`: the slice from `current_slice`, the model, `run.stage(df)`, `run.push()`."""
    run = fixture_run
    write_slice(run)
    monkeypatch.setattr(lab, "run", lambda *a, **k: run)
    monkeypatch.setattr(Run, "push", lambda self, confirm=False: f"PREVIEW confirm={confirm}")  # the real one needs Aura
    runpy.run_path(example.__file__, run_name="__main__")
    assert capsys.readouterr().out.strip().endswith("PREVIEW confirm=False")
    scored = model_file(run)
    assert 0 < scored.height and set(scored["key"]) <= set(SLICE)
    query = "MATCH (a:Account) RETURN a.account_id AS k, a.risk_tgn AS v"
    staged = {r["k"]: r["v"] for r in rows(run.push_store(), query)}
    assert staged == pytest.approx(dict(zip(scored["key"], scored["score"], strict=True)))
