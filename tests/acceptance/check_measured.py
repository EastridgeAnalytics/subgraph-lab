"""A8: every number on the measured page names its run.

    uv run python tests/acceptance/check_measured.py [PAGE]

The page is architecture-docs/98-measured.md. Its tables end with a "Record" column that names, in backticks, the
record or records the row comes from: architecture-docs/measured/<id>.json, written by tools/measure.py. A row passes
when every number in its other cells, the first (the row's label) excepted, appears among the `shown` strings or
the `machine` fields of the records it names, and every record names at least one run. A row with no number in
those cells (such as "waiting for credentials") needs no record. Numbers outside tables are not checked: the page keeps them to dates and to the
rows' labels.

Exit 0 and "A8 passes" when every row passes; otherwise each failing row is printed and the exit is 1.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PAGE = REPO / "architecture-docs" / "98-measured.md"
RECORDS = REPO / "architecture-docs" / "measured"
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
RECORD_ID = re.compile(r"`([0-9]{8}-[a-z]+-[A-Za-z0-9._-]+)`")


def numbers(text: str) -> list[str]:
    return [n.replace(",", "") for n in NUMBER.findall(text)]


def table_rows(markdown: str) -> list[tuple[list[str], list[str]]]:
    """(header, cells) for each body row of each table, in order."""
    out, header = [], None
    for line in markdown.splitlines():
        if not line.startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            header = cells
        elif all(re.fullmatch(r":?-+:?", c) for c in cells):
            continue
        else:
            out.append((header, cells))
    return out


def check(page: Path = PAGE) -> list[str]:
    problems: list[str] = []
    for header, cells in table_rows(page.read_text(encoding="utf-8")):
        if not header or header[-1].lower() != "record":
            problems.append(f"a table without a Record column: {' | '.join(header)}")
            continue
        values, record_cell = cells[1:-1], cells[-1]
        found = [n for cell in values for n in numbers(cell)]
        if not found:
            continue
        ids = RECORD_ID.findall(record_cell)
        if not ids:
            problems.append(f"{cells[0]}: numbers {found} and no record named")
            continue
        shown: set[str] = set()
        for rid in ids:
            path = RECORDS / f"{rid}.json"
            if not path.exists():
                problems.append(f"{cells[0]}: record {rid} does not exist")
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            if not record.get("runs"):
                problems.append(f"{cells[0]}: record {rid} names no run")
            for text in [*record.get("shown", {}).values(), *record.get("machine", {}).values()]:
                shown.update(numbers(str(text)))
        missing = [n for n in found if n not in shown]
        if missing:
            problems.append(f"{cells[0]}: {missing} are not in {ids}")
    return problems


def main() -> int:
    page = Path(sys.argv[1]) if len(sys.argv) > 1 else PAGE
    problems = check(page)
    for p in problems:
        print("A8:", p)
    if problems:
        return 1
    print("A8 passes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
