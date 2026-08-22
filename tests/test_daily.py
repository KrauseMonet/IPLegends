"""`web.daily` -- one day's deck, one player's sequence through it, and the rules a daily
draft withholds.

Uses the real committed deck (`tools.snapshot_deck`) rather than a synthetic one, because
what is being asserted here is partly about the actual archive: whether sixteen real squads
can serve a real drafter, and whether the fallback fires when they cannot.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import replace
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


def test_replay_day_actually_passes_the_fallback_the_reroll_budget_and_the_deal_rule(
        monkeypatch):
    """A wiring test, and it exists because the tests above cannot serve as one: they call
    `run_draft` directly with a fallback, so every one of them still passed when
    `replay_day` was changed to pass `fallback_fs_ids=None`. The rescue was proven and the
    wiring that delivers it was not.

    Captured at the seam rather than inferred from an outcome, because the outcome is a
    ~1% event -- a behavioural test would need a hand-built twelve-move state that strands,
    and would still only cover the one path it encoded."""
    seen = {}
    real = sess.replay

    def spy(deck, seed, moves, rerolls_allowed=None, fallback_fs_ids=None,
            unique_deals=None):
        seen["rerolls_allowed"] = rerolls_allowed
        seen["fallback"] = fallback_fs_ids
        seen["unique_deals"] = unique_deals
        return real(deck, seed, moves, rerolls_allowed=rerolls_allowed,
                    fallback_fs_ids=fallback_fs_ids, unique_deals=unique_deals)

    monkeypatch.setattr(daily.sess, "replay", spy)
    daily.replay_day(FULL, DAY, 1, _deck(), (), unique_deals=True)

    assert seen["rerolls_allowed"] == 0, "a daily draft was given a reroll budget"
    assert seen["fallback"] is not None, "no fallback pool reached the draft"
    assert set(seen["fallback"]) == set(FULL.fs_ids), \
        "the fallback must be the whole archive, or it can strand too"
    assert seen["unique_deals"] is True, "the day's deal rule never reached the draft"

    daily.replay_day(FULL, DAY, 1, _deck(), (), unique_deals=False)
    assert seen["unique_deals"] is False, \
        "a day generated before unique dealing must still deal the way it was drafted"


# --- playing and marking a day ------------------------------------------------------------

from tools.snapshot_deck import model_from

MODEL = model_from(read_document())


def _a_side(offset=0):
    fs = FULL.fs_ids[10 + offset]
    side = daily.side_for_fs(FULL, fs)
    assert side is not None
    return side


def test_a_legacy_chase_plays_one_innings_and_every_live_kind_plays_two():
    """A legacy chase's target was fixed when the day was created, so replaying the
    opposition per player would have made it differ by whoever happened to be bowling.
    Every kind generated today plays both innings instead -- which is exactly what puts a
    drafted twelve's five bowlers on the field."""
    from game.scenarios import CHASE, GENERATED_KINDS, Scenario
    mine, opp = _a_side(), _a_side(1)

    chase = Scenario(CHASE, 1, "X 2013", "Final", target=170)
    my_inn, their_inn = daily.play_day(MODEL, chase, mine, None, random.Random(1))
    assert my_inn.balls > 0
    assert their_inn is None, "a chase replayed the opposition"

    for s in _one_of_each_live_kind():
        my_inn, their_inn = daily.play_day(MODEL, s, mine, opp, random.Random(1))
        assert their_inn is not None and their_inn.balls > 0, f"{s.kind} played one innings"
        assert my_inn.balls > 0
    assert len(_one_of_each_live_kind()) == len(GENERATED_KINDS)


def _one_of_each_live_kind():
    from game.scenarios import (
        CHASE_IN_OVERS, Scenario, WIN_BY_RUNS, WIN_BY_WICKETS,
    )
    return [Scenario(WIN_BY_RUNS, 1, "X 2013", "Final", runs_required=20),
            Scenario(WIN_BY_WICKETS, 1, "X 2013", "Final", wickets_required=4),
            Scenario(CHASE_IN_OVERS, 1, "X 2013", "Final", overs_required=17)]


def test_the_players_own_bowlers_are_the_ones_the_opposition_faces():
    """The point of the whole shape. If the opposition batted against anything else, the
    target would not be the player's own doing and picking bowlers would not matter."""
    from game.scenarios import Scenario, WIN_BY_WICKETS
    mine, opp = _a_side(), _a_side(1)
    s = Scenario(WIN_BY_WICKETS, 1, "X 2013", "Final", wickets_required=4)
    _my_inn, their_inn = daily.play_day(MODEL, s, mine, opp, random.Random(1))

    bowled = {b.player.person_id for b in their_inn.bowling}
    assert bowled, "nobody bowled at the opposition"
    mine_ids = {c.person_id for c in daily.with_bowling_depth(mine).xi}
    assert bowled <= mine_ids, "the opposition faced somebody who is not in the player's XI"


def test_a_full_match_without_an_opposition_side_is_refused():
    """Restored after a slice-based edit of this file quietly deleted it, and widened while
    it was being put back: every LIVE kind needs the opposition too, not just the legacy
    defence it was originally written for. Both innings decide the result, so a missing
    side must raise rather than acquire a default (A23)."""
    from game.scenarios import DEFEND_BY, Scenario
    for s in [Scenario(DEFEND_BY, 1, "X 2013", "Final", runs_required=20)] + \
             _one_of_each_live_kind():
        with pytest.raises(ValueError):
            daily.play_day(MODEL, s, _a_side(), None, random.Random(1))


def _four_bowler_side():
    """An eleven with EXACTLY four bowling options and a fifth on the bench.

    Built deliberately rather than found: the first squad that came to hand had six
    bowlers, so thinning it by one still left five and the fixture posed no question at all
    -- it passed against a `with_bowling_depth` that did nothing. Real cards, so `has_bowl`
    and the batting ratings are the engine's own."""
    from game.season import Side
    for fs in FULL.fs_ids:
        squad = list(FULL.cards_by_fs[fs])
        bowlers = [c for c in squad if c.has_bowl]
        others = [c for c in squad if not c.has_bowl]
        if len(bowlers) >= 5 and len(others) >= 7:
            return Side(name="short", short="SH", xi=bowlers[:4] + others[:7],
                        impact=bowlers[4])
    pytest.fail("no squad in the deck can pose the four-bowler question")


def test_a_side_that_needs_its_impact_player_to_bowl_can_still_bowl_twenty_overs():
    """A twelve is legal with five bowling options across all TWELVE, so a legal ELEVEN can
    hold four -- and `attack()` then returns four, five bowlers' worth of overs runs out
    around the seventeenth, and `choose_bowler` raises on an empty sequence. That is a 500
    on somebody's single attempt of the day.

    Found by a calibration sweep crashing, not by a test, and reachable on a defence long
    before every kind started needing the player to bowl. Asserted on the CONSEQUENCE --
    whether twenty overs can actually be bowled -- rather than on a bowler count, because
    the count is the mechanism and the crash is the thing that matters."""
    from etl.feasibility import BOWLERS_IN_TWELVE
    from game.__main__ import attack, lineup
    from game.simulator import play_innings

    posed = _four_bowler_side()
    batting = lineup(list(posed.xi), MODEL, posed.impact)
    assert len(attack(list(posed.xi), MODEL, posed.impact)) == BOWLERS_IN_TWELVE - 1

    with pytest.raises(ValueError):
        play_innings(MODEL, batting, attack(list(posed.xi), MODEL, posed.impact),
                     random.Random(4))

    fixed = daily.with_bowling_depth(posed)
    assert len(attack(list(fixed.xi), MODEL, fixed.impact)) == BOWLERS_IN_TWELVE
    innings = play_innings(MODEL, lineup(list(fixed.xi), MODEL, fixed.impact),
                           attack(list(fixed.xi), MODEL, fixed.impact), random.Random(4))
    assert innings.balls > 0


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
    assert outcome.objective_met == my_inn.chased
    # The margin's own rule differs by whether the chase came off, so the assertion has to
    # follow it: wickets in hand on a success, how close it came on a failure. Asserting
    # only the success form is what made this test fail once the failure form was
    # introduced -- correctly, since it was checking a rule that had changed.
    if my_inn.chased:
        assert outcome.margin == 10 - my_inn.wickets
    else:
        assert outcome.margin == -(s.target + 1 - my_inn.runs)


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



def test_a_real_generated_day_advertises_one_bonus_and_it_is_the_days_own():
    """Through the real generator against the real deck, not a hand-built scenario -- the
    rotation is pinned in tests/test_scenarios.py, and what this checks is that a day
    actually built and stored carries it through."""
    from game.scenarios import daily_bonus
    for i in range(12):
        date = DAY + datetime.timedelta(i)
        day = daily._generate_day(FULL, MODEL, date)
        assert day.scenario.bonus == daily_bonus(date)
        assert daily.bonuses_on_offer(day.scenario) == [daily_bonus(date)]


def test_a_days_bonus_survives_being_stored_and_read_back():
    """It lives in the scenario jsonb, so it goes through `_scenario_row` and back. A field
    the writer forgets is a day that silently reverts to offering all nine."""
    day = daily._generate_day(FULL, MODEL, DAY)
    row = json.loads(daily._scenario_row(day.scenario, 0))
    assert row["bonus"] == day.scenario.bonus
    assert daily._scenario_from_row(day.scenario.kind, row).bonus == day.scenario.bonus


def test_a_day_is_the_same_however_often_it_is_generated():
    """DAY-determinism. If a day were seeded from anything but the date, two servers
    creating today at the same moment would store different challenges and the leaderboard
    would be ranking answers to two different questions."""
    a = daily._generate_day(FULL, MODEL, DAY)
    b = daily._generate_day(FULL, MODEL, DAY)
    assert a.scenario == b.scenario, "the same date generated two different challenges"
    assert a.deck_fs_ids == b.deck_fs_ids

    c = daily._generate_day(FULL, MODEL, DAY + datetime.timedelta(1))
    assert (c.scenario, c.deck_fs_ids) != (a.scenario, a.deck_fs_ids)


def test_only_the_bonuses_today_can_actually_award_are_advertised():
    """Promising a bonus that cannot be earned would be the page lying about the rules.

    Asserted against what `bonuses_earned` can actually return for that kind, not against a
    hand-written list -- otherwise the two could drift and this would still pass. That is
    the whole reason availability is derived from which innings a bonus reads rather than
    branched on the kind: the branch was correct for three kinds and would have gone stale
    on the fourth."""
    from game.scenarios import (
        CHASE, FOUR_WICKET_HAUL, OPENER_CENTURY, Scenario, WIN_BY_WICKETS, bonuses_earned,
    )
    chase = Scenario(CHASE, 1, "X", "Final", target=170)          # legacy: nobody bowls
    live = Scenario(WIN_BY_WICKETS, 1, "X", "Final", wickets_required=4)

    assert FOUR_WICKET_HAUL not in daily.bonuses_on_offer(chase)
    assert FOUR_WICKET_HAUL in daily.bonuses_on_offer(live)
    assert OPENER_CENTURY in daily.bonuses_on_offer(chase)

    # A real day carries ONE, and it is the only thing advertised.
    today = Scenario(WIN_BY_WICKETS, 1, "X", "Final", wickets_required=4,
                     bonus=FOUR_WICKET_HAUL)
    assert daily.bonuses_on_offer(today) == [FOUR_WICKET_HAUL]

    # And each advertised bonus is one the evaluator really can hand out for that kind: a
    # performance that earns EVERY one of them must come back with exactly that set.
    from game.simulator import BatterCard, BowlerCard, Innings, OverSnapshot, Player
    def _p(n): return Player(name=n, bat=0.0, bowl=None, person_id=n)
    bats = [BatterCard(player=_p("a"), runs=120, balls=50, faced_any=True),
            BatterCard(player=_p("b"), runs=10, balls=8, faced_any=True)]
    bats += [BatterCard(player=_p(f"c{i}"), runs=0, balls=0) for i in range(2, 6)]
    bats += [BatterCard(player=_p("tail"), runs=55, balls=20, faced_any=True)]
    bats += [BatterCard(player=_p(f"d{i}"), runs=0, balls=0) for i in range(7, 11)]
    clean = [OverSnapshot(over=i, bowler="x", runs=8 * (i + 1), wickets=0,
                          balls=6 * (i + 1), over_runs=8, over_wickets=0)
             for i in range(6)]
    theirs_log = [OverSnapshot(over=0, bowler="w", runs=0, wickets=0, balls=6,
                               over_runs=0, over_wickets=0),
                  OverSnapshot(over=1, bowler="w", runs=9, wickets=3, balls=12,
                               over_runs=9, over_wickets=3)]

    everything = Innings(batting=bats, bowling=[], runs=180, wickets=0, balls=90,
                         chased=True, sixes=12, over_log=clean)
    reply = Innings(batting=bats, bowling=[BowlerCard(player=_p("w"), wickets=4, balls=24,
                                                      runs=20)],
                    runs=140, wickets=10, balls=120, over_log=theirs_log)

    for scenario, other in ((chase, None), (live, reply)):
        earned = set(bonuses_earned(scenario, everything, other))
        assert earned == set(daily.bonuses_on_offer(scenario)), \
            f"{scenario.kind} advertises a bonus its own evaluator cannot award"


# --- the shareable line --------------------------------------------------------------------

def _day(kind, **kw):
    from game.scenarios import Scenario
    return daily.Day(datetime.date(2026, 8, 20), 1,
                     Scenario(kind, 1, "Sunrisers Hyderabad 2017", "Qualifier 1", **kw),
                     [], 4)


def test_a_shared_line_never_names_a_player():
    """THE rule. Everybody gets the same deal, so naming the twelve would hand a reader
    the answer instead of the question -- the opposite of what sharing is for. Checked
    against real card names from the real deck rather than a hand-picked few, so a field
    added to the line later cannot smuggle one in unnoticed."""
    from game.scenarios import CHASE_WITH_WICKETS
    text = daily.share_text(
        _day(CHASE_WITH_WICKETS, target=151, wickets_required=5),
        {"objective_met": True, "margin": 7, "bonuses": ["opener_century"]}, 3, 12)

    names = {c.name for cards in FULL.cards_by_fs.values() for c in cards}
    leaked = [n for n in names if len(n) > 6 and n in text]
    assert not leaked, f"the shared line names players: {leaked}"


def test_it_carries_the_challenge_the_outcome_and_where_to_play():
    from game.scenarios import CHASE_WITH_WICKETS
    text = daily.share_text(
        _day(CHASE_WITH_WICKETS, target=151, wickets_required=5),
        {"objective_met": True, "margin": 7, "bonuses": ["opener_century"]}, 3, 12)
    assert "152" in text, "the score to REACH, so a reader knows what was asked"
    assert "Sunrisers Hyderabad 2017" in text
    assert "7 wickets in hand" in text
    assert "#3 of 12" in text
    assert daily.SHARE_URL in text


def test_a_failed_chase_shares_how_close_it_came():
    """Its margin is negative for exactly this reason, so the line must read it as runs
    short rather than printing a minus sign at somebody."""
    from game.scenarios import CHASE
    text = daily.share_text(_day(CHASE, target=151),
                            {"objective_met": False, "margin": -14, "bonuses": []}, 9, 12)
    assert "fell 14 short" in text
    assert "-14" not in text
    assert "❌" in text


def test_a_defence_shares_in_runs_and_a_chase_in_wickets():
    """The unit is the scenario's, and mixing them would make two lines incomparable to
    anyone reading both."""
    from game.scenarios import CHASE, DEFEND_BY
    won = daily.share_text(_day(DEFEND_BY, runs_required=20),
                           {"objective_met": True, "margin": 31, "bonuses": []}, 1, 5)
    assert "won by 31 runs" in won and "wicket" not in won

    chased = daily.share_text(_day(CHASE, target=151),
                              {"objective_met": True, "margin": 1, "bonuses": []}, 1, 5)
    assert "1 wicket in hand" in chased, "singular, not '1 wickets'"


def test_bonus_lines_appear_only_when_earned():
    from game.scenarios import CHASE
    none = daily.share_text(_day(CHASE, target=151),
                            {"objective_met": True, "margin": 5, "bonuses": []}, 1, 5)
    assert "⭐" not in none

    two = daily.share_text(
        _day(CHASE, target=151),
        {"objective_met": True, "margin": 5,
         "bonuses": ["opener_century", "finished_early"]}, 1, 5)
    assert two.count("⭐") == 2


def test_the_rank_line_is_omitted_when_there_is_no_rank_to_show():
    """A result submitted before the board is readable, or shared by something that did
    not ask for a rank, must not print '#None of None'."""
    from game.scenarios import CHASE
    text = daily.share_text(_day(CHASE, target=151),
                            {"objective_met": True, "margin": 5, "bonuses": []})
    assert "#" not in text and "None" not in text
    assert daily.SHARE_URL in text


# --- streaks --------------------------------------------------------------------------------

DAY_BEFORE = datetime.timedelta(days=1)


def _days(*offsets):
    """Dates relative to DAY, oldest first -- `_days(2, 1, 0)` is the three days ending
    today."""
    return [DAY - datetime.timedelta(days=n) for n in sorted(offsets, reverse=True)]


def test_a_streak_survives_a_day_not_yet_played():
    """THE rule the feature turns on. Playing yesterday and not yet today has lost nothing
    -- today is still there to be played -- so the streak is alive and merely unextended.

    Counting only runs that REACH today would tell somebody at breakfast their streak was
    broken and then, after they played, that it was intact: wrong, and wrong at exactly the
    moment the streak is supposed to be doing its work."""
    assert daily.streaks(_days(3, 2, 1), DAY) == (3, 3), "yesterday's run read as broken"
    assert daily.streaks(_days(2, 1, 0), DAY) == (3, 3), "and it extends when today lands"


def test_a_streak_ends_once_a_day_has_actually_been_missed():
    """The other side of it: older than yesterday means a day really was missed."""
    assert daily.streaks(_days(2), DAY) == (0, 1)
    assert daily.streaks(_days(9, 8, 7), DAY) == (0, 3)


def test_the_longest_streak_survives_the_current_one_breaking():
    """What it is for -- the best run ever, not the run in progress."""
    current, longest = daily.streaks(_days(9, 8, 7, 6, 1, 0), DAY)
    assert (current, longest) == (2, 4)


def test_never_played_is_zero_and_not_an_error():
    assert daily.streaks([], DAY) == (0, 0)


def test_one_day_is_a_streak_of_one():
    assert daily.streaks(_days(0), DAY) == (1, 1)


def test_the_order_and_any_repeats_of_the_input_do_not_matter():
    """The primary key makes a repeat impossible today, so this is defensive rather than a
    live case -- but a streak silently doubling because a row arrived twice is the kind of
    thing nobody would question on a leaderboard."""
    jumbled = [DAY, DAY - DAY_BEFORE, DAY, DAY - 2 * DAY_BEFORE, DAY - DAY_BEFORE]
    assert daily.streaks(jumbled, DAY) == (3, 3)


def test_a_run_spanning_a_month_boundary_is_still_a_run():
    """Consecutive means the calendar's own next day, not an arithmetic trick on the day
    number -- 31 August to 1 September is one day apart and 31 to 1 is not."""
    end = datetime.date(2026, 9, 1)
    days = [datetime.date(2026, 8, 30), datetime.date(2026, 8, 31), end]
    assert daily.streaks(days, end) == (3, 3)


def test_a_one_day_streak_is_not_worth_sharing():
    """Every first-time player has one, and the line above it already says they played
    today -- so it would be noise on the most-shared result there is."""
    from game.scenarios import CHASE
    r = {"objective_met": True, "margin": 5, "bonuses": []}
    assert "streak" not in daily.share_text(_day(CHASE, target=151), r, 1, 5, streak=1)
    assert "streak" not in daily.share_text(_day(CHASE, target=151), r, 1, 5, streak=0)


def test_a_real_streak_is_shared():
    from game.scenarios import CHASE
    text = daily.share_text(_day(CHASE, target=151),
                            {"objective_met": True, "margin": 5, "bonuses": []},
                            1, 5, streak=4)
    assert "4-day streak" in text
    # Still spoiler-free with the extra line on it.
    names = {c.name for cards in FULL.cards_by_fs.values() for c in cards}
    assert not [n for n in names if len(n) > 6 and n in text]


# --- the played match, for watching and for the scorecard ------------------------------------

def test_the_chase_branch_scores_without_the_opposition_and_still_shows_it():
    from game.scenarios import CHASE_WITH_WICKETS, Scenario
    day = daily.Day(DAY, 1,
                    Scenario(CHASE_WITH_WICKETS, FULL.fs_ids[10], "X 2013", "Final",
                             target=150, wickets_required=5),
                    daily.build_day(FULL, DAY), 6)
    # A real, finished draft for this day and account.
    state = _finished_state(day, account_id=1)
    play = daily.play_and_score(FULL, MODEL, day, 1, state)

    assert play.first.balls > 0, "the opposition's innings was not produced for display"
    assert play.second.balls > 0, "the player's own innings is missing"
    assert play.first_label != "You" and play.second_label == "You", \
        "in a chase the opposition bats first"


def test_a_chase_is_scored_with_NO_opposition_innings(monkeypatch):
    """Captured at the seam, because no assertion about the OUTCOME can establish this.

    `theirs` changes a chase's result only through the four-wicket bonus, so an
    implementation that wrongly passed the opposition's innings scores identically on every
    day whose derived innings happens to contain no four-for -- which is most of them. Two
    earlier versions of this test compared outcomes and both passed against a real break.
    What is actually being asserted is the WIRING: that nothing but the player's own innings
    ever reaches the evaluator on a chase."""
    from game.scenarios import CHASE_WITH_WICKETS, Scenario
    import game.scenarios as scen

    day = daily.Day(DAY, 1,
                    Scenario(CHASE_WITH_WICKETS, FULL.fs_ids[10], "X 2013", "Final",
                             target=150, wickets_required=5),
                    daily.build_day(FULL, DAY), 6)
    seen = []
    real = scen.evaluate

    def spy(scenario, mine, theirs=None):
        seen.append(theirs)
        return real(scenario, mine, theirs)

    monkeypatch.setattr(daily, "evaluate", spy)
    daily.play_and_score(FULL, MODEL, day, 1, _finished_state(day, account_id=1))

    assert seen, "the evaluator was never called"
    assert all(t is None for t in seen), \
        "a chase was scored against an innings nobody bowled"


def _finished_state(day, account_id):
    """Drive a real draft for this day to twelve picks and return its state string.

    Under the DAY'S OWN deal rule, not the default. A state is a list of indexes into the
    deals it was shown, so drafting under one rule and replaying under another decodes to a
    different twelve -- the state would still be perfectly valid and would simply describe
    somebody else's team."""
    from etl.feasibility import DraftState, IMPACT_SLOT, pick_rational
    moves = ()
    for _ in range(40):
        s = daily.replay_day(FULL, day.challenge_date, account_id, day.deck_fs_ids, moves,
                             unique_deals=day.scenario.deal_unique)
        if s.deal is None:
            return sess.encode(daily.player_seed(day.challenge_date, account_id), moves)
        open_slots = frozenset(
            [n for n in range(1, 12) if s.order[n - 1] is None]
            + ([IMPACT_SLOT] if s.impact is None else []))
        st = DraftState(tuple(s.picks), tuple(s.order), s.impact, open_slots,
                        any(c.keeper_eligible for c in s.picks),
                        sum(1 for c in s.picks if c.bowl is not None), 12 - len(s.picks))
        card, slot = pick_rational(list(s.deal.options), st, random.Random(account_id))
        i = next(n for n, c in enumerate(s.deal.options) if c.person_id == card.person_id)
        moves = moves + (sess.Pick(i, slot),)
    raise AssertionError("draft did not finish")


def _scenario_for(kind):
    from game.scenarios import Scenario
    fields = {"defend_by": dict(runs_required=20), "win_by_runs": dict(runs_required=20),
              "chase": dict(target=150), "win_by_wickets": dict(wickets_required=6),
              "chase_in_overs": dict(overs_required=16)}[kind]
    return Scenario(kind, 1, "CSK 2010", "Final", **fields)


@pytest.mark.parametrize("kind,first_runs,second_runs,chased,met,expected", [
    # A side batting FIRST wins by outscoring the reply. Reporting the second side as the
    # winner handed a defence the player had just won by 77 runs to the opposition -- found
    # by reading a real result, not by a test, which is why this table exists now.
    ("defend_by", 237, 160, False, True, "You"),
    ("defend_by", 200, 230, True, False, "CSK 2010"),
    ("win_by_runs", 237, 160, False, True, "You"),
    ("win_by_runs", 150, 151, True, False, "CSK 2010"),
    # Batting SECOND, the player wins by chasing it down -- whatever the objective said.
    ("chase", 150, 157, True, True, "You"),
    ("chase", 150, 147, False, False, "CSK 2010"),
    # The case that matters most here: chased it, MISSED the objective, still won the
    # match. Nothing may read `objective_met` -- or the margin's sign -- as "who won".
    ("win_by_wickets", 150, 157, True, False, "You"),
    ("chase_in_overs", 150, 157, True, False, "You"),
])
def test_the_winner_is_the_side_that_actually_won(kind, first_runs, second_runs, chased,
                                                  met, expected):
    import web.app as app
    from game.scenarios import Outcome

    scenario = _scenario_for(kind)
    bats_first = scenario.player_bats_first
    labels = ("You", "CSK 2010") if bats_first else ("CSK 2010", "You")
    play = daily.DayPlay(Outcome(met, 0, 0, (), "s"),
                         _stub_innings(first_runs),
                         _stub_innings(second_runs, chased=chased),
                         labels[0], labels[1], player_bats_first=bats_first)
    assert app._daily_match_out(play, scenario)["winner"] == expected


def _stub_innings(runs, chased=False):
    """Only the fields the scorecard shape reads -- this is about which LABEL comes back,
    not about cricket."""
    from game.simulator import Innings
    return Innings(batting=[], bowling=[], runs=runs, wickets=4, balls=120, chased=chased)


# --- the rules a day was generated under -----------------------------------------------------
#
# Both of these change what a stored state decodes to, or what the match does with it. A day
# has to go on replaying under the rules it was drafted under, so they are RECORDED on the
# scenario rather than inferred from the kind -- and the reason they cannot be inferred is a
# real case, not a hypothetical: a day generated the day before they landed carries a current
# kind under the old rules, so the kind cannot tell the two apart.

def test_a_generated_day_records_both_of_the_rules_it_was_generated_under():
    day = daily._generate_day(FULL, MODEL, DAY + datetime.timedelta(1))
    assert day.scenario.deal_unique and day.scenario.impact_plays


def test_the_rules_survive_being_stored_and_read_back():
    """They live in the scenario jsonb. A flag the writer forgets is a day that silently
    reverts to dealing with replacement and benching its own Impact Player."""
    day = daily._generate_day(FULL, MODEL, DAY + datetime.timedelta(1))
    row = json.loads(daily._scenario_row(day.scenario, 0))
    assert (row["deal_unique"], row["impact_plays"]) == (True, True)
    back = daily._scenario_from_row(day.scenario.kind, row)
    assert (back.deal_unique, back.impact_plays) == (True, True)


def test_a_day_stored_before_the_rules_existed_reads_as_false_not_missing():
    """The stored days really do lack these keys, so the reader has to answer for them
    rather than raise -- and it must answer FALSE, because that is how they were played."""
    legacy = {"opposition_fs_id": 1, "opposition_name": "X", "stage": "Final",
              "target": 170, "wickets_required": 7}
    sc = daily._scenario_from_row("chase_with_wickets", legacy)
    assert sc.deal_unique is False and sc.impact_plays is False


def test_a_daily_deals_each_franchise_season_at_most_once():
    """Twelve draws with replacement out of sixteen collide constantly -- measured at 8.6
    distinct squads per draft before this, with one squad dealt five times in the worst of
    400. The pool is only small because a daily is shared; solo draws from all 166, which is
    why nobody saw it there."""
    day = daily._generate_day(FULL, MODEL, DAY + datetime.timedelta(1))
    deck = daily.deck_for_day(FULL, day.deck_fs_ids)
    for acct in range(1, 16):
        r = run_draft(deck, pick_rational,
                      random.Random(daily.player_seed(day.challenge_date, acct)),
                      fallback_fs_ids=tuple(FULL.fs_ids), unique_deals=True)
        assert r.completed, "unique dealing stranded a drafter"
        seasons = [(c.franchise, c.season_year) for c in r.picks]
        assert len(set(seasons)) == len(seasons), f"repeated a squad: {seasons}"


def test_dealing_with_replacement_is_the_DEFAULT_and_what_an_older_day_gets():
    """Not a preference, and the DEFAULT is the load-bearing half.

    An older day's stored state is a list of INDEXES into the deals it was actually shown,
    so changing the draw changes which players those indexes select. The same is true of
    every solo state string and every room seed in circulation -- and nothing else in this
    suite would notice a flipped default, because both sides of every comparison it makes
    would move together. Hence a test that passes no argument at all: an earlier version
    of this one always passed `unique_deals=False` explicitly and so could not fail when
    the default was flipped to True.
    """
    day = daily._generate_day(FULL, MODEL, DAY + datetime.timedelta(1))
    deck = daily.deck_for_day(FULL, day.deck_fs_ids)

    def repeats(**kw):
        n = 0
        for acct in range(1, 16):
            r = run_draft(deck, pick_rational,
                          random.Random(daily.player_seed(day.challenge_date, acct)),
                          fallback_fs_ids=tuple(FULL.fs_ids), **kw)
            seasons = [(c.franchise, c.season_year) for c in r.picks]
            n += len(seasons) - len(set(seasons))
        return n

    assert repeats() > 0, "run_draft's default has become unique dealing"
    assert repeats(unique_deals=False) > 0, "asked for replacement and did not get it"


# --- the Impact Player actually takes the field ------------------------------------------------

def _impact_took_the_field(play):
    """Whether the drafted Impact Player appears in either innings the player owns.
    `Player.is_impact` is set by `lineup`/`attack` only for the card that was really
    substituted in, so this reads the played innings rather than the drafted twelve."""
    mine = play.first if play.player_bats_first else play.second
    theirs = play.second if play.player_bats_first else play.first
    return (any(b.player.is_impact for b in mine.batting)
            or any(w.player.is_impact for w in theirs.bowling))


def test_the_impact_player_can_actually_play_on_a_day_generated_today():
    """He never did. `decide_impact` is called from `game.season` alone, and the daily
    passed its Impact card to `lineup` -- which its own docstring says "changes no
    arithmetic" and expects to have been substituted in already. Twelve were drafted and
    eleven played.

    He is not guaranteed to play: A78 lets `decide_impact` decline when the gain does not
    clear its bar, and declining is a decision rather than a failure. What is asserted is
    that he CAN, across a spread of real days and accounts."""
    played = 0
    for i in range(1, 5):
        day = daily._generate_day(FULL, MODEL, DAY + datetime.timedelta(i))
        assert day.scenario.impact_plays
        for acct in (1, 2, 3):
            play = daily.play_and_score(FULL, MODEL, day, acct,
                                        _finished_state(day, acct))
            played += _impact_took_the_field(play)
    assert played > 0, "the Impact Player never took the field in twelve real dailies"


def test_a_day_generated_before_the_rule_still_benches_its_impact_player():
    """The pre-A134 path, kept because a day generated under it must replay under it. The
    one thing that can still put him on is the bowling-depth floor, which is a legality
    rule rather than the Impact rule."""
    from dataclasses import replace as _replace
    day = daily._generate_day(FULL, MODEL, DAY + datetime.timedelta(1))
    old = _replace(day, scenario=_replace(day.scenario, impact_plays=False))
    state = _finished_state(day, 1)
    play = daily.play_and_score(FULL, MODEL, old, 1, state)
    mine = play.first if play.player_bats_first else play.second
    batted = [b for b in mine.batting if b.player.is_impact]
    assert not batted, "the old path substituted an Impact Player in to bat"
