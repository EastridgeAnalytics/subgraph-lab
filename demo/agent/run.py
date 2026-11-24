"""Record the agent session that demo/agent.md is rendered from. One runner for every shell: PowerShell, cmd, bash, zsh.

    uv run python demo/agent/run.py                              then: uv run python demo/build_agent.py
    uv run python demo/agent/run.py --out .tmp/session.jsonl     a trial that leaves the committed log alone

Needs the lab running (`docker compose --profile local up`, or `uv run python -m lab serve`) and Claude Code on the
path. The two servers' ports come from the lab's settings, read from the repo's `.env` (else the defaults), which are
the settings Compose publishes them from, so the config follows the running lab (decision 51). `mcp.json` beside
this file is the same config with the default ports.

The agent gets only the two MCP servers: `--tools ""` turns off every built-in tool (no shell, files or web), and
`--strict-mcp-config` ignores any other MCP server configured on the machine. The prompt goes in on stdin and the
config in a file under `.tmp/`, so nothing on the command line depends on the shell.

The log is replaced only after a run that ends in a result and whose init record lists the two servers' tools and
nothing else. Any other run leaves the old log as it was, and its own log beside it as `<out>.part`. The one change on
the way is `build_agent.trim_log`: the init record keeps the model, the tools and the servers, not the recording
machine's paths, plugins and skills.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SERVERS = ("lab", "relate")


def mcp_config(lab_port: int, relate_mcp_port: int) -> dict:
    """The two servers, on the loopback host: the lab's tools and Relate's read-only view of the current run."""
    return {"mcpServers": {
        "lab": {"type": "http", "url": f"http://127.0.0.1:{lab_port}/mcp"},
        "relate": {"type": "http", "url": f"http://127.0.0.1:{relate_mcp_port}/mcp"},
    }}


def command(claude: str, config: Path, model: str = "sonnet", max_turns: int = 80) -> list[str]:
    """Claude Code in print mode with no built-in tools and only the config's MCP servers. The prompt is not here: it
    goes in on stdin."""
    return [claude, "-p", "--model", model, "--tools", "",
            "--mcp-config", str(config), "--strict-mcp-config",
            "--allowedTools", "mcp__lab", "mcp__relate",
            "--max-turns", str(max_turns),
            "--output-format", "stream-json", "--verbose"]


def check_session(records: list[dict]) -> str | None:
    """Why this log must not replace the committed one, or None when it may: the init record lists only the two
    servers' tools with both servers connected, and the session ends in a successful result."""
    init = next((r for r in records if r.get("type") == "system" and r.get("subtype") == "init"), None)
    if init is None:
        return "the log has no init record"
    other = [t for t in init.get("tools", []) if not t.startswith(tuple(f"mcp__{s}__" for s in SERVERS))]
    if other:
        return f"the agent had tools beyond the two servers: {', '.join(other)}"
    status = {s.get("name"): s.get("status") for s in init.get("mcp_servers", [])}
    down = [s for s in SERVERS if status.get(s) != "connected"]
    if down:
        return "not connected: " + ", ".join(f"{s} ({status.get(s, 'missing')})" for s in down)
    result = next((r for r in reversed(records) if r.get("type") == "result"), None)
    if result is None:
        return "the session ended without a result"
    if result.get("is_error") or result.get("subtype") != "success":
        return f"the session ended with {result.get('subtype')}"
    return None


def _progress(record: dict) -> str | None:
    if record.get("type") == "assistant":
        calls = [c.get("name", "") for c in record.get("message", {}).get("content", []) if c.get("type") == "tool_use"]
        return ", ".join(calls) or None
    if record.get("type") == "result":
        return f"result: {record.get('subtype')}, {record.get('num_turns')} turns"
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=HERE / "session.jsonl", help="the log to write (default: the committed one)")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--max-turns", type=int, default=80)
    args = ap.parse_args(argv)

    sys.path.insert(0, str(REPO))
    from demo.build_agent import trim_log
    from lab.settings import Settings

    settings = Settings(_env_file=REPO / ".env")
    claude = shutil.which("claude")
    if claude is None:
        print("Claude Code is not on the path: install it, then run this again.", file=sys.stderr)
        return 2
    config = REPO / ".tmp" / "agent-mcp.json"
    config.parent.mkdir(exist_ok=True)
    config.write_text(json.dumps(mcp_config(settings.port, settings.relate_mcp_port), indent=2) + "\n", encoding="utf-8")
    print(f"[agent] lab on {settings.port}, relate on {settings.relate_mcp_port}; {config.relative_to(REPO).as_posix()}",
          flush=True)

    proc = subprocess.Popen(command(claude, config, args.model, args.max_turns), cwd=REPO,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, env={**os.environ, "DO_NOT_TRACK": "1"})
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write((HERE / "prompt.md").read_bytes())
    proc.stdin.close()
    lines: list[str] = []
    for raw in proc.stdout:
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line.strip():
            continue
        lines.append(line)
        try:
            note = _progress(json.loads(line))
        except json.JSONDecodeError:
            note = None
        if note:
            print(f"[agent] {note}", flush=True)
    code = proc.wait()

    kept = trim_log(lines)
    out = args.out if args.out.is_absolute() else Path.cwd() / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")
    part.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
    try:
        why = f"claude exited with {code}" if code else check_session([json.loads(x) for x in kept])
    except json.JSONDecodeError:
        why = "a line of the log is not JSON"
    if why:
        print(f"[agent] not written: {why}. {out} is as it was; this run's log is {part}", file=sys.stderr)
        return 1
    os.replace(part, out)
    print(f"[agent] wrote {out} ({len(kept)} lines). Then: uv run python demo/build_agent.py", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
