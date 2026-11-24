"""Load the demo seed into an empty Neo4j or Aura over Bolt: `python -m generator.load [--size small] [--dir DIR]`.

The bundled Neo4j is seeded by `neo4j-admin database import` (`import.sh`), which works on the database's own files
and so cannot reach Aura. This writes the same graph through the driver to the database the `NEO4J_*` settings name,
as the lab reads them: the same labels, relationship types, property names and value types, from the same CSVs. With
`--dir` it loads a folder the generator wrote; without it, it generates `--size` (default `small`) first.

It is not the lab, and it only fills (decision 52). Before writing anything it reads the database and goes on only
when the database is empty, or holds this seed and nothing else, so a load that stopped can be finished: no label or
relationship type outside the seed's, and no node whose key is not one of this seed's keys. Anything else is refused
and nothing is written. The lab still writes to the master graph only through a confirmed push.

Every statement merges on the key and sets properties only on create, so a batch the driver retries, or a second
run, creates nothing twice. At the end it counts each label and type against `generator.json`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import polars as pl

from generator.model import SIZES
from generator.writer import HEADERS, NODE_FILES, REL_FILES

SCHEMA = Path(__file__).resolve().parent / "schema.cypher"
# neo4j-admin import type -> the Cypher that gives the same value type from the CSV's text
CONVERT = {"string": "{}", "date": "date({})", "double": "toFloat({})", "datetime": "datetime({})"}

Read = Callable[..., list[dict]]  # (query, **params) -> rows
Write = Callable[[str, list[dict]], dict]  # (statement, rows) -> counters


class Refused(Exception):
    """The database holds something other than this seed. Nothing was written."""


@dataclass(frozen=True)
class Column:
    name: str  # the row key: the property name, or `start` / `end`
    role: str  # id, start, end, prop
    label: str | None = None  # for id, start and end
    type: str = "string"


def columns(header: str) -> list[Column]:
    """One header line, as neo4j-admin reads it: `account_id:ID(Account)`, `:START_ID(Account)`, `amount:double`."""
    out = []
    for part in header.split(","):
        name, _, kind = part.partition(":")
        if m := re.fullmatch(r"(ID|START_ID|END_ID)\((\w+)\)", kind):
            role = {"ID": "id", "START_ID": "start", "END_ID": "end"}[m.group(1)]
            out.append(Column(name if role == "id" else role, role, m.group(2)))
        elif kind in CONVERT:
            out.append(Column(name, "prop", type=kind))
        else:
            raise ValueError(f"no Cypher conversion for the import type {kind!r} in {header!r}")
    return out


def keys() -> dict[str, str]:
    """Label -> its key property, from the node files' headers."""
    return {label: next(c.name for c in columns(HEADERS[f]) if c.role == "id") for label, f in NODE_FILES.items()}


def statement(file: str) -> str:
    """The batch statement for one CSV: MERGE on the key, properties set on create only."""
    cols = columns(HEADERS[file])
    props = [c for c in cols if c.role == "prop"]
    label_of = {c.role: c.label for c in cols if c.label}
    if "id" in label_of:
        var, key = "n", next(c.name for c in cols if c.role == "id")
        head = f"UNWIND $rows AS r MERGE (n:{label_of['id']} {{{key}: r.{key}}})"
    else:
        rtype = next(t for t, f in REL_FILES.items() if f == file)
        k = keys()
        rel_key = f" {{{props[0].name}: r.{props[0].name}}}" if props else ""  # a payment's tx_id is its key
        props = props[1:] if props else props
        var = "t"
        head = (f"UNWIND $rows AS r MATCH (a:{label_of['start']} {{{k[label_of['start']]}: r.start}}) "
                f"MATCH (b:{label_of['end']} {{{k[label_of['end']]}: r.end}}) "
                f"MERGE (a)-[{var}:{rtype}{rel_key}]->(b)")
    if not props:
        return head
    sets = ", ".join(f"{var}.{p.name} = {CONVERT[p.type].format('r.' + p.name)}" for p in props)
    return f"{head} ON CREATE SET {sets}"


def rows(folder: Path, file: str) -> list[dict]:
    """The CSV's rows as text, keyed by the statement's names. Cypher converts the values."""
    names = [c.name for c in columns(HEADERS[file])]
    return pl.read_csv(folder / file, infer_schema=False, new_columns=names).to_dicts()


def batches(items: list[dict], size: int) -> Iterator[list[dict]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def schema_statements() -> list[str]:
    text = "\n".join(ln for ln in SCHEMA.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("//"))
    return [s.strip() for s in text.split(";") if s.strip()]


# ---- the guard ---------------------------------------------------------------------------------------------------
def check_target(read: Read, folder: Path) -> str:
    """'empty' or 'seed only'; raises `Refused` with what it found otherwise. Reads only."""
    if read("MATCH (n) RETURN count(n) AS n")[0]["n"] == 0:
        return "empty"
    labels, types = list(NODE_FILES), list(REL_FILES)
    found = read("MATCH (n) WHERE NOT (size(labels(n)) = 1 AND labels(n)[0] IN $labels) "
                 "RETURN count(n) AS n, collect(DISTINCT labels(n))[..5] AS labels", labels=labels)[0]
    if found["n"]:
        raise Refused(f"{found['n']} nodes with other labels, such as {found['labels']}")
    for label, key in keys().items():
        own = [r[key] for r in rows(folder, NODE_FILES[label])]
        n = read(f"MATCH (n:{label}) WHERE n.{key} IS NULL OR NOT n.{key} IN $keys RETURN count(n) AS n", keys=own)[0]["n"]
        if n:
            raise Refused(f"{n} {label} nodes whose {key} is not one of this seed's")
    found = read("MATCH ()-[r]->() WHERE NOT type(r) IN $types "
                 "RETURN count(r) AS n, collect(DISTINCT type(r))[..5] AS types", types=types)[0]
    if found["n"]:
        raise Refused(f"{found['n']} relationships of other types, such as {found['types']}")
    return "seed only"


# ---- the load ----------------------------------------------------------------------------------------------------
def load(folder: Path, read: Read, write: Write, batch: int, say: Callable[[str], None] = print) -> dict:
    """Check, then constraints, nodes, relationships, then the counts. Returns what landed and what was created."""
    state = check_target(read, folder)
    say(f"the database is {state}; loading {folder}")
    for s in schema_statements():
        write(s, [])
    created = {"nodes": 0, "relationships": 0}
    for file in [*NODE_FILES.values(), *REL_FILES.values()]:
        stmt, data = statement(file), rows(folder, file)
        made = 0
        for chunk in batches(data, batch):
            counters = write(stmt, chunk)
            made += counters.get("nodes_created", 0) + counters.get("relationships_created", 0)
            created["nodes"] += counters.get("nodes_created", 0)
            created["relationships"] += counters.get("relationships_created", 0)
        say(f"{file}: {len(data):,} rows, {made:,} created")
    want = json.loads((folder / "generator.json").read_text(encoding="utf-8"))["counts"]
    got = {label: read(f"MATCH (n:{label}) RETURN count(n) AS n")[0]["n"] for label in NODE_FILES}
    got |= {t: read(f"MATCH ()-[r:{t}]->() RETURN count(r) AS n")[0]["n"] for t in REL_FILES}
    short = {k: (v, {**want["nodes"], **want["relationships"]}[k]) for k, v in got.items()
             if v != {**want["nodes"], **want["relationships"]}[k]}
    return {"state": state, "created": created, "counts": got, "mismatch": short}


def where(uri: str, database: str) -> str:
    u = urlparse(uri)
    return f"{u.hostname}{':' + str(u.port) if u.port else ''}, database {database}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m generator.load", description=__doc__.splitlines()[0])
    p.add_argument("--size", default="small", choices=sorted(SIZES),
                   help="generate this size when --dir is not given (default: small)")
    p.add_argument("--seed", type=int, default=None, help="random seed when generating (default: LAB_SEED, 42)")
    p.add_argument("--dir", type=Path, help="load a folder the generator already wrote")
    args = p.parse_args(argv)

    import logging

    from neo4j import GraphDatabase
    from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable

    from lab.settings import get_settings

    logging.getLogger("neo4j").setLevel(logging.ERROR)  # a failure is said once, below, in one sentence

    if args.dir is not None and not (args.dir / "generator.json").is_file():
        p.error(f"{args.dir} has no generator.json: point --dir at a folder `python -m generator` wrote")
    s = get_settings()
    target = where(s.neo4j_uri, s.neo4j_database)
    with tempfile.TemporaryDirectory() as tmp:
        folder = args.dir
        if folder is None:
            from generator.model import build
            from generator.writer import write as write_files


            folder, seed = Path(tmp), s.seed if args.seed is None else args.seed
            write_files(build(args.size, seed), folder, args.size, seed)
        print(f"load: {target}", flush=True)
        driver = GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_username, s.neo4j_password))
        try:
            with driver.session(database=s.neo4j_database) as session:
                def read(query: str, **params) -> list[dict]:
                    return session.execute_read(lambda tx: tx.run(query, params).data())

                def write(stmt: str, data: list[dict]) -> dict:
                    c = session.execute_write(lambda tx: tx.run(stmt, rows=data).consume()).counters
                    return {"nodes_created": c.nodes_created, "relationships_created": c.relationships_created}

                result = load(folder, read, write, s.push_batch, say=lambda m: print(f"load: {m}", flush=True))
        except Refused as e:
            print(f"Refused: {target} holds data that is not this seed ({e}). This loader only fills an empty "
                  "database or finishes its own load. Nothing was written.", file=sys.stderr)
            return 2
        except AuthError:
            print(f"Neo4j at {target} rejected the username or password in the NEO4J_* settings.", file=sys.stderr)
            return 1
        except ServiceUnavailable:
            print(f"Could not reach Neo4j at {target}: check NEO4J_URI, and that the server is running.",
                  file=sys.stderr)
            return 1
        except Neo4jError as e:
            if e.code == "Neo.ClientError.Database.DatabaseNotFound":
                home = _home_database(driver)
                hint = f"; this server's default database is {home}, so set NEO4J_DATABASE={home}" if home else ""
                print(f"Neo4j at {target} has no database {s.neo4j_database}{hint}.", file=sys.stderr)
                return 1
            raise
        finally:
            driver.close()
    print("load: " + json.dumps(result, sort_keys=True), flush=True)
    if result["mismatch"]:
        print(f"The counts do not match generator.json: {result['mismatch']} (found, expected).", file=sys.stderr)
        return 1
    return 0


def _home_database(driver) -> str | None:
    """The database a session without a name opens: on Aura, the instance's own."""
    try:
        with driver.session() as session:
            return session.run("CALL db.info() YIELD name RETURN name").single()["name"]
    except Exception:  # noqa: BLE001 (only a hint)
        return None


if __name__ == "__main__":
    raise SystemExit(main())
