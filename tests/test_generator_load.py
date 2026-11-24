"""`python -m generator.load`, the seed loader for an empty Neo4j or Aura (decision 52).

The statements are checked against the import headers they come from. The guard and the load run against a small
in-memory stand-in for the database that understands exactly the loader's own queries. The real thing, against the
real import in a throwaway Neo4j, is the `live` test in `tests/test_generator.py`.
"""
from __future__ import annotations

import math
import re

import pytest

from generator.__main__ import main as generate
from generator.load import Refused, check_target, columns, keys, load, statement
from generator.writer import HEADERS, NODE_FILES, REL_FILES

STATEMENTS = {
    "accounts.csv": "UNWIND $rows AS r MERGE (n:Account {account_id: r.account_id}) ON CREATE SET n.opened = date(r.opened)",
    "devices.csv": "UNWIND $rows AS r MERGE (n:Device {device_id: r.device_id})",
    "emails.csv": "UNWIND $rows AS r MERGE (n:Email {email_id: r.email_id})",
    "addresses.csv": "UNWIND $rows AS r MERGE (n:Address {address_id: r.address_id})",
    "uses_device.csv": "UNWIND $rows AS r MATCH (a:Account {account_id: r.start}) MATCH (b:Device {device_id: r.end}) "
                       "MERGE (a)-[t:USES_DEVICE]->(b)",
    "has_email.csv": "UNWIND $rows AS r MATCH (a:Account {account_id: r.start}) MATCH (b:Email {email_id: r.end}) "
                     "MERGE (a)-[t:HAS_EMAIL]->(b)",
    "has_address.csv": "UNWIND $rows AS r MATCH (a:Account {account_id: r.start}) MATCH (b:Address {address_id: r.end}) "
                       "MERGE (a)-[t:HAS_ADDRESS]->(b)",
    "tx.csv": "UNWIND $rows AS r MATCH (a:Account {account_id: r.start}) MATCH (b:Account {account_id: r.end}) "
              "MERGE (a)-[t:TX {tx_id: r.tx_id}]->(b) ON CREATE SET t.amount = toFloat(r.amount), t.ts = datetime(r.ts)",
}


@pytest.fixture(scope="module")
def seed(tmp_path_factory):
    out = tmp_path_factory.mktemp("seed")
    generate(["--size", "small", "--out", str(out), "--seed", "42"])
    return out


class FakeDB:
    """Nodes as (labels, key) and relationships as (type, identity), answering only the loader's queries. A write
    merges by key, so a second load creates nothing."""

    def __init__(self, nodes=(), rels=()):
        self.nodes: dict[tuple[tuple[str, ...], str], None] = dict.fromkeys(nodes)
        self.rels: dict[tuple[str, str], None] = dict.fromkeys(rels)
        self.writes: list[tuple[str, int]] = []

    def read(self, query: str, **p) -> list[dict]:
        if query == "MATCH (n) RETURN count(n) AS n":
            return [{"n": len(self.nodes)}]
        if "size(labels(n)) = 1" in query:
            odd = [ls for ls, _ in self.nodes if not (len(ls) == 1 and ls[0] in p["labels"])]
            return [{"n": len(odd), "labels": [list(x) for x in dict.fromkeys(odd)][:5]}]
        if m := re.fullmatch(r"MATCH \(n:(\w+)\) WHERE n\.\w+ IS NULL OR NOT n\.\w+ IN \$keys RETURN count\(n\) AS n", query):
            own = set(p["keys"])
            return [{"n": sum(1 for ls, k in self.nodes if m.group(1) in ls and k not in own)}]
        if "NOT type(r) IN $types" in query:
            odd = [t for t, _ in self.rels if t not in p["types"]]
            return [{"n": len(odd), "types": list(dict.fromkeys(odd))[:5]}]
        if m := re.fullmatch(r"MATCH \(n:(\w+)\) RETURN count\(n\) AS n", query):
            return [{"n": sum(1 for ls, _ in self.nodes if m.group(1) in ls)}]
        if m := re.fullmatch(r"MATCH \(\)-\[r:(\w+)\]->\(\) RETURN count\(r\) AS n", query):
            return [{"n": sum(1 for t, _ in self.rels if t == m.group(1))}]
        raise AssertionError(f"unexpected read: {query}")

    def write(self, stmt: str, rows: list[dict]) -> dict:
        self.writes.append((stmt, len(rows)))
        if stmt.startswith("CREATE CONSTRAINT"):
            return {}
        before = (len(self.nodes), len(self.rels))
        if m := re.match(r"UNWIND \$rows AS r MERGE \(n:(\w+) \{(\w+):", stmt):
            for r in rows:
                self.nodes.setdefault(((m.group(1),), r[m.group(2)]), None)
        else:
            rtype = re.search(r"\[t:(\w+)", stmt).group(1)
            for r in rows:
                self.rels.setdefault((rtype, r.get("tx_id") or f"{r['start']}->{r['end']}"), None)
        return {"nodes_created": len(self.nodes) - before[0], "relationships_created": len(self.rels) - before[1]}


# ---- the statements --------------------------------------------------------------------------------------------
def test_each_csv_has_its_statement_from_the_import_header():
    assert {f: statement(f) for f in HEADERS} == STATEMENTS
    assert keys() == {"Account": "account_id", "Device": "device_id", "Email": "email_id", "Address": "address_id"}


def test_an_import_type_without_a_conversion_is_an_error():
    with pytest.raises(ValueError, match="localdatetime"):
        columns("id:ID(Thing),at:localdatetime")


# ---- the guard -------------------------------------------------------------------------------------------------
def test_an_empty_database_and_a_partial_seed_may_be_filled(seed):
    assert check_target(FakeDB().read, seed) == "empty"
    partial = FakeDB(nodes=[(("Account",), "A0000001"), (("Device",), "D0000001")], rels=[("USES_DEVICE", "x")])
    assert check_target(partial.read, seed) == "seed only"


@pytest.mark.parametrize(("nodes", "rels", "why"), [
    ([(("Customer",), "C1")], [], "other labels"),
    ([(("Account", "Customer"), "A0000001")], [], "other labels"),
    ([(("Account",), "ACC-0001")], [], "Account nodes whose account_id is not one of this seed's"),
    ([(("Account",), "A0000001")], [("OWNS", "x")], "relationships of other types"),
])
def test_anything_else_is_refused_and_nothing_is_written(seed, nodes, rels, why):
    db = FakeDB(nodes=nodes, rels=rels)
    with pytest.raises(Refused, match=re.escape(why)):
        load(seed, db.read, db.write, batch=5000, say=lambda m: None)
    assert db.writes == []


def test_a_real_graph_with_the_seeds_four_labels_is_still_refused(seed):
    """Labels alone would let the loader into a payments graph that happens to use Account, Device, Email and
    Address; the keys keep it out."""
    db = FakeDB(nodes=[(("Account",), "A0000001"), (("Account",), "cust-77812")])
    with pytest.raises(Refused, match="1 Account nodes"):
        check_target(db.read, seed)


# ---- the load --------------------------------------------------------------------------------------------------
def test_the_load_fills_an_empty_database_in_batches_and_matches_the_counts(seed):
    import json

    want = json.loads((seed / "generator.json").read_text(encoding="utf-8"))["counts"]
    db = FakeDB()
    result = load(seed, db.read, db.write, batch=5000, say=lambda m: None)
    assert result["state"] == "empty" and result["mismatch"] == {}
    assert result["created"] == {"nodes": want["nodes"]["total"], "relationships": want["relationships"]["total"]}
    assert [s for s, _ in db.writes[:4]] == [s for s, _ in db.writes if s.startswith("CREATE CONSTRAINT")]
    tx = [n for s, n in db.writes if ":TX " in s]
    assert len(tx) == math.ceil(want["relationships"]["TX"] / 5000) and max(tx) == 5000
    order = [s for s, _ in db.writes if not s.startswith("CREATE")]
    first_rel = next(i for i, s in enumerate(order) if "MATCH (a:" in s)
    assert all("MERGE (n:" in s for s in order[:first_rel])  # every node before any relationship


def test_a_second_load_creates_nothing(seed):
    db = FakeDB()
    load(seed, db.read, db.write, batch=5000, say=lambda m: None)
    again = load(seed, db.read, db.write, batch=5000, say=lambda m: None)
    assert again["state"] == "seed only" and again["created"] == {"nodes": 0, "relationships": 0}
    assert again["mismatch"] == {}


def test_the_files_the_generator_writes_are_the_files_the_loader_reads():
    assert set(HEADERS) == set(NODE_FILES.values()) | set(REL_FILES.values())
