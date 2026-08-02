"""SPEC 12. The league's own arithmetic, which nothing else protects.

A season is 70 matches of simulation wrapped in about forty lines of bookkeeping, and the
bookkeeping is where it can be quietly wrong. A fixture list that gives one side thirteen
matches, a net run rate that rewards being bowled out, a playoff bracket that eliminates
the team that finished first -- none of those crash, and none show up in a scoreboard.
"""

from __future__ import annotations

from collections import Counter

from game.season import (
    DOUBLE_AT, MATCHES_EACH, POINTS_TIE, POINTS_WIN, TEAMS, JourneyAccumulator, Result,
    Season, Side, Standing, _abbrev, _credit, _leader, fixtures, journey_stats,
)
from game.simulator import BALLS_PER_OVER, OVERS, BatterCard, BowlerCard, Innings, Player

FULL = OVERS * BALLS_PER_OVER


def side(name="X"):
    return Side(name=name, short=name, xi=[])


# --- the fixture list -------------------------------------------------------------------

def test_every_side_plays_exactly_fourteen():
    counts = Counter()
    for i, j in fixtures(TEAMS):
        counts[i] += 1
        counts[j] += 1
    assert set(counts) == set(range(TEAMS))
    assert set(counts.values()) == {MATCHES_EACH}, dict(counts)


def test_the_league_is_seventy_matches():
    assert len(fixtures(TEAMS)) == TEAMS * MATCHES_EACH // 2 == 70


def test_the_double_fixtures_are_symmetric():
    """Every side must have the same number of twice-played opponents, or the draw is
    unfair in a way no scoreboard would ever reveal.

    The count is five, not three: distances 1 and 2 each name two opponents (either side
    of you), while distance 5 is the antipode and names one. Derived here rather than
    written as 5, so a change to DOUBLE_AT is checked instead of silently accommodated.
    """
    per_side = sum(1 if (2 * d) % TEAMS == 0 else 2 for d in DOUBLE_AT)
    assert per_side * 2 + (TEAMS - 1 - per_side) == MATCHES_EACH
    doubles = Counter()
    seen = Counter()
    for i, j in fixtures(TEAMS):
        seen[frozenset((i, j))] += 1
    for pair, n in seen.items():
        if n == 2:
            for t in pair:
                doubles[t] += 1
    assert set(doubles.values()) == {per_side}, dict(doubles)


def test_no_side_is_drawn_against_itself():
    assert all(i != j for i, j in fixtures(TEAMS))


# --- net run rate -----------------------------------------------------------------------

def test_being_bowled_out_is_charged_the_full_twenty_overs():
    """The competition's own rule. Without it, collapsing for 60 in 12 overs would score a
    BETTER run rate than grinding out 60 in 20, and a side could improve its net run rate
    by losing badly."""
    quick = Standing(side=side())
    _credit(quick, runs=60, balls=72, wickets=10, against_runs=61, against_balls=72,
            against_wickets=4)
    assert quick.balls_for == FULL, "an all-out innings is charged twenty overs"
    assert quick.balls_against == 72, "the side that did NOT lose ten keeps its real overs"


def test_an_unfinished_innings_keeps_its_real_overs():
    chase = Standing(side=side())
    _credit(chase, runs=150, balls=108, wickets=4, against_runs=149, against_balls=FULL,
            against_wickets=8)
    assert chase.balls_for == 108


def test_net_run_rate_is_scored_minus_conceded_per_over():
    s = Standing(side=side())
    _credit(s, runs=180, balls=FULL, wickets=3, against_runs=120, against_balls=FULL,
            against_wickets=8)
    assert s.nrr == (180 - 120) / OVERS


def test_net_run_rate_is_zero_before_a_ball_is_bowled():
    """A division by zero here would take down the whole table, not one row."""
    assert Standing(side=side()).nrr == 0.0


# --- points -----------------------------------------------------------------------------

def test_points_are_two_for_a_win_and_one_for_a_tie():
    s = Standing(side=side(), won=9, lost=4, tied=1)
    assert s.points == 9 * POINTS_WIN + POINTS_TIE


# --- naming -----------------------------------------------------------------------------

def test_a_side_is_named_like_a_fixture_list():
    assert _abbrev("Mumbai Indians", 2018) == "MI 2018"
    assert _abbrev("Kolkata Knight Riders", 2024) == "KKR 2024"
    assert _abbrev("Royal Challengers Bangalore", 2016) == "RCB 2016"


def test_a_missing_franchise_does_not_produce_a_blank_side():
    assert _abbrev(None, 2011) == "2011"


# --- the journey card's stats accumulator ------------------------------------------------

def _batter(name, runs, balls, faced_any=True):
    return BatterCard(Player(name, 0.0), runs=runs, balls=balls, faced_any=faced_any)


def _bowler(name, wickets, balls=24):
    return BowlerCard(Player(name, 0.0), balls=balls, wickets=wickets)


def test_add_batting_accumulates_runs_across_matches():
    acc = JourneyAccumulator()
    acc.add_batting(Innings(batting=[_batter("A", 30, 20), _batter("B", 10, 8)], bowling=[]))
    acc.add_batting(Innings(batting=[_batter("A", 15, 10)], bowling=[]))
    assert acc.runs == {"A": 45, "B": 10}
    assert acc.total_runs == 55


def test_a_batter_who_never_faced_a_ball_is_not_counted():
    acc = JourneyAccumulator()
    acc.add_batting(Innings(batting=[_batter("A", 0, 0, faced_any=False)], bowling=[]))
    assert acc.runs == {}
    assert acc.total_runs == 0


def test_add_bowling_accumulates_wickets_across_matches():
    acc = JourneyAccumulator()
    acc.add_bowling(Innings(batting=[], bowling=[_bowler("X", 2), _bowler("Y", 0)]))
    acc.add_bowling(Innings(batting=[], bowling=[_bowler("X", 1)]))
    assert acc.wickets == {"X": 3, "Y": 0}
    assert acc.total_wickets == 3


def test_a_bowler_who_never_bowled_a_ball_is_not_counted():
    acc = JourneyAccumulator()
    acc.add_bowling(Innings(batting=[], bowling=[_bowler("X", 0, balls=0)]))
    assert acc.wickets == {}


def test_leader_breaks_ties_alphabetically():
    """A dict's own iteration order is insertion order, not a real tie-break -- the leader
    must not depend on which of two equal totals happened to be added first."""
    assert _leader({"Zed": 50, "Amy": 50, "Mid": 10}) == ("Amy", 50)


def test_leader_on_no_evidence_at_all():
    assert _leader({}) == ("", 0)


def test_journey_stats_combines_the_league_record_with_however_far_the_playoffs_went():
    you, them = side("YOU"), side("THEM")
    standing = Standing(side=you, played=14, won=9, lost=5, tied=0)
    season = Season(sides=[you, them], table=[standing])
    season.playoffs = [
        Result(home=you, away=them, winner=you, stage="Qualifier 1"),
        Result(home=them, away=you, winner=them, stage="Final"),
    ]
    season.champion = them  # lost the final

    acc = JourneyAccumulator()
    acc.add_batting(Innings(batting=[_batter("A", 50, 30)], bowling=[]))
    acc.add_bowling(Innings(batting=[], bowling=[_bowler("B", 2)]))

    stats = journey_stats(season, you, acc)
    assert stats.played == 16 and stats.won == 10 and stats.lost == 6
    assert stats.champion is False
    assert stats.runs == 50 and stats.wickets == 2
    assert stats.top_scorer == ("A", 50)
    assert stats.top_wicket_taker == ("B", 2)


def test_journey_stats_for_a_side_that_never_reached_the_playoffs():
    you, them = side("YOU"), side("THEM")
    standing = Standing(side=you, played=14, won=4, lost=10, tied=0)
    season = Season(sides=[you, them], table=[standing])
    season.playoffs = [Result(home=them, away=them, winner=them, stage="Final")]
    season.champion = them

    stats = journey_stats(season, you, JourneyAccumulator())
    assert stats.played == 14, "no playoff match involved this side, so none is added"
    assert stats.won == 4 and stats.lost == 10
