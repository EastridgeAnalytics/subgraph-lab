"""Check the Markdown pages: banned names, AI-tell words, prose dashes and ampersands, links, ids.

Run from anywhere: `python -I tools/check_docs.py [--mermaid]`.
With --mermaid it also writes .tmp/mermaid-check.html, a page that parses and renders every Mermaid block
with mermaid 11 from jsdelivr; serve .tmp/ and open it in a browser to see the result.
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = ([ROOT / "README.md", ROOT / "CLAUDE.md"] + sorted((ROOT / "architecture-docs").glob("*.md"))
         + sorted(p for p in ROOT.glob("*/README.md")
                  if p.parent.name not in {"architecture-docs", "node_modules"} and not p.parent.name.startswith("."))
         + [p for p in [ROOT / "demo" / "agent.md"] if p.exists()])

BANNED = re.compile(r"Neo4j-powered|built to sell", re.I)
GPL = re.compile(r"igraph|leidenalg")
TELLS = re.compile(
    r"\b(leverage|leverages|leveraging|robust|seamless|seamlessly|delve|delves|streamline|streamlines|"
    r"navigate|navigating|landscape|furthermore|moreover|additionally|that said|in conclusion|unlock|unlocks|"
    r"at the end of the day|in today's)\b",
    re.I,
)
FENCE = re.compile(r"```.*?```", re.S)
INLINE_CODE = re.compile("`[^`\\n]*`")
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def check(path: pathlib.Path, mermaid_blocks: list) -> list:
    text = path.read_text(encoding="utf-8")
    for m in re.finditer(r"```mermaid\n(.*?)```", text, re.S):
        mermaid_blocks.append({"file": path.name, "src": m.group(1)})
    prose = FENCE.sub("", text)
    prose = INLINE_CODE.sub("code", prose)  # inline code is not prose
    lines = [ln for ln in prose.splitlines() if not re.match(r"^\|[-|: ]+\|$", ln)]
    prose = "\n".join(lines)
    prose_nolist = re.sub(r"(?m)^\s*(?:>\s*)*(-|\d+\.)\s+", "", prose)  # list markers, inside a quote too
    words = len(re.findall(r"\b\w+\b", prose))
    dashes = len(re.findall(r"—|–|(?<=\S) - (?=\S)|(?<![-\w])--(?![-\w])", prose_nolist))
    amps = prose.count("&")
    flags = []
    b = [m.group(0) for m in BANNED.finditer(prose)]
    if b:
        flags.append(f"BANNED {b}")
    paragraphs = re.split(r"\n\s*\n|\n(?=\s*(?:-|\d+\.)\s)", prose)
    g = [p.strip()[:80] for p in paragraphs if GPL.search(p) and "GPL" not in p]
    if g:
        flags.append(f"GPL-NAME-WITHOUT-GPL {g}")
    t = [m.group(0) for m in TELLS.finditer(prose)]
    if t:
        flags.append(f"TELLS {t}")
    if dashes > 1:
        flags.append(f"DASHES {dashes}")
    if amps > 1:
        flags.append(f"AMPS {amps}")
    sents = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", prose))
    if not any(1 <= len(re.findall(r"\b\w+\b", s)) <= 6 for s in sents):
        flags.append("NO-SHORT-SENTENCE")
    bad = []
    for m in LINK.finditer(text):
        target = m.group(1).split("#")[0]
        if target.startswith("http") or not target:
            continue
        if not (path.parent / target).exists():
            bad.append(target)
    if bad:
        flags.append(f"BAD-LINKS {bad}")
    print(f"{path.relative_to(ROOT).as_posix():36} words={words:5} dashes={dashes} amps={amps} {' | '.join(flags)}")
    return flags


def main() -> int:
    blocks: list = []
    problems = 0
    for f in FILES:
        if check(f, blocks):
            problems += 1
    prd = ROOT / "architecture-docs" / "88-product-requirements.md"
    if prd.exists():
        text = prd.read_text(encoding="utf-8")
        rs = [int(x) for x in re.findall(r"\bR(\d+)\b", text)]
        as_ = sorted({int(x) for x in re.findall(r"\bA(\d+)\b", text)})
        print("R ids sequential:", rs == list(range(1, len(rs) + 1)), "| A ids complete:", as_ == list(range(1, len(as_) + 1)))
    print("mermaid blocks:", len(blocks), "| problem files:", problems)
    if "--mermaid" in sys.argv:
        html = """<!doctype html><html><body><pre id="out">running</pre>
<script type="module">
import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
mermaid.initialize({startOnLoad:false, securityLevel:'loose'});
const blocks = %s;
const results = [];
for (let i = 0; i < blocks.length; i++) {
  try { await mermaid.parse(blocks[i].src); const r = await mermaid.render('d'+i, blocks[i].src); results.push({file: blocks[i].file, ok: true, svg: r.svg.length}); }
  catch (e) { results.push({file: blocks[i].file, ok: false, err: String(e.message || e)}); }
}
window.__results = results;
document.getElementById('out').textContent = 'DONE ' + JSON.stringify(results);
</script></body></html>""" % json.dumps(blocks)  # noqa: UP031 (the page is full of braces)
        out = ROOT / ".tmp" / "mermaid-check.html"
        out.parent.mkdir(exist_ok=True)
        out.write_text(html, encoding="utf-8")
        print("wrote", out)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
