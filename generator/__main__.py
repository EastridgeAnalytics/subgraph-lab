"""`python -m generator --size small|1m --out DIR [--seed N]`"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from generator.model import SIZES, build
from generator.writer import write


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m generator", description="Write the synthetic payments graph.")
    p.add_argument("--size", choices=sorted(SIZES), required=True,
                   help="small: under 50,000 nodes and 175,000 relationships; 1m: about a million relationships")
    p.add_argument("--out", type=Path, required=True, help="directory for the CSVs, ground_truth.parquet, generator.json")
    p.add_argument("--seed", type=int, default=None, help="random seed (default: LAB_SEED, 42)")
    args = p.parse_args(argv)
    if args.seed is None:
        from lab.settings import get_settings

        args.seed = get_settings().seed
    if args.seed < 0:
        p.error("--seed must be zero or more")

    t0 = time.perf_counter()
    info = write(build(args.size, args.seed), args.out, args.size, args.seed)
    c = info["counts"]
    print(f"wrote {args.size} (seed {args.seed}) to {args.out}: {c['nodes']['total']:,} nodes, "
          f"{c['relationships']['total']:,} relationships in {time.perf_counter() - t0:.1f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
