"""The way in: the recipe's broad slice from the master graph into the run's fresh store (page 01, "Pull").

`pull(run)` runs `relate pull` over the HTTP Query API. If that endpoint cannot be reached it falls back to
parallel Bolt reads, loaded into the same empty store, and records the fallback with its reason on the manifest.
Either way it then makes sure the account key is unique in the run store and creates the search indexes.

Nothing here writes to the master graph. Both routes only read.
"""
from __future__ import annotations

import queue
import re
import shutil
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timezone
from typing import TYPE_CHECKING, Any

from lab.common.log import get_logger
from lab.common.neo4j import neo4j_driver
from lab.common.relate_cli import RelateError, credentials_file, plaintext_flag, relate_cli
from lab.common.store import open_store, rows
from lab.contracts.manifest import Route
from lab.roundtrip.indexes import INDEX_NAMES, create_indexes, ensure_account_constraint, unique_constraint
from lab.roundtrip.loader import LoadStats, load_rows
from lab.roundtrip.queries import (
    KEY_HI,
    KEY_LO,
    PullStatements,
    account_keys_statement,
    ident,
    pull_statements,
    schema_statements,
    selected_links,
)
from lab.settings import Settings, get_settings

if TYPE_CHECKING:
    from lab.runs import Run

log = get_logger("roundtrip.pull")

MAX_WORKERS = 4  # parallel Bolt readers, one per disjoint account-key range

SUMMARY = re.compile(
    r"pulled (?P<nodes>\d+) new / (?P<reused_nodes>\d+) reused nodes, "
    r"(?P<rels>\d+) new / (?P<reused_rels>\d+) reused rels "
    r"\(\+ (?P<constraints>\d+) constraints, (?P<indexes>\d+) indexes; (?P<skipped>\d+) schema skipped\)"
)


class PullError(RuntimeError):
    pass


class StoreNotEmpty(PullError):
    """`relate pull` writes into an empty database only, so every run gets a fresh run store."""


@dataclass
class PullCounts:
    """What a pull put in the store. `nodes` and `relationships` are new ones; reused ones were returned twice."""

    nodes: int = 0
    relationships: int = 0
    constraints: int = 0
    reused_nodes: int = 0
    reused_relationships: int = 0
    indexes: int = 0  # Relate's own count of the indexes it copied; the lab's search indexes come after
    schema_skipped: int = 0


@dataclass
class PullResult:
    route: Route
    nodes: int
    relationships: int
    constraints: int
    seconds: float  # the route that delivered the data, not the failed attempt before a fallback
    reused_nodes: int = 0
    reused_relationships: int = 0
    indexes: list[str] = field(default_factory=list)  # the search indexes now in the run store
    reason: str | None = None  # why the fallback ran, when it did


def parse_summary(text: str) -> PullCounts:
    """`pulled N new / M reused nodes, X new / Y reused rels (+ C constraints, I indexes; S schema skipped) to PATH`."""
    m = SUMMARY.search(text)
    if not m:
        raise PullError(f"could not read relate pull's summary line from: {text.strip()[:300]!r}")
    return PullCounts(
        nodes=int(m["nodes"]), reused_nodes=int(m["reused_nodes"]),
        relationships=int(m["rels"]), reused_relationships=int(m["reused_rels"]),
        constraints=int(m["constraints"]), indexes=int(m["indexes"]), schema_skipped=int(m["skipped"]),
    )


def unreachable_reason(err: RelateError) -> str | None:
    """Why the Query API counts as unreachable, or None when the failure is not about reaching it.

    Unreachable: the request never got an answer (connection refused, DNS failure, timeout, TLS failure), or the
    endpoint answered 5xx, or answered 404 without a Neo4j error document (no Query API at that address). A
    Cypher error, a bad credential, or a Neo4j error such as an unknown database would fail the same way over
    Bolt, so those are never a reason to fall back.
    """
    line = err.result.error_line().removeprefix("Error:").strip()
    line = re.sub(r"(://)[^/@\s]*@", r"\1", line)  # a URL with credentials in it must not reach the manifest
    if re.search(r"HTTP request to \S+ failed", line):
        return f"Query API unreachable: {line[:300]}"
    m = re.match(r"HTTP (\d{3})\b", line)
    if m:
        code = int(m.group(1))
        if code >= 500 or (code == 404 and "Neo." not in line):
            return f"Query API answered HTTP {code}: {line[:300]}"
    return None


# ---- the pull ------------------------------------------------------------------------------------------------
def pull(run: Run) -> PullResult:
    """Pull the recipe's broad slice into `run.paths.store`, then create its indexes.

    Refuses a store that already holds nodes. A failed pull leaves no store behind, so it can be run again. If only
    the index step fails, the pulled store stays and the `indexes` step is marked failed. The run store's writable
    connection (`run.store()`) is left open for the steps that follow.
    """
    settings = get_settings()
    mapping, spec = run.recipe.graph, run.recipe.pull
    stmts = pull_statements(mapping, spec)  # a bad recipe fails here, before anything is touched
    _require_empty(run)

    manifest = run.manifest
    manifest.start("pull")
    run.save()
    run.close()  # `relate pull` is the store's one writer while it runs
    try:
        counts, route, reason, seconds = _fetch(run, stmts, settings)
        conn = run.store()
        if ensure_account_constraint(conn, mapping):
            counts.constraints += 1
    except Exception as e:
        manifest.fail("pull", f"{type(e).__name__}: {e}"[:500])
        run.save()
        _reset_store(run)
        raise
    if route is Route.RELATE:
        manifest.routes["pull"] = Route.RELATE
    manifest.finish(
        "pull",
        {"nodes": counts.nodes, "relationships": counts.relationships, "constraints": counts.constraints,
         "reused_nodes": counts.reused_nodes, "reused_relationships": counts.reused_relationships},
        note=f"route {route.value}, {seconds:.1f}s",
    )
    run.save()
    log.info("pulled %d nodes and %d relationships by %s in %.1fs", counts.nodes, counts.relationships,
             route.value, seconds)

    manifest.start("indexes")
    run.save()
    try:
        created = create_indexes(conn, settings, mapping)
    except Exception as e:
        manifest.fail("indexes", f"{type(e).__name__}: {e}"[:500])
        run.save()
        raise
    manifest.finish("indexes", {"indexes": len(INDEX_NAMES), "created": len(created)})
    run.save()

    return PullResult(
        route=route, nodes=counts.nodes, relationships=counts.relationships, constraints=counts.constraints,
        seconds=seconds, reused_nodes=counts.reused_nodes, reused_relationships=counts.reused_relationships,
        indexes=list(INDEX_NAMES), reason=reason,
    )


def _fetch(run: Run, stmts: PullStatements, settings: Settings) -> tuple[PullCounts, Route, str | None, float]:
    started = time.perf_counter()
    try:
        counts = _relate_pull(run, stmts, settings)
        return counts, Route.RELATE, None, time.perf_counter() - started
    except RelateError as e:
        reason = unreachable_reason(e)
        if reason is None:
            raise
    log.warning("falling back to Bolt reads: %s", reason)
    _reset_store(run)
    run.manifest.fallback("pull", Route.BOLT, reason)
    run.save()
    started = time.perf_counter()
    counts = _bolt_pull(run, stmts, settings)
    return counts, Route.BOLT, reason, time.perf_counter() - started


def _relate_pull(run: Run, stmts: PullStatements, settings: Settings) -> PullCounts:
    store = run.paths.store.resolve()
    with credentials_file(settings) as creds:
        result = relate_cli(
            ["pull", "--from", str(creds), "--query", stmts.query, "--database", str(store),
             *stmts.cli_params(), *plaintext_flag(settings)],
            settings=settings,
        )
    try:
        return parse_summary(result.stdout)
    except PullError as e:
        log.warning("%s; counting the store instead", e)
        conn = run.store()
        return PullCounts(
            nodes=rows(conn, "MATCH (n) RETURN count(n) AS n")[0]["n"],
            relationships=rows(conn, "MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"],
            constraints=len(rows(conn, "SHOW CONSTRAINTS")),
        )


def _require_empty(run: Run) -> None:
    path = run.paths.store
    if not path.exists() or not any(path.iterdir()):
        return
    run.close()
    try:
        conn = open_store(path, read_only=True)
        try:
            n = rows(conn, "MATCH (n) RETURN count(n) AS n")[0]["n"]
        finally:
            conn.close()
    except Exception as e:
        raise PullError(f"{path} exists and is not an empty Relate store: {e}") from e
    if n:
        raise StoreNotEmpty(
            f"the run store {path} already holds {n} nodes; relate pull writes into an empty database only, "
            "so start a new run"
        )


def _reset_store(run: Run) -> None:
    """Remove what a failed pull left. `_require_empty` showed the store empty first, so it is all ours."""
    run.close()
    shutil.rmtree(run.paths.store, ignore_errors=True)
    if run.paths.store.exists():
        log.warning("could not remove the half-pulled store %s", run.paths.store)


# ---- the Bolt fallback ---------------------------------------------------------------------------------------
def to_store_value(value: Any) -> Any:
    """A value read over Bolt, as a parameter Relate stores with its type kept (dates stay dates)."""
    import relate
    from neo4j import time as nt
    from neo4j.spatial import Point

    if isinstance(value, nt.DateTime):
        native = value.to_native()
        if native.tzinfo is not None:  # a plain offset, whatever zone object the driver built
            native = native.replace(tzinfo=timezone(native.utcoffset()))
        return native
    if isinstance(value, nt.Date | nt.Time):
        return value.to_native()
    if isinstance(value, nt.Duration):
        return relate.Duration(months=value.months, days=value.days, seconds=value.seconds,
                               nanoseconds=value.nanoseconds)
    if isinstance(value, Point):
        return relate.Point(value.srid, *value)
    if isinstance(value, list | tuple):
        return [to_store_value(v) for v in value]
    return value


@dataclass
class _Batch:
    """Graph elements read from one stretch of records, keyed by the mapping's keys."""

    nodes: dict[tuple, tuple[tuple[str, ...], dict]] = field(default_factory=dict)  # (label, key) -> (labels, props)
    rels: list[tuple[str, tuple, tuple, dict]] = field(default_factory=list)  # (type, start key, end key, props)

    def __len__(self) -> int:
        return len(self.nodes) + len(self.rels)


class _Stopped(Exception):
    """Another reader failed; this one stops."""


_DONE = object()


def _keyed_labels(run: Run) -> dict[str, str]:
    """label -> key property, for the labels a pull can join on: the account, then identifiers."""
    m, spec = run.recipe.graph, run.recipe.pull
    links = selected_links(m, spec) if spec.mode == "schema" else m.identifiers
    keyed = {m.account_label: m.account_key}
    for link in links:
        keyed.setdefault(link.label, link.key)
    return keyed


def _node_key(node, keyed: dict[str, str]) -> tuple:
    for label, prop in keyed.items():
        if label in node.labels:
            value = node.get(prop)
            if value is None:
                raise ValueError(f"a {label} node has no {prop}, which the graph mapping names as its key")
            return label, to_store_value(value)
    raise ValueError(
        f"the Bolt fallback joins nodes on the keys in the graph mapping ({sorted(keyed)}); a node labelled "
        f"{sorted(node.labels)} has none of them"
    )


def _walk(value, nodes: list, rels: list) -> None:
    from neo4j.graph import Node, Path, Relationship

    if value is None:
        return
    if isinstance(value, Node):
        nodes.append(value)
    elif isinstance(value, Relationship):
        rels.append(value)
    elif isinstance(value, Path):
        nodes.extend(value.nodes)
        rels.extend(value.relationships)
    elif isinstance(value, list | tuple):
        for v in value:
            _walk(v, nodes, rels)
    else:
        raise ValueError(f"every pull statement must RETURN graph elements; got a {type(value).__name__}")


def _collect(record, keyed: dict[str, str], eid_key: dict[str, tuple], batch: _Batch, seen_rels: set | None) -> None:
    nodes: list = []
    rels: list = []
    for value in record.values():
        _walk(value, nodes, rels)
    for node in nodes:
        key = _node_key(node, keyed)
        eid_key[node.element_id] = key
        if key not in batch.nodes:
            batch.nodes[key] = (tuple(sorted(node.labels)), {k: to_store_value(v) for k, v in node.items()})
    for rel in rels:
        if seen_rels is not None:
            if rel.element_id in seen_rels:
                continue
            seen_rels.add(rel.element_id)
        start, end = eid_key.get(rel.start_node.element_id), eid_key.get(rel.end_node.element_id)
        if start is None or end is None:
            raise ValueError(
                f"a {rel.type} relationship came back without both its end nodes; the Bolt fallback needs "
                "them returned beside it (RETURN a, r, b)"
            )
        batch.rels.append((rel.type, start, end, {k: to_store_value(v) for k, v in rel.items()}))


def _put(out: queue.Queue, item: object, stop: threading.Event) -> None:
    while True:
        if stop.is_set():
            raise _Stopped
        try:
            out.put(item, timeout=0.2)
            return
        except queue.Full:
            continue


def _read_job(driver, database: str, statements: tuple[str, ...], params: dict, keyed: dict[str, str],
              chunk: int, single_stream: bool, out: queue.Queue, stop: threading.Event) -> None:
    """One reader: run its statements, send what they return to the loader in chunks of records.

    A schema reader gets both end nodes of every relationship in the same record, and ranges never overlap, so it
    remembers nothing between records. A cypher reader is a single stream whose statements can return the same
    relationship twice, or a relationship before its end nodes, so it remembers both.
    """
    eid_key: dict[str, tuple] = {}
    seen_rels: set | None = set() if single_stream else None
    try:
        with driver.session(database=database) as session:
            for statement in statements:
                batch, records = _Batch(), 0
                for record in session.run(statement, params):
                    if stop.is_set():
                        raise _Stopped
                    if not single_stream:
                        eid_key.clear()
                    _collect(record, keyed, eid_key, batch, seen_rels)
                    records += 1
                    if records >= chunk:
                        _put(out, batch, stop)
                        batch, records = _Batch(), 0
                if len(batch):
                    _put(out, batch, stop)
        _put(out, _DONE, stop)
    except _Stopped:
        return
    except Exception as e:  # handed to the loader thread, which raises it
        try:
            _put(out, e, stop)
        except _Stopped:
            pass


def _node_statement(labels: tuple[str, ...]) -> str:
    return "UNWIND $rows AS r CREATE (n:" + ":".join(ident(label) for label in labels) + ") SET n = r.props"


def _rel_statement(rel_type: str, start: tuple[str, str], end: tuple[str, str]) -> str:
    return (
        f"UNWIND $rows AS r MATCH (s:{ident(start[0])} {{{ident(start[1])}: r.s}}) "
        f"MATCH (e:{ident(end[0])} {{{ident(end[1])}: r.e}}) "
        f"CREATE (s)-[t:{ident(rel_type)}]->(e) SET t = r.props"
    )


class _Writer:
    """The one writer. It holds what the readers send until `flush_at` rows are waiting, then loads the new nodes
    and after them the relationships, through `load_rows`. A relationship's end nodes are always in the same
    batch or an earlier one, so they are in the store by the time its group loads.
    """

    def __init__(self, conn, keyed: dict[str, str], flush_at: int):
        self.conn, self.keyed, self.flush_at, self.size = conn, keyed, flush_at, flush_at
        self.seen: set[tuple] = set()
        self.relationships = 0
        self.stats = LoadStats(size=flush_at)
        self._nodes: dict[tuple[str, ...], list[dict]] = defaultdict(list)
        self._rels: dict[tuple, list[dict]] = defaultdict(list)
        self._waiting = 0

    def add(self, batch: _Batch) -> None:
        for key, (labels, props) in batch.nodes.items():
            if key not in self.seen:  # a node comes back from every reader that touches it
                self.seen.add(key)
                self._nodes[labels].append({"props": props})
                self._waiting += 1
        for rel_type, start, end, props in batch.rels:
            self._rels[(rel_type, start[0], end[0])].append({"s": start[1], "e": end[1], "props": props})
            self._waiting += 1
        if self._waiting >= self.flush_at:
            self.flush()

    def flush(self) -> None:
        for labels, data in self._nodes.items():
            self._load(_node_statement(labels), data)
        for (rel_type, start_label, end_label), data in self._rels.items():
            statement = _rel_statement(
                rel_type, (start_label, self.keyed[start_label]), (end_label, self.keyed[end_label])
            )
            self._load(statement, data)
            self.relationships += len(data)
        self._nodes.clear()
        self._rels.clear()
        self._waiting = 0

    def _load(self, statement: str, data: list[dict]) -> None:
        stats = load_rows(self.conn, statement, data, start=self.size)
        self.size = stats.size
        self.stats.add(stats)


def _bolt_pull(run: Run, stmts: PullStatements, settings: Settings) -> PullCounts:
    """Read the same slice over Bolt and load it into the empty run store.

    Schema mode takes the account keys first, splits the sorted list into up to MAX_WORKERS disjoint ranges and
    reads each range on its own session. A cypher pull cannot be split, so one reader runs its statements.
    """
    m, spec = run.recipe.graph, run.recipe.pull
    keyed = _keyed_labels(run)
    conn = run.store()
    for label, key in keyed.items():
        conn.execute(unique_constraint(label, key))  # first, so the relationship lookups below use the index
    chunk = max(1, settings.load_start_rows)

    driver = neo4j_driver(settings)
    try:
        if spec.mode == "schema":
            with driver.session(database=settings.neo4j_database) as session:
                keys = [r["k"] for r in session.run(account_keys_statement(m))]
            ranged = schema_statements(m, spec, key_range=True)
            jobs = [(ranged.statements, {**ranged.params, KEY_LO: lo, KEY_HI: hi}) for lo, hi in _key_ranges(keys)]
            single_stream = False  # each relationship has one source account, so ranges never overlap
        else:
            jobs = [(stmts.statements, dict(stmts.params))]
            single_stream = True
        writer = _Writer(conn, keyed, chunk)
        _run_jobs(driver, settings.neo4j_database, jobs, keyed, chunk, single_stream, writer)
    finally:
        driver.close()
    log.info("bolt load: %d rows in %d commits, largest %d, %d refused", writer.stats.rows, writer.stats.batches,
             writer.stats.largest_batch, writer.stats.refusals)
    return PullCounts(nodes=len(writer.seen), relationships=writer.relationships, constraints=len(keyed))


def _key_ranges(keys: list) -> list[tuple]:
    """Split the sorted account keys into up to MAX_WORKERS contiguous ranges, as (first, last) pairs."""
    if not keys:
        return []
    workers = min(MAX_WORKERS, len(keys))
    size = -(-len(keys) // workers)
    return [(part[0], part[-1]) for part in (keys[i : i + size] for i in range(0, len(keys), size))]


def _run_jobs(driver, database: str, jobs: list, keyed: dict[str, str], chunk: int, single_stream: bool,
              writer: _Writer) -> None:
    if not jobs:
        return
    out: queue.Queue = queue.Queue(maxsize=len(jobs))  # a reader waits when the loader is behind
    stop = threading.Event()
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        for statements, params in jobs:
            pool.submit(_read_job, driver, database, statements, params, keyed, chunk, single_stream, out, stop)
        done = 0
        try:
            while done < len(jobs):
                item = out.get()
                if item is _DONE:
                    done += 1
                elif isinstance(item, Exception):
                    raise item
                else:
                    writer.add(item)
            writer.flush()
        finally:
            stop.set()
