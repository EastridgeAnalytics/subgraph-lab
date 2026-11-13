"""The estimate (page 02): what a slice will hold, shown before anything runs.

Search mode sizes the search slice inside a run store whose first pass is done (decision 31). Schema and Cypher
modes size the pull, read from the master graph over Bolt in read-only transactions: nothing here can write to it,
and a query that tries is refused by the server.

`estimate_pull` mirrors the statements `lab.roundtrip.queries` builds for the pull: the payments between accounts in
the window, then the identifier links of the accounts that have one. If that file changes shape, change this one.
"""
from __future__ import annotations

from collections import defaultdict

from lab.common.log import get_logger
from lab.common.neo4j import neo4j_driver
from lab.common.store import estimate_memory
from lab.contracts.api import SliceEstimate, SliceRequest
from lab.contracts.graph import GraphMapping
from lab.contracts.manifest import RunManifest, StepStatus
from lab.contracts.recipe import PullSpec
from lab.roundtrip.queries import SINCE, UNTIL, cypher_statements, ident, schema_statements, selected_links
from lab.runs import Run
from lab.settings import get_settings
from lab.slice.search import search_slice

log = get_logger("slice.estimate")


def estimate(request: SliceRequest) -> SliceEstimate:
    """The estimate for a slice request. Search goes to the named run, or the newest run whose first pass is done;
    with none it says to pull a schema slice first (decision 31). Schema and Cypher go to the master graph.
    """
    search = request.search or request.recipe.search
    if search is not None:
        run = _search_run(request.run_id)
        if run is None:
            settings = get_settings()
            return SliceEstimate(
                mode="search", hops=settings.search_hops if search.hops is None else search.hops, nodes=0,
                relationships=0, memory_bytes=0, over_cap=False, cap=settings.slice_cap,
                note="No run has had its first pass yet: pull a schema slice first, then search it (decision 31)",
            )
        return search_slice(run, search)
    return estimate_pull(request.pull or request.recipe.pull, request.recipe.graph)


def _search_run(run_id: str | None) -> Run | None:
    """The run named, or the newest whose first pass is done, or None."""
    from lab.common.store import list_runs, run_paths

    if run_id:
        return Run(run_id)
    for rid in list_runs():  # newest first
        manifest = RunManifest.load(run_paths(rid).manifest)
        if manifest.step("first_pass").status is StepStatus.DONE:
            return Run(rid)
    return None


# ---- the pull -------------------------------------------------------------------------------------------------
def estimate_pull(spec: PullSpec, mapping: GraphMapping) -> SliceEstimate:
    """What the pull would bring, counted on the master graph. Read only."""
    if spec.mode == "cypher":
        nodes, rels, note = _cypher_counts(spec)
    else:
        nodes, rels, note = _schema_counts(spec, mapping)
    cap = get_settings().slice_cap
    over = nodes > cap
    if over:
        note += f". More than the cap of {cap:,} nodes: narrow the window or the labels"
    return SliceEstimate(
        mode=spec.mode, nodes=nodes, relationships=rels, memory_bytes=estimate_memory(nodes, rels),
        over_cap=over, cap=cap, note=note,
    )


def _master_read(work):
    """`work(session)` on the master graph in one read session. Write queries fail on the server."""
    settings = get_settings()
    driver = neo4j_driver(settings)
    try:
        with driver.session(database=settings.neo4j_database) as session:
            return work(session)
    finally:
        driver.close()


def _rows(session, query: str, params: dict) -> list[dict]:
    return session.execute_read(lambda tx: tx.run(query, params).data())


def _schema_counts(spec: PullSpec, mapping: GraphMapping) -> tuple[int, int, str]:
    links = selected_links(mapping, spec)  # also rejects labels and types the mapping does not know
    params = dict(schema_statements(mapping, spec).params)  # the validated since and until
    account, pay, ts = ident(mapping.account_label), ident(mapping.payment_type), ident(mapping.payment_time)
    window = []
    if spec.since:
        window.append(f"t.{ts} >= datetime(${SINCE})")
    if spec.until:
        window.append(f"t.{ts} < datetime(${UNTIL})")
    where = ("WHERE " + " AND ".join(window)) if window else ""

    accounts_q = (
        f"CALL () {{ MATCH (a:{account})-[t:{pay}]->(:{account}) {where} RETURN DISTINCT a AS x "
        f"UNION MATCH (:{account})-[t:{pay}]->(a:{account}) {where} RETURN DISTINCT a AS x }} "
        "RETURN count(x) AS n"
    )
    payments_q = f"MATCH (:{account})-[t:{pay}]->(:{account}) {where} RETURN count(t) AS n"
    by_label: dict[str, list[str]] = defaultdict(list)
    for link in links:
        by_label[link.label].append(ident(link.rel_type))
    links_q = {
        label: (
            f"MATCH (a:{account})-[r:{'|'.join(types)}]->(x:{ident(label)}) "
            f"WHERE EXISTS {{ MATCH (a)-[t:{pay}]-(:{account}) {where} }} "
            "RETURN count(r) AS links, count(DISTINCT x) AS identifiers"
        )
        for label, types in by_label.items()
    }

    def work(session):
        accounts = _rows(session, accounts_q, params)[0]["n"]
        payments = _rows(session, payments_q, params)[0]["n"]
        found = {label: _rows(session, q, params)[0] for label, q in links_q.items()}
        return accounts, payments, found

    accounts_n, payments_n, found = _master_read(work)
    identifiers_n = sum(r["identifiers"] for r in found.values())
    links_n = sum(r["links"] for r in found.values())
    window_text = " and ".join(
        text for text in (f"from {spec.since}" if spec.since else "", f"before {spec.until}" if spec.until else "") if text
    )
    note = (f"counted on the master graph: {accounts_n:,} accounts, {payments_n:,} payments"
            f"{' ' + window_text if window_text else ''}, {identifiers_n:,} identifier nodes, {links_n:,} "
            "identifier links")
    return accounts_n + identifiers_n, payments_n + links_n, note


def _cypher_counts(spec: PullSpec) -> tuple[int, int, str]:
    """The pull's own query, wrapped to count: its rows, and the distinct nodes and relationships in them.

    `relate pull` takes graph elements only, so a column that holds anything else counts for nothing. Paths are not
    unpacked.
    """
    stmts = cypher_statements(spec)
    params = stmts.params

    def work(session):
        columns = [
            session.execute_read(lambda tx, s=s: list(tx.run(f"CALL () {{ {s} }} RETURN * LIMIT 0", params).keys()))
            for s in stmts.statements
        ]
        rows = sum(
            _rows(session, f"CALL () {{ {s} }} RETURN count(*) AS n", params)[0]["n"] for s in stmts.statements
        )
        branches = [
            f"CALL () {{ {s} }} UNWIND [{', '.join(ident(c) for c in cols)}] AS e RETURN e"
            for s, cols in zip(stmts.statements, columns, strict=True)
            if cols
        ]
        if not branches:
            return rows, 0, 0
        counted = _rows(
            session,
            "CALL () { " + " UNION ALL ".join(branches) + " } WITH DISTINCT e "
            "RETURN sum(CASE WHEN e IS :: NODE THEN 1 ELSE 0 END) AS nodes, "
            "sum(CASE WHEN e IS :: RELATIONSHIP THEN 1 ELSE 0 END) AS rels",
            params,
        )[0]
        return rows, counted["nodes"] or 0, counted["rels"] or 0

    try:
        rows, nodes, rels = _master_read(work)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"the pull query was not counted: it could not run as a read-only query on the master graph ({e})") from e
    note = (f"counted by running your query read-only on the master graph: {rows:,} rows, holding {nodes:,} "
            f"distinct nodes and {rels:,} distinct relationships")
    return nodes, rels, note
