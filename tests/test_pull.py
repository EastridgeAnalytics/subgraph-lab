"""Track 1b: the pull. Unit tests run on the fixture graph with a stand-in for `relate pull`; the live tests run the
real thing against the local Neo4j, which `live_neo4j` loads with the same fixture graph."""
from __future__ import annotations

import shutil
from pathlib import Path

import polars as pl
import pytest

import lab.roundtrip.pull as pull_mod
from lab.common.neo4j import master_rows
from lab.common.relate_cli import CliResult, RelateError
from lab.common.store import open_store, rows
from lab.contracts.graph import GraphMapping, IdentifierLink
from lab.contracts.manifest import Route, StepStatus
from lab.contracts.recipe import PullSpec, Recipe
from lab.roundtrip.indexes import INDEX_NAMES
from lab.roundtrip.pull import PullCounts, PullError, StoreNotEmpty, parse_summary, pull, unreachable_reason
from lab.roundtrip.queries import (
    check_params,
    cypher_statements,
    param_args,
    pull_statements,
    schema_statements,
    split_statements,
)
from lab.runs import Run
from lab.settings import reload_settings
from tests.fixtures.graph_loader import GRAPH

SUMMARY_LINE = (
    "pulled 777 new / 470 reused nodes, 2600 new / 0 reused rels (+ 4 constraints, 0 indexes; 0 schema skipped) "
    "to C:/Users/eastr/AppData/Local/Temp/pe/s1\n"
)
SINCE, UNTIL = "2026-10-05T00:00:00Z", "2026-10-08T00:00:00Z"
UNREACHABLE = "http://localhost:1"  # nothing listens here: connection refused


# ---- helpers -------------------------------------------------------------------------------------------------
def csv(name: str) -> pl.DataFrame:
    return pl.read_csv(GRAPH / name, infer_schema=False)


def expected(since: str | None = None, until: str | None = None) -> dict[str, int]:
    """What a schema pull of that window should hold, worked out from the fixture CSVs alone."""
    tx = csv("tx.csv")
    if since:
        tx = tx.filter(pl.col("ts") >= since)
    if until:
        tx = tx.filter(pl.col("ts") < until)
    accounts = set(tx["src"]) | set(tx["dst"])
    out = {"accounts": len(accounts), "payments": tx.height, "links": 0, "identifiers": 0}
    for name, col in (("uses_device.csv", "device_id"), ("has_email.csv", "email_id"), ("has_address.csv", "address_id")):
        links = csv(name).filter(pl.col("account_id").is_in(list(accounts)))
        out["links"] += links.height
        out["identifiers"] += links[col].n_unique()
    return out


def store_counts(conn) -> dict[str, int]:
    nodes = {r["l"]: r["n"] for r in rows(conn, "MATCH (n) RETURN labels(n)[0] AS l, count(n) AS n")}
    return {
        "accounts": nodes.get("Account", 0),
        "payments": rows(conn, "MATCH ()-[t:TX]->() RETURN count(t) AS n")[0]["n"],
        "links": rows(conn, "MATCH (:Account)-[r]->(x) WHERE type(r) <> 'TX' RETURN count(r) AS n")[0]["n"],
        "identifiers": sum(n for label, n in nodes.items() if label != "Account"),
    }


def reloaded(run: Run):
    """The manifest as it was written to disk."""
    return Run(run.id, run.paths.root.parent.parent).manifest


@pytest.fixture
def make_run(data_dir):
    made: list[Run] = []

    def make(recipe: Recipe | None = None) -> Run:
        run = Run.create(recipe or Recipe(), data_dir=data_dir)
        made.append(run)
        return run

    yield make
    for run in made:
        run.close()


@pytest.fixture
def use_env(data_dir):
    """Set environment variables and reload the settings; both are put back before the test's data_dir goes."""
    mp = pytest.MonkeyPatch()

    def use(**env: str) -> None:
        for name, value in env.items():
            mp.setenv(name, value)
        reload_settings()

    yield use
    mp.undo()
    reload_settings()


@pytest.fixture
def fake_relate(monkeypatch, fixture_store_path):
    """Stands in for `relate pull`: records the call, then copies the fixture store to --database."""
    calls: list[list[str]] = []

    def install(stdout: str = SUMMARY_LINE, error: str | None = None, leave: bool = False):
        def fake(args, **kw):
            calls.append(list(args))
            dest = Path(args[args.index("--database") + 1])
            if error:
                if leave:  # what a failed relate pull leaves: a directory with an empty database in it
                    open_store(dest).close()
                raise RelateError(args, CliResult(1, "", error + "\n"))
            shutil.copytree(fixture_store_path, dest, dirs_exist_ok=True)
            return CliResult(0, stdout, "")

        monkeypatch.setattr(pull_mod, "relate_cli", fake)
        return calls

    return install


# ---- the query builder ---------------------------------------------------------------------------------------
def test_schema_statements_without_a_window():
    s = schema_statements(GraphMapping(), PullSpec())
    assert s.params == {}
    pay, links = s.statements
    assert pay == "MATCH (a:Account)-[t:TX]->(b:Account)\nRETURN a, t, b"
    assert "RETURN a, r, x" in links
    assert "r:USES_DEVICE|HAS_EMAIL|HAS_ADDRESS" in links
    assert "EXISTS { MATCH (a)-[t:TX]-(:Account) }" in links
    assert "$since" not in s.query and "$until" not in s.query
    assert s.query.count(";") == 1  # a `;`-separated list, as relate pull runs it


def test_schema_statements_with_since_and_until():
    s = schema_statements(GraphMapping(), PullSpec(since=SINCE, until=UNTIL))
    assert s.params == {"since": SINCE, "until": UNTIL}
    for statement in s.statements:
        assert "t.ts >= datetime($since) AND t.ts < datetime($until)" in statement
    only_since = schema_statements(GraphMapping(), PullSpec(since=SINCE))
    assert only_since.params == {"since": SINCE} and "$until" not in only_since.query
    only_until = schema_statements(GraphMapping(), PullSpec(until=UNTIL))
    assert only_until.params == {"until": UNTIL} and "$since" not in only_until.query


def test_since_must_be_iso():
    with pytest.raises(ValueError, match="ISO-8601"):
        schema_statements(GraphMapping(), PullSpec(since="last tuesday"))


def test_names_come_from_the_mapping_and_are_quoted_when_needed():
    mapping = GraphMapping(
        account_label="Customer", account_key="cust id", payment_type="PAID", payment_time="paid at",
        identifiers=[IdentifierLink(rel_type="ON", label="Phone Number", key="n", kind="phone")],
    )
    s = schema_statements(mapping, PullSpec(since=SINCE))
    pay, links = s.statements
    assert pay.startswith("MATCH (a:Customer)-[t:PAID]->(b:Customer)")
    assert "t.`paid at` >= datetime($since)" in pay
    assert "(a:Customer)-[r:ON]->(x:`Phone Number`)" in links
    assert "Account" not in s.query and "TX" not in s.query


def test_labels_and_types_narrow_the_pull():
    m = GraphMapping()
    only_device = schema_statements(m, PullSpec(labels=["Account", "Device"], types=["TX", "USES_DEVICE"]))
    assert "(a:Account)-[r:USES_DEVICE]->(x:Device)" in only_device.statements[1]
    assert "HAS_EMAIL" not in only_device.query
    no_links = schema_statements(m, PullSpec(types=["TX"]))
    assert len(no_links.statements) == 1
    no_email = schema_statements(m, PullSpec(labels=["Account", "Device", "Address"]))
    assert "HAS_EMAIL" not in no_email.query and "HAS_ADDRESS" in no_email.query
    assert "(type(r) = 'USES_DEVICE' AND x:Device)" in no_email.statements[1]


@pytest.mark.parametrize("spec,message", [
    (PullSpec(labels=["Account", "Merchant"]), "does not know"),
    (PullSpec(types=["TX", "SENT"]), "does not know"),
    (PullSpec(labels=["Device"]), "must include the account label"),
    (PullSpec(types=["USES_DEVICE"]), "must include the payment type"),
])
def test_filters_that_cannot_work_are_refused(spec, message):
    with pytest.raises(ValueError, match=message):
        schema_statements(GraphMapping(), spec)


def test_key_range_statements_take_the_range_on_both():
    s = schema_statements(GraphMapping(), PullSpec(since=SINCE), key_range=True)
    for statement in s.statements:
        assert "a.account_id >= $key_lo AND a.account_id <= $key_hi" in statement
    assert "key_lo" not in schema_statements(GraphMapping(), PullSpec()).query


def test_cypher_statements_pass_the_query_through_with_scalar_params():
    spec = PullSpec(mode="cypher", query="MATCH (a:Account)-[t]->(b) WHERE a.account_id < $x RETURN a, t, b",
                    params={"x": "A0020", "n": 5, "f": 1.5, "ok": True, "none": None})
    s = cypher_statements(spec)
    assert s.query == spec.query and s.statements == (spec.query,)
    assert param_args(s.params) == [
        "--param", 'x="A0020"', "--param", "n=5", "--param", "f=1.5", "--param", "ok=true", "--param", "none=null",
    ]
    assert pull_statements(GraphMapping(), spec) == s


def test_strings_that_look_like_numbers_stay_strings():
    assert param_args({"k": "123"}) == ["--param", 'k="123"']


@pytest.mark.parametrize("params", [{"ids": ["A1", "A2"]}, {"m": {"a": 1}}, {"x": float("nan")}, {"bad name": 1}])
def test_non_scalar_params_are_refused_with_a_clear_error(params):
    with pytest.raises(ValueError, match="scalars only|JSON cannot carry|plain identifier"):
        check_params(params)
    with pytest.raises(ValueError):
        cypher_statements(PullSpec(mode="cypher", query="MATCH (n) RETURN n", params=params))


def test_split_statements_leaves_semicolons_in_strings_and_comments():
    text = "MATCH (a {x: 'a;b'}) RETURN a; // done; really\nMATCH (b:`we;ird`) /* ; */ RETURN b;"
    assert split_statements(text) == ["MATCH (a {x: 'a;b'}) RETURN a", "MATCH (b:`we;ird`)  RETURN b"]


def test_statements_find_what_the_csvs_say_on_the_fixture_store(fixture_store_path, tmp_path):
    """The statements are Neo4j's to run, but the fixture store speaks the same Cypher: check their meaning."""
    shutil.copytree(fixture_store_path, tmp_path / "s")
    conn = open_store(tmp_path / "s")
    try:
        for since, until in ((None, None), (SINCE, UNTIL), (SINCE, None), (None, UNTIL)):
            s = schema_statements(GraphMapping(), PullSpec(since=since, until=until))
            want = expected(since, until)
            payments = rows(conn, s.statements[0], s.params)
            links = rows(conn, s.statements[1], s.params)
            assert len(payments) == want["payments"], (since, until)
            assert len({r["a"].id for r in links}) <= want["accounts"]
            assert len({r["x"].id for r in links}) == want["identifiers"], (since, until)
    finally:
        conn.close()


# ---- relate pull's summary line, and when the Query API counts as unreachable ---------------------------------
def test_parse_summary():
    assert parse_summary(SUMMARY_LINE) == PullCounts(
        nodes=777, reused_nodes=470, relationships=2600, reused_relationships=0, constraints=4, indexes=0,
        schema_skipped=0,
    )
    again = parse_summary("warning: x\npulled 5 new / 1 reused nodes, 3 new / 2 reused rels (+ 0 constraints, 2 indexes; 1 schema skipped) to p")
    assert (again.nodes, again.reused_relationships, again.indexes, again.schema_skipped) == (5, 2, 2, 1)


def test_parse_summary_names_what_it_could_not_read():
    with pytest.raises(PullError, match="summary line"):
        parse_summary("done\n")


def err(line: str) -> RelateError:
    return RelateError(["pull"], CliResult(1, "", line + "\n"))


@pytest.mark.parametrize("line", [
    "Error: HTTP request to http://localhost:1/db/neo4j/query/v2 failed: io: No connection could be made because "
    "the target machine actively refused it. (os error 10061)",
    "Error: HTTP request to http://nope.invalid:7474/db/neo4j/query/v2 failed: io: No such host is known. (os error 11001)",
    "Error: HTTP request to https://x.databases.neo4j.io/db/neo4j/query/v2 failed: io: timed out",
    "Error: HTTP request to https://localhost:27474/db/neo4j/query/v2 failed: io: received corrupt message of type InvalidContentType",
    "Error: HTTP 503: Service Unavailable",
    "Error: HTTP 502: Bad Gateway",
    "Error: HTTP 404: Not Found",
])
def test_unreachable_query_api_falls_back(line):
    assert unreachable_reason(err(line)).startswith("Query API")


@pytest.mark.parametrize("line", [
    "Error: shape query parse error: parse error: expected RightParen, got \"RETURN\"",
    "Error: shape query RETURN must be graph elements (nodes/rels/paths), not projections/scalars/aggregates",
    'Error: HTTP 401: {"errors":[{"code":"Neo.ClientError.Security.Unauthorized","message":"Invalid credential."}]}',
    'Error: HTTP 400: {"errors":[{"code":"Neo.ClientError.Statement.SyntaxError","message":"Invalid input"}]}',
    'Error: HTTP 404: {"errors":[{"code":"Neo.ClientError.Database.DatabaseNotFound","message":"Graph not found: x"}]}',
    "Error: destination database is not empty; pull writes into an empty database only",
    "",
])
def test_cypher_and_credential_errors_never_fall_back(line):
    assert unreachable_reason(err(line)) is None


def test_the_recorded_reason_carries_no_credentials():
    reason = unreachable_reason(err("Error: HTTP request to http://neo4j:s3cret@host:7474/db/x failed: io: refused"))
    assert "s3cret" not in reason and "neo4j:" not in reason
    assert reason == "Query API unreachable: HTTP request to http://host:7474/db/x failed: io: refused"


def test_key_ranges_are_disjoint_cover_every_key_and_number_at_most_four():
    keys = [f"A{i:03d}" for i in range(10)]
    ranges = pull_mod._key_ranges(keys)
    assert len(ranges) == pull_mod.MAX_WORKERS == 4
    covered = [k for lo, hi in ranges for k in keys if lo <= k <= hi]
    assert covered == keys  # every key in exactly one range
    assert pull_mod._key_ranges(keys[:3]) == [("A000", "A000"), ("A001", "A001"), ("A002", "A002")]
    assert pull_mod._key_ranges(keys[:1]) == [("A000", "A000")] and pull_mod._key_ranges([]) == []


def test_bolt_values_keep_their_types_in_the_store(tmp_path):
    import datetime as dt

    import pytz
    from neo4j import time as nt
    from neo4j.spatial import CartesianPoint

    paris = pytz.timezone("Europe/Paris")
    values = {
        "date": nt.Date(2026, 1, 2),
        "utc": nt.DateTime(2026, 1, 2, 3, 4, 5, 0, tzinfo=dt.UTC),
        "paris": nt.DateTime.from_native(paris.localize(dt.datetime(2026, 1, 2, 3, 4, 5))),
        "local": nt.DateTime(2026, 1, 2, 3, 4, 5),
        "duration": nt.Duration(months=1, days=2, seconds=3),
        "point": CartesianPoint((1.0, 2.0)),
        "dates": [nt.Date(2026, 1, 2), nt.Date(2026, 2, 3)],
    }
    conn = open_store(tmp_path / "s")
    try:
        for name, value in values.items():
            conn.execute("CREATE (:T {name: $n, v: $v})", {"n": name, "v": pull_mod.to_store_value(value)})
        got = {r["n"]: (r["ty"], r["v"]) for r in rows(
            conn, "MATCH (t:T) RETURN t.name AS n, valueType(t.v) AS ty, t.v AS v")}
    finally:
        conn.close()
    assert {k: v[0] for k, v in got.items()} == {
        "date": "DATE NOT NULL", "utc": "ZONED DATETIME NOT NULL", "paris": "ZONED DATETIME NOT NULL",
        "local": "LOCAL DATETIME NOT NULL", "duration": "DURATION NOT NULL", "point": "POINT NOT NULL",
        "dates": "LIST<DATE NOT NULL> NOT NULL",
    }
    assert got["paris"][1].utcoffset().total_seconds() == 3600  # the offset in force on that date, not the zone
    assert got["date"][1] == dt.date(2026, 1, 2)


# ---- pull() ---------------------------------------------------------------------------------------------------
def test_pull_refuses_a_store_that_holds_nodes(fixture_run, fake_relate):
    calls = fake_relate()
    with pytest.raises(StoreNotEmpty, match="start a new run"):
        pull(fixture_run)
    assert calls == []  # relate was never asked
    assert fixture_run.manifest.step("pull").status is StepStatus.PENDING
    assert store_counts(fixture_run.store())["accounts"] == 200  # untouched


def test_pull_accepts_the_empty_database_a_failed_pull_leaves(make_run, fake_relate):
    run = make_run()
    open_store(run.paths.store).close()
    assert any(run.paths.store.iterdir())
    fake_relate()
    assert pull(run).route is Route.RELATE


def test_pull_by_relate(make_run, fake_relate):
    run = make_run(Recipe(pull=PullSpec(since=SINCE, until=UNTIL)))
    calls = fake_relate()
    result = pull(run)

    assert result.route is Route.RELATE and result.reason is None
    assert (result.nodes, result.relationships, result.constraints) == (777, 2600, 4)
    assert result.reused_nodes == 470 and result.seconds >= 0
    assert result.indexes == list(INDEX_NAMES)

    (args,) = calls
    assert args[0] == "pull" and args[args.index("--from") + 1].endswith(".env")
    assert args[args.index("--database") + 1] == str(run.paths.store.resolve())
    assert args[args.index("--query") + 1] == schema_statements(run.recipe.graph, run.recipe.pull).query
    assert [args[i + 1] for i, a in enumerate(args) if a == "--param"] == [f'since="{SINCE}"', f'until="{UNTIL}"']
    assert "--allow-plaintext" not in args  # the local Neo4j is loopback

    saved = reloaded(run)  # what was written to disk
    assert saved.routes["pull"] is Route.RELATE and saved.fallbacks == []
    assert saved.step("pull").status is StepStatus.DONE
    assert saved.step("pull").counts["nodes"] == 777 and saved.step("pull").counts["relationships"] == 2600
    assert saved.step("indexes").status is StepStatus.DONE and saved.step("indexes").counts["indexes"] == 4
    names = {r["name"] for r in rows(run.store(), "SHOW INDEXES")}
    assert set(INDEX_NAMES) <= names


def test_plaintext_is_asked_for_only_on_a_non_loopback_http_query_api(make_run, fake_relate, use_env):
    calls = fake_relate()
    pull(make_run())
    assert "--allow-plaintext" not in calls[0]  # the local Neo4j is loopback

    use_env(NEO4J_URI="bolt://neo4j:7687")  # derives http://neo4j:7474, which is not loopback
    pull(make_run())
    assert "--allow-plaintext" in calls[1]


def test_pull_counts_the_store_when_the_summary_line_is_not_there(make_run, fake_relate):
    run = make_run()
    fake_relate(stdout="done\n")
    result = pull(run)
    assert result.nodes == 777 and result.relationships == 2600  # the fixture graph


def test_pull_falls_back_to_bolt_when_the_query_api_is_unreachable(make_run, fake_relate, monkeypatch,
                                                                    fixture_store_path):
    run = make_run()
    calls = fake_relate(error="Error: HTTP request to http://localhost:1/db/neo4j/query/v2 failed: io: refused",
                        leave=True)

    def fake_bolt(r, stmts, settings):
        assert not r.paths.store.exists(), "the failed pull's leftovers must be gone before Bolt loads"
        shutil.copytree(fixture_store_path, r.paths.store, dirs_exist_ok=True)
        return PullCounts(nodes=777, relationships=2600, constraints=4)

    monkeypatch.setattr(pull_mod, "_bolt_pull", fake_bolt)
    result = pull(run)

    assert len(calls) == 1
    assert result.route is Route.BOLT and "Query API unreachable" in result.reason
    saved = reloaded(run)
    assert saved.routes["pull"] is Route.BOLT
    (fallback,) = saved.fallbacks
    assert fallback.step == "pull" and fallback.route is Route.BOLT and "refused" in fallback.reason
    assert saved.step("pull").status is StepStatus.DONE and saved.step("indexes").status is StepStatus.DONE


def test_a_cypher_error_is_raised_not_fallen_back_from(make_run, fake_relate, monkeypatch):
    run = make_run(Recipe(pull=PullSpec(mode="cypher", query="MATCH (a RETURN a")))
    fake_relate(error="Error: shape query parse error: expected RightParen", leave=True)
    monkeypatch.setattr(pull_mod, "_bolt_pull", lambda *a: pytest.fail("a Cypher error must not fall back to Bolt"))
    with pytest.raises(RelateError, match="parse error"):
        pull(run)
    manifest = reloaded(run)
    assert manifest.step("pull").status is StepStatus.FAILED and "parse error" in manifest.step("pull").note
    assert manifest.fallbacks == [] and "pull" not in manifest.routes
    assert not run.paths.store.exists()  # nothing left behind, so the pull can be run again


def test_a_bad_recipe_fails_before_anything_is_touched(make_run, fake_relate):
    run = make_run(Recipe(pull=PullSpec(labels=["Merchant"])))
    calls = fake_relate()
    with pytest.raises(ValueError, match="does not know"):
        pull(run)
    assert calls == [] and run.manifest.step("pull").status is StepStatus.PENDING


def test_a_failed_pull_can_be_run_again(make_run, fake_relate):
    run = make_run()
    fake_relate(error="Error: HTTP 401: unauthorized", leave=True)
    with pytest.raises(RelateError):
        pull(run)
    fake_relate()
    assert pull(run).route is Route.RELATE


# ---- live: the real Query API and Bolt, against the local Neo4j ------------------------------------------------
def master_counts() -> dict[str, int]:
    labels = {r["l"]: r["n"] for r in master_rows("MATCH (n) RETURN labels(n)[0] AS l, count(n) AS n")}
    return {
        "accounts": labels["Account"],
        "payments": master_rows("MATCH ()-[t:TX]->() RETURN count(t) AS n")[0]["n"],
        "links": master_rows("MATCH (:Account)-[r]->(x) WHERE type(r) <> 'TX' RETURN count(r) AS n")[0]["n"],
        "identifiers": sum(n for label, n in labels.items() if label != "Account"),
    }


def graph_tables(conn) -> dict[str, list]:
    """Everything a pull should carry, as sorted rows, so two routes can be compared value for value."""
    return {
        "accounts": rows(conn, "MATCH (a:Account) RETURN a.account_id AS id, a.opened AS opened ORDER BY id"),
        "payments": rows(conn, "MATCH (a:Account)-[t:TX]->(b:Account) RETURN t.tx_id AS id, a.account_id AS src, "
                               "b.account_id AS dst, t.amount AS amount, t.ts AS ts ORDER BY id"),
        "links": rows(conn, "MATCH (a:Account)-[r]->(x) WHERE type(r) <> 'TX' RETURN a.account_id AS a, "
                            "type(r) AS type, labels(x)[0] AS label, "
                            "coalesce(x.device_id, x.email_id, x.address_id) AS x ORDER BY a, type, x"),
        "constraints": sorted(
            (r["labelsOrTypes"][0], r["properties"][0]) for r in rows(conn, "SHOW CONSTRAINTS")
        ),
    }


@pytest.mark.live
def test_live_schema_pull_by_relate(live_neo4j, make_run):
    run = make_run()
    result = pull(run)
    want = master_counts()
    assert want["accounts"] == 200 and want["payments"] == 2000

    assert result.route is Route.RELATE and result.reason is None
    assert store_counts(run.store()) == want
    assert result.nodes == want["accounts"] + want["identifiers"]
    assert result.relationships == want["payments"] + want["links"]
    assert result.constraints >= 1
    assert {r["name"] for r in rows(run.store(), "SHOW INDEXES") if r["type"] in ("VECTOR", "FULLTEXT")} == set(
        INDEX_NAMES
    )
    assert run.manifest.routes["pull"] is Route.RELATE and run.manifest.fallbacks == []
    assert rows(run.store(), "MATCH ()-[t:TX]->() RETURN valueType(t.ts) AS ty LIMIT 1")[0]["ty"].startswith("ZONED")


@pytest.mark.live
def test_live_forced_fallback_gives_the_same_graph(live_neo4j, make_run, use_env):
    by_relate = make_run()
    pull(by_relate)
    want = graph_tables(by_relate.store())

    use_env(NEO4J_QUERY_URI=UNREACHABLE)
    by_bolt = make_run()
    result = pull(by_bolt)

    assert result.route is Route.BOLT
    assert "unreachable" in result.reason and "http://localhost:1" in result.reason
    assert by_bolt.manifest.routes["pull"] is Route.BOLT
    (fallback,) = by_bolt.manifest.fallbacks
    assert fallback.route is Route.BOLT and fallback.reason == result.reason
    assert (result.nodes, result.relationships) == (
        by_relate.manifest.step("pull").counts["nodes"], by_relate.manifest.step("pull").counts["relationships"],
    )
    assert store_counts(by_bolt.store()) == master_counts()
    assert graph_tables(by_bolt.store()) == want  # values and types: dates, datetimes, amounts, keys
    assert {r["name"] for r in rows(by_bolt.store(), "SHOW INDEXES")} >= set(INDEX_NAMES)
    assert by_bolt.manifest.step("indexes").status is StepStatus.DONE


@pytest.mark.live
def test_live_window_on_both_routes(live_neo4j, make_run, use_env):
    recipe = Recipe(pull=PullSpec(since=SINCE, until=UNTIL))
    want = expected(SINCE, UNTIL)
    assert 0 < want["payments"] < 2000

    by_relate = make_run(recipe)
    pull(by_relate)
    assert store_counts(by_relate.store()) == want

    use_env(NEO4J_QUERY_URI=UNREACHABLE)
    by_bolt = make_run(recipe)
    assert pull(by_bolt).route is Route.BOLT
    assert store_counts(by_bolt.store()) == want
    assert graph_tables(by_bolt.store()) == graph_tables(by_relate.store())


@pytest.mark.live
def test_live_fallback_in_small_chunks_loads_every_row_once(live_neo4j, make_run, use_env):
    """With 150 rows per commit the readers' chunks, the shared nodes and the loader's batches all cross
    boundaries, and the graph still comes out the same."""
    by_relate = make_run()
    pull(by_relate)

    use_env(NEO4J_QUERY_URI=UNREACHABLE, LAB_LOAD_START_ROWS="150")
    by_bolt = make_run()
    assert pull(by_bolt).route is Route.BOLT
    assert graph_tables(by_bolt.store()) == graph_tables(by_relate.store())
    assert store_counts(by_bolt.store()) == master_counts()


CYPHER = (
    "MATCH (a:Account)-[t:TX]->(b:Account) WHERE a.account_id < $x RETURN a, t, b; "
    "MATCH (a:Account)-[r:USES_DEVICE]->(d:Device) WHERE a.account_id < $x RETURN a, r, d"
)


@pytest.mark.live
def test_live_cypher_pull_on_both_routes(live_neo4j, make_run, use_env):
    recipe = Recipe(pull=PullSpec(mode="cypher", query=CYPHER, params={"x": "A0020"}))
    by_relate = make_run(recipe)
    result = pull(by_relate)
    assert result.route is Route.RELATE

    mine = master_rows("MATCH (a:Account) WHERE a.account_id < 'A0020' RETURN count(a) AS n")[0]["n"]
    got = store_counts(by_relate.store())
    assert 0 < mine < 200
    assert got["accounts"] >= mine and got["payments"] == master_rows(
        "MATCH (a:Account)-[t:TX]->(:Account) WHERE a.account_id < 'A0020' RETURN count(t) AS n"
    )[0]["n"]

    use_env(NEO4J_QUERY_URI=UNREACHABLE)
    by_bolt = make_run(recipe)
    assert pull(by_bolt).route is Route.BOLT
    assert graph_tables(by_bolt.store()) == graph_tables(by_relate.store())


@pytest.mark.live
def test_live_cypher_error_is_not_a_reason_to_fall_back(live_neo4j, make_run):
    run = make_run(Recipe(pull=PullSpec(mode="cypher", query="MATCH (a:Account RETURN a")))
    with pytest.raises(RelateError, match="parse error"):
        pull(run)
    assert run.manifest.fallbacks == [] and run.manifest.step("pull").status is StepStatus.FAILED
    assert not run.paths.store.exists()


@pytest.mark.live
def test_live_pull_into_a_pulled_store_is_refused(live_neo4j, make_run):
    run = make_run()
    pull(run)
    with pytest.raises(StoreNotEmpty):
        pull(run)
    assert store_counts(run.store())["accounts"] == 200
