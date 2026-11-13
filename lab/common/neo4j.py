"""The Bolt driver for the master graph: the fallback routes, the delta sample, retractions."""
from __future__ import annotations

from contextlib import contextmanager

from lab.settings import Settings, get_settings


def neo4j_driver(settings: Settings | None = None):
    from neo4j import GraphDatabase

    s = settings or get_settings()
    return GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_username, s.neo4j_password))


@contextmanager
def neo4j_session(settings: Settings | None = None):
    s = settings or get_settings()
    driver = neo4j_driver(s)
    try:
        with driver.session(database=s.neo4j_database) as session:
            yield session
    finally:
        driver.close()


def master_rows(query: str, params: dict | None = None, settings: Settings | None = None) -> list[dict]:
    """Read from the master graph over Bolt. Never used to write except by the push fallback and retractions."""
    with neo4j_session(settings) as session:
        return [r.data() for r in session.run(query, params or {})]
