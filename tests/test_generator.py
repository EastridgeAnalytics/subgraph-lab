"""The synthetic payments generator and the bundled-Neo4j seed (page 97, wave 1a).

Unit tests run `--size small` once per module. `slow` builds the 1M size. `live` runs the real import into a real
Neo4j, using throwaway Docker resources named `sgl-1a-*` and host ports 37474 and 37687 (never the `live_neo4j`
fixture, never the compose project's own containers or volumes). The same test then wipes that Neo4j and loads the
seed again with `python -m generator.load` (decision 52), and the two graphs must match.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from generator.__main__ import main as generate
from lab.settings import reload_settings

REPO = Path(__file__).resolve().parent.parent
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
WINDOW_END = date(2026, 11, 1)
OUTPUT_FILES = [
    "accounts.csv", "devices.csv", "emails.csv", "addresses.csv", "uses_device.csv", "has_email.csv",
    "has_address.csv", "tx.csv", "ground_truth.parquet", "generator.json",
]
NODE_LABELS = ["Account", "Device", "Email", "Address"]
REL_TYPES = ["USES_DEVICE", "HAS_EMAIL", "HAS_ADDRESS", "TX"]


def load(out: Path) -> SimpleNamespace:
    """The generator's output as frames. Ids stay strings; the CSV headers are renamed to plain names."""

    def csv(name: str, cols: list[str]) -> pl.DataFrame:
        return pl.read_csv(out / name, infer_schema=False, new_columns=cols)

    return SimpleNamespace(
        out=out,
        info=json.loads((out / "generator.json").read_text(encoding="utf-8")),
        truth=pl.read_parquet(out / "ground_truth.parquet"),
        accounts=csv("accounts.csv", ["account_id", "opened"]).with_columns(pl.col("opened").str.to_date()),
        device=csv("uses_device.csv", ["account_id", "device_id"]),
        email=csv("has_email.csv", ["account_id", "email_id"]),
        address=csv("has_address.csv", ["account_id", "address_id"]),
        tx=csv("tx.csv", ["src", "dst", "tx_id", "amount", "ts"]).with_columns(
            pl.col("amount").cast(pl.Float64), pl.col("ts").str.to_datetime(TS_FORMAT, time_zone="UTC")
        ),
    )


def groups(truth: pl.DataFrame, id_col: str, pattern_col: str) -> list[dict]:
    """[{id, pattern, accounts}] for every planted group, in id order."""
    rows = (truth.filter(pl.col(id_col) != "").group_by(id_col, pattern_col)
            .agg(pl.col("account_id").sort()).sort(id_col))
    return [{"id": r[0], "pattern": r[1], "accounts": r[2]} for r in rows.iter_rows()]


def rings(g: SimpleNamespace, pattern: str) -> list[dict]:
    return [r for r in groups(g.truth, "ring_id", "pattern") if r["pattern"] == pattern]


def lookalikes(g: SimpleNamespace, pattern: str) -> list[dict]:
    return [r for r in groups(g.truth, "lookalike_id", "lookalike_pattern") if r["pattern"] == pattern]


def inside(g: SimpleNamespace, members: list[str]) -> pl.DataFrame:
    """Payments with both ends in the group, oldest first."""
    return g.tx.filter(pl.col("src").is_in(members) & pl.col("dst").is_in(members)).sort("ts", "tx_id")


@pytest.fixture(scope="module")
def small(tmp_path_factory) -> SimpleNamespace:
    out = tmp_path_factory.mktemp("small")
    generate(["--size", "small", "--out", str(out), "--seed", "42"])
    return load(out)


# --- size, files, headers -------------------------------------------------------------------------------------


def test_small_is_under_aura_frees_smaller_limit(small):
    c = small.info["counts"]
    assert c["nodes"]["total"] < 50_000
    assert c["relationships"]["total"] < 175_000
    assert c["nodes"]["total"] == sum(c["nodes"][label] for label in NODE_LABELS)
    assert c["relationships"]["total"] == sum(c["relationships"][t] for t in REL_TYPES)


def test_generator_json_counts_match_the_files(small):
    lines = {f: sum(1 for _ in (small.out / f).open("rb")) - 1 for f in OUTPUT_FILES if f.endswith(".csv")}
    c = small.info["counts"]
    assert c["nodes"] == {
        "Account": lines["accounts.csv"], "Device": lines["devices.csv"], "Email": lines["emails.csv"],
        "Address": lines["addresses.csv"], "total": sum(lines[f] for f in
                                                        ("accounts.csv", "devices.csv", "emails.csv", "addresses.csv")),
    }
    assert c["relationships"]["TX"] == lines["tx.csv"]
    assert c["relationships"]["USES_DEVICE"] == lines["uses_device.csv"] == small.accounts.height
    assert small.info["seed"] == 42 and small.info["size"] == "small"
    assert re.fullmatch(r"\d+\.\d+\.\d+", small.info["generator_version"])


def test_every_output_file_is_written_with_unix_line_endings(small):
    for f in OUTPUT_FILES:
        assert (small.out / f).stat().st_size > 0, f
        if f.endswith((".csv", ".json")):
            assert b"\r" not in (small.out / f).read_bytes(), f


NEO4J_TYPES = {"string", "int", "long", "double", "float", "boolean", "date", "datetime", "localdatetime", "time"}


def test_csv_headers_are_neo4j_admin_headers(small):
    """Every header column is `name:type`, `name:ID(Space)`, `:START_ID(Space)` or `:END_ID(Space)`, and every
    relationship end names the id space of a node file."""
    spaces: dict[str, str] = {}
    for label, f in zip(NODE_LABELS, ["accounts.csv", "devices.csv", "emails.csv", "addresses.csv"], strict=True):
        cols = (small.out / f).open(encoding="utf-8").readline().rstrip("\n").split(",")
        ids = [c for c in cols if re.fullmatch(r"\w+:ID\(\w+\)", c)]
        assert len(ids) == 1 and ids[0].endswith(f":ID({label})"), (f, cols)
        spaces[label] = ids[0]
        for c in cols:
            m = re.fullmatch(r"(\w+):(\w+)(\(\w+\))?", c)
            assert m and (m[2] == "ID" or m[2] in NEO4J_TYPES), (f, c)
    for f, (start, end) in {
        "uses_device.csv": ("Account", "Device"), "has_email.csv": ("Account", "Email"),
        "has_address.csv": ("Account", "Address"), "tx.csv": ("Account", "Account"),
    }.items():
        cols = (small.out / f).open(encoding="utf-8").readline().rstrip("\n").split(",")
        assert cols[:2] == [f":START_ID({start})", f":END_ID({end})"], (f, cols)
        for c in cols[2:]:
            m = re.fullmatch(r"(\w+):(\w+)", c)
            assert m and m[2] in NEO4J_TYPES, (f, c)
    assert (small.out / "tx.csv").open(encoding="utf-8").readline().rstrip("\n").split(",")[2:] == [
        "tx_id:string", "amount:double", "ts:datetime"]
    assert (small.out / "accounts.csv").open(encoding="utf-8").readline().rstrip("\n") == \
        "account_id:ID(Account),opened:date"


def test_keys_are_unique_and_every_link_resolves(small):
    accounts = small.accounts["account_id"]
    assert accounts.n_unique() == accounts.len()
    assert small.tx["tx_id"].n_unique() == small.tx.height
    assert small.tx["src"].is_in(accounts.implode()).all() and small.tx["dst"].is_in(accounts.implode()).all()
    assert (small.tx["src"] != small.tx["dst"]).all()
    for links in (small.device, small.email, small.address):  # exactly one of each per account
        assert sorted(links["account_id"].to_list()) == sorted(accounts.to_list())
    for node_file, links, col in (("devices.csv", small.device, "device_id"), ("emails.csv", small.email, "email_id"),
                                  ("addresses.csv", small.address, "address_id")):
        ids = pl.read_csv(small.out / node_file, infer_schema=False).to_series()
        assert ids.n_unique() == ids.len()
        assert set(links[col].to_list()) == set(ids.to_list())  # no node without an account


def test_amounts_and_times_are_valid(small):
    tx = small.tx
    assert (tx["amount"] > 0).all()
    assert tx["ts"].min().date() >= date(2026, 5, 1) and tx["ts"].max().date() < WINDOW_END
    assert tx["ts"].is_sorted()  # payment ids follow time


def test_every_payment_comes_after_both_accounts_opened(small):
    acc = small.accounts
    late = (small.tx.join(acc.rename({"account_id": "src", "opened": "src_opened"}), on="src")
            .join(acc.rename({"account_id": "dst", "opened": "dst_opened"}), on="dst")
            .filter((pl.col("ts").dt.date() < pl.col("src_opened")) | (pl.col("ts").dt.date() < pl.col("dst_opened"))))
    assert late.height == 0


# --- ground truth ---------------------------------------------------------------------------------------------


def test_ground_truth_columns_and_empty_strings(small):
    t = small.truth
    assert t.columns == ["account_id", "ring_id", "pattern", "lookalike_id", "lookalike_pattern"]
    assert all(dtype == pl.String for dtype in t.schema.values())
    assert t.null_count().row(0) == (0, 0, 0, 0, 0)
    assert t["account_id"].to_list() == small.accounts["account_id"].to_list()
    both = t.filter((pl.col("ring_id") != "") & (pl.col("lookalike_id") != ""))
    assert both.height == 0  # a group per account at most
    assert t.filter((pl.col("ring_id") == "") != (pl.col("pattern") == "")).height == 0
    assert t.filter((pl.col("lookalike_id") == "") != (pl.col("lookalike_pattern") == "")).height == 0


def test_group_counts_match_the_ground_truth_and_scale_for_small(small):
    planted = small.info["planted"]
    assert set(planted) == {"mule_ring", "structuring", "pass_through", "family", "payroll", "landlord"}
    for pattern, p in planted.items():
        found = rings(small, pattern) if pattern in ("mule_ring", "structuring", "pass_through") \
            else lookalikes(small, pattern)
        assert 15 <= len(found) <= 25, pattern
        assert p["groups"] == len(found), pattern
        assert p["accounts"] == sum(len(r["accounts"]) for r in found), pattern


# --- planted fraud --------------------------------------------------------------------------------------------


def _members(g: SimpleNamespace, table: str, col: str, members: list[str]) -> list[str]:
    frame = getattr(g, table)
    return frame.filter(pl.col("account_id").is_in(members))[col].to_list()


def test_mule_ring_members_share_one_device_and_one_address(small):
    found = rings(small, "mule_ring")
    assert found
    for ring in found:
        ids = ring["accounts"]
        assert 4 <= len(ids) <= 8
        assert len(set(_members(small, "device", "device_id", ids))) == 1, ring["id"]
        assert len(set(_members(small, "address", "address_id", ids))) == 1, ring["id"]
        assert len(set(_members(small, "email", "email_id", ids))) == len(ids), ring["id"]  # own emails
        device = _members(small, "device", "device_id", ids)[0]
        assert small.device.filter(pl.col("device_id") == device).height == len(ids)  # nobody else on it


def test_mule_ring_takes_money_in_then_cycles_it_within_hours_repeatedly(small):
    for ring in rings(small, "mule_ring"):
        ids = ring["accounts"]
        k = len(ids)
        internal = inside(small, ids)
        inflow = small.tx.filter(pl.col("dst").is_in(ids) & ~pl.col("src").is_in(ids))
        assert inflow.height >= 3 * k, ring["id"]  # outsiders pay in, round after round
        assert set(internal["src"]) == set(ids) == set(internal["dst"]), ring["id"]  # a cycle touches everyone
        assert internal.height >= 3 * k, ring["id"]  # repeated: at least three rounds
        first_cycle = internal.head(k)
        assert first_cycle["ts"].max() - first_cycle["ts"].min() <= timedelta(hours=10), ring["id"]


def test_structuring_payments_are_just_under_10000_and_the_collector_pays_out(small):
    found = rings(small, "structuring")
    assert found
    for ring in found:
        ids = ring["accounts"]
        assert 5 <= len(ids) <= 10
        assert len(set(_members(small, "email", "email_id", ids))) == 1, ring["id"]  # one shared email
        paid = inside(small, ids)
        assert paid.height >= 2 * (len(ids) - 1), ring["id"]  # repeatedly
        assert (paid["amount"] < 10_000).all(), ring["id"]
        assert (paid["amount"] >= 9_000).all(), ring["id"]
        collector = paid["dst"].mode().item()
        assert (paid["dst"] == collector).all(), ring["id"]
        assert paid["src"].n_unique() >= len(ids) - 1 and collector not in set(paid["src"]), ring["id"]
        payouts = small.tx.filter((pl.col("src") == collector) & ~pl.col("dst").is_in(ids)
                                  & (pl.col("amount") >= 25_000))
        assert payouts.height >= 4, ring["id"]  # a large sum after each batch (at least four batches)


def test_pass_through_chains_forward_nearly_the_same_amount_within_an_hour(small):
    found = rings(small, "pass_through")
    assert found
    for ring in found:
        ids = ring["accounts"]
        assert 3 <= len(ids) <= 5
        internal = inside(small, ids)
        assert internal.height >= 4 * (len(ids) - 1), ring["id"]
        into = {}
        for r in small.tx.filter(pl.col("dst").is_in(ids)).iter_rows(named=True):
            into.setdefault(r["dst"], []).append(r)
        for p in internal.iter_rows(named=True):  # each hop forwards what its sender just received
            assert any(timedelta(0) <= p["ts"] - r["ts"] <= timedelta(hours=1) and 0.96 <= p["amount"] / r["amount"] <= 1.0
                       for r in into[p["src"]]), (ring["id"], p)


def test_planted_ring_members_are_new_accounts(small):
    ordinary_opened = small.accounts.join(
        small.truth.filter((pl.col("ring_id") == "") & (pl.col("lookalike_id") == "")), on="account_id")["opened"]
    assert (ordinary_opened.max() - ordinary_opened.min()).days >= 650  # spread over about two years
    assert ordinary_opened.max() <= WINDOW_END - timedelta(days=30)
    opened = dict(zip(small.accounts["account_id"], small.accounts["opened"], strict=True))
    for ring in groups(small.truth, "ring_id", "pattern"):
        first = inside(small, ring["accounts"])["ts"].min().date()
        for a in ring["accounts"]:
            assert 5 <= (first - opened[a]).days <= 36, (ring["id"], a)
            assert opened[a] >= WINDOW_END - timedelta(days=112)


# --- look-alikes ----------------------------------------------------------------------------------------------


def test_lookalikes_of_all_three_kinds_are_present(small):
    kinds = set(small.truth.filter(pl.col("lookalike_id") != "")["lookalike_pattern"])
    assert kinds == {"family", "payroll", "landlord"}
    assert small.truth.filter(pl.col("lookalike_id") != "")["lookalike_id"].str.starts_with("L").all()


def test_family_shares_one_device_and_address_and_pays_small_amounts(small):
    for fam in lookalikes(small, "family"):
        ids = fam["accounts"]
        assert 2 <= len(ids) <= 5
        assert len(set(_members(small, "device", "device_id", ids))) == 1, fam["id"]
        assert len(set(_members(small, "address", "address_id", ids))) == 1, fam["id"]
        paid = inside(small, ids)
        assert paid.height >= 10 and paid["amount"].max() <= 500, fam["id"]


def test_payroll_pays_20_to_60_accounts_the_same_amount_on_the_same_day_each_month(small):
    for pay in lookalikes(small, "payroll"):
        ids = pay["accounts"]
        assert 21 <= len(ids) <= 61
        paid = inside(small, ids)
        payer = paid["src"].mode().item()
        assert (paid["src"] == payer).all() and paid["dst"].n_unique() >= 15, pay["id"]
        assert paid["amount"].n_unique() == 1, pay["id"]
        days = paid.with_columns(day=pl.col("ts").dt.date())
        assert days["day"].dt.day().n_unique() == 1, pay["id"]  # the same day of every month
        assert days["day"].dt.month().n_unique() >= 4, pay["id"]  # every month of the window
        # nobody is paid twice in one month
        assert days.group_by("dst", days["day"].dt.month().alias("m")).len()["len"].max() == 1, pay["id"]


def test_landlord_collects_rent_just_under_a_round_number_each_month(small):
    for lord in lookalikes(small, "landlord"):
        ids = lord["accounts"]
        assert 6 <= len(ids) <= 16
        paid = inside(small, ids)
        landlord = paid["dst"].mode().item()
        assert (paid["dst"] == landlord).all() and paid["src"].n_unique() >= 4, lord["id"]
        assert ((paid["amount"] % 100) == 95).all(), lord["id"]  # 1,995 or 2,495
        assert paid["amount"].is_between(1_000, 3_000).all(), lord["id"]
        assert paid["ts"].dt.month().n_unique() >= 4, lord["id"]


# --- everyone else ---------------------------------------------------------------------------------------------


def test_everyone_else_has_their_own_device_email_and_address(small):
    shared_by = {"device": {"mule_ring", "family"}, "address": {"mule_ring", "family"}, "email": {"structuring"}}
    group_of = dict(zip(small.truth["account_id"],
                        (r or l_ for r, l_ in zip(small.truth["pattern"], small.truth["lookalike_pattern"], strict=True)),
                        strict=True))
    for kind, links in (("device", small.device), ("address", small.address), ("email", small.email)):
        col = f"{kind}_id"
        for key, members in links.group_by(col).agg(pl.col("account_id")).iter_rows():
            if len(members) > 1:
                assert {group_of[a] for a in members} <= shared_by[kind], (kind, key)
    mule_and_family = len(rings(small, "mule_ring")) + len(lookalikes(small, "family"))
    assert small.device.group_by("device_id").len().filter(pl.col("len") > 1).height == mule_and_family
    assert small.email.group_by("email_id").len().filter(pl.col("len") > 1).height == len(rings(small, "structuring"))


# --- determinism, the CLI ---------------------------------------------------------------------------------------


def _hashes(out: Path) -> dict[str, str]:
    return {f: hashlib.sha256((out / f).read_bytes()).hexdigest() for f in OUTPUT_FILES}


def _cli(out: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "generator", "--out", str(out), *args], cwd=REPO,
                          capture_output=True, text=True, check=True, timeout=300)


def test_same_seed_and_size_give_byte_identical_files(small, tmp_path):
    _cli(tmp_path / "a", "--size", "small", "--seed", "42")  # through the real command line
    assert _hashes(tmp_path / "a") == _hashes(small.out)
    generate(["--size", "small", "--out", str(tmp_path / "b"), "--seed", "42"])
    assert _hashes(tmp_path / "b") == _hashes(small.out)


def test_a_different_seed_gives_a_different_graph(small, tmp_path):
    generate(["--size", "small", "--out", str(tmp_path), "--seed", "43"])
    other = _hashes(tmp_path)
    mine = _hashes(small.out)
    assert other["tx.csv"] != mine["tx.csv"] and other["ground_truth.parquet"] != mine["ground_truth.parquet"]
    assert json.loads((tmp_path / "generator.json").read_text(encoding="utf-8"))["seed"] == 43


def test_the_default_seed_is_the_lab_seed(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_SEED", "7")
    reload_settings()
    try:
        generate(["--size", "small", "--out", str(tmp_path)])
    finally:
        monkeypatch.undo()
        reload_settings()
    assert json.loads((tmp_path / "generator.json").read_text(encoding="utf-8"))["seed"] == 7


def test_the_command_line_rejects_an_unknown_size(tmp_path):
    r = subprocess.run([sys.executable, "-m", "generator", "--size", "huge", "--out", str(tmp_path)], cwd=REPO,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode != 0 and "invalid choice" in r.stderr


def test_import_script_and_schema_are_what_compose_mounts():
    sh = (REPO / "generator" / "import.sh").read_bytes()
    assert b"\r" not in sh and sh.startswith(b"#!/usr/bin/env bash\n") and b"set -euo pipefail" in sh
    assert b".subgraph-lab-seeded" in sh and b"--overwrite-destination" in sh
    cypher = (REPO / "generator" / "schema.cypher").read_text(encoding="utf-8")
    for label, key in (("Account", "account_id"), ("Device", "device_id"), ("Email", "email_id"),
                       ("Address", "address_id")):
        assert re.search(rf"CREATE CONSTRAINT \w+ IF NOT EXISTS FOR \(\w+:{label}\) REQUIRE \w+\.{key} IS UNIQUE;", cypher)
    compose = (REPO / "compose.yaml").read_text(encoding="utf-8")
    assert "./generator/import.sh:/seed/import.sh:ro" in compose
    assert "./generator/schema.cypher:/seed/schema.cypher:ro" in compose


# --- 1M ----------------------------------------------------------------------------------------------------------


@pytest.mark.slow
def test_1m_lands_near_a_million_relationships(tmp_path):
    t0 = time.perf_counter()
    generate(["--size", "1m", "--out", str(tmp_path), "--seed", "42"])
    seconds = time.perf_counter() - t0
    info = json.loads((tmp_path / "generator.json").read_text(encoding="utf-8"))
    total = info["counts"]["relationships"]["total"]
    assert 950_000 <= total <= 1_050_000, total
    assert info["counts"]["relationships"]["TX"] == sum(1 for _ in (tmp_path / "tx.csv").open("rb")) - 1
    assert all(p["groups"] >= 100 for p in info["planted"].values())  # groups scale with size
    assert seconds < 300, seconds
    print(f"1m: {info['counts']} in {seconds:.1f}s")


# --- live: the real import into a real Neo4j ----------------------------------------------------------------------

IMAGE = "neo4j:2026.08.1"
BOLT_PORT = int(os.environ.get("SGL_1A_BOLT_PORT", "37687"))  # when another Neo4j already holds the defaults
HTTP_PORT = int(os.environ.get("SGL_1A_HTTP_PORT", "37474"))
PASSWORD = "sgl-test-pass"


def _docker(*args: str, check: bool = True, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, timeout=timeout)


def _fingerprint(one) -> dict:
    """What a load must reproduce: counts, property names, value types, and a hash of every node and relationship."""

    def digest(query: str) -> str:
        return hashlib.sha256(json.dumps([list(r.values()) for r in one(query)]).encode()).hexdigest()

    out: dict = {label: one(f"MATCH (n:{label}) RETURN count(n) AS n")[0]["n"] for label in NODE_LABELS}
    out |= {t: one(f"MATCH ()-[r:{t}]->() RETURN count(r) AS n")[0]["n"] for t in REL_TYPES}
    out["node_keys"] = sorted(one("MATCH (n) UNWIND keys(n) AS k RETURN collect(DISTINCT k) AS k")[0]["k"])
    out["rel_keys"] = sorted(one("MATCH ()-[r]->() UNWIND keys(r) AS k RETURN collect(DISTINCT k) AS k")[0]["k"])
    out["types"] = one("MATCH (a:Account)-[t:TX]->() RETURN collect(DISTINCT valueType(a.opened)) AS opened, "
                       "collect(DISTINCT valueType(t.ts)) AS ts, collect(DISTINCT valueType(t.amount)) AS amount, "
                       "collect(DISTINCT valueType(t.tx_id)) AS tx_id")[0]
    out["accounts"] = digest("MATCH (a:Account) RETURN a.account_id AS k, toString(a.opened) AS o ORDER BY k")
    out["spokes"] = digest("MATCH (a:Account)-[r]->(x) WHERE type(r) <> 'TX' RETURN a.account_id AS a, type(r) AS t, "
                           "coalesce(x.device_id, x.email_id, x.address_id) AS x ORDER BY a, t")
    out["tx"] = digest("MATCH (a:Account)-[t:TX]->(b:Account) RETURN t.tx_id AS id, a.account_id AS s, "
                       "b.account_id AS d, t.amount AS m, toString(t.ts) AS ts ORDER BY id")
    return out


@pytest.mark.live
@pytest.mark.timeout(900)
def test_import_script_seeds_a_real_neo4j(small):
    from neo4j import GraphDatabase

    if shutil.which("docker") is None or _docker("info", check=False, timeout=60).returncode != 0:
        pytest.skip("docker is not running")
    uid = uuid.uuid4().hex[:8]
    volume, name = f"sgl-1a-data-{uid}", f"sgl-1a-neo4j-{uid}"
    import_sh, schema = REPO / "generator" / "import.sh", REPO / "generator" / "schema.cypher"

    def run_import(n: int) -> subprocess.CompletedProcess:
        return _docker(
            "run", "--rm", "--name", f"sgl-1a-import-{uid}-{n}", "--entrypoint", "bash",
            "--mount", f"type=bind,source={small.out},target=/import,readonly",
            "--mount", f"type=volume,source={volume},target=/data",
            "--mount", f"type=bind,source={import_sh},target=/seed/import.sh,readonly",
            IMAGE, "/seed/import.sh", timeout=600,
        )

    driver = None
    try:
        _docker("volume", "create", volume)
        first = run_import(1)
        assert "seed: done" in first.stdout, first.stdout[-500:]
        second = run_import(2)  # the marker makes a second run a no-op, not a wipe
        assert "already seeded" in second.stdout and "IMPORT DONE" not in second.stdout

        _docker(
            "run", "-d", "--name", name, "-p", f"127.0.0.1:{BOLT_PORT}:7687", "-p", f"127.0.0.1:{HTTP_PORT}:7474",
            "-e", f"NEO4J_AUTH=neo4j/{PASSWORD}", "-e", "DO_NOT_TRACK=1",
            "--mount", f"type=volume,source={volume},target=/data",
            "--mount", f"type=bind,source={schema},target=/seed/schema.cypher,readonly",
            IMAGE,
        )
        driver = GraphDatabase.driver(f"bolt://127.0.0.1:{BOLT_PORT}", auth=("neo4j", PASSWORD))
        deadline = time.monotonic() + 240
        while True:
            try:
                driver.execute_query("RETURN 1", database_="neo4j")
                break
            except Exception as e:  # noqa: BLE001
                running = _docker("inspect", "-f", "{{.State.Running}}", name, check=False).stdout.strip()
                if running != "true" or time.monotonic() > deadline:
                    pytest.fail(f"Neo4j did not come up: {e}\n{_docker('logs', '--tail', '40', name, check=False).stdout}")
                time.sleep(3)

        def one(query: str, **params):
            records, _, _ = driver.execute_query(query, params, database_="neo4j")
            return records

        c = small.info["counts"]
        for label in NODE_LABELS:
            assert one(f"MATCH (n:{label}) RETURN count(n) AS n")[0]["n"] == c["nodes"][label], label
        for rel in REL_TYPES:
            assert one(f"MATCH ()-[r:{rel}]->() RETURN count(r) AS n")[0]["n"] == c["relationships"][rel], rel
        typed = one("MATCH (a:Account)-[t:TX]->() RETURN valueType(a.opened) AS d, valueType(t.ts) AS ts, "
                    "valueType(t.amount) AS amount, t.tx_id AS id LIMIT 1")[0]
        assert typed["d"].startswith("DATE") and typed["ts"].startswith("ZONED DATETIME")
        assert typed["amount"].startswith("FLOAT") and typed["id"].startswith("T")
        ring = rings(small, "mule_ring")[0]["accounts"]
        assert one("MATCH (a:Account)-[:USES_DEVICE]->(d:Device) WHERE a.account_id IN $ids "
                   "RETURN count(DISTINCT d) AS n", ids=ring)[0]["n"] == 1

        for _ in range(2):  # the schema file works through cypher-shell, and running it again changes nothing
            _docker("exec", name, "cypher-shell", "-u", "neo4j", "-p", PASSWORD, "-f", "/seed/schema.cypher",
                    timeout=300)
        constraints = {(r["label"], r["property"]) for r in one(
            "SHOW CONSTRAINTS YIELD type, labelsOrTypes, properties WHERE type = 'NODE_PROPERTY_UNIQUENESS' "
            "RETURN labelsOrTypes[0] AS label, properties[0] AS property")}
        assert constraints == {("Account", "account_id"), ("Device", "device_id"), ("Email", "email_id"),
                               ("Address", "address_id")}

        # The seed loader (decision 52): the same graph as the import, over Bolt, and nothing twice.
        imported = _fingerprint(one)
        with driver.session(database="neo4j") as session:
            session.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS").consume()
        assert one("MATCH (n) RETURN count(n) AS n")[0]["n"] == 0
        env = {**os.environ, "NEO4J_URI": f"bolt://127.0.0.1:{BOLT_PORT}", "NEO4J_USERNAME": "neo4j",
               "NEO4J_PASSWORD": PASSWORD, "NEO4J_DATABASE": "neo4j", "DO_NOT_TRACK": "1"}

        def loader() -> subprocess.CompletedProcess:
            return subprocess.run([sys.executable, "-m", "generator.load", "--dir", str(small.out)], cwd=REPO, env=env,
                                  capture_output=True, text=True, timeout=600)

        first = loader()
        assert first.returncode == 0, first.stderr[-2000:]
        assert _fingerprint(one) == imported
        second = loader()
        assert second.returncode == 0, second.stderr[-2000:]
        assert '"created": {"nodes": 0, "relationships": 0}' in second.stdout and '"state": "seed only"' in second.stdout
        driver.execute_query("CREATE (:Customer {customer_id: 'C1'})", database_="neo4j")
        third = loader()
        assert third.returncode == 2 and "Customer" in third.stderr and "Nothing was written" in third.stderr
        assert {k: v for k, v in _fingerprint(one).items() if k in NODE_LABELS + REL_TYPES} == {
            k: v for k, v in imported.items() if k in NODE_LABELS + REL_TYPES}
    finally:
        if driver is not None:
            driver.close()
        _docker("rm", "-f", name, check=False)
        _docker("volume", "rm", "-f", volume, check=False)
