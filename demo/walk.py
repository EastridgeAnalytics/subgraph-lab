"""Drive the app at http://localhost:8000 with Playwright and save a screenshot at each beat of the walkthrough.

    uv run python demo/walk.py run            Connect, Models, Slice and estimate, Run until done; prints the run id
    uv run python demo/walk.py ring RUN KEY   Results, the community KEY, a reviewer decision, search, Review and push
    uv run python demo/walk.py neo4j RUN KEY PASSWORD   Neo4j Browser on the bundled Neo4j: the ring on the master graph

Screenshots go to demo/shots/, which is not committed. `build_walkthrough.py` crops them into img/ and writes the
pages. Needs the lab running (`docker compose --profile local up`, or `uv run python -m lab serve` against it) and
Playwright with Chromium (`uv run --with playwright python -m playwright install chromium`).
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = "http://localhost:8000"
NEO4J_BROWSER = "http://localhost:17474/browser/?dbms=neo4j://neo4j@localhost:17687&db=neo4j"
OUT = Path(__file__).resolve().parent / "shots"
SINCE = "2026-10-20"
SEARCH_TEXT = "new accounts sharing one device and address"
REVIEWER = "Demo reviewer"


def browser(p):
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 820}, device_scale_factor=1)
    page.set_default_timeout(60_000)
    return b, page


def shot(page, name: str, full: bool = False, clip=None) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    if clip is not None:
        clip.screenshot(path=str(path))
    else:
        page.screenshot(path=str(path), full_page=full)
    print("shot", path.name, flush=True)


def stage_run() -> None:
    with sync_playwright() as p:
        b, page = browser(p)
        page.goto(f"{BASE}/connect")
        page.get_by_role("button", name="Test connection").click()
        expect(page.get_by_text("Test result")).to_be_visible()
        shot(page, "01-connect", full=True)
        page.get_by_role("button", name="Use this connection").click()
        time.sleep(1)

        page.get_by_role("link", name="Models").click()
        tgn = page.get_by_label("tgn", exact=True)
        tgn.wait_for()
        if tgn.is_checked():
            tgn.uncheck()
        time.sleep(0.5)
        shot(page, "02-models", full=True)
        page.get_by_role("button", name="Show code for factor new_accounts_shared_identifier").click()
        time.sleep(2)
        shot(page, "03-factor-code")
        page.keyboard.press("Escape")
        time.sleep(0.5)

        page.get_by_role("link", name="Slice").click()
        page.get_by_role("tab", name="By schema").click()
        page.get_by_label("Since").fill(SINCE)
        page.get_by_role("button", name="Estimate").click()
        expect(page.get_by_text("Memory", exact=True)).to_be_visible()
        time.sleep(0.5)
        shot(page, "04-slice-estimate", full=True)
        page.get_by_role("button", name="Run this slice").click()
        page.wait_for_url("**/run**")
        time.sleep(25)  # a moment into the run: the steps filling in
        shot(page, "05-run-in-progress", full=True)
        page.get_by_role("button", name="See the results").wait_for(timeout=30 * 60 * 1000)
        time.sleep(1)
        shot(page, "06-run-done", full=True)
        run_id = page.evaluate("() => [...document.querySelectorAll('code, span, p')].map(e => e.textContent)"
                               ".find(t => /^run-\\d{8}T\\d{6}Z-[0-9a-f]{6}$/.test((t||'').trim()))")
        print("RUN", (run_id or "").strip(), flush=True)
        b.close()


def stage_ring(run_id: str, key: str) -> None:
    with sync_playwright() as p:
        b, page = browser(p)
        page.goto(f"{BASE}/results?run={run_id}")
        expect(page.get_by_role("tab", name="Accounts")).to_be_visible()
        time.sleep(2)
        shot(page, "07-results-accounts")
        page.get_by_role("tab", name="Communities").click()
        time.sleep(2)
        shot(page, "08-results-communities")

        page.goto(f"{BASE}/results?run={run_id}&community={key}")
        expect(page.get_by_text("Score and its parts")).to_be_visible()
        time.sleep(3)
        shot(page, "09-community-full", full=True)
        page.get_by_label("Your name").fill(REVIEWER)
        confirm = page.get_by_role("button", name=re.compile(r"as confirmed$", re.I)).first
        confirm.scroll_into_view_if_needed()
        confirm.click()
        time.sleep(2)
        shot(page, "09b-decision")
        for title, name in (("Summary", "10-community-summary"), ("Score and its parts", "11-community-parts"),
                            ("Members and links", "12-community-graph"), ("Evidence", "13-community-evidence"),
                            ("Communities like this one", "14-community-like-this")):
            card = page.locator("section, div").filter(has=page.get_by_role("heading", name=title, exact=True)).last
            try:
                card.scroll_into_view_if_needed()
                time.sleep(0.5)
                shot(page, name, clip=card)
            except Exception as e:  # noqa: BLE001 (a card missing is reported, not fatal)
                print("no card", title, e, flush=True)

        page.goto(f"{BASE}/slice?run={run_id}")
        page.get_by_role("tab", name="By search").click()
        page.get_by_label("Search text").fill(SEARCH_TEXT)
        page.get_by_role("button", name="Estimate").click()
        expect(page.get_by_text("Memory", exact=True)).to_be_visible()
        time.sleep(1)
        shot(page, "15-search-estimate", full=True)

        page.goto(f"{BASE}/review?run={run_id}")
        expect(page.get_by_text("What changes")).to_be_visible(timeout=10 * 60 * 1000)
        time.sleep(1)
        shot(page, "16-review", full=True)
        page.get_by_role("button", name="Push to the master graph").click()
        time.sleep(1)
        shot(page, "17-confirm")
        page.get_by_role("button", name="Confirm and push").click()
        expect(page.get_by_text("Pushed to the master graph")).to_be_visible(timeout=10 * 60 * 1000)
        time.sleep(1)
        shot(page, "18-pushed", full=True)
        b.close()


def stage_neo4j(run_id: str, key: str, password: str) -> None:
    queries = [
        ("20-neo4j-ring",
         f"MATCH (c:RiskCommunity {{community_key: '{key}'}})<-[r:IN_RISK_COMMUNITY]-(a:Account) "
         "OPTIONAL MATCH (a)-[s:USES_DEVICE|HAS_ADDRESS]->(i) RETURN c, r, a, s, i"),
        ("21-neo4j-table",
         f"MATCH (c:RiskCommunity {{community_key: '{key}'}}) MATCH (l:LabRun {{run_id: c.run_id}}) "
         "RETURN round(c.score, 2) AS score, c.size AS members, c.risk_disposition AS decision, c.risk_disposition_by AS by, "
         "c.summary_source AS summary, l.run_id AS run, l.route_pull AS pulled_by, l.route_push AS pushed_by"),
    ]
    with sync_playwright() as p:
        b, page = browser(p)
        page.set_viewport_size({"width": 1400, "height": 900})
        page.goto(NEO4J_BROWSER)
        page.get_by_label("Password").fill(password)
        page.keyboard.press("Enter")
        time.sleep(6)
        try:
            page.get_by_role("button", name="Dismiss").click(timeout=3000)
        except Exception:  # noqa: BLE001 (no tip shown)
            pass
        try:  # fold the Queries side panel for room
            page.locator("button").filter(has=page.locator("svg")).nth(6).click(timeout=2000)
        except Exception:  # noqa: BLE001
            pass
        for name, q in queries:
            editor = page.locator(".cm-content, [role='textbox'], textarea").first
            editor.click()
            page.keyboard.press("Control+A")
            page.keyboard.type(q)
            page.keyboard.press("Escape")
            page.keyboard.press("Control+Enter")
            time.sleep(6)
            try:
                page.get_by_role("button", name="Dismiss").click(timeout=1500)
            except Exception:  # noqa: BLE001
                pass
            page.mouse.move(5, 400)
            time.sleep(1)
            shot(page, name)
        b.close()


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "run"
    if stage == "run":
        stage_run()
    elif stage == "ring":
        stage_ring(sys.argv[2], sys.argv[3])
    elif stage == "neo4j":
        stage_neo4j(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        sys.exit(__doc__)
