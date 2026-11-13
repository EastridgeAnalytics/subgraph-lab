"""Write a `Graph` as neo4j-admin import CSVs, `ground_truth.parquet` and `generator.json`.

Headers carry the import types, for example `account_id:ID(Account)`, `opened:date`, `:START_ID(Account)`,
`amount:double`, `ts:datetime`. `generator/import.sh` names the labels and types on the command line. Nothing in
any file depends on the time of the run, so the same seed and size give byte-identical files.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl

from generator import GENERATOR_VERSION
from generator.model import END, START, Graph

TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# file name -> header line. The column order of each frame below follows its header.
HEADERS = {
    "accounts.csv": "account_id:ID(Account),opened:date",
    "devices.csv": "device_id:ID(Device)",
    "emails.csv": "email_id:ID(Email)",
    "addresses.csv": "address_id:ID(Address)",
    "uses_device.csv": ":START_ID(Account),:END_ID(Device)",
    "has_email.csv": ":START_ID(Account),:END_ID(Email)",
    "has_address.csv": ":START_ID(Account),:END_ID(Address)",
    "tx.csv": ":START_ID(Account),:END_ID(Account),tx_id:string,amount:double,ts:datetime",
}
NODE_FILES = {"Account": "accounts.csv", "Device": "devices.csv", "Email": "emails.csv", "Address": "addresses.csv"}
REL_FILES = {"USES_DEVICE": "uses_device.csv", "HAS_EMAIL": "has_email.csv", "HAS_ADDRESS": "has_address.csv",
             "TX": "tx.csv"}


def _write_csv(path: Path, header: str, frame: pl.DataFrame) -> None:
    with path.open("wb") as f:
        f.write(header.encode() + b"\n")
        frame.write_csv(f, include_header=False, line_terminator="\n", float_precision=2)


def _day(epoch_seconds: int) -> str:
    return datetime.fromtimestamp(epoch_seconds, UTC).strftime("%Y-%m-%d")


def write(g: Graph, out: Path, size: str, seed: int) -> dict:
    """Write every file into `out` (made if missing) and return what `generator.json` holds."""
    out.mkdir(parents=True, exist_ok=True)
    ids = g.account_ids
    frames = {
        "accounts.csv": pl.DataFrame({"a": ids, "o": pl.Series(g.opened_days.astype(np.int32)).cast(pl.Date)}),
        "devices.csv": pl.DataFrame({"d": g.device_ids}),
        "emails.csv": pl.DataFrame({"e": g.email_ids}),
        "addresses.csv": pl.DataFrame({"x": g.address_ids}),
        "uses_device.csv": pl.DataFrame({"a": ids, "d": g.device_ids[g.device_of]}),
        "has_email.csv": pl.DataFrame({"a": ids, "e": g.email_ids[g.email_of]}),
        "has_address.csv": pl.DataFrame({"a": ids, "x": g.address_ids[g.address_of]}),
        "tx.csv": pl.DataFrame({
            "s": ids[g.tx_src], "d": ids[g.tx_dst], "t": g.tx_id, "m": g.tx_amount,
            "ts": pl.from_epoch(pl.Series(g.tx_ts), time_unit="s").dt.strftime(TS_FORMAT),
        }),
    }
    for name, frame in frames.items():
        _write_csv(out / name, HEADERS[name], frame)

    pl.DataFrame({
        "account_id": ids, "ring_id": g.ring_id, "pattern": g.pattern,
        "lookalike_id": g.lookalike_id, "lookalike_pattern": g.lookalike_pattern,
    }).write_parquet(out / "ground_truth.parquet")

    nodes = {label: frames[name].height for label, name in NODE_FILES.items()}
    rels = {rtype: frames[name].height for rtype, name in REL_FILES.items()}
    info = {
        "generator_version": GENERATOR_VERSION,
        "size": size,
        "seed": seed,
        "window": {"start": _day(START), "end": _day(END)},
        "counts": {
            "nodes": {**nodes, "total": sum(nodes.values())},
            "relationships": {**rels, "total": sum(rels.values())},
        },
        "planted": {p: {"groups": g.groups[p], "accounts": g.group_accounts[p]} for p in g.groups},
    }
    (out / "generator.json").write_bytes((json.dumps(info, indent=2) + "\n").encode())
    return info
