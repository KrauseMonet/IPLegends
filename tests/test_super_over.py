"""The super over: how a tied match is decided, and what it is not allowed to touch.

Every rule here is behaviour rather than schema, so by CLAUDE.md's standing rule every
one of them needs a test or it is unprotected. Two kinds of rule live in this file and
they fail in very different ways.

The first kind announces itself: a super over that fielded four batters, or ran two overs,
is wrong the moment anyone looks at a scorecard. The second kind is silent, and it is the
one worth the effort -- super over runs must reach net run rate, the Orange and Purple
Caps, the journey card and Season Analysis in exactly no way at all. That holds today
because a super over's innings live on `Result.super_overs` and every one of those
consumers reads `home_innings`/`away_innings`, which is a property of where the objects
are kept and not of any rule a reader would see. Move them and nothing else in the suite
would notice; the tournament's own arithmetic would simply start counting an over nobody
should be able to see.
"""

from __future__ import annotations

import random

import pytest

from etl.feasibility import Card
from game.season import (
    MAX_SUPER_OVERS, SUPER_OVER_BATTERS, SUPER_OVER_STATE_OVER, SUPER_OVER_WICKETS,
    JourneyAccumulator, Side, _credit, play, play_one_super_over, play_super_overs,
    super_over_batters, super_over_bowler, tournament_leaders,
)
from etl.state_model import bucket_of
from game.simulator import BALLS_PER_OVER, OVERS


# --- stubs -------------------------------------------------------------------------------

class _FixedModel:
    """A `Model` stand-in whose distribution never varies with (over, wickets).

    `seen` records every (over, wickets) it was ASKED for, which is how the state-pinning
    rule below is checked -- the distribution being constant is exactly what makes the
    question "which state did you ask for" answerable in isolation from "what did you do
    with the answer"."""

    def __init__(self, probs, values, wide_rate=0.0, wide_runs=1.0, extras_rate=0.0):
        self._probs, self._values = probs, values
        self.wide_rate = wide_rate
        self.wide_runs = wide_runs
        self.extras_rate = extras_rate
        self.seen: list[tuple[int, int]] = []

    def state(self, over, wickets):
        self.seen.append((over, wickets))
        return self._probs, self._values


# Index 0 is the (negative) wicket cost; 1-7 are OFF_THE_BAT (0..6 runs). So mass on
# index 2 is "every ball a single", and on index 0 "every ball a wicket".
_VALUES = (-1.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)


def _model(probs):
    return _FixedModel(probs, _VALUES)


def _singles():
    return _model((0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0))


def _wickets():
    return _model((1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0))


def _card(name, bat=0.0, bowl=None, role="batter"):
    """Same shape `tests/test_season.py` builds -- `person_id` is the name, since these
    fixtures are looked up by identity in the journey/leaders tests below and this file
    must not key a player on a name string any more than the engine does."""
    return Card(1, name, name, bat=bat, bowl=bowl, role=role,
                positions=frozenset({1, 2, 3}))


def _side(tag, bats=(0.30, 0.25, 0.20, 0.15, 0.10, 0.05), impact=None):
    """Eleven in batting order: six batters, then five bowlers on descending bowling
    ratings, so the batting nomination (position) and the bowling one (rating) are
    unambiguous and independent of each other."""
    xi = [_card(f"{tag}bat{i}", bat=b) for i, b in enumerate(bats)]
    xi += [_card(f"{tag}bowl{i}", bat=0.0, bowl=0.30 - 0.05 * i) for i in range(5)]
    return Side(name=tag, short=tag[:3].upper(), xi=xi, impact=impact)


# --- nomination --------------------------------------------------------------------------

def test_three_batters_are_nominated_off_the_top_of_the_batting_order():
    side = _side("home")
    picked = super_over_batters(side)
    assert len(picked) == SUPER_OVER_BATTERS
    assert [c.name for c in picked] == ["homebat0", "homebat1", "homebat2"]


def test_a_tailender_with_a_freak_per_ball_rating_is_still_a_tailender():
    """The live regression, reproduced as a fixture.

    `Card.bat` is `rated_per_ball`, A65 rates every season that faced a ball, and A66
    shrinks a per-ball figure on balls -- so a man who scored one run off one ball can
    carry the highest batting number in the twelve. A real drafted season sent in
    P Parameswaran and Mustafizur Rahman ahead of Shikhar Dhawan and Rinku Singh, and
    Mustafizur's own +0.2612 came off exactly one ball. Nominating off the ORDER is
    immune to it, because the order holds no per-ball quantity at all."""
    xi = [_card(f"bat{i}", bat=0.05 - 0.005 * i) for i in range(9)]
    xi.append(_card("freak10", bat=0.99))          # one run off one ball, position 10
    xi.append(_card("freak11", bat=0.98, bowl=0.2))
    side = Side(name="X", short="X", xi=xi)
    picked = [c.name for c in super_over_batters(side)]
    assert picked == ["bat0", "bat1", "bat2"]
    assert "freak10" not in picked and "freak11" not in picked


def test_the_bowler_is_the_best_in_the_eleven():
    assert super_over_bowler(_side("home")).name == "homebowl0"


def test_the_impact_player_is_never_nominated():
    """He is not in the eleven, and the eleven is what a super over is played from. His
    one substitution has usually been spent by the time a match is tied, and nothing
    here records whether it was."""
    star = _card("impactstar", bat=9.0, bowl=9.0)
    side = _side("home", impact=star)
    assert star not in super_over_batters(side)
    assert super_over_bowler(side) is not star


def test_a_repeat_rotates_past_the_batters_who_already_went_in():
    """The playing condition says a batter dismissed in one super over may not bat in the
    next. With at most two of three dismissed, moving the window past all three can never
    field somebody ineligible -- so the rotation reaches the rule without this engine
    having to track who was out."""
    side = _side("home")
    first = super_over_batters(side, 1)
    second = super_over_batters(side, 2)
    assert not (set(c.name for c in first) & set(c.name for c in second))
    assert [c.name for c in second] == ["homebat3", "homebat4", "homebat5"]


def test_a_repeat_changes_the_bowler():
    """A bowler may not bowl two super overs in succession."""
    side = _side("home")
    assert super_over_bowler(side, 2).name != super_over_bowler(side, 1).name
    assert super_over_bowler(side, 2).name == "homebowl1"


def test_an_eleven_with_nobody_who_bowls_still_gets_the_over_bowled():
    """Not reachable from a legal twelve, but reachable from a hand-built one -- and
    somebody still has to bowl it. The last man in the order gets it, which is what a
    real side does with an over it has no bowler for."""
    xi = [_card(f"b{i}", bat=0.30 - 0.02 * i) for i in range(11)]
    side = Side(name="NB", short="NB", xi=xi)
    assert super_over_bowler(side).name == "b10"


# --- the over itself ---------------------------------------------------------------------

def test_a_super_over_is_one_over_long():
    so = play_one_super_over(_singles(), _side("a"), _side("b"), random.Random(1))
    assert so.first_innings.balls == BALLS_PER_OVER
    assert so.second_innings.balls <= BALLS_PER_OVER


def test_the_innings_ends_on_the_second_wicket():
    """The observable rule, and all this one claims.

    It deliberately does NOT claim to pin `max_wickets`, because at three nominated
    batters it cannot: two dismissals leave one man with no partner, so `play_innings`'s
    own "ran out of batters" exit fires on exactly the ball `max_wickets` would. The two
    are coincident by construction and no test at this level can separate them --
    removing `max_wickets=SUPER_OVER_WICKETS` from `play_one_super_over` leaves every
    assertion here true, which was checked rather than assumed. The parameter is kept
    anyway because it states the playing condition's own rule, and it is pinned where it
    IS separable, one test below."""
    so = play_one_super_over(_wickets(), _side("a"), _side("b"), random.Random(1))
    assert so.first_innings.wickets == SUPER_OVER_WICKETS
    assert so.first_innings.balls == SUPER_OVER_WICKETS
    assert sum(b.faced_any for b in so.first_innings.batting) == SUPER_OVER_WICKETS


def test_max_wickets_ends_an_innings_early_on_its_own():
    """`play_innings`'s new bound, exercised where nothing else can be doing the work:
    FOUR batters, so running out of them cannot end the innings at two. Without the
    parameter this runs on to a third and fourth dismissal."""
    from game.simulator import Player, play_innings
    bats = [Player(f"b{i}", 0.0) for i in range(4)]
    innings = play_innings(_wickets(), bats, [Player("bow", 0.0, bowl=0.0)],
                            random.Random(1), max_wickets=SUPER_OVER_WICKETS)
    assert innings.wickets == SUPER_OVER_WICKETS
    assert sum(b.faced_any for b in innings.batting) == SUPER_OVER_WICKETS


def test_only_one_bowler_bowls_it():
    so = play_one_super_over(_singles(), _side("a"), _side("b"), random.Random(1))
    assert [b.player.name for b in so.first_innings.bowling] == ["bbowl0"]


def test_every_ball_is_priced_as_the_twentieth_over():
    """A super over's loop index is always 0, so an unpinned state would price six
    all-out slogging balls as a cagey opening over.

    Only the OVER is pinned; the wickets half is passed through untouched. It never
    actually varies either, but that is a separate fact with its own test below -- it is
    NOT asserted here, because a model that produces no wickets makes any such assertion
    pass whatever the constants say. Checked rather than assumed: with `_singles` this
    test passed unchanged after `SUPER_OVER_BATTERS`/`SUPER_OVER_WICKETS` were widened to
    4 and 3, which is precisely the vacuous check this project refuses to keep."""
    model = _singles()
    play_one_super_over(model, _side("a"), _side("b"), random.Random(1))
    assert model.seen, "no state was ever asked for"
    assert {over for over, _ in model.seen} == {SUPER_OVER_STATE_OVER}
    assert SUPER_OVER_STATE_OVER == OVERS - 1


def test_a_super_over_can_never_leave_one_wicket_bucket():
    """Why the wicket half of the state does not vary, asserted where it can actually
    fail: every wicket count a super over can REACH falls in one bucket.

    The innings ends on the second wicket, so 0 and 1 are the only counts a ball is ever
    bowled at, and `bucket_of` puts both in "0-1". Widen the trio past that boundary and
    this fails -- which is the point of writing it as a statement about the constants
    rather than as an observation of one simulated over."""
    reachable = {bucket_of(w) for w in range(SUPER_OVER_WICKETS)}
    assert reachable == {bucket_of(0)}, (
        "a super over can now reach more than one wicket bucket, so the state really does "
        "vary and every comment saying it does not is wrong")


def test_the_side_that_batted_second_in_the_match_bats_first_in_the_super_over():
    home, away = _side("home"), _side("away")
    played = play_super_overs(_singles(), home, away, random.Random(1))
    assert played[0].first is away
    assert played[0].second is home


# --- a tied match reaches it, and a decided one does not ---------------------------------

def _tied_match(model, home, away, seeds=6000):
    """The first seed on which the ordinary twenty overs finish level. Searched rather
    than contrived: a hand-built tie would prove the super over runs, not that a real
    match reaches it."""
    for seed in range(seeds):
        r = play(model, home, away, random.Random(seed))
        if r.home_runs == r.away_runs:
            return r
    pytest.fail("no tie found")


def _match_model():
    """Middling and non-degenerate, so both innings run their full twenty and a level
    finish is reachable rather than certain."""
    return _model((0.06, 0.20, 0.30, 0.16, 0.08, 0.12, 0.02, 0.06))


def test_a_tied_match_is_decided_by_a_super_over():
    r = _tied_match(_match_model(), _side("home"), _side("away"))
    assert r.super_overs, "a level match was left drawn"
    assert r.winner is not None
    assert r.winner is r.super_overs[-1].winner
    assert "super over" in r.margin


def test_a_decided_match_plays_none_at_all():
    r = play(_match_model(), _side("home"), _side("away"), random.Random(0))
    assert r.home_runs != r.away_runs
    assert r.super_overs == []


def test_the_decisive_super_over_is_the_last_one_and_every_earlier_one_was_tied():
    played = play_super_overs(_match_model(), _side("home"), _side("away"),
                               random.Random(3))
    assert played[-1].winner is not None or len(played) == MAX_SUPER_OVERS
    assert all(so.winner is None for so in played[:-1])
    assert [so.number for so in played] == list(range(1, len(played) + 1))


def test_a_tied_super_over_is_replayed_and_the_repeat_is_bounded():
    """Every ball a dot ties every super over, forever -- the cap is what stops the loop.
    Reaching it leaves the match TIED, which is why `POINTS_TIE` and the seed fallbacks
    are kept rather than deleted."""
    dots = _model((0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
    played = play_super_overs(dots, _side("home"), _side("away"), random.Random(1))
    assert len(played) == MAX_SUPER_OVERS
    assert all(so.winner is None for so in played)


def test_a_match_still_level_after_the_cap_stays_tied():
    dots = _model((0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
    r = play(dots, _side("home"), _side("away"), random.Random(1))
    assert r.home_runs == r.away_runs
    assert r.winner is None
    assert r.margin == f"tied after {MAX_SUPER_OVERS} super overs"


# --- what a super over must NOT touch ----------------------------------------------------

def test_net_run_rate_never_sees_a_super_over():
    """The IPL's own rule, and here it holds by construction: `_credit` is fed the match
    innings' own runs and balls, which a super over does not change. Checked against a
    real tied match rather than by reading the code -- what is being pinned is that the
    numbers `Result` reports for the match are still the twenty-over ones."""
    r = _tied_match(_match_model(), _side("home"), _side("away"))
    assert r.home_runs == r.home_innings.runs
    assert r.away_runs == r.away_innings.runs
    assert r.home_balls == r.home_innings.balls
    assert r.away_balls == r.away_innings.balls
    assert r.home_runs == r.away_runs, "the match itself finished level"

    from game.season import Standing
    s = Standing(side=r.home)
    _credit(s, r.home_runs, r.home_balls, r.home_wickets,
            r.away_runs, r.away_balls, r.away_wickets)
    assert s.runs_for == r.home_innings.runs
    assert s.runs_against == r.away_innings.runs


def _innings(bat, bowl):
    """An `Innings` built by hand from (name, runs) and (name, wickets) pairs.

    Hand-built rather than simulated for the two leaderboard tests below, and that choice
    is the point of them. A simulated tie's super over is a handful of runs to three of
    the side's best-rated batters, and the tournament's leading scorer is very often
    somebody else entirely -- so an implementation that WAS folding super overs in still
    reported the same leader, and the first version of these tests passed against exactly
    the break they exist to catch. Reproduced, not assumed: the top scorer came out
    `awaybowl0` on 67 while the super over's largest contribution was 7.

    Built here so the fold, if it ever happened, MUST change the reported answer."""
    from game.simulator import BatterCard, BowlerCard, Innings, Player
    cards = []
    for name, runs in bat:
        c = BatterCard(Player(name, 0.0, person_id=name))
        c.runs, c.balls, c.faced_any = runs, max(runs, 1), True
        cards.append(c)
    attack = []
    for name, wkts in bowl:
        b = BowlerCard(Player(name, 0.0, bowl=0.0, person_id=name))
        b.wickets, b.balls = wkts, 6
        attack.append(b)
    inn = Innings(cards, attack)
    inn.runs = sum(r for _, r in bat)
    inn.wickets = sum(w for _, w in bowl)
    return inn


def _hand_built_tie():
    """A tied match whose super over would REORDER both leaderboards if it were counted.

    Batting: HOME's `hbat` leads the match on 50 to AWAY's `abat` on 40, and `abat` makes
    20 more in the super over -- fold it in and the Orange Cap flips to `abat` on 60.
    Bowling: HOME's `hbowl` leads on 3 to AWAY's `abowl` on 2, and `abowl` takes 2 more
    in the super over, so the Purple Cap flips to `abowl` on 4.
    """
    from game.season import Result, SuperOver
    home, away = _side("home"), _side("away")
    home_inn = _innings([("hbat", 50), ("hbat2", 10)], [("abowl", 2)])
    away_inn = _innings([("abat", 40), ("abat2", 20)], [("hbowl", 3)])
    so_first = _innings([("abat", 20)], [("hbowl", 0)])
    so_second = _innings([("hbat", 5)], [("abowl", 2)])
    r = Result(home=home, away=away,
               home_runs=60, home_wickets=1, home_balls=120,
               away_runs=60, away_wickets=1, away_balls=120,
               home_innings=home_inn, away_innings=away_inn)
    r.super_overs = [SuperOver(number=1, first=away, second=home,
                                first_innings=so_first, second_innings=so_second,
                                winner=away)]
    r.winner, r.margin = away, "AWA won the super over, 20-5"
    return r


def test_the_orange_cap_never_counts_a_super_over():
    r = _hand_built_tie()
    assert tournament_leaders([r]).top_scorer == ("hbat", 50)


def test_the_purple_cap_never_counts_a_super_over():
    r = _hand_built_tie()
    assert tournament_leaders([r]).top_wicket_taker == ("hbowl", 3)


def test_the_journey_card_never_counts_a_super_over():
    """`JourneyAccumulator` is fed the match innings by `play()` itself, and this pins
    that the totals it ends up with are the ones the two match scorecards show.

    The precondition is asserted rather than hoped for: a super over in which the tracked
    side happened to score nothing would make this pass whatever the accumulator did."""
    home, away = _side("home"), _side("away")
    model = _match_model()
    for seed in range(6000):
        stats = JourneyAccumulator()
        r = play(model, home, away, random.Random(seed), track=home, stats=stats)
        if not r.super_overs or r.super_overs[-1].second_innings.runs == 0:
            continue
        assert sum(stats.runs.values()) == sum(b.runs for b in r.home_innings.batting)
        assert sum(stats.wickets.values()) == sum(
            bo.wickets for bo in r.away_innings.bowling)
        return
    pytest.fail("no tie with a scoring super over found")


# --- the wire format ----------------------------------------------------------------------
#
# A109's lesson, applied before it can bite rather than after: `web/app.py`'s mapping of an
# engine object onto a response model had no test at all, and a name collision made
# `/api/profile` return 500 for every account that had actually played -- invisible to every
# test, because the empty case never constructs the model. A super over is the same shape of
# risk: it is on about one match in a hundred and fifty, so a shape bug here would sit
# unnoticed until a real player tied one.

def test_a_tied_result_maps_onto_the_wire_format():
    """Every field the frontend reads, built from a real `Result` through the real mapper."""
    from game.season import Side
    from web.app import ResultOut, _result_out

    r = _hand_built_tie()
    you = Side(name="you", short="YOU", xi=list(r.home.xi))
    out = _result_out(r, you)
    assert isinstance(out, ResultOut)
    assert len(out.super_overs) == 1
    so = out.super_overs[0]
    assert (so.number, so.first, so.second, so.winner) == (1, "AWA", "HOM", "AWA")
    # "5/2": `_hand_built_tie` gives that innings' bowler two wickets, so the chasing
    # side really did lose both -- the score is read off the innings, not assumed.
    assert (so.first_score, so.second_score) == ("20/0", "5/2")
    assert so.first_innings is not None and so.second_innings is not None
    assert [b.name for b in so.first_innings.batting] == ["abat"]


def test_a_decided_result_carries_an_empty_super_over_list():
    """Empty rather than absent, so the frontend never has to test for the field itself."""
    from game.season import Side
    from web.app import _result_out

    r = _hand_built_tie()
    r.super_overs = []
    r.winner, r.margin = r.home, "HOM by 5 runs"
    assert _result_out(r, Side(name="you", short="YOU", xi=list(r.home.xi))).super_overs == []


def test_a_daily_carries_its_super_over_through_to_the_scorecard():
    """The daily reaches `play()` too, so a level daily really can go to one -- and it is
    SHOWN and never scored, since a day's objective is a margin and a level match has a
    margin of zero however the super over went.

    `_daily_match_out` builds its own dict rather than a `ResultOut`, so it is a second
    place the field could simply have been forgotten. `winner` stays null on a level
    match on purpose: the DAY was not won, whatever happened afterwards."""
    from game.scenarios import Outcome
    from web.app import _daily_match_out
    from web.daily import DayPlay

    r = _hand_built_tie()
    outcome = Outcome(objective_met=False, margin=0, bonuses_met=(), bonus_points=0,
                       summary="tied")
    play = DayPlay(outcome, r.home_innings, r.away_innings, "You", "Them",
                    player_bats_first=True, super_overs=list(r.super_overs))
    out = _daily_match_out(play, _DailyScenario())

    assert out["winner"] is None, "a level match has no winner in the day's own terms"
    assert len(out["super_overs"]) == 1
    assert out["super_overs"][0]["winner"] == "AWA"
    assert out["super_overs"][0]["first_score"] == "20/0"


class _DailyScenario:
    stage = "Final"
