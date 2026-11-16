"""Build the fraud-ring walkthrough from the screenshots: img/, README.md and walkthrough.html, from one list.

    uv run --with pillow python demo/build_walkthrough.py [--shots DIR]

The raw screenshots come from `demo/walk.py` and live outside the repo (default `demo/shots/`, not committed).
Every figure is cropped from them into `img/`. README.md renders on GitHub; walkthrough.html is the same page
with the images embedded, for sending or reading offline. Change the text in STEPS and run again.
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import re
from pathlib import Path

from PIL import Image

DEMO = Path(__file__).resolve().parent
IMG = DEMO / "img"
RUN = "run-20261116T045118Z-4953ae"
RING = "6cb5be18371a49ace1bdea774b6878ef87612fea"


def crop(shots: Path, name: str, box: tuple[int, int, int, int] | None = None) -> Image.Image:
    im = Image.open(shots / f"{name}.png").convert("RGB")
    return im.crop(box) if box else im


def stack(parts: list[Image.Image], gap: int = 18) -> Image.Image:
    w = max(p.width for p in parts)
    h = sum(p.height for p in parts) + gap * (len(parts) - 1)
    out = Image.new("RGB", (w, h), (229, 231, 235))
    y = 0
    for p in parts:
        out.paste(p, (0, y))
        y += p.height + gap
    return out


def figures(shots: Path) -> dict[str, Image.Image]:
    c = lambda name, box=None: crop(shots, name, box)  # noqa: E731
    return {
        "connect": c("01-connect", (0, 0, 1280, 510)),
        "models": c("02-models", (0, 0, 1280, 1260)),
        "code": c("03-factor-code"),
        "estimate": c("04-slice-estimate"),
        "running": c("05-run-in-progress", (0, 0, 1280, 560)),
        "done": c("06-run-done"),
        "accounts": c("07-results-accounts", (0, 420, 1280, 820)),
        "communities": c("08-results-communities", (0, 420, 1280, 820)),
        "decision": c("09b-decision", (0, 530, 1280, 820)),
        "summary": c("10-community-summary"),
        "parts": c("11-community-parts"),
        "graph": c("12-community-graph", (0, 0, 1104, 760)),
        "evidence": c("13-community-evidence"),
        "like": c("14-community-like-this"),
        "search": stack([c("15-search-estimate", (0, 120, 1280, 1255)), c("15-search-estimate", (0, 2690, 1280, 2856))]),
        "review": c("16-review", (0, 0, 1280, 925)),
        "confirm": c("17-confirm"),
        "pushed": c("18-pushed", (0, 1470, 1280, 2010)),
        "neo4j_graph": c("20-neo4j-ring", (400, 60, 1400, 760)),
        "neo4j_table": c("21-neo4j-table", (0, 60, 1400, 420)),
    }


# Each step: title, body in Markdown (paragraphs, "- " lists, "> " quotes, **bold**, *em*, `code`), figures.
STEPS: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("Connect to the graph",
     "The lab starts by testing the master graph. Here that is the Neo4j bundled with the lab, holding the demo "
     "bank: 10,000 accounts with their devices, emails and addresses, and 120,000 payments. The screen names the "
     "route the pull will take: `relate pull`, over Neo4j's Query API.",
     [("connect", "Connect: the graph in use, its version, and the pull route.")]),
    ("Choose the risk factors",
     "Every way of spotting risk is a **risk factor** in one YAML file: five rules in Cypher, a forecast in Python, "
     "and a temporal graph model. Tick the ones this run uses. Each weight says how much that factor alone can say "
     "about an account.\n\n"
     "The rule that matters most for this ring is *new accounts sharing an identifier*. Its code is one click away. "
     "It is plain Cypher, and anyone can read it or change it.",
     [("models", "Models: the factors, their kind and weight. TGN is unticked to keep this run short."),
      ("code", "The rule behind \"new accounts sharing an identifier\", as the lab runs it.")]),
    ("Pick the slice, and see its size first",
     "The slice is the part of the graph the models work on. Here it is every payment since 20 October, with the "
     "accounts and identifiers around them. **The lab counts it on the master graph before anything moves**, "
     "memory included, so nobody pulls more than they meant to.",
     [("estimate", "Slice by schema: the estimate comes first. Then the Run button comes on.")]),
    ("Run: pull, learn, score",
     "One click runs the whole recipe. Relate pulls 28,677 nodes and 30,882 relationships into a fresh store in "
     "about four seconds. A first pass embeds every node with Node2Vec from PyTorch Geometric and writes a short "
     "summary on every account, so the store can be searched by meaning and by words. Then the risk factors score "
     "every account, NetworkX finds communities, and each community gets a score and a written summary.",
     [("running", "A few seconds in: the pull done, the first pass running."),
      ("done", "Every step done. Nothing has touched the master graph yet.")]),
    ("The accounts at the top",
     "The highest-scoring accounts score 0.90, and each shows why. Three factors fired on each one: five or more "
     "accounts on one device, new accounts sharing an identifier, and money in and out of similar size within a "
     "day. The factors combine as a *noisy-or*. A factor that does not fire changes nothing, and each one that does "
     "can only add.",
     [("accounts", "Results, Accounts: the score, every factor with its score and weight, the community, the alert.")]),
    ("The communities at the top",
     "Accounts do not commit fraud alone, so the lab groups them. It links accounts that share a device, an email "
     "or an address first, then chains of money forwarded within a day, then payments. Of 172 communities in this "
     "slice, the top one has five members and scores 0.84.",
     [("communities", "Results, Communities: highest risk first, each with its summary.")]),
    ("Open the ring",
     "The community opens to everything a reviewer needs to judge it.\n\n"
     "- **The summary** says what the evidence shows, why that pattern could mean risk, and **the innocent "
     "explanation to rule out first**. It is written only from a checked evidence object. This one came from the "
     "template, since no API key is set.\n"
     "- **The score's parts** are shown with the arithmetic, so the 0.84 can be checked by hand.\n"
     "- **The picture** shows five accounts on one device and one address, paying each other in loops.\n"
     "- **The evidence** has the shared identifiers, the money in, out and around, 21 payment cycles in ten and a "
     "half days, the factors that fired, and how this community compares with the rest.",
     [("summary", "The summary, with its source and the hash of the evidence it was written from."),
      ("parts", "The score and its parts: 1 − Π(1 − w·v) = 0.84, the score the lab stored."),
      ("graph", "Members and links: circles are accounts, squares the device and address they share, arrows payments."),
      ("evidence", "The evidence: keys and totals, never names.")]),
    ("Check it against the truth",
     "> **This is planted ring R0006.** The demo generator plants 60 fraud rings and writes down who is in each. "
     "R0006 is a mule ring: five accounts opened within weeks of each other on one device and one address, taking "
     "money in from outsiders and cycling it between them.\n"
     ">\n"
     "> The lab's top community is **exactly those five accounts, no more and no fewer**. It was not told the answer.",
     []),
    ("The reviewer decides",
     "The reviewer marks the community **Confirmed**, with their name. The decision is staged and goes back to "
     "Neo4j with the push, with who made it and when. False positive and Needs more work go the same way. A later "
     "version learns from them.",
     [("decision", "The decision on this community, staged for the next push.")]),
    ("Find more like it",
     "Two ways to find the next one. **Communities like this one** runs one hybrid search over the community "
     "summaries and embeddings. **Search** asks the store a question in plain words. Asked for \"new accounts "
     "sharing one device and address\", vector search and full-text search, fused by rank, **put the five members "
     "of R0006 at the top of the list**. Each was found by both. The slice around the hits is sized before anything "
     "runs: 696 nodes and 1.0 MB.",
     [("like", "Communities like this one."),
      ("search", "Slice by search: the top five hits are the ring's members. The size and memory come first.")]),
    ("Review the push",
     "Before anything is written, the lab shows two things side by side. One is the plan from Relate's dry run. "
     "The other is **the lab's own comparison with the master graph**: 211 new alerts, 172 new communities, scores "
     "on 7,254 accounts, one reviewer decision. Nothing is deleted, ever.",
     [("review", "Review and push: the delta and the plan, before anything is sent.")]),
    ("One confirm",
     "The confirm names the graph it will write to and says what will land. It is the only place in the lab that "
     "writes to the master graph. An agent working through the lab's tools gets the same gate.",
     [("confirm", "The one confirm."),
      ("pushed", "Pushed. The run marker is written last: if it is there, the whole run landed.")]),
    ("Back in Neo4j",
     "In Neo4j Browser, on the master graph: the ring's community node, linked to its five members and to the "
     "device and address they share. On the node sit the score, the decision \"confirmed\" by the reviewer, and the "
     "run that wrote it, pulled and pushed by Relate.",
     [("neo4j_graph", "Neo4j Browser: the RiskCommunity linked to its members, their device and their address."),
      ("neo4j_table", "The score, the reviewer's decision, and the LabRun that put it there.")]),
]

INTRO = (
    "**What you are looking at.** subgraph-lab pulls part of a Neo4j graph into Relate, Neo4j's embedded graph "
    "database, hands it to open-source graph models, scores accounts and communities, writes down why each "
    "community could be risky, and pushes the results back to Neo4j after a person has reviewed a dry run.\n\n"
    "This walkthrough follows one planted fraud ring through every screen: from the graph, to a score and a "
    "written reason, to a reviewer's decision, and back into Neo4j. Every screenshot is from one real run of the "
    "lab on its demo data."
)

ABOUT_DATA = (
    "The demo bank is synthetic. Its generator plants three kinds of fraud ring (mule rings, structuring, "
    "pass-through chains) plus honest look-alikes that should not score high: families sharing a laptop, payroll "
    "accounts, landlords. Because it writes down who is in every ring, anyone can check what the lab finds."
)

INSPECT_INTRO = (
    "The store this run pulled and enriched is in this folder as one file, `store.rl` (9.8 MB): the slice, every "
    "account's embedding and summary, the 172 communities with their scores and summaries, and the 11 indexes. "
    "Relate wrote it with `relate clone`. With the `relate` CLI installed ([relatedb.ai/docs](https://relatedb.ai/docs/), "
    "or `uv run python tools/install_relate.py` from the repo root), run these from this folder. The output under "
    "each is what the committed file returns."
)

# (what it shows, the command, the output as captured against store.rl on 16 November 2026)
INSPECT: list[tuple[str, str, str]] = [
    ("The store, without opening it.",
     "relate info -d store.rl",
     "layout: .rl bundle\ncodec: parquet\nnodes (at checkpoint LSN 93): 28849\nrelationships (at checkpoint LSN 93): 37612\n"
     "indexes: 11\n  account_embedding: :Account(risk_embedding)\n  account_summary: :Account(lab_summary)\n"
     "  community_embedding: :RiskCommunity(embedding)\n  community_summary: :RiskCommunity(summary)\n  ..."),
    ("The riskiest communities, with the reason the lab wrote for each.",
     "relate query -d store.rl --mode table \"MATCH (c:RiskCommunity) RETURN c.size AS members, round(c.score,2) AS score, "
     "left(c.summary, 90) AS summary ORDER BY c.score DESC LIMIT 5\"",
     "+---------+-------+----------------------------------------------------------------------------------------------+\n"
     "| members | score | summary                                                                                      |\n"
     "+---------+-------+----------------------------------------------------------------------------------------------+\n"
     "| 5       | 0.84  | \"This community has 5 accounts and scores 0.84, ranking 1 of 172 communities in the slice; \" |\n"
     "| 6       | 0.77  | \"This community has 6 accounts and scores 0.77, ranking 2 of 172 communities in the slice; \" |\n"
     "| 5       | 0.75  | \"This community has 5 accounts and scores 0.75, ranking 3 of 172 communities in the slice; \" |\n"
     "| 7       | 0.74  | \"This community has 7 accounts and scores 0.74, ranking 4 of 172 communities in the slice; \" |\n"
     "| 7       | 0.72  | \"This community has 7 accounts and scores 0.72, ranking 5 of 172 communities in the slice; \" |\n"
     "+---------+-------+----------------------------------------------------------------------------------------------+"),
    ("The ring's members and what they share. The connected context is the evidence.",
     f"relate query -d store.rl --mode table \"MATCH (c:RiskCommunity {{community_key:'{RING}'}})<-[:IN_RISK_COMMUNITY]-(a:Account)"
     "-[:USES_DEVICE|HAS_ADDRESS]->(i) RETURN a.account_id AS account, labels(i)[0] AS shares, "
     "coalesce(i.device_id, i.address_id) AS id ORDER BY account, shares\"",
     "+------------+-----------+-------------+\n| account    | shares    | id          |\n+------------+-----------+-------------+\n"
     "| \"A0000897\" | \"Address\" | \"AD0000896\" |\n| \"A0000897\" | \"Device\"  | \"D0000896\"  |\n"
     "| \"A0001287\" | \"Address\" | \"AD0000896\" |\n| \"A0001287\" | \"Device\"  | \"D0000896\"  |\n"
     "| \"A0001723\" | \"Address\" | \"AD0000896\" |\n| \"A0001723\" | \"Device\"  | \"D0000896\"  |\n"
     "| \"A0001818\" | \"Address\" | \"AD0000896\" |\n| \"A0001818\" | \"Device\"  | \"D0000896\"  |\n"
     "| \"A0005373\" | \"Address\" | \"AD0000896\" |\n| \"A0005373\" | \"Device\"  | \"D0000896\"  |\n"
     "+------------+-----------+-------------+"),
    ("Ask the store in plain words. Full-text search over the account summaries returns the five members.",
     "relate query -d store.rl --mode table \"MATCH (a:Account) SEARCH a IN (FULLTEXT INDEX account_summary "
     "FOR 'new accounts sharing one device and address' LIMIT 5) SCORE AS s RETURN a.account_id AS account, round(s, 2) AS score\"",
     "+------------+-------+\n| account    | score |\n+------------+-------+\n| \"A0001818\" | 5.61  |\n| \"A0000897\" | 5.61  |\n"
     "| \"A0001287\" | 5.61  |\n| \"A0005373\" | 5.61  |\n| \"A0001723\" | 5.61  |\n+------------+-------+"),
    ("Communities like the ring, by vector search on the community embeddings.",
     f"relate query -d store.rl --mode table \"MATCH (seed:RiskCommunity {{community_key:'{RING}'}}) MATCH (c:RiskCommunity) "
     "SEARCH c IN (VECTOR INDEX community_embedding FOR seed.embedding LIMIT 4) SCORE AS s "
     "RETURN c.size AS members, round(c.score,2) AS risk, round(s,3) AS similarity\"",
     "+---------+------+------------+\n| members | risk | similarity |\n+---------+------+------------+\n| 5       | 0.84 | 1.0        |\n"
     "| 3       | 0.63 | 0.656      |\n| 3       | 0.07 | 0.654      |\n| 8       | 0.03 | 0.647      |\n+---------+------+------------+"),
    ("Let an agent read it. Relate's own MCP server on the file, read-only, for Claude Desktop or Claude Code.",
     "relate serve --database store.rl --mcp stdio --read-only",
     "serve: opened the store read-only — no writer lease and never creates the store; every write fails\n"
     "serve: MCP (stdio) serving on stdin/stdout (auth: none)"),
]

RESULTS: list[tuple[str, str]] = [
    ("Accounts scored", "7,254"),
    ("Alerts, and alerts on a planted ring account", "211, of which 210"),
    ("Communities scoring 0.6 or more, and those holding planted ring accounts", "39, all 39"),
    ("Rings with three or more members in the slice, found whole in a community scoring 0.6 or more", "39 of 53"),
    ("Honest look-alike groups in a community scoring 0.6 or more", "0 of 60"),
    ("Look-alike accounts that alerted", "1 of 911"),
]

RESULTS_INTRO = "R0006 was not a lucky pick. Against the generator's ground truth, for this one run:"
RESULTS_AFTER = (
    "This slice keeps only payments since 20 October, and 77 percent of the planted accounts in it alerted. Scored "
    "over the whole seed, the lab's measured page reports alert recall of 1.00, precision of 0.98, and all 60 "
    "planted rings found whole in a high-scoring community."
)
WHAT_IS_REAL = (
    "**What is demo and what is real.** The data is synthetic, and the generator plants exactly the patterns the "
    "rules look for, so these numbers say the lab finds what it was built to find, not how it does on real fraud. "
    "The weights are set for the demo data. The summaries here came from the template; with an Anthropic API key "
    "set, a model writes them from the same checked evidence. The TGN model was left out of this run to keep it "
    "short. The pull, the scores, the communities, the review and the push are the real thing, and the push "
    "landed on the Neo4j you see in the last step."
)


# ---- rendering -------------------------------------------------------------------------------------------------

def inline_html(text: str) -> str:
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", out)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', out)
    return out


def md_to_html(md: str) -> str:
    """The subset used above: paragraphs, '- ' lists, '> ' quotes, inline code, bold, em, links."""
    parts = []
    for block in re.split(r"\n\s*\n", md.strip()):
        lines = block.splitlines()
        if all(ln.startswith("- ") for ln in lines):
            parts.append("<ul>" + "".join(f"<li>{inline_html(ln[2:])}</li>" for ln in lines) + "</ul>")
        elif all(ln.startswith(">") for ln in lines):
            quoted = [ln[2:] for ln in lines if ln.startswith("> ")]
            parts.append('<div class="truth">' + "".join(f"<p>{inline_html(q)}</p>" for q in quoted) + "</div>")
        else:
            parts.append(f"<p>{inline_html(' '.join(lines))}</p>")
    return "".join(parts)


def data_uri(im: Image.Image) -> str:
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


STYLE = """
:root { --ink:#1f2937; --muted:#6b7280; --line:#e5e7eb; --accent:#4338ca; --soft:#eef2ff; --ok:#166534; --okbg:#ecfdf5; }
* { box-sizing: border-box; }
body { margin:0; background:#fff; color:var(--ink); font:16px/1.6 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
main { max-width: 980px; margin: 0 auto; padding: 48px 28px 90px; }
h1 { font-size: 32px; line-height:1.2; margin:0 0 6px; }
.sub { color: var(--muted); margin: 0 0 26px; }
.lede { background: var(--soft); border-left: 4px solid var(--accent); padding: 16px 20px; margin-bottom: 18px; }
.lede p:last-child { margin-bottom: 0; }
p { margin: 0 0 12px; }
ul { margin: 0 0 14px 22px; padding: 0; } li { margin-bottom: 6px; }
section.step { margin-top: 44px; padding-top: 22px; border-top: 1px solid var(--line); }
.num { display:inline-block; min-width: 30px; height: 30px; line-height: 30px; text-align:center; border-radius: 15px;
       background: var(--accent); color: #fff; font-weight: 600; font-size: 15px; margin-right: 10px; }
h2 { font-size: 22px; margin: 0 0 12px; display:flex; align-items:center; }
h3 { font-size: 17px; margin: 18px 0 6px; }
figure { margin: 18px 0 22px; }
figure img { width: 100%; height: auto; border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
figcaption { color: var(--muted); font-size: 14px; margin-top: 6px; }
code { font: 14px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; background:#f3f4f6; padding:1px 5px; border-radius:4px; }
pre { font: 13px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; background:#f3f4f6; padding:10px 12px; border-radius:6px;
      overflow-x:auto; margin: 6px 0 14px; }
pre code { background: none; padding: 0; font-size: 13px; }
.truth { background: var(--okbg); border-left: 4px solid var(--ok); padding: 16px 20px; }
.truth p:last-child { margin-bottom: 0; }
table { border-collapse: collapse; width: 100%; margin: 10px 0 18px; font-size: 15px; }
th, td { text-align:left; padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { background:#f9fafb; font-weight:600; } td.n { text-align:right; white-space:nowrap; }
.toc { columns: 2; font-size: 15px; margin: 8px 0 0 20px; }
.small { color: var(--muted); font-size: 14px; }
@media print { main { padding: 0; } section.step { break-before: page; } figure { break-inside: avoid; } }
"""


def build_html(images: dict[str, Image.Image]) -> str:
    def fig(key: str, caption: str) -> str:
        return (f'<figure><img src="{data_uri(images[key])}" alt="{html.escape(caption)}">'
                f"<figcaption>{html.escape(caption)}</figcaption></figure>")

    toc = "".join(f'<li><a href="#s{i}">{html.escape(t)}</a></li>' for i, (t, _, _) in enumerate(STEPS, 1))
    toc += f'<li><a href="#s{len(STEPS) + 1}">How the whole slice did</a></li>'
    inspect = "".join(f"<h3>{inline_html(what)}</h3><pre><code>{html.escape(cmd)}</code></pre>"
                      f"<pre class=\"small\">{html.escape(out)}</pre>" for what, cmd, out in INSPECT)
    steps = "".join(f'<section class="step" id="s{i}"><h2><span class="num">{i}</span>{html.escape(title)}</h2>'
                    f"{md_to_html(body)}{''.join(fig(k, c) for k, c in figs)}</section>"
                    for i, (title, body, figs) in enumerate(STEPS, 1))
    rows = "".join(f"<tr><td>{html.escape(m)}</td><td class=\"n\">{html.escape(v)}</td></tr>" for m, v in RESULTS)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>subgraph-lab · a fraud ring, end to end</title><style>{STYLE}</style></head>
<body><main>
<h1>A fraud ring, end to end</h1>
<p class="sub">subgraph-lab walkthrough · run <code>{RUN}</code> · 15 November 2026 · synthetic data</p>
<div class="lede">{md_to_html(INTRO)}</div>
<p class="small">{inline_html(ABOUT_DATA)}</p>
<ol class="toc">{toc}</ol>
<section class="step" id="inspect"><h2><span class="num">&gt;_</span>Inspect it yourself</h2>{md_to_html(INSPECT_INTRO)}{inspect}</section>
{steps}
<section class="step" id="s{len(STEPS) + 1}"><h2><span class="num">✓</span>How the whole slice did</h2>
<p>{inline_html(RESULTS_INTRO)}</p>
<table><tr><th>Measure</th><th class="n">This slice</th></tr>{rows}</table>
<p>{inline_html(RESULTS_AFTER)}</p>
<p class="small">{inline_html(WHAT_IS_REAL)}</p>
</section>
</main></body></html>
"""


def build_markdown() -> str:
    out = [
        "# A fraud ring, end to end", "",
        f"subgraph-lab walkthrough · run `{RUN}` · 15 November 2026 · synthetic data", "",
        INTRO, "", ABOUT_DATA, "",
        "The same page with the images embedded, for sending or reading offline: [walkthrough.html](walkthrough.html). "
        "The screenshots came from [walk.py](walk.py) driving the app, and this page from [build_walkthrough.py](build_walkthrough.py).", "",
        "The same job done by an agent through the two MCP servers, from its own log: [agent.md](agent.md).", "",
        "## Inspect it yourself", "", INSPECT_INTRO, "",
    ]
    for what, cmd, res in INSPECT:
        out += [f"**{what}**", "", "```", cmd, "```", "", "```", res, "```", ""]
    out += ["## The run, step by step", ""]
    for i, (title, body, figs) in enumerate(STEPS, 1):
        out += [f"### {i}. {title}", "", body, ""]
        for key, caption in figs:
            out += [f"![{caption}](img/{key}.png)", "", f"*{caption}*", ""]
    out += ["## How the whole slice did", "", RESULTS_INTRO, "", "| Measure | This slice |", "|---|---|"]
    out += [f"| {m} | {v} |" for m, v in RESULTS]
    out += ["", RESULTS_AFTER, "", WHAT_IS_REAL, ""]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--shots", type=Path, default=DEMO / "shots", help="folder with the raw screenshots from walk.py")
    args = ap.parse_args()
    images = figures(args.shots)
    IMG.mkdir(exist_ok=True)
    for key, im in images.items():
        im.save(IMG / f"{key}.png", format="PNG", optimize=True)
    (DEMO / "README.md").write_text(build_markdown(), encoding="utf-8", newline="\n")
    page = DEMO / "walkthrough.html"
    page.write_text(build_html(images), encoding="utf-8", newline="\n")
    print(f"{len(images)} figures in {IMG.name}/, README.md, {page.name} {page.stat().st_size / 2**20:.1f} MB")


if __name__ == "__main__":
    main()
