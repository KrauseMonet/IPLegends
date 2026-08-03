"""SPEC 11.2/11.3, extended one layer up. `web/season_session.py` is what makes a season
"replay from scratch every request" the same way `web/session.py` already makes a draft --
these tests are for exactly that contract: the combined state string round-trips, a
`MoveCursor` answers a live toss/Impact and every skip hatch through one mechanism, and a
whole draft-then-season replay reconstructs identically from a seed and a move list alone.

The fixture deck is synthetic, same reasoning as `tests/test_web.py`'s own: a test that
needed Neon to prove a season replays would be testing the network, not the replay.
"""

from __future__ import annotations

import random

import pytest

from etl.feasibility import IMPACT_SLOT, TWELVE_SIZE, XI_SIZE, Card, Deck
from game.season import TOSS_DEFAULT_ELECTS, ImpactPick, MATCHES_EACH, TossElect
from game.simulator import Model
from web import session as sess
from web import season_session as ss

# --- a synthetic deck and model, same shape as tests/test_web.py's own ------------------
#
# Duplicated rather than imported -- tests/test_rooms.py already set the precedent of each
# test file keeping its own fixture helpers rather than reaching into a sibling module.

FRANCHISES = ["Alpha", "Beta", "Gamma", "Delta"]


def card(n: int, positions: frozenset[int], *, fs: int = 1, role: str = "batter",
         bowl: float | None = None, franchise: str | None = None,
         season_year: int | None = None) -> Card:
    return Card(fs_id=fs, person_id=f"c{n}", name=f"c{n}", bat=0.1, bowl=bowl,
                role=role, overseas=False, positions=positions,
                franchise=franchise, season_year=season_year,
                keeper_eligible=(role == "keeper"))


def deck_of(n_fs: int = 24, per_fs: int = 16) -> Deck:
    by_fs: dict[int, list[Card]] = {}
    n = 0
    for fs in range(1, n_fs + 1):
        franchise = FRANCHISES[(fs - 1) % len(FRANCHISES)]
        season_year = 2000 + (fs - 1) // len(FRANCHISES)
        cards = []
        for i in range(per_fs):
            start = i % XI_SIZE
            pos = frozenset(((start + k) % XI_SIZE) + 1 for k in range(4))
            role = "keeper" if i == 0 else "batter"
            bowl = 0.1 if i % 2 == 0 else None
            cards.append(card(n, pos, fs=fs, role=role, bowl=bowl,
                               franchise=franchise, season_year=season_year))
            n += 1
        by_fs[fs] = cards
    return Deck(by_fs, sorted(by_fs))


DECK = deck_of()


def _spread(candidates, made: int) -> int:
    return (made * 7) % len(candidates)


def _open_slots(s: sess.Session) -> frozenset[int]:
    open_slots = set(range(1, XI_SIZE + 1)) | {IMPACT_SLOT}
    for i, c in enumerate(s.order):
        if c is not None:
            open_slots.discard(i + 1)
    if s.impact is not None:
        open_slots.discard(IMPACT_SLOT)
    return frozenset(open_slots)


def walk(seed: int, chooser=_spread) -> sess.Session:
    s = sess.replay(DECK, seed, ())
    while not s.squad_complete:
        i = chooser(s.deal.options, len(s.picks))
        chosen = s.deal.options[i]
        slot = min(chosen.slots & _open_slots(s))
        s = sess.replay(DECK, seed, s.moves + (sess.Pick(i, slot),))
    return s


def _model_with_fixed_state() -> Model:
    """A real `Model`, with `.state()` overridden to a fixed (over, wickets)-independent
    distribution -- these tests exercise the toss/Impact bookkeeping around a match, not
    the state grid itself, and no local database exists to fit a real one against
    (CLAUDE.md). `grid`/`costs`/`dist`/`outs`/`faced` are never touched once `.state` is
    replaced, so empty placeholders are enough."""
    m = Model(dist={}, faced={}, outs={}, grid=None, costs=None,
              wide_rate=0.0, wide_runs=1.0, extras_rate=0.0,
              unrated_bat={}, season_mean={})
    probs = (0.06, 0.20, 0.30, 0.16, 0.08, 0.12, 0.02, 0.06)
    values = (-6.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    m.state = lambda over, wickets: (probs, values)
    return m


# --- the state string --------------------------------------------------------------------

@pytest.mark.parametrize("moves", [
    (),
    (TossElect("bat"),),
    (TossElect("bowl"), ImpactPick(None)),
    (TossElect("bat"), ImpactPick(7), TossElect("bowl"), ImpactPick(None)),
    tuple(ImpactPick(n) for n in (1, 11, None, 5)),
])
def test_season_moves_round_trip(moves):
    assert ss.decode_season(ss.encode_season(moves)) == moves


def test_decode_season_of_the_empty_string_is_no_moves():
    assert ss.decode_season("") == ()


@pytest.mark.parametrize("bad", ["x", "ta.", "i", "i12", "i-1", "tab", ".ta"])
def test_a_malformed_season_segment_is_rejected_not_guessed(bad):
    with pytest.raises(sess.InvalidState):
        ss.decode_season(bad)


def test_a_season_state_longer_than_the_cap_is_rejected():
    with pytest.raises(sess.InvalidState):
        ss.decode_season(".".join("to" for _ in range(ss.SEASON_MOVE_CAP + 1)))


def test_decode_full_of_a_bare_draft_state_is_zero_season_moves():
    """A bare draft state (no `~`) is exactly what the draft flow hands back the moment
    a squad completes -- must be readable as "season not started yet", not rejected."""
    draft_part, moves = ss.decode_full("7-1:1.5:2.7")
    assert draft_part == "7-1:1.5:2.7"
    assert moves == ()


def test_decode_full_splits_on_the_first_tilde():
    draft_part, moves = ss.decode_full("7-1:1.5~ta.i0")
    assert draft_part == "7-1:1.5"
    assert moves == (TossElect("bat"), ImpactPick(None))


def test_encode_full_always_carries_a_tilde():
    assert ss.encode_full("7-1:1.5", ()) == "7-1:1.5~"
    assert ss.encode_full("7-1:1.5", (TossElect("bowl"),)) == "7-1:1.5~to"


def test_encode_full_and_decode_full_round_trip():
    draft_state = "7-1:1.5:2.7"
    moves = (TossElect("bat"), ImpactPick(3))
    assert ss.decode_full(ss.encode_full(draft_state, moves)) == (draft_state, moves)


# --- the move cursor: one mechanism behind live play and every skip hatch ----------------

def test_recorded_moves_answers_from_the_tuple_then_reports_no_move():
    cursor = ss.recorded_moves((TossElect("bat"), ImpactPick(5)))
    assert cursor.next_toss(0) == TossElect("bat")
    assert cursor.next_impact(0) == ImpactPick(5)
    assert cursor.next_toss(1) is None
    assert cursor.next_impact(1) is None
    assert cursor.emitted == [TossElect("bat"), ImpactPick(5)]


def test_a_move_of_the_wrong_type_at_a_position_is_rejected():
    cursor = ss.recorded_moves((ImpactPick(None),))
    with pytest.raises(sess.InvalidState):
        cursor.next_toss(0)


def test_skip_this_match_only_auto_resolves_the_named_match():
    cursor = ss.skip_this_match((), pending_match_no=2)
    assert cursor.next_toss(1) is None            # not the named match: still pauses
    assert cursor.next_toss(2) == TossElect(TOSS_DEFAULT_ELECTS)


def test_skip_this_match_still_replays_recorded_moves_first():
    cursor = ss.skip_this_match((TossElect("bat"),), pending_match_no=0)
    assert cursor.next_toss(0) == TossElect("bat"), "recorded takes priority over auto"
    assert cursor.next_impact(0) == ImpactPick(None), "then this match auto-resolves"


def test_skip_group_stage_covers_every_league_match_and_no_playoff_one():
    cursor = ss.skip_group_stage(())
    for n in range(MATCHES_EACH):
        assert cursor.next_toss(n) is not None
        assert cursor.next_impact(n) is not None
    assert cursor.next_toss(MATCHES_EACH) is None
    assert cursor.next_impact(MATCHES_EACH) is None


def test_skip_tournament_covers_everything():
    cursor = ss.skip_tournament(())
    for n in (0, 1, MATCHES_EACH, MATCHES_EACH + 2):
        assert cursor.next_toss(n) is not None
        assert cursor.next_impact(n) is not None


def test_every_move_the_cursor_ever_emits_is_appended_in_order():
    """The tuple a caller re-encodes into the next state string -- must be exactly the
    sequence a plain replay of the SAME moves would reproduce, whether each move was
    replayed from `recorded` or freshly auto-resolved."""
    cursor = ss.skip_this_match((TossElect("bowl"),), pending_match_no=1)
    cursor.next_toss(0)     # replayed
    cursor.next_impact(1)   # auto (this is the named match)
    assert cursor.emitted == [TossElect("bowl"), ImpactPick(None)]


# --- replaying a whole draft-then-season from scratch ------------------------------------

def test_replay_season_pauses_at_the_first_league_decision_from_a_fresh_draft():
    """Seed 11's own rng stream loses the human's first toss (verified directly: a lost
    toss never raises `_MatchNeedsToss` at all, see `game.season._play_human_match`), so
    the first thing a fresh replay needs is the break-time Impact choice, not a toss --
    this is the real, deterministic shape for THIS seed, not an assumption."""
    played = walk(11)
    replay = ss.replay_season(DECK, _model_with_fixed_state(), played.state,
                              ss.recorded_moves(()))
    assert not replay.complete
    assert replay.pending_kind == "impact"
    assert replay.pending_human_match_no == 0
    assert replay.pending_stage == "league"


def test_replay_season_advances_one_decision_per_supplied_move():
    played = walk(11)
    after_impact = ss.replay_season(DECK, _model_with_fixed_state(), played.state,
                                    ss.recorded_moves((ImpactPick(None),)))
    # the match completes on this one supplied move (a lost toss needs no move of its
    # own) -- next is either the season's second human match or the season is done.
    assert after_impact.pending_kind != "impact" or after_impact.pending_human_match_no != 0


def test_replaying_the_same_moves_twice_reconstructs_identically():
    """SPEC 11.3's contract, one layer up from the draft: nothing about a season in
    progress may depend on anything but the seed and the moves recorded so far."""
    played = walk(11)
    moves = (ImpactPick(None),)
    a = ss.replay_season(DECK, _model_with_fixed_state(), played.state,
                         ss.recorded_moves(moves))
    b = ss.replay_season(DECK, _model_with_fixed_state(), played.state,
                         ss.recorded_moves(moves))
    assert a.complete == b.complete
    assert a.pending_kind == b.pending_kind
    assert a.pending_human_match_no == b.pending_human_match_no


def test_skip_tournament_from_a_fresh_draft_completes_the_whole_season():
    played = walk(11)
    replay = ss.replay_season(DECK, _model_with_fixed_state(), played.state,
                              ss.skip_tournament(()))
    assert replay.complete
    assert replay.season.champion is not None
    assert len(replay.season.results) == MATCHES_EACH * 5   # (TEAMS * MATCHES_EACH) // 2


def test_an_incomplete_draft_is_refused_before_the_season_ever_starts():
    s = sess.replay(DECK, 4, ())   # nowhere near a complete squad
    with pytest.raises(sess.InvalidState):
        ss.replay_season(DECK, _model_with_fixed_state(), s.state, ss.recorded_moves(()))
