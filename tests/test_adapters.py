"""Track 1c: the adapters, on the fixture graph (200 accounts, 2,000 payments, rings R1 and R2, look-alike L1)."""
from __future__ import annotations

import itertools
import math
from datetime import UTC, datetime, timedelta

import networkx as nx
import polars as pl
import pytest
import torch
from torch_geometric.data import Data, TemporalData

import lab.adapters as adapters
from lab.adapters import PygGraph, TemporalGraph, _read, table, to_networkx, to_pyg, to_series, to_temporal
from lab.contracts.graph import GraphMapping, IdentifierLink
from lab.contracts.recipe import Recipe
from lab.runs import Run
from lab.settings import get_settings, reload_settings
from tests.fixtures.graph_loader import GRAPH
from tests.fixtures.make_fixture import L1, N_ACCOUNTS, N_PAYMENTS, R1, R2

KINDS = {"uses_device.csv": ("device", "device_id", "Device"),
         "has_email.csv": ("email", "email_id", "Email"),
         "has_address.csv": ("address", "address_id", "Address")}


# ---- the fixture, read the plain way, to check the adapters against ------------------------------------------
def tx_frame() -> pl.DataFrame:
    return pl.read_csv(GRAPH / "tx.csv", schema_overrides={"amount": pl.Float64}).with_columns(
        t=pl.col("ts").str.strptime(pl.Datetime, "%Y-%m-%dT%H:%M:%SZ").dt.epoch("s")
    )


def links_frame() -> pl.DataFrame:
    """account, identifier (as 'Label:key'), kind, one row per link in the fixture."""
    return pl.concat(
        pl.read_csv(GRAPH / name).select(
            account=pl.col("account_id"), identifier=pl.format("{}:{}", pl.lit(label), pl.col(col)), kind=pl.lit(kind)
        )
        for name, (kind, col, label) in KINDS.items()
    )


def expected_communities(shared_max: int) -> tuple[dict[frozenset, tuple[float, set]], int]:
    """The community graph worked out with loops and sets: {pair: (weight, kinds)}, and the identifiers skipped."""
    s = get_settings()
    weight = {"device": s.weight_device, "email": s.weight_email, "address": s.weight_address}
    edges: dict[frozenset, tuple[float, set]] = {}

    def add(a: str, b: str, w: float, kind: str) -> None:
        old_w, old_kinds = edges.get(frozenset((a, b)), (0.0, set()))
        edges[frozenset((a, b))] = (old_w + w, old_kinds | {kind})

    pay_pairs = {frozenset((s_, d)) for s_, d in tx_frame().select("src", "dst").iter_rows() if s_ != d}
    for pair in pay_pairs:
        a, b = sorted(pair)
        add(a, b, s.weight_payment, "payment")
    skipped = 0
    for (_, kind), grp in links_frame().group_by("identifier", "kind"):
        members = sorted(grp["account"])
        if len(members) > shared_max:
            skipped += 1
            continue
        for a, b in itertools.combinations(members, 2):
            add(a, b, weight[kind], kind)
    return edges, skipped


def epoch(dt: datetime) -> int:
    return int(dt.replace(tzinfo=UTC).timestamp())


# ---- the export surface and the facade --------------------------------------------------------------------------
def test_the_package_exports_the_five_adapters_and_the_facade_reaches_them(fixture_run: Run):
    for name in ("table", "to_pyg", "to_temporal", "to_networkx", "to_series"):
        assert callable(getattr(adapters, name))
    keys = list(R1)
    assert fixture_run.to_pyg(keys=keys).data.num_nodes == to_pyg(fixture_run, keys=keys).data.num_nodes
    assert isinstance(fixture_run.to_temporal(keys=keys), TemporalGraph)
    assert isinstance(fixture_run.to_networkx(keys=keys), nx.Graph)
    assert fixture_run.to_series(keys=keys).columns == ["unique_id", "ds", "y"]


def test_table_is_run_table(fixture_run: Run):
    q = "MATCH (a:Account) WHERE a.account_id STARTS WITH $p RETURN a.account_id AS k ORDER BY k"
    df = table(fixture_run, q, {"p": "A001"})
    assert df.equals(fixture_run.table(q, {"p": "A001"}))
    assert df["k"].to_list() == [f"A{i:04d}" for i in range(10, 20)]
    assert table(fixture_run, "MATCH (a:Account) RETURN count(a) AS n")["n"].to_list() == [N_ACCOUNTS]


# ---- to_pyg -----------------------------------------------------------------------------------------------------
def test_pyg_all_has_accounts_then_identifiers(fixture_run: Run):
    g = to_pyg(fixture_run)
    assert isinstance(g, PygGraph) and isinstance(g.data, Data)
    links = links_frame()
    n_ident = links["identifier"].n_unique()
    assert g.data.num_nodes == N_ACCOUNTS + n_ident == len(g.node_keys) == len(g.node_labels)
    assert len(g.account_index) == N_ACCOUNTS  # keys=None: the whole store
    ei = g.data.edge_index
    assert ei.dtype == torch.long and ei.dim() == 2 and ei.shape[0] == 2
    assert int(ei.min()) >= 0 and int(ei.max()) < g.data.num_nodes
    assert g.data.is_undirected()
    assert g.node_labels[:N_ACCOUNTS] == ["Account"] * N_ACCOUNTS
    assert set(g.node_labels[N_ACCOUNTS:]) == {"Device", "Email", "Address"}


def test_pyg_index_maps_agree(fixture_run: Run):
    g = to_pyg(fixture_run)
    for key, i in g.account_index.items():
        assert g.node_keys[i] == f"Account:{key}" and g.node_labels[i] == "Account"
    assert sorted(g.account_index.values()) == list(range(N_ACCOUNTS))
    assert len(set(g.node_keys)) == len(g.node_keys)  # every node has its own name
    assert g.node_keys[g.account_index["A0011"]] == "Account:A0011"
    assert "Device:D-R1" in g.node_keys and "Address:AD-R1" in g.node_keys


def test_pyg_edges_are_exactly_the_payment_pairs_and_the_identifier_links(fixture_run: Run):
    g = to_pyg(fixture_run)
    ei = g.data.edge_index
    got = {frozenset((g.node_keys[a], g.node_keys[b])) for a, b in ei.t().tolist()}
    want = {frozenset((f"Account:{a}", f"Account:{b}")) for a, b in tx_frame().select("src", "dst").iter_rows() if a != b}
    want |= {frozenset((f"Account:{a}", i)) for a, i in links_frame().select("account", "identifier").iter_rows()}
    assert got == want
    assert ei.shape[1] == 2 * len(want)  # both directions, nothing twice


def test_pyg_accounts_only(fixture_run: Run):
    g = to_pyg(fixture_run, nodes="accounts")
    assert g.data.num_nodes == N_ACCOUNTS and set(g.node_labels) == {"Account"}
    want = {frozenset((a, b)) for a, b in tx_frame().select("src", "dst").iter_rows() if a != b}
    assert g.data.edge_index.shape[1] == 2 * len(want)
    assert g.data.is_undirected()
    # the same account indices as the full graph
    assert g.account_index == to_pyg(fixture_run).account_index


def test_pyg_keys_restrict_to_the_slice_and_its_identifiers(fixture_run: Run):
    keys = [*R1, "A0001"]
    g = to_pyg(fixture_run, keys=keys)
    assert set(g.account_index) == set(keys)
    # six R1 accounts on one device and one address, each with its own email, plus A0001's three
    assert g.data.num_nodes == 7 + 1 + 1 + 6 + 3
    dev = g.node_keys.index("Device:D-R1")
    neighbours = {g.node_keys[b] for a, b in g.data.edge_index.t().tolist() if a == dev}
    assert neighbours == {f"Account:{k}" for k in R1}
    # the payment edges are the payments among the slice, and none leaves it
    names = {frozenset((g.node_keys[a], g.node_keys[b])) for a, b in g.data.edge_index.t().tolist()}
    got_pay = {p for p in names if all(n.startswith("Account:") for n in p)}
    want_pay = {frozenset((f"Account:{a}", f"Account:{b}")) for a, b in tx_frame().select("src", "dst").iter_rows()
                if a != b and a in keys and b in keys}
    assert got_pay == want_pay and want_pay


def test_pyg_unknown_keys_are_ignored_and_empty_is_empty(fixture_run: Run):
    g = to_pyg(fixture_run, keys=["nobody", "A0001"], nodes="accounts")
    assert list(g.account_index) == ["A0001"] and g.data.edge_index.shape == (2, 0)
    e = to_pyg(fixture_run, keys=[])
    assert e.data.num_nodes == 0 and e.data.edge_index.shape == (2, 0) and e.data.edge_index.dtype == torch.long
    assert e.node_keys == [] and e.account_index == {}


def test_pyg_refuses_an_unknown_node_mode(fixture_run: Run):
    with pytest.raises(ValueError, match="nodes"):
        to_pyg(fixture_run, nodes="everything")  # type: ignore[arg-type]


# ---- to_temporal ------------------------------------------------------------------------------------------------
def test_temporal_is_the_tgb_layout_sorted_by_time(fixture_run: Run):
    tg = to_temporal(fixture_run)
    d = tg.data
    assert isinstance(tg, TemporalGraph) and isinstance(d, TemporalData)
    assert d.src.dtype == d.dst.dtype == torch.long and d.t.dtype == torch.int64
    assert d.msg.dtype == torch.float32 and d.msg.shape == (N_PAYMENTS, 1)
    assert d.src.shape == d.dst.shape == d.t.shape == (N_PAYMENTS,)
    assert d.num_events == N_PAYMENTS
    assert bool((d.t[1:] >= d.t[:-1]).all())
    assert len(tg.tx_ids) == len(set(tg.tx_ids)) == N_PAYMENTS
    assert len(tg.keys) == N_ACCOUNTS and int(d.src.max()) < N_ACCOUNTS and int(d.dst.max()) < N_ACCOUNTS


def test_temporal_events_match_the_payments_and_the_pyg_indices(fixture_run: Run):
    tg = to_temporal(fixture_run)
    d = tg.data
    by_id = {r["tx_id"]: r for r in tx_frame().iter_rows(named=True)}
    for i in (0, 1, 777, 1500, N_PAYMENTS - 1):
        r = by_id[tg.tx_ids[i]]
        assert tg.keys[int(d.src[i])] == r["src"] and tg.keys[int(d.dst[i])] == r["dst"]
        assert int(d.t[i]) == r["t"]
        assert float(d.msg[i, 0]) == pytest.approx(math.log1p(r["amount"]), rel=1e-6)
    # every event, in one pass
    ev = pl.DataFrame({"tx_id": tg.tx_ids, "s": [tg.keys[i] for i in d.src.tolist()],
                       "d": [tg.keys[i] for i in d.dst.tolist()], "t": d.t.tolist()})
    chk = ev.join(tx_frame(), on="tx_id")
    assert chk.height == N_PAYMENTS and (chk["s"] == chk["src"]).all() and (chk["d"] == chk["dst"]).all()
    assert (chk["t"] == chk["t_right"]).all()
    # the same account indices as to_pyg
    g = to_pyg(fixture_run)
    assert [g.node_keys[i] for i in range(N_ACCOUNTS)] == [f"Account:{k}" for k in tg.keys]


def test_temporal_keys_keep_payments_among_the_slice(fixture_run: Run):
    keys = [*R1, *L1]
    tg = to_temporal(fixture_run, keys=keys)
    want = tx_frame().filter(pl.col("src").is_in(keys) & pl.col("dst").is_in(keys))
    assert tg.data.num_events == want.height > 0
    assert set(tg.tx_ids) == set(want["tx_id"])
    assert set(tg.keys) == set(keys)
    assert tg.data.msg.shape == (want.height, 1)


def test_temporal_empty_slice(fixture_run: Run):
    tg = to_temporal(fixture_run, keys=[])
    assert tg.data.num_events == 0 and tg.keys == [] and tg.tx_ids == []
    assert tg.data.t.dtype == torch.int64 and tg.data.msg.shape == (0, 1)


# ---- to_networkx ------------------------------------------------------------------------------------------------
def test_communities_link_the_rings_by_what_they_share(fixture_run: Run):
    s = get_settings()
    G = to_networkx(fixture_run)
    assert isinstance(G, nx.Graph) and not G.is_directed() and not G.is_multigraph()
    assert G.number_of_nodes() == N_ACCOUNTS  # every account, linked or not
    for ring, kinds, floor in ((R1, {"device", "address"}, s.weight_device + s.weight_address),
                               (L1, {"device", "address"}, s.weight_device + s.weight_address)):
        for a, b in itertools.combinations(ring, 2):
            assert G.has_edge(a, b), (a, b)
            e = G[a][b]
            assert e["weight"] >= floor and kinds <= set(e["kinds"])
            assert e["kinds"] == sorted(e["kinds"])
    # R2 share one email
    for a, b in itertools.combinations(R2, 2):
        assert G[a][b]["weight"] >= s.weight_email and "email" in G[a][b]["kinds"]
    assert G.graph["skipped_identifiers"] == 0


def test_communities_weights_are_once_per_pair_and_once_per_identifier(fixture_run: Run):
    G = to_networkx(fixture_run)
    want, skipped = expected_communities(get_settings().shared_max)
    assert skipped == 0
    assert G.number_of_edges() == len(want)
    for pair, (w, kinds) in want.items():
        a, b = sorted(pair)
        assert G[a][b]["weight"] == pytest.approx(w)
        assert G[a][b]["kinds"] == sorted(kinds)
    # a pair that shares a device and an address and never paid each other: exactly those two weights
    s = get_settings()
    assert G["A0011"]["A0014"] == {"weight": s.weight_device + s.weight_address, "kinds": ["address", "device"]}


def test_communities_skip_identifiers_shared_by_more_than_shared_max(fixture_run: Run, monkeypatch):
    monkeypatch.setenv("LAB_SHARED_MAX", "5")  # D-R1, AD-R1 (six accounts each) and E-R2 (eight) are too shared
    reload_settings()
    G = to_networkx(fixture_run)
    want, skipped = expected_communities(5)
    assert skipped == 3 and G.graph["skipped_identifiers"] == 3
    assert G.number_of_nodes() == N_ACCOUNTS and G.number_of_edges() == len(want)
    for pair, (w, kinds) in want.items():
        a, b = sorted(pair)
        assert G[a][b]["weight"] == pytest.approx(w) and G[a][b]["kinds"] == sorted(kinds)
    assert not G.has_edge("A0011", "A0014")  # the ring's device no longer links them
    assert G.has_edge("A0150", "A0153")  # the family of four is under the cap


def test_communities_keys_return_only_those_accounts(fixture_run: Run):
    keys = [*R1, "A0001", "nobody"]
    G = to_networkx(fixture_run, keys=keys)
    assert set(G.nodes) == {*R1, "A0001"}
    assert all(u in G and v in G for u, v in G.edges)
    assert "A0001" in G and G.degree["A0001"] == 0  # a node with no link in the slice
    full = to_networkx(fixture_run)
    for a, b in itertools.combinations(R1, 2):
        assert G[a][b] == full[a][b]
    assert G.graph["skipped_identifiers"] == 0
    # a slice's identifiers are counted in the slice: three members of R1 do not make D-R1 six
    three = to_networkx(fixture_run, keys=R1[:3])
    assert three.has_edge(R1[0], R1[2]) and three.graph["skipped_identifiers"] == 0


def test_communities_empty_slice(fixture_run: Run):
    G = to_networkx(fixture_run, keys=[])
    assert G.number_of_nodes() == 0 and G.graph["skipped_identifiers"] == 0


def test_raw_is_the_property_graph_from_the_cursor(fixture_run: Run):
    G = to_networkx(fixture_run, how="raw")
    assert isinstance(G, nx.MultiDiGraph)
    types = [d["type"] for _, _, d in G.edges(data=True)]
    assert types.count("TX") == N_PAYMENTS
    assert {t for t in types} == {"TX", "USES_DEVICE", "HAS_EMAIL", "HAS_ADDRESS"}
    assert len(types) == N_PAYMENTS + 3 * N_ACCOUNTS
    labels = [tuple(d["labels"]) for _, d in G.nodes(data=True)]
    assert labels.count(("Account",)) == N_ACCOUNTS
    assert G.number_of_nodes() == N_ACCOUNTS + links_frame()["identifier"].n_unique()
    tx = next(d for _, _, d in G.edges(data=True) if d["type"] == "TX")
    assert {"tx_id", "amount", "ts"} <= set(tx)
    acc = next(d for _, d in G.nodes(data=True) if d["labels"] == ["Account"])
    assert "account_id" in acc


def test_raw_keys_restrict_payments_to_the_slice(fixture_run: Run):
    keys = [*R1, *L1]
    G = to_networkx(fixture_run, keys=keys, how="raw")
    want = tx_frame().filter(pl.col("src").is_in(keys) & pl.col("dst").is_in(keys)).height
    tx = [d for _, _, d in G.edges(data=True) if d["type"] == "TX"]
    assert len(tx) == want > 0
    accounts = {d["account_id"] for _, d in G.nodes(data=True) if d["labels"] == ["Account"]}
    assert accounts == set(keys)
    assert {d["tx_id"] for d in tx} == set(tx_frame().filter(pl.col("src").is_in(keys) & pl.col("dst").is_in(keys))["tx_id"])


def test_networkx_refuses_an_unknown_form(fixture_run: Run):
    with pytest.raises(ValueError, match="how"):
        to_networkx(fixture_run, how="pretty")  # type: ignore[arg-type]


# ---- to_series --------------------------------------------------------------------------------------------------
def test_series_is_nixtla_long_format_and_regular(fixture_run: Run):
    df = to_series(fixture_run)
    assert df.columns == ["unique_id", "ds", "y"]
    assert df.schema["unique_id"] == pl.Utf8 and df.schema["y"] == pl.Float64
    assert isinstance(df.schema["ds"], pl.Datetime) and df.schema["ds"].time_zone is None
    tx = tx_frame()
    assert df["y"].sum() == pytest.approx(tx["amount"].sum())
    days = tx["t"].min() // 86400, tx["t"].max() // 86400
    n_days = days[1] - days[0] + 1
    per_id = df.group_by("unique_id").agg(n=pl.len(), lo=pl.col("ds").min(), hi=pl.col("ds").max(),
                                          steps=pl.col("ds").diff().drop_nulls().unique())
    assert per_id["n"].unique().to_list() == [n_days]
    assert per_id["lo"].n_unique() == 1 and per_id["hi"].n_unique() == 1
    assert all(s.to_list() == [timedelta(days=1)] for s in per_id["steps"])
    assert df.select(pl.struct("unique_id", "ds").n_unique()).item() == df.height  # no duplicate period
    assert set(df["unique_id"]) == set(tx["src"])  # accounts that paid out
    assert (df["y"] >= 0).all() and (df["y"] == 0).any()  # zero-filled


def test_series_values_are_the_days_outflow(fixture_run: Run):
    df = to_series(fixture_run)
    tx = tx_frame().with_columns(day=pl.from_epoch("t", "s").dt.truncate("1d"))
    want = tx.group_by("src", "day").agg(y=pl.col("amount").sum())
    got = df.filter(pl.col("y") > 0).join(want, left_on=["unique_id", "ds"], right_on=["src", "day"])
    assert got.height == want.height == df.filter(pl.col("y") > 0).height
    assert (got["y"] - got["y_right"]).abs().max() < 1e-6


def test_series_keys_are_the_slice_outflow_wherever_it_went(fixture_run: Run):
    keys = [*R1, *R2]
    df = to_series(fixture_run, keys=keys)
    assert set(df["unique_id"]) <= set(keys)
    want = tx_frame().filter(pl.col("src").is_in(keys))
    assert df["y"].sum() == pytest.approx(want["amount"].sum())
    assert df.group_by("unique_id").len()["len"].n_unique() == 1


def test_series_freq_changes_the_period_not_the_total(fixture_run: Run):
    weekly = to_series(fixture_run, freq="1w")
    assert weekly["y"].sum() == pytest.approx(tx_frame()["amount"].sum())
    assert (weekly["ds"].dt.weekday() == 1).all()  # Polars truncates a week to its Monday
    steps = weekly.select(pl.col("ds").diff().over("unique_id").drop_nulls().unique()).to_series().to_list()
    assert steps == [timedelta(days=7)]
    assert weekly.height < to_series(fixture_run).height


def test_series_empty_slice(fixture_run: Run):
    df = to_series(fixture_run, keys=[])
    assert df.height == 0 and df.columns == ["unique_id", "ds", "y"]
    assert df.schema["y"] == pl.Float64 and df.schema["unique_id"] == pl.Utf8


# ---- a long key list goes to Relate in batches, and the answer is the same ----------------------------------------
def test_a_slice_longer_than_one_batch_reads_the_same(fixture_run: Run, monkeypatch):
    # sixty accounts with the rings among them, one key twice, and a batch of seven: nine statements per read
    keys = [*R1, *L1, *R2, *[f"A{i:04d}" for i in range(20, 60)], R1[0], "nobody"]
    one = (to_pyg(fixture_run, keys=keys), to_temporal(fixture_run, keys=keys), to_networkx(fixture_run, keys=keys),
           to_series(fixture_run, keys=keys), to_networkx(fixture_run, keys=keys, how="raw"))
    monkeypatch.setenv("LAB_KEY_BATCH", "7")
    reload_settings()
    many = (to_pyg(fixture_run, keys=keys), to_temporal(fixture_run, keys=keys), to_networkx(fixture_run, keys=keys),
            to_series(fixture_run, keys=keys), to_networkx(fixture_run, keys=keys, how="raw"))
    assert one[0].node_keys == many[0].node_keys and one[0].data.edge_index.tolist() == many[0].data.edge_index.tolist()
    assert one[1].tx_ids == many[1].tx_ids and one[1].data.t.tolist() == many[1].data.t.tolist()
    assert one[2].graph == many[2].graph and nx.utils.graphs_equal(one[2], many[2])
    assert one[3].equals(many[3])
    assert sorted(one[4].nodes) == sorted(many[4].nodes) and one[4].number_of_edges() == many[4].number_of_edges()
    assert one[0].data.num_nodes > 60 and one[1].data.num_events > 0 and one[2].number_of_edges() > 0


def test_key_batches(data_dir):
    size = get_settings().key_batch
    assert list(_read.key_batches(None)) == [None]
    assert list(_read.key_batches([])) == []
    assert [len(b) for b in _read.key_batches([str(i) for i in range(size * 2 + 1)])] == [size, size, 1]
    assert list(_read.key_batches(["a", "b", "a"])) == [["a", "b"]]


# ---- the adapters read the recipe's mapping, not the generator's names ------------------------------------------
@pytest.fixture
def mapped_run(data_dir) -> Run:
    """A five-customer store that shares none of the generator's labels, types or property names."""
    mapping = GraphMapping(
        account_label="Customer", account_key="cid", account_opened=None,
        payment_type="PAID", payment_key="pid", payment_amount="value", payment_time="at",
        identifiers=[
            IdentifierLink(rel_type="USES_LAPTOP", label="Laptop", key="serial", kind="device"),
            IdentifierLink(rel_type="LIVES_AT", label="Home", key="hid", kind="address"),
            IdentifierLink(rel_type="OWNS_PHONE", label="Phone", key="number", kind="phone"),
        ],
    )
    run = Run.create(Recipe(graph=mapping), data_dir=data_dir)
    c = run.store()
    c.execute("UNWIND ['c1','c2','c3','c4','c5'] AS k CREATE (:Customer {cid: k})")
    c.execute("CREATE (:Laptop {serial: 'L1'}), (:Home {hid: 'H1'}), (:Phone {number: 'P1'})")
    for cid, rel, label, key, val in [("c1", "USES_LAPTOP", "Laptop", "serial", "L1"), ("c2", "USES_LAPTOP", "Laptop", "serial", "L1"),
                                       ("c1", "LIVES_AT", "Home", "hid", "H1"), ("c2", "LIVES_AT", "Home", "hid", "H1"),
                                       ("c2", "OWNS_PHONE", "Phone", "number", "P1"), ("c3", "OWNS_PHONE", "Phone", "number", "P1")]:
        c.execute(f"MATCH (a:Customer {{cid: $a}}), (x:{label} {{{key}: $v}}) CREATE (a)-[:{rel}]->(x)", {"a": cid, "v": val})
    for pid, a, b, value, at in [("p1", "c1", "c2", 10.0, "2026-01-01T00:00:00Z"), ("p2", "c2", "c1", 5.0, "2026-01-02T12:00:00Z"),
                                 ("p3", "c3", "c4", None, "2026-01-03T00:00:00Z"), ("p4", "c4", "c5", 7.0, None),
                                 ("p5", "c1", "c3", 100.0, "2026-01-05T23:59:00Z")]:
        c.execute(
            "MATCH (a:Customer {cid: $a}), (b:Customer {cid: $b}) "
            "CREATE (a)-[:PAID {pid: $pid, value: $value, at: CASE WHEN $at IS NULL THEN null ELSE datetime($at) END}]->(b)",
            {"a": a, "b": b, "pid": pid, "value": value, "at": at},
        )
    yield run
    run.close()


def test_mapped_pyg(mapped_run: Run):
    g = to_pyg(mapped_run)
    assert g.node_keys == ["Customer:c1", "Customer:c2", "Customer:c3", "Customer:c4", "Customer:c5",
                           "Home:H1", "Laptop:L1", "Phone:P1"]
    assert g.node_labels[:5] == ["Customer"] * 5 and g.account_index["c3"] == 2
    names = {frozenset((g.node_keys[a], g.node_keys[b])) for a, b in g.data.edge_index.t().tolist()}
    assert frozenset(("Customer:c1", "Laptop:L1")) in names and frozenset(("Customer:c3", "Phone:P1")) in names
    assert frozenset(("Customer:c4", "Customer:c5")) in names  # a payment with no time is still a link in a graph


def test_mapped_temporal_leaves_out_what_it_cannot_place(mapped_run: Run):
    tg = to_temporal(mapped_run)
    assert tg.tx_ids == ["p1", "p2", "p3", "p5"]  # p4 has no time
    assert tg.keys == ["c1", "c2", "c3", "c4", "c5"]
    assert tg.data.t.tolist() == [epoch(datetime(2026, 1, 1)), epoch(datetime(2026, 1, 2, 12)),
                                  epoch(datetime(2026, 1, 3)), epoch(datetime(2026, 1, 5, 23, 59))]
    assert tg.data.msg[:, 0].tolist() == pytest.approx([math.log1p(10.0), math.log1p(5.0), 0.0, math.log1p(100.0)])
    assert tg.data.src.tolist() == [0, 1, 2, 0] and tg.data.dst.tolist() == [1, 0, 3, 2]


def test_mapped_communities_and_every_kind_weighted(mapped_run: Run):
    s = get_settings()
    G = to_networkx(mapped_run)
    assert set(G.nodes) == {"c1", "c2", "c3", "c4", "c5"}
    assert G["c1"]["c2"] == {"weight": s.weight_device + s.weight_address + s.weight_payment,
                             "kinds": ["address", "device", "payment"]}
    assert G["c1"]["c3"]["kinds"] == ["payment"] and G["c1"]["c3"]["weight"] == s.weight_payment
    assert G["c4"]["c5"]["weight"] == s.weight_payment
    assert G["c2"]["c3"] == {"weight": s.weight_phone, "kinds": ["phone"]}  # LAB_WEIGHT_PHONE
    assert "unweighted_kinds" not in G.graph and G.graph["skipped_identifiers"] == 0


def test_mapped_raw_and_series(mapped_run: Run):
    G = to_networkx(mapped_run, how="raw")
    assert sorted(d["type"] for _, _, d in G.edges(data=True)).count("PAID") == 5
    assert G.number_of_nodes() == 8
    df = to_series(mapped_run)
    assert df["unique_id"].unique().sort().to_list() == ["c1", "c2", "c3"]  # c4's only payment has no time
    assert df.group_by("unique_id").len()["len"].to_list() == [5, 5, 5]  # 1 to 5 January
    assert df["y"].sum() == pytest.approx(115.0)  # c3's missing amount is zero
    c1 = df.filter(pl.col("unique_id") == "c1").sort("ds")["y"].to_list()
    assert c1 == [10.0, 0.0, 0.0, 0.0, 100.0]


def test_mapped_keys_are_the_customers_own(mapped_run: Run):
    assert to_pyg(mapped_run, keys=["c1", "c2"], nodes="accounts").account_index == {"c1": 0, "c2": 1}
    assert set(to_networkx(mapped_run, keys=["c1", "c2"]).nodes) == {"c1", "c2"}
    assert to_series(mapped_run, keys=["c2"])["unique_id"].unique().to_list() == ["c2"]
