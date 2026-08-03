"""SPEC 1.1. Pure tests for the simulator rules that no constraint can protect.

The engine has no schema. Every rule in it is behaviour, so by the standing rule in
CLAUDE.md every rule in it needs a test or it is unprotected -- and the ones here are the
kind that break silently. A tilt that misses its target mean still returns a valid
distribution and still plays a plausible-looking match; the scoreboard would just quietly
stop agreeing with the ratings that produced it.

`--validate` is the other half and cannot live here: it needs the database, and it checks
the engine against the archive rather than against itself. These check the arithmetic.
"""

from __future__ import annotations

import random

import pytest

from etl.feasibility import BOWLERS_IN_TWELVE, Card
from game.__main__ import OVERSEAS_LIMIT, attack, enforce_overseas, lineup, viable
from game.simulator import (
    BALLS_PER_OVER, MAX_OVERS_PER_BOWLER, OVERS, WICKETS, BowlerCard, Player,
    choose_bowler, play_innings, tilt,
)

# A death-overs state: dots and boundaries, a wicket worth -12 runs. Shaped like a real
# stored cell rather than a uniform toy, because the tilt's whole job is to preserve shape.
PROBS = (0.08, 0.30, 0.32, 0.10, 0.01, 0.12, 0.00, 0.07)
VALUES = (-12.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)


def mean(probs, values=VALUES) -> float:
    return sum(p * v for p, v in zip(probs, values))


# --- the tilt: the one piece of arithmetic tying the scoreboard to the ratings ---------

def test_a_zero_delta_leaves_the_state_distribution_alone():
    """The engine must reduce to the state model when nobody is above average.

    This is what makes `--validate` meaningful: with every delta zero the simulated league
    has to reproduce the real one, and it can only do that if the tilt is the identity here.
    """
    assert tilt(PROBS, VALUES, mean(PROBS)) == pytest.approx(PROBS, abs=1e-9)


@pytest.mark.parametrize("delta", [-0.6, -0.25, -0.05, 0.05, 0.25, 0.6, 1.5])
def test_the_tilt_lands_on_the_mean_it_was_asked_for(delta):
    """A rating is runs per ball. If the tilt misses, the rating stops meaning that."""
    assert mean(tilt(PROBS, VALUES, mean(PROBS) + delta)) == pytest.approx(
        mean(PROBS) + delta, abs=1e-6
    )


def test_the_tilt_returns_a_distribution():
    tilted = tilt(PROBS, VALUES, mean(PROBS) + 0.4)
    assert sum(tilted) == pytest.approx(1.0)
    assert all(p >= 0 for p in tilted)


def test_an_impossible_outcome_stays_impossible():
    """Reweighting cannot invent a five off the bat in a state that has never seen one.

    `q = p * exp(theta v)` multiplies, so a zero stays zero however hard the tilt pulls.
    Worth pinning: an additive adjustment would not have this property, and five of the
    stored states have outcomes with genuinely zero mass.
    """
    assert tilt(PROBS, VALUES, mean(PROBS) + 1.0)[6] == 0.0


def test_a_better_batter_scores_more_and_gets_out_less():
    """One theta moves both halves, in the direction and proportion the state's own shape
    implies. Nothing else in the engine decides how a rating splits between runs and
    survival, so if this ever inverted, nothing would catch it."""
    better = tilt(PROBS, VALUES, mean(PROBS) + 0.5)
    worse = tilt(PROBS, VALUES, mean(PROBS) - 0.5)
    assert better[0] < PROBS[0] < worse[0]          # index 0 is the dismissal
    assert better[7] > PROBS[7] > worse[7]          # index 7 is the six


def test_a_target_beyond_every_outcome_saturates_at_the_top_rather_than_inverting():
    """No finite theta gives a mean of 99, so the bisection runs to its rail. It must run to
    the RIGHT rail: the failure this pins is a solver that ends up at the far end and hands
    back an all-wickets distribution for the most aggressive target in the game.

    (The clamp inside `tilt` is belt-and-braces and no test here can distinguish it -- the
    theta rail saturates the distribution on its own. Only the search direction is pinned.)
    """
    tilted = tilt(PROBS, VALUES, 99.0)
    assert sum(tilted) == pytest.approx(1.0)
    assert tilted[7] > 0.99


# --- the attack: twenty overs, five bowlers, nobody twice in a row --------------------

def bowlers(n: int) -> list[BowlerCard]:
    return [BowlerCard(Player(f"b{i}", 0.0, 0.1 * i)) for i in range(n)]


def test_an_attack_of_five_bowls_four_overs_each_and_never_twice_running():
    attack_of_five = bowlers(BOWLERS_IN_TWELVE)
    previous = None
    for _ in range(OVERS):
        picked = choose_bowler(attack_of_five, previous)
        assert picked is not previous, "no bowler may bowl consecutive overs"
        picked.balls += BALLS_PER_OVER
        previous = picked
    assert all(b.balls == MAX_OVERS_PER_BOWLER * BALLS_PER_OVER for b in attack_of_five)


def test_a_bowler_is_not_recalled_immediately_even_when_he_is_the_obvious_choice():
    """The no-consecutive-overs rule only binds when the man who just bowled is also the one
    the tie-break wants: equal workloads, and he is the best bowler available. Sharing 20
    overs between five never reaches that state -- whoever just bowled always has the most
    balls -- so the test above passes with the rule deleted and this one is what holds it.
    """
    worse, better = bowlers(2)
    assert choose_bowler([worse, better], previous=better) is worse


def test_the_attack_is_capped_at_five_however_many_can_bowl():
    """An XI with seven bowlers would otherwise give all seven three overs each, and the
    best bowler in the side would bowl a quarter of their allocation."""
    xi = [Card(1, f"p{i}", f"p{i}", bat=0.0, bowl=0.1 * i) for i in range(7)]
    picked = attack(xi, model=None)
    assert len(picked) == BOWLERS_IN_TWELVE
    assert [p.name for p in picked] == ["p6", "p5", "p4", "p3", "p2"]


def test_attack_tags_the_impact_player_when_he_is_among_the_xi_it_is_given():
    """`attack` no longer widens a pool -- `game.season.decide_impact`/`_impact_xi`
    (game/season.py) decide, per match, whether the Impact Player is actually IN the
    eleven `attack` receives at all, substituted in for whoever he displaced. `attack`'s
    only remaining job regarding him is to tag his `Player.is_impact` when `impact=` names
    a card that's actually present -- it does not change who gets picked."""
    xi = [Card(1, f"p{i}", f"p{i}", bat=0.0, bowl=0.1 * i) for i in range(10)]
    impact = Card(1, "impact", "impact", bat=0.0, bowl=99.0)
    picked = attack(xi + [impact], model=None, impact=impact)
    assert len(picked) == BOWLERS_IN_TWELVE
    assert picked[0].name == "impact", "still just the best five by rating"
    assert picked[0].is_impact is True
    assert all(p.is_impact is False for p in picked[1:])


def test_attack_never_tags_anyone_when_no_impact_player_is_in_play():
    xi = [Card(1, f"p{i}", f"p{i}", bat=0.0, bowl=0.1 * i) for i in range(7)]
    picked = attack(xi, model=None)
    assert all(p.is_impact is False for p in picked)


def test_lineup_tags_the_impact_player_in_whatever_slot_he_occupies():
    xi = [Card(1, "impact" if i == 3 else f"p{i}", "impact" if i == 3 else f"p{i}",
                bat=0.0) for i in range(11)]
    impact = xi[3]
    players = lineup(xi, model=None, impact=impact)
    assert [p.is_impact for p in players] == [i == 3 for i in range(11)]


def test_lineup_tags_nobody_when_impact_is_none():
    xi = [Card(1, f"p{i}", f"p{i}", bat=0.0) for i in range(11)]
    assert all(not p.is_impact for p in lineup(xi, model=None))


# --- the four-overseas rule, enforced on what is KNOWN --------------------------------

def squad_card(i: int, overseas, *, keeper=False, bat=0.30, bowl=None) -> Card:
    return Card(1, f"p{i}", f"p{i}", bat=bat, bowl=bowl,
                role="keeper" if keeper else None, overseas=overseas)


# One XI, built so that the CHEAPEST swap by rating is also the illegal one. p5 is the
# fifth bowler and the worst-rated player in the side, so a repair reading ratings alone
# drops him and fields four bowlers; the four overseas batters above him are what a repair
# that reads `viable` has to reach for instead.
FIFTH_BOWLER_IS_THE_CHEAPEST_XI = (
    [squad_card(0, False, keeper=True, bat=0.40)]
    + [squad_card(i, False, bat=0.10, bowl=0.30) for i in range(1, 5)]
    + [squad_card(5, True, bat=0.02, bowl=0.05)]                       # overseas, worst
    + [squad_card(i, True, bat=0.35) for i in range(6, 10)]            # overseas batters
    + [squad_card(10, False, bat=0.20)]
)
DOMESTIC_BENCH = [squad_card(i, False, bat=0.20) for i in range(11, 15)]


def test_an_illegal_xi_is_repaired_down_to_the_limit_without_breaking_the_attack():
    xi = FIFTH_BOWLER_IS_THE_CHEAPEST_XI
    repaired = enforce_overseas(xi, xi + DOMESTIC_BENCH)
    assert sum(c.overseas is True for c in repaired) <= OVERSEAS_LIMIT
    assert len(repaired) == 11
    assert viable(repaired), "a repair may not cost the XI its keeper or its fifth bowler"
    assert any(c.person_id == "p5" for c in repaired), (
        "the cheapest swap was the fifth bowler and taking it leaves four"
    )


def test_an_unknown_nationality_is_not_counted_as_overseas():
    """A23's rule, at the point it would do damage. Four known overseas plus an unknown is
    a LEGAL XI that cannot be certified, not an illegal one -- so nothing may be swapped
    out over it. A domestic bench is present precisely so a wrong count would be visible:
    counting the unknown as a fifth overseas player would trigger a swap it can afford."""
    xi = ([squad_card(0, False, keeper=True)]
          + [squad_card(i, False, bowl=0.20) for i in range(1, 6)]
          + [squad_card(i, True) for i in range(6, 10)]
          + [squad_card(10, None)])
    assert enforce_overseas(xi, xi + DOMESTIC_BENCH) == xi


def test_an_unknown_nationality_is_not_counted_as_domestic_either():
    """The same rule on the incoming side. The only bench available here is unknown, so
    there is no repair -- swapping in an unknown would lower the count this code can see
    without lowering the count that matters, and report a legal XI on the strength of it."""
    xi = ([squad_card(0, False, keeper=True)]
          + [squad_card(i, False, bowl=0.20) for i in range(1, 6)]
          + [squad_card(i, True) for i in range(6, 11)])          # five known overseas
    unknown_bench = [squad_card(i, None) for i in range(11, 15)]
    assert enforce_overseas(xi, xi + unknown_bench) == xi


def test_a_squad_that_cannot_field_a_legal_xi_is_returned_unchanged_not_short():
    """The draft has no nationality constraint, so it can deal a squad of twelve overseas
    players. Returning ten would be a silent illegal team; returning eleven lets the report
    say what happened."""
    squad = ([squad_card(0, True, keeper=True)]
             + [squad_card(i, True, bowl=0.20) for i in range(1, 6)]
             + [squad_card(i, True) for i in range(6, 15)])
    xi = squad[:11]
    repaired = enforce_overseas(xi, squad)
    assert repaired == xi
    assert len(repaired) == 11


# --- the over-by-over log: a pure side effect, no extra rng draw, no changed outcome ----

class _FixedModel:
    """A `Model` stand-in whose `.state()` never varies with (over, wickets) -- these
    tests are about the LOG, not the state grid, so a fixed distribution keeps every
    scenario below deterministic and easy to reason about by construction."""

    def __init__(self, probs, values, wide_rate=0.0, wide_runs=1.0, extras_rate=0.0):
        self._probs, self._values = probs, values
        self.wide_rate = wide_rate
        self.wide_runs = wide_runs
        self.extras_rate = extras_rate

    def state(self, over, wickets):
        return self._probs, self._values


# `values`' shape mirrors the real `Model.state()`: index 0 is the (negative) wicket
# cost, indices 1-7 are `OFF_THE_BAT` (0..6 runs) -- `outcome == 0` is a dismissal,
# `outcome == k` (k >= 1) scores `OFF_THE_BAT[k - 1]` runs, so "all singles" needs its
# mass on index 2 (OFF_THE_BAT[1] == 1), not index 1 (OFF_THE_BAT[0] == 0, a dot ball).
_VALUES = (-1.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
ALL_SINGLES = _FixedModel((0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0), _VALUES)
ALL_WICKETS = _FixedModel((1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0), _VALUES)


def _players(n, bowl=None):
    return [Player(f"p{i}", 0.0, bowl=bowl) for i in range(n)]


def test_a_full_innings_with_no_wicket_logs_all_twenty_overs_matching_the_final_card():
    bat = _players(11)
    bowl = _players(BOWLERS_IN_TWELVE, bowl=0.0)
    innings = play_innings(ALL_SINGLES, bat, bowl, random.Random(1))
    assert innings.balls == OVERS * BALLS_PER_OVER
    assert innings.wickets == 0
    assert len(innings.over_log) == OVERS
    last = innings.over_log[-1]
    assert (last.runs, last.wickets, last.balls) == (innings.runs, innings.wickets, innings.balls)
    assert last.over == OVERS - 1
    assert last.over_runs == BALLS_PER_OVER
    assert last.over_wickets == 0


def test_over_runs_and_over_wickets_are_the_deltas_between_consecutive_entries():
    bat = _players(11)
    bowl = _players(BOWLERS_IN_TWELVE, bowl=0.0)
    innings = play_innings(ALL_SINGLES, bat, bowl, random.Random(2))
    prev_runs, prev_wkts = 0, 0
    for o in innings.over_log:
        assert o.runs - prev_runs == o.over_runs
        assert o.wickets - prev_wkts == o.over_wickets
        prev_runs, prev_wkts = o.runs, o.wickets


def test_a_partial_final_over_gets_no_log_entry():
    """Ten wickets fall on the tenth delivery here (every ball is a wicket), which is
    mid-way through the SECOND over -- the first over completed in full and is logged;
    the second, which ends the innings on its fourth ball, is not."""
    bat = _players(11)
    bowl = _players(BOWLERS_IN_TWELVE, bowl=0.0)
    innings = play_innings(ALL_WICKETS, bat, bowl, random.Random(3))
    assert innings.wickets == WICKETS
    assert innings.balls == 10
    assert len(innings.over_log) == 1
    assert innings.over_log[0].over == 0
    assert innings.over_log[0].wickets == BALLS_PER_OVER


def test_over_log_records_the_bowler_choose_bowler_actually_picked():
    """Ties the log's `bowler` field to the already-proven rotation rule
    (`choose_bowler`, pinned separately above) rather than re-deriving it -- predicted
    independently here on a fresh copy of the same bowler cards, then compared."""
    bat = _players(11)
    bowl_players = [Player(f"b{i}", 0.0, bowl=0.1 * i) for i in range(BOWLERS_IN_TWELVE)]

    cards = [BowlerCard(p) for p in bowl_players]
    previous = None
    expected = []
    for _ in range(OVERS):
        picked = choose_bowler(cards, previous)
        expected.append(picked.player.name)
        picked.balls += BALLS_PER_OVER
        previous = picked

    innings = play_innings(ALL_SINGLES, bat, bowl_players, random.Random(4))
    assert [o.bowler for o in innings.over_log] == expected


def test_play_innings_is_deterministic_so_the_log_capture_hides_no_state_leak():
    """Same model, same cards, same seed, twice. If the log capture ever consumed an
    rng draw or mutated something shared between overs, this would be the first thing
    to stop reproducing -- a golden-regression stand-in that needs no hardcoded snapshot
    (CLAUDE.md: no local database, so nothing here can be checked against a fitted
    model instead)."""
    probs = (0.08, 0.30, 0.32, 0.10, 0.01, 0.12, 0.00, 0.07)
    values = (-12.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    model = _FixedModel(probs, values, wide_rate=0.05, wide_runs=1.2, extras_rate=0.03)
    bat = [Player(f"p{i}", 0.05 * i) for i in range(11)]
    bowl = [Player(f"b{i}", 0.0, bowl=0.05 * i) for i in range(BOWLERS_IN_TWELVE)]

    a = play_innings(model, bat, bowl, random.Random(42))
    b = play_innings(model, bat, bowl, random.Random(42))

    assert (a.runs, a.wickets, a.balls, a.extras) == (b.runs, b.wickets, b.balls, b.extras)
    assert a.commentary == b.commentary
    assert [(o.over, o.bowler, o.runs, o.wickets, o.over_runs, o.over_wickets)
            for o in a.over_log] == [
            (o.over, o.bowler, o.runs, o.wickets, o.over_runs, o.over_wickets)
            for o in b.over_log]
