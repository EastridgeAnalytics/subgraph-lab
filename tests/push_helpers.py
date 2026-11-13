"""Helpers for the stage, push, delta and retract tests (track 1e): stand-ins for `relate push` and for the master
graph's Bolt session, and the snapshots the live tests compare. Not a test module."""
from __future__ import annotations

import json
import math
from contextlib import contextmanager
from types import SimpleNamespace

import polars as pl

from lab.common.neo4j import neo4j_session
from lab.common.relate_cli import CliResult
from lab.common.store import open_store, rows
from lab.contracts.graph import ALERT_ON, IN_COMMUNITY
from lab.roundtrip.push import OWN_CONSTRAINTS

FIXTURE_LABELS = ("Account", "Device", "Email", "Address")
FIXTURE_TYPES = ("TX", "USES_DEVICE", "HAS_EMAIL", "HAS_ADDRESS")
LAB_LABELS = ("Alert", "RiskCommunity", "LabRun")
LAB_TYPES = (ALERT_ON, IN_COMMUNITY)
HTTP_400 = '{"errors":[{"code":"Neo.ClientError.Request.Invalid","message":"Bad Request"}]}'
REFUSED = "HTTP request to http://localhost:1/db/neo4j/query/v2 failed: io: connection refused (os error 10061)"


# ---- frames -----------------------------------------------------------------------------------------------------
def account_frame(keys: list[str], score: float = 0.9, dim: int = 3) -> pl.DataFrame:
    return pl.DataFrame({
        "account_id": keys,
        "risk_score": [score] * len(keys),
        "risk_score_model": [score / 2] * len(keys),
        "risk_factors": ['[{"id":"shared_device_5plus","score":0.9,"weight":0.3}]'] * len(keys),
        "risk_embedding": [[round(0.1 * (i + 1), 3)] * dim for i in range(len(keys))],
    })


def alert_frame(pairs: list[tuple[str, str]], score: float = 0.8) -> pl.DataFrame:
    """(alert_key, account_key) pairs."""
    return pl.DataFrame({
        "alert_key": [a for a, _ in pairs], "account_key": [k for _, k in pairs], "window": ["2026-10-05"] * len(pairs),
        "score": [score] * len(pairs), "evidence": ['{"members":1}'] * len(pairs),
    })


def community_frame(key: str, members: list[str], score: float = 0.7) -> pl.DataFrame:
    return pl.DataFrame({
        "community_key": [key], "member_keys": [members], "score": [score], "size": [len(members)],
        "score_parts": ['{"shared":0.5}'], "embedding": [[0.5, 0.5, 0.5]],
    })


# ---- a stand-in for `relate push` ---------------------------------------------------------------------------------
class FakeRelate:
    """`relate push` as the lab sees it: arguments in, a `CliResult` out, in the shape wave 1 recorded.

    It reads the real push store (the lab has closed its writer by then, as it must for the real binary) and prints a
    report with one entity per node label and relationship type, `batches` as the planned count, and a `(dangling)` entity
    for relationships a `--labels` push leaves out. `fail_at` names the entity whose first batch fails with HTTP 400, as
    a zoned datetime does: entities before it land, it and the rest do not. `unreachable` makes every real push fail
    before it sends anything, as a refused connection does.
    """

    def __init__(self, fail_at: str | None = None, unreachable: bool = False):
        self.fail_at, self.unreachable = fail_at, unreachable
        self.calls: list[list[str]] = []

    @property
    def real_pushes(self) -> list[list[str]]:
        return [c for c in self.calls if "--dry-run" not in c]

    @property
    def dry_runs(self) -> list[list[str]]:
        return [c for c in self.calls if "--dry-run" in c]

    def __call__(self, args, *, check=True, timeout=None, settings=None) -> CliResult:
        self.calls.append(list(args))
        dry, as_json = "--dry-run" in args, "--json" in args
        batch = int(args[args.index("--batch-size") + 1])
        entities = self._entities(args)
        planned = lambda e: math.ceil(e["total"] / batch) if e["total"] else 0  # noqa: E731
        failing = None if dry else self.fail_at
        err = ""
        landed = not dry and not self.unreachable
        for e in entities:
            if e["entity"] == "(dangling)":
                continue
            if self.unreachable and not dry:
                if not err:
                    err = f"Error: push failed: pre-read of {e['entity']} failed: {REFUSED}"
                e["batches"] = 0
            elif failing and e["entity"] == failing:
                e["batches"], landed = 0, False
                err = (f"Error: push failed: batch 1 of {e['entity']} (rows 0..{e['total']}) failed: HTTP 400: "
                       f"{HTTP_400} statement: UNWIND $rows AS row MERGE (n:`Alert` {{`alert_key`: row.key.`alert_key`}})")
            elif landed or dry:
                e["batches"] = planned(e)
            else:
                e["batches"] = 0
            e["created"] = e["total"] if (landed or dry) else 0
        report = {"bytesSent": 0, "dryRun": dry, "elapsedMs": 1, "entities": entities,
                  "rowsSent": 0 if dry else sum(e["total"] for e in entities if e["batches"]), "statementsSent": 0}
        rc = 1 if err else 0
        if dry and not as_json:
            text = "\n".join(f"{e['entity']:50}{e['total']:>6}" for e in entities) + "\ndry run: nothing was sent\n"
            return CliResult(rc, text, "")
        return CliResult(rc, json.dumps(report), err + ("\n" if err else ""), report)

    def _entities(self, args: list[str]) -> list[dict]:
        store = args[args.index("--database") + 1]
        conn = open_store(store, read_only=True)
        try:
            counts = {r["l"]: r["n"] for r in rows(conn, "MATCH (n) UNWIND labels(n) AS l RETURN l, count(n) AS n")}
            rels = sorted(
                rows(conn, "MATCH (a)-[r]->(b) RETURN labels(a)[0] AS a, type(r) AS t, labels(b)[0] AS b, count(r) AS n"),
                key=lambda r: (LAB_TYPES.index(r["t"]) if r["t"] in LAB_TYPES else len(LAB_TYPES), r["t"]),
            )
            ends = {}
            for r in rels:
                for side, label in (("a", r["a"]), ("b", r["b"])):
                    q = f"MATCH (a)-[:{r['t']}]->(b) RETURN count(DISTINCT {side}) AS n"
                    ends[(r["t"], label)] = rows(conn, q)[0]["n"]
        finally:
            conn.close()
        node = lambda label, n: {"entity": label, "kind": "node", "total": n, "created": 0, "updated": 0, "batches": 0,  # noqa: E731
                                 "skipped": dict.fromkeys(("dangling", "policy", "remoteNewer", "unkeyed"), 0)}
        rel = lambda r: {**node(f"({r['a']})-[:{r['t']}]->({r['b']})", r["n"]), "kind": "relationship"}  # noqa: E731
        if "--labels" in args:
            labels = args[args.index("--labels") + 1].split(",")
            out = [node(label, counts[label]) for label in labels if counts.get(label)]
            out += [rel(r) for r in rels if r["a"] in labels and r["b"] in labels]
            left_out = sum(r["n"] for r in rels if r["a"] not in labels or r["b"] not in labels)
            if left_out:
                out.append({**node("(dangling)", left_out), "kind": "relationship",
                            "skipped": {"dangling": left_out, "policy": 0, "remoteNewer": 0, "unkeyed": 0}})
            return out
        types = args[args.index("--types") + 1].split(",")
        picked = [r for r in rels if r["t"] in types]
        nodes: dict[str, int] = {}
        for r in picked:
            for label in (r["a"], r["b"]):
                nodes[label] = max(nodes.get(label, 0), ends[(r["t"], label)])
        return [*(node(label, n) for label, n in nodes.items()), *(rel(r) for r in picked)]


# ---- a stand-in for the master graph's Bolt session ----------------------------------------------------------------
class _Record(dict):
    def data(self) -> dict:
        return dict(self)


class _Result(list):
    def __init__(self, data, summary):
        super().__init__(_Record(r) for r in data)
        self._summary = summary

    def consume(self):
        return self._summary

    def single(self):
        return self[0] if self else None


def _has_every_key(query: str, params: dict) -> list[dict]:
    """The default fake master graph: it holds every account key it is asked about (decision 35's check passes)."""
    if "IN $keys" in query and " AS k" in query:
        return [{"k": k} for k in params.get("keys", [])]
    return []


class FakeSession:
    """Records every statement; `answer(query, params)` supplies the rows a read returns. It is its own transaction."""

    def __init__(self, answer=None):
        self.statements: list[tuple[str, dict]] = []
        self.modes: list[str] = []  # how each statement was run: "read" (execute_read), "write" or "auto" (session.run)
        self.answer = answer or _has_every_key
        self._mode = "auto"

    def run(self, query, parameters=None, **params):
        params = {**(parameters or {}), **params}
        self.statements.append((query, params))
        self.modes.append(self._mode)
        made = len(params.get("rows", []))
        summary = SimpleNamespace(counters=SimpleNamespace(nodes_created=made, relationships_created=made))
        return _Result(self.answer(query, params), summary)

    def execute_read(self, fn, *args):
        self._mode = "read"
        try:
            return fn(self, *args)
        finally:
            self._mode = "auto"

    def execute_write(self, fn, *args):
        self._mode = "write"
        try:
            return fn(self, *args)
        finally:
            self._mode = "auto"

    def writes(self) -> list[tuple[str, dict]]:
        words = ("MERGE", "CREATE", "DELETE", "REMOVE", " SET ")
        return [(q, p) for q, p in self.statements if any(w in q.upper() for w in words)]


@contextmanager
def fake_master(session: FakeSession):
    yield session


def patch_master(monkeypatch, session: FakeSession) -> FakeSession:
    """Every `neo4j_session` the push, delta and retract modules open is `session`."""
    for module in ("lab.roundtrip.push", "lab.roundtrip.delta", "lab.roundtrip.retract"):
        monkeypatch.setattr(f"{module}.neo4j_session", lambda settings=None, s=session: fake_master(s))
    return session


# ---- live: what is on the master graph -----------------------------------------------------------------------------
def _key(item) -> str:
    return json.dumps(item, sort_keys=True, default=str)


def lab_snapshot() -> dict:
    """The lab's own nodes with every property, every `risk_*` property on every account, and the lab's link counts."""
    with neo4j_session() as s:
        snap: dict = {}
        for label in LAB_LABELS:
            snap[label] = sorted((r["p"] for r in s.run(f"MATCH (n:{label}) RETURN properties(n) AS p")), key=_key)
        snap["risk"] = {
            r["k"]: {k: v for k, v in r["p"].items() if k.startswith("risk_")}
            for r in s.run("MATCH (a:Account) RETURN a.account_id AS k, properties(a) AS p")
            if any(k.startswith("risk_") for k in r["p"])
        }
        for t in LAB_TYPES:
            snap[t] = s.run(f"MATCH ()-[r:{t}]->() RETURN count(r) AS n").single()["n"]
        return snap


def customer_state() -> dict:
    """Every customer node and relationship with its non-`risk_` properties, as a comparable value."""
    with neo4j_session() as s:
        nodes = sorted(
            _key([r["l"], {k: v for k, v in r["p"].items() if not k.startswith("risk_")}])
            for r in s.run("MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) RETURN labels(n) AS l, properties(n) AS p",
                           labels=list(FIXTURE_LABELS))
        )
        rels = sorted(
            _key([r["t"], r["a"], r["b"], r["p"]])
            for r in s.run("MATCH (a)-[r]->(b) WHERE type(r) IN $types RETURN type(r) AS t, "
                           "coalesce(a.account_id, a.device_id, a.email_id, a.address_id) AS a, "
                           "coalesce(b.account_id, b.device_id, b.email_id, b.address_id) AS b, properties(r) AS p",
                           types=list(FIXTURE_TYPES))
        )
        return {"nodes": nodes, "rels": rels}


def own_constraints_on_master() -> set[tuple[str, str]]:
    with neo4j_session() as s:
        have = {(r["l"][0], r["p"][0]) for r in s.run(
            "SHOW CONSTRAINTS YIELD labelsOrTypes AS l, properties AS p WHERE size(l) = 1 AND size(p) = 1")}
    return {c for c in OWN_CONSTRAINTS if c in have}


def wipe_lab() -> None:
    """Back to the fixture graph on the TEST Neo4j: delete the lab's nodes, remove every `risk_*` property, drop the
    constraints on the lab's own labels. Only ever called after `live_neo4j`, which refuses anything but a local server."""
    with neo4j_session() as s:
        s.run("MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) DETACH DELETE n", labels=list(LAB_LABELS)).consume()
        # accounts a test created on purpose to see what a push does with a key the master graph lacks
        s.run("MATCH (a:Account) WHERE a.account_id STARTS WITH 'ZZ' DETACH DELETE a").consume()
        names = sorted({r["n"] for r in s.run(
            "MATCH (a:Account) UNWIND keys(a) AS n WITH DISTINCT n WHERE n STARTS WITH 'risk_' RETURN n")})
        if names:
            s.run("MATCH (a:Account) WHERE any(k IN keys(a) WHERE k STARTS WITH 'risk_') REMOVE "
                  + ", ".join(f"a.`{n}`" for n in names)).consume()
        for r in list(s.run("SHOW CONSTRAINTS YIELD name, labelsOrTypes AS l WHERE size(l) = 1 RETURN name, l")):
            if r["l"][0] in LAB_LABELS:
                s.run(f"DROP CONSTRAINT `{r['name']}` IF EXISTS").consume()
