"""Live multiplayer rooms, match phase (`web/room_match.py`). No live Postgres -- the
same `FakeConn` stand-in `tests/test_rooms.py` already built for the draft phase, reused
directly here since the two phases share the same `rooms` table and locking contract; a
second hand-copied FakeConn would just be a second place for that SQL shape to drift.

Confirmed with the user, and what these tests are actually pinning: the TOSS WINNER
calls bat/bowl for their own side and nobody else's; Impact Player substitutions are
always automatic (`game.season.decide_impact`, no host choice at all any more); only
the HOST advances a ROUND to the next; independent fixtures within one round (a cup's
two semis, a league's Qualifier 1 + Eliminator) resolve in parallel, not one at a time;
and a player's own journey stats are available the moment THEIR tournament ends, not
only once the whole room finishes.
"""

from __future__ import annotations

import pytest

from game.simulator import Model
from web import room_match, rooms
from tests.test_rooms import _make_room, _play_room_to_completion, conn, deck_of


def _model() -> Model:
    """A real `Model`, `.state()` overridden to a fixed distribution -- these tests are
    about the toss/advance/elimination bookkeeping around a room's matches, not the
    state grid itself, mirroring `tests/test_season.py`'s own `_model()`."""
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


def _find_pending_toss(replay):
    """`(stage, winner_pid)` for the FIRST fixture in `current_round` still pending its
    own toss, or `None` if nothing is -- the round-based replacement for the old
    `pending_kind == "toss"` scalar. Several fixtures can be independently pending at
    once; callers that need to drain a whole round call this repeatedly."""
    for fs in (replay.current_round or []):
        if fs.result is None:
            return fs.stage, fs.pending_toss_winner_pid
    return None


def _complete_final_room(conn) -> tuple[rooms.Room, str, str]:
    """A two-seat 'final' room, both seats human, drafted to completion."""
    room, host_id = _make_room(conn, "final")
    room2, guest_id = rooms.join_room(conn, room.code, "Guest", DECK)
    room = rooms.start_room(conn, room.code, host_id, DECK)
    room = _play_room_to_completion(conn, room, DECK, [host_id, guest_id])
    assert room.status == "complete", room.failure_reason
    room_match.start_matches(conn, room.code, host_id, DECK, MODEL)
    return room, host_id, guest_id


def _find_toss_winner(conn, room, host_id, guest_id):
    """Both seats are human, so whichever wins the toss must be asked -- returns
    (stage, winner_pid, loser_pid, replay) once the room actually has a toss pending."""
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    pending = _find_pending_toss(replay)
    assert pending is not None
    stage, winner = pending
    loser = guest_id if winner == host_id else host_id
    return stage, winner, loser, replay


def _drafted_but_not_started(conn) -> tuple[rooms.Room, str, str]:
    """A two-seat 'final' room, drafted to completion but with no "start" move recorded
    yet -- the exact window every seat's own squad-review screen occupies. Deliberately
    NOT `_complete_final_room` (which calls `start_matches` for every other test in this
    file, since they're about what happens once matches actually begin)."""
    room, host_id = _make_room(conn, "final")
    room2, guest_id = rooms.join_room(conn, room.code, "Guest", DECK)
    room = rooms.start_room(conn, room.code, host_id, DECK)
    room = _play_room_to_completion(conn, room, DECK, [host_id, guest_id])
    assert room.status == "complete", room.failure_reason
    return room, host_id, guest_id


# --- the squad-review gate: nothing resolves until the host starts the matches ----------

def test_a_freshly_drafted_room_awaits_start_and_resolves_nothing(conn):
    room, host_id, guest_id = _drafted_but_not_started(conn)
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert replay.awaiting_start
    assert replay.results == []
    assert not replay.complete
    assert _find_pending_toss(replay) is None


def test_start_matches_is_refused_to_anyone_but_the_host(conn):
    room, host_id, guest_id = _drafted_but_not_started(conn)
    with pytest.raises(rooms.RoomError):
        room_match.start_matches(conn, room.code, guest_id, DECK, MODEL)
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert replay.awaiting_start, "a refused start must not have recorded anything"


def test_the_host_starting_matches_unlocks_the_first_fixture(conn):
    room, host_id, guest_id = _drafted_but_not_started(conn)
    room_match.start_matches(conn, room.code, host_id, DECK, MODEL)
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert not replay.awaiting_start
    assert _find_pending_toss(replay) is not None


def test_starting_matches_twice_is_a_no_op_not_an_error(conn):
    """Matches `rooms.play_again`/`advance_league_reveal`'s own convergence philosophy:
    a double-click or a retried request is expected, not a bug to reject."""
    room, host_id, guest_id = _drafted_but_not_started(conn)
    room_match.start_matches(conn, room.code, host_id, DECK, MODEL)
    room_match.start_matches(conn, room.code, host_id, DECK, MODEL)  # must not raise
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert not replay.awaiting_start


# --- authorisation: the toss winner, and only the toss winner ---------------------------

def test_only_the_toss_winner_may_call_it(conn):
    room, host_id, guest_id = _complete_final_room(conn)
    stage, winner, loser, _ = _find_toss_winner(conn, room, host_id, guest_id)
    with pytest.raises(rooms.RoomError):
        room_match.submit_toss(conn, room.code, loser, stage, "bat", DECK, MODEL)


def test_the_toss_winner_can_call_it(conn):
    room, host_id, guest_id = _complete_final_room(conn)
    stage, winner, loser, _ = _find_toss_winner(conn, room, host_id, guest_id)
    room_match.submit_toss(conn, room.code, winner, stage, "bat", DECK, MODEL)
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert _find_pending_toss(replay) is None


def test_an_invalid_elect_is_refused(conn):
    room, host_id, guest_id = _complete_final_room(conn)
    stage, winner, _, _ = _find_toss_winner(conn, room, host_id, guest_id)
    with pytest.raises(rooms.RoomError):
        room_match.submit_toss(conn, room.code, winner, stage, "sideways", DECK, MODEL)


def test_no_toss_is_pending_once_it_has_already_been_answered(conn):
    room, host_id, guest_id = _complete_final_room(conn)
    stage, winner, _, _ = _find_toss_winner(conn, room, host_id, guest_id)
    room_match.submit_toss(conn, room.code, winner, stage, "bat", DECK, MODEL)
    with pytest.raises(rooms.RoomError):
        room_match.submit_toss(conn, room.code, winner, stage, "bowl", DECK, MODEL)


def test_a_toss_move_tagged_to_a_different_stage_never_resolves_this_one(conn):
    """Moves are looked up by their own `"stage"` tag, not by position -- a toss move
    present for some OTHER fixture's name must never satisfy this one. This is the
    mechanism that lets two fixtures in the same round be answered in either order."""
    room, host_id, guest_id = _complete_final_room(conn)
    room = rooms._load_room(conn, room.code)
    room.match_moves = room.match_moves + [{"kind": "toss", "stage": "Not This One", "elects": "bat"}]
    rooms._save_room(conn, room)
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert not replay.complete
    assert _find_pending_toss(replay) is not None


def test_impact_resolves_automatically_with_no_move_at_all(conn):
    """Once the toss is answered there is nothing left to ask for either side -- Impact
    Player substitutions go straight to `decide_impact`, so the match runs straight
    through to completion in the same call."""
    room, host_id, guest_id = _complete_final_room(conn)
    stage, winner, _, _ = _find_toss_winner(conn, room, host_id, guest_id)
    room_match.submit_toss(conn, room.code, winner, stage, "bat", DECK, MODEL)
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert replay.complete


# --- authorisation: the host, and only the host, advances a round -----------------------

def _make_room_and_complete_cup(conn):
    room, host_id = _make_room(conn, "cup")
    seat_ids = [host_id]
    for n in range(3):
        _, pid = rooms.join_room(conn, room.code, f"P{n}", DECK)
        seat_ids.append(pid)
    room = rooms.start_room(conn, room.code, host_id, DECK)
    room = _play_room_to_completion(conn, room, DECK, seat_ids)
    assert room.status == "complete", room.failure_reason
    room_match.start_matches(conn, room.code, host_id, DECK, MODEL)
    return room, host_id, seat_ids


def _drain_to_advance_or_complete(conn, room, host_id):
    """Answers every toss pending in the CURRENT round -- there can be more than one
    open at once -- until the round is fully resolved (`advance_ready`) or the room
    completes outright."""
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    guard = 0
    while not replay.complete and not replay.advance_ready and guard < 50:
        guard += 1
        pending = _find_pending_toss(replay)
        if pending is None:
            raise AssertionError(f"nothing pending but not advance_ready/complete: {replay}")
        stage, winner_pid = pending
        room_match.submit_toss(conn, room.code, winner_pid, stage, "bat", DECK, MODEL)
        _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    return replay


def test_advance_is_refused_to_anyone_but_the_host(conn):
    room, host_id, seat_ids = _make_room_and_complete_cup(conn)
    replay = _drain_to_advance_or_complete(conn, room, host_id)
    if not replay.advance_ready:
        pytest.skip("this seed's cup finished before reaching a gate")
    non_host = next(pid for pid in seat_ids if pid != host_id)
    with pytest.raises(rooms.RoomError):
        room_match.advance_match(conn, room.code, non_host, DECK, MODEL)


def test_the_host_may_advance(conn):
    room, host_id, seat_ids = _make_room_and_complete_cup(conn)
    replay = _drain_to_advance_or_complete(conn, room, host_id)
    if not replay.advance_ready:
        pytest.skip("this seed's cup finished before reaching a gate")
    room_match.advance_match(conn, room.code, host_id, DECK, MODEL)
    _, replay2 = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert not replay2.advance_ready or replay2.round_label != replay.round_label


def test_advance_is_refused_when_none_is_pending(conn):
    room, host_id, guest_id = _complete_final_room(conn)
    with pytest.raises(rooms.RoomError):
        room_match.advance_match(conn, room.code, host_id, DECK, MODEL)


# --- the defining behaviour: independent fixtures resolve in parallel -------------------

def test_two_fixtures_in_the_same_round_resolve_independently_in_either_order(conn):
    """Answering Semi-final 2's toss first must not require Semi-final 1's to already
    be answered, and Semi-final 1 must still be independently answerable afterwards --
    this is what "flows in parallel" actually means, mechanically."""
    room, host_id, seat_ids = _make_room_and_complete_cup(conn)
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert replay.round_label == "Semi-finals"
    semi1 = next(fs for fs in replay.current_round if fs.stage == "Semi-final 1")
    semi2 = next(fs for fs in replay.current_round if fs.stage == "Semi-final 2")
    assert semi1.result is None and semi2.result is None, \
        "all four seats are human here, so both semis must open on a genuine toss"

    room_match.submit_toss(conn, room.code, semi2.pending_toss_winner_pid,
                            "Semi-final 2", "bat", DECK, MODEL)
    _, replay2 = room_match.room_match_state(conn, room.code, DECK, MODEL)
    semi1_after = next(fs for fs in replay2.current_round if fs.stage == "Semi-final 1")
    semi2_after = next(fs for fs in replay2.current_round if fs.stage == "Semi-final 2")
    assert semi1_after.result is None, "Semi-final 1 must still be independently pending"
    assert semi1_after.pending_toss_winner_pid == semi1.pending_toss_winner_pid
    assert semi2_after.result is not None, "Semi-final 2 must have resolved on its own"

    room_match.submit_toss(conn, room.code, semi1_after.pending_toss_winner_pid,
                            "Semi-final 1", "bat", DECK, MODEL)
    _, replay3 = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert all(fs.result is not None for fs in replay3.current_round)


# --- the replay itself: results accumulate, and completion is real --------------------

def test_a_final_room_completes_once_the_toss_is_answered(conn):
    room, host_id, guest_id = _complete_final_room(conn)
    stage, winner, _, _ = _find_toss_winner(conn, room, host_id, guest_id)
    room_match.submit_toss(conn, room.code, winner, stage, "bat", DECK, MODEL)
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert replay.complete
    assert len(replay.results) == 1


def test_replaying_the_same_room_twice_reconstructs_identically(conn):
    room, host_id, guest_id = _complete_final_room(conn)
    _, a = room_match.room_match_state(conn, room.code, DECK, MODEL)
    _, b = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert a.round_label == b.round_label
    assert _find_pending_toss(a) == _find_pending_toss(b)


# --- the tournament's very last fixture: no paused stopover, unlike every earlier round -

# Every round before the last one gives the frontend a paused, advance-gated stopover
# (`current_round` still holds the just-resolved fixture, result and all, until the host
# advances) -- that's what lets a viewer's poll catch a fresh result and play its reveal
# animation before it moves on. The tournament's very last fixture in every format skips
# that stopover entirely: the same call that resolves it also flips `complete` and empties
# `current_round`, in ALL of 'final' (its only fixture), 'cup' and 'league' (their Final).
# Confirmed live: this is why the scoreline used to appear with no reveal at all for a
# room's last match -- the frontend had nowhere left to find it. The fix is
# frontend-only (`roomMyMatchToReveal` in web/static/index.html falls back to `results`
# once a fixture is no longer in `current_matches`), so what these three pin is the data
# contract that fallback depends on: the fixture must still be reachable via `results`,
# by name, the instant the room completes -- not just "some results exist".

def test_the_final_rooms_only_fixture_is_reachable_via_results_once_it_completes(conn):
    room, host_id, guest_id = _complete_final_room(conn)
    stage, winner, _, _ = _find_toss_winner(conn, room, host_id, guest_id)
    room_match.submit_toss(conn, room.code, winner, stage, "bat", DECK, MODEL)
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert replay.complete
    assert not replay.current_round
    assert any(e.stage == "Final" for e in replay.results)


def test_the_cup_final_is_reachable_via_results_once_it_completes(conn):
    room, host_id, seat_ids = _make_room_and_complete_cup(conn)
    replay = _drive_room_to_completion(conn, room, host_id)
    assert not replay.current_round
    assert any(e.stage == "Final" for e in replay.results)


def test_the_league_final_is_reachable_via_results_once_it_completes(conn):
    room, host_id = _make_and_complete_league(conn)
    replay = _drive_room_to_completion(conn, room, host_id)
    assert not replay.current_round
    assert any(e.stage == "Final" for e in replay.results)


def _drive_room_to_completion(conn, room, host_id):
    """Answers whatever is pending, one decision at a time, until the whole match phase
    completes -- a toss to its actual winner, an advance to the host once a round is
    fully resolved, or (for a league room) a single jump straight through the rest of
    the group-stage reveal, since these tests are about the KNOCKOUT stages and don't
    care about reveal pacing itself. A guard generous enough for a ten-seat league (70
    league fixtures + up to 4 playoff ones, each contributing at most a toss)."""
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    guard = 0
    while not replay.complete and guard < 1000:
        guard += 1
        if replay.league_progress is not None:
            _, total = replay.league_progress
            room_match.advance_league_reveal(conn, room.code, host_id, total, DECK, MODEL)
        else:
            pending = _find_pending_toss(replay)
            if pending is not None:
                stage, winner_pid = pending
                room_match.submit_toss(conn, room.code, winner_pid, stage, "bat", DECK, MODEL)
            elif replay.advance_ready:
                room_match.advance_match(conn, room.code, host_id, DECK, MODEL)
            else:
                raise AssertionError(f"not complete but nothing pending/advance_ready: {replay}")
        _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert replay.complete, f"did not complete within {guard} steps"
    return replay


def _make_and_complete_league(conn):
    room, host_id = _make_room(conn, "league")
    room = rooms.start_room(conn, room.code, host_id, DECK)   # host + 9 CPU
    room = _play_room_to_completion(conn, room, DECK, [host_id])
    assert room.status == "complete", room.failure_reason
    room_match.start_matches(conn, room.code, host_id, DECK, MODEL)
    return room, host_id


def _settle_league_table(conn, room, host_id):
    """Jump straight through a league room's own group-stage reveal to the settled
    table in one call -- for tests that assert post-round-robin state directly (the
    table, who's eliminated, opening the playoffs) and aren't exercising reveal pacing
    itself. A no-op if the room isn't a league room, or the reveal is already settled."""
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    if replay.league_progress is not None:
        _, total = replay.league_progress
        room_match.advance_league_reveal(conn, room.code, host_id, total, DECK, MODEL)
        _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    return replay


def test_a_league_room_plays_the_round_robin_straight_through_to_the_playoffs(conn):
    """Two human seats, eight CPU -- a realistic room, not the worst case. The
    round-robin plays NON-interactively (see room_match.py's own comment on why: it is
    a measured architectural ceiling, not a style choice), so this is the ONE call that
    matters for proving it -- seventy fixtures resolve in a single `replay_room_matches`
    call, with a well-formed table and both playoff fixtures ready to build the instant
    the host advances. Finishing the four playoff matches too (each interactive, each
    re-replaying the whole round-robin from scratch per A62) is NOT done here: it would
    only re-prove what the 'cup' tests already cover in isolation, at real extra cost
    for no new coverage."""
    room, host_id = _make_room(conn, "league")
    _, guest_id = rooms.join_room(conn, room.code, "Guest", DECK)
    seat_ids = [host_id, guest_id]
    room = rooms.start_room(conn, room.code, host_id, DECK)
    room = _play_room_to_completion(conn, room, DECK, seat_ids)
    assert room.status == "complete", room.failure_reason
    room_match.start_matches(conn, room.code, host_id, DECK, MODEL)

    replay = _settle_league_table(conn, room, host_id)
    assert len(replay.results) == 70             # game.season.fixtures(10)
    assert all(e.stage == "league" for e in replay.results)
    assert replay.table is not None and len(replay.table) == 10
    assert sum(row.standing.played for row in replay.table) == 70 * 2
    # The round-robin has nothing interactive in it, so current_round is empty while
    # the one host-paced gate before the playoffs open sits waiting.
    assert replay.round_label == "League"
    assert replay.current_round == []
    assert replay.advance_ready

    room2 = room_match.advance_match(conn, room.code, host_id, DECK, MODEL)
    _, replay2 = room_match.room_match_state(conn, room2.code, DECK, MODEL)
    assert replay2.round_label == "Qualifiers"
    stages = {fs.stage for fs in replay2.current_round}
    assert stages == {"Qualifier 1", "Eliminator"}
    q1 = next(fs for fs in replay2.current_round if fs.stage == "Qualifier 1")
    first, second = replay.table[0].pid, replay.table[1].pid
    assert {q1.a_pid, q1.b_pid} == {first, second}


# --- elimination: who's out, and exactly when -------------------------------------------

def test_the_non_top_four_are_eliminated_the_instant_the_table_settles(conn):
    """All six non-playoff seats know their tournament is over the moment the
    round-robin resolves -- well before the host has even advanced past it, let alone
    before any playoff match is played."""
    room, host_id = _make_and_complete_league(conn)
    replay = _settle_league_table(conn, room, host_id)
    assert replay.round_label == "League"
    assert not replay.complete
    bottom_six = {row.pid for row in replay.table[4:]}
    assert replay.eliminated_pids == bottom_six


def test_the_eliminator_loser_is_out_but_qualifier_one_loser_is_not(conn):
    """The real IPL asymmetry: an Eliminator loss is immediate elimination, but a
    Qualifier 1 loss earns a second life via Qualifier 2 -- reproduced here from
    `game.season.run_playoffs`'s own adjacency, not a new bracket design."""
    room, host_id = _make_and_complete_league(conn)
    _settle_league_table(conn, room, host_id)
    room = room_match.advance_match(conn, room.code, host_id, DECK, MODEL)
    replay = _drain_to_advance_or_complete(conn, room, host_id)
    assert replay.round_label == "Qualifiers"
    q1 = next(fs for fs in replay.current_round if fs.stage == "Qualifier 1")
    elim = next(fs for fs in replay.current_round if fs.stage == "Eliminator")
    assert q1.result is not None and elim.result is not None
    assert q1.a_pid not in replay.eliminated_pids
    assert q1.b_pid not in replay.eliminated_pids
    elim_eliminated = {elim.a_pid, elim.b_pid} & replay.eliminated_pids
    assert len(elim_eliminated) == 1


def test_a_semifinals_loser_is_eliminated_before_its_sibling_semi_resolves(conn):
    """Elimination is applied fixture by fixture, not round by round: Semi-final 1's
    own loser must already be out the instant SEMI-FINAL 1 resolves, with Semi-final 2
    still genuinely pending and nothing of its own decided yet."""
    room, host_id, seat_ids = _make_room_and_complete_cup(conn)
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    semi1 = next(fs for fs in replay.current_round if fs.stage == "Semi-final 1")
    semi2 = next(fs for fs in replay.current_round if fs.stage == "Semi-final 2")
    assert semi1.result is None and semi2.result is None, \
        "all four seats are human here, so both semis must open on a genuine toss"

    room_match.submit_toss(conn, room.code, semi1.pending_toss_winner_pid,
                            "Semi-final 1", "bat", DECK, MODEL)
    _, replay2 = room_match.room_match_state(conn, room.code, DECK, MODEL)
    semi1_after = next(fs for fs in replay2.current_round if fs.stage == "Semi-final 1")
    semi2_after = next(fs for fs in replay2.current_round if fs.stage == "Semi-final 2")
    assert semi1_after.result is not None
    assert semi2_after.result is None, "the sibling must still be independently pending"
    assert len(replay2.eliminated_pids) == 1, \
        "only Semi-final 1's own loser should be eliminated at this point"
    assert replay2.eliminated_pids < {semi1_after.a_pid, semi1_after.b_pid}


def test_a_qualifiers_fixture_that_resolves_first_updates_elimination_on_its_own(conn):
    """The same fixture-not-round elimination timing, on the league's Qualifiers round
    -- whichever of Qualifier 1 / the Eliminator resolves first must update
    `eliminated_pids` by exactly the rule that fixture's own stage carries (the
    Eliminator's loser is out immediately; Qualifier 1's is not), without waiting on
    its still-pending sibling."""
    room, host_id = _make_and_complete_league(conn)
    _settle_league_table(conn, room, host_id)
    room = room_match.advance_match(conn, room.code, host_id, DECK, MODEL)
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert replay.round_label == "Qualifiers"
    bottom_six = frozenset(replay.eliminated_pids)

    q1 = next(fs for fs in replay.current_round if fs.stage == "Qualifier 1")
    elim = next(fs for fs in replay.current_round if fs.stage == "Eliminator")
    if q1.result is not None and elim.result is not None:
        pytest.skip("both Qualifiers fixtures resolved automatically under this seed")

    if q1.result is None:
        room_match.submit_toss(conn, room.code, q1.pending_toss_winner_pid,
                                "Qualifier 1", "bat", DECK, MODEL)
        resolved_stage, held_back_stage = "Qualifier 1", "Eliminator"
    else:
        room_match.submit_toss(conn, room.code, elim.pending_toss_winner_pid,
                                "Eliminator", "bat", DECK, MODEL)
        resolved_stage, held_back_stage = "Eliminator", "Qualifier 1"

    _, replay2 = room_match.room_match_state(conn, room.code, DECK, MODEL)
    held_back = next(fs for fs in replay2.current_round if fs.stage == held_back_stage)
    if held_back.result is not None:
        pytest.skip("the held-back fixture resolved on its own (CPU v CPU) in the same pass")

    new_eliminated = replay2.eliminated_pids - bottom_six
    if resolved_stage == "Eliminator":
        assert len(new_eliminated) == 1, "the Eliminator's own loser must be out immediately"
    else:
        assert len(new_eliminated) == 0, "Qualifier 1's own loser must not be out yet"


def test_round_robin_cache_does_not_change_playoff_outcomes(conn):
    """`_ROUND_ROBIN_CACHE` is a correctness-sensitive optimisation, not a plain
    memoisation -- see its own docstring on why (the RNG's own state has to be
    reproduced exactly, not just the round-robin's results). Verified directly here:
    the SAME already-completed room, replayed once with the cache cleared (forcing a
    genuine resimulation) and once with it warm, must reach the identical table and
    the identical champion either way."""
    room, host_id = _make_and_complete_league(conn)
    replay = _drive_room_to_completion(conn, room, host_id)
    assert replay.complete

    def _fingerprint(rows):
        return [(row.pid, row.standing.points, round(row.standing.nrr, 6)) for row in rows]

    room_match._ROUND_ROBIN_CACHE.clear()
    _, replay_fresh = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert replay_fresh.complete
    assert replay_fresh.champion_pid == replay.champion_pid
    assert _fingerprint(replay_fresh.table) == _fingerprint(replay.table)

    # Cache is now warm again (populated by the call just above) -- confirm a THIRD
    # replay, served entirely from cache, still reconstructs the same thing.
    _, replay_cached = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert replay_cached.champion_pid == replay.champion_pid
    assert _fingerprint(replay_cached.table) == _fingerprint(replay.table)


# --- the group-stage reveal: pure presentation on an outcome already fully decided ------

def test_league_reveal_pacing_does_not_change_the_final_outcome(conn):
    """Revealing is pure presentation on an outcome the round-robin cache already fully
    decided -- proven by revealing two otherwise-identical rooms' group stages two
    different ways (one fixture at a time, all the way through, versus a single jump to
    the end via `_drive_room_to_completion`'s own skip) and confirming they land on the
    exact same table and champion either way. Compared by side NAME, not pid -- two
    separate rooms mint their own random pids even under the same pinned seed, but the
    CPU numbering and host name are deterministic."""
    def _fingerprint(rows):
        return [(row.standing.side.name, row.standing.points, round(row.standing.nrr, 6))
                for row in rows]

    room_a, host_a = _make_and_complete_league(conn)
    _, replay_a = room_match.room_match_state(conn, room_a.code, DECK, MODEL)
    assert replay_a.league_progress is not None
    _, total = replay_a.league_progress
    for through in range(1, total + 1):
        room_match.advance_league_reveal(conn, room_a.code, host_a, through, DECK, MODEL)
    replay_a = _drive_room_to_completion(conn, room_a, host_a)

    room_b, host_b = _make_and_complete_league(conn)
    replay_b = _drive_room_to_completion(conn, room_b, host_b)   # skips straight to the end

    assert replay_a.complete and replay_b.complete
    assert room_a.players[replay_a.champion_pid].name == \
        room_b.players[replay_b.champion_pid].name
    assert _fingerprint(replay_a.table) == _fingerprint(replay_b.table)


def test_eliminated_pids_are_empty_while_the_group_stage_is_still_revealing(conn):
    """Nobody reads as 'out' before the host has actually reached the settled table --
    even once a PARTIAL table's own top 4 would already exclude someone, revealing more
    fixtures could still change who's really in it, so nothing here may leak the true
    final answer early."""
    room, host_id = _make_and_complete_league(conn)
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert replay.league_progress == (0, 70)
    assert replay.eliminated_pids == frozenset()

    room_match.advance_league_reveal(conn, room.code, host_id, 40, DECK, MODEL)
    _, replay2 = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert replay2.league_progress == (40, 70)
    assert replay2.eliminated_pids == frozenset()


def test_league_reveal_is_host_only(conn):
    room, host_id = _make_and_complete_league(conn)
    with pytest.raises(rooms.RoomError):
        room_match.advance_league_reveal(conn, room.code, "not-the-host", 1, DECK, MODEL)


def test_league_reveal_through_at_or_below_current_is_a_no_op(conn):
    room, host_id = _make_and_complete_league(conn)
    room_match.advance_league_reveal(conn, room.code, host_id, 5, DECK, MODEL)
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert replay.league_progress == (5, 70)

    # A repeat of the same through, or a lower one, must not raise and must not move
    # the cursor backwards or forwards -- a double-click or a network retry sending the
    # same request twice is a real, expected case here, not a bug to reject.
    room_match.advance_league_reveal(conn, room.code, host_id, 5, DECK, MODEL)
    room_match.advance_league_reveal(conn, room.code, host_id, 3, DECK, MODEL)
    _, replay2 = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert replay2.league_progress == (5, 70)


def test_league_reveal_beyond_the_total_is_refused(conn):
    room, host_id = _make_and_complete_league(conn)
    with pytest.raises(rooms.RoomError):
        room_match.advance_league_reveal(conn, room.code, host_id, 71, DECK, MODEL)


def test_league_next_always_matches_the_fixture_at_the_current_cursor(conn):
    """The exposed 'next fixture to reveal' never skips or repeats one, walking the
    cursor one at a time from 0 all the way to the group stage's own total."""
    room, host_id = _make_and_complete_league(conn)
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    _, total = replay.league_progress
    seen = []
    while replay.league_progress is not None:
        revealed, _ = replay.league_progress
        assert replay.league_next is not None
        seen.append(replay.league_next)
        room_match.advance_league_reveal(conn, room.code, host_id, revealed + 1, DECK, MODEL)
        _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert len(seen) == total
    assert replay.league_next is None


def test_the_finals_loser_is_marked_eliminated_the_same_way_as_an_earlier_loss(conn):
    """No special-casing the very last match: a losing finalist lands in
    `eliminated_pids` exactly like anyone knocked out earlier, which is what lets
    `room_journey` treat both cases identically (see the test below)."""
    room, host_id, guest_id = _complete_final_room(conn)
    replay = _drain_final_to_completion(conn, room, host_id, guest_id)
    assert replay.champion_pid in (host_id, guest_id)
    loser_pid = guest_id if replay.champion_pid == host_id else host_id
    assert loser_pid in replay.eliminated_pids
    assert replay.champion_pid not in replay.eliminated_pids


def test_a_wrong_kind_of_move_is_rejected(conn):
    """Corrupting the match-move log directly (never reachable through the mutators
    above, which only ever append the kind currently being asked for) -- proves the
    replay validates the move's own shape rather than assuming every recorded entry
    is well-formed."""
    room, host_id, guest_id = _complete_final_room(conn)
    room = rooms._load_room(conn, room.code)
    room.match_moves = room.match_moves + [{"kind": "impact", "slot": None}]   # this kind no longer exists
    rooms._save_room(conn, room)
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    # An unrecognised move is simply never matched by the stage lookup -- the toss
    # stays pending rather than the room crashing on a move kind nothing asks for.
    assert not replay.complete
    assert _find_pending_toss(replay) is not None


# --- room_journey: the journey card's own numbers, one seat at a time -------------------

def test_room_journey_is_none_while_your_own_run_is_still_alive(conn):
    room, host_id, guest_id = _complete_final_room(conn)
    _, replay = room_match.room_match_state(conn, room.code, DECK, MODEL)
    assert not replay.complete
    assert room_match.room_journey(room, replay, host_id) is None


def test_room_journey_is_available_for_an_eliminated_seat_before_the_room_completes(conn):
    """A cup semi-final loser's own tournament is over the instant their semi resolves
    -- well before the final (someone else's match) is even played, let alone the whole
    room reaching `complete`. This is the whole point of gating `room_journey` on
    `eliminated_pids` rather than `replay.complete`."""
    room, host_id, seat_ids = _make_room_and_complete_cup(conn)
    replay = _drain_to_advance_or_complete(conn, room, host_id)
    if replay.complete:
        pytest.skip("this seed's cup completed in the same pass as the semis")
    assert replay.eliminated_pids, "the semis resolved, so somebody must be out already"
    eliminated_pid = next(iter(replay.eliminated_pids))
    still_in = next(pid for pid in seat_ids if pid not in replay.eliminated_pids)
    assert room_match.room_journey(room, replay, eliminated_pid) is not None
    assert room_match.room_journey(room, replay, still_in) is None


def test_room_journey_is_none_for_a_pid_not_seated_in_the_room(conn):
    room, host_id, guest_id = _complete_final_room(conn)
    replay = _drain_final_to_completion(conn, room, host_id, guest_id)
    assert room_match.room_journey(room, replay, "not-a-real-seat") is None


def _drain_final_to_completion(conn, room, host_id, guest_id):
    stage, winner, _, _ = _find_toss_winner(conn, room, host_id, guest_id)
    room_match.submit_toss(conn, room.code, winner, stage, "bat", DECK, MODEL)
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
