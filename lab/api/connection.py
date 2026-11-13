"""The Connect screen's two jobs: test a master graph, and use it for this session (R1, R2, decision 30).

`check_connection` checks the Bolt driver (server version, edition) and the HTTP Query API that `relate pull` and
`relate push` use, and names the pull route that will run: `relate` when the Query API answers, `bolt` when it does not
(page 01's fallback rule: unreachable, a 5xx, or a 404 that is not a Neo4j error document), nothing when neither works.
It never returns a password, and scrubs a pasted one out of every message it builds.

`ConnectionState.use` puts pasted credentials into the process's cached settings, in memory only. Every `lab` module
reads `get_settings()`, so they all see it at once. Nothing is written to disk, and `.env` is not touched.
"""
from __future__ import annotations

import re
import threading

import httpx

from lab.contracts.api import ConnectResult, ConnectTest
from lab.settings import Settings, get_settings

TIMEOUT = 8.0  # seconds for each of the two checks
_USERINFO = re.compile(r"(://)[^/@\s]*@")


def _scrub(text: str, *secrets: str | None) -> str:
    text = _USERINFO.sub(r"\1", text)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text.strip()[:300]


def effective(request: ConnectTest, settings: Settings | None = None) -> Settings:
    """The settings with the pasted fields laid over them. An empty field means the settings' own value."""
    base = settings or get_settings()
    update: dict[str, object] = {}
    for field, value in (("neo4j_uri", request.uri), ("neo4j_username", request.username),
                         ("neo4j_password", request.password), ("neo4j_database", request.database)):
        if value:
            update[field] = value
    if request.uri:
        update["neo4j_query_uri"] = None  # derive it from the new URI, not from the old one
    return base.model_copy(update=update)


def _query_api(s: Settings) -> tuple[str | None, bool, str]:
    """(address, reachable, why not). `reachable` follows the pull's own rule for falling back to Bolt."""
    try:
        base = s.query_uri
    except ValueError as e:
        return None, False, str(e)
    url = f"{base}/db/{s.neo4j_database}/query/v2"
    try:
        r = httpx.post(url, json={"statement": "RETURN 1 AS ok"}, auth=(s.neo4j_username, s.neo4j_password),
                       headers={"Accept": "application/json"}, timeout=TIMEOUT)
    except httpx.HTTPError as e:
        return base, False, f"the Query API at {base} did not answer ({type(e).__name__})"
    if r.status_code >= 500 or (r.status_code == 404 and "Neo." not in r.text):
        return base, False, f"the Query API at {base} answered HTTP {r.status_code}"
    if r.status_code in (401, 403):
        return base, True, f"the Query API at {base} rejected the credentials (HTTP {r.status_code})"
    return base, True, ""


def check_connection(request: ConnectTest | None = None, settings: Settings | None = None) -> ConnectResult:
    """Test the master graph the request (or else the settings) names. Never raises; `ok` says how it went."""
    from neo4j import GraphDatabase
    from neo4j.exceptions import AuthError

    s = effective(request or ConnectTest(), settings)
    secrets = (s.neo4j_password,)
    version = edition = None
    driver = None
    try:
        driver = GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_username, s.neo4j_password),
                                      connection_timeout=TIMEOUT, connection_acquisition_timeout=TIMEOUT)
        driver.verify_connectivity()
        agent = driver.get_server_info().agent or ""
        version = agent.split("/", 1)[1] if "/" in agent else (agent or None)
        try:
            with driver.session(database=s.neo4j_database) as session:
                # one row per component on Neo4j 2026; the kernel's is the server's version
                row = session.run("CALL dbms.components() YIELD name, versions, edition "
                                  "RETURN versions[0] AS version, edition "
                                  "ORDER BY CASE name WHEN 'Neo4j Kernel' THEN 0 ELSE 1 END LIMIT 1").single()
            if row:
                version, edition = row["version"] or version, row["edition"]
        except Exception:  # noqa: BLE001 (a user without that procedure still has a working connection)
            pass
    except AuthError:
        return ConnectResult(ok=False, message=f"Neo4j at {_scrub(s.neo4j_uri)} rejected the username or password.")
    except Exception as e:  # noqa: BLE001 (every way a connection fails, said plainly)
        return ConnectResult(ok=False, message=_scrub(f"Could not connect to {s.neo4j_uri}: {type(e).__name__}: {e}",
                                                      *secrets))
    finally:
        if driver is not None:
            driver.close()

    query_uri, reachable, why = _query_api(s)
    named = f"Neo4j {version}" + (f" ({edition})" if edition else "") if version else "Neo4j"
    if query_uri is None:
        return ConnectResult(
            ok=False, neo4j_version=version, edition=edition,
            message=_scrub(f"Connected to {named} over Bolt, but relate pull cannot be told where its Query API is: {why}",
                           *secrets))
    if reachable and why:  # reachable, but it will not let relate in
        return ConnectResult(ok=False, neo4j_version=version, edition=edition, query_uri=query_uri,
                             message=_scrub(f"Connected to {named} over Bolt, but {why}.", *secrets))
    if reachable:
        return ConnectResult(ok=True, neo4j_version=version, edition=edition, query_uri=query_uri, pull_route="relate",
                             message=f"Connected to {named}. relate pull will use the Query API at {query_uri}.")
    return ConnectResult(
        ok=True, neo4j_version=version, edition=edition, query_uri=query_uri, pull_route="bolt",
        message=_scrub(f"Connected to {named}, but {why}. The pull will fall back to parallel Bolt reads.", *secrets))


class ConnectionState:
    """Whether this process is using pasted credentials. The credentials live in the cached settings object only."""

    FIELDS = ("neo4j_uri", "neo4j_username", "neo4j_password", "neo4j_database", "neo4j_query_uri")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._original: dict[str, object] | None = None

    @property
    def active(self) -> bool:
        return self._original is not None

    def use(self, request: ConnectTest) -> None:
        """Make the pasted credentials the process's settings. Only the fields the request fills change."""
        s = get_settings()
        new = effective(request, s)
        with self._lock:
            if self._original is None:
                self._original = {f: getattr(s, f) for f in self.FIELDS}
            for f in self.FIELDS:
                setattr(s, f, getattr(new, f))

    def restore(self) -> None:
        """Back to what `.env` and the environment said. Tests use it; the app has no button for it."""
        with self._lock:
            if self._original is None:
                return
            s = get_settings()
            for f, value in self._original.items():
                setattr(s, f, value)
            self._original = None

    def current(self) -> ConnectResult:
        """A live check of the master graph the process is using now, with where its credentials came from."""
        result = check_connection()
        where = ("the credentials pasted into the Connect screen, for this session only" if self.active
                 else "the settings (.env)")
        return result.model_copy(update={"message": f"{result.message} Using {where}."})


__all__ = ["ConnectionState", "check_connection", "effective"]
