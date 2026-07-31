"""SPEC 12. The league's own arithmetic, which nothing else protects.

A season is 70 matches of simulation wrapped in about forty lines of bookkeeping, and the
bookkeeping is where it can be quietly wrong. A fixture list that gives one side thirteen
matches, a net run rate that rewards being bowled out, a playoff bracket that eliminates
the team that finished first -- none of those crash, and none show up in a scoreboard.
"""

from __future__ import annotations

from collections import Counter

from game.season import (
    DOUBLE_AT, MATCHES_EACH, POINTS_TIE, POINTS_WIN, TEAMS, Side, Standing, _abbrev,
    _credit, fixtures,
)
from game.simulator import BALLS_PER_OVER, OVERS

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
