"""`web.db`'s connection reuse.

Driven entirely by a fake connection -- the rest of the suite touches no database and
this must not be the exception. `web.db._connect` is the single place a real connection
is created precisely so it can be replaced here.

What is worth testing is not "does it cache" (it plainly does) but the four ways caching
can be WRONG: handing back a connection the server has already dropped, losing reuse on
an ordinary application error, replaying work after a mid-request failure, and letting
two threads share one connection.
"""

from __future__ import annotations

import threading
import time

import pytest

from web import db


class FakeConn:
    """Enough of a psycopg connection for this module: it only ever calls `execute`,
    `commit`, `rollback`, `close`, and reads `closed`."""

    def __init__(self, alive: bool = True):
        self.closed = False
        self.alive = alive          # False => the server has dropped it underneath us
        self.commits = 0
        self.rollbacks = 0
        self.pings = 0
        self.statements: list[str] = []

    def execute(self, sql, params=()):
        if self.closed or not self.alive:
            raise db.psycopg.OperationalError("connection is closed")
        self.statements.append(sql)
        if "select 1" in sql:
            self.pings += 1
        return self

    def fetchone(self):
        return (1,)

    def commit(self):
        if self.closed or not self.alive:
            raise db.psycopg.OperationalError("connection is closed")
        self.commits += 1

    def rollback(self):
        if self.closed or not self.alive:
            raise db.psycopg.OperationalError("connection is closed")
        self.rollbacks += 1

    def close(self):
        self.closed = True


@pytest.fixture
def conns(monkeypatch):
    """Every connection this test handed out, newest last."""
    made: list[FakeConn] = []

    def fake_connect():
        made.append(FakeConn())
        return made[-1]

    monkeypatch.setattr(db, "_connect", fake_connect)
    db.close_all()
    yield made
    db.close_all()


def test_a_second_request_reuses_the_first_ones_connection(conns):
    with db.connection() as a:
        pass
    with db.connection() as b:
        pass
    assert a is b
    assert len(conns) == 1, "a second connection was opened for the second request"
    assert a.commits == 2, "each request must still commit its own transaction"


def test_an_idle_connection_is_pinged_and_replaced_when_the_server_dropped_it(conns):
    """The failure this module exists to survive. A connection Neon has closed -- its
    free-tier compute suspends after about five minutes idle -- still reads `closed ==
    False` here, because nothing has tried to use it. Only actually using it finds out."""
    with db.connection() as first:
        pass
    first.alive = False                      # dropped server-side, silently
    db._LOCAL.last_used = time.monotonic() - (db.IDLE_REVALIDATE_S + 1)

    with db.connection() as second:
        pass
    assert second is not first, "a dead connection was handed to a request"
    assert first.closed, "the dead connection was left open"
    assert len(conns) == 2


def test_a_recently_used_connection_is_not_pinged(conns):
    """The hot path: during a live draft every seat polls every two seconds, so a
    connection is never idle long enough to need checking. Pinging anyway would put a
    round trip back onto every request, which is most of what this module removes."""
    with db.connection() as first:
        pass
    with db.connection() as second:
        pass
    assert second is first
    assert first.pings == 0, "a busy connection was pinged"


def test_an_application_error_rolls_back_but_keeps_the_connection(conns):
    """A refused pick raises through the caller's own `with` block and says nothing about
    the connection's health. Dropping one on every `RoomError` would give up most of the
    reuse -- refusals are routine, not exceptional."""
    with pytest.raises(ValueError):
        with db.connection() as first:
            raise ValueError("room is not drafting")
    assert first.rollbacks == 1
    assert first.commits == 0
    assert not first.closed

    with db.connection() as second:
        pass
    assert second is first
    assert len(conns) == 1


def test_a_connection_that_dies_mid_request_is_discarded_not_retried(conns):
    """The rule that keeps reuse safe. Replaying the request would be the dangerous fix:
    a `submit_pick` whose INSERT reached the server before the connection dropped would
    append the move a second time. The request fails (`web.app` turns OperationalError
    into a 503) and the connection is dropped so the NEXT request gets a fresh one."""
    with pytest.raises(db.psycopg.OperationalError):
        with db.connection() as first:
            first.alive = False
            first.execute("insert into rooms ...")

    assert getattr(db._LOCAL, "conn", None) is None, "a broken connection was kept"
    with db.connection() as second:
        pass
    assert second is not first
    assert len(conns) == 2


def test_a_connection_is_recycled_once_it_is_too_old(conns):
    with db.connection() as first:
        pass
    db._LOCAL.opened_at = time.monotonic() - (db.MAX_AGE_S + 1)
    with db.connection() as second:
        pass
    assert second is not first
    assert first.closed


def test_two_threads_never_share_one_connection(conns):
    """A psycopg connection is not for concurrent use by two threads, and the routes are
    synchronous `def` endpoints -- which Starlette runs in a worker threadpool, so two
    requests really can be in flight in one process."""
    seen: dict[int, object] = {}
    barrier = threading.Barrier(2)

    def run():
        with db.connection() as c:
            barrier.wait(timeout=5)      # both inside their own `with` at once
            seen[threading.get_ident()] = c
        db.close_all()

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=10)

    assert len(seen) == 2
    a, b = seen.values()
    assert a is not b, "two threads were handed the same connection"


def test_stats_report_whether_reuse_is_actually_happening(conns):
    before = db.stats()
    with db.connection():
        pass
    with db.connection():
        pass
    after = db.stats()
    assert after["opened"] == before["opened"] + 1
    assert after["reused"] == before["reused"] + 1


def test_a_failed_connect_leaves_nothing_cached(conns, monkeypatch):
    """Neon unreachable, or a cold start that cannot wake the compute. The request must
    fail cleanly -- `web.app` turns OperationalError into a 503 -- and, more importantly,
    must not leave a half-built entry behind that wedges every later request on this
    thread. Checked by counting connect attempts: a second request tries again."""
    attempts = {"n": 0}

    def failing():
        attempts["n"] += 1
        raise db.psycopg.OperationalError("could not connect to server")

    monkeypatch.setattr(db, "_connect", failing)
    db.close_all()

    for _ in range(2):
        with pytest.raises(db.psycopg.OperationalError):
            with db.connection():
                pass
        assert getattr(db._LOCAL, "conn", None) is None, "a poisoned entry was cached"
    assert attempts["n"] == 2, "a later request did not retry the connect"
