"""`tools/check_docs.py`'s store rule: a documented `relate query` or `relate serve` on a `.rl` file must pass
`--read-only`, because opening the committed `demo/store.rl` for writing changes the file even when it only reads."""
from __future__ import annotations

import pytest

from tools import check_docs


def flagged(line: str) -> list[bool]:
    return ["--read-only" not in m.group(0) for m in check_docs.STORE_COMMAND.finditer(line)]


@pytest.mark.parametrize("line", [
    'relate query -d store.rl --mode table "MATCH (n) RETURN count(n)"',
    "relate serve --database demo/store.rl --mcp stdio",
    "`relate query -d demo/store.rl \"MATCH (n) RETURN n\"`",
])
def test_a_store_command_without_read_only_is_flagged(line):
    assert flagged(line) == [True]


@pytest.mark.parametrize("line", [
    'relate query -d store.rl --read-only --mode table "MATCH (n) RETURN count(n)"',
    "relate serve --database demo/store.rl --mcp stdio --read-only",
    "relate serve --database PATH --bolt 7687 --http 7474 --mcp 7475",  # no .rl file: the lab's own server, page 97
    "relate info -d store.rl",  # info never opens the store for writing
])
def test_read_only_commands_and_other_lines_pass(line):
    assert not any(flagged(line))


def test_every_committed_page_passes_the_store_rule():
    found = [(f.name, m.group(0)) for f in check_docs.FILES for m in check_docs.STORE_COMMAND.finditer(
        f.read_text(encoding="utf-8")) if "--read-only" not in m.group(0)]
    assert found == []
