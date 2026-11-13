"""The run store's indexes: the account-key constraint, and the four search indexes of decision 29.

`relate pull` skips vector and full-text indexes, so the lab creates them after every pull, whichever route filled
the store. Relate 1.0.1 takes `IF NOT EXISTS` on all of these, so calling twice changes nothing. The indexes are
created on empty properties: the first pass writes the embeddings and summaries later, and Relate indexes them as
they land.
"""
from __future__ import annotations

from lab.common.store import rows
from lab.contracts.graph import (
    ACCOUNT_TEXT_INDEX,
    ACCOUNT_VECTOR_INDEX,
    COMMUNITY_LABEL,
    COMMUNITY_TEXT_INDEX,
    COMMUNITY_VECTOR_INDEX,
    EMBEDDING_PROP,
    SUMMARY_PROP,
    GraphMapping,
)
from lab.roundtrip.queries import ident
from lab.settings import Settings, get_settings

INDEX_NAMES = (ACCOUNT_VECTOR_INDEX, ACCOUNT_TEXT_INDEX, COMMUNITY_VECTOR_INDEX, COMMUNITY_TEXT_INDEX)

# Community nodes in the run store carry their own embedding and summary (page 02, "Where it goes next").
COMMUNITY_EMBEDDING_PROP = "embedding"
COMMUNITY_SUMMARY_PROP = "summary"

SIMILARITY = "cosine"


def unique_constraint(label: str, key: str) -> str:
    return f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{ident(label)}) REQUIRE n.{ident(key)} IS UNIQUE"


def ensure_account_constraint(conn, mapping: GraphMapping | None = None) -> bool:
    """The account key is unique in the run store, whichever route filled it. True if this call created it."""
    m = mapping or GraphMapping()
    before = _constraint_count(conn)
    conn.execute(unique_constraint(m.account_label, m.account_key))
    return _constraint_count(conn) > before


def _constraint_count(conn) -> int:
    return len(rows(conn, "SHOW CONSTRAINTS"))


def index_statements(settings: Settings | None = None, mapping: GraphMapping | None = None) -> dict[str, str]:
    """index name -> its CREATE statement. Vector indexes take `LAB_EMBEDDING_DIM` dimensions, cosine."""
    s = settings or get_settings()
    m = mapping or GraphMapping()
    account, community = ident(m.account_label), ident(COMMUNITY_LABEL)
    options = f"{{indexConfig: {{`vector.dimensions`: {int(s.embedding_dim)}, `vector.similarity_function`: '{SIMILARITY}'}}}}"

    def vector(name: str, label: str, prop: str) -> str:
        return f"CREATE VECTOR INDEX {name} IF NOT EXISTS FOR (n:{label}) ON (n.{ident(prop)}) OPTIONS {options}"

    def fulltext(name: str, label: str, prop: str) -> str:
        return f"CREATE FULLTEXT INDEX {name} IF NOT EXISTS FOR (n:{label}) ON EACH [n.{ident(prop)}]"

    return {
        ACCOUNT_VECTOR_INDEX: vector(ACCOUNT_VECTOR_INDEX, account, EMBEDDING_PROP),
        ACCOUNT_TEXT_INDEX: fulltext(ACCOUNT_TEXT_INDEX, account, SUMMARY_PROP),
        COMMUNITY_VECTOR_INDEX: vector(COMMUNITY_VECTOR_INDEX, community, COMMUNITY_EMBEDDING_PROP),
        COMMUNITY_TEXT_INDEX: fulltext(COMMUNITY_TEXT_INDEX, community, COMMUNITY_SUMMARY_PROP),
    }


def existing_indexes(conn) -> set[str]:
    return {r["name"] for r in rows(conn, "SHOW INDEXES")}


def create_indexes(conn, settings: Settings | None = None, mapping: GraphMapping | None = None) -> list[str]:
    """Create the four search indexes that are missing. Returns the names this call created.

    Raises if one is still absent afterwards.
    """
    before = existing_indexes(conn)
    statements = index_statements(settings, mapping)
    created = []
    for name, statement in statements.items():
        conn.execute(statement)
        if name not in before:
            created.append(name)
    missing = set(statements) - existing_indexes(conn)
    if missing:
        raise RuntimeError(f"Relate did not create the indexes {sorted(missing)}")
    return created
