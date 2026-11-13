"""Build a Relate store from the fixture CSVs, shaped the way `relate pull` leaves one: the generator's labels,
keys and types, the uniqueness constraints, zoned datetimes on payments."""
from __future__ import annotations

from pathlib import Path

import polars as pl

from lab.common.store import open_store

GRAPH = Path(__file__).parent / "graph"

CONSTRAINTS = [
    "CREATE CONSTRAINT FOR (a:Account) REQUIRE a.account_id IS UNIQUE",
    "CREATE CONSTRAINT FOR (d:Device) REQUIRE d.device_id IS UNIQUE",
    "CREATE CONSTRAINT FOR (e:Email) REQUIRE e.email_id IS UNIQUE",
    "CREATE CONSTRAINT FOR (a:Address) REQUIRE a.address_id IS UNIQUE",
]

LOADS = [
    ("accounts.csv", "UNWIND $rows AS r CREATE (:Account {account_id: r.account_id, opened: date(r.opened)})"),
    ("devices.csv", "UNWIND $rows AS r CREATE (:Device {device_id: r.device_id})"),
    ("emails.csv", "UNWIND $rows AS r CREATE (:Email {email_id: r.email_id})"),
    ("addresses.csv", "UNWIND $rows AS r CREATE (:Address {address_id: r.address_id})"),
    ("uses_device.csv", "UNWIND $rows AS r MATCH (a:Account {account_id: r.account_id}) "
                        "MATCH (d:Device {device_id: r.device_id}) CREATE (a)-[:USES_DEVICE]->(d)"),
    ("has_email.csv", "UNWIND $rows AS r MATCH (a:Account {account_id: r.account_id}) "
                      "MATCH (e:Email {email_id: r.email_id}) CREATE (a)-[:HAS_EMAIL]->(e)"),
    ("has_address.csv", "UNWIND $rows AS r MATCH (a:Account {account_id: r.account_id}) "
                        "MATCH (x:Address {address_id: r.address_id}) CREATE (a)-[:HAS_ADDRESS]->(x)"),
    ("tx.csv", "UNWIND $rows AS r MATCH (s:Account {account_id: r.src}) MATCH (d:Account {account_id: r.dst}) "
               "CREATE (s)-[:TX {tx_id: r.tx_id, amount: toFloat(r.amount), ts: datetime(r.ts)}]->(d)"),
]


def build_fixture_store(path: Path) -> None:
    conn = open_store(path)
    try:
        for stmt in CONSTRAINTS:
            conn.execute(stmt)
        for name, stmt in LOADS:
            rows = pl.read_csv(GRAPH / name, infer_schema=False).to_dicts()
            conn.execute(stmt, {"rows": rows})
    finally:
        conn.close()


def ground_truth() -> pl.DataFrame:
    return pl.read_csv(GRAPH / "ground_truth.csv", infer_schema=False).with_columns(
        pl.col("ring_id").fill_null(""), pl.col("lookalike_id").fill_null(""), pl.col("pattern").fill_null("")
    )
