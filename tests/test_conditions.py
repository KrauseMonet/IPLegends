"""[A267] Match conditions: one draw a match, shared by both innings and any super over,
measured in size, and off for a daily day played before them."""

from __future__ import annotations

import datetime
import math
import random
import statistics
from types import SimpleNamespace

import pytest

import game.season as SE
import game.simulator as G
from web import daily as D


def test_the_levels_are_a_centred_spread_of_the_measured_size():
    lv = G.CONDITIONS_LEVELS
    assert len(lv) == 7 and statistics.fmean(lv) == pytest.approx(0.0, abs=1e-9)
    assert math.sqrt(statistics.fmean(v * v for v in lv)) == pytest.approx(G.CONDITIONS_SD,
                                                                          rel=1e-4)
    assert G.CONDITIONS_SD == 0.142          # measured (the shared run rate), not fitted
    assert lv == tuple(sorted(lv)) and lv[0] < 0 < lv[-1]


def test_a_match_draws_its_conditions_with_exactly_one_random_number():
    a, b = random.Random(5), random.Random(5)
    c = G.match_conditions(a)
    b.random()
    assert a.random() == b.random()
    assert c in G.CONDITIONS_LEVELS


def test_with_conditions_off_nothing_is_drawn_and_the_match_is_level():
    a, b = random.Random(5), random.Random(5)
    with G.no_conditions():
        assert G.match_conditions(a) == 0.0
    assert a.random() == b.random()
    with G.no_conditions(False):
        assert G.match_conditions(random.Random(5)) in G.CONDITIONS_LEVELS


def test_conditions_move_every_balls_aim(monkeypatch):
    aims = []
    real = G.split_ball
    monkeypatch.setattr(G, "split_ball",
                        lambda probs, values, aim, odds: (aims.append(aim), real(
                            probs, values, aim, odds))[1])
    monkeypatch.setattr(G, "draw", lambda dist, rng: 1)     # every ball a dot: same states
    from tools.snapshot_deck import model_from, read_document
    model = model_from(read_document())
    side = [G.Player(f"p{i}", 0.0, 0.0) for i in range(11)]
    G.play_innings(model, side, side[:5], random.Random(1))
    plain, aims[:] = list(aims), []
    G.play_innings(model, side, side[:5], random.Random(1), conditions=0.2)
    lifted = aims[:len(plain)]
    assert len(plain) == 120 and all(b - a == pytest.approx(0.2) for a, b in zip(plain, lifted))


class _Spy:
    def __init__(self, monkeypatch):
        self.seen = []
        real = SE.play_innings

        def spy(*args, **kw):
            self.seen.append(kw.get("conditions", "missing"))
            return real(*args, **kw)
        monkeypatch.setattr(SE, "play_innings", spy)


@pytest.fixture(scope="module")
def sides():
    from game.season import historical_sides
    from tools.snapshot_deck import deck_from, model_from, read_document
    doc = read_document()
    deck, model = deck_from(doc), model_from(doc)
    return model, historical_sides(deck, random.Random(3), 2)


def test_both_innings_of_a_match_share_one_condition(monkeypatch, sides):
    model, (a, b) = sides
    spy = _Spy(monkeypatch)
    seen = set()
    for s in range(12):
        spy.seen.clear()
        r = SE.play(model, a, b, random.Random(s))
        assert spy.seen[0] == spy.seen[1] == r.conditions        # both innings, one draw
        assert r.conditions in G.CONDITIONS_LEVELS
        seen.add(r.conditions)
    assert len(seen) > 1                                         # it really varies by match


def test_play_open_and_the_human_match_share_it_too(monkeypatch, sides):
    model, (a, b) = sides
    spy = _Spy(monkeypatch)
    r = SE.play_open(model, a, b, random.Random(2), moves=None)
    assert spy.seen[0] == spy.seen[1] == r.conditions


def test_a_tie_plays_its_super_overs_on_the_same_condition(monkeypatch, sides):
    model, (a, b) = sides
    got = []
    monkeypatch.setattr(SE, "play_super_overs",
                        lambda model, home, away, rng, conditions=0.0:
                        got.append(conditions) or [SimpleNamespace(winner=home, number=1,
                                                                   first=home, first_runs=1,
                                                                   second_runs=0)])
    inn = SimpleNamespace(runs=150, wickets=5, balls=120)
    r = SE._finish_result(model, random.Random(1), a, b, "league", inn, inn, conditions=0.07)
    assert got == [0.07] and r.conditions == 0.07


@pytest.mark.parametrize("offset, off", [(-1, True), (0, False)])
def test_a_daily_before_the_switch_plays_without_conditions(monkeypatch, offset, off):
    seen = {}
    monkeypatch.setattr(D, "_play_and_score", lambda *a: seen.update(
        draws=G.match_conditions(random.Random(1))))
    for name in ("ENGINE_TWO_LEVEL_FROM", "REPLAY_ENGINE_FROM", "SITUATIONS_FROM"):
        monkeypatch.setattr(D, name, datetime.date(2000, 1, 1))
    day = SimpleNamespace(challenge_date=D.CONDITIONS_FROM + datetime.timedelta(days=offset))
    D.play_and_score(SimpleNamespace(cards_by_fs={}, fs_ids=[]), None, day, 1, "s")
    assert (seen["draws"] == 0.0) == off
