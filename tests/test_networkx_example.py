"""Track 3c: the NetworkX example, communities and money cycling (page 03), on the lab's own community code.

`lab.scoring.communities` is track 3a's. Without it the example has nothing to import, so this module is skipped
until 3a is merged.
"""
from __future__ import annotations

import ast
import inspect
import json
import runpy

import polars as pl
import pytest

import lab
from lab.common.store import rows
from lab.contracts.recipe import Recipe
from lab.contracts.staged import staged_errors
from lab.runs import Run

pytest.importorskip("lab.scoring.communities")

import examples.networkx as pkg  # noqa: E402
from examples.networkx import cycles_by_account, example  # noqa: E402
from tests.fixtures.make_fixture import L1, R1, R2  # noqa: E402

SLICE = list(dict.fromkeys(R1 + R2 + [f"A{i:04d}" for i in range(1, 31)]))  # 38 accounts, as a search slice would be


def write_slice(run) -> None:
    (run.paths.artifacts / "slice.json").write_text(
        json.dumps({"estimate": {"over_cap": False}, "keys": SLICE}), encoding="utf-8")


def pairs_store(data_dir, pairs: list[tuple[str, str]]) -> Run:
    """A store of accounts and the payments between them, one payment per pair given (a pair may repeat)."""
    run = Run.create(Recipe(), data_dir=data_dir)
    conn = run.store()
    names = sorted({a for pair in pairs for a in pair})
    conn.execute("UNWIND $keys AS k CREATE (:Account {account_id: k})", {"keys": names})
    batch = [{"s": s, "d": d, "id": f"T{i}"} for i, (s, d) in enumerate(pairs)]
    conn.execute("UNWIND $rows AS r MATCH (s:Account {account_id: r.s}), (d:Account {account_id: r.d}) "
                 "CREATE (s)-[:TX {tx_id: r.id, amount: 100.0, ts: datetime('2026-06-01T00:00:00Z')}]->(d)",
                 {"rows": batch})
    return run


# ---- the example ---------------------------------------------------------------------------------------------------
def test_the_example_is_importable_and_under_100_lines_of_code():
    assert cycles_by_account is pkg.cycles_by_account is example.cycles_by_account
    source = inspect.getsource(example)
    docstrings = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef) and ast.get_docstring(node, clean=False):
            first = node.body[0]
            docstrings.update(range(first.lineno, first.end_lineno + 1))
    code = [n for n, line in enumerate(source.splitlines(), 1)
            if line.strip() and not line.strip().startswith("#") and n not in docstrings]
    assert len(code) < 100, f"{len(code)} lines of code"


def test_the_example_uses_the_labs_community_code_and_has_no_louvain_of_its_own():
    """Decision 34: one place per job."""
    import lab.scoring.communities as communities

    assert example.communities is communities
    names = {n.id for n in ast.walk(ast.parse(inspect.getsource(example))) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(ast.parse(inspect.getsource(example))) if isinstance(n, ast.Attribute)}
    assert not [n for n in names if "louvain" in n.lower() or "modularity" in n.lower()]


def test_every_account_in_the_slice_has_a_count_and_the_ring_members_have_one_or_more(fixture_run):
    df = cycles_by_account(fixture_run, SLICE)
    assert df.schema == {"key": pl.Utf8, "cycles": pl.Int64}
    assert df["key"].to_list() == sorted(SLICE)
    assert (df["cycles"] >= 0).all()
    got = dict(zip(df["key"], df["cycles"], strict=True))
    assert all(got[a] > 0 for a in R1)  # R1's six accounts pay each other round in a ring
    assert all(got[a] == 0 for a in R2)  # structuring is eight accounts paying one collector: nothing comes back


def test_most_ordinary_accounts_in_a_slice_have_no_cycle(fixture_run):
    """A slice is the part of the graph a search picked, so it is sparse. The whole fixture is not: its 2,000
    ordinary payments between 180 accounts are random pairs, a dense random graph, and nearly every ordinary account
    lies on some short cycle there (7 of 182 on none, with counts into the hundreds). The count says how much money
    comes back; it is not a score on its own."""
    df = cycles_by_account(fixture_run, SLICE)
    ordinary = df.filter(~pl.col("key").is_in(R1 + R2))
    assert ordinary.height == 24
    assert ordinary.filter(pl.col("cycles") == 0).height > ordinary.height / 2


def test_the_whole_run_is_counted_when_there_is_no_slice(fixture_run):
    df = cycles_by_account(fixture_run)
    assert df.height == 200 and df["key"].n_unique() == 200
    got = dict(zip(df["key"], df["cycles"], strict=True))
    assert all(got[a] > 0 for a in R1) and all(got[a] > 0 for a in L1)  # a family that pays back and forth cycles too


def test_what_counts_as_a_cycle(data_dir):
    pairs = [("a", "b"), ("b", "c"), ("c", "a"), ("a", "b"), ("b", "c")]  # a three-ring; repeats count once
    pairs += [("d", "e"), ("e", "f")]  # a chain: money goes on, not back
    pairs += [("g", "h"), ("h", "g")]  # two accounts paying each other: a cycle of two
    pairs += [("s", "s")]  # an account paying itself is not a cycle
    long = [f"r{i}" for i in range(example.MAX_CYCLE + 1)]
    pairs += [(long[i], long[(i + 1) % len(long)]) for i in range(len(long))]  # a ring one account too long
    run = pairs_store(data_dir, pairs)
    try:
        df = cycles_by_account(run)
        got = dict(zip(df["key"], df["cycles"], strict=True))
    finally:
        run.close()
    assert {k: got[k] for k in "abcdefghs"} == {"a": 1, "b": 1, "c": 1, "d": 0, "e": 0, "f": 0, "g": 1, "h": 1, "s": 0}
    assert all(got[k] == 0 for k in long)
    assert example.MAX_CYCLE == 6


def test_a_ring_only_counts_inside_a_community(data_dir, monkeypatch):
    """The cycles are looked for among a community's members. Cut the graph into one community per account and a ring
    is on no cycle at all, though every payment is still there."""
    run = pairs_store(data_dir, [("a", "b"), ("b", "c"), ("c", "a")])
    try:
        assert cycles_by_account(run)["cycles"].to_list() == [1, 1, 1]
        monkeypatch.setattr(example.communities, "split_graph", lambda G: [{"a", "b"}, {"c"}])
        assert cycles_by_account(run)["cycles"].to_list() == [0, 0, 0]
    finally:
        run.close()


# ---- the ending: stage, then push ----------------------------------------------------------------------------------
def test_the_main_block_stages_risk_cycles_and_previews_the_push(fixture_run, monkeypatch, capsys):
    """`python -m examples.networkx.example`: the slice from `current_slice`, the counts, `run.stage(df)`, `run.push()`."""
    run = fixture_run
    write_slice(run)
    monkeypatch.setattr(lab, "run", lambda *a, **k: run)
    monkeypatch.setattr(Run, "push", lambda self, confirm=False: f"PREVIEW confirm={confirm}")  # the real one needs Aura
    runpy.run_path(example.__file__, run_name="__main__")
    assert capsys.readouterr().out.strip().endswith("PREVIEW confirm=False")
    counted = cycles_by_account(run, SLICE)
    staged_frame = counted.select(pl.col("key").alias("account_id"), pl.col("cycles").alias("risk_cycles"))
    assert staged_errors(staged_frame, "account_id") == []
    query = "MATCH (a:Account) WHERE a.risk_cycles IS NOT NULL RETURN a.account_id AS k, a.risk_cycles AS v"
    staged = {r["k"]: r["v"] for r in rows(run.push_store(), query)}
    assert staged == dict(zip(counted["key"], counted["cycles"], strict=True)) and len(staged) == len(SLICE)
