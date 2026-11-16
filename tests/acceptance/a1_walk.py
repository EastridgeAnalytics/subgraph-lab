"""A1: from `docker compose up` to a reviewed push on the seed, timed.

    uv run python tests/acceptance/a1_walk.py --dir CLONE --project P [--since 2026-10-20] [--record] [--down]
    uv run python tests/acceptance/a1_walk.py --base http://localhost:8000 [--since ...]     (a lab already up)

With `--dir` it runs `docker compose -p P --profile local up -d --build` in that folder (a clean clone with its own
.env), waits until the lab's health route answers, and then walks the app in a headless browser the way the video
does, clicking only what a new user would:

    Connect     test the bundled Neo4j, use it
    Models      untick TGN unless --with-tgn (it trains on every account's payments: the slow step on 98)
    Slice       by schema, payments since --since, Estimate, Run this slice
    Run         follow the steps until the run is done
    Review      read the delta, Push to the master graph, Confirm and push, wait for "Pushed"

It prints each part's time. With `--record` it writes the measured page's record through tools/measure.py. The times
are a script's clicks: a person reading each screen takes longer, and the measured page says so. With `--down` the
compose project is taken down at the end with its volumes (its own Neo4j, its own runs); nothing else is touched.
Playwright drives Chromium (`uv run playwright install chromium` once).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

STEP_TIMEOUT_MS = 30 * 60 * 1000  # a run on a slow machine; the walk fails rather than hangs past it


def compose_up(folder: Path, project: str) -> float:
    started = time.perf_counter()
    subprocess.run(["docker", "compose", "-p", project, "--profile", "local", "up", "-d", "--build"], cwd=folder,
                   check=True)
    return time.perf_counter() - started


def wait_healthy(base: str, limit: float = 900) -> float:
    started = time.perf_counter()
    while time.perf_counter() - started < limit:
        try:
            with urllib.request.urlopen(f"{base}/api/health", timeout=5) as r:
                if json.loads(r.read()).get("ok"):
                    return time.perf_counter() - started
        except Exception:  # noqa: BLE001 (not up yet)
            pass
        time.sleep(2)
    raise TimeoutError(f"{base}/api/health did not answer ok within {limit:.0f} s")


def walk(base: str, since: str, shots: Path | None, with_tgn: bool = False) -> dict[str, float]:
    from playwright.sync_api import expect, sync_playwright

    times: dict[str, float] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_default_timeout(60_000)

        def shot(name: str) -> None:
            if shots:
                page.screenshot(path=str(shots / f"{name}.png"), full_page=True)

        t = time.perf_counter()
        page.goto(f"{base}/connect")
        page.get_by_role("button", name="Test connection").click()
        expect(page.get_by_text("Test result")).to_be_visible()
        page.get_by_role("button", name="Use this connection").click()
        shot("1-connect")
        times["connect"] = time.perf_counter() - t

        t = time.perf_counter()
        page.get_by_role("link", name="Models").click()
        tgn = page.get_by_label("tgn", exact=True)
        tgn.wait_for()
        if not with_tgn and tgn.is_checked():
            tgn.uncheck()
        shot("2-models")
        times["models"] = time.perf_counter() - t

        t = time.perf_counter()
        page.get_by_role("link", name="Slice").click()
        page.get_by_role("tab", name="By schema").click()
        page.get_by_label("Since").fill(since)
        page.get_by_role("button", name="Estimate").click()
        expect(page.get_by_text("Memory", exact=True)).to_be_visible()
        shot("3-estimate")
        page.get_by_role("button", name="Run this slice").click()
        page.wait_for_url("**/run**")
        times["slice"] = time.perf_counter() - t

        t = time.perf_counter()
        page.get_by_role("button", name="See the results").wait_for(timeout=STEP_TIMEOUT_MS)
        shot("4-run")
        times["run"] = time.perf_counter() - t

        t = time.perf_counter()
        page.get_by_role("button", name="Review and push").click()
        expect(page.get_by_text("What changes")).to_be_visible(timeout=STEP_TIMEOUT_MS)
        shot("5-review")
        page.get_by_role("button", name="Push to the master graph").click()
        page.get_by_role("button", name="Confirm and push").click()
        expect(page.get_by_text("Pushed to the master graph")).to_be_visible(timeout=STEP_TIMEOUT_MS)
        shot("6-pushed")
        times["review_and_push"] = time.perf_counter() - t
        browser.close()
    return times


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, help="a clean clone with its own .env; compose runs there")
    ap.add_argument("--project", default="subgraph-a1")
    ap.add_argument("--base", help="the lab's address; read from the clone's .env LAB_PORT when --dir is given")
    ap.add_argument("--since", default="2026-10-20")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--shots", type=Path, help="a folder for a screenshot of each screen")
    ap.add_argument("--down", action="store_true")
    ap.add_argument("--with-tgn", action="store_true", help="keep the TGN model ticked")
    a = ap.parse_args()

    base = a.base
    times: dict[str, float] = {}
    if a.dir:
        env = dict(line.split("=", 1) for line in (a.dir / ".env").read_text(encoding="utf-8").splitlines()
                   if "=" in line and not line.lstrip().startswith("#"))
        base = base or f"http://localhost:{env.get('LAB_PORT', '8000').strip()}"
        times["compose_up"] = compose_up(a.dir, a.project)
        times["until_healthy"] = wait_healthy(base)
    if not base:
        ap.error("give --dir or --base")
    if a.shots:
        a.shots.mkdir(parents=True, exist_ok=True)
    try:
        times |= walk(base, a.since, a.shots, a.with_tgn)
        times["walk"] = sum(v for k, v in times.items() if k in ("connect", "models", "slice", "run", "review_and_push"))
        times["total"] = times["walk"] + times.get("compose_up", 0) + times.get("until_healthy", 0)
        print(json.dumps({k: round(v, 1) for k, v in times.items()}, indent=1))
        if a.record:
            from tools.measure import seconds, write

            with urllib.request.urlopen(f"{base}/api/runs", timeout=10) as r:
                runs = [m["run_id"] for m in json.loads(r.read())["runs"]][:1]
            shown = {f"{k.replace('_', ' ')} time": seconds(v) for k, v in times.items()}
            write("walk", a.project, f"A1: compose up to a reviewed push, payments since {a.since}, {'with' if a.with_tgn else 'without'} TGN, by script", times,
                  shown, runs)
    finally:
        if a.dir and a.down:
            subprocess.run(["docker", "compose", "-p", a.project, "--profile", "local", "down", "-v"], cwd=a.dir,
                           check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
