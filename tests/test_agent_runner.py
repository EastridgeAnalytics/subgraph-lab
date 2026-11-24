"""`demo/agent/run.py`, the agent runner for every shell (decision 51): the config it writes, the command it runs, and
the check that decides whether a session's log may replace the committed one. `claude` itself never runs here."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from demo.agent.run import HERE, check_session, command, mcp_config

INIT = {"type": "system", "subtype": "init", "model": "claude-sonnet-5-5",
        "tools": ["mcp__lab__apply_push", "mcp__lab__egress", "mcp__relate__read_cypher"],
        "mcp_servers": [{"name": "lab", "status": "connected"}, {"name": "relate", "status": "connected"}]}
RESULT = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 13}


def test_the_committed_config_is_the_runners_with_the_default_ports():
    assert mcp_config(8000, 7475) == json.loads((HERE / "mcp.json").read_text(encoding="utf-8"))


def test_the_config_follows_the_ports_it_is_given():
    servers = mcp_config(9000, 17475)["mcpServers"]
    assert servers["lab"]["url"] == "http://127.0.0.1:9000/mcp"
    assert servers["relate"]["url"] == "http://127.0.0.1:17475/mcp"


def test_the_command_turns_off_built_in_tools_and_other_servers_and_holds_no_prompt():
    argv = command("claude", Path("cfg.json"))
    assert argv[:2] == ["claude", "-p"]
    assert argv[argv.index("--tools") + 1] == ""  # no built-in tools: no shell, files or web
    assert "--strict-mcp-config" in argv and argv[argv.index("--mcp-config") + 1] == "cfg.json"
    assert argv[argv.index("--allowedTools") + 1:argv.index("--allowedTools") + 3] == ["mcp__lab", "mcp__relate"]
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    prompt = (HERE / "prompt.md").read_text(encoding="utf-8")
    assert not any(prompt[:40] in a for a in argv)  # the prompt goes in on stdin


def test_a_clean_session_on_the_two_servers_may_replace_the_log():
    assert check_session([INIT, {"type": "assistant"}, RESULT]) is None


def test_the_committed_session_passes_its_own_check():
    lines = (HERE / "session.jsonl").read_text(encoding="utf-8").splitlines()
    assert check_session([json.loads(x) for x in lines if x.strip()]) is None


@pytest.mark.parametrize(("records", "why"), [
    ([RESULT], "no init record"),
    ([{**INIT, "tools": [*INIT["tools"], "Bash"]}, RESULT], "beyond the two servers: Bash"),
    ([{**INIT, "mcp_servers": [{"name": "lab", "status": "connected"}, {"name": "relate", "status": "failed"}]}, RESULT],
     "relate (failed)"),
    ([{**INIT, "mcp_servers": [{"name": "lab", "status": "connected"}]}, RESULT], "relate (missing)"),
    ([INIT], "without a result"),
    ([INIT, {**RESULT, "subtype": "error_max_turns", "is_error": True}], "error_max_turns"),
])
def test_any_other_session_leaves_the_log_alone(records, why):
    reason = check_session(records)
    assert reason is not None and why in reason
