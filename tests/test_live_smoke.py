"""The live harness itself: the local Neo4j holds the fixture graph after `live_neo4j`."""
import pytest

from lab.common.neo4j import master_rows

pytestmark = pytest.mark.live


def test_local_neo4j_has_the_fixture(live_neo4j):
    row = master_rows("MATCH (a:Account) WITH count(a) AS accounts MATCH ()-[t:TX]->() "
                      "RETURN accounts, count(t) AS payments")[0]
    assert row == {"accounts": 200, "payments": 2000}
