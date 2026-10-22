"""A captain's bowling plan (A213): a phase per bowler, honoured over by over, never at the
cost of an innings that cannot finish, and nothing at all without a plan."""

from __future__ import annotations

import itertools
import random

import pytest

from game import simulator as S
from game.simulator import BowlerCard, Player, choose_bowler, phase_of_over


def _attack(n=5):
    return [BowlerCard(Player(f"b{i}", 0.0, 0.1 * i, person_id=f"p{i}")) for i in range(n)]


def _old_choose(bowlers, previous):
    """The rule as it stood before plans: kept here as the reference."""
    available = [b for b in bowlers if b.balls < 24 and b is not previous]
    return min(available, key=lambda b: (b.balls, -(b.player.bowl or 0.0)))


def test_without_a_plan_bowler_choice_is_exactly_the_old_rule():
    rng = random.Random(3)
    for _ in range(2000):
        attack = _attack(rng.choice((5, 6)))
        for b in attack:
            b.balls = 6 * rng.randrange(0, 4)
        prev = rng.choice(attack + [None])
        assert choose_bowler(attack, prev) is _old_choose(attack, prev)
        assert choose_bowler(attack, prev, rng.randrange(20), None) is _old_choose(attack, prev)


def _bowl_twenty(plan, n=5):
    attack, prev, order = _attack(n), None, []
    for over in range(20):
        b = choose_bowler(attack, prev, over, plan)
        b.balls += 6
        order.append(b.player.person_id)
        prev = b
    return order


@pytest.mark.parametrize("seed", range(40))
def test_any_plan_keeps_the_rules_and_finishes_the_innings(seed):
    rng = random.Random(seed)
    n = rng.choice((5, 6))
    plan = {f"p{i}": rng.choice(("powerplay", "middle", "death", None)) for i in range(n)}
    order = _bowl_twenty({k: v for k, v in plan.items() if v}, n)
    assert len(order) == 20
    assert all(a != b for a, b in zip(order, order[1:]))
    assert max(order.count(p) for p in set(order)) <= 4


def test_planned_bowlers_take_their_phase():
    plan = {"p0": "powerplay", "p1": "powerplay", "p3": "death", "p4": "death"}
    order = _bowl_twenty(plan)
    assert set(order[:6]) == {"p0", "p1"}                     # the powerplay is theirs
    assert order[15:].count("p3") + order[15:].count("p4") >= 4   # the death is mostly theirs
    plain = _bowl_twenty({})
    assert set(plain[:6]) != {"p0", "p1"}                     # and it would not have been


def test_a_death_bowler_is_saved_until_needed():
    order = _bowl_twenty({"p4": "death"})
    first = order.index("p4")
    assert first >= 12, order                                   # held back as long as possible
    assert order.count("p4") == 4


def _brute_can_finish(quota, prev, overs):
    """Exhaustive: is there a sequence of `overs` with no two equal running, within quota?"""
    def go(q, last, k):
        if k == 0:
            return True
        return any(q[i] > 0 and i != last and go(q[:i] + (q[i] - 1,) + q[i + 1:], i, k - 1)
                   for i in range(len(q)))
    return go(tuple(quota), prev, overs)


def test_can_finish_agrees_with_an_exhaustive_search():
    rng = random.Random(11)
    for _ in range(400):
        attack = _attack(rng.choice((2, 3, 4, 5)))
        for b in attack:
            b.balls = 6 * rng.randrange(0, 5)
        options = [b for b in attack if b.balls < 24]
        if not options:
            continue
        chosen = rng.choice(options)
        overs = rng.randrange(0, 9)
        quota = tuple(4 - b.balls // 6 - (b is chosen) for b in attack)
        want = _brute_can_finish(quota, attack.index(chosen), overs)
        assert S._can_finish(attack, chosen, overs) == want


def test_phases_follow_season_analysis():
    from game.analysis import PHASE_OVERS
    for phase, span in PHASE_OVERS.items():
        lo, hi = (int(x) for x in span.split("-"))
        assert {phase_of_over(o) for o in range(lo - 1, hi)} == {phase}


def test_a_sides_plan_reaches_only_its_own_bowling(monkeypatch):
    """`play` hands each innings the plan of the side BOWLING it (A101's trap, guarded)."""
    from game import season as SE
    seen = []
    real = SE.play_innings

    def spy(model, batting, bowling, rng, target=None, **kw):
        seen.append(kw.get("plan"))
        return real(model, batting, bowling, rng, target, **kw)

    monkeypatch.setattr(SE, "play_innings", spy)
    from tools.snapshot_deck import deck_from, model_from, read_document
    doc = read_document()
    if doc is None:
        pytest.skip("no deck snapshot")
    deck, model = deck_from(doc), model_from(doc)
    from game.__main__ import opposition_twelve
    fss = sorted(deck.cards_by_fs)[:2]
    home = SE.Side("H", "H", *opposition_twelve(list(deck.cards_by_fs[fss[0]])))
    away = SE.Side("A", "A", *opposition_twelve(list(deck.cards_by_fs[fss[1]])))
    home.bowling_plan = {"x": "death"}
    SE.play(model, home, away, random.Random(1))
    assert seen[:2] == [None, {"x": "death"}]     # away bowls first; home bowls the chase
