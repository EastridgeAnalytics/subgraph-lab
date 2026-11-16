"""Measurements for the measured page, architecture-docs/98-measured.md (A8: every number names its run).

Each subcommand measures one thing and writes one record, architecture-docs/measured/<id>.json: what was measured, the
run ids, the machine, the command, the raw values, and `shown`: the exact strings the page prints. The page quotes a
record by its id, and `tests/acceptance/check_measured.py` checks that every number on the page is in the record it
names.

    uv run python tools/measure.py run RUN_ID [--truth DIR] [--label L]
        Step times and counts of a finished run, and its alerts, rings and look-alikes against the generator's ground
        truth (by default `python -m generator --size small --seed 42`, the bundled seed).
    uv run python tools/measure.py pull --label L [--since ISO] [--route relate|bolt]
        One schema pull from the master graph in the settings into a fresh run, timed, with its peak memory and the
        store's size on disk. relate is `relate pull`; bolt is the lab's fallback, run on purpose.
    uv run python tools/measure.py serve RUN_ID [--label L]
        `relate serve --read-only=live` on a run's store: its memory once it answers, and after a two-hop query.
    uv run python tools/measure.py image [--tag subgraph-lab:dev] [--build CLONE]
        The lab image's size, as Docker reports it; with --build, first built in a clean clone with no cache, timed.

Every Relate call runs with DO_NOT_TRACK=1. Nothing here writes to the master graph.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

os.environ["DO_NOT_TRACK"] = "1"
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import polars as pl  # noqa: E402
import psutil  # noqa: E402

OUT = REPO / "architecture-docs" / "measured"


# ---- the record ----------------------------------------------------------------------------------------------------
def machine() -> dict:
    from lab.api.relate_server import relate_version

    ok, version = relate_version()
    return {
        "os": platform.platform(), "cpu": platform.processor(), "logical_cpus": psutil.cpu_count(),
        "physical_cpus": psutil.cpu_count(logical=False), "ram_gb": round(psutil.virtual_memory().total / 2**30, 1),
        "python": platform.python_version(), "relate": version if ok else None,
    }


def write(kind: str, label: str, what: str, values: dict, shown: dict, runs: list[str]) -> Path:
    now = datetime.now(UTC)
    rid = f"{now:%Y%m%d}-{kind}-{label}"
    record = {
        "id": rid, "kind": kind, "what": what, "measured_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "runs": runs, "command": " ".join(["tools/measure.py", *sys.argv[1:]]), "machine": machine(),
        "shown": shown, "values": values,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{rid}.json"
    path.write_text(json.dumps(record, indent=1, default=str) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"id": rid, "shown": shown}, indent=1))
    return path


def seconds(s: float) -> str:
    return f"{s:,.0f} s" if s >= 10 else f"{s:.1f} s"


def megabytes(n: float) -> str:
    return f"{n / 2**20:,.0f} MB"


def ratio(x: float) -> str:
    return f"{x:.2f}"


def count(n: int) -> str:
    return f"{n:,}"


class PeakMemory:
    """Samples the resident memory of this process and its children every 100 ms. `peak_children` is the largest sum
    over the children (a `relate` subprocess); `peak_self` is this process's largest, less what it held at the start."""

    def __init__(self) -> None:
        self.me = psutil.Process()
        self.base = self.me.memory_info().rss
        self.peak_self = 0
        self.peak_children = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> PeakMemory:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.peak_self = max(self.peak_self, self.me.memory_info().rss - self.base)
                kids = 0
                for c in self.me.children(recursive=True):
                    try:
                        kids += c.memory_info().rss
                    except psutil.Error:
                        pass
                self.peak_children = max(self.peak_children, kids)
            except psutil.Error:
                pass
            self._stop.wait(0.1)


def dir_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


# ---- run -----------------------------------------------------------------------------------------------------------
def truth_dir(given: str | None) -> Path:
    if given:
        return Path(given)
    path = REPO / ".tmp" / "truth-small-42"
    if not (path / "ground_truth.parquet").exists():
        subprocess.run([sys.executable, "-m", "generator", "--size", "small", "--seed", "42", "--out", str(path)],
                       cwd=REPO, check=True)
    return path


def measure_run(run_id: str, truth: str | None, label: str | None) -> Path:
    from lab.common.store import run_paths
    from lab.contracts.manifest import RunManifest
    from lab.runs import Run
    from lab.scoring.communities import COMMUNITIES_FILE, ring_recall
    from lab.scoring.policy import load_policy

    paths = run_paths(run_id)
    manifest = RunManifest.load(paths.manifest)
    run = Run(run_id)
    policy = load_policy(run)
    steps = {}
    for name in ("pull", "first_pass", "slice", "models", "score", "communities", "summaries", "stage", "review", "push"):
        try:
            st = manifest.step(name)
        except (KeyError, ValueError):
            continue
        if st.started_at and st.finished_at:
            a, b = (datetime.fromisoformat(x.replace("Z", "+00:00")) for x in (st.started_at, st.finished_at))
            steps[name] = {"seconds": (b - a).total_seconds(), "counts": st.counts}
    ends = [s.finished_at for s in manifest.steps if s.finished_at]
    whole = None
    if ends:
        first = datetime.fromisoformat(manifest.created_at.replace("Z", "+00:00"))
        whole = (max(datetime.fromisoformat(e.replace("Z", "+00:00")) for e in ends) - first).total_seconds()

    accounts = pl.read_parquet(sorted(paths.staged.glob("account-*.parquet"))[-1])
    alerts = pl.read_parquet(sorted(paths.staged.glob("alert-*.parquet"))[-1])
    communities = pl.read_parquet(paths.artifacts / COMMUNITIES_FILE)
    key = run.recipe.graph.account_key
    in_slice = set(accounts[key].to_list())
    gt = pl.read_parquet(truth_dir(truth) / "ground_truth.parquet").filter(pl.col("account_id").is_in(sorted(in_slice)))
    planted = set(gt.filter(pl.col("ring_id") != "")["account_id"])
    lookalike = set(gt.filter(pl.col("lookalike_id") != "")["account_id"])
    alerted = set(alerts["account_key"].to_list())
    hit = alerted & planted
    precision = len(hit) / len(alerted) if alerted else 0.0
    recall = len(hit) / len(planted) if planted else 0.0

    min_size = policy.community.min_size
    sizes = gt.filter(pl.col("ring_id") != "").group_by("ring_id").len()
    held = set(sizes.filter(pl.col("len") >= min_size)["ring_id"])  # rings the slice holds enough of to be a community
    rep = ring_recall(communities, gt.filter((pl.col("ring_id") == "") | pl.col("ring_id").is_in(sorted(held))),
                      alert_threshold=policy.alert_threshold)
    pattern = dict(gt.filter(pl.col("ring_id") != "").select("ring_id", "pattern").unique().iter_rows())
    by_pattern: dict[str, dict] = {}
    for rid, r in rep["by_ring"].items():
        p = by_pattern.setdefault(pattern[rid], {"held": 0, "whole": 0, "high": 0})
        p["held"] += 1
        p["whole"] += int(r["whole"])
        p["high"] += int(r["community_score"] is not None and r["community_score"] >= policy.alert_threshold - 1e-9)
    all_rings = gt.filter(pl.col("ring_id") != "")["ring_id"].n_unique()
    high_communities = int((communities["score"] >= policy.alert_threshold - 1e-9).sum())
    high_with_ring = sum(
        1 for m, s in zip(communities["member_keys"], communities["score"], strict=True)
        if s >= policy.alert_threshold - 1e-9 and set(m) & planted
    )

    values = {
        "steps": steps, "accounts_in_slice": len(in_slice), "planted_in_slice": len(planted),
        "lookalike_accounts_in_slice": len(lookalike), "alerts": len(alerted), "alerts_on_planted": len(hit),
        "alerts_on_lookalikes": len(alerted & lookalike), "precision": precision, "recall": recall,
        "rings_touched": all_rings, "rings_held": len(held), "rings_whole": rep["whole"], "by_pattern": by_pattern,
        "lookalike_groups": rep["lookalikes"], "lookalike_groups_high": rep["lookalikes_in_high_scoring_community"],
        "communities": communities.height, "communities_high": high_communities,
        "communities_high_with_planted": high_with_ring, "alert_threshold": policy.alert_threshold,
        "min_size": min_size, "policy_combine": policy.combine, "community_combine": policy.community.combine,
    }
    values["whole_run_seconds"] = whole
    shown = {f"{k} time": seconds(v["seconds"]) for k, v in steps.items()}
    if whole is not None:
        shown["whole run time"] = seconds(whole)
    shown |= {
        "accounts in slice": count(len(in_slice)), "planted accounts in slice": count(len(planted)),
        "alerts": count(len(alerted)), "precision": ratio(precision), "recall": ratio(recall),
        "look-alike accounts alerted": f"{count(len(alerted & lookalike))} of {count(len(lookalike))}",
        "rings held": f"{len(held)} of {all_rings}", "rings whole": f"{rep['whole']} of {len(held)}",
        "look-alike groups high": f"{rep['lookalikes_in_high_scoring_community']} of {rep['lookalikes']}",
        "communities high": f"{high_communities} of {communities.height}",
        "communities high holding planted accounts": f"{high_with_ring} of {high_communities}",
    }
    for name, p in sorted(by_pattern.items()):
        shown[f"{name} whole"] = f"{p['whole']} of {p['held']}"
        shown[f"{name} high"] = f"{p['high']} of {p['held']}"
    run.close()
    return write("run", label or run_id.rsplit("-", 1)[-1], f"run {run_id} against the ground truth", values, shown,
                 [run_id])


# ---- pull ----------------------------------------------------------------------------------------------------------
def measure_pull(label: str, since: str | None, route: str) -> Path:
    from lab.common.store import estimate_memory
    from lab.contracts.recipe import PullSpec, Recipe
    from lab.roundtrip import pull as P
    from lab.runs import Run
    from lab.settings import get_settings

    settings = get_settings()
    run = Run.create(Recipe(pull=PullSpec(since=since)))
    stmts = P.pull_statements(run.recipe.graph, run.recipe.pull)
    run.close()
    with PeakMemory() as mem:
        started = time.perf_counter()
        # The two routes on purpose: `pull()` takes Relate and falls back to Bolt only when the Query API is unreachable.
        counts = P._relate_pull(run, stmts, settings) if route == "relate" else P._bolt_pull(run, stmts, settings)
        elapsed = time.perf_counter() - started
    run.close()
    store = dir_bytes(run.paths.store)
    peak = mem.peak_children if route == "relate" else mem.peak_self
    values = {"route": route, "since": since, "nodes": counts.nodes, "relationships": counts.relationships,
              "seconds": elapsed, "peak_rss_bytes": peak, "store_bytes": store,
              "lab_estimate_bytes": estimate_memory(counts.nodes, counts.relationships), "neo4j": settings.neo4j_uri}
    shown = {"nodes": count(counts.nodes), "relationships": count(counts.relationships), "time": seconds(elapsed),
             "peak memory": megabytes(peak), "store on disk": megabytes(store),
             "lab estimate": megabytes(values["lab_estimate_bytes"])}
    return write("pull", f"{label}-{route}", f"a schema pull by {route} from {settings.neo4j_uri}", values, shown,
                 [run.id])


# ---- serve ---------------------------------------------------------------------------------------------------------
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def measure_serve(run_id: str, label: str | None) -> Path:
    from neo4j import GraphDatabase

    from lab.common.relate_cli import relate_bin, relate_env
    from lab.common.store import run_paths

    store = run_paths(run_id).store
    bolt, http, mcp = free_port(), free_port(), free_port()
    cmd = [relate_bin(), "serve", "--database", str(store), "--read-only=live", "--host", "127.0.0.1",
           "--bolt", str(bolt), "--http", str(http), "--mcp", str(mcp), "--auth", "none"]
    log = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, env=relate_env(), stdout=log, stderr=subprocess.STDOUT)
    try:
        ps = psutil.Process(proc.pid)
        driver = GraphDatabase.driver(f"bolt://127.0.0.1:{bolt}", auth=None)
        for _ in range(300):
            try:
                driver.verify_connectivity()
                break
            except Exception:  # noqa: BLE001 (not up yet)
                time.sleep(0.2)
        with driver.session() as s:
            n = s.run("MATCH (n) RETURN count(n) AS n").single()["n"]
            idle = ps.memory_info().rss
            t = time.perf_counter()
            hops = s.run("MATCH (a:Account)-[:TX]->(:Account)-[:TX]->(c:Account) RETURN count(DISTINCT c) AS n").single()["n"]
            q = time.perf_counter() - t
            after = ps.memory_info().rss
        driver.close()
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
    values = {"nodes": n, "rss_idle_bytes": idle, "rss_after_query_bytes": after, "two_hop_seconds": q,
              "two_hop_rows": hops, "store_bytes": dir_bytes(store)}
    shown = {"nodes served": count(n), "memory answering": megabytes(idle),
             "memory after a two-hop count": megabytes(after), "two-hop count time": seconds(q)}
    return write("serve", label or run_id.rsplit("-", 1)[-1], f"relate serve --read-only=live on {run_id}'s store",
                 values, shown, [run_id])


# ---- image ---------------------------------------------------------------------------------------------------------
def measure_image(tag: str, build_dir: str | None = None) -> Path:
    built = None
    if build_dir:  # a clean clone: every layer built, nothing from Docker's cache
        started = time.perf_counter()
        subprocess.run(["docker", "compose", "build", "--no-cache", "--pull", "lab"], cwd=build_dir, check=True)
        built = time.perf_counter() - started
    out = subprocess.run(["docker", "image", "inspect", tag, "--format", "{{.Size}} {{.Id}}"],
                         capture_output=True, text=True, check=True).stdout.split()
    size, image_id = int(out[0]), out[1]
    values = {"tag": tag, "bytes": size, "image_id": image_id, "build_seconds": built, "build_dir": build_dir}
    shown = {"image size": f"{size / 10**9:.2f} GB"}
    if built is not None:
        shown["build time with no cache"] = seconds(built)
    return write("image", tag.replace(":", "-").replace("/", "-"), f"the lab image {tag}", values, shown, [image_id])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("run_id")
    r.add_argument("--truth")
    r.add_argument("--label")
    p = sub.add_parser("pull")
    p.add_argument("--label", required=True)
    p.add_argument("--since")
    p.add_argument("--route", choices=["relate", "bolt"], default="relate")
    s = sub.add_parser("serve")
    s.add_argument("run_id")
    s.add_argument("--label")
    i = sub.add_parser("image")
    i.add_argument("--tag", default="subgraph-lab:dev")
    i.add_argument("--build", help="a clean clone to build the image in first, with no cache, and time it")
    a = ap.parse_args()
    if a.cmd == "run":
        measure_run(a.run_id, a.truth, a.label)
    elif a.cmd == "pull":
        measure_pull(a.label, a.since, a.route)
    elif a.cmd == "serve":
        measure_serve(a.run_id, a.label)
    else:
        measure_image(a.tag, a.build)


if __name__ == "__main__":
    main()
