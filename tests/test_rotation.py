"""[A276] Who bowls when: each side spreads its spin overs over the twenty in the archive's
per-over shape, pace in the other, and still bowls its twenty legally. Older dailies keep
the workload-only rotation they were scored under."""

from __future__ import annotations

import datetime
import random

import pytest

from game import simulator as S
from game.simulator import (BowlerCard, Player, SPIN_SHARE_BY_OVER, choose_bowler,
                            phase_blind_rotation, play_innings)
from tools.snapshot_deck import model_from, read_document

DOC = read_document()


def _old_choose(bowlers, previous):
    """The workload-only rule, as it stood before A276: the reference."""
    available = [b for b in bowlers if b.balls < 24 and b is not previous]
    return min(available, key=lambda b: (b.balls, -(b.player.bowl or 0.0)))


def _attack(styles):
    return [BowlerCard(Player(f"w{i}", 0.0, 0.1 * i, person_id=f"w{i}", bowling_style=s))
            for i, s in enumerate(styles)]


def _bowl(styles, rng=None, plan=None):
    attack, prev, order = _attack(styles), None, []
    for over in range(20):
        b = choose_bowler(attack, prev, over, plan, rng)
        b.balls += 6
        order.append(b)
        prev = b
    return order


def test_phase_blind_is_exactly_the_old_rule():
    rng = random.Random(1)
    with phase_blind_rotation():
        for _ in range(2000):
            attack = _attack([rng.choice(("pace", "spin", None)) for _ in range(5)])
            for b in attack:
                b.balls = 6 * rng.randrange(0, 4)
            prev = rng.choice(attack + [None])
            assert choose_bowler(attack, prev, rng.randrange(20), None, rng) \
                is _old_choose(attack, prev)


def test_a_side_with_one_style_rotates_as_before():
    """Only a choice between styles is new: an all-pace attack is the old rotation."""
    rng = random.Random(2)
    for _ in range(500):
        attack = _attack(["pace"] * 5)
        for b in attack:
            b.balls = 6 * rng.randrange(0, 4)
        prev = rng.choice(attack + [None])
        assert choose_bowler(attack, prev, rng.randrange(20), None, rng) \
            is _old_choose(attack, prev)


@pytest.mark.parametrize("spinners", range(6))
@pytest.mark.parametrize("seed", range(20))
def test_every_attack_still_bowls_twenty_legal_overs(spinners, seed):
    order = _bowl(["spin"] * spinners + ["pace"] * (5 - spinners), random.Random(seed))
    assert len(order) == 20
    assert all(a is not b for a, b in zip(order, order[1:]))
    assert max(order.count(b) for b in order) <= 4


def test_spin_follows_the_archives_shape():
    """Two spinners and three quicks, the commonest attack: spin rare in the powerplay,
    most of the middle, rare again at the death -- within a few points of the archive's
    own per-phase shares, scaled to this side's eight spin overs."""
    rng = random.Random(7)
    spin = [0] * 20
    trials = 4000
    for _ in range(trials):
        for over, b in enumerate(_bowl(["pace", "pace", "pace", "spin", "spin"], rng)):
            spin[over] += b.player.bowling_style == "spin"
    scale = 8 / sum(SPIN_SHARE_BY_OVER)
    for lo, hi in ((0, 6), (6, 15), (15, 20)):
        got = sum(spin[lo:hi]) / trials
        want = scale * sum(SPIN_SHARE_BY_OVER[lo:hi])
        assert abs(got - want) < 0.35, (lo, hi, got, want)
    assert sum(spin[:6]) < sum(spin[6:12]) / 3          # the shape, not just the total
    # and smooth: a deterministic choice put half of every third over to spin and almost
    # none of the fourth, because every side made the same call at the same over
    assert max(spin[:6]) / trials < 0.4, [round(x / trials, 2) for x in spin[:6]]


def test_a_side_without_spin_never_needs_a_spinner():
    order = _bowl(["pace"] * 5, random.Random(3))
    assert all(b.player.bowling_style == "pace" for b in order)


def test_a_plan_still_comes_first():
    plan = {"w3": "powerplay", "w4": "powerplay"}          # two spinners asked to open
    order = _bowl(["pace", "pace", "pace", "spin", "spin"], random.Random(4), plan)
    assert {b.player.person_id for b in order[:6]} == {"w3", "w4"}


@pytest.mark.skipif(DOC is None, reason="no deck snapshot")
def test_the_engine_bowls_spin_through_the_middle():
    model = model_from(DOC)
    bat = [Player(f"b{i}", 0.0, person_id=f"b{i}") for i in range(11)]
    att = [Player(f"w{i}", 0.0, 0.0, person_id=f"w{i}",
                  bowling_style="spin" if i >= 3 else "pace") for i in range(5)]
    rng = random.Random(11)
    spin = [[0, 0] for _ in range(3)]
    for _ in range(300):
        inn = play_innings(model, bat, att, rng)
        for o in inn.over_log:
            ph = 0 if o.over < 6 else 1 if o.over < 15 else 2
            spin[ph][0] += int(o.bowler_id[1:]) >= 3
            spin[ph][1] += 1
    pp, mid, death = (a / b for a, b in spin)
    assert mid > 0.5 > pp and mid > death


def test_the_daily_pins_its_older_days(monkeypatch):
    """A day before ROTATION_FROM replays under the old rotation, one from it under the new."""
    from web import daily as D
    seen = []
    monkeypatch.setattr(D, "_play_and_score",
                        lambda *a, **k: seen.append(S._PHASE_BLIND.get()))
    monkeypatch.setattr(D, "nationality_deck", lambda full, date: full)
    for date in (D.ROTATION_FROM - datetime.timedelta(days=1), D.ROTATION_FROM):
        day = type("Day", (), {"challenge_date": date})()
        D.play_and_score(_Deck(), _Model(), day, 1, "x")
    assert seen == [True, False]


class _Deck:
    cards_by_fs: dict = {}


class _Model:
    pass
