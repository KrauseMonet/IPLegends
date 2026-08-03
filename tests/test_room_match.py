"""Live multiplayer rooms, match phase (`web/room_match.py`). No live Postgres -- the
same `FakeConn` stand-in `tests/test_rooms.py` already built for the draft phase, reused
directly here since the two phases share the same `rooms` table and locking contract; a
second hand-copied FakeConn would just be a second place for that SQL shape to drift.

Confirmed with the user, and what these tests are actually pinning: the TOSS WINNER
calls bat/bowl for their own side and nobody else's; the HOST decides every Impact
Player choice regardless of whose side it is; and only the HOST advances from one
match's result to the next.
"""

from __future__ import annotations

import pytest

from game.simulator import Model
from web import room_match, rooms
from tests.test_rooms import _make_room, _play_room_to_completion, conn, deck_of


def _model() -> Model:
    """A real `Model`, `.state()` overridden to a fixed distribution -- these tests are
    about the toss/Impact/advance bookkeeping around a room's matches, not the state
    grid itself, mirroring `tests/test_season.py`'s own `_model()`."""
    m = Model(dist={}, faced={}, outs={}, grid=None, costs=None,
              wide_rate=0.0, wide_runs=1.0, extras_rate=0.0,
              unrated_bat={}, season_mean={})
    probs = (0.06, 0.20, 0.30, 0.16, 0.08, 0.12, 0.02, 0.06)
    values = (-6.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    m.state = lambda over, wickets: (probs, values)
    return m


MODEL = _model()
DECK = deck_of()


@pytest.fixture(autouse=True)
def _pinned_seed(monkeypatch):
    """A genuinely random room seed can occasionally strand this tiny synthetic deck --
    `tests/test_rooms.py`'s own `test_a_shared_pool_stranding_is_caused_by_another_
    seats_earlier_pick` documents exactly this and pins a seed for the same reason.
    0 is verified by hand (swept 0-49, all clean) to complete without stranding for
    both a 2-seat 'final' and a 4-seat 'cup' room drafted entirely by humans."""
    monkeypatch.setattr(rooms.sess, "new_seed", lambda *a, **k: 0)


def _complete_final_room(conn) -> tuple[rooms.Room, str, str]:
    """A two-seat 'final' room, both seats human, drafted to completion."""
    room, host_id = _make_room(conn, "final")
    room2, guest_id = rooms.join_room(conn, room.code, "Guest", DECK)
    room = rooms.start_room(conn, room.code, host_id, DECK)
    room = _play_room_to_completion(conn, room, DECK, [host_id, guest_id])
    assert room.status == "complete", room.failure_reason
    return room, host_id, guest_id


def _find_toss_winner(conn, room, host_id, guest_id):
    """Both seats are human, so whichever wins the toss must be asked -- returns
    (winner_pid, loser_pid, replay) once the room actually pauses on a toss."""
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert replay.pending_kind == "toss"
    winner = replay.pending_toss_winner_pid
    loser = guest_id if winner == host_id else host_id
    return winner, loser, replay


# --- authorisation: the toss winner, and only the toss winner ---------------------------

def test_only_the_toss_winner_may_call_it(conn):
    room, host_id, guest_id = _complete_final_room(conn)
    winner, loser, _ = _find_toss_winner(conn, room, host_id, guest_id)
    with pytest.raises(rooms.RoomError):
        room_match.submit_toss(conn, room.code, loser, "bat", DECK, MODEL)


def test_the_toss_winner_can_call_it(conn):
    room, host_id, guest_id = _complete_final_room(conn)
    winner, loser, _ = _find_toss_winner(conn, room, host_id, guest_id)
    room_match.submit_toss(conn, room.code, winner, "bat", DECK, MODEL)
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert replay.pending_kind != "toss"


def test_an_invalid_elect_is_refused(conn):
    room, host_id, guest_id = _complete_final_room(conn)
    winner, _, _ = _find_toss_winner(conn, room, host_id, guest_id)
    with pytest.raises(rooms.RoomError):
        room_match.submit_toss(conn, room.code, winner, "sideways", DECK, MODEL)


def test_no_toss_is_pending_once_it_has_already_been_answered(conn):
    room, host_id, guest_id = _complete_final_room(conn)
    winner, _, _ = _find_toss_winner(conn, room, host_id, guest_id)
    room_match.submit_toss(conn, room.code, winner, "bat", DECK, MODEL)
    with pytest.raises(rooms.RoomError):
        room_match.submit_toss(conn, room.code, winner, "bowl", DECK, MODEL)


# --- authorisation: the host, and only the host, for Impact and for advancing -----------

def _reach_impact_or_complete(conn, room, host_id, guest_id):
    """Answers the toss (whoever actually wins it) and returns the replay -- either
    paused on 'impact' or already complete, since a 'final' room's one match has
    nothing to advance past."""
    winner, _, _ = _find_toss_winner(conn, room, host_id, guest_id)
    room_match.submit_toss(conn, room.code, winner, "bat", DECK, MODEL)
    return room_match.room_match_state(conn, room.code, DECK, MODEL)


def test_impact_is_refused_to_anyone_but_the_host(conn):
    room, host_id, guest_id = _complete_final_room(conn)
    _, replay = _reach_impact_or_complete(conn, room, host_id, guest_id)
    if replay.pending_kind != "impact":
        pytest.skip("this seed's drafted sides had no Impact decision to make")
    with pytest.raises(rooms.RoomError):
        room_match.submit_impact(conn, room.code, guest_id, None, DECK, MODEL)


def test_the_host_may_decide_impact(conn):
    """Both sides can carry an Impact Player, so up to TWO 'impact' decisions can be
    pending across a single match (home's, then away's) -- submitting one must always
    make PROGRESS (the log grows), even if a second one still follows."""
    room, host_id, guest_id = _complete_final_room(conn)
    _, replay = _reach_impact_or_complete(conn, room, host_id, guest_id)
    if replay.pending_kind != "impact":
        pytest.skip("this seed's drafted sides had no Impact decision to make")
    before = len(room_match.room_match_state(conn, room.code, DECK, MODEL)[0].match_moves)
    room_match.submit_impact(conn, room.code, host_id, None, DECK, MODEL)
    after_room, replay2 = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert len(after_room.match_moves) == before + 1
    while replay2.pending_kind == "impact":
        room_match.submit_impact(conn, room.code, host_id, None, DECK, MODEL)
        _, replay2 = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert replay2.pending_kind != "impact"


def test_impact_is_refused_when_none_is_pending(conn):
    room, host_id, guest_id = _complete_final_room(conn)
    with pytest.raises(rooms.RoomError):
        room_match.submit_impact(conn, room.code, host_id, None, DECK, MODEL)


def _make_room_and_complete_cup(conn):
    room, host_id = _make_room(conn, "cup")
    seat_ids = [host_id]
    for n in range(3):
        _, pid = rooms.join_room(conn, room.code, f"P{n}", DECK)
        seat_ids.append(pid)
    room = rooms.start_room(conn, room.code, host_id, DECK)
    room = _play_room_to_completion(conn, room, DECK, seat_ids)
    assert room.status == "complete", room.failure_reason
    return room, host_id, seat_ids


def _drain_to_advance_or_complete(conn, room, host_id):
    """Answers whatever is pending -- toss to its winner (host or not), Impact to the
    host -- until the room pauses on 'advance' or completes outright. Every seat in
    `_make_room_and_complete_cup` is human, so a toss can genuinely land on any of
    them, which is exactly the scenario `test_advance_is_refused_to_anyone_but_the_host`
    needs: the pause this function stops on is never itself the host's own toss win,
    it is the FIRST 'advance' gate after Semi-final 1."""
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    guard = 0
    while replay.pending_kind not in ("advance", None) and guard < 50:
        guard += 1
        if replay.pending_kind == "toss":
            room_match.submit_toss(conn, room.code, replay.pending_toss_winner_pid,
                                    "bat", DECK, MODEL)
        elif replay.pending_kind == "impact":
            room_match.submit_impact(conn, room.code, host_id, None, DECK, MODEL)
        _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    return replay


def test_advance_is_refused_to_anyone_but_the_host(conn):
    room, host_id, seat_ids = _make_room_and_complete_cup(conn)
    replay = _drain_to_advance_or_complete(conn, room, host_id)
    if replay.pending_kind != "advance":
        pytest.skip("this seed's cup finished (or paused again) before reaching a gate")
    non_host = next(pid for pid in seat_ids if pid != host_id)
    with pytest.raises(rooms.RoomError):
        room_match.advance_match(conn, room.code, non_host, DECK, MODEL)


def test_the_host_may_advance(conn):
    room, host_id, seat_ids = _make_room_and_complete_cup(conn)
    replay = _drain_to_advance_or_complete(conn, room, host_id)
    if replay.pending_kind != "advance":
        pytest.skip("this seed's cup finished (or paused again) before reaching a gate")
    room_match.advance_match(conn, room.code, host_id, DECK, MODEL)
    _, replay2 = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert replay2.pending_kind != "advance"


def test_advance_is_refused_when_none_is_pending(conn):
    room, host_id, guest_id = _complete_final_room(conn)
    with pytest.raises(rooms.RoomError):
        room_match.advance_match(conn, room.code, host_id, DECK, MODEL)


# --- the replay itself: results accumulate, and completion is real --------------------

def test_a_final_room_completes_once_the_toss_is_answered(conn):
    room, host_id, guest_id = _complete_final_room(conn)
    winner, _, _ = _find_toss_winner(conn, room, host_id, guest_id)
    room_match.submit_toss(conn, room.code, winner, "bat", DECK, MODEL)
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    while replay.pending_kind == "impact":
        room_match.submit_impact(conn, room.code, host_id, None, DECK, MODEL)
        _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert replay.complete
    assert len(replay.results) == 1


def test_replaying_the_same_room_twice_reconstructs_identically(conn):
    room, host_id, guest_id = _complete_final_room(conn)
    _, a = room_match.room_match_state(conn, room.code, DECK, MODEL)
    _, b = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert a.pending_kind == b.pending_kind
    assert a.pending_toss_winner_pid == b.pending_toss_winner_pid


def test_a_wrong_kind_of_move_at_the_current_position_is_rejected(conn):
    """Corrupting the match-move log directly (never reachable through the mutators
    above, which only ever append the kind the current pause is actually asking for)
    -- proves the cursor validates position-implies-kind rather than trusting it."""
    room, host_id, guest_id = _complete_final_room(conn)
    room = rooms._load_room(conn, room.code)
    room.match_moves = [{"kind": "impact", "slot": None}]   # a toss is what's expected
    rooms._save_room(conn, room)
    with pytest.raises(rooms.RoomError):
        room_match.room_match_state(conn, room.code, DECK, MODEL)
