"""Live multiplayer rooms (`web/rooms.py`). No database, no HTTP layer -- like
`tests/test_web.py`, these exercise the pure session/room logic directly; the FastAPI
routes in `web/app.py` are verified by hand against a running server, the same split
`test_web.py`'s own docstring already draws for the solo draft.

A room reuses `web.session.replay`/`etl.feasibility.run_draft` verbatim (A62's standing
rule) for each seat's own draft, so these tests are about what a room adds on top: seat
limits, the lazy per-round timer, the AFK auto-pick fallback, CPU-fill, and the one thing
that is new and was found by hand rather than by a test -- a stranded auto-pick must mark
the room `"failed"`, never crash it for every player still in it.
"""

from __future__ import annotations

import random

import pytest

from etl.feasibility import (
    IMPACT_SLOT, TWELVE_SIZE, XI_SIZE, Card, Deck, order_errors,
)
from web import rooms
from web import session as sess


def card(n: int, positions: frozenset[int], *, fs: int = 1, role: str = "batter",
         bowl: float | None = None, overseas: bool = False) -> Card:
    return Card(fs_id=fs, person_id=f"c{n}", name=f"c{n}", bat=0.5, bowl=bowl,
                role=role, overseas=overseas, career_positions=positions)


def deck_of(n_fs: int = 24, per_fs: int = 16) -> Deck:
    """Every franchise-season offers a keeper, several bowlers and a full position
    spread -- the same proven shape `tests/test_web.py` uses, so `run_draft` completes
    quickly and reliably for both a human's own replay and a CPU-fill."""
    by_fs: dict[int, list[Card]] = {}
    n = 0
    for fs in range(1, n_fs + 1):
        cards = []
        for i in range(per_fs):
            start = i % XI_SIZE
            pos = frozenset(((start + k) % XI_SIZE) + 1 for k in range(4))
            role = "keeper" if i == 0 else "batter"
            bowl = 0.1 if i % 2 == 0 else None
            overseas = i % 6 == 0
            cards.append(card(n, pos, fs=fs, role=role, bowl=bowl, overseas=overseas))
            n += 1
        by_fs[fs] = cards
    return Deck(by_fs, sorted(by_fs))


def deck_of_no_keeper(n_fs: int = 6, per_fs: int = 16) -> Deck:
    """The stranding fixture: identical shape to `deck_of` -- full position spread,
    plenty of bowlers -- except NO card anywhere is a keeper. `eligible`'s forward check
    (A73) only tests keeper coverage on the very LAST pick (`remaining_after == 0`), so a
    deck like this completes picks 1-11 completely normally -- the deal-time guarantee
    never once sees an empty candidate list -- and only reveals it is unplayable when the
    final pick is actually simulated. That gap between "looked fine for eleven picks" and
    "provably doomed on the twelfth" is exactly the live bug: `sess.replay` re-simulates
    the WHOLE draft from the seed every time (A62), so asking for the state after eleven
    moves already forces that twelfth, doomed pick and raises `InvalidState` -- even
    though no twelfth move was ever recorded."""
    by_fs: dict[int, list[Card]] = {}
    n = 0
    for fs in range(1, n_fs + 1):
        cards = []
        for i in range(per_fs):
            start = i % XI_SIZE
            pos = frozenset(((start + k) % XI_SIZE) + 1 for k in range(4))
            bowl = 0.1 if i % 2 == 0 else None
            cards.append(card(n, pos, fs=fs, role="batter", bowl=bowl, overseas=False))
            n += 1
        by_fs[fs] = cards
    return Deck(by_fs, sorted(by_fs))


DECK = deck_of()


def _open_slots(s: sess.Session) -> frozenset[int]:
    open_slots = set(range(1, XI_SIZE + 1)) | {IMPACT_SLOT}
    for i, c in enumerate(s.order):
        if c is not None:
            open_slots.discard(i + 1)
    if s.impact is not None:
        open_slots.discard(IMPACT_SLOT)
    return frozenset(open_slots)


def _make_room(fmt: str = "final", timer_seconds: int = 15) -> tuple[rooms.Room, str]:
    room, host_id = rooms.create_room(fmt, timer_seconds, "Host")
    return room, host_id


def _complete_human_draft(room: rooms.Room, player_id: str, deck: Deck) -> rooms.Room:
    """Walk one seat's own room draft to completion via `submit_pick`, exactly as a real
    player would -- picks spread across candidates (never index 0 every time, which would
    draft nothing but the fixture's one keeper, see `tests/test_web.py`'s own `_spread`)."""
    player = room.players[player_id]
    s = sess.replay(deck, player.seed, player.moves)
    made = len(player.moves)
    while s.deal is not None:
        i = (made * 7) % len(s.deal.options)
        chosen = s.deal.options[i]
        slot = min(chosen.slots & _open_slots(s))
        room = rooms.submit_pick(room.code, player_id, i, slot, deck)
        made += 1
        player = room.players[player_id]
        s = sess.replay(deck, player.seed, player.moves)
    return room


# --- lobby: creation, joining, seat limits -----------------------------------------------

def test_create_room_seats_the_host_alone():
    room, host_id = _make_room("cup")
    assert room.seats == 4
    assert list(room.players) == [host_id]
    assert not room.full
    assert room.status == "lobby"


def test_create_room_refuses_an_unknown_format_or_timer():
    with pytest.raises(rooms.RoomError):
        rooms.create_room("triangular", 15, "Host")
    with pytest.raises(rooms.RoomError):
        rooms.create_room("final", 20, "Host")


def test_join_room_fills_seats_until_full_then_refuses():
    room, host_id = _make_room("final")
    room2, bob_id = rooms.join_room(room.code, "Bob")
    assert room2.full
    with pytest.raises(rooms.RoomError):
        rooms.join_room(room.code, "Carol")


def test_join_room_refuses_once_the_draft_has_started():
    room, host_id = _make_room("final")
    rooms.join_room(room.code, "Bob")
    rooms.start_room(room.code, host_id, DECK)
    with pytest.raises(rooms.RoomError):
        rooms.join_room(room.code, "Carol")


def test_each_seat_gets_an_independent_stable_seed():
    """Two different player ids must not collide on the same per-seat seed -- a room-wide
    stable hash, not the room seed reused verbatim (A62's determinism, extended per seat)."""
    room, host_id = _make_room("final")
    _, bob_id = rooms.join_room(room.code, "Bob")
    host_seed = room.players[host_id].seed
    bob_seed = room.players[bob_id].seed
    assert host_seed != bob_seed
    assert rooms._player_seed(room.seed, host_id) == host_seed, "must be reproducible"


# --- start: host-only, CPU fills the remaining seats --------------------------------------

def test_start_room_is_host_only():
    room, host_id = _make_room("final")
    _, bob_id = rooms.join_room(room.code, "Bob")
    with pytest.raises(rooms.RoomError):
        rooms.start_room(room.code, bob_id, DECK)


def test_start_room_fills_remaining_seats_with_a_complete_legal_cpu_squad():
    room, host_id = _make_room("cup")   # 4 seats, host is the only human
    room = rooms.start_room(room.code, host_id, DECK)
    assert room.status == "drafting"
    cpu_players = [p for p in room.players.values() if p.is_cpu]
    assert len(cpu_players) == 3
    for p in cpu_players:
        assert p.done
        assert len(p.order) == XI_SIZE and all(c is not None for c in p.order)
        assert p.impact is not None
        twelve = [c for c in p.order if c] + [p.impact]
        assert order_errors(list(p.order), p.impact, twelve) == []


# --- the live round: lazy timer resolution -------------------------------------------------

def test_the_round_does_not_advance_before_the_timer_expires(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(rooms.time, "monotonic", lambda: clock["t"])

    room, host_id = _make_room("final", timer_seconds=15)
    _, bob_id = rooms.join_room(room.code, "Bob")
    room = rooms.start_room(room.code, host_id, DECK)
    assert room.round == 0

    clock["t"] += 5   # well inside the 15s window
    room = rooms.room_state(room.code, DECK)
    assert room.round == 0
    assert room.status == "drafting"
    assert not room.players[host_id].done and not room.players[bob_id].done


def test_the_timer_auto_picks_the_lowest_rated_eligible_candidate_for_whoever_is_pending(
    monkeypatch,
):
    clock = {"t": 1000.0}
    monkeypatch.setattr(rooms.time, "monotonic", lambda: clock["t"])

    room, host_id = _make_room("final", timer_seconds=15)
    _, bob_id = rooms.join_room(room.code, "Bob")
    room = rooms.start_room(room.code, host_id, DECK)

    host = room.players[host_id]
    before = sess.replay(DECK, host.seed, host.moves)
    assert before.deal is not None
    worst = min(before.deal.options, key=lambda c: c.rating)

    clock["t"] += 16   # past the 15s window; nobody has picked
    room = rooms.room_state(room.code, DECK)

    assert room.round == 1, "the round must advance once every pending seat is resolved"
    host = room.players[host_id]
    assert len(host.moves) == 1
    picked_move = host.moves[0]
    picked_card = before.deal.options[picked_move.index]
    assert picked_card.person_id == worst.person_id, (
        "the auto-pick must take the LOWEST-rated eligible candidate, not the best"
    )
    assert picked_move.slot in picked_card.slots


def test_an_auto_pick_never_touches_a_seat_that_already_picked(monkeypatch):
    """One human picks in time, the other lets the clock run out -- only the AFK seat
    should be auto-assigned; the on-time pick must survive untouched."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(rooms.time, "monotonic", lambda: clock["t"])

    room, host_id = _make_room("final", timer_seconds=15)
    _, bob_id = rooms.join_room(room.code, "Bob")
    room = rooms.start_room(room.code, host_id, DECK)

    host = room.players[host_id]
    s = sess.replay(DECK, host.seed, host.moves)
    chosen = s.deal.options[0]
    slot = min(chosen.slots & _open_slots(s))
    room = rooms.submit_pick(room.code, host_id, 0, slot, DECK)
    host_moves_after_own_pick = room.players[host_id].moves

    clock["t"] += 16
    room = rooms.room_state(room.code, DECK)

    assert room.players[host_id].moves == host_moves_after_own_pick, (
        "a seat that already picked this round must not be auto-picked over"
    )
    assert len(room.players[bob_id].moves) == 1, "the AFK seat must have been resolved"


def test_a_cpu_seat_is_never_treated_as_pending(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(rooms.time, "monotonic", lambda: clock["t"])

    room, host_id = _make_room("cup", timer_seconds=15)
    room = rooms.start_room(room.code, host_id, DECK)   # 3 CPU seats fill in

    clock["t"] += 16
    room = rooms.room_state(room.code, DECK)
    assert room.round == 1
    assert all(p.done for p in room.players.values() if p.is_cpu)


# --- the regression: a stranded auto-pick must fail the room, never crash it ---------------

def test_a_room_that_strands_on_auto_pick_is_marked_failed_not_raised(monkeypatch):
    """The bug, reproduced directly. `deck_of_no_keeper` completes picks 1-11 without ever
    showing an empty deal (the forward check's own optimism -- A73), and only the twelfth,
    unavoidably-doomed pick reveals there is no keeper anywhere in the archive. Fetching
    the state after those eleven moves therefore raises `sess.InvalidState` on its own --
    exactly what `_auto_pick` does when the timer expires on this seat's stuck round.
    `_resolve` must catch that and mark the room failed rather than let it propagate, which
    is precisely what a bare `room_state`/`submit_pick` call used to do before this fix."""
    deck = deck_of_no_keeper()

    clock = {"t": 1000.0}
    monkeypatch.setattr(rooms.time, "monotonic", lambda: clock["t"])
    room, host_id = _make_room("final", timer_seconds=15)
    _, bob_id = rooms.join_room(room.code, "Bob")
    room = rooms.start_room(room.code, host_id, deck)
    host_seed = room.players[host_id].seed

    # Build the eleven-move corner against THIS seat's own per-room seed, not an arbitrary
    # one -- a room derives each seat's seed from `_player_seed` (A62 extended per seat),
    # so the moves have to be walked against the exact seed they will later be replayed
    # with, or they resolve to a different deal sequence entirely.
    moves: tuple = ()
    s = sess.replay(deck, host_seed, ())
    for made in range(TWELVE_SIZE - 2):   # ten picks, each confirmed still paused
        i = (made * 3) % len(s.deal.options)
        chosen = s.deal.options[i]
        slot = min(chosen.slots & _open_slots(s))
        moves = moves + (sess.Pick(i, slot),)
        s = sess.replay(deck, host_seed, moves)
        assert s.deal is not None, "ten picks must still be short of a completed twelve"

    # The eleventh move is recorded WITHOUT replaying it here -- that replay call is
    # exactly the one that discovers the doom, which is the point of the next assertion.
    i = 0 % len(s.deal.options)
    chosen = s.deal.options[i]
    slot = min(chosen.slots & _open_slots(s))
    moves = moves + (sess.Pick(i, slot),)

    # Confirmed: the state after eleven moves is already unrecoverable on its own.
    with pytest.raises(sess.InvalidState):
        sess.replay(deck, host_seed, moves)

    # Engineer the same corner directly rather than replaying all ten safe rounds through
    # `submit_pick` for both seats -- `round` must stay in lockstep with how many moves
    # are actually recorded (that invariant is `_resolve`'s own, not just bookkeeping),
    # so both are set together.
    room.players[host_id].moves = moves
    room.round = len(moves)
    room.round_started_at = clock["t"]

    clock["t"] += 16
    room = rooms.room_state(room.code, deck)   # must not raise

    assert room.status == "failed"
    assert room.failure_reason and "stranded" in room.failure_reason

    # And the room stays gracefully failed on every subsequent call, rather than being a
    # one-shot recovery that crashes again the next time round.
    room_again = rooms.room_state(room.code, deck)
    assert room_again.status == "failed"


def test_submit_pick_refuses_a_move_that_would_strand_the_draft_rather_than_recording_it():
    """The safety net that already existed and already worked for a HUMAN's own attempt --
    kept here as a sanity check so the two paths (a player's own bad pick vs. the timeout
    auto-pick on the same corner) are both pinned in one file. The eleventh pick is the one
    that discovers the doom (see the room-level test above), so THAT specific `submit_pick`
    call must be refused with a `RoomError` -- never recorded, never left to crash the room.

    Both seats are human and pick every round in lockstep -- a `final` room's other seat
    can't be CPU-filled here, since `start_room`'s own CPU draft against this same
    keeperless deck would fail for exactly the same reason this test is about."""
    deck = deck_of_no_keeper()
    room, host_id = _make_room("final", timer_seconds=15)
    _, bob_id = rooms.join_room(room.code, "Bob")
    room = rooms.start_room(room.code, host_id, deck)

    for made in range(TWELVE_SIZE - 2):   # ten safe rounds, both seats
        for pid in (host_id, bob_id):
            player = room.players[pid]
            s = sess.replay(deck, player.seed, player.moves)
            i = (made * 3) % len(s.deal.options)
            chosen = s.deal.options[i]
            slot = min(chosen.slots & _open_slots(s))
            room = rooms.submit_pick(room.code, pid, i, slot, deck)

    host = room.players[host_id]
    s = sess.replay(deck, host.seed, host.moves)
    i = 0 % len(s.deal.options)
    chosen = s.deal.options[i]
    slot = min(chosen.slots & _open_slots(s))
    with pytest.raises(rooms.RoomError):
        rooms.submit_pick(room.code, host_id, i, slot, deck)

    # And the refusal must not have half-recorded the move or corrupted the room.
    room = rooms._get(room.code)
    assert room.status == "drafting"
    assert len(room.players[host_id].moves) == TWELVE_SIZE - 2


# --- room_sides: what each format's match is built from ------------------------------------

@pytest.mark.parametrize("fmt", ["final", "cup", "league"])
def test_room_sides_gives_every_seat_a_legal_twelve(fmt):
    room, host_id = _make_room(fmt, timer_seconds=15)
    room = rooms.start_room(room.code, host_id, DECK)   # host + CPU-filled rest
    room = _complete_human_draft(room, host_id, DECK)
    sides = rooms.room_sides(room, DECK)
    assert len(sides) == rooms.ROOM_FORMATS[fmt]
    for pid, player, order, impact in sides:
        assert len(order) == XI_SIZE
        twelve = [c for c in order if c] + ([impact] if impact else [])
        assert order_errors(order, impact, twelve) == [], f"seat {pid} is not a legal twelve"
