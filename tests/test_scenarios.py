"""`game.scenarios` -- what the daily challenge asks of you, and whether you did it.

No database and no simulator: a scenario is evaluated from a finished innings, so the
fixtures here are hand-built innings. That is the whole reason the module is pure.
"""

from __future__ import annotations

import pytest

from game.scenarios import (
    BALLS_PER_INNINGS, BONUS_ORDER, BONUS_POINTS, BOWLED_THEM_OUT, CHASE,
    CHASE_IN_OVERS, CHASE_WITH_WICKETS, DEFEND_BY, EARLY_FINISH_BALLS, FINISHED_EARLY,
    FOUR_WICKET_HAUL, GENERATED_KINDS, LOWER_ORDER_FIFTY, MAIDEN_OVER,
    NO_POWERPLAY_WICKET, OPENER_CENTURY, SCENARIO_KINDS, Scenario, TEN_SIXES,
    THREE_IN_AN_OVER, WIN_BY_RUNS, WIN_BY_WICKETS, bonuses_earned, bonuses_on_offer,
    evaluate, rank_key,
)
from game.simulator import BatterCard, BowlerCard, Innings, OverSnapshot, Player


def _player(name="P"):
    # `Player` carries no positions -- that is a Card concern; the engine only needs the
    # two per-ball deltas and an identity.
    return Player(name=name, bat=0.0, bowl=None, person_id=name)


def innings(runs=150, wickets=5, balls=120, openers=(30, 30), bowler_wickets=(),
            chased=False, sixes=0, overs=None):
    """A minimal but LEGAL innings: `batting` in batting order (index is position, A110),
    `bowling` belonging to the side that bowled at it.

    `overs` is a list of (over_runs, over_wickets) and builds the `over_log` the phase and
    per-over bonuses read. Cumulative wickets are ACCUMULATED here rather than stated, so a
    fixture cannot claim a log that disagrees with itself -- the shape A116 had to repair
    once, where a fixture described an innings no real one could be."""
    bats = [BatterCard(player=_player(f"b{i}"), runs=r, balls=20, faced_any=True)
            for i, r in enumerate(openers)]
    bats += [BatterCard(player=_player(f"b{i}"), runs=0, balls=0) for i in range(len(openers), 11)]
    bowls = [BowlerCard(player=_player(f"w{i}"), wickets=w, balls=24, runs=30)
             for i, w in enumerate(bowler_wickets)]
    log, cum_runs, cum_wkts, cum_balls = [], 0, 0, 0
    for over_no, (over_runs, over_wickets) in enumerate(overs or ()):
        cum_runs += over_runs
        cum_wkts += over_wickets
        cum_balls += 6
        log.append(OverSnapshot(over=over_no, bowler="w0", runs=cum_runs,
                                wickets=cum_wkts, balls=cum_balls,
                                over_runs=over_runs, over_wickets=over_wickets))
    return Innings(batting=bats, bowling=bowls, runs=runs, wickets=wickets,
                   balls=balls, chased=chased, sixes=sixes, over_log=log)


def _chase(**kw):
    """A LEGACY chase: target fixed at day creation, nobody bowls. Still generated for no
    new day, still scored for every old one."""
    return Scenario(CHASE, 1, "CSK 2013", "Final", target=185, **kw)


def _bowl_first(**kw):
    """A live "win by N wickets" day -- the player bowls, then chases what that earned
    them. Both innings are real, which is what every kind generated today looks like.

    No `bonus` unless a test names one, so the bonus PREDICATES can each be exercised on
    their own; the rotation itself is pinned separately."""
    kw.setdefault("wickets_required", 4)
    return Scenario(WIN_BY_WICKETS, 1, "CSK 2013", "Final", **kw)


# --- the objective ----------------------------------------------------------------------

def test_a_chase_is_met_only_by_actually_chasing():
    got = evaluate(_chase(), innings(runs=186, wickets=4, chased=True), innings(runs=185))
    assert got.objective_met
    assert got.margin == 6, "a chase's margin is wickets IN HAND, not wickets lost"

    missed = evaluate(_chase(), innings(runs=170, wickets=9, chased=False), innings(runs=185))
    assert not missed.objective_met
    assert "short" in missed.summary


def test_chase_with_wickets_needs_the_floor_as_well_as_the_chase():
    """The difference between the two chase kinds: here the wickets floor is part of the
    OBJECTIVE, not merely the margin. A chase completed with too few in hand fails."""
    s = Scenario(CHASE_WITH_WICKETS, 1, "MI 2015", "Final", target=185, wickets_required=7)
    comfortable = evaluate(s, innings(runs=186, wickets=2, chased=True), innings(runs=185))
    assert comfortable.objective_met and comfortable.margin == 8

    scraped = evaluate(s, innings(runs=186, wickets=8, chased=True), innings(runs=185))
    assert not scraped.objective_met, "chased with 2 in hand should fail a 7-wicket floor"
    assert scraped.margin == 2, "a failed objective still reports its real margin"


def test_defend_by_needs_MORE_than_the_stated_runs():
    """'win by more than 20' is strict: exactly 20 is not more than 20."""
    s = Scenario(DEFEND_BY, 1, "RCB 2016", "Final", runs_required=20)
    assert evaluate(s, innings(runs=180), innings(runs=159)).objective_met      # by 21
    assert not evaluate(s, innings(runs=180), innings(runs=160)).objective_met  # by exactly 20
    assert not evaluate(s, innings(runs=180), innings(runs=175)).objective_met  # by 5


def test_a_defence_that_loses_reports_a_negative_margin():
    s = Scenario(DEFEND_BY, 1, "RCB 2016", "Final", runs_required=20)
    got = evaluate(s, innings(runs=140), innings(runs=170))
    assert not got.objective_met
    assert got.margin == -30 and "Lost by 30" in got.summary


# --- bonuses ------------------------------------------------------------------------------

def test_an_openers_century_counts_and_a_middle_order_one_does_not():
    """Openers are positions one and two. A hundred from number four is a fine innings and
    not this bonus -- if it counted, the bonus would just be 'somebody scored a century'."""
    opener_ton = innings(openers=(105, 20))
    assert OPENER_CENTURY in bonuses_earned(_chase(), opener_ton, None)

    number_four = innings(openers=(20, 20))
    number_four.batting[3].runs = 120
    assert OPENER_CENTURY not in bonuses_earned(_chase(), number_four, None)


def test_the_bowling_bonus_reads_the_opposition_innings_not_the_players_own():
    """The A101 shape: that bug credited every side's bowling figures to its opponent.
    Here the player's four-wicket haul lives on the innings they BOWLED at."""
    mine = innings(bowler_wickets=(4, 0))       # would be the OPPOSITION's bowlers
    theirs = innings(bowler_wickets=(4, 1))     # the player's own bowlers
    assert FOUR_WICKET_HAUL in bonuses_earned(_bowl_first(), mine, theirs)

    theirs_thin = innings(bowler_wickets=(3, 1))
    assert FOUR_WICKET_HAUL not in bonuses_earned(_bowl_first(), mine, theirs_thin)


def test_finishing_early_is_only_available_to_a_chase_and_only_if_it_succeeded():
    from game.scenarios import BALLS_PER_INNINGS
    early = innings(balls=BALLS_PER_INNINGS - EARLY_FINISH_BALLS, chased=True)
    assert FINISHED_EARLY in bonuses_earned(_chase(), early, None)

    late = innings(balls=BALLS_PER_INNINGS - 1, chased=True)
    assert FINISHED_EARLY not in bonuses_earned(_chase(), late, None)

    # Batting first, there is nothing to finish early.
    defending = Scenario(DEFEND_BY, 1, "X", "Final", runs_required=20)
    assert FINISHED_EARLY not in bonuses_earned(defending, early, None)


def test_bonus_points_are_summed_from_the_bonuses_actually_earned():
    got = evaluate(_bowl_first(), innings(runs=186, wickets=1, openers=(101, 40),
                                          chased=True, balls=100),
                   innings(runs=185, bowler_wickets=(4,)))
    assert set(got.bonuses_met) == {OPENER_CENTURY, FOUR_WICKET_HAUL, FINISHED_EARLY}
    assert got.bonus_points == sum(BONUS_POINTS[b] for b in got.bonuses_met)


# --- ranking ------------------------------------------------------------------------------

def test_meeting_the_objective_outranks_any_margin_or_bonus_haul():
    """The ratified order. A scraped success beats a spectacular failure, however many
    bonuses the failure collected."""
    scraped = rank_key(True, 1, 0)
    spectacular_failure = rank_key(False, 9, 999)
    assert scraped > spectacular_failure


def test_within_a_tier_margin_leads_and_bonuses_break_the_tie():
    assert rank_key(True, 7, 0) > rank_key(True, 6, 999)
    assert rank_key(True, 7, 25) > rank_key(True, 7, 10)


# --- the scenario's own guards ------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    dict(kind=CHASE, target=None),
    dict(kind=CHASE_WITH_WICKETS, target=185, wickets_required=None),
    dict(kind=DEFEND_BY, runs_required=None),
])
def test_a_scenario_missing_the_field_its_kind_needs_is_refused(kwargs):
    """A23's rule at the scenario layer: a missing requirement must not quietly become a
    zero, which would silently ask the player for nothing at all."""
    kind = kwargs.pop("kind")
    with pytest.raises(ValueError):
        Scenario(kind, 1, "X", "Final", **kwargs)


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        Scenario("win_somehow", 1, "X", "Final")


# --- generating a day ---------------------------------------------------------------------

import datetime
import os
import pathlib
import random

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

from game.scenarios import (
    DAILY_DECK_SIZE, choose_deck, daily_bonus, daily_seed, generate, player_order,
)

_DAY0 = datetime.date(2026, 8, 22)


def test_the_date_alone_decides_the_day():
    d = datetime.date(2026, 8, 20)
    assert daily_seed(d) == daily_seed(datetime.date(2026, 8, 20))
    assert daily_seed(d) != daily_seed(datetime.date(2026, 8, 21))


def test_the_days_seed_is_the_same_in_a_DIFFERENT_PROCESS():
    """Run in subprocesses under different PYTHONHASHSEED values, and that is the only way
    this can be tested at all: `hash()` is stable WITHIN a process, so an in-process
    assertion passes just as happily against it -- which is exactly what the first version
    of this test did.

    The hazard is real rather than theoretical. Measured, `hash(date(2026,8,20))` returns
    7398043886692611116, 2696407172745156768 and -7378784384795936963 under three different
    hash seeds, because a date hashes through its byte representation and that hash IS
    salted. Two servers would disagree about which challenge is today's, and the disagreement
    would be invisible on any one machine."""
    import subprocess
    import sys

    prog = ("import datetime, sys; sys.path.insert(0, '.');"
            " from game.scenarios import daily_seed;"
            " print(daily_seed(datetime.date(2026, 8, 20)))")
    seen = set()
    for hash_seed in ("0", "1", "42"):
        env = dict(os.environ, PYTHONHASHSEED=hash_seed)
        out = subprocess.run([sys.executable, "-c", prog], capture_output=True,
                             text=True, env=env, cwd=REPO_ROOT, check=True)
        seen.add(out.stdout.strip())
    assert len(seen) == 1, f"the day's seed differs between processes: {seen}"


def test_the_day_deck_is_the_right_size_and_holds_no_duplicates():
    fs_ids = list(range(1, 167))
    deck = choose_deck(random.Random(1), fs_ids)
    assert len(deck) == DAILY_DECK_SIZE
    assert len(set(deck)) == DAILY_DECK_SIZE, "a squad was dealt twice in one challenge"
    assert set(deck) <= set(fs_ids)


def test_the_day_deck_is_the_same_for_everyone_but_the_order_is_not():
    """The whole shape of the challenge: same squads, your own sequence."""
    fs_ids = list(range(1, 167))
    day = datetime.date(2026, 8, 20)
    deck = choose_deck(random.Random(daily_seed(day)), fs_ids)

    alice = player_order(deck, 1, day)
    bob = player_order(deck, 2, day)
    assert sorted(alice) == sorted(bob) == sorted(deck), "the shared deck was not shared"
    assert alice != bob, "two players were handed the same sequence"


def test_a_players_order_is_reproducible_so_a_reload_does_not_reshuffle_it():
    day = datetime.date(2026, 8, 20)
    deck = choose_deck(random.Random(daily_seed(day)), list(range(1, 167)))
    assert player_order(deck, 7, day) == player_order(deck, 7, day)
    assert player_order(deck, 7, day) != player_order(deck, 7, day + datetime.timedelta(1))


def test_a_deck_too_small_to_serve_a_day_is_refused_rather_than_silently_short():
    with pytest.raises(ValueError):
        choose_deck(random.Random(1), list(range(DAILY_DECK_SIZE - 1)))


def test_a_generated_scenario_is_always_complete_for_its_own_kind():
    """Scenario.__post_init__ refuses a kind missing its required field, so generating a
    thousand days is itself the assertion that the generator never builds one."""
    kinds = set()
    for i in range(1000):
        s = generate(random.Random(i), 1, "CSK 2013", _DAY0 + datetime.timedelta(i))
        kinds.add(s.kind)
        assert s.describe() and s.short()
    assert kinds == set(GENERATED_KINDS), \
        f"generator never produced {set(GENERATED_KINDS) - kinds}"


def test_no_generated_day_is_batting_only():
    """The whole point of the live kinds. A day the player cannot bowl in is a day where
    five of their twelve picks never take the field, which is what made the bowling half of
    a draft pointless two days in three."""
    for i in range(200):
        s = generate(random.Random(i), 1, "CSK 2013", _DAY0 + datetime.timedelta(i))
        assert s.player_bowls, f"{s.kind} does not put the player's bowlers on the field"
        assert s.opposition_bowls, f"{s.kind} bats against a synthetic attack"
        assert s.target is None, "a live kind's target is whatever they make on the day"


def test_a_retired_kind_is_still_constructible_so_a_stored_day_still_replays():
    """Migration 032 stores the scenario precisely so a past leaderboard cannot move. A
    kind dropped from the generator must therefore stay READABLE -- deleting it would
    re-score, or fail to load, every day that was played under it."""
    for kind in (CHASE, CHASE_WITH_WICKETS, DEFEND_BY):
        assert kind in SCENARIO_KINDS and kind not in GENERATED_KINDS
    assert Scenario(CHASE, 1, "X", "Final", target=170).describe()
    assert Scenario(DEFEND_BY, 1, "X", "Final", runs_required=20).describe()


def test_a_chase_needs_no_opposition_innings_at_all():
    """The day fixes the target in advance -- the same number for everybody, which is what
    makes two chases comparable -- so the opposition's innings is played once when the day
    is created, not replayed per player. Nobody bowls at them."""
    got = evaluate(_chase(), innings(runs=186, wickets=3, chased=True))
    assert got.objective_met and got.margin == 7
    assert FOUR_WICKET_HAUL not in got.bonuses_met, "a chase has no bowling card to read"


def test_a_defence_without_the_oppositions_reply_is_refused_rather_than_assumed():
    """Its objective IS the reply. Defaulting the missing side to zero would hand every
    defender a win -- A23's rule about an unobserved value acquiring a plausible default,
    in the place it would be most rewarding to get wrong."""
    s = Scenario(DEFEND_BY, 1, "RCB 2016", "Final", runs_required=20)
    with pytest.raises(ValueError):
        evaluate(s, innings(runs=180))


def test_a_failed_chase_is_ranked_on_how_close_it_came_not_on_wickets_in_hand():
    """Ranking a failure on wickets in hand rewards NOT losing any -- a side that blocked
    out twenty overs for 100/1 would place above one that fell two runs short at 149/9.
    Nothing is compared across the tiers, because `objective_met` separates them first."""
    s = _chase()                                    # target 185, so 186 is needed
    close = evaluate(s, innings(runs=184, wickets=9, chased=False), innings(runs=185))
    blocked = evaluate(s, innings(runs=100, wickets=1, chased=False), innings(runs=185))

    assert not close.objective_met and not blocked.objective_met
    assert close.margin == -2 and blocked.margin == -86
    assert rank_key(False, close.margin, 0) > rank_key(False, blocked.margin, 0), \
        "blocking out for 100/1 outranked falling two short"
    assert "Fell 2 short" in close.summary


def test_a_successful_chase_still_outranks_every_failure_however_narrow():
    s = _chase()
    scraped = evaluate(s, innings(runs=186, wickets=9, chased=True), innings(runs=185))
    agonising = evaluate(s, innings(runs=185, wickets=0, chased=False), innings(runs=185))
    assert rank_key(True, scraped.margin, 0) > rank_key(False, agonising.margin, 999)


# --- the live kinds -------------------------------------------------------------------------
#
# Every kind generated today is a full match, so `theirs` is always real and the player's
# own bowlers are always on the field. These pin the three win conditions, and in
# particular the two places where MISSING the objective does not mean falling short.

def _win_by_runs(n=20):
    return Scenario(WIN_BY_RUNS, 1, "MI 2019", "Final", runs_required=n)


def _in_overs(n=17):
    return Scenario(CHASE_IN_OVERS, 1, "MI 2019", "Final", overs_required=n)


def test_win_by_runs_is_strict_and_reports_the_run_margin():
    s = _win_by_runs(20)
    assert evaluate(s, innings(runs=180), innings(runs=159)).objective_met      # by 21
    got = evaluate(s, innings(runs=180), innings(runs=160))                     # by exactly 20
    assert not got.objective_met and got.margin == 20


def test_a_live_kind_takes_its_target_from_what_the_opposition_actually_made():
    """The reversal this shape rests on. A legacy chase carried its target on the scenario;
    a live one has none, because the target is whatever the player's own bowlers allowed --
    which is the entire reason picking bowlers means anything."""
    s = _bowl_first()
    assert s.target is None
    got = evaluate(s, innings(runs=161, wickets=3, chased=True), innings(runs=160))
    assert got.objective_met and got.margin == 7

    # Same batting, a tighter spell: 40 fewer conceded and the chase is the same one, but
    # it was a chase of 121 rather than 161 -- so falling on 161 would have been a win.
    missed = evaluate(s, innings(runs=118, wickets=9, chased=False), innings(runs=120))
    assert not missed.objective_met and missed.margin == -3


def test_missing_a_wickets_floor_is_not_the_same_as_falling_short():
    """The case that would have printed "-2 short" at somebody who chased it. A chase
    completed with too few in hand MISSES the objective while keeping a perfectly good
    positive margin, so nothing downstream may read `not objective_met` as "failed to
    chase" -- the SIGN of the margin is what carries that."""
    s = _bowl_first(wickets_required=6)
    scraped = evaluate(s, innings(runs=161, wickets=8, chased=True), innings(runs=160))
    assert not scraped.objective_met
    assert scraped.margin == 2 and "Chased with 2 wickets in hand" in scraped.summary

    lost = evaluate(s, innings(runs=150, wickets=10, chased=False), innings(runs=160))
    assert lost.margin < 0, "only a chase that did NOT finish reports a negative margin"


def test_chase_in_overs_is_ranked_on_balls_to_spare():
    s = _in_overs(17)
    quick = evaluate(s, innings(runs=161, balls=17 * 6, chased=True), innings(runs=160))
    assert quick.objective_met, "exactly 17 overs is inside 17 overs"
    assert quick.margin == BALLS_PER_INNINGS - 17 * 6 == 18
    assert "3.0 overs to spare" in quick.summary

    quicker = evaluate(s, innings(runs=161, balls=15 * 6, chased=True), innings(runs=160))
    assert rank_key(True, quicker.margin, 0) > rank_key(True, quick.margin, 0)


def test_a_slow_winner_outranks_a_loser_within_the_failed_tier():
    """Two DIFFERENT failures share one tier here -- a chase completed too slowly and a
    chase not completed at all -- and ordering the loser above the winner would be plainly
    wrong. Balls to spare is never negative and runs short never positive, so the two
    separate without needing a fourth ranking key."""
    s = _in_overs(16)
    slow = evaluate(s, innings(runs=161, balls=19 * 6, chased=True), innings(runs=160))
    lost = evaluate(s, innings(runs=158, wickets=10, chased=False), innings(runs=160))

    assert not slow.objective_met and not lost.objective_met
    assert slow.margin == 6 and lost.margin == -3
    assert rank_key(False, slow.margin, 0) > rank_key(False, lost.margin, 0), \
        "a side that chased it slowly ranked below one that never chased it"


@pytest.mark.parametrize("scenario", [_win_by_runs(), _bowl_first(), _in_overs()])
def test_a_live_kind_without_the_other_innings_is_refused_rather_than_assumed(scenario):
    """Both innings decide it. Defaulting the missing side to zero would hand everybody a
    win -- A23's rule about an unobserved value acquiring a plausible default, in the place
    it would be most rewarding to get wrong."""
    with pytest.raises(ValueError):
        evaluate(scenario, innings(runs=180))


# --- which bonuses a day can award ----------------------------------------------------------

def test_a_day_offers_exactly_one_bonus():
    """One thing to chase, not nine things that might happen to land."""
    for bonus in (OPENER_CENTURY, MAIDEN_OVER, TEN_SIXES):
        assert bonuses_on_offer(_bowl_first(bonus=bonus)) == [bonus]


def test_only_todays_bonus_can_be_earned_however_good_the_performance():
    """A performance that would clear every landmark still collects the one on offer. This
    is what makes a day's card comparable at all: everybody is chasing the same extra."""
    everything = innings(openers=(120, 10), sixes=14, chased=True, balls=100,
                         overs=[(8, 0)] * 6)
    theirs = innings(wickets=10, bowler_wickets=(4, 2), overs=[(0, 0), (9, 3)])

    assert bonuses_earned(_bowl_first(bonus=MAIDEN_OVER), everything, theirs) == (MAIDEN_OVER,)
    assert bonuses_earned(_bowl_first(bonus=TEN_SIXES), everything, theirs) == (TEN_SIXES,)

    # And one it did NOT earn stays unearned, however much else it did.
    thin = innings(openers=(20, 10), chased=True)
    assert bonuses_earned(_bowl_first(bonus=OPENER_CENTURY), thin, theirs) == ()


def test_a_day_generated_before_a_day_carried_one_bonus_still_offers_all_of_them():
    """A scenario with no bonus is a stored day from before the rotation, and it must go on
    scoring the way it was scored -- migration 032's whole reason for storing a scenario."""
    assert _win_by_runs().bonus is None
    offered = set(bonuses_on_offer(_win_by_runs()))
    assert offered == set(BONUS_ORDER) - {FINISHED_EARLY}, "batting first, nothing to finish"
    assert set(bonuses_on_offer(_bowl_first())) == set(BONUS_ORDER)


def test_a_scenario_carrying_a_bonus_its_own_kind_cannot_award_is_refused():
    """The one pairing that is not free. Nothing should be able to construct a day that
    advertises something its own evaluator would never hand out."""
    with pytest.raises(ValueError):
        Scenario(CHASE_IN_OVERS, 1, "X", "Final", overs_required=17, bonus=FINISHED_EARLY)
    with pytest.raises(ValueError):
        Scenario(CHASE, 1, "X", "Final", target=170, bonus=FOUR_WICKET_HAUL)
    with pytest.raises(ValueError):
        Scenario(WIN_BY_RUNS, 1, "X", "Final", runs_required=20, bonus="scored_some_runs")


def test_a_legacy_chase_offers_no_bowling_bonus_because_nobody_bowled():
    """Availability is DERIVED from which innings a bonus reads, not branched on the kind.
    A legacy chase's opposition batted against a synthetic attack when the day was made, so
    there is no bowling card of the player's to read."""
    offered = set(bonuses_on_offer(_chase()))
    assert not offered & {FOUR_WICKET_HAUL, BOWLED_THEM_OUT, THREE_IN_AN_OVER, MAIDEN_OVER}
    assert OPENER_CENTURY in offered and FINISHED_EARLY in offered


def test_finishing_early_is_withheld_when_finishing_early_is_the_objective():
    """Paying a bonus for the thing already being marked is paying twice for one piece of
    cricket."""
    assert FINISHED_EARLY not in bonuses_on_offer(_in_overs())
    early = innings(balls=BALLS_PER_INNINGS - EARLY_FINISH_BALLS, chased=True)
    assert FINISHED_EARLY not in bonuses_earned(_in_overs(), early, innings(runs=100))
    assert FINISHED_EARLY in bonuses_earned(_bowl_first(), early, innings(runs=100))


# --- the new bonuses themselves --------------------------------------------------------------

def test_ten_sixes_reads_the_innings_total_not_a_single_batter():
    assert TEN_SIXES in bonuses_earned(_bowl_first(), innings(sixes=10), innings())
    assert TEN_SIXES not in bonuses_earned(_bowl_first(), innings(sixes=9), innings())


def test_a_powerplay_wicket_anywhere_in_the_first_six_overs_costs_the_bonus():
    clean = innings(overs=[(8, 0)] * 6 + [(8, 1)] * 4)
    assert NO_POWERPLAY_WICKET in bonuses_earned(_bowl_first(), clean, innings())

    # The wicket falls in the sixth over -- the last one that counts.
    late = innings(overs=[(8, 0)] * 5 + [(8, 1)] + [(8, 0)] * 4)
    assert NO_POWERPLAY_WICKET not in bonuses_earned(_bowl_first(), late, innings())


def test_an_innings_that_ended_inside_the_powerplay_falls_back_to_its_own_total():
    """`over_log` holds completed overs only, so a four-over innings has no sixth entry --
    and nothing more can fall, so the innings total IS the answer. Without the fallback a
    collapse would silently qualify."""
    collapse = innings(wickets=10, balls=24, overs=[(4, 3), (2, 3), (5, 2), (1, 2)])
    assert NO_POWERPLAY_WICKET not in bonuses_earned(_bowl_first(), collapse, innings())


def test_a_lower_order_fifty_means_seven_or_lower_and_someone_who_actually_batted():
    late = innings()
    late.batting[6].runs, late.batting[6].faced_any = 55, True
    assert LOWER_ORDER_FIFTY in bonuses_earned(_bowl_first(), late, innings())

    # Number six is not the lower order, however many he made.
    six = innings()
    six.batting[5].runs, six.batting[5].faced_any = 80, True
    assert LOWER_ORDER_FIFTY not in bonuses_earned(_bowl_first(), six, innings())


def test_the_over_bonuses_read_the_innings_the_player_bowled_at():
    """The A101 shape again, in the two newest places it could occur: a maiden and a
    three-wicket over belong to the side that BOWLED them, so both read `theirs`."""
    theirs = innings(overs=[(0, 0), (12, 3), (9, 0)])
    assert {MAIDEN_OVER, THREE_IN_AN_OVER} <= set(
        bonuses_earned(_bowl_first(), innings(overs=[(0, 0), (12, 3)]), theirs))

    quiet = innings(overs=[(4, 1), (7, 2)])
    assert not {MAIDEN_OVER, THREE_IN_AN_OVER} & set(
        bonuses_earned(_bowl_first(), innings(overs=[(0, 0), (12, 3)]), quiet))


def test_bowling_them_out_needs_all_ten():
    assert BOWLED_THEM_OUT in bonuses_earned(_bowl_first(), innings(), innings(wickets=10))
    assert BOWLED_THEM_OUT not in bonuses_earned(_bowl_first(), innings(), innings(wickets=9))


# --- the rotation -----------------------------------------------------------------------

def test_the_bonus_rotates_one_step_a_day_and_never_repeats():
    """A draw would land the same bonus three days running and leave another unseen for a
    fortnight, which is the opposite of what makes a single bonus worth chasing. A rotation
    advances by exactly one and nothing skips, so consecutive days can never match."""
    days = [_DAY0 + datetime.timedelta(i) for i in range(400)]
    seq = [daily_bonus(d) for d in days]
    assert not [i for i in range(len(seq) - 1) if seq[i] == seq[i + 1]]

    # One full cycle covers every bonus exactly once, and the next cycle repeats it.
    cycle = seq[:len(BONUS_ORDER)]
    assert set(cycle) == set(BONUS_ORDER)
    assert seq[len(BONUS_ORDER):2 * len(BONUS_ORDER)] == cycle


def test_the_rotation_is_the_date_and_nothing_else():
    """Not drawn from the day's rng: the rotation has to be legible -- a player can see it
    come round -- and a change to the scenario generator must not shift it out from under
    an unfinished week."""
    assert daily_bonus(_DAY0) == daily_bonus(datetime.date(_DAY0.year, _DAY0.month,
                                                           _DAY0.day))


def test_the_generated_kind_can_always_award_the_days_own_bonus():
    """The bonus is chosen first and the kind accommodates it. Only `win_by_wickets` can
    offer FINISHED_EARLY, so on that one day in nine the kind is forced -- and on no day may
    a scenario be built that cannot award what the rotation asked for."""
    from game.scenarios import kind_offers
    forced = 0
    for i in range(200):
        day = _DAY0 + datetime.timedelta(i)
        s = generate(random.Random(i), 1, "CSK 2013", day)
        assert s.bonus == daily_bonus(day), "the day's scenario ignored the rotation"
        assert kind_offers(s.kind, s.bonus)
        if s.bonus == FINISHED_EARLY:
            assert s.kind == WIN_BY_WICKETS
            forced += 1
    assert forced > 0, "the forced-kind branch was never exercised"
