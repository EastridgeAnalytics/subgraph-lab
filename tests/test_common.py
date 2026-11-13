"""Wave 0e: the shared helpers."""
from __future__ import annotations

from datetime import UTC, datetime

import polars as pl
import pytest

from lab.common import (
    alert_key,
    community_key,
    content_hash,
    estimate_memory,
    evidence_hash,
    iso_utc,
    query_frame,
    relate_cli,
    run_id,
)
from lab.common.relate_cli import credentials_file
from lab.settings import Settings, derive_query_uri


def test_run_id_sorts_by_time():
    a = run_id(datetime(2026, 11, 12, 9, tzinfo=UTC), "aaaaaa")
    b = run_id(datetime(2026, 11, 12, 10, tzinfo=UTC), "000000")
    assert a == "run-20261112T090000Z-aaaaaa" and a < b


def test_keys_are_stable_and_documented():
    assert alert_key("r1", "A1", "P30D") == alert_key("r1", "A1", "P30D")
    assert alert_key("r1", "A1", "P30D") != alert_key("r2", "A1", "P30D")
    # sha1(run_id | lowest member key): member order does not matter
    assert community_key("r1", ["A3", "A1", "A2"]) == community_key("r1", {"A2", "A1"})
    with pytest.raises(ValueError):
        community_key("r1", [])


def test_evidence_hash_ignores_key_order():
    assert evidence_hash({"a": 1, "b": [1, 2]}) == evidence_hash({"b": [1, 2], "a": 1})
    assert evidence_hash({"a": 1}) != evidence_hash({"a": 2})
    assert len(content_hash({"x": 1})) == 16


def test_iso_utc_has_no_offset_or_fraction():
    assert iso_utc(datetime(2026, 11, 12, 10, 0, 0, 123456, tzinfo=UTC)) == "2026-11-12T10:00:00Z"
    assert iso_utc(datetime(2026, 11, 12, 10)) == "2026-11-12T10:00:00Z"


@pytest.mark.parametrize("bolt,expected", [
    ("neo4j+s://abc.databases.neo4j.io", "https://abc.databases.neo4j.io"),
    ("bolt://localhost:7687", "http://localhost:7474"),
    ("neo4j://neo4j", "http://neo4j:7474"),
    ("http://localhost:17474", "http://localhost:17474"),
])
def test_query_uri_derivation(bolt, expected):
    assert derive_query_uri(bolt) == expected


def test_query_uri_maps_bundled_ports_and_refuses_unknown():
    assert derive_query_uri("bolt://localhost:17687", {17687: 17474}) == "http://localhost:17474"
    with pytest.raises(ValueError):
        derive_query_uri("bolt://localhost:9999")


def test_plaintext_only_for_non_loopback_http():
    assert Settings(NEO4J_URI="bolt://neo4j:7687").plaintext is True
    assert Settings(NEO4J_URI="bolt://localhost:7687").plaintext is False
    assert Settings(NEO4J_URI="neo4j+s://x.databases.neo4j.io").plaintext is False


def test_credentials_file_is_removed(data_dir):
    with credentials_file() as path:
        text = path.read_text()
        assert "NEO4J_URI=http" in text and "NEO4J_PASSWORD=" in text
    assert not path.exists()


def test_memory_estimate_uses_documented_sizes():
    assert estimate_memory(1000, 0) == int(1000 * 502 * 1.54)


def test_fixture_store_shape(fixture_run, truth):
    counts = fixture_run.table(
        "MATCH (a:Account) WITH count(a) AS accounts "
        "MATCH ()-[t:TX]->() RETURN accounts, count(t) AS payments"
    )
    assert counts.row(0, named=True) == {"accounts": 200, "payments": 2000}
    assert truth.filter(pl.col("ring_id") == "R1").height == 6
    assert truth.filter(pl.col("ring_id") == "R2").height == 8
    shared = fixture_run.table(
        "MATCH (a:Account)-[:USES_DEVICE]->(d:Device {device_id: 'D-R1'}) RETURN count(a) AS n"
    )
    assert shared["n"][0] == 6


def test_query_frame_through_arrow(fixture_run):
    df = query_frame(fixture_run.store(), "MATCH (a:Account) RETURN a.account_id AS id ORDER BY id LIMIT 3")
    assert df["id"].to_list() == ["A0001", "A0002", "A0003"]


def test_relate_binary_is_1_0_1_and_untracked():
    out = relate_cli(["--version"])
    assert "1.0.1" in out.stdout
    status = relate_cli(["telemetry", "status"])
    assert "disabled" in status.stdout
