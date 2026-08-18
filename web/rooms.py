"""Live multiplayer rooms: friends draft together, live, turn by turn, from one shared
competitive pool.

[Shared-pool, v3.] Rooms began in-memory (v1), then moved to Neon while keeping a
round-based design where every seat was dealt its own private, unrelated sequence of
franchise-seasons -- nobody could ever compete for the same card, and there was no turn
order at all, just a shared per-round deadline. This version replaces that: going first
now genuinely means first dibs. Once any seat drafts a real person, no other seat in the
room can ever draft that same person, and picks proceed strictly one seat at a time in
snake order (1234 4321 1234...).

A shared pool means a seat's own progress now depends on every OTHER seat's moves too --
so there is no more "one seat, one independent (seed, moves) pair" to store. Instead the
whole room is ONE shared, ordered move log (`rooms.moves`, migration 020): every entry is
`{"seat": player_id, "index": int, "slot": int}`, position in the array is the move
number, and the entire room -- every seat's order/impact, the one shared taken-person-id
set, the one shared rng stream -- is rebuilt from scratch by replaying that log from move
0 (`replay_room`), the same "resolve on read" pattern `web.session.replay` already used
for a single seat (SPEC 11.3/A62), generalised here to N seats sharing one pool.

A room still does not reimplement the draft's legality rules. `replay_room` calls
`etl.feasibility.eligible`/`could_still_complete`/`choose_slot` directly, unmodified --
none of that logic needed to change for a shared pool, since `eligible` already takes an
arbitrary `taken` set as a plain parameter with no assumption about whose picks are in it.
A room only orchestrates WHOSE turn it is, what a shared deal looks like, and what
happens when a turn's clock runs out.

Every human seat's turn has its own deadline (`rooms.turn_started_at`, reset each time a
new turn becomes pending), resolved LAZILY: whichever request next touches the room first
resolves any CPU turn at the front of the queue (instantly -- there is no round-by-round
experience to give a CPU, and nobody is watching it deliberate) and any human turn whose
clock has actually expired (auto-picking a RANDOM eligible candidate via
`etl.feasibility.pick_afk`, reused directly rather than reimplemented) before doing
anything else. Nothing runs in the background (A62).

A CPU seat can no longer be pre-drafted whole at room start -- its pick at each of its
own turns now depends on what humans have already taken by that point in the shared
sequence, so it is computed once, exactly when its turn arrives, and appended to
`rooms.moves` as a real recorded move, just like a human's. This is a deliberate,
explicit departure from A19's "never store what's derivable": under the old independent-
per-seat model a CPU's twelve was a pure function of (deck, seed) and never needed
storing; under a shared pool it depends on the live taken-set at the moment its turn
arrives -- itself a function of every human decision before it -- so it is no longer
derivable from (deck, seed) alone.

Rerolls and repositions are still deliberately NOT part of a room draft -- unchanged from
the prior design, and what keeps `replay_room`'s dealing loop simple (no exception-driven
pool-narrowing to replicate): every recorded move is a plain index into whatever was
dealt for that turn, nothing more.

Raised again on 2026-08-16 ("you aren't able to change the batting order in play with
friends") and confirmed to stay out, so it is a standing choice rather than an oversight
nobody got to. What it would cost, for whoever weighs it next: a room move would stop
being a plain index and gain a KIND, and `turn_seat_index` derives whose turn it is from
the raw `len(room.moves)` -- so a non-pick move in that log silently shifts the snake
order for every seat, not just the one repositioning. That is the change to plan for, and
it is why this cannot be a small patch.
"""

from __future__ import annotations

import random
import secrets
import string
import time
from dataclasses import dataclass, field

from psycopg.types.json import Json

from etl.feasibility import (
    ALL_SLOTS, IMPACT_SLOT, REDRAW_CAP, TWELVE_SIZE, XI_SIZE, Card, Deck,
    DraftState, eligible, pick_afk,
)
from game.season import historical_sides
from web import session as sess

ROOM_FORMATS = {"final": 2, "cup": 4, "league": 10}
TIMER_CHOICES = (15, 30, 45)
# A public, anonymous browse list (this app has no accounts, A62) needs a bound so it
# can't grow without limit -- rooms are few and short-lived (ROOM_TTL_HOURS below), so
# this is generous rather than a real constraint in practice.
OPEN_ROOMS_LIMIT = 30
# A pure display choice, exactly the solo draft's own client-side DRAFT_MODE distinction
# (web/static/index.html) -- 'stat' shows a rating badge and lets a name be clicked for
# his season stats, 'memory' shows neither. Chosen once by the host at creation and
# binding on every seat (migration 023); the join flow gets no choice of its own.
DRAFT_MODES = ("stat", "memory")
_CODE_ALPHABET = string.ascii_uppercase + string.digits
_CODE_LENGTH = 6


class RoomError(ValueError):
    """A room request that cannot be honoured -- refused with a 4xx, never a 500."""


@dataclass
class RoomPlayer:
    """A seat's identity only. Its draft progress is never stored per-seat any more --
    see `SeatProgress`/`replay_room` -- so there is nothing else to carry here."""

    player_id: str
    name: str
    is_cpu: bool


@dataclass
class Room:
    code: str
    format: str
    timer_seconds: int
    seed: int
    host_id: str
    players: dict = field(default_factory=dict)   # player_id -> RoomPlayer, join order
    status: str = "lobby"          # "lobby" | "drafting" | "complete" | "failed"
    moves: list = field(default_factory=list)      # the one shared, ordered turn log
    turn_started_at: float = 0.0
    failure_reason: str | None = None
    # The match-phase shared move log (migration 022) -- see web/room_match.py. Empty
    # for a room that hasn't reached "complete" yet, and generally still empty for a
    # while after: nothing here starts consuming it until the first `/match` poll.
    match_moves: list = field(default_factory=list)
    draft_mode: str = "stat"       # migration 023 -- see DRAFT_MODES above
    # migration 025 -- true lists this room for anyone to join without the code
    # (list_open_rooms below). Set once at creation, never updated afterward, same as
    # format/timer_seconds/draft_mode.
    is_open: bool = False
    # migration 031 -- how long since this room was last active, as READ. Computed by
    # the database rather than carried as a timestamp, so no clock comparison ever crosses
    # between Postgres and Python (migration 019's own header makes the same point about
    # `turn_started_at` going the other way). Never written from here; `_save_room` and
    # `_touch_room` are what move it.
    idle_seconds: float = 0.0
    # migration 030 -- the stored monotonic state counter, as READ. Never incremented
    # here: `_save_room` increments it in the database and writes the new value back onto
    # this field, so the number a request serves is always the one the write produced
    # rather than an in-memory guess at it.
    version: int = 0

    @property
    def seats(self) -> int:
        return ROOM_FORMATS[self.format]

    @property
    def full(self) -> bool:
        return len(self.players) >= self.seats

    @property
    def round(self) -> int:
        """Which snake wave we're in, for display only -- derived from the shared move
        log, never stored (A19: `len(moves) // seat_count` says the same thing a stored
        counter would, and a stored one could drift from the log it's supposed to track).
        Divided by the DRAFTING seat count, not every seat -- a filler (`is_cpu`) seat
        never takes a turn, so counting it here would understate which wave the actual
        drafters are in."""
        n = sum(1 for p in self.players.values() if not p.is_cpu)
        return len(self.moves) // n if n else 0


# A room is a short-lived social thing; nothing here schedules a sweep, it just happens
# to run whenever someone next starts a new one (A62's "resolve on read" stance, extended
# to cleanup).
ROOM_TTL_HOURS = 24

# An OPEN room still sitting in the lobby is deleted once it has been inactive this long.
# Scoped deliberately narrowly, and each half of the scope is doing work:
#   * `is_open`, because these are the rooms a stranger can walk into off the public Join
#     list -- a dead private room harms nobody and its host may be about to share the code.
#   * `lobby`, because an open room that has STARTED is no longer listed anyway
#     (`list_open_rooms` filters on status), so deleting one would destroy a game in
#     progress for no benefit to anybody browsing.
# Everything outside that scope keeps ROOM_TTL_HOURS.
OPEN_ROOM_IDLE_MINUTES = 15

# How stale `updated_at` may get before a plain poll refreshes it. This is what makes
# "inactive" mean "nobody is here" rather than "nobody has written recently" -- polls do
# not write (A119), so an occupied lobby waiting on one more player generates no writes at
# all and would otherwise be swept out from under the people sitting in it.
#
# A THIRD of the idle window, so a room with anybody present refreshes about three times
# before it could ever be considered idle -- one missed heartbeat cannot delete a live
# room. The cost is bounded and tiny: at most one write per room per five minutes, however
# many seats are polling and however often, against the ~0.5 writes/second/seat that
# updating on every poll would have cost.
PRESENCE_HEARTBEAT_MINUTES = 5


def _sweep_stale_rooms(conn) -> None:
    """Two rules, one statement. Every room ages out after ROOM_TTL_HOURS whatever it was
    doing; an OPEN room still in the lobby goes much sooner once nobody is there, because
    it is the one kind a stranger can find and walk into off the public list.

    `updated_at`, not `created_at`, for the second rule -- the rooms this exists to remove
    are exactly the ones created and then abandoned, so creation time says nothing about
    whether anyone is still around."""
    conn.execute(
        """
        delete from rooms
         where created_at < now() - make_interval(hours => %s)
            or (is_open and status = 'lobby'
                and updated_at < now() - make_interval(mins => %s))
        """,
        (ROOM_TTL_HOURS, OPEN_ROOM_IDLE_MINUTES),
    )


def _new_code(conn) -> str:
    while True:
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
        if conn.execute("select 1 from rooms where code = %s", (code,)).fetchone() is None:
            return code


def _load_room(conn, code: str, *, lock: bool = True) -> Room:
    """Locks the room row for the rest of the caller's transaction by default -- every
    mutating function below pairs a locked call with a `_save_room` before the
    connection commits, so two requests that both intend to WRITE serialise through
    this lock rather than racing. `lock=False` is for a caller that only needs to
    look, not write (`room_state`'s own fast path) -- see its docstring for why a
    plain poll doesn't need this lock at all most of the time."""
    # ONE round trip, not two. This used to read `rooms` and then `room_players` as
    # separate statements; on a remote database a round trip is the dominant cost of a
    # request (the queries themselves are a primary-key lookup and a ten-row index scan),
    # and every seat polling every 2s pays this on a connection of its own. The join is
    # `for update of r` rather than a bare `for update`: the lock this function exists to
    # take is on the ROOM row, and Postgres refuses `for update` on the nullable side of
    # an outer join anyway -- which is the right restriction here, since a room with no
    # seats yet must still load rather than erroring.
    if lock:
        # SET LOCAL, not SET: under transaction-mode pooling (Neon's pooled endpoint is
        # PgBouncer) a session-level setting can outlive this transaction and reach the
        # next client handed the same server connection. LOCAL is scoped to the
        # transaction the statement below runs in, which is exactly the scope the lock
        # itself has. Issued only when actually locking -- a read-only poll waits on no
        # lock, so bounding one for it was a round trip per request buying nothing.
        conn.execute("set local lock_timeout = '5s'")
    rows = conn.execute(
        f"""
        select r.code, r.format, r.timer_seconds, r.seed, r.host_id, r.status,
               r.turn_started_at, r.failure_reason, r.moves, r.match_moves,
               r.draft_mode, r.is_open, r.version,
               extract(epoch from (now() - r.updated_at)) as idle_seconds,
               p.player_id, p.name, p.is_cpu
          from rooms r
          left join room_players p on p.room_code = r.code
         where r.code = %s
         order by p.seat_order
        {"for update of r" if lock else ""}
        """,
        (code,),
    ).fetchall()
    if not rows:
        raise RoomError(f"no room {code!r}")
    (code, fmt, timer_seconds, seed, host_id, status,
     turn_started_at, failure_reason, moves, match_moves, draft_mode, is_open,
     version, idle_seconds) = rows[0][:14]
    room = Room(code=code, format=fmt, timer_seconds=timer_seconds, seed=seed,
                host_id=host_id, status=status,
                turn_started_at=turn_started_at or 0.0, failure_reason=failure_reason,
                moves=list(moves or []), match_moves=list(match_moves or []),
                draft_mode=draft_mode, is_open=is_open, version=version,
                idle_seconds=float(idle_seconds or 0.0))
    for row in rows:
        player_id, name, is_cpu = row[14], row[15], row[16]
        # NULL on every column of the right-hand side means the outer join matched no
        # seat at all -- a room that exists with nobody in it, which is a real state
        # (`leave_room` can empty a lobby), not a missing row to guess at.
        if player_id is None:
            continue
        room.players[player_id] = RoomPlayer(player_id, name, is_cpu)
    return room


def _save_room(conn, room: Room) -> None:
    # draft_mode and is_open join format/timer_seconds/seed/host_id as "set once at
    # creation, never updated" -- deliberately absent from the ON CONFLICT clause
    # below, same as those already are.
    # `version = rooms.version + 1` reads the CURRENT stored value and increments it in
    # the same statement, so two concurrent writers cannot both produce the same number --
    # this is the database's own counter, not one carried in from the caller's `Room`
    # object, which could be an arbitrarily stale read. RETURNING writes it straight back
    # onto the object so whatever serialises this room afterwards serves the version its
    # own write produced, without a second SELECT to go and fetch it.
    room.version = conn.execute(
        """
        insert into rooms (code, format, timer_seconds, seed, host_id, status,
                            turn_started_at, failure_reason, moves, match_moves,
                            draft_mode, is_open, version)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
        on conflict (code) do update set
            status = excluded.status, turn_started_at = excluded.turn_started_at,
            failure_reason = excluded.failure_reason, moves = excluded.moves,
            match_moves = excluded.match_moves,
            version = rooms.version + 1,
            -- Every write is activity by definition: a join, a leave, a pick, a toss,
            -- an advance. The poll path adds the missing half (somebody merely PRESENT)
            -- via `_touch_room` below.
            updated_at = now()
        returning version
        """,
        (room.code, room.format, room.timer_seconds, room.seed, room.host_id,
         room.status, room.turn_started_at, room.failure_reason, Json(room.moves),
         Json(room.match_moves), room.draft_mode, room.is_open),
    ).fetchone()[0]
    # ONE multi-row insert, not one round trip per seat -- this used to loop and issue a
    # separate `conn.execute` per player, but every existing player's row is a guaranteed
    # ON CONFLICT DO NOTHING no-op (a seat's identity never changes after it's created:
    # `RoomPlayer`'s own docstring), so a 10-seat league room was paying up to nine wasted
    # round trips on EVERY single write -- a pick, an auto-resolve, anything that touches
    # `_save_room` -- for zero effect. Measured directly against this project's own Neon
    # connection: ~0.3-0.5s per additional round trip on an already-open connection, which
    # is most of why a pick in a full room felt slow rather than any lock contention (that
    # was the earlier, separate fix -- A92). Values are still individually conflict-
    # checked/skipped exactly as before; only the round-trip count changes.
    if room.players:
        values_sql = ", ".join(["(%s, %s, %s, %s, %s)"] * len(room.players))
        params = []
        for seat_order, (player_id, p) in enumerate(room.players.items()):
            params.extend([room.code, player_id, seat_order, p.name, p.is_cpu])
        conn.execute(
            f"""
            insert into room_players (room_code, player_id, seat_order, name, is_cpu)
            values {values_sql}
            on conflict (room_code, player_id) do nothing
            """,
            params,
        )


def create_room(conn, fmt: str, timer_seconds: int, host_name: str,
                 draft_mode: str = "stat", is_open: bool = False) -> tuple[Room, str]:
    if fmt not in ROOM_FORMATS:
        raise RoomError(f"unknown format {fmt!r}: choose one of {sorted(ROOM_FORMATS)}")
    if timer_seconds not in TIMER_CHOICES:
        raise RoomError(f"timer must be one of {TIMER_CHOICES}")
    if draft_mode not in DRAFT_MODES:
        raise RoomError(f"unknown draft_mode {draft_mode!r}: choose one of {DRAFT_MODES}")
    _sweep_stale_rooms(conn)
    room_seed = sess.new_seed()
    host_id = secrets.token_urlsafe(8)
    room = Room(code=_new_code(conn), format=fmt, timer_seconds=timer_seconds,
                seed=room_seed, host_id=host_id, draft_mode=draft_mode, is_open=is_open)
    room.players[host_id] = RoomPlayer(host_id, host_name, is_cpu=False)
    _save_room(conn, room)
    return room, host_id


def join_room(conn, code: str, name: str, deck: Deck) -> tuple[Room, str]:
    room = _load_room(conn, code)
    if room.status != "lobby":
        raise RoomError("this room has already started")
    if room.full:
        raise RoomError("this room is full")
    player_id = secrets.token_urlsafe(8)
    room.players[player_id] = RoomPlayer(player_id, name, is_cpu=False)
    _save_room(conn, room)
    return room, player_id


@dataclass
class OpenRoom:
    """One row of the public browse list -- just enough to show and join it, never the
    full `Room` (a joiner has no business seeing another seat's own progress before
    they're even in)."""

    code: str
    format: str
    timer_seconds: int
    draft_mode: str
    host_name: str
    seats_filled: int


def list_open_rooms(conn) -> list[OpenRoom]:
    """Open, still-joinable rooms -- `is_open`, still in the lobby, and ACTIVE within
    `OPEN_ROOM_IDLE_MINUTES` -- most recently active first, capped at `OPEN_ROOMS_LIMIT`.

    The idle filter is here as well as in the sweep, and duplicating it is the point: the
    sweep only runs when somebody creates a room, so without this a dead room stays
    advertised until that happens to occur. Filtering on read makes the list correct
    immediately and costs no write.

    Ordered by activity rather than creation, which is a real change of meaning: a room
    somebody just joined is a better thing to show a stranger than one merely created more
    recently and since abandoned.

    A room already full is excluded here in Python, not SQL: capacity is
    `ROOM_FORMATS[format]`, a constant keyed by format, not a stored column, so there is
    nothing to compare against inside the query itself."""
    rows = conn.execute(
        """
        select r.code, r.format, r.timer_seconds, r.draft_mode,
               (select name from room_players
                 where room_code = r.code and player_id = r.host_id) as host_name,
               (select count(*) from room_players where room_code = r.code) as seats_filled
          from rooms r
         where r.is_open and r.status = 'lobby'
           and r.updated_at > now() - make_interval(mins => %s)
         order by r.updated_at desc
         limit %s
        """,
        (OPEN_ROOM_IDLE_MINUTES, OPEN_ROOMS_LIMIT),
    ).fetchall()
    return [
        OpenRoom(code=code, format=fmt, timer_seconds=timer_seconds, draft_mode=draft_mode,
                 host_name=host_name or "Host", seats_filled=seats_filled)
        for code, fmt, timer_seconds, draft_mode, host_name, seats_filled in rows
        if seats_filled < ROOM_FORMATS[fmt]
    ]


def _remove_seat(conn, room: Room, code: str, target_id: str) -> None:
    """The one place a seat actually leaves `room_players` -- `_save_room`'s own write
    there is insert-or-do-nothing (never an update, never a delete), so removing an
    entry from `room.players` in memory and calling `_save_room` would never touch the
    corresponding row. Shared by `leave_room` and `kick_player`, which differ only in
    WHO is allowed to invoke it against WHOM, not in what removal itself does."""
    del room.players[target_id]
    conn.execute("delete from room_players where room_code = %s and player_id = %s",
                 (code, target_id))


def leave_room(conn, code: str, player_id: str) -> Room:
    """A non-host seat leaving the LOBBY is actually freed here, not just abandoned
    client-side. Without this, a seat lingered forever, and rejoining under the same
    code added a brand new player_id alongside the ghost rather than reclaiming it --
    the duplicate-seat bug this closes. Scoped to the lobby only: once drafting starts,
    seats are a fixed count the snake turn order and move log are keyed to (A73), and an
    inactive human's turn already times out into an auto-pick -- there is no seat to
    free."""
    room = _load_room(conn, code)
    if room.status != "lobby":
        raise RoomError("a seat can only be freed while the room is still in its lobby")
    if player_id == room.host_id:
        raise RoomError("the host can't leave -- there's no one to hand the room off to")
    if player_id not in room.players:
        raise RoomError("you are not seated in this room")
    _remove_seat(conn, room, code, player_id)
    return room


def kick_player(conn, code: str, host_id: str, target_id: str) -> Room:
    """The host's mirror of `leave_room` -- the same removal, invoked against someone
    else's seat rather than the caller's own, for a seat that needs freeing by someone
    ELSE's decision (AFK, wrong lobby, whatever) rather than its own occupant's. Same
    scope as `leave_room`: lobby only, and the host itself can never be the target
    (nobody to hand the room off to)."""
    room = _load_room(conn, code)
    if host_id != room.host_id:
        raise RoomError("only the host can remove another seat")
    if room.status != "lobby":
        raise RoomError("a seat can only be removed while the room is still in its lobby")
    if target_id == room.host_id:
        raise RoomError("the host can't be removed")
    if target_id not in room.players:
        raise RoomError("that seat is not in this room")
    _remove_seat(conn, room, code, target_id)
    return room


def start_room(conn, code: str, player_id: str, deck: Deck) -> Room:
    """Host-only. Fills every seat still empty with a CPU seat and starts the first
    turn's clock. Unlike the old per-seat-independent design, a CPU's twelve can no
    longer be validated as feasible up front -- it depends on what humans take at their
    own turns, interleaved with the CPU's, so there is nothing to pre-draft or check
    here any more. A draft that turns out to be unservable is discovered lazily, at
    whichever turn it actually happens on, and marks the room "failed" (`_resolve`)."""
    room = _load_room(conn, code)
    if player_id != room.host_id:
        raise RoomError("only the host can start the draft")
    if room.status != "lobby":
        raise RoomError("this room has already started")

    n = 0
    while len(room.players) < room.seats:
        n += 1
        cpu_id = f"__cpu_{n}__"
        room.players[cpu_id] = RoomPlayer(cpu_id, f"CPU {n}", is_cpu=True)

    room.status = "drafting"
    room.turn_started_at = time.time()
    _save_room(conn, room)
    return room


def play_again(conn, code: str, player_id: str) -> Room:
    """Any seated player, not the host only -- resets a finished room back to its own
    lobby, in place: same code, same host, same format/timer_seconds/draft_mode, same
    real seats, but a FRESH seed (so playing again is a genuinely new draft and match,
    not a rerun of the one just finished) and both move logs wiped. CPU seats are
    dropped rather than carried over -- `start_room` refills whatever's still empty when
    the host next clicks Start, so keeping last game's CPU count would misrepresent who
    actually showed up this time, the same reasoning `leave_room`/`kick_player` already
    apply to a departing seat.

    Idempotent by design: if the room is already back in 'lobby' -- because some OTHER
    seat's own "play again" click already reset it -- this is a no-op rather than a
    second reset, so several players clicking within the same couple of seconds converge
    on one shared lobby instead of racing or double-resetting each other's fresh seed.
    `_load_room`'s row lock (its own docstring) is what makes that convergence safe
    rather than a check-then-act gap: two concurrent calls serialise through it, and
    whichever runs second sees the first's already-'lobby' room and returns immediately.
    """
    room = _load_room(conn, code)
    if player_id not in room.players:
        raise RoomError("you are not seated in this room")
    if room.status == "lobby":
        return room
    if room.status not in ("complete", "failed"):
        raise RoomError(f"cannot play again while the room is still {room.status}")

    for cpu_id in [pid for pid, p in room.players.items() if p.is_cpu]:
        _remove_seat(conn, room, code, cpu_id)

    room.status = "lobby"
    room.seed = sess.new_seed()
    room.moves = []
    room.match_moves = []
    room.turn_started_at = 0.0
    room.failure_reason = None
    _save_room(conn, room)
    # `_save_room`'s own ON CONFLICT clause deliberately excludes `seed` -- it's "set
    # once at creation, never updated" for every OTHER mutator, and this is the one
    # place in the whole module that's meant to be the exception, so it gets its own
    # narrow write rather than loosening the general one for everybody.
    conn.execute("update rooms set seed = %s where code = %s", (room.seed, code))
    return room


# --- turn order -------------------------------------------------------------------------

def turn_seat_index(move_no: int, n_seats: int) -> int:
    """0-indexed seat position whose turn `move_no` (0-indexed, overall across the whole
    room) belongs to. Standard snake draft: ascending on even rounds, descending on odd
    -- 1234 4321 1234... Round 0 (even) ascending gives seat positions 0,1,2,3; round 1
    (odd) descending gives 3,2,1,0; round 2 ascending again. Every seat therefore gets an
    equal mix of firsts and lasts across a full draft, the standard snake fairness
    property -- seat order is left as join order (host first), unrandomised, since that
    property already makes it fair over the whole draft."""
    round_no, pos = divmod(move_no, n_seats)
    return pos if round_no % 2 == 0 else n_seats - 1 - pos


# --- the shared replay -------------------------------------------------------------------

@dataclass
class SeatProgress:
    """One seat's own progress, rebuilt fresh on every `replay_room` call -- the per-seat
    half of what used to live entirely in a `web.session.Session`, now interleaved with
    every other seat's against one shared `taken` set.

    `historical_name` is set only for a FILLER seat (see `replay_room`'s own comment) --
    a real franchise-season's display name, assigned once the room starts drafting and
    never touched again. `None` for every drafting seat."""

    order: list = field(default_factory=lambda: [None] * XI_SIZE)
    impact: Card | None = None
    picks: list = field(default_factory=list)
    open_slots: set = field(default_factory=lambda: set(ALL_SLOTS))
    keeper_have: bool = False
    bowl_have: int = 0
    overseas_taken: int = 0
    historical_name: str | None = None

    @property
    def done(self) -> bool:
        return len(self.picks) >= TWELVE_SIZE


@dataclass
class RoomReplay:
    """The whole room's state at this instant, rebuilt from scratch by replaying
    `room.moves` from move 0 -- the same 'resolve on read' pattern `web.session.replay`
    already used for one seat (SPEC 11.3), generalised to N seats sharing one pool."""

    seats: dict                                # player_id -> SeatProgress
    complete: bool
    stranded: bool = False
    pending_seat_id: str | None = None
    pending_deal: tuple | None = None          # (fs_id, candidates: list[Card])
    pending_blocked: list | None = None        # [(Card, reason)] -- the REST of the squad


def _apply(seat: SeatProgress, taken: set[str], card: Card, slot: int) -> None:
    """Mirrors `etl.feasibility.run_draft`'s own post-pick bookkeeping exactly, applied
    to one seat's progress plus the ONE shared taken set every seat draws against."""
    taken.add(card.person_id)
    if card.overseas is True:
        seat.overseas_taken += 1
    if card.keeper_eligible:
        seat.keeper_have = True
    if card.has_bowl:
        seat.bowl_have += 1
    seat.open_slots.discard(slot)
    seat.picks.append(card)
    if slot == IMPACT_SLOT:
        seat.impact = card
    else:
        seat.order[slot - 1] = card


def replay_room(room: Room, deck: Deck) -> RoomReplay:
    """Rebuild the whole room from scratch: one shared `rng` seeded from `room.seed`,
    one shared `taken` person-id set, and one `SeatProgress` per seat. Replays every
    recorded move in `room.moves` in order, dealing exactly one franchise-season per
    turn (the same deal-time redraw guarantee `run_draft` uses, via `eligible` from
    `etl.feasibility`, unmodified) against whichever DRAFTING seat's turn it is and the
    shared `taken` set. After the recorded history, deals the next pending turn the same
    way, which is what becomes visible (options included, to that seat only) or hidden
    (franchise/season only, to everyone else) at the API layer.

    A filler (`is_cpu`) seat never takes a turn at all -- it is handed a real historical
    franchise-season's algorithmic best eleven the instant the room starts drafting,
    reusing `game.season.historical_sides` (the exact mechanism solo's own season
    opposition is built from, A63) rather than drafting from the shared pool. That
    assignment is a pure function of `(room.seed, how many filler seats there are)`,
    independent of `room.moves` -- a filler's opposition is no more related to what
    humans draft than solo's nine opponents are related to the human's own twelve, so it
    can legally overlap a human's picks and never competes with them for a card.
    """
    seat_ids = list(room.players)
    drafting_ids = [pid for pid in seat_ids if not room.players[pid].is_cpu]
    filler_ids = [pid for pid in seat_ids if room.players[pid].is_cpu]
    n = len(drafting_ids)
    rng = random.Random(room.seed)
    taken: set[str] = set()
    seats = {pid: SeatProgress() for pid in seat_ids}

    if filler_ids:
        filler_rng = random.Random(f"{room.seed}-fillers")
        sides = historical_sides(deck, filler_rng, len(filler_ids))
        if len(sides) < len(filler_ids):
            raise RoomError("not enough legal historical squads to fill this room")
        for pid, side in zip(filler_ids, sides):
            seat = seats[pid]
            seat.order = list(side.xi)
            seat.impact = side.impact
            seat.picks = list(side.xi) + ([side.impact] if side.impact else [])
            seat.historical_name = side.name

    def _deal_for(pid: str):
        seat = seats[pid]
        remaining = TWELVE_SIZE - len(seat.picks)
        for _ in range(REDRAW_CAP):
            fs_id = rng.choice(deck.fs_ids)
            cards = deck.cards_by_fs.get(fs_id, ())
            candidates = list(eligible(
                cards, taken, frozenset(seat.open_slots), seat.keeper_have,
                seat.bowl_have, seat.overseas_taken, remaining,
            ))
            if candidates:
                return fs_id, candidates
        return None

    for move_no, mv in enumerate(room.moves):
        pid = drafting_ids[turn_seat_index(move_no, n)]
        if mv["seat"] != pid:
            raise sess.InvalidState(
                f"move {move_no} recorded for {mv['seat']!r}, expected {pid!r}")
        dealt = _deal_for(pid)
        if dealt is None:
            return RoomReplay(seats, complete=False, stranded=True, pending_seat_id=pid)
        fs_id, candidates = dealt
        if not (0 <= mv["index"] < len(candidates)):
            raise sess.InvalidState(
                f"choice {mv['index']} not among {len(candidates)} options dealt")
        card = candidates[mv["index"]]
        if mv["slot"] not in (card.slots & seats[pid].open_slots):
            raise sess.InvalidState(f"{card.name} cannot be placed at {mv['slot']}")
        _apply(seats[pid], taken, card, mv["slot"])

    if len(room.moves) >= n * TWELVE_SIZE:
        return RoomReplay(seats, complete=True)

    pid = drafting_ids[turn_seat_index(len(room.moves), n)]
    dealt = _deal_for(pid)
    if dealt is None:
        return RoomReplay(seats, complete=False, stranded=True, pending_seat_id=pid)
    fs_id, _candidates = dealt
    # The WHOLE squad, not just the pickable part -- same as solo's own deal (A64),
    # via the identical `sess._blocked` a solo session already uses, so the reasons
    # ("already drafted", "overseas quota full", the forward-check diagnoses) can never
    # drift between the two modes.
    blocked = sess._blocked(deck, fs_id, _draft_state(seats[pid]))
    return RoomReplay(seats, complete=False, pending_seat_id=pid, pending_deal=dealt,
                       pending_blocked=blocked)


def _draft_state(seat: SeatProgress) -> DraftState:
    """The one shape `etl.feasibility`'s own policies/`_blocked` need, built from a
    room seat's own progress -- identical to what `web.session.replay` builds from a
    solo `DraftState` at pause time, just sourced from `SeatProgress` instead."""
    return DraftState(
        picks=tuple(seat.picks), order=tuple(seat.order), impact=seat.impact,
        open_slots=frozenset(seat.open_slots), keeper_have=seat.keeper_have,
        bowl_have=seat.bowl_have, remaining=TWELVE_SIZE - len(seat.picks),
    )


def _resolve(room: Room, deck: Deck) -> None:
    """Lazily catch the room up to the present: auto-pick (via `pick_afk`) for whoever's
    turn has actually timed out, before doing anything else. Called at the top of every
    room route, so no background scheduler is needed (A62).

    Filler (`is_cpu`) seats never reach this function at all -- `replay_room` only ever
    assigns `pending_seat_id` to a drafting seat, since a filler's historical squad is
    fixed the instant the room starts drafting rather than built turn by turn.

    A room can genuinely strand -- the same wildcard-optimism gap A73 documents for the
    solo draft (the forward check is an OPTIMISTIC bound: with one pick left it assumes
    a hypothetical future pick can be a keeper AND a bowler AND eligible wherever
    remains, and real archive data does not always supply one). Under a shared pool this
    is a materially broader failure surface than before: one seat's earlier picks can
    now strand a DIFFERENT seat by exhausting a card that seat specifically needed.
    Either way there is no undo and no reroll to recover with, so the room is marked
    "failed" and stops resolving -- reported to every seat, never a crash.
    """
    while room.status == "drafting":
        replay = replay_room(room, deck)
        if replay.stranded:
            room.status = "failed"
            room.failure_reason = f"stranded on seat {replay.pending_seat_id}'s turn"
            return
        if replay.complete:
            room.status = "complete"
            return

        pid = replay.pending_seat_id
        if time.time() - room.turn_started_at <= room.timer_seconds:
            return  # still within this seat's own window

        fs_id, candidates = replay.pending_deal
        seat = replay.seats[pid]
        state = _draft_state(seat)
        # Seeded from the room's own state rather than left to ambient entropy. The pick
        # is recorded into `room.moves` immediately below, so replay determinism (A62)
        # holds either way -- but an unseeded draw made the choice unreproducible for
        # anyone debugging a room after the fact, and this codebase has already been bitten
        # once by a policy drawing from global `random` (A73). Keyed on the move count so
        # successive timeouts in one room do not all draw from the same stream position.
        rng = random.Random(f"{room.seed}:afk:{len(room.moves)}")
        card, slot = pick_afk(candidates, state, rng)
        index = next(i for i, c in enumerate(candidates) if c.person_id == card.person_id)
        room.moves = room.moves + [{"seat": pid, "index": index, "slot": slot}]
        room.turn_started_at = time.time()


def _mutable_state(room: Room):
    """Everything `_resolve` is capable of changing, as a comparable value -- used to tell
    "this poll actually caught the room up" from "another poll got here first".

    Deliberately NOT `room.version`: that only moves when a write happens, so comparing it
    across a resolve would always report no change and the room would never be saved at
    all. These are the four fields `_resolve` assigns to, and it assigns to nothing else.
    `moves` is compared by LENGTH because `_resolve` only ever appends -- it never edits a
    recorded move, and a length is cheap where a deep comparison of the whole log is not.
    """
    return (room.status, room.turn_started_at, len(room.moves), room.failure_reason)


def submit_pick(conn, code: str, player_id: str, index: int, slot: int, deck: Deck) -> Room:
    room = _load_room(conn, code)
    _resolve(room, deck)
    if room.status != "drafting":
        raise RoomError(f"this room is not drafting (status: {room.status})")
    player = room.players.get(player_id)
    if player is None:
        raise RoomError("you are not seated in this room")
    if player.is_cpu:
        raise RoomError("a CPU seat cannot be picked for")

    replay = replay_room(room, deck)
    if replay.pending_seat_id != player_id:
        raise RoomError("it is not your turn")
    fs_id, candidates = replay.pending_deal
    if not (0 <= index < len(candidates)):
        raise RoomError(f"choice {index} not among {len(candidates)} options dealt")
    card = candidates[index]
    seat = replay.seats[player_id]
    if slot not in (card.slots & seat.open_slots):
        raise RoomError(f"{card.name} cannot be placed at {slot}")

    room.moves = room.moves + [{"seat": player_id, "index": index, "slot": slot}]
    room.turn_started_at = time.time()
    _resolve(room, deck)   # the next turn may already be a CPU's -- fast-forward it
    _save_room(conn, room)
    return room


def _touch_room(conn, room: Room) -> None:
    """Record that somebody is still here, at most once per PRESENCE_HEARTBEAT_MINUTES.

    A single UPDATE of one column, not a `_save_room`: nothing about the room has changed,
    so re-writing its moves and bumping `version` would be a lie to every client watching
    the version to order responses (A119) -- a heartbeat is not a state change and must not
    read as one.

    Guarded on `idle_seconds` from the read the caller already did, so the common poll
    costs nothing extra: a room being actively used writes once every five minutes,
    whatever its seat count and however fast anyone polls."""
    conn.execute("update rooms set updated_at = now() where code = %s", (room.code,))
    room.idle_seconds = 0.0


def room_state(conn, code: str, deck: Deck) -> Room:
    """Read-only poll: resolve any expired turn first, so a client that polls slowly
    still sees an up-to-date room rather than one waiting on a clock nobody is checking.

    Answered from a LOCK-FREE read whenever possible -- this used to always take
    `_load_room`'s FOR UPDATE lock and always write back, even though the overwhelming
    majority of polls find nothing to resolve. With every connected seat polling every
    2 seconds (`ROOM_POLL`), that meant every poll serialised against every OTHER poll
    AND against a real pick submission for the same room, for no reason most of the
    time -- diagnosed as the cause of drafts intermittently freezing on a pick: a pick
    is a write that needs the lock, and if it landed while a concurrent poll already
    held it (or was queued behind one), the pick just waited.

    Safe because every WRITE path already resolves before saving: `submit_pick` calls
    `_resolve` before appending a move and again before `_save_room`, so the stored
    `status`/`moves` can only go stale by time passing with nobody writing -- exactly
    the condition `_resolve`'s own loop acts on (a pending turn's clock has run out).
    Checking that clock here, without a lock, is therefore equivalent to asking "would
    `_resolve` do anything at all" without needing the full replay to answer it. If the
    clock HASN'T run out, resolving is guaranteed to be a no-op and this returns the
    lock-free read directly. If it HAS, this escalates to a locked re-read and does the
    real resolve-and-save -- but only if the resolve actually changed something. Another
    poll may have caught the room up between this one's two reads, in which case there is
    nothing left to do and NOTHING IS WRITTEN; see the comment on that branch below for
    why that case is the common one at a timeout rather than a rare one."""
    room = _load_room(conn, code, lock=False)
    # Presence, before anything else and regardless of status: a lobby waiting on one more
    # player is exactly the case that generates no writes of its own, and is exactly the
    # case the idle sweep would otherwise delete out from under the people sitting in it.
    if room.idle_seconds > PRESENCE_HEARTBEAT_MINUTES * 60:
        _touch_room(conn, room)
    if room.status != "drafting" or time.time() - room.turn_started_at <= room.timer_seconds:
        return room
    room = _load_room(conn, code, lock=True)
    before = _mutable_state(room)
    _resolve(room, deck)
    if _mutable_state(room) == before:
        # Nothing to do after all, so nothing is written. This is the COMMON case at a
        # timeout rather than a rare one: every connected seat polls on its own 2s timer,
        # so when a turn's clock runs out they all escalate to the locked read at once and
        # queue behind each other -- but only the FIRST one through finds work. The rest
        # used to each perform a full write of identical content, serialised one after
        # another, with any real pick submitted in that window queuing behind the lot of
        # them (and able to hit `lock_timeout` and fail outright). Skipping the write does
        # not skip the lock, so this is not a correctness shortcut: the read is still
        # serialised against a concurrent writer, and a caller that DOES find work still
        # writes under the lock exactly as before.
        return room
    _save_room(conn, room)
    return room


def room_sides(room: Room, deck: Deck):
    """(player_id, RoomPlayer, order, impact) for every seat, in join order -- the raw
    material for `game.season.Side`. A drafting seat's `RoomPlayer` is returned as
    stored; a filler seat's is returned with its `.name` overridden to the historical
    squad's own display name (`SeatProgress.historical_name`, set by `replay_room`) --
    the one place that override happens, so it shows up correctly everywhere downstream
    that reads a seat's name: the draft-lobby roster AND the match-phase `Side` naming
    used in scorecards and results alike."""
    replay = replay_room(room, deck)
    out = []
    for pid, p in room.players.items():
        seat = replay.seats[pid]
        display = RoomPlayer(p.player_id, seat.historical_name, p.is_cpu) \
            if seat.historical_name else p
        out.append((pid, display, list(seat.order), seat.impact))
    return out
