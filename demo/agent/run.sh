#!/usr/bin/env sh
# Run the agent session that demo/agent.md is rendered from. Needs the lab running (`python -m lab serve`, or
# `docker compose --profile local up`) on the ports in mcp.json, and Claude Code on the path.
#
#   sh demo/agent/run.sh            then: uv run python demo/build_agent.py
#
# The agent gets only the two MCP servers: `--tools ""` turns off every built-in tool (no shell, files or web),
# `--strict-mcp-config` ignores any other MCP server configured on the machine. Every line it produces goes to
# session.jsonl, which is the evidence the page is built from. The one change on the way: the session's init record
# keeps only the model, the tools and the servers, not the recording machine's paths, plugins and skills.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
export DO_NOT_TRACK=1
claude -p "$(cat "$here/prompt.md")" \
  --model sonnet \
  --tools "" \
  --mcp-config "$here/mcp.json" --strict-mcp-config \
  --allowedTools mcp__lab mcp__relate \
  --max-turns 80 \
  --output-format stream-json --verbose \
  | uv run python "$here/../build_agent.py" --trim-log > "$here/session.jsonl"
echo "wrote $here/session.jsonl ($(wc -l < "$here/session.jsonl") lines)"
