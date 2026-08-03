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
clock has actually expired (auto-picking the lowest-rated eligible candidate via
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
"""

from __future__ import annotations

import random
import secrets
import string
import time
from dataclasses import dataclass, field

from psycopg.types.json import Json

from etl.feasibility import (
    ALL_SLOTS, IMPACT_SLOT, POLICIES, REDRAW_CAP, TWELVE_SIZE, XI_SIZE, Card, Deck,
    DraftState, eligible, pick_afk,
)
from web import session as sess

ROOM_FORMATS = {"final": 2, "cup": 4, "league": 10}
TIMER_CHOICES = (15, 30, 45)
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
        counter would, and a stored one could drift from the log it's supposed to track)."""
        return len(self.moves) // len(self.players) if self.players else 0


# A room is a short-lived social thing; nothing here schedules a sweep, it just happens
# to run whenever someone next starts a new one (A62's "resolve on read" stance, extended
# to cleanup).
ROOM_TTL_HOURS = 24


def _sweep_stale_rooms(conn) -> None:
    conn.execute(
        "delete from rooms where created_at < now() - make_interval(hours => %s)",
        (ROOM_TTL_HOURS,),
    )


def _new_code(conn) -> str:
    while True:
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
        if conn.execute("select 1 from rooms where code = %s", (code,)).fetchone() is None:
            return code


def _load_room(conn, code: str) -> Room:
    """Locks the room row for the rest of the caller's transaction -- every public
    function below pairs this with a `_save_room` before the connection commits, so two
    requests touching the same room serialise through this lock rather than racing."""
    row = conn.execute(
        """
        select code, format, timer_seconds, seed, host_id, status,
               turn_started_at, failure_reason, moves, match_moves
          from rooms where code = %s for update
        """,
        (code,),
    ).fetchone()
    if row is None:
        raise RoomError(f"no room {code!r}")
    (code, fmt, timer_seconds, seed, host_id, status,
     turn_started_at, failure_reason, moves, match_moves) = row
    room = Room(code=code, format=fmt, timer_seconds=timer_seconds, seed=seed,
                host_id=host_id, status=status,
                turn_started_at=turn_started_at or 0.0, failure_reason=failure_reason,
                moves=list(moves or []), match_moves=list(match_moves or []))
    for player_id, name, is_cpu in conn.execute(
        "select player_id, name, is_cpu from room_players "
        "where room_code = %s order by seat_order",
        (code,),
    ):
        room.players[player_id] = RoomPlayer(player_id, name, is_cpu)
    return room


def _save_room(conn, room: Room) -> None:
    conn.execute(
        """
        insert into rooms (code, format, timer_seconds, seed, host_id, status,
                            turn_started_at, failure_reason, moves, match_moves)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (code) do update set
            status = excluded.status, turn_started_at = excluded.turn_started_at,
            failure_reason = excluded.failure_reason, moves = excluded.moves,
            match_moves = excluded.match_moves
        """,
        (room.code, room.format, room.timer_seconds, room.seed, room.host_id,
         room.status, room.turn_started_at, room.failure_reason, Json(room.moves),
         Json(room.match_moves)),
    )
    for seat_order, (player_id, p) in enumerate(room.players.items()):
        conn.execute(
            """
            insert into room_players (room_code, player_id, seat_order, name, is_cpu)
            values (%s, %s, %s, %s, %s)
            on conflict (room_code, player_id) do nothing
            """,
            (room.code, player_id, seat_order, p.name, p.is_cpu),
        )


def create_room(conn, fmt: str, timer_seconds: int, host_name: str) -> tuple[Room, str]:
    if fmt not in ROOM_FORMATS:
        raise RoomError(f"unknown format {fmt!r}: choose one of {sorted(ROOM_FORMATS)}")
    if timer_seconds not in TIMER_CHOICES:
        raise RoomError(f"timer must be one of {TIMER_CHOICES}")
    _sweep_stale_rooms(conn)
    room_seed = sess.new_seed()
    host_id = secrets.token_urlsafe(8)
    room = Room(code=_new_code(conn), format=fmt, timer_seconds=timer_seconds,
                seed=room_seed, host_id=host_id)
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
    every other seat's against one shared `taken` set."""

    order: list = field(default_factory=lambda: [None] * XI_SIZE)
    impact: Card | None = None
    picks: list = field(default_factory=list)
    open_slots: set = field(default_factory=lambda: set(ALL_SLOTS))
    keeper_have: bool = False
    bowl_have: int = 0
    overseas_taken: int = 0

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
    `etl.feasibility`, unmodified) against whichever seat's turn it is and the shared
    `taken` set. After the recorded history, deals the next pending turn the same way,
    which is what becomes visible (options included, to that seat only) or hidden
    (franchise/season only, to everyone else) at the API layer.
    """
    seat_ids = list(room.players)
    n = len(seat_ids)
    rng = random.Random(room.seed)
    taken: set[str] = set()
    seats = {pid: SeatProgress() for pid in seat_ids}

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
        pid = seat_ids[turn_seat_index(move_no, n)]
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

    pid = seat_ids[turn_seat_index(len(room.moves), n)]
    dealt = _deal_for(pid)
    if dealt is None:
        return RoomReplay(seats, complete=False, stranded=True, pending_seat_id=pid)
    return RoomReplay(seats, complete=False, pending_seat_id=pid, pending_deal=dealt)


def _resolve_cpu_turn(replay: RoomReplay) -> dict:
    """A CPU's pick, computed exactly once, at the moment its turn arrives -- see this
    module's own docstring for why that is now a deliberate departure from A19 rather
    than a recomputable derivation of (deck, seed) alone."""
    pid = replay.pending_seat_id
    fs_id, candidates = replay.pending_deal
    seat = replay.seats[pid]
    state = DraftState(
        picks=tuple(seat.picks), order=tuple(seat.order), impact=seat.impact,
        open_slots=frozenset(seat.open_slots), keeper_have=seat.keeper_have,
        bowl_have=seat.bowl_have, remaining=TWELVE_SIZE - len(seat.picks),
    )
    card, slot = POLICIES["rational"](candidates, state, random.Random())
    index = next(i for i, c in enumerate(candidates) if c.person_id == card.person_id)
    return {"seat": pid, "index": index, "slot": slot}


def _resolve(room: Room, deck: Deck) -> None:
    """Lazily catch the room up to the present: resolve every CPU turn at the front of
    the queue instantly, and auto-pick (via `pick_afk`) for whoever's turn has actually
    timed out, before doing anything else. Called at the top of every room route, so no
    background scheduler is needed (A62).

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
        if room.players[pid].is_cpu:
            room.moves = room.moves + [_resolve_cpu_turn(replay)]
            room.turn_started_at = time.time()
            continue

        if time.time() - room.turn_started_at <= room.timer_seconds:
            return  # still within this seat's own window

        fs_id, candidates = replay.pending_deal
        seat = replay.seats[pid]
        state = DraftState(
            picks=tuple(seat.picks), order=tuple(seat.order), impact=seat.impact,
            open_slots=frozenset(seat.open_slots), keeper_have=seat.keeper_have,
            bowl_have=seat.bowl_have, remaining=TWELVE_SIZE - len(seat.picks),
        )
        card, slot = pick_afk(candidates, state, random.Random())
        index = next(i for i, c in enumerate(candidates) if c.person_id == card.person_id)
        room.moves = room.moves + [{"seat": pid, "index": index, "slot": slot}]
        room.turn_started_at = time.time()


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


def room_state(conn, code: str, deck: Deck) -> Room:
    """Read-only poll: resolve any expired turn first, so a client that polls slowly
    still sees an up-to-date room rather than one waiting on a clock nobody is checking."""
    room = _load_room(conn, code)
    _resolve(room, deck)
    _save_room(conn, room)
    return room


def room_sides(room: Room, deck: Deck):
    """(player_id, RoomPlayer, order, impact) for every seat, in join order -- the raw
    material for `game.season.Side`. CPU and human seats are indistinguishable here now
    -- both are read uniformly off one `replay_room` call, since a CPU's progress is
    exactly as much a product of the shared move log as a human's."""
    replay = replay_room(room, deck)
    return [(pid, p, list(replay.seats[pid].order), replay.seats[pid].impact)
            for pid, p in room.players.items()]
