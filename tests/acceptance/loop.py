"""The whole loop, from compose up to a verified LabRun in the bundled Neo4j (page 97, "Verification across the plan").

    uv run python tests/acceptance/loop.py [--through PHASE] [--keep] [--since ISO] [--search TEXT]

The director runs it after every merge. Phases run in order and stop after `--through`:

    up        the bundled Neo4j with the seed is healthy
    pull      a schema slice from the master graph into a fresh run store
    first     the first pass: embeddings and summaries (wave 2)
    search    a search-defined slice inside the run store (wave 2)
    score     factors, policy, alerts; communities and summaries when they exist (waves 2 and 3)
    review    the dry run plus the delta; the master graph unchanged
    push      apply with confirm; LabRun verified on the master graph

Before wave 2, `score` stages a hand-made table instead. Without `--keep` the run is retracted at the end, so the
demo graph stays clean. Every timing printed names this run's id; none is a published measurement.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ["DO_NOT_TRACK"] = "1"
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

PHASES = ("up", "pull", "first", "search", "score", "review", "push")


def say(msg: str) -> None:
    print(f"[loop] {msg}", flush=True)


def up() -> None:
    subprocess.run(["docker", "compose", "--profile", "local", "up", "-d", "neo4j", "seed-schema"], cwd=REPO,
                   check=True, capture_output=True)
    from lab.common.neo4j import master_rows

    for _ in range(60):
        try:
            n = master_rows("MATCH (a:Account) RETURN count(a) AS n")[0]["n"]
            if n:
                say(f"bundled Neo4j up with {n} accounts")
                return
        except Exception:  # noqa: BLE001 (still starting)
            pass
        time.sleep(2)
    raise SystemExit("the bundled Neo4j did not come up with the seed")


def has(module: str, name: str) -> bool:
    try:
        mod = __import__(module, fromlist=[name])
    except ImportError:
        return False
    return hasattr(mod, name)


def hand_made_scores(run):
    """Before wave 2: a table with a key column and risk_* columns, the one format in."""
    import polars as pl

    keys = run.table("MATCH (a:Account) RETURN a.account_id AS k ORDER BY k LIMIT 100")["k"].to_list()
    return pl.DataFrame({"account_id": keys, "risk_score": [round(i / 100, 2) for i in range(len(keys))]})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--through", choices=PHASES, default="push")
    ap.add_argument("--keep", action="store_true", help="leave the run's results on the master graph")
    ap.add_argument("--since", default="2026-08-15T00:00:00Z")
    ap.add_argument("--search", default="money in and out within a day across new accounts")
    a = ap.parse_args()
    stop = PHASES.index(a.through)
    t0 = time.time()

    up()
    if stop < PHASES.index("pull"):
        return 0

    from lab.common.neo4j import master_rows
    from lab.contracts.recipe import PullSpec, Recipe, SearchSpec
    from lab.runs import Run

    recipe = Recipe(name="loop", pull=PullSpec(since=a.since), search=SearchSpec(text=a.search))
    run = Run.create(recipe)
    say(f"run {run.id}")
    t = time.time()
    result = run.pull()
    say(f"pull: route {result.route}, {result.nodes} nodes, {result.relationships} relationships, "
        f"{time.time() - t:.1f}s")
    assert result.nodes > 0 and result.relationships > 0

    if stop >= PHASES.index("first") and has("lab.slice.first_pass", "first_pass"):
        t = time.time()
        say(f"first pass: {run.first_pass()} in {time.time() - t:.1f}s")
    if stop >= PHASES.index("search") and has("lab.slice.search", "search_slice"):
        est = run.search()
        top = [h.key for h in est.hits[:5]]
        say(f"search: {len(est.hits)} hits, {est.nodes} nodes within {est.hops} hops, over cap {est.over_cap}; top {top}")
        assert est.hits, "the demo search found nothing"

    if stop >= PHASES.index("score"):
        if "tgn" in run.recipe.models and has("examples.tgn", "run_model"):
            from lab.pipeline import _models

            t = time.time()
            say(f"models: {_models(run)} in {time.time() - t:.1f}s")
        if has("lab.scoring.pipeline", "score"):
            t = time.time()
            say(f"score: {run.score()} in {time.time() - t:.1f}s")
        else:
            staged = run.stage(hand_made_scores(run), "account_id", "account")
            say(f"staged a hand-made table: {staged} accounts")

    if stop >= PHASES.index("review"):
        before = master_rows("MATCH (n) WHERE n:Alert OR n:RiskCommunity OR n:LabRun RETURN count(n) AS n")[0]["n"]
        review = run.preview_push()
        after = master_rows("MATCH (n) WHERE n:Alert OR n:RiskCommunity OR n:LabRun RETURN count(n) AS n")[0]["n"]
        assert before == after, "the review wrote to the master graph"
        say("review: " + ", ".join(f"{e.entity} {e.total}" for e in review.plan))
        say(f"delta: {json.dumps(review.delta.model_dump())}")

    if stop >= PHASES.index("push"):
        res = run.apply_push(confirm=True)
        assert res.pushed, res.message
        rows = master_rows("MATCH (r:LabRun {run_id: $id}) RETURN r.finished_at AS f, r.counts AS c", {"id": run.id})
        assert rows, "no LabRun on the master graph"
        say(f"push: LabRun {run.id} landed at {rows[0]['f']}")
        scored = master_rows("MATCH (a:Account {risk_run_id: $id}) RETURN count(a) AS n", {"id": run.id})[0]["n"]
        say(f"master graph: {scored} accounts carry this run's scores")
        top = master_rows(
            "MATCH (c:RiskCommunity {run_id: $id}) OPTIONAL MATCH (a:Account)-[:IN_RISK_COMMUNITY]->(c) "
            "WITH c, count(a) AS members ORDER BY c.score DESC LIMIT 1 "
            "RETURN c.community_key AS key, c.score AS score, c.summary AS summary, c.summary_source AS source, members",
            {"id": run.id})
        communities = master_rows("MATCH (c:RiskCommunity {run_id: $id}) RETURN count(c) AS n", {"id": run.id})[0]["n"]
        if communities:
            t0c = top[0]
            say(f"master graph: {communities} RiskCommunity nodes; the top one scores {t0c['score']:.2f} with "
                f"{t0c['members']} members linked, summary from {t0c['source']}: {(t0c['summary'] or '')[:160]}")
            assert t0c["members"] > 0 and t0c["summary"], "the top community landed without members or a summary"
            from lab.api import results
            from lab.api.view import RunView

            try:
                detail = results.community_detail(RunView(run.id), t0c["key"])
                say("communities like this one: " + ", ".join(f"{r.key[:8]} ({r.score:.2f})" for r in detail.similar[:3]))
            except Exception as e:  # noqa: BLE001 (reported, not fatal: the push already landed)
                say(f"communities like this one: not available ({type(e).__name__}: {e})")
        if not a.keep:
            from lab.roundtrip.retract import retract

            retract(run, confirm=True)
            left = master_rows("MATCH (n {run_id: $id}) RETURN count(n) AS n", {"id": run.id})[0]["n"]
            left += master_rows("MATCH (a:Account {risk_run_id: $id}) RETURN count(a) AS n", {"id": run.id})[0]["n"]
            assert left == 0, "retract left results behind"
            say("retracted: the demo graph is clean")
    run.close()
    say(f"done through {a.through} in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
