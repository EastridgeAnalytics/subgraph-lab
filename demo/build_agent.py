"""Render demo/agent.md from the agent session's own log, demo/agent/session.jsonl. Never edit agent.md by hand.

    uv run python demo/build_agent.py                               the page, from the log
    uv run --with pillow python demo/build_agent.py --shots DIR      also crop walk.py's `agent` screenshots

The log is Claude Code's stream-json output from `demo/agent/run.py`: the session's init line (model, tools,
MCP servers), each assistant message (text and tool calls), each tool result, and the final result. Tool results
are shown trimmed to what a reader needs, with the whole result folded underneath.
"""
from __future__ import annotations

import json
from pathlib import Path

DEMO = Path(__file__).resolve().parent
LOG = DEMO / "agent" / "session.jsonl"
MANIFEST = DEMO / "agent" / "manifest.json"
OUT = DEMO / "agent.md"
FULL_LINES = 80  # how much of a folded result to keep

INTRO = """\
# An agent finds the ring

Gave Claude Code (Sonnet) two MCP servers, the lab's and Relate's, and nothing else. The task, in plain words: find
the riskiest ring among accounts active since 20 October 2026 and explain it. It found R0006, the planted ring a
person finds in [the walkthrough](README.md). On the way it pulled a slice, searched it, scored it, read the
communities through Relate, wrote a summary the lab checked, read the dry run and pushed the results back with a
confirm. 13 turns, 3.7 minutes.

This page is rendered from the session's log, [`agent/session.jsonl`](agent/session.jsonl), by
[`build_agent.py`](build_agent.py). Nobody edits it by hand.

## The guardrails

None of these rely on the prompt. Each is a flag or a check in the lab's code.

- **Two MCP servers, no other tools.** No shell, no files, no web. The flags in `run.py` turn the rest off, and it
  keeps a log only when the session had the two servers' tools and nothing else.
- **Reads go through Relate's MCP server, read-only.** `relate serve --read-only=live` follows the lab's writes and
  refuses any write sent through it.
- **Actions go through the lab's nine tools.** The long ones queue their work and answer at once. `run_status`
  waits for them.
- **One write reaches the master graph: `apply_push` with `confirm` set to the JSON boolean `true`.** A string or a
  number is refused (decision 50). It fails without a `preview_push` newer
  than the last thing staged. That review is Relate's dry run plus the lab's own delta.
- **The agent's summary gets the same checks as the API's.** Every number has to be in the evidence, every member it
  names has to be a member, and no verdict words. A summary that fails is replaced by the template, and the tool
  says why. This one failed on three numbers.

## Run it yourself

With the lab running (`docker compose --profile local up`, or `uv run python -m lab serve`) and Claude Code
installed, from the repo root, in any shell:

```
uv run python demo/agent/run.py      # the prompt is demo/agent/prompt.md; the ports come from .env, as Compose's do
uv run python demo/build_agent.py    # this page, from the new log
```
"""

CLOSE = """\
## What the agent used, and what it could not do

The agent never saw a table of accounts. It searched the run's store by meaning and by words, read the
communities and the identifiers their members share with Cypher through Relate's server, and wrote its summary
from the evidence object the lab built from those links. Its report above is its own words, including anything
it could not do.

Its last note has a plain cause. The store Relate serves is the slice as it was pulled from Neo4j, and the run's own
scores are staged beside it until the push. The five members had no score on Neo4j when this run pulled, because
retracting the first recording (below) had cleared them.

The first recording of this session found a bug in the lab. `describe_alert` ran the checks and stored the result,
then failed to answer, because it handed back the summary alone where its contract promises `stored` and the
summary. The agent reported "Error executing tool" twice and carried on. The fix and a test for both outcomes are
in the same commit as this page, and the session above is the second recording.

After the session the agent's run was retracted from the demo Neo4j with `retract(run, confirm=True)`, which
removes one run's own writes and nothing else. An account carries the scores of the last run that pushed it, so
the retraction also cleared the scores on the accounts both runs touched; [the walkthrough's](README.md) run was
pushed again to put them back. The demo graph holds only that run.

The same agent with a different factor file is the next vertical: see
[Fraud today, other verticals next](../README.md#fraud-today-other-verticals-next).
"""


def short_name(tool: str) -> tuple[str, str]:
    """`mcp__lab__egress` -> ("lab", "egress")."""
    parts = tool.split("__")
    return (parts[1], parts[2]) if len(parts) >= 3 else ("", tool)


def result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
    return content or ""


def as_json(text: str):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def fold(text: str) -> str:
    data = as_json(text)
    body = json.dumps(data, indent=2, ensure_ascii=False) if data is not None else text
    lines = body.splitlines()
    if len(lines) > FULL_LINES:
        lines = lines[:FULL_LINES] + [f"... {len(body.splitlines()) - FULL_LINES} more lines"]
    return "\n".join(lines)


def table(rows: list[dict], limit: int = 10) -> str:
    if not rows:
        return "*no rows*"
    cols = list(rows[0].keys())

    def cell(v) -> str:
        if isinstance(v, float):
            v = round(v, 4)
        s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
        s = s.replace("|", "\\|").replace("\n", " ")
        return s if len(s) <= 160 else s[:157] + "..."

    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(cell(r.get(c)) for c in cols) + " |" for r in rows[:limit]]
    if len(rows) > limit:
        lines.append(f"\n*{len(rows) - limit} more rows*")
    return "\n".join(lines)


def rows_of(data) -> list[dict] | None:
    """Relate's read_cypher answers with rows in one of a few shapes; find the list of dicts."""
    if isinstance(data, list) and all(isinstance(r, dict) for r in data):
        return data
    if isinstance(data, dict):
        for key in ("rows", "records", "data", "results", "values"):
            if key in data:
                found = rows_of(data[key])
                if found is not None:
                    return found
        if "fields" in data and "values" in data:
            return [dict(zip(data["fields"], v, strict=True)) for v in data["values"]]
    return None


def steps_table(manifest: dict) -> str:
    rows = []
    for s in manifest.get("steps", []):
        counts = s.get("counts") or {}
        c = ", ".join(f"{k} {v:,}" if isinstance(v, int) else f"{k} {v}" for k, v in list(counts.items())[:3])
        rows.append({"step": s.get("name"), "status": s.get("status"), "counts": c})
    return table(rows, limit=20)


def plan_table(entities: list[dict], landed: bool = False) -> str:
    cols = ("entity", "kind", "created", "updated") + (("landed",) if landed else ())
    return table([{c: e.get(c) for c in cols} for e in entities], limit=20)


def summarise(server: str, name: str, args: dict, text: str, is_error: bool) -> str:
    """The few lines a reader needs from one result."""
    if is_error:
        return f"**Error:** {text.strip()[:600]}"
    data = as_json(text)
    if not isinstance(data, (dict, list)):
        return text.strip()[:600]
    if name in ("egress", "score"):
        return " · ".join(f"`{k}`: {data[k]}" for k in ("run_id", "status") if data.get(k) is not None)
    if name == "run_status":
        return f"Run `{data.get('status')}`.\n\n{steps_table(data)}"
    if name == "search_slice":
        hits = data.get("hits", [])
        top = ", ".join(f"{h['key']} ({' and '.join(h.get('found_by', []))})" for h in hits[:5])
        return (f"{len(hits)} hits, {data.get('hops')} hop. The slice around them: {data.get('nodes'):,} nodes, "
                f"{data.get('relationships'):,} relationships, {data.get('memory_bytes', 0) / 2**20:.1f} MB, under the cap "
                f"of {data.get('cap'):,}: {not data.get('over_cap')}. Top five by fused rank: {top}.")
    if name == "get_schema":
        labels = ", ".join(f"{k} {v.get('count', '?'):,}" for k, v in data.items() if isinstance(v, dict) and v.get("type") == "node")
        rels = sorted({r for v in data.values() if isinstance(v, dict) for r in (v.get("relationships") or {})})
        return f"Labels with counts: {labels}. Relationship types: {', '.join(rels)}."
    if name == "read_cypher":
        rows = rows_of(data)
        if rows is not None:
            return table(rows)
    if name == "explain_alert":
        ev, s = data.get("evidence", {}), data.get("summary") or {}
        ids = "; ".join(f"{i['kind']} {i['key']} shared by {i['members_sharing']}" for i in ev.get("shared_identifiers", []))
        m = ev.get("money", {})
        factors = "; ".join(f"{f['name']} ({f['accounts']} accounts)" for f in ev.get("factors", []))
        cmp_ = ev.get("comparison", {})
        return (f"The evidence object for {ev.get('subject')} `{ev.get('key', '')[:12]}…`: {ev.get('member_count')} members "
                f"({', '.join(ev.get('member_keys', []))}), score {ev.get('score')}, rank {cmp_.get('score_rank')} of "
                f"{cmp_.get('communities_in_slice')}.\n\n"
                f"- Shared identifiers: {ids}\n"
                f"- Money: in {m.get('inflow'):,}, out {m.get('outflow'):,}, between members {m.get('internal'):,}, "
                f"{m.get('cycles')} cycles, {m.get('first_at', '')[:10]} to {m.get('last_at', '')[:10]}\n"
                f"- Factors that fired: {factors}\n"
                f"- Score parts: {', '.join(f'{k} {v}' for k, v in ev.get('score_parts', {}).items())}\n"
                f"- The stored summary's source: `{s.get('source')}`")
    if name == "describe_alert":
        s = data.get("summary", {})
        head = ("**Stored.** The agent's text passed every check." if data.get("stored") else
                f"**Not stored.** The lab kept the template instead: {s.get('failed_reason')}.")
        return f"{head}\n\nThe summary now on the community (source `{s.get('source')}`):\n\n> {s.get('text', '')}"
    if name == "preview_push":
        d = data.get("delta", {})
        return (f"The plan, from Relate's dry run:\n\n{plan_table(data.get('plan', []))}\n\nThe lab's own delta against the "
                f"master graph: {d.get('accounts_scored')} accounts scored, {d.get('new_alerts')} new alerts, "
                f"{d.get('new_communities')} new communities, {d.get('vectors')} vectors, {d.get('dispositions')} reviewer "
                f"decisions, about {d.get('bytes_estimate', 0) / 2**10:.0f} KB.")
    if name == "apply_push":
        return (f"Pushed: {data.get('pushed')}. {data.get('message', '')}\n\n{plan_table(data.get('entities', []), landed=True)}")
    if isinstance(data, dict):
        bits = []
        for k, v in data.items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                s = str(v)
                bits.append(f"`{k}`: {s if len(s) <= 200 else s[:197] + '...'}")
            elif isinstance(v, list):
                bits.append(f"`{k}`: {len(v)} items")
            elif isinstance(v, dict):
                inner = ", ".join(f"{ik} {iv}" for ik, iv in v.items() if isinstance(iv, (str, int, float, bool)))
                bits.append(f"`{k}`: {inner[:300] if inner else f'{len(v)} fields'}")
        return " · ".join(bits[:12])
    return text.strip()[:600]


def render() -> str:
    events = [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines() if line.strip()]
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    final = next((e for e in reversed(events) if e.get("type") == "result"), {})
    results: dict[str, dict] = {}
    for e in events:
        if e.get("type") == "user":
            for b in e.get("message", {}).get("content", []) or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    results[b["tool_use_id"]] = b

    servers = ", ".join(f"`{s.get('name')}` {s.get('status')}" for s in init.get("mcp_servers", []))
    tools = [t for t in init.get("tools", []) if t.startswith("mcp__")]
    other = [t for t in init.get("tools", []) if not t.startswith("mcp__")]
    out = [INTRO, "## The session", ""]
    out.append(f"Model `{init.get('model', '?')}`. MCP servers: {servers}. Tools the agent had: {len(tools)} from the "
               f"two servers" + (f", and {', '.join(other)}" if other else ", and nothing else") + ". "
               f"{final.get('num_turns', '?')} turns, {round(final.get('duration_ms', 0) / 60000, 1)} minutes.")
    out.append("")
    if MANIFEST.exists():
        m = json.loads(MANIFEST.read_text(encoding="utf-8"))
        out.append(f"The run it made, from the lab's manifest ([`agent/manifest.json`](agent/manifest.json)): "
                   f"`{m.get('run_id')}`, pulled by `{m.get('routes', {}).get('pull')}` and pushed by "
                   f"`{m.get('routes', {}).get('push')}`.")
        out += ["", steps_table(m), ""]

    report = (final.get("result") or "").strip()  # the last message is the report: shown once, under its heading
    n = 0
    for e in events:
        if e.get("type") != "assistant":
            continue
        for b in e.get("message", {}).get("content", []) or []:
            if b.get("type") == "text" and b.get("text", "").strip() and b["text"].strip() != report:
                out += ["> " + ln if ln.strip() else ">" for ln in b["text"].strip().splitlines()]
                out.append("")
            elif b.get("type") == "tool_use":
                n += 1
                server, name = short_name(b.get("name", ""))
                args = b.get("input", {})
                res = results.get(b.get("id"), {})
                text = result_text(res)
                out.append(f"**{n}. `{name}`** on `{server}`")
                out.append("")
                if name == "read_cypher" and set(args) <= {"statement", "query", "params", "parameters"}:
                    out += ["```cypher", (args.get("statement") or args.get("query") or "").strip(), "```", ""]
                else:
                    out += ["```json", json.dumps(args, indent=2, ensure_ascii=False), "```", ""]
                out.append(summarise(server, name, args, text, bool(res.get("is_error"))))
                out.append("")
                out += ["<details><summary>The whole result</summary>", "", "```", fold(text), "```", "", "</details>", ""]
    out += ["## The agent's report", ""]
    out += ["> " + ln if ln.strip() else ">" for ln in (final.get("result") or "").strip().splitlines()]
    out.append("")
    shown = [(f, c) for f, c in IMAGES if (DEMO / "img" / f).exists()]
    if shown:
        out += ["## The same run in the app", "",
                "The agent's run is an ordinary run: the app shows it like any other.", ""]
        for f, caption in shown:
            out += [f"![{caption}](img/{f})", "", f"*{caption}*", ""]
    out += [CLOSE]
    return "\n".join(out)


IMAGES = [
    ("agent-run.png", "The Run screen for the agent's run: ten of eleven steps done (this recipe skips the models step), "
                      "pulled and pushed by Relate, the review on record."),
    ("agent-summary.png", "Results: the community the agent found. The summary on it is the template, because the agent's "
                          "own text failed the numbers check."),
]
SHOTS = {"agent-run.png": "30-agent-run", "agent-summary.png": "31-agent-summary"}


def crop_images(shots: Path) -> None:
    """Crop walk.py's `agent` screenshots into img/. Pillow is needed only here."""
    from PIL import Image

    for out_name, shot in SHOTS.items():
        src = shots / f"{shot}.png"
        if not src.exists():
            continue
        im = Image.open(src).convert("RGB")
        im.save(DEMO / "img" / out_name, format="PNG", optimize=True)


# The init record's fields that go in the committed log. The rest describe the recording machine (home paths,
# plugins, skills, slash commands) and say nothing about the session.
INIT_KEEP = ("type", "subtype", "session_id", "uuid", "model", "claude_code_version", "permissionMode", "tools", "mcp_servers")


def trim_log(lines) -> list[str]:
    """Claude Code's stream-json, with the init record cut down to INIT_KEEP. Every other line is kept as it is."""
    out = []
    for line in lines:
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("type") == "system" and e.get("subtype") == "init":
            line = json.dumps({k: e[k] for k in INIT_KEEP if k in e}, ensure_ascii=False)
        out.append(line.rstrip("\n"))
    return out


def main() -> None:
    import argparse
    import sys

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--shots", type=Path, help="folder with walk.py's agent screenshots; crops them into img/")
    ap.add_argument("--trim-log", action="store_true", help="read Claude Code's stream-json on stdin, write the log to commit")
    args = ap.parse_args()
    if args.trim_log:
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        sys.stdout.write("\n".join(trim_log(sys.stdin.buffer.read().decode("utf-8").splitlines())) + "\n")
        return
    if args.shots:
        crop_images(args.shots)
    OUT.write_text(render(), encoding="utf-8", newline="\n")
    print(OUT.name, len(OUT.read_text(encoding="utf-8").splitlines()), "lines")


if __name__ == "__main__":
    main()
