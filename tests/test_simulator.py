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

import pytest

from etl.feasibility import BOWLERS_IN_TWELVE, Card
from game.__main__ import OVERSEAS_LIMIT, attack, enforce_overseas, viable
from game.simulator import (
    BALLS_PER_OVER, MAX_OVERS_PER_BOWLER, OVERS, BowlerCard, Player,
    choose_bowler, tilt,
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


def test_the_attack_may_be_drawn_from_a_twelve_including_the_impact_player():
    """[A72] The Impact Player widens the bowling POOL, not the cap: passing eleven plus
    an Impact still returns exactly five, and the Impact may be among them if he is the
    best-rated bowler on offer."""
    xi = [Card(1, f"p{i}", f"p{i}", bat=0.0, bowl=0.1 * i) for i in range(11)]
    impact = Card(1, "impact", "impact", bat=0.0, bowl=99.0)
    picked = attack(xi + [impact], model=None)
    assert len(picked) == BOWLERS_IN_TWELVE
    assert picked[0].name == "impact", "the Impact Player is eligible to be the best bowler"


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
