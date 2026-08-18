"""Per-thread database connections, reused across warm invocations.

Every request used to open its own connection. That is the textbook-correct thing for a
serverless function and it is also the single largest fixed cost in a room request:
measured against this project's own Neon endpoint, establishing a connection takes
~1.5-2.2s from a developer machine and a query on an ALREADY-OPEN connection takes
~0.25-0.5s. Vercel keeps a Python function's process alive between invocations, so that
cost was being paid again on every poll from every seat for a connection the process had
just thrown away.

**Why a cache and not a connection pool.** `psycopg_pool` is the obvious answer and is
the wrong one here, for a reason specific to the platform rather than to the library: a
pool keeps a BACKGROUND MAINTENANCE THREAD to open, retire and health-check connections
on a timer, and a Lambda execution environment is FROZEN between invocations. That thread
does not run while frozen, its timers do not fire, and it wakes believing far less time
has passed than really has -- so the pool's own idea of which connections are fresh is
exactly the thing that cannot be trusted. Nothing in this module runs outside a request.

**The staleness problem, and why it is not solved by checking `conn.closed`.** A
connection the server has dropped -- Neon's free-tier compute suspends after about five
minutes idle -- can still look open from here, because nothing has tried to use it. So a
connection that has been idle a while is PINGED before being handed out, and replaced if
the ping fails. A connection in active use is not pinged: during a live draft every seat
polls every two seconds, so the hot path never reaches the ping at all, which is the
whole point of the idle threshold.

**A retry after a failed ping is safe; a retry of real work is not, and this module never
does the second one.** The ping happens before the request has executed anything, so
reconnecting and continuing cannot repeat an effect. If a connection instead dies midway
through a request, that request fails -- surfaced as a 503 by `web.app`'s own
`psycopg.OperationalError` handler -- rather than being replayed, because a `submit_pick`
whose INSERT reached the server before the connection dropped would append the move a
second time. That risk is not new or unique to reuse (a fresh connection can die mid
request too); what would be new is a retry loop quietly turning it into duplicated state.

**Thread-local, because the routes are synchronous.** All 21 call sites are plain `def`
endpoints, which Starlette runs in an anyio worker threadpool -- so two requests really
can be in flight in one process, whatever a given host's concurrency happens to be today.
A psycopg connection is not for concurrent use by two threads, so each thread keeps its
own rather than sharing one behind a lock: on Lambda (one invocation at a time) that is a
single connection either way, and it does not serialise a local `uvicorn` the way a shared
one would.

**Reuse is therefore not 100%, by design, and `/api/health` will show that.** anyio
retires a worker thread after `MAX_IDLE_TIME`, which is **10 seconds** (measured, anyio
4.14) -- so a burst of requests more than ten seconds after the last one runs on brand new
threads with no cached connection. That is the right trade rather than a defect: the case
this module exists for is a room being actively played, where every seat polls every two
seconds and the thread never goes idle long enough to be retired. Measured against a live
server, a burst following a quiet gap opened new connections and the burst immediately
after it reused every one.

**A retired thread does not leak its connection**, which was checked rather than assumed:
the thread's local storage is released when it dies, the connection's last reference goes
with it, and psycopg closes the socket on finalisation. Verified with a weakref -- with
nothing holding it, the object is collected as soon as the owning thread exits.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager

import psycopg

# Bounds how long a request waits for Neon itself -- TCP/TLS plus, on the free tier,
# waking a suspended compute -- rather than hanging on a dead endpoint indefinitely.
CONNECT_TIMEOUT_S = 10

# Below this much idle time a cached connection is used without being pinged. Neon's
# free-tier compute suspends at around five minutes, so this is well inside the window
# where a connection is still almost certainly good; a room being actively played never
# gets near it, since every seat polls every two seconds.
IDLE_REVALIDATE_S = 30

# Recycled regardless of health once this old. Nothing observed requires this -- it is
# here so a single connection cannot live for the whole lifetime of a warm process and
# accumulate whatever server-side state a long-lived session can.
MAX_AGE_S = 900

_LOCAL = threading.local()

_STATS_LOCK = threading.Lock()
_STATS = {"opened": 0, "reused": 0, "pinged": 0, "replaced": 0}


def _bump(key: str) -> None:
    with _STATS_LOCK:
        _STATS[key] += 1


def stats() -> dict:
    """A snapshot, for `/api/health`. Reuse is invisible when it works -- the same
    property that let A107's snapshot fallback hide a broken deploy for an hour -- so the
    counters are reported rather than inferred from a latency that could have any cause."""
    with _STATS_LOCK:
        return dict(_STATS)


def _connect():
    """The one place a connection is created. Patched wholesale in tests, which is what
    keeps the suite free of any real database."""
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=CONNECT_TIMEOUT_S)


def _forget() -> None:
    _LOCAL.conn = None


def _close_quietly(conn) -> None:
    try:
        conn.close()
    except Exception:
        pass    # already gone; nothing here can or should care why


def _alive(conn) -> bool:
    """One round trip, and the transaction it implicitly opens is rolled back so the
    request's real work starts a fresh one -- otherwise `SET LOCAL` in `rooms._load_room`
    would be scoped to a transaction that began before the request did."""
    _bump("pinged")
    try:
        conn.execute("select 1").fetchone()
        conn.rollback()
        return True
    except Exception:
        return False


def _checkout():
    conn = getattr(_LOCAL, "conn", None)
    now = time.monotonic()
    if conn is not None:
        stale = (
            conn.closed
            or now - getattr(_LOCAL, "opened_at", now) > MAX_AGE_S
            or (now - getattr(_LOCAL, "last_used", now) > IDLE_REVALIDATE_S
                and not _alive(conn))
        )
        if stale:
            _close_quietly(conn)
            _forget()
            _bump("replaced")
            conn = None
    if conn is None:
        conn = _connect()
        _LOCAL.conn = conn
        _LOCAL.opened_at = time.monotonic()
        _LOCAL.last_used = _LOCAL.opened_at
        _bump("opened")
    else:
        _bump("reused")
    return conn


@contextmanager
def connection():
    """Commits on a clean exit and rolls back on any exception, exactly as the
    `with psycopg.connect(...)` this replaces did -- the difference is only that the
    connection is kept afterwards instead of closed.

    An application-level failure (a `RoomError` turned into an HTTPException inside the
    caller's own `with` block, which is routine) rolls back and KEEPS the connection: it
    says nothing about the connection's health, and discarding one on every refused pick
    would give up most of the reuse this module exists for. A connection is dropped only
    when it is genuinely unusable -- closed, or a rollback that itself fails."""
    conn = _checkout()
    try:
        yield conn
    except BaseException:
        try:
            if conn.closed:
                _forget()
            else:
                conn.rollback()
                _LOCAL.last_used = time.monotonic()
        except Exception:
            _close_quietly(conn)
            _forget()
        raise
    else:
        try:
            conn.commit()
        except Exception:
            _close_quietly(conn)
            _forget()
            raise
        _LOCAL.last_used = time.monotonic()


def close_all() -> None:
    """This thread's connection, for tests and for an orderly local shutdown. There is no
    cross-thread registry to close from: a thread-local is by construction only reachable
    from the thread that owns it, and a warm process's connections die with it anyway."""
    conn = getattr(_LOCAL, "conn", None)
    if conn is not None:
        _close_quietly(conn)
    _forget()
