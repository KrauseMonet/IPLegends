"""`web.daily` -- one day's deck, one player's sequence through it, and the rules a daily
draft withholds.

Uses the real committed deck (`tools.snapshot_deck`) rather than a synthetic one, because
what is being asserted here is partly about the actual archive: whether sixteen real squads
can serve a real drafter, and whether the fallback fires when they cannot.
"""

from __future__ import annotations

import datetime
import random

import pytest

from etl.feasibility import pick_naive, pick_random, pick_rational, run_draft
from game.scenarios import DAILY_DECK_SIZE
from tools.snapshot_deck import deck_from, read_document
from web import daily
from web import session as sess

FULL = deck_from(read_document())
DAY = datetime.date(2026, 8, 20)


def _deck():
    return daily.build_day(FULL, DAY)


# --- the shared deck ----------------------------------------------------------------------

def test_the_day_is_decided_by_the_date_alone():
    assert daily.build_day(FULL, DAY) == daily.build_day(FULL, DAY)
    assert daily.build_day(FULL, DAY) != daily.build_day(FULL, DAY + datetime.timedelta(1))


def test_the_days_deck_narrows_what_is_DRAWN_but_not_what_can_be_LOOKED_UP():
    """`run_draft` draws an id from `fs_ids` and then looks the squad up in `cards_by_fs`.
    Narrowing the lookup as well would leave the fallback pool naming squads the deck
    cannot resolve -- it would fire and then find nothing, which is worse than not firing."""
    d = daily.deck_for_day(FULL, _deck())
    assert len(d.fs_ids) == DAILY_DECK_SIZE
    assert set(d.fs_ids) <= set(FULL.fs_ids)
    assert len(d.cards_by_fs) == len(FULL.cards_by_fs), \
        "cards_by_fs was narrowed; the fallback pool would find nothing"


def test_every_squad_a_player_is_dealt_comes_from_the_days_deck():
    fs_ids = _deck()
    for account in range(1, 6):
        s = daily.replay_day(FULL, DAY, account, fs_ids, ())
        assert s.deal is not None
        assert s.deal.fs_id in fs_ids, "a player was dealt a squad outside the day's deck"


def test_two_players_share_the_deck_but_not_the_sequence():
    fs_ids = _deck()
    firsts = {daily.replay_day(FULL, DAY, a, fs_ids, ()).deal.fs_id for a in range(1, 12)}
    assert len(firsts) > 1, "every player was dealt the same opening squad"
    assert firsts <= set(fs_ids)


def test_a_players_own_deal_is_reproducible_across_reloads_and_processes():
    fs_ids = _deck()
    a = daily.replay_day(FULL, DAY, 7, fs_ids, ()).deal.fs_id
    b = daily.replay_day(FULL, DAY, 7, fs_ids, ()).deal.fs_id
    assert a == b
    assert daily.player_seed(DAY, 7) == daily.player_seed(DAY, 7)
    assert daily.player_seed(DAY, 7) != daily.player_seed(DAY, 8)


# --- what a daily draft withholds ---------------------------------------------------------

def test_a_reroll_is_refused_outright():
    """Not merely unavailable in the UI: a state carrying one is rejected on replay, which
    is the only place it matters. Everybody answers the same question off the same squads,
    so waving a squad away would make two scores incomparable."""
    with pytest.raises(sess.InvalidState):
        daily.replay_day(FULL, DAY, 1, _deck(), (sess.Reroll("team"),))


def test_repositions_are_still_allowed():
    """Withholding rerolls must not withhold rearranging: the batting order is skill
    applied to what you were dealt, not an escape from it."""
    fs_ids = _deck()
    s = daily.replay_day(FULL, DAY, 1, fs_ids, ())
    first = s.deal.options[0]
    # `slots` is the card's own eligible set (its positions plus Impact); on the first pick
    # every one of them is still open, so any is a legal placement.
    slot = min(first.slots)
    moves = (sess.Pick(0, slot),)
    after = daily.replay_day(FULL, DAY, 1, fs_ids, moves)
    assert len(after.picks) == 1
    # Move him to another slot HE IS ELIGIBLE FOR. A reposition is still bounded by A76's
    # batting-role rule -- "positions can be changed" means rearranging within what a
    # player can actually bat, not anywhere at all -- so the destination comes from his own
    # `slots`, not from any free slot on the board.
    elsewhere = sorted(x for x in first.slots
                       if x != slot and x <= 11 and after.order[x - 1] is None)
    if not elsewhere:
        pytest.skip("this deal's first card is eligible for only one open slot")
    moved = daily.replay_day(FULL, DAY, 1, fs_ids,
                             moves + (sess.Reposition(slot, elsewhere[0]),))
    assert moved.order[elsewhere[0] - 1] is not None, "a legal reposition was refused"
    assert moved.order[slot - 1] is None, "he was left behind in the slot he moved out of"


# --- the fallback that makes a one-shot daily safe -----------------------------------------

# Real (day-offset, policy) pairs on which sixteen squads strand a careless drafter,
# found by search and pinned. Deliberately NOT "run 120 random trials and expect at least
# one failure": stranding is a ~1-3% event, so a trial count small enough to keep the suite
# fast is also small enough to see none of it -- the first version of these two tests did
# exactly that and failed on a run where nothing happened to strand. A pinned case either
# reproduces or the behaviour has genuinely changed.
STRANDING_CASES = ((51, pick_random), (144, pick_random),
                   (151, pick_naive), (180, pick_naive), (187, pick_naive))


@pytest.mark.parametrize("offset,policy", STRANDING_CASES)
def test_a_restricted_deck_really_does_strand_without_the_fallback(offset, policy):
    """The premise, reproduced rather than asserted -- if these stop stranding, the
    fallback is dead weight and should be reconsidered rather than kept out of habit."""
    fs_ids = daily.build_day(FULL, DAY + datetime.timedelta(offset))
    d = daily.deck_for_day(FULL, fs_ids)
    assert not run_draft(d, policy, random.Random(offset)).completed


@pytest.mark.parametrize("offset,policy", STRANDING_CASES)
def test_the_fallback_rescues_those_same_drafts(offset, policy):
    """The same day, the same seed, the same policy -- the only difference is the top-up."""
    fs_ids = daily.build_day(FULL, DAY + datetime.timedelta(offset))
    d = daily.deck_for_day(FULL, fs_ids)
    topped = run_draft(d, policy, random.Random(offset),
                       fallback_fs_ids=tuple(FULL.fs_ids))
    assert topped.completed, "the fallback did not rescue a draft that strands without it"
    assert topped.widened > 0, "it completed without the fallback ever firing"


def test_the_fallback_stays_out_of_the_way_when_it_is_not_needed():
    """It must be a last resort, not a second deck. A rational drafter should never see a
    squad from outside the day at all."""
    for t in range(40):
        fs_ids = daily.build_day(FULL, DAY + datetime.timedelta(t))
        d = daily.deck_for_day(FULL, fs_ids)
        r = run_draft(d, pick_rational, random.Random(t),
                      fallback_fs_ids=tuple(FULL.fs_ids))
        assert r.completed and r.widened == 0, \
            f"the fallback fired {r.widened} times for a drafter that did not need it"


def test_an_ordinary_draft_is_completely_unaffected_by_the_new_parameter():
    """`fallback_fs_ids` defaults to None, so solo and rooms must behave exactly as before
    -- byte-identical picks for the same seed."""
    a = run_draft(FULL, pick_rational, random.Random(11))
    b = run_draft(FULL, pick_rational, random.Random(11), fallback_fs_ids=None)
    assert [c.person_id for c in a.picks] == [c.person_id for c in b.picks]
    assert a.widened == 0 and b.widened == 0


def test_replay_day_actually_passes_the_fallback_and_the_zero_reroll_budget(monkeypatch):
    """A wiring test, and it exists because the tests above cannot serve as one: they call
    `run_draft` directly with a fallback, so every one of them still passed when
    `replay_day` was changed to pass `fallback_fs_ids=None`. The rescue was proven and the
    wiring that delivers it was not.

    Captured at the seam rather than inferred from an outcome, because the outcome is a
    ~1% event -- a behavioural test would need a hand-built twelve-move state that strands,
    and would still only cover the one path it encoded."""
    seen = {}
    real = sess.replay

    def spy(deck, seed, moves, rerolls_allowed=None, fallback_fs_ids=None):
        seen["rerolls_allowed"] = rerolls_allowed
        seen["fallback"] = fallback_fs_ids
        return real(deck, seed, moves, rerolls_allowed=rerolls_allowed,
                    fallback_fs_ids=fallback_fs_ids)

    monkeypatch.setattr(daily.sess, "replay", spy)
    daily.replay_day(FULL, DAY, 1, _deck(), ())

    assert seen["rerolls_allowed"] == 0, "a daily draft was given a reroll budget"
    assert seen["fallback"] is not None, "no fallback pool reached the draft"
    assert set(seen["fallback"]) == set(FULL.fs_ids), \
        "the fallback must be the whole archive, or it can strand too"


# --- playing and marking a day ------------------------------------------------------------

from tools.snapshot_deck import model_from

MODEL = model_from(read_document())


def _a_side(offset=0):
    fs = FULL.fs_ids[10 + offset]
    side = daily.side_for_fs(FULL, fs)
    assert side is not None
    return side


def test_a_chase_plays_one_innings_and_a_defence_plays_two():
    """Not a detail: a chase's target was fixed when the day was created, so replaying the
    opposition per player would make the target differ by whoever happened to be bowling --
    and two chases against different targets are not comparable, which is the whole point
    of a shared daily."""
    from game.scenarios import CHASE, DEFEND_BY, Scenario
    mine, opp = _a_side(), _a_side(1)

    chase = Scenario(CHASE, opp_fs := 1, "X 2013", "Final", target=170)
    my_inn, their_inn = daily.play_day(MODEL, chase, mine, None, random.Random(1))
    assert my_inn.balls > 0
    assert their_inn is None, "a chase replayed the opposition"

    defence = Scenario(DEFEND_BY, opp_fs, "X 2013", "Final", runs_required=20)
    my_inn, their_inn = daily.play_day(MODEL, defence, mine, opp, random.Random(1))
    assert their_inn is not None and their_inn.balls > 0


def test_a_defence_without_an_opposition_side_is_refused():
    from game.scenarios import DEFEND_BY, Scenario
    s = Scenario(DEFEND_BY, 1, "X 2013", "Final", runs_required=20)
    with pytest.raises(ValueError):
        daily.play_day(MODEL, s, _a_side(), None, random.Random(1))


def test_the_same_attempt_scores_the_same_every_time():
    """The match is seeded from the day and the account, so a re-submission cannot re-roll
    a bad result -- and a stored row can be re-verified later by replaying it."""
    from game.scenarios import CHASE, Scenario
    s = Scenario(CHASE, 1, "X 2013", "Final", target=170)
    mine = _a_side()
    a = daily.score_day(MODEL, s, mine, None, random.Random("fixed"))[0]
    b = daily.score_day(MODEL, s, mine, None, random.Random("fixed"))[0]
    assert (a.objective_met, a.margin, a.bonuses_met) == (b.objective_met, b.margin, b.bonuses_met)


@pytest.mark.parametrize("seed", range(8))
def test_scoring_marks_the_very_innings_it_played(seed):
    """`score_day` plays and marks in one call so the two cannot be done against different
    innings -- the shape A116 had to repair once already.

    Parametrised over several seeds deliberately: with a single seed, an implementation
    that marked a DIFFERENT innings still passed, because two innings can happen to end on
    the same number of wickets. One coincidence is likely; eight in a row is not."""
    from game.scenarios import CHASE, Scenario
    s = Scenario(CHASE, 1, "X 2013", "Final", target=170)
    outcome, my_inn, _ = daily.score_day(MODEL, s, _a_side(), None, random.Random(seed))
    assert outcome.margin == 10 - my_inn.wickets
    assert outcome.objective_met == my_inn.chased


# --- the integrity rule specific to a daily ------------------------------------------------

def test_a_state_carrying_someone_elses_seed_is_refused():
    """The seed is NOT the player's here -- it is derived from the date and the account. A
    state with any other seed describes a deal nobody offered, and every other check in
    `submit` would pass, because the draft it encodes is perfectly legal. Just not theirs."""
    mine = daily.player_seed(DAY, 1)
    ok = sess.encode(mine, ())
    daily.decode_own_state(ok, DAY, 1)                      # their own deal: fine

    with pytest.raises(daily.DailyError):
        daily.decode_own_state(sess.encode(mine, ()), DAY, 2)      # another account's
    with pytest.raises(daily.DailyError):
        daily.decode_own_state(sess.encode(mine + 1, ()), DAY, 1)  # a hand-edited seed
    with pytest.raises(daily.DailyError):
        daily.decode_own_state(ok, DAY + datetime.timedelta(1), 1)  # yesterday's deal


def test_side_for_fs_declines_a_squad_that_cannot_field_an_eleven():
    """A property of the squad, not a failure -- which is why day generation redraws rather
    than raising the first time it meets one."""
    thin = daily.side_for_fs(
        type(FULL)(cards_by_fs={999: FULL.cards_by_fs[FULL.fs_ids[0]][:3]}, fs_ids=[999]),
        999)
    assert thin is None



def test_a_days_target_is_the_same_however_often_it_is_generated():
    """Player-independence needs no test -- `opposition_total` has no access to the player,
    so it is enforced by the signature rather than by a rule that could drift. What can
    drift is DAY-determinism: if the opposition's innings were seeded from anything but the
    date, two servers creating today at the same moment would store different targets and
    the leaderboard would be comparing different challenges."""
    a, _ = daily._generate_day(FULL, MODEL, DAY)
    b, _ = daily._generate_day(FULL, MODEL, DAY)
    assert a.scenario == b.scenario, "the same date generated two different challenges"
    assert a.deck_fs_ids == b.deck_fs_ids

    c, _ = daily._generate_day(FULL, MODEL, DAY + datetime.timedelta(1))
    assert (c.scenario, c.deck_fs_ids) != (a.scenario, a.deck_fs_ids)
