"""Write every contract's JSON schema to lab/contracts/schemas/. `tests/test_contracts.py` fails when the files and
the models disagree, so a contract change shows up in review. Run it after the director changes a contract.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import BaseModel

from lab.contracts import api, evidence, factors, graph, manifest, recipe, tools

OUT = Path(__file__).resolve().parent.parent / "lab" / "contracts" / "schemas"


def models() -> dict[str, type[BaseModel]]:
    found: dict[str, type[BaseModel]] = {}
    for mod in (api, evidence, factors, graph, manifest, recipe, tools):
        for name, obj in vars(mod).items():
            if isinstance(obj, type) and issubclass(obj, BaseModel) and obj.__module__ == mod.__name__:
                found[name] = obj
    return dict(sorted(found.items()))


def render() -> dict[str, str]:
    return {f"{name}.json": json.dumps(m.model_json_schema(), indent=2, sort_keys=True) + "\n"
            for name, m in models().items()}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    files = render()
    for old in OUT.glob("*.json"):
        if old.name not in files:
            old.unlink()
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8")
    print(f"wrote {len(files)} schemas to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
