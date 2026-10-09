"""[A180] How a room talks to a REAL Postgres: transactions, row locks and the lock
timeout. A fake connection keyed on SQL text cannot see any of these -- it has no
transactions to leave open and no locks to wait on -- so these run against a real server.

Opt-in, like tests/test_admin.py's query tests: point IPLEGENDS_SCRATCH_DB at a THROWAWAY
database with every migration applied. These tests COMMIT (the point of one of them is a
read that opens no transaction), and clean up the one room they create.
"""

from __future__ import annotations

import os
import time

import pytest

from web import rooms

SCRATCH = os.environ.get("IPLEGENDS_SCRATCH_DB")
pytestmark = pytest.mark.skipif(
    not SCRATCH, reason="set IPLEGENDS_SCRATCH_DB to a throwaway, fully migrated database")

CODE = "ZZPGTS"


@pytest.fixture
def pg():
    import psycopg
    setup = psycopg.connect(SCRATCH, autocommit=True)
    setup.execute("delete from rooms where code = %s", (CODE,))
    room = rooms.Room(code=CODE, format="final", timer_seconds=30, seed=1, host_id="h",
                      players={"h": rooms.RoomPlayer("h", "Host", False)})
    rooms._save_room(setup, room)
    conn = psycopg.connect(SCRATCH)
    # A guard so a broken lock bound fails this test instead of hanging the suite.
    conn.execute("set statement_timeout = '15s'")
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()
        setup.execute("delete from rooms where code = %s", (CODE,))
        setup.close()


def _status(conn):
    import psycopg
    return psycopg.pq.TransactionStatus(conn.info.transaction_status).name


def test_a_lock_free_read_opens_no_transaction(pg):
    """The poll's read is one round trip only if psycopg never sends BEGIN for it --
    which shows as no transaction being open afterwards. With a transaction it would be
    three (BEGIN, the read, COMMIT): measured 214 ms against 69 ms to Singapore."""
    room = rooms._load_room(pg, CODE, lock=False)
    assert room.code == CODE
    assert _status(pg) == "IDLE", "a lock-free read must not open a transaction"
    assert pg.autocommit is False, "autocommit must be switched back off afterwards"


def test_a_lock_free_read_inside_a_transaction_keeps_the_callers_lock(pg):
    """Inside a transaction the helper must do nothing: switching to autocommit there
    would commit early and release a row lock the caller still relies on."""
    import psycopg
    rooms._load_room(pg, CODE, lock=True)
    rooms._load_room(pg, CODE, lock=False)
    assert _status(pg) == "INTRANS"
    other = psycopg.connect(SCRATCH)
    try:
        with pytest.raises(psycopg.errors.LockNotAvailable):
            other.execute("select 1 from rooms where code = %s for update nowait", (CODE,))
    finally:
        other.close()
    pg.rollback()


def test_a_locked_read_gives_up_at_its_bound_under_contention(pg):
    """The lock timeout now lives inside the locking statement rather than in a separate
    SET LOCAL before it. It must still bound THIS statement's wait: a move queued behind
    a stuck lock has to fail cleanly (a 503) rather than hang until the platform kills it."""
    import psycopg
    holder = psycopg.connect(SCRATCH)
    try:
        holder.execute("select 1 from rooms where code = %s for update", (CODE,))
        start = time.monotonic()
        with pytest.raises(psycopg.errors.LockNotAvailable):
            rooms._load_room(pg, CODE, lock=True)
        waited = time.monotonic() - start
        assert 4.0 < waited < 9.0, f"expected the 5s bound, waited {waited:.1f}s"
    finally:
        holder.rollback()
        holder.close()
        pg.rollback()


def test_the_lock_bound_does_not_outlive_its_transaction(pg):
    """Scoped to the transaction (`set_config(..., true)`), so under transaction-mode
    pooling it cannot reach the next client handed the same server connection."""
    default = pg.execute("show lock_timeout").fetchone()[0]
    pg.rollback()
    rooms._load_room(pg, CODE, lock=True)
    assert pg.execute("show lock_timeout").fetchone()[0] == "5s", "bound inside the move"
    pg.commit()
    assert pg.execute("show lock_timeout").fetchone()[0] == default
    pg.rollback()


def test_a_poll_of_a_quiet_room_leaves_no_transaction_open(pg):
    """The whole poll, not just the read: a lobby with a stale heartbeat writes its
    presence too, and that write must not open a transaction either."""
    pg.execute("update rooms set updated_at = now() - interval '1 hour' where code = %s",
               (CODE,))
    pg.commit()
    rooms.room_state(pg, CODE, None)
    assert _status(pg) == "IDLE"
    pg.execute("select 1")
    idle = pg.execute("select extract(epoch from now() - updated_at) from rooms "
                      "where code = %s", (CODE,)).fetchone()[0]
    pg.rollback()
    assert idle < 60, "the heartbeat must still have been written"
