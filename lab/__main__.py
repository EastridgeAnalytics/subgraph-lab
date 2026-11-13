"""`python -m lab <command>`. Each command delegates to the track that owns it (lab/runs.py has the table)."""
from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault("DO_NOT_TRACK", "1")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m lab", description="subgraph-lab")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("version", help="print the lab's and Relate's versions")
    sub.add_parser("serve", help="the API, the app, the MCP tools, and relate serve for the current run")
    r = sub.add_parser("run", help="run a recipe end to end, stopping before the push")
    r.add_argument("--recipe", default=None, help="a recipe YAML; default is the default recipe")
    a = p.parse_args(argv)

    if a.cmd == "version":
        from lab import __version__
        from lab.common.relate_cli import relate_cli

        print(f"subgraph-lab {__version__}; {relate_cli(['--version']).stdout.splitlines()[0]}")
        return 0
    if a.cmd == "serve":
        from lab.api.server import serve

        return serve()
    if a.cmd == "run":
        from pathlib import Path

        from lab.contracts.recipe import Recipe
        from lab.pipeline import run_recipe

        recipe = Recipe.load(Path(a.recipe)) if a.recipe else Recipe()
        run = run_recipe(recipe)
        print(run.id)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
