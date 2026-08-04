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
    1 is verified by hand (swept 0-29) to complete without stranding for a 2-seat
    'final', a 4-seat 'cup' AND a 10-seat 'league' room drafted entirely by humans --
    0 works for the first two but strands the ten-seat league, so it is 1 across the
    whole file rather than a second, format-specific seed."""
    monkeypatch.setattr(rooms.sess, "new_seed", lambda *a, **k: 1)


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


def _drive_room_to_completion(conn, room, host_id):
    """Answers whatever is pending, one decision at a time, until the whole match phase
    completes -- toss to its actual winner, Impact and advance to the host. A guard
    generous enough for a ten-seat league (70 league fixtures + up to 4 playoff ones,
    each contributing at most a toss, up to two Impact decisions and an advance)."""
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    guard = 0
    while not replay.complete and guard < 1000:
        guard += 1
        if replay.pending_kind == "toss":
            room_match.submit_toss(conn, room.code, replay.pending_toss_winner_pid,
                                    "bat", DECK, MODEL)
        elif replay.pending_kind == "impact":
            room_match.submit_impact(conn, room.code, host_id, None, DECK, MODEL)
        elif replay.pending_kind == "advance":
            room_match.advance_match(conn, room.code, host_id, DECK, MODEL)
        else:
            raise AssertionError(f"not complete but nothing pending: {replay}")
        _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert replay.complete, f"did not complete within {guard} steps"
    return replay


def test_a_league_room_plays_the_round_robin_straight_through_to_the_playoffs(conn):
    """Two human seats, eight CPU -- a realistic room, not the worst case. The
    round-robin plays NON-interactively (see room_match.py's own comment on why: it is
    a measured architectural ceiling, not a style choice), so this is the ONE call that
    matters for proving it -- seventy fixtures resolve in a single `replay_room_matches`
    call, with a well-formed table and the dynamically-built Qualifier 1 already
    pending. Finishing the four playoff matches too (each interactive, each re-
    replaying the whole round-robin from scratch per A62) is NOT done here: it would
    only re-prove what the 'cup' tests already cover in isolation, at real extra cost
    for no new coverage."""
    room, host_id = _make_room(conn, "league")
    _, guest_id = rooms.join_room(conn, room.code, "Guest", DECK)
    seat_ids = [host_id, guest_id]
    room = rooms.start_room(conn, room.code, host_id, DECK)
    room = _play_room_to_completion(conn, room, DECK, seat_ids)
    assert room.status == "complete", room.failure_reason

    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert len(replay.results) == 70             # game.season.fixtures(10)
    assert all(e.stage == "league" for e in replay.results)
    assert replay.table is not None and len(replay.table) == 10
    assert sum(row.standing.played for row in replay.table) == 70 * 2
    # The round-robin -> playoffs transition is the one host-paced gate in this format
    # (see room_match.py's own comment); Qualifier 1's fixture is only built AFTER the
    # host advances past it, so what should be pending here is that gate itself.
    assert replay.pending_kind == "advance"
    assert replay.pending_stage == "league"

    room2 = room_match.advance_match(conn, room.code, host_id, DECK, MODEL)
    _, replay2 = room_match.room_match_state(conn, room2.code, DECK, MODEL)
    assert replay2.pending_stage == "Qualifier 1"
    first, second = replay.table[0].pid, replay.table[1].pid
    assert {replay2.pending_a_pid, replay2.pending_b_pid} == {first, second}


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


# --- room_journey: the journey card's own numbers, one seat at a time -------------------

def test_room_journey_is_none_before_the_match_phase_completes(conn):
    room, host_id, guest_id = _complete_final_room(conn)
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert not replay.complete
    assert room_match.room_journey(room, replay, host_id) is None


def test_room_journey_is_none_for_a_pid_not_seated_in_the_room(conn):
    room, host_id, guest_id = _complete_final_room(conn)
    replay = _drain_final_to_completion(conn, room, host_id, guest_id)
    assert room_match.room_journey(room, replay, "not-a-real-seat") is None


def _drain_final_to_completion(conn, room, host_id, guest_id):
    winner, _, _ = _find_toss_winner(conn, room, host_id, guest_id)
    room_match.submit_toss(conn, room.code, winner, "bat", DECK, MODEL)
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    while replay.pending_kind == "impact":
        room_match.submit_impact(conn, room.code, host_id, None, DECK, MODEL)
        _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert replay.complete
    return replay


def test_room_journey_records_exactly_one_played_match_for_a_final(conn):
    room, host_id, guest_id = _complete_final_room(conn)
    replay = _drain_final_to_completion(conn, room, host_id, guest_id)
    host_journey = room_match.room_journey(room, replay, host_id)
    guest_journey = room_match.room_journey(room, replay, guest_id)
    assert host_journey.played == 1 and guest_journey.played == 1
    # exactly one of the two actually won (a tie is possible in principle but this
    # seed's own drafted sides, verified by hand, do not produce one)
    assert host_journey.won + guest_journey.won == 1
    assert host_journey.lost + guest_journey.lost == 1
    winner_journey = host_journey if host_journey.won else guest_journey
    assert winner_journey.champion
    loser_journey = guest_journey if host_journey.won else host_journey
    assert not loser_journey.champion


def test_room_journey_accumulator_totals_reconcile_with_the_reported_runs(conn):
    """`journey.runs`/`journey.wickets` are folded from `journey.acc` at the moment
    `room_journey` builds it -- this pins that they can never quietly drift apart."""
    room, host_id, guest_id = _complete_final_room(conn)
    replay = _drain_final_to_completion(conn, room, host_id, guest_id)
    journey = room_match.room_journey(room, replay, host_id)
    assert journey.runs == sum(journey.acc.runs.values())
    assert journey.wickets == sum(journey.acc.wickets.values())
    assert journey.top_scorer[1] <= journey.runs
    assert journey.top_wicket_taker[1] <= journey.wickets


def test_room_journey_accumulates_across_every_match_a_seat_actually_played(conn):
    """A cup finalist plays TWO matches (semi + final); a seat eliminated in the semis
    plays only one. Summed across all four seats, total `played` must be exactly
    2 x 3 (three matches, two participants each) -- proving no match is double-counted
    or dropped for any seat, win or lose."""
    room, host_id, seat_ids = _make_room_and_complete_cup(conn)
    replay = _drive_room_to_completion(conn, room, host_id)
    assert replay.complete
    journeys = [room_match.room_journey(room, replay, pid) for pid in seat_ids]
    assert all(j is not None for j in journeys)
    assert sum(j.played for j in journeys) == 2 * 3
    # The champion played the final, so at least two matches; nobody else can have
    # played more than two either (semi + final is the whole bracket's depth).
    champion_journeys = [j for j in journeys if j.champion]
    assert len(champion_journeys) == 1
    assert champion_journeys[0].played == 2
    assert all(j.played in (1, 2) for j in journeys)
