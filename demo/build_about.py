"""Build about.html, subgraph-lab in plain English, at the repo root. Never edit about.html by hand.

    uv run --with markdown python demo/build_about.py

The words are demo/about/about.md. A `{{FIG:name}}` line becomes a figure (its path buttons, the drawing and its
card), drawn by demo/about/figures.js in Eastridge Analytics' technical-figures house style. `{{IMG:graph}}` becomes
the walkthrough's picture of ring R0006, inlined. One self-contained file: the fonts and anime.js come from a CDN.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

import markdown

DEMO = Path(__file__).resolve().parent
ROOT = DEMO.parent
MD = DEMO / "about" / "about.md"
FIG_JS = DEMO / "about" / "figures.js"
OUT = ROOT / "about.html"
REPO = "github.com/EastridgeAnalytics/subgraph-lab"

FIGS = {
    "relate": "Figure 1 · Relate in one picture",
    "ring": "Figure 2 · A ring in plain sight",
    "loop": "Figure 3 · The loop",
    "next": "Figure 4 · One loop, more industries",
}
IMGS = {
    "graph": (DEMO / "img" / "graph.png",
              "The same ring in the lab’s app: circles are accounts, squares the device and address they share, "
              "arrows the payments between them."),
}
DESCRIPTION = ("subgraph-lab in plain English: what Relate is, why Eastridge Analytics built subgraph-lab on it, and the "
               "fraud detection use case on top. Pull a slice of a Neo4j graph into one file, learn from it, and write "
               "the results back after a review.")

CSS = """
  @font-face { font-family:"Departure Mono"; src:url("https://cdn.jsdelivr.net/gh/rektdeckard/departure-mono@main/public/assets/DepartureMono-Regular.woff2") format("woff2"); font-display:swap; }
  :root { color-scheme:light; --bg:#fbfbfb; --paper:#fff; --ink:#171717; --soft:#3b4252; --muted:#6b6b6b; --hair:#d9d9d9; --blue:#2341ff;
          --grey:#7a7f99; --grey-f:#f2f3f7;
          --serif:"EB Garamond", "Iowan Old Style", Georgia, serif; --mono:"Departure Mono", ui-monospace, "SF Mono", Menlo, monospace; }
  * { box-sizing:border-box }
  html { -webkit-font-smoothing:antialiased }
  body { margin:0; background:var(--bg); color:var(--ink); font:18px/1.7 var(--serif) }
  main { max-width:1280px; margin:0 auto; padding:40px 32px 24px }
  main > :not(.figwrap):not(.verdict):not(table):not(section) { max-width:860px }
  .eyebrow, .where, th, button, .tag, .files, .figcap, .shot figcaption { font-family:var(--mono); text-transform:uppercase; font-size:11.5px; letter-spacing:.02em; line-height:1.5 }
  .eyebrow { color:var(--blue); font-size:12.5px; margin:0 0 6px }
  h1 { font-weight:400; font-size:42px; line-height:1.12; margin:0 0 14px; max-width:980px }
  h2 { font-weight:400; font-size:28px; line-height:1.2; margin:48px 0 10px; padding-top:18px; border-top:1px solid var(--hair) }
  h3 { font-weight:400; font-size:21px; margin:28px 0 6px }
  p { margin:0 0 13px }
  strong, b { font-weight:500 }
  a { color:var(--blue) }
  ul, ol { margin:0 0 14px; padding-left:22px }
  li { margin:0 0 7px }
  code { font-family:var(--mono); font-size:12.5px; border:1px solid var(--hair); padding:0 4px; overflow-wrap:anywhere }
  .verdict { background:var(--paper); border:1px solid var(--ink); padding:18px 24px 8px; margin:10px 0 16px; max-width:980px }
  .verdict > p:first-child { font-family:var(--mono); text-transform:uppercase; font-size:12px; letter-spacing:.02em; color:var(--blue) }
  .verdict > p:first-child strong { font-weight:400 }
  table { border-collapse:collapse; width:100%; font-size:16px; margin:6px 0 20px; background:var(--paper); border:1px solid var(--hair) }
  th, td { text-align:left; padding:8px 11px; border-bottom:1px solid var(--hair); vertical-align:top }
  th { color:var(--muted); border-bottom-color:var(--ink); font-weight:400 }
  .tag { display:inline-block; border:1px solid var(--blue); color:var(--blue); padding:0 5px; font-size:10px; margin:0 4px 0 0; vertical-align:1px; white-space:nowrap; background:var(--paper) }
  .tag.not { border-style:dashed; border-color:var(--grey); color:var(--grey); background:var(--grey-f) }
  /* figures, after the technical-figures skill's fig-006 */
  .figwrap { margin:18px 0 30px }
  .figcap { color:var(--muted); margin:0 0 6px }
  .controls { display:flex; gap:8px; flex-wrap:wrap; margin:0 0 10px }
  button { background:var(--paper); color:var(--ink); border:1px solid var(--ink); border-radius:0; padding:5px 11px; cursor:pointer }
  button:hover, button.on { background:var(--ink); color:var(--paper) }
  .stage { display:grid; grid-template-columns:minmax(0,1fr) 320px; gap:22px; align-items:start }
  @media (max-width:1100px) { .stage { grid-template-columns:1fr } }
  figure { margin:0; background:var(--paper); border:1px solid var(--hair) }
  figure svg { display:block; width:100%; height:auto; user-select:none }
  figure svg text { font-family:var(--mono); text-transform:uppercase }
  figure .obj { cursor:pointer; transition:opacity .25s; outline:none }
  figure .obj:focus-visible polygon, figure .obj:focus-visible path, figure .obj:focus-visible ellipse { stroke-width:1.8 }
  figure .obj.dim, figure .conn.dim, figure .clabel.dim { opacity:.18 }
  figure .obj.sel polygon, figure .obj.sel path, figure .obj.sel ellipse { stroke-width:1.8 }
  figure .conn { fill:none; stroke:#2341ff; stroke-width:1; stroke-dasharray:5 4; transition:opacity .25s }
  figure .conn.gap { stroke-dasharray:2 4 }
  figure .conn.on { stroke-width:1.8; animation:flow .8s linear infinite }
  @keyframes flow { to { stroke-dashoffset:-18 } }
  @media (prefers-reduced-motion: reduce) { figure .conn.on { animation:none } }
  aside { background:var(--paper); border:1px solid var(--ink); padding:16px 18px; position:sticky; top:16px }
  aside h3 { margin:0 0 4px; font-size:22px; line-height:1.25 }
  aside .where { color:var(--muted); margin:0 0 10px }
  aside p { margin:0 0 10px; font-size:16px; line-height:1.55 }
  aside .files { color:var(--blue); font-size:10.5px; overflow-wrap:anywhere }
  aside ul { list-style:none; margin:10px 0 0; padding:0 }
  aside li { border-top:1px dashed var(--hair); padding:6px 0; font-size:15px; line-height:1.4; cursor:pointer; margin:0 }
  aside li:hover { color:var(--blue) }
  .shot { margin:6px 0 30px; max-width:860px }
  .shot img { display:block; width:100%; height:auto }
  .shot figcaption { color:var(--muted); padding:8px 12px; border-top:1px solid var(--hair) }
  @media (max-width:700px) { main { padding:24px 16px } h1 { font-size:32px } table { display:block; overflow-x:auto } }
  @page { size:Letter; margin:.55in .6in }
  @media print { body { background:#fff; font-size:11pt } main { padding:0; max-width:none } aside { position:static } .controls { display:none }
    figure, .verdict, tr { break-inside:avoid } h2, h3 { break-after:avoid } }
  .made-with { max-width:1280px; margin:8px auto 48px; padding:16px 32px 0; font:15px/1.6 var(--serif); color:var(--soft) }
  .made-with > div { border-top:1px solid var(--hair); padding-top:16px }
  .made-with .eyebrow { font-size:11.5px; margin:0 0 6px }
  .made-with .reuse { display:flex; gap:8px; align-items:stretch; flex-wrap:wrap; margin-top:8px }
  .made-with .reuse code { font-size:12.5px; background:var(--paper); padding:6px 10px; flex:1 1 420px }
"""

# The skill's footer, as on every page in this style. The skill lives in a private repo, so there is no link.
FOOTER = """<footer class="made-with"><div>
  <p class="eyebrow">Made with the technical-figures skill</p>
  <p>This page's look (isometric line drawings in one blue, Departure Mono labels, EB Garamond text, click to open) comes from
    Eastridge Analytics' technical-figures skill, written instructions an AI coding assistant follows. With the skill installed, ask your assistant:</p>
  <div class="reuse">
    <code id="made-with-prompt">Use the technical-figures skill to draw &lt;what you want to show&gt;, starting from about.html in @REPO@.</code>
    <button type="button" onclick="(async b=>{const t=document.getElementById('made-with-prompt').textContent;try{await navigator.clipboard.writeText(t)}catch(e){const a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();document.execCommand('copy');a.remove()}b.textContent='Copied';b.classList.add('on');setTimeout(()=>{b.textContent='Copy';b.classList.remove('on')},1400)})(this)">Copy</button>
  </div>
</div></footer>""".replace("@REPO@", REPO)


def figure_block(name: str) -> str:
    return (f'<div class="figwrap" id="wrap-{name}"><p class="figcap">{FIGS[name]}</p>'
            f'<div class="controls" id="ctl-{name}"></div>'
            f'<div class="stage"><figure><svg id="fig-{name}" role="img"></svg></figure>'
            f'<aside id="card-{name}" aria-live="polite"></aside></div></div>')


def image_block(name: str) -> str:
    path, caption = IMGS[name]
    data = base64.b64encode(path.read_bytes()).decode()
    return (f'<figure class="shot"><img src="data:image/png;base64,{data}" alt="{caption}">'
            f"<figcaption>{caption}</figcaption></figure>")


def main() -> None:
    body = markdown.markdown(MD.read_text(encoding="utf-8"),
                             extensions=["tables", "fenced_code", "sane_lists", "md_in_html"])
    found = re.findall(r"\{\{FIG:(\w+)\}\}", body)
    assert sorted(found) == sorted(FIGS), found
    body = re.sub(r"(?:<p>)?\{\{FIG:(\w+)\}\}(?:</p>)?", lambda m: figure_block(m.group(1)), body)
    body = re.sub(r"(?:<p>)?\{\{IMG:(\w+)\}\}(?:</p>)?", lambda m: image_block(m.group(1)), body)
    title = re.sub(r"<[^>]+>", "", re.search(r"<h1>(.*?)</h1>", body).group(1))
    doc = ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
           "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
           "<meta name=\"color-scheme\" content=\"light\">\n<meta name=\"author\" content=\"Eastridge Analytics\">\n"
           f"<meta name=\"description\" content=\"{DESCRIPTION}\">\n"
           "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\"><link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin>\n"
           "<link href=\"https://fonts.googleapis.com/css2?family=EB+Garamond:ital,wght@0,400;0,500;1,400&display=swap\" rel=\"stylesheet\">\n"
           f"<title>{title}</title>\n<style>{CSS}</style>\n</head>\n<body>\n<main>\n"
           + body + "\n</main>\n" + FOOTER + "\n<script type=\"module\">\n" + FIG_JS.read_text(encoding="utf-8")
           + "\n</script>\n</body>\n</html>\n")
    OUT.write_text(doc, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.name} ({len(doc):,} bytes, {len(found)} figures)")


if __name__ == "__main__":
    main()
