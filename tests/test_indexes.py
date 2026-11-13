"""Track 1b: the account-key constraint and the four search indexes (decision 29)."""
from __future__ import annotations

import random
import shutil

import pytest
from relate.exceptions import IntegrityError

from lab.common.store import open_store, rows
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
from lab.roundtrip.indexes import (
    INDEX_NAMES,
    create_indexes,
    ensure_account_constraint,
    existing_indexes,
    index_statements,
)
from lab.settings import Settings, get_settings


@pytest.fixture
def store(fixture_store_path, tmp_path):
    """A writable copy of the fixture store."""
    shutil.copytree(fixture_store_path, tmp_path / "store")
    conn = open_store(tmp_path / "store")
    yield conn
    conn.close()


@pytest.fixture
def empty_store(tmp_path):
    conn = open_store(tmp_path / "empty")
    yield conn
    conn.close()


def search_surface(conn) -> dict[str, tuple]:
    return {
        r["name"]: (r["type"], r["labelsOrTypes"], r["properties"])
        for r in rows(conn, "SHOW INDEXES")
        if r["type"] in ("VECTOR", "FULLTEXT")
    }


def test_the_names_are_decision_29():
    assert INDEX_NAMES == ("account_embedding", "account_summary", "community_embedding", "community_summary")
    assert INDEX_NAMES == (ACCOUNT_VECTOR_INDEX, ACCOUNT_TEXT_INDEX, COMMUNITY_VECTOR_INDEX, COMMUNITY_TEXT_INDEX)


def test_create_indexes_makes_the_four_and_a_second_call_changes_nothing(store):
    assert existing_indexes(store).isdisjoint(INDEX_NAMES)
    assert create_indexes(store, get_settings()) == list(INDEX_NAMES)
    assert search_surface(store) == {
        "account_embedding": ("VECTOR", ["Account"], [EMBEDDING_PROP]),
        "account_summary": ("FULLTEXT", ["Account"], [SUMMARY_PROP]),
        "community_embedding": ("VECTOR", [COMMUNITY_LABEL], ["embedding"]),
        "community_summary": ("FULLTEXT", [COMMUNITY_LABEL], ["summary"]),
    }
    again = create_indexes(store, get_settings())  # idempotent: no error, nothing new
    assert again == [] and len(search_surface(store)) == 4


def test_create_indexes_makes_only_the_missing_ones(store):
    store.execute(index_statements()[ACCOUNT_TEXT_INDEX])
    assert create_indexes(store) == [ACCOUNT_VECTOR_INDEX, COMMUNITY_VECTOR_INDEX, COMMUNITY_TEXT_INDEX]


def test_vector_indexes_take_the_embedding_dimension_and_cosine():
    statements = index_statements(Settings(LAB_EMBEDDING_DIM=8))
    for name in (ACCOUNT_VECTOR_INDEX, COMMUNITY_VECTOR_INDEX):
        assert "`vector.dimensions`: 8" in statements[name]
        assert "`vector.similarity_function`: 'cosine'" in statements[name]
    assert "`vector.dimensions`: 64" in index_statements(Settings())[ACCOUNT_VECTOR_INDEX]
    assert statements[ACCOUNT_TEXT_INDEX] == (
        "CREATE FULLTEXT INDEX account_summary IF NOT EXISTS FOR (n:Account) ON EACH [n.lab_summary]"
    )


def test_the_account_label_comes_from_the_mapping(empty_store):
    mapping = GraphMapping(account_label="Customer", account_key="cid")
    create_indexes(empty_store, get_settings(), mapping)
    surface = search_surface(empty_store)
    assert surface["account_embedding"][1] == ["Customer"] and surface["account_summary"][1] == ["Customer"]
    assert surface["community_embedding"][1] == [COMMUNITY_LABEL]
    assert ensure_account_constraint(empty_store, mapping) is True
    (constraint,) = rows(empty_store, "SHOW CONSTRAINTS")
    assert (constraint["labelsOrTypes"], constraint["properties"]) == (["Customer"], ["cid"])


def test_the_indexes_work_for_vectors_and_summaries_written_after_them(store):
    create_indexes(store)
    dim = get_settings().embedding_dim
    rng = random.Random(7)
    vectors = {k: [rng.random() for _ in range(dim)] for k in ("A0001", "A0002", "A0003")}
    for key, v in vectors.items():
        store.execute(
            f"MATCH (a:Account {{account_id: $k}}) SET a.{EMBEDDING_PROP} = $v, a.{SUMMARY_PROP} = $s",
            {"k": key, "v": v, "s": f"{key} sent several transfers in one day"},
        )
    nearest = rows(store, "MATCH (a:Account) SEARCH a IN (VECTOR INDEX account_embedding FOR $v LIMIT 3) "
                          "SCORE AS score RETURN a.account_id AS id, score", {"v": vectors["A0002"]})
    assert nearest[0]["id"] == "A0002" and {r["id"] for r in nearest} == set(vectors)
    words = rows(store, "MATCH (a:Account) SEARCH a IN (FULLTEXT INDEX account_summary FOR $q LIMIT 5) "
                        "SCORE AS score RETURN a.account_id AS id", {"q": "transfers"})
    assert {r["id"] for r in words} == set(vectors)


def test_ensure_account_constraint_is_idempotent(store, empty_store):
    assert ensure_account_constraint(store) is False  # the pull brought it
    assert ensure_account_constraint(empty_store) is True
    assert ensure_account_constraint(empty_store) is False
    assert rows(empty_store, "SHOW CONSTRAINTS")[0]["type"] == "NODE_PROPERTY_UNIQUENESS"
    empty_store.execute("CREATE (:Account {account_id: 'X'})")
    with pytest.raises(IntegrityError, match="uniqueness"):  # it holds: a second account with that key is refused
        empty_store.execute("CREATE (:Account {account_id: 'X'})")
