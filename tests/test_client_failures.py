"""[A180] client_failure_reports against a REAL Postgres: the single-statement insert, its
spam guard and its pruning are SQL a fake connection could not check. Opt-in, like
tests/test_room_pg.py; each test runs in a transaction that is rolled back."""

from __future__ import annotations

import os

import pytest

from web import client_failures

SCRATCH = os.environ.get("IPLEGENDS_SCRATCH_DB")
pytestmark = pytest.mark.skipif(
    not SCRATCH, reason="set IPLEGENDS_SCRATCH_DB to a throwaway, fully migrated database")

FAIL = {"t": 1.0, "kind": "timeout", "method": "POST", "path": "/api/rooms/AB12CD/auction/bid",
        "status": None, "ms": 7000, "online": True, "net": "4g", "visible": True}


@pytest.fixture
def pg():
    import psycopg
    conn = psycopg.connect(SCRATCH)
    conn.execute("delete from client_failure_reports")   # inside the rolled-back transaction
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def test_a_report_is_stored_and_summarised_as_a_rate(pg):
    assert client_failures.record(pg, "/room", 99, [FAIL])
    s = client_failures.summary(pg, 24)
    assert (s["reports"], s["failures"], s["ok"]) == (1, 1, 99)
    assert s["rate"] == pytest.approx(0.01)
    assert s["by_kind"][0][:2] == ("timeout", 1)
    assert s["by_path"][0][0] == "POST /api/rooms/AB12CD/auction/bid"


def test_reports_older_than_the_keep_window_are_pruned_on_the_next_insert(pg):
    pg.execute("insert into client_failure_reports (received_at, page, ok_count, failures) "
               "values (now() - interval '31 days', '/old', 1, '[]'), "
               "(now() - interval '29 days', '/recent', 1, '[]')")
    client_failures.record(pg, "/room", 0, [FAIL])
    pages = {r[0] for r in pg.execute("select page from client_failure_reports")}
    assert pages == {"/recent", "/room"}


def test_the_spam_guard_drops_reports_past_the_hourly_cap(pg, monkeypatch):
    monkeypatch.setattr(client_failures, "MAX_REPORTS_PER_HOUR", 3)
    stored = [client_failures.record(pg, "/room", 0, [FAIL]) for _ in range(5)]
    assert stored == [True, True, True, False, False]
    assert pg.execute("select count(*) from client_failure_reports").fetchone()[0] == 3


def test_a_report_is_capped_at_fifty_failures(pg):
    client_failures.record(pg, "/room", 0, [FAIL] * 80)
    n = pg.execute("select jsonb_array_length(failures) from client_failure_reports"
                   ).fetchone()[0]
    assert n == client_failures.MAX_PER_REPORT
