"""`game.scenarios` -- what the daily challenge asks of you, and whether you did it.

No database and no simulator: a scenario is evaluated from a finished innings, so the
fixtures here are hand-built innings. That is the whole reason the module is pure.
"""

from __future__ import annotations

import pytest

from game.scenarios import (
    BONUS_POINTS, CHASE, CHASE_WITH_WICKETS, DEFEND_BY, EARLY_FINISH_BALLS,
    FINISHED_EARLY, FOUR_WICKET_HAUL, OPENER_CENTURY, SCENARIO_KINDS, Scenario,
    bonuses_earned, evaluate, rank_key,
)
from game.simulator import BatterCard, BowlerCard, Innings, Player


def _player(name="P"):
    # `Player` carries no positions -- that is a Card concern; the engine only needs the
    # two per-ball deltas and an identity.
    return Player(name=name, bat=0.0, bowl=None, person_id=name)


def innings(runs=150, wickets=5, balls=120, openers=(30, 30), bowler_wickets=(),
            chased=False):
    """A minimal but LEGAL innings: `batting` in batting order (index is position, A110),
    `bowling` belonging to the side that bowled at it."""
    bats = [BatterCard(player=_player(f"b{i}"), runs=r, balls=20, faced_any=True)
            for i, r in enumerate(openers)]
    bats += [BatterCard(player=_player(f"b{i}"), runs=0, balls=0) for i in range(len(openers), 11)]
    bowls = [BowlerCard(player=_player(f"w{i}"), wickets=w, balls=24, runs=30)
             for i, w in enumerate(bowler_wickets)]
    return Innings(batting=bats, bowling=bowls, runs=runs, wickets=wickets,
                   balls=balls, chased=chased)


def _chase(**kw):
    return Scenario(CHASE, 1, "CSK 2013", "Final", target=185, **kw)


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
    assert FOUR_WICKET_HAUL in bonuses_earned(_chase(), mine, theirs)

    theirs_thin = innings(bowler_wickets=(3, 1))
    assert FOUR_WICKET_HAUL not in bonuses_earned(_chase(), mine, theirs_thin)


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
    got = evaluate(_chase(), innings(runs=186, wickets=1, openers=(101, 40), chased=True,
                                     balls=100),
                   innings(bowler_wickets=(4,)))
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
    DAILY_DECK_SIZE, choose_deck, daily_seed, generate, player_order,
)


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
        s = generate(random.Random(i), 1, "CSK 2013", 178)
        kinds.add(s.kind)
        assert s.describe()
    assert kinds == set(SCENARIO_KINDS), f"generator never produced {set(SCENARIO_KINDS)-kinds}"


def test_a_chase_target_is_the_score_the_opposition_actually_posted():
    """Passed in rather than invented, so the target is a real innings against this engine
    rather than a plausible-looking number."""
    for i in range(50):
        s = generate(random.Random(i), 1, "CSK 2013", 178)
        if s.kind != DEFEND_BY:
            assert s.target == 178
            assert "179" in s.describe(), "the line must show the score to REACH, not to beat"


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
