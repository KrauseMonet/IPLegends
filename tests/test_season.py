"""SPEC 12. The league's own arithmetic, which nothing else protects.

A season is 70 matches of simulation wrapped in about forty lines of bookkeeping, and the
bookkeeping is where it can be quietly wrong. A fixture list that gives one side thirteen
matches, a net run rate that rewards being bowled out, a playoff bracket that eliminates
the team that finished first -- none of those crash, and none show up in a scoreboard.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from etl.feasibility import Card
from game.season import (
    DOUBLE_AT, IMPACT_SIT_OUT_BAR, IMPACT_TOO_GOOD_GAIN, MATCHES_EACH, POINTS_TIE,
    POINTS_WIN, TEAMS, TOSS_DEFAULT_ELECTS, ImpactPick, JourneyAccumulator, NeedImpact,
    NeedToss, Result, Season, Side, Standing, TossElect, _MatchNeedsImpact,
    _MatchNeedsToss, _abbrev, _credit, _impact_xi, _leader, _play_human_match,
    _pure_bowler_678, _weakest_bowler, _weakest_pure_batter, decide_impact, fixtures,
    journey_stats, run_league, run_playoffs, toss,
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
#
# JourneyAccumulator keys by person_id, not name (see its docstring) -- these helpers
# default person_id to the given name so every existing assertion below, keyed by name,
# still holds: one name in these tests always means one person.

def _batter(name, runs, balls, faced_any=True, person_id=None):
    return BatterCard(Player(name, 0.0, person_id=person_id or name),
                       runs=runs, balls=balls, faced_any=faced_any)


def _bowler(name, wickets, balls=24, person_id=None):
    return BowlerCard(Player(name, 0.0, person_id=person_id or name),
                       balls=balls, wickets=wickets)


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


def test_two_different_people_sharing_a_name_do_not_collapse_into_one_total():
    """The whole reason this accumulator keys by person_id rather than name (CLAUDE.md's
    standing rule): two drafted seasons can share a registry name, and if the dict keyed
    on that name, their runs would merge into one total and `_leader` could crown a person
    who never scored most of the runs credited to him."""
    acc = JourneyAccumulator()
    acc.add_batting(Innings(batting=[
        _batter("S Sharma", 80, 40, person_id="p1"),
        _batter("S Sharma", 5, 10, person_id="p2"),
    ], bowling=[]))
    assert acc.runs == {"p1": 80, "p2": 5}
    assert acc.names == {"p1": "S Sharma", "p2": "S Sharma"}
    assert _leader(acc.runs, acc.names) == ("S Sharma", 80)


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


# --- the situational Impact Player --------------------------------------------------------

def _card(name, bat=None, bowl=None, role=None):
    return Card(1, name, name, bat=bat, bowl=bowl, role=role)


def own_xi():
    """A shaped eleven, not a uniform one: two candidates exist for each kind of swap, so
    picking the WRONG one (not just any eligible one) is what these tests pin.

    bowl8 (bat .02) is the weaker of the two bowlers at 7/8, so a batting Impact Player
    must free him up, not bowl7 (bat .05). mid5 (bat .15) is the weakest player who does
    not bowl at all, so a bowling Impact Player must free him up, not the keeper -- who
    is weaker still (.01) but is never a candidate."""
    return [
        _card("op1", bat=0.30), _card("op2", bat=0.28), _card("top3", bat=0.25),
        _card("mid4", bat=0.20), _card("mid5", bat=0.15),
        _card("keeper", bat=0.01, role="keeper"),
        _card("bowl7", bat=0.05, bowl=0.15), _card("bowl8", bat=0.02, bowl=0.20),
        _card("bowl9", bowl=0.25), _card("bowl10", bowl=0.22), _card("bowl11", bowl=0.10),
    ]


def opp_side(bat=0.05, bowl=None):
    xi = [_card(f"o{i}", bat=bat, bowl=bowl) for i in range(11)]
    return Side(name="OPP", short="OPP", xi=xi)


def test_pure_bowler_678_takes_the_weaker_bat_of_the_two_candidates():
    target = _pure_bowler_678(own_xi())
    assert target is not None and target.name == "bowl8"


def test_pure_bowler_678_is_none_when_no_bowler_sits_at_six_seven_or_eight():
    """Replaces positions 7 and 8 with non-bowlers IN PLACE, keeping bowl9-11 at their
    original positions 9-11 -- appending replacements at the end instead would shift
    bowl9-11 up into slots 6-8, changing what the test claims to check."""
    xi = own_xi()
    xi[6] = _card("nonbowler7", bat=0.10)
    xi[7] = _card("nonbowler8", bat=0.08)
    assert _pure_bowler_678(xi) is None


def test_weakest_pure_batter_never_picks_the_keeper():
    """The keeper (.01) rates below mid5 (.15) and does not bowl either -- exactly the
    shape that would trip a naive `min` over every non-bowler. The keeper must never be a
    swap-out candidate at all."""
    target = _weakest_pure_batter(own_xi())
    assert target is not None and target.name == "mid5"


def test_weakest_bowler_is_measured_within_the_bowlers_not_against_a_non_bowler():
    """bowl11 (.10) is the weakest of the five who actually bowl -- mid5, who does not
    bowl at all, must never enter this comparison (see `_weakest_bowler`'s own docstring
    for why that would be a sentinel every time)."""
    target = _weakest_bowler(own_xi())
    assert target is not None and target.name == "bowl11"


def test_a_big_enough_raw_gain_plays_regardless_of_the_matchup():
    """IMPACT_TOO_GOOD_GAIN: a batting Impact Player far better than bowl8 plays even
    against a side with no matching threat at all."""
    xi = own_xi()
    impact = _card("impact", bat=0.02 + IMPACT_TOO_GOOD_GAIN + 0.1)
    weak_opponent = opp_side(bat=0.0, bowl=None)
    discipline, target = decide_impact(
        Side(name="ME", short="ME", xi=xi, impact=impact), weak_opponent)
    assert discipline == "bat"
    assert target.name == "bowl8"


def test_a_big_enough_bowling_gain_also_overrides_the_matchup():
    xi = own_xi()
    impact = _card("impact", bowl=0.15 + IMPACT_TOO_GOOD_GAIN + 0.1)
    weak_opponent = opp_side(bat=0.0, bowl=None)
    discipline, target = decide_impact(
        Side(name="ME", short="ME", xi=xi, impact=impact), weak_opponent)
    assert discipline == "bowl"
    assert target.name == "mid5"


def test_he_sits_out_when_neither_discipline_clears_the_bar():
    xi = own_xi()
    # gain_bat == 0 (ties bowl8), gain_bowl slightly negative (below mid5's bowl rating
    # via the no-rating sentinel) -- and an opponent with nothing to exploit either way.
    impact = _card("impact", bat=0.02, bowl=0.10)
    weak_opponent = opp_side(bat=0.0, bowl=None)
    discipline, target = decide_impact(
        Side(name="ME", short="ME", xi=xi, impact=impact), weak_opponent)
    assert (discipline, target) == (None, None)


def test_a_strong_opposing_batting_order_can_flip_the_choice_to_bowling():
    """Raw gain alone would favour batting here (.18 vs .15) -- the situational weight on
    the opponent's batting strength is what has to flip it to bowling."""
    xi = own_xi()
    impact = _card("impact", bat=0.20, bowl=0.30)   # gain_bat .18, gain_bowl .15
    strong_batting_opponent = opp_side(bat=0.6, bowl=None)   # no bowlers: opp_bowl = 0
    discipline, target = decide_impact(
        Side(name="ME", short="ME", xi=xi, impact=impact), strong_batting_opponent)
    assert discipline == "bowl"
    assert target.name == "mid5"


def test_impact_xi_only_substitutes_for_the_discipline_actually_chosen():
    xi = own_xi()
    impact = _card("impact", bat=1.0)
    me = Side(name="ME", short="ME", xi=xi, impact=impact)

    batting, bat_impact = _impact_xi(me, "bat", xi[7], "bat")   # xi[7] is bowl8
    assert bat_impact is impact
    assert impact in batting and xi[7] not in batting

    bowling, bowl_impact = _impact_xi(me, "bat", xi[7], "bowl")
    assert bowl_impact is None
    assert bowling == xi, "chosen for batting only -- the bowling pool must be untouched"


def test_impact_xi_is_a_no_op_when_he_sits_out():
    xi = own_xi()
    me = Side(name="ME", short="ME", xi=xi, impact=_card("impact", bat=0.02))
    for wanted in ("bat", "bowl"):
        result_xi, played = _impact_xi(me, None, None, wanted)
        assert result_xi == xi and played is None


def test_a_side_with_no_impact_player_never_substitutes():
    xi = own_xi()
    me = Side(name="ME", short="ME", xi=xi, impact=None)
    assert decide_impact(me, opp_side()) == (None, None)


# --- A50's invariant: the attack needs BOWLERS_IN_TWELVE, whatever the situation says ---

def thin_bowling_xi():
    """Like `own_xi`, but only FOUR of the eleven bowl -- bowl11 replaced with a plain
    batter. A legally drafted twelve relies on the Impact Player himself to reach
    BOWLERS_IN_TWELVE here, so `decide_impact` must never let him sit that out."""
    xi = own_xi()
    xi[10] = _card("extra_bat", bat=0.12)
    return xi


def test_he_must_bowl_when_the_xi_alone_falls_short_of_the_attack():
    """A batting gain this large would win the situational comparison outright on its
    own (it clears IMPACT_TOO_GOOD_GAIN for batting too) -- the bowling requirement has
    to override it, not just edge it out on points."""
    xi = thin_bowling_xi()
    impact = _card("impact", bat=1.0, bowl=0.05)
    discipline, target = decide_impact(
        Side(name="ME", short="ME", xi=xi, impact=impact), opp_side())
    assert discipline == "bowl"
    assert target.name == "extra_bat", "still the weakest pure batter, unchanged rule"


def test_a_squad_that_cannot_reach_five_bowlers_even_with_impact_is_reported():
    """Not reachable from a legally drafted twelve (order_errors already requires the
    TWELVE to clear BOWLERS_IN_TWELVE) -- only from a hand-built Side, which is exactly
    what this test is. Must be reported, not silently field an attack `choose_bowler`
    cannot serve."""
    xi = thin_bowling_xi()
    impact = _card("impact", bat=1.0)   # no bowling at all
    with pytest.raises(ValueError):
        decide_impact(Side(name="ME", short="ME", xi=xi, impact=impact), opp_side())


# --- the toss, a human match that pauses for it, and the season-level resume ------------

class _Model:
    """A `Model` stand-in with a fixed (over, wickets)-independent distribution -- these
    tests are about the toss/Impact bookkeeping around a match, not the state grid
    itself, and no local database exists to fit a real one against (CLAUDE.md)."""

    def __init__(self, probs, values, wide_rate=0.0, wide_runs=1.0, extras_rate=0.0):
        self._probs, self._values = probs, values
        self.wide_rate = wide_rate
        self.wide_runs = wide_runs
        self.extras_rate = extras_rate

    def state(self, over, wickets):
        return self._probs, self._values


def _model():
    # Middling, non-degenerate mix -- realistic enough that both sides' innings run a
    # full twenty overs almost always, without needing to be tuned per test.
    probs = (0.06, 0.20, 0.30, 0.16, 0.08, 0.12, 0.02, 0.06)
    values = (-6.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    return _Model(probs, values)


def _xi(tag):
    """Eleven Cards, six of whom bowl -- enough for `attack()` to field a full five-man
    attack, which `own_xi()`/`opp_side()` above never needed (those are exercised only
    through `decide_impact` directly, never through a real simulated innings)."""
    # Every card carries a real `bat` value, never `None` -- these tests need no
    # `unrated_bat`/`season_mean` fallback machinery on `_Model`, since a card with a
    # real batting rating never touches it (see `bat_delta` in `game/__main__.py`).
    return [
        _card(f"{tag}k", bat=0.10, role="keeper"),
        _card(f"{tag}b1", bat=0.20), _card(f"{tag}b2", bat=0.15),
        _card(f"{tag}b3", bat=0.10), _card(f"{tag}b4", bat=0.05),
        _card(f"{tag}ar1", bat=0.05, bowl=0.10), _card(f"{tag}ar2", bat=0.02, bowl=0.12),
        _card(f"{tag}bw1", bat=0.0, bowl=0.14), _card(f"{tag}bw2", bat=0.0, bowl=0.16),
        _card(f"{tag}bw3", bat=0.0, bowl=0.18), _card(f"{tag}bw4", bat=0.0, bowl=0.20),
    ]


def bowling_opp(bat=0.05):
    """Unlike `opp_side` above, this opponent can actually bowl -- needed here because
    `_play_human_match` really simulates both innings, and an attack with no bowlers at
    all would leave `choose_bowler` nothing to hand an over to."""
    xi = [_card(f"o{i}", bat=bat) for i in range(6)]
    xi += [_card(f"ob{i}", bat=0.0, bowl=0.15) for i in range(5)]
    return Side(name="OPP", short="OPP", xi=xi)


class _Moves:
    """A minimal stand-in for `web/season_session.py`'s `MoveCursor` -- only the two
    methods `_next_toss`/`_next_impact` ever call. Kept local rather than importing
    `web/`: nothing else in `game/` depends on it, and this file shouldn't be the first
    exception."""

    def __init__(self, tosses=(), impacts=()):
        self._tosses = list(tosses)
        self._impacts = list(impacts)

    def next_toss(self, human_match_no):
        return self._tosses.pop(0) if self._tosses else None

    def next_impact(self, human_match_no):
        return self._impacts.pop(0) if self._impacts else None


def test_toss_default_elects_is_declared_as_bowl():
    assert TOSS_DEFAULT_ELECTS == "bowl"


def test_toss_is_a_one_draw_coin_flip():
    # Seeds picked by inspection (`random.Random(seed).random()`), not swept -- `toss`
    # is a one-line function and this only needs to show both outcomes are reachable.
    assert toss(random.Random(1)) is True
    assert toss(random.Random(0)) is False


def test_a_lost_toss_never_asks_for_a_move_and_uses_the_default():
    human = Side(name="ME", short="ME", xi=_xi("h"))
    opponent = bowling_opp()
    r = _play_human_match(_model(), human, opponent, random.Random(0), "league", 0,
                           moves=_Moves())
    assert r.toss_won_by_you is False
    assert r.toss_elected == TOSS_DEFAULT_ELECTS


def test_winning_the_toss_with_no_move_available_pauses_the_match():
    human = Side(name="ME", short="ME", xi=_xi("h"))
    opponent = bowling_opp()
    with pytest.raises(_MatchNeedsToss):
        _play_human_match(_model(), human, opponent, random.Random(1), "league", 0,
                           moves=_Moves())


def test_moves_none_is_fully_automatic_and_never_pauses_win_or_lose():
    """The CLI/no-web path: `moves=None` must behave as an always-auto source, exactly
    the always-default answer `_next_toss`/`_next_impact` fall back to."""
    for seed in (0, 1):     # one where the toss is lost, one where it is won
        human = Side(name="ME", short="ME", xi=_xi("h"), impact=_card("h_imp", bat=0.5))
        opponent = bowling_opp()
        r = _play_human_match(_model(), human, opponent, random.Random(seed),
                               "league", 0, moves=None)
        assert r.toss_elected == TOSS_DEFAULT_ELECTS


def test_the_break_with_no_move_available_pauses_for_impact():
    human = Side(name="ME", short="ME", xi=_xi("h"), impact=_card("h_imp", bat=0.5))
    opponent = bowling_opp()
    with pytest.raises(_MatchNeedsImpact):
        _play_human_match(_model(), human, opponent, random.Random(0), "league", 0,
                           moves=_Moves())


def test_a_side_with_no_impact_player_never_pauses_for_one():
    human = Side(name="ME", short="ME", xi=_xi("h"))   # impact=None
    opponent = bowling_opp()
    r = _play_human_match(_model(), human, opponent, random.Random(0), "league", 0,
                           moves=_Moves())
    assert r is not None


def test_the_humans_first_innings_never_carries_the_impact_player():
    """Won the toss (seed 1), elected to bat -- human bats FIRST, so the break-time
    decision can only affect innings 2 (`_play_human_match` fixes this discipline as
    'bowl' here). Even an Impact Player who would clearly play -- he clears
    IMPACT_TOO_GOOD_GAIN against this opponent's weakest bowler -- must not appear
    anywhere in innings 1's batting card; declining at the break should still let him
    appear in innings 2, which is what proves the block is specific to innings 1 rather
    than a blanket 'never plays' bug."""
    xi = _xi("h")
    impact = _card("impact", bat=0.0, bowl=0.14 + IMPACT_TOO_GOOD_GAIN + 0.1)
    human = Side(name="ME", short="ME", xi=xi, impact=impact)
    opponent = bowling_opp()
    r = _play_human_match(_model(), human, opponent, random.Random(1), "league", 0,
                           moves=_Moves(tosses=[TossElect("bat")], impacts=[ImpactPick(None)]))
    assert r.home is human
    assert all(not b.player.is_impact for b in r.home_innings.batting)
    assert any(bo.player.is_impact for bo in r.away_innings.bowling)


def test_an_explicit_slot_overrides_decide_impact_even_when_it_would_sit_him_out():
    """Neither discipline clears IMPACT_SIT_OUT_BAR against this matchup -- verified
    directly against `decide_impact` first, the same shape as
    `test_he_sits_out_when_neither_discipline_clears_the_bar` above. An explicit slot
    must still force him in regardless: the plan's own "no new legality or desirability
    check on the human's own swap" simplification."""
    xi = _xi("h")
    # 0.13 sits out on its own (below the sit-out bar against this matchup) but still
    # ranks ABOVE the man at slot 7 he explicitly replaces (0.10) -- high enough that,
    # once forced in, `attack()`'s own re-ranking by rating does not immediately drop
    # him again for a different reason than the one this test is pinning.
    impact = _card("impact", bat=0.0, bowl=0.13)
    human = Side(name="ME", short="ME", xi=xi, impact=impact)
    opponent = bowling_opp()
    assert decide_impact(human, opponent) == (None, None), "sanity: sits out undirected"
    r = _play_human_match(_model(), human, opponent, random.Random(1), "league", 0,
                           moves=_Moves(tosses=[TossElect("bat")], impacts=[ImpactPick(7)]))
    assert any(bo.player.is_impact for bo in r.away_innings.bowling)


# --- the season-level pause: the same mechanism, wrapped with match/stage context --------

def _four_a_side(human_impact=True):
    human = Side(name="ME", short="ME", xi=_xi("h"),
                 impact=_card("h_imp", bat=0.5) if human_impact else None)
    others = [Side(name=f"O{i}", short=f"O{i}", xi=_xi(f"o{i}")) for i in range(3)]
    return [human] + others, human


def test_run_league_raises_the_season_scoped_need_toss_enriched_with_match_context():
    sides, human = _four_a_side()
    with pytest.raises(NeedToss) as exc:
        run_league(_model(), sides, random.Random(1), track=human, moves=_Moves())
    assert exc.value.stage == "league"
    assert exc.value.human_match_no == 0


def test_run_league_resumes_past_a_supplied_toss_and_then_needs_impact():
    """Replay from scratch with one more move recorded -- exactly SPEC 11.3's contract,
    proven here at the season level rather than only at the bare match level above."""
    sides, human = _four_a_side()
    with pytest.raises(NeedImpact) as exc:
        run_league(_model(), sides, random.Random(1), track=human,
                   moves=_Moves(tosses=[TossElect("bat")]))
    assert exc.value.stage == "league"
    assert exc.value.human_match_no == 0


def test_run_league_completes_the_fixture_and_moves_to_the_next_once_both_are_supplied():
    sides, human = _four_a_side()
    # Whichever of NeedToss/NeedImpact the SECOND human fixture pauses on next (its own
    # toss draw decides which -- irrelevant here), `human_match_no` must have advanced
    # to 1, proof the FIRST fixture resolved completely rather than pausing again on it.
    with pytest.raises((NeedToss, NeedImpact)) as exc:
        run_league(_model(), sides, random.Random(1), track=human,
                   moves=_Moves(tosses=[TossElect("bat")], impacts=[ImpactPick(None)]))
    assert exc.value.human_match_no == 1


def test_replaying_the_same_recorded_moves_twice_gives_an_identical_season():
    sides, human = _four_a_side()
    moves = (TossElect("bat"), ImpactPick(None), TossElect("bowl"))

    def _play_out():
        s2, h2 = _four_a_side()
        try:
            run_league(_model(), s2, random.Random(1), track=h2, moves=_Moves(
                tosses=[m for m in moves if isinstance(m, TossElect)],
                impacts=[m for m in moves if isinstance(m, ImpactPick)]))
        except NeedToss as exc:
            return exc.human_match_no
        except NeedImpact as exc:
            return exc.human_match_no
        return None

    assert _play_out() == _play_out()


# --- the regression anchor: `web/rooms.py` never sets `track`, and never may ------------

def test_no_tracked_side_means_the_human_match_path_can_never_fire(monkeypatch):
    """`web/rooms.py`'s `room_match` route calls `run_league(model, sides, rng)` and
    `run_playoffs(model, season, rng)` with no `track` and no `moves` at all -- exactly
    as it did before this feature existed, and it must go on doing so untouched.
    Proven directly rather than by comparing numbers: the human-match path is made to
    explode if it is ever reached, and a full ten-team season is played through it."""
    import game.season as season_mod

    def _boom(*a, **k):
        raise AssertionError("the human-match path fired with track=None")

    monkeypatch.setattr(season_mod, "_play_human_match", _boom)

    sides = [Side(name=f"T{i}", short=f"T{i}", xi=_xi(f"t{i}")) for i in range(TEAMS)]
    rng = random.Random(2024)
    league = run_league(_model(), sides, rng)
    playoffs = run_playoffs(_model(), league, rng)
    assert len(league.results) == TEAMS * MATCHES_EACH // 2
    assert len(playoffs.playoffs) == 4
