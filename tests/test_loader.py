"""Track 1b: the auto-sizing batch loader. Unit tests use a connection that refuses big commits the way Relate does;
one slow test makes the real Relate refuse."""
from __future__ import annotations

import pytest
from relate.exceptions import DataError

from lab.common.store import open_store, rows
from lab.roundtrip.loader import LoadError, LoadStats, is_wal_refusal, load_rows
from lab.settings import get_settings

# The message Relate 1.0.1 raises for a commit of 64 MB or more, verbatim.
WAL_REFUSAL = (
    "This statement tried to commit a single WAL record of at least 64 MB. This is a safety limit protecting "
    "against memory exhaustion — the per-record WAL payload cap is 64 MB; commit the data in smaller transactions."
)
# The same refusal from Relate 1.1.0, verbatim: MiB, and a longer hint. The loader must know both.
WAL_REFUSAL_1_1 = (
    "This statement tried to commit a single WAL record of at least 64 MiB. This is a safety limit protecting "
    "against memory exhaustion — the per-commit WAL payload cap is 64 MiB (ADR-0086); commit the data in smaller "
    "transactions (CALL { … } IN TRANSACTIONS OF n ROWS, or client-side batches)."
)
STATEMENT = "UNWIND $rows AS r CREATE (:N {id: r.id})"


class FakeConn:
    """Takes a batch up to `limit` rows; refuses a bigger one whole and keeps nothing, like Relate."""

    def __init__(self, limit: int, error: Exception | None = None):
        self.limit = limit
        self.error = error
        self.attempts: list[int] = []
        self.taken: list[dict] = []

    def execute(self, statement, params):
        batch = params["rows"]
        self.attempts.append(len(batch))
        if self.error is not None:
            raise self.error
        if len(batch) > self.limit:
            raise DataError(WAL_REFUSAL)
        self.taken.extend(batch)


def data(n: int) -> list[dict]:
    return [{"id": i} for i in range(n)]


def test_the_refusal_text_is_recognised():
    assert is_wal_refusal(DataError(WAL_REFUSAL))
    assert is_wal_refusal(DataError(WAL_REFUSAL_1_1))
    assert not is_wal_refusal(DataError("type error: InvalidPropertyType"))
    assert not is_wal_refusal(RuntimeError("disk full"))


def test_it_doubles_after_each_success_then_halves_on_refusal_and_holds():
    conn = FakeConn(limit=1000)
    stats = load_rows(conn, STATEMENT, data(5000), start=10)

    assert conn.attempts[:8] == [10, 20, 40, 80, 160, 320, 640, 1280]  # 1280 is refused ...
    assert conn.attempts[8:] == [640] * 5 + [530]  # ... so the same rows go again at half, and the size holds
    assert (stats.rows, stats.refusals, stats.largest_batch, stats.size) == (5000, 1, 640, 640)
    assert stats.batches == 7 + 5 + 1
    assert conn.taken == data(5000)  # every row once, in order


def test_it_keeps_halving_until_a_batch_goes_through():
    conn = FakeConn(limit=100)
    stats = load_rows(conn, STATEMENT, data(250), start=1000)
    assert conn.attempts[:3] == [250, 125, 62]
    assert stats.refusals == 2 and stats.largest_batch == 62
    assert conn.taken == data(250)


def test_everything_fits_in_one_batch_when_nothing_is_refused():
    conn = FakeConn(limit=10**9)
    stats = load_rows(conn, STATEMENT, data(300), start=1000)
    assert conn.attempts == [300] and stats == LoadStats(rows=300, batches=1, largest_batch=300, refusals=0,
                                                         size=2000)


def test_it_starts_at_the_setting_when_no_start_is_given(data_dir):
    start = get_settings().load_start_rows
    assert start == 20_000  # LAB_LOAD_START_ROWS, page 07
    conn = FakeConn(limit=10**9)
    load_rows(conn, STATEMENT, data(start + 5))
    assert conn.attempts[0] == start and conn.attempts[1] == 5


def test_a_single_refused_row_raises():
    conn = FakeConn(limit=0)
    with pytest.raises(LoadError, match="one row"):
        load_rows(conn, STATEMENT, data(10), start=8)
    assert conn.attempts == [8, 4, 2, 1] and conn.taken == []


def test_any_other_error_goes_straight_up_with_no_retry():
    conn = FakeConn(limit=10**9, error=DataError("type error: InvalidPropertyType"))
    with pytest.raises(DataError, match="InvalidPropertyType"):
        load_rows(conn, STATEMENT, data(10), start=8)
    assert conn.attempts == [8]


def test_it_takes_any_iterable_and_no_rows():
    conn = FakeConn(limit=10**9)
    assert load_rows(conn, STATEMENT, iter(data(5)), start=2).rows == 5
    assert load_rows(conn, STATEMENT, [], start=2).batches == 0
    assert conn.taken == data(5)


def test_it_loads_into_a_real_store(tmp_path):
    conn = open_store(tmp_path / "s")
    try:
        stats = load_rows(conn, "UNWIND $rows AS r CREATE (:N {id: r.id, tag: r.tag})",
                          [{"id": i, "tag": "x"} for i in range(1000)], start=100)
        assert stats.rows == 1000 and stats.batches == 4 and stats.largest_batch == 400
        assert rows(conn, "MATCH (n:N) RETURN count(n) AS n, count(DISTINCT n.id) AS ids") == [{"n": 1000, "ids": 1000}]
    finally:
        conn.close()


@pytest.mark.slow
def test_real_relate_refuses_a_big_commit_and_the_loader_recovers(tmp_path):
    """About 63 MB in one go is refused by the pinned Relate (wave 0 on 1.0.1, again on 1.1.0). Takes ten seconds or so."""
    pad = "x" * 380
    big = [{"id": i, "pad": pad} for i in range(150_000)]
    conn = open_store(tmp_path / "s")
    try:
        stats = load_rows(conn, "UNWIND $rows AS r CREATE (:N {id: r.id, pad: r.pad})", big, start=150_000)
        assert stats.refusals >= 1 and stats.rows == 150_000 and stats.largest_batch < 150_000
        assert rows(conn, "MATCH (n:N) RETURN count(n) AS n, count(DISTINCT n.id) AS ids") == [
            {"n": 150_000, "ids": 150_000}
        ]
    finally:
        conn.close()
