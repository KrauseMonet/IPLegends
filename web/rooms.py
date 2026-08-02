"""Live multiplayer rooms: friends draft together, live, turn by turn.

[In-memory, v1 -- ratified with the user over a Neon table.] Rooms live in a plain
process-lifetime dict, not a database table. SPEC 11's "no storage, no accounts, no
migration" bias is extended here rather than broken: a room is a short-lived social
thing, and the cost of that choice (a server restart loses every open room) is one the
user chose to accept for the simplicity it buys.

A room does not reimplement the draft. Each seat's own twelve picks replay through the
EXACT SAME `etl.feasibility.run_draft` / `web.session.replay` machinery the solo draft
uses (A62's standing rule) -- a room only orchestrates WHEN a seat's next move is
accepted, WHO can see what live, and what happens when a seat's clock runs out. This is
also why a room never needs to validate a pick's legality itself: `sess.replay` already
does, by replaying the seat's own move list against the real deck.

Round by round, not a shared pool. Every seat is dealt its OWN independent franchise-
season each round -- derived from a per-seat seed, itself a stable hash of the room's
seed and the seat's id -- so nobody ever competes for the same card and there is no
locking to design around, only a per-round DEADLINE. The deadline is resolved LAZILY:
whichever request next touches the room checks whether time is up and, if so, auto-picks
(the lowest-rated eligible candidate) for whoever is still waiting before doing anything
else. Nothing runs in the background -- this mirrors how replay itself resolves
everything on read rather than needing a scheduler (A62).

Rerolls are deliberately NOT part of a room draft in this pass -- out of scope for what
the user asked for here, and the per-round timer already gives a natural (if less
forgiving) answer to "I don't like this deal": pick anyway, or let the clock supply the
worst option for you.

A CPU-filled seat is drafted ONCE, instantly, in full (`run_draft` with the `rational`
policy) the moment the host starts the room -- there is no "round by round" experience to
give a CPU, and nobody is watching it deliberate.
"""

from __future__ import annotations

import hashlib
import random
import secrets
import string
import time
from dataclasses import dataclass, field

from etl.feasibility import (
    IMPACT_SLOT, POLICIES, TWELVE_SIZE, XI_SIZE, Card, Deck, choose_slot, run_draft,
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
    player_id: str
    name: str
    is_cpu: bool
    seed: int
    moves: tuple = ()                  # human seats: recorded Picks, one per round
    order: list | None = None          # CPU seats only: precomputed final order
    impact: Card | None = None         # CPU seats only: precomputed Impact

    @property
    def done(self) -> bool:
        if self.is_cpu:
            return True
        return len(self.moves) >= TWELVE_SIZE


@dataclass
class Room:
    code: str
    format: str
    timer_seconds: int
    seed: int
    host_id: str
    players: dict = field(default_factory=dict)   # player_id -> RoomPlayer, join order
    status: str = "lobby"          # "lobby" | "drafting" | "complete" | "failed"
    round: int = 0
    round_started_at: float = 0.0
    failure_reason: str | None = None

    @property
    def seats(self) -> int:
        return ROOM_FORMATS[self.format]

    @property
    def full(self) -> bool:
        return len(self.players) >= self.seats


ROOMS: dict[str, Room] = {}


def _new_code() -> str:
    while True:
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
        if code not in ROOMS:
            return code


def _player_seed(room_seed: int, player_id: str) -> int:
    """A stable per-seat draft seed -- NOT Python's own `hash()`, which is randomised per
    process for strings and would make a room's own replay irreproducible across a
    restart or a second worker."""
    digest = hashlib.sha256(f"{room_seed}:{player_id}".encode()).hexdigest()
    return int(digest, 16) % 1_000_000_000


def _get(code: str) -> Room:
    room = ROOMS.get(code)
    if room is None:
        raise RoomError(f"no room {code!r}")
    return room


def create_room(fmt: str, timer_seconds: int, host_name: str) -> tuple[Room, str]:
    if fmt not in ROOM_FORMATS:
        raise RoomError(f"unknown format {fmt!r}: choose one of {sorted(ROOM_FORMATS)}")
    if timer_seconds not in TIMER_CHOICES:
        raise RoomError(f"timer must be one of {TIMER_CHOICES}")
    room_seed = sess.new_seed()
    host_id = secrets.token_urlsafe(8)
    room = Room(code=_new_code(), format=fmt, timer_seconds=timer_seconds,
                seed=room_seed, host_id=host_id)
    room.players[host_id] = RoomPlayer(host_id, host_name, is_cpu=False,
                                        seed=_player_seed(room_seed, host_id))
    ROOMS[room.code] = room
    return room, host_id


def join_room(code: str, name: str) -> tuple[Room, str]:
    room = _get(code)
    if room.status != "lobby":
        raise RoomError("this room has already started")
    if room.full:
        raise RoomError("this room is full")
    player_id = secrets.token_urlsafe(8)
    room.players[player_id] = RoomPlayer(player_id, name, is_cpu=False,
                                          seed=_player_seed(room.seed, player_id))
    return room, player_id


def start_room(code: str, player_id: str, deck: Deck) -> Room:
    room = _get(code)
    if player_id != room.host_id:
        raise RoomError("only the host can start the draft")
    if room.status != "lobby":
        raise RoomError("this room has already started")

    n = 0
    while len(room.players) < room.seats:
        cpu_id = f"__cpu_{n}__"
        n += 1
        cpu_seed = _player_seed(room.seed, cpu_id)
        result = run_draft(deck, POLICIES["rational"], random.Random(cpu_seed))
        if not result.completed:
            raise RoomError("could not draft a CPU squad against this deck")
        room.players[cpu_id] = RoomPlayer(
            cpu_id, f"CPU {n}", is_cpu=True, seed=cpu_seed,
            order=result.order, impact=result.impact,
        )

    room.status = "drafting"
    room.round = 0
    room.round_started_at = time.monotonic()
    return room


# --- the live round -----------------------------------------------------------------

def _open_slots(s: sess.Session) -> set[int]:
    open_slots = set(range(1, XI_SIZE + 1)) | {IMPACT_SLOT}
    for i, c in enumerate(s.order):
        if c is not None:
            open_slots.discard(i + 1)
    if s.impact is not None:
        open_slots.discard(IMPACT_SLOT)
    return open_slots


def _auto_pick(player: RoomPlayer, deck: Deck) -> None:
    """The timeout fallback: the lowest-rated eligible candidate from THIS seat's own
    already-dealt, already-eligible-filtered squad -- never a re-deal, and never a better
    outcome than picking in time would have been."""
    s = sess.replay(deck, player.seed, player.moves)
    if s.deal is None:
        return  # already complete; nothing to resolve
    worst = min(s.deal.options, key=lambda c: c.rating)
    slot = choose_slot(worst, frozenset(_open_slots(s)))
    index = next(i for i, c in enumerate(s.deal.options) if c.person_id == worst.person_id)
    player.moves = player.moves + (sess.Pick(index, slot),)


def _resolve(room: Room, deck: Deck) -> None:
    """Lazily catch the room up to the present: advance past any round every human has
    already picked for, and auto-pick past any round whose timer has actually expired.
    Called at the top of every room route, so no background scheduler is needed (A62).

    A room CAN genuinely strand -- the same wildcard-optimism gap A73 documents for the
    solo draft (the forward check is an OPTIMISTIC bound, not a guarantee: with one pick
    left it assumes a hypothetical future pick can be a keeper AND a bowler AND eligible
    at whatever slot remains, and real archive data does not always supply one). An
    auto-picked lowest-rated candidate on an earlier round is exactly the kind of choice
    that can walk into this corner. When it happens there is no undo (final at pick time,
    A73) and no reroll (out of scope for rooms) to recover with, so the room is marked
    `"failed"` and stops resolving -- reported to every seat, never a crash that leaves
    the room permanently 500-ing on every future request.
    """
    while room.status == "drafting":
        pending = [p for p in room.players.values() if not p.done and not p.is_cpu
                   and len(p.moves) <= room.round]
        if pending and time.monotonic() - room.round_started_at <= room.timer_seconds:
            return  # still within the window; nothing to resolve yet
        for p in pending:
            try:
                _auto_pick(p, deck)
            except sess.InvalidState as exc:
                room.status = "failed"
                room.failure_reason = str(exc)
                return
        room.round += 1
        room.round_started_at = time.monotonic()
        if room.round >= TWELVE_SIZE:
            room.status = "complete"
            return


def submit_pick(code: str, player_id: str, index: int, slot: int, deck: Deck) -> Room:
    room = _get(code)
    _resolve(room, deck)
    if room.status != "drafting":
        raise RoomError(f"this room is not drafting (status: {room.status})")
    player = room.players.get(player_id)
    if player is None:
        raise RoomError("you are not seated in this room")
    if player.is_cpu:
        raise RoomError("a CPU seat cannot be picked for")
    if len(player.moves) > room.round:
        raise RoomError("you have already picked for this round")
    new_moves = player.moves + (sess.Pick(index, slot),)
    try:
        sess.replay(deck, player.seed, new_moves)   # validates the move against the deck
    except sess.InvalidState as exc:
        raise RoomError(str(exc)) from exc
    player.moves = new_moves
    _resolve(room, deck)   # this pick may have been the last one the round was waiting on
    return room


def room_state(code: str, deck: Deck) -> Room:
    """Read-only poll: resolve any expired round first, so a client that polls slowly
    still sees an up-to-date room rather than one waiting on a clock nobody is checking."""
    room = _get(code)
    _resolve(room, deck)
    return room


def player_session(player: RoomPlayer, deck: Deck) -> sess.Session | None:
    """A human seat's current session (deal, order, errors) -- `None` for a CPU seat,
    which has no live deal to show, being complete from the moment the room started."""
    if player.is_cpu:
        return None
    return sess.replay(deck, player.seed, player.moves)


def room_sides(room: Room, deck: Deck):
    """(player_id, order, impact) for every seat, in join order -- the raw material for
    `game.season.Side`, built the same way regardless of whether a seat is human (via
    replay) or CPU (already precomputed at start)."""
    out = []
    for pid, p in room.players.items():
        if p.is_cpu:
            out.append((pid, p, list(p.order), p.impact))
        else:
            s = sess.replay(deck, p.seed, p.moves)
            out.append((pid, p, list(s.order), s.impact))
    return out
