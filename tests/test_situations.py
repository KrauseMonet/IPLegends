"""[A260, A261] The engine's situations: the settling-in curve, each card's leans, and chasing
intent. Pure functions on hand-built inputs where the answer is obvious; the engine wiring on
the committed model with deliberately large effects, so a direction cannot hide in noise."""

from __future__ import annotations

import dataclasses
import random

import pytest

import game.simulator as S
from etl import intent as I
from etl import settling as T
from etl import situations as X
from etl.skills import Ball
from tools.snapshot_deck import model_from, read_document


@pytest.fixture(scope="module")
def model():
    return model_from(read_document())


def players(n=11, **kw):
    return [dataclasses.replace(S.Player(f"p{i}", 0.0, 0.0), **kw) for i in range(n)]


def runs(model, batting, bowling, n=300, seed=5, **kw):
    rng = random.Random(seed)
    return sum(S.play_innings(model, batting, bowling, rng, **kw).runs for _ in range(n)) / n


def flat(model, value):
    """Every state and faced bucket given the same adjustment."""
    return {(o, wb, fb): value for o in range(20) for wb in ("0-1", "2-3", "4-5", "6+")
            for fb in range(len(T.FACED_BUCKETS))}


# --------------------------------------------------------------------------- the curve

def test_faced_buckets_are_the_declared_ones():
    assert [T.faced_bucket(f) for f in (0, 1, 2, 3, 4, 5, 9, 10, 14, 15, 80)] == \
        [0, 1, 2, 3, 3, 4, 4, 5, 5, 6, 6]


def test_the_curve_is_centred_within_every_state_on_its_own_mix():
    curve = {(ph, fb): (fb - 3) * 0.1 for ph in ("pp", "mid", "death") for fb in range(7)}
    ratio = {(ph, fb): 0.5 + fb * 0.2 for ph in ("pp", "mid", "death") for fb in range(7)}
    mix = {(0, "0-1"): {0: 0.7, 1: 0.3}, (12, "2-3"): {0: 0.1, 4: 0.4, 6: 0.5}}
    runs_ = T.centred(curve, mix, ratio=False)
    outs = T.centred(ratio, mix, ratio=True)
    for (o, wb), shares in mix.items():
        assert sum(sh * runs_[(o, wb, fb)] for fb, sh in shares.items()) == pytest.approx(0)
        assert sum(sh * outs[(o, wb, fb)] for fb, sh in shares.items()) == pytest.approx(1)
    assert runs_[(0, "0-1", 0)] < 0 < runs_[(0, "0-1", 1)]     # new slower than set


def test_settling_moves_par_by_the_cell_for_its_state_and_balls_faced():
    b = Ball("m", 2016, 1, 12, 2, "a", "b", "x", None, 1, False, False, 0, 0, 3, 12, 1, None,
             par_runs=1.2, par_out=0.05)
    table = {"cells": [[12, "2-3", 0, -0.3, 0.4]]}
    X.apply_settling([b], table)
    assert b.par_runs == pytest.approx(0.9) and b.par_out == pytest.approx(0.02)


# --------------------------------------------------------------------------- intent

def test_pressure_buckets_and_interpolation():
    # a bucket holds its lower edge, as the measurement's ranges do: -5 is "-5 to +5"
    assert [I.bucket(p) for p in (-60, -40, -21, -6, -5, 0, 5, 19, 20, 99)] == \
        [0, 1, 1, 2, 3, 3, 4, 4, 5, 6]
    curve = [(-30.0, -0.2, 0.8), (0.0, 0.0, 1.0), (30.0, 0.3, 1.25)]
    assert I.at(curve, -99) == (-0.2, 0.8) and I.at(curve, 99) == (0.3, 1.25)   # flat beyond
    r, m = I.at(curve, 15.0)
    assert r == pytest.approx(0.15) and m == pytest.approx(1.25 ** 0.5)          # log scale


def test_a_thin_cell_is_left_out_of_the_table():
    doc = {"centres": list(I.CENTRES),
           "cells": [["death", 0, 0.2, 1.3, 230], ["death", 1, -0.25, 0.74, 2816]]}
    assert I.table(doc) == {"death": [(I.CENTRES[1], -0.25, 0.74)]}


def test_pressure_is_needed_runs_less_what_the_engine_expects(model):
    p = I.pressure(model, 160, 50, 60, 3)
    assert p == pytest.approx(111 - I.expected_remaining(model, 60, 3))
    assert I.expected_remaining(model, 120, 3) == 0 and I.expected_remaining(model, 60, 10) == 0
    assert I.expected_remaining(model, 0, 0) > I.expected_remaining(model, 60, 0) > \
        I.expected_remaining(model, 60, 6)


# --------------------------------------------------------------------------- leans

def test_leans_are_centred_over_his_own_balls():
    class M:
        def __init__(self, season):
            self.season, self.career = {**season, ("x", 2016): list(season.values())[0]}, {}
    balls = []
    for i, over in enumerate([1] * 30 + [10] * 60 + [17] * 10):
        balls.append(Ball("m", 2016, 1, over, 0, "a", "b", "x", "spin" if i % 3 else "pace",
                          1, False, False, i, 0, 1, 0, 1, None,
                          par_wkt=0.08 if over >= 15 else 0.02))
    measured = {k: M({("a", 2016): v}) for k, v in
                (("pp", 1.2), ("mid", -0.6), ("death", 3.0), ("spin", 0.9), ("pace", -0.3),
                 ("slow_burn", 1.8), ("pp_econ", 0.0), ("mid_econ", 0.0), ("death_econ", 0.0),
                 ("death_wkt_lean", 0.4))}
    got = X.leans(balls, measured)[("a", 2016)]
    shares = (0.3, 0.6, 0.1)
    assert sum(s * v for s, v in zip(shares, got["phase_bat"])) == pytest.approx(0, abs=1e-6)
    assert got["phase_bat"][2] > 0 > got["phase_bat"][1]
    new = 10 / 100
    assert new * got["burn"][0] + (1 - new) * got["burn"][1] == pytest.approx(0, abs=1e-6)
    assert got["burn"][1] - got["burn"][0] == pytest.approx(1.8 / 6, abs=1e-6)
    # [A265] where his wickets come: death against the rest by the lean, centred over his
    # own EXPECTED wickets by phase (not his balls) so his overall rate is untouched
    wk = X.leans(balls, measured)[("x", 2016)]["phase_wkt"]       # x bowled them
    xw = (30 * 0.02, 60 * 0.02, 10 * 0.08)                   # par_wkt set below
    assert sum(w * v for w, v in zip(xw, wk)) == pytest.approx(0, abs=1e-6)
    assert wk[2] - wk[0] == pytest.approx(0.4, abs=1e-6) and wk[0] == wk[1] < 0 < wk[2]


def test_a_ratio_lean_is_the_tag_against_everything_else():
    """[A265] death wickets against par, minus the rest of his overs' wickets against par,
    each as log (events + 0.5) / (expected + 0.5)."""
    import math
    from etl import skills as K
    m = K.METRIC_BY_KEY["death_wkt_lean"]
    d = {"all": K.Acc(n=120, events=8.0, expected=6.0),
         "death": K.Acc(n=24, events=4.0, expected=1.5)}
    v, noise, balls = K.value(m, d)
    assert v == pytest.approx(math.log(4.5 / 2.0) - math.log(4.5 / 5.0))
    assert noise == pytest.approx(1 / 2.0 + 1 / 5.0) and balls == 24


# --------------------------------------------------------------------------- the engine

def test_neutral_tables_play_exactly_the_old_ball(model):
    plain = dataclasses.replace(model, settle={}, intent={})
    zero = dataclasses.replace(model, settle=flat(model, (0.0, 1.0)),
                               intent={ph: [(0.0, 0.0, 1.0)] for ph in ("pp", "mid", "death")})
    a, b = players(), players()
    for kw in ({}, {"target": 170}):
        x = S.play_innings(plain, a, b, random.Random(3), **kw)
        y = S.play_innings(zero, a, b, random.Random(3), **kw)
        assert (x.runs, x.wickets, x.balls) == (y.runs, y.wickets, y.balls)


def test_the_settling_curve_reaches_the_ball(model):
    plain = dataclasses.replace(model, settle={}, intent={})
    slow = dataclasses.replace(plain, settle=flat(model, (-0.5, 1.0)))
    assert runs(slow, players(), players()) < runs(plain, players(), players()) - 30


def test_the_curve_is_off_in_a_super_over(model):
    plain = dataclasses.replace(model, settle={}, intent={})
    slow = dataclasses.replace(plain, settle=flat(model, (-0.5, 1.0)))
    kw = dict(overs=1, max_wickets=2, state_over=19)
    assert runs(slow, players(3), players(1), **kw) == runs(plain, players(3), players(1), **kw)


def test_his_settled_share_comes_off_his_level_only_with_the_curve(model):
    plain = dataclasses.replace(model, settle={}, intent={})
    zero = dataclasses.replace(plain, settle=flat(model, (0.0, 1.0)))
    off = players(settle_offset=(0.4, 0.0))
    assert runs(zero, off, players()) < runs(zero, players(), players()) - 30
    assert runs(plain, off, players()) == runs(plain, players(), players())


def test_a_slow_burner_is_slower_new_and_faster_set(model, monkeypatch):
    """By TIMING, read off each ball's scoring target: a total-runs test cannot tell a lean
    applied before he is set from one applied after."""
    zero = dataclasses.replace(model, settle=flat(model, (0.0, 1.0)), intent={})
    seen, faced = {"new": [], "set": []}, {}
    real_fb, real_split = S.faced_bucket, S.split_ball

    def fb(balls):
        faced["n"] = balls
        return real_fb(balls)

    def split(probs, values, aim, odds):
        seen["new" if faced["n"] < S.SETTLED else "set"].append(aim)
        return real_split(probs, values, aim, odds)

    monkeypatch.setattr(S, "faced_bucket", fb)
    monkeypatch.setattr(S, "split_ball", split)
    runs(zero, players(burn=(-0.6, 0.2)), players(), n=60)
    new, set_ = (sum(v) / len(v) for v in (seen["new"], seen["set"]))
    assert new == pytest.approx(-0.6) and set_ == pytest.approx(0.2)


def test_a_spin_lean_bites_only_against_spin(model):
    plain = dataclasses.replace(model, settle={}, intent={})
    basher = players(style_bat=(0.6, 0.0))
    spin = players(5, bowling_style="spin", bowl=0.0)
    pace = players(5, bowling_style="pace", bowl=0.0)
    assert runs(plain, basher, spin) > runs(plain, players(), spin) + 30
    assert runs(plain, basher, pace) == runs(plain, players(), pace)


def test_a_chase_reads_the_scoreboard_and_only_a_chase(model):
    plain = dataclasses.replace(model, settle={}, intent={})
    hurry = dataclasses.replace(plain, intent={ph: [(0.0, 0.5, 1.0)]
                                               for ph in ("pp", "mid", "death")})
    a, b = players(), players()
    assert runs(hurry, a, b, target=400) > runs(plain, a, b, target=400) + 30
    assert runs(hurry, a, b) == runs(plain, a, b)                     # no target, no intent
    kw = dict(overs=1, max_wickets=2, state_over=19, target=50)
    assert runs(hurry, players(3), players(1), **kw) == runs(plain, players(3), players(1), **kw)


def test_a_death_wicket_lean_moves_his_wickets_to_the_death(model, monkeypatch):
    """[A265] Counted per phase off the over log, with a lean large enough that luck
    cannot hide it: more wickets at the death, fewer before, and with a centred lean about
    the same in all."""
    plain = dataclasses.replace(model, settle={}, intent={})

    def wickets(lean):
        attack = players(5, phase_wkt=lean)
        by = {"early": 0, "death": 0}
        for s in range(400):
            inn = S.play_innings(plain, players(), attack, random.Random(s))
            for o in inn.over_log:
                by["death" if o.over >= 15 else "early"] += o.over_wickets
        return by

    flat = wickets(None)
    f = flat["death"] / (flat["death"] + flat["early"])
    # centred on where his wickets fall, as etl.situations centres a real lean
    lean = wickets((-0.6 * f, -0.6 * f, 0.6 * (1 - f)))
    assert lean["death"] > flat["death"] * 1.25
    assert lean["early"] < flat["early"] * 0.9
    assert sum(lean.values()) == pytest.approx(sum(flat.values()), rel=0.05)


def test_a_zero_death_wicket_lean_plays_the_old_ball(model):
    plain = dataclasses.replace(model, settle={}, intent={})
    for kw in ({}, {"target": 170}):
        x = S.play_innings(plain, players(), players(), random.Random(9), **kw)
        y = S.play_innings(plain, players(), players(phase_wkt=(0.0, 0.0, 0.0)),
                           random.Random(9), **kw)
        assert (x.runs, x.wickets, x.balls) == (y.runs, y.wickets, y.balls)


def test_a_card_carries_its_death_wicket_lean_into_the_engine(model):
    """[A265] the committed deck has the lean on its bowling cards, and `to_player` passes
    it to the ball."""
    from game.__main__ import to_player
    from tools.snapshot_deck import deck_from
    cards = [c for cs in deck_from(read_document()).cards_by_fs.values() for c in cs]
    leaned = [c for c in cards if c.phase_wkt is not None]
    assert len(leaned) > 2000
    assert all(c.bowl_scoring is not None for c in leaned)
    c = max(leaned, key=lambda c: c.phase_wkt[2])
    assert to_player(c, model).phase_wkt == c.phase_wkt


def test_the_committed_model_carries_the_curve_and_intent(model):
    assert len(model.settle) > 500
    assert set(model.intent) == {"pp", "mid", "death"}
    behind = I.at(model.intent["mid"], 45.0)
    ahead = I.at(model.intent["mid"], -30.0)
    assert behind[0] > 0 > ahead[0] and behind[1] > 1 > ahead[1]


# --------------------------------------------------------------------------- [A268] centring
def _ball(over, pos, faced, style="pace"):
    return Ball("m", 2016, 1, over, 0, "a", "b", "x", style, 1, False, False, faced, 0, pos,
                0, 1, None)


def _mix():
    """Place 1 faces the new ball and mostly pace; place 9 only the death and mostly spin."""
    balls = ([_ball(2, 1, i, "pace") for i in range(60)] + [_ball(10, 1, 20, "spin")] * 20
             + [_ball(18, 9, 0, "spin")] * 9 + [_ball(18, 9, 3, "pace")])
    return X.position_mix(balls)


def test_position_mix_counts_each_places_balls():
    mix = _mix()
    assert mix[1]["pp"] == 60 and mix[1]["mid"] == 20 and mix[1]["death"] == 0
    assert mix[1]["spin"] == 20 and mix[1]["pace"] == 60
    assert mix[9]["death"] == 10 and mix[9]["spin"] == 9 and mix[9]["new"] == 10
    # SETTLED is the boundary: faced 0..SETTLED-1 is new
    from etl.skills import SETTLED
    assert mix[1]["new"] == SETTLED and mix[1]["set"] == 80 - SETTLED


def test_a_lean_nets_to_zero_where_the_card_bats_and_keeps_its_shape():
    """Narine 2012's shape: a career powerplay and middle-overs lean, centred over 7 balls at
    the death, so it adds runs on nearly every ball once his card opens. Re-centred on place
    1, it adds none there, and the gap between the phases is untouched."""
    mix = _mix()
    fields = {"phase_bat": [0.22, 0.25, 0.0], "style_bat": [0.17, 0.0], "burn": [0.0, 0.05],
              "settle_offset": [-0.07, -0.26], "phase_bowl": [0.1, -0.1, 0.0],
              "phase_wkt": [-0.02, -0.02, 0.05]}
    got = X.recentre(fields, {1}, mix)
    m = mix[1]
    assert sum(v * m[k] for v, k in zip(got["phase_bat"], X.PHASES)) == pytest.approx(0, abs=1e-5)
    assert sum(v * m[k] for v, k in zip(got["style_bat"], X.STYLES)) == pytest.approx(0, abs=1e-5)
    assert got["burn"][0] * m["new"] + got["burn"][1] * m["set"] == pytest.approx(0, abs=1e-5)
    for f in ("phase_bat", "style_bat", "burn"):
        old, new = fields[f], got[f]
        assert [a - b for a, b in zip(old, old[1:])] == pytest.approx(
            [a - b for a, b in zip(new, new[1:])], abs=1e-5)
    # settle_offset is not a lean, and the bowling leans are A265's business
    for f in ("settle_offset", "phase_bowl", "phase_wkt"):
        assert got[f] == fields[f]
    # and where he really batted (place 9, the death) a lean centred there would barely move
    at9 = X.recentre({"phase_bat": [0.22, 0.25, 0.0]}, {9}, mix)["phase_bat"]
    assert at9 == pytest.approx([0.22, 0.25, 0.0], abs=1e-6)


def test_places_are_pooled_by_balls_faced_not_averaged():
    """Places 1 and 9 together: place 1's 80 balls outweigh place 9's 10."""
    mix = _mix()
    got = X.recentre({"phase_bat": [0.3, 0.0, 0.0]}, {1, 9}, mix)["phase_bat"]
    assert got[0] == pytest.approx(0.3 - 0.3 * 60 / 90, abs=1e-6)


def test_a_place_with_no_balls_or_a_missing_lean_is_left_alone():
    mix = _mix()
    lean = {"phase_bat": [0.3, 0.0, 0.0], "style_bat": None}
    got = X.recentre(lean, {5}, mix)                 # nobody batted at 5 in this archive
    assert got["phase_bat"] == [0.3, 0.0, 0.0] and got["style_bat"] is None
    assert X.recentre(lean, None, mix)["phase_bat"] == [0.3, 0.0, 0.0]


def test_attach_situations_centres_on_the_cards_own_positions(monkeypatch):
    from etl import feasibility as F
    card = next(c for cs in read_document()["deck"]["cards_by_fs"].values() for c in cs
                if c["bat_scoring"] is not None and c["bowl_scoring"] is not None)
    from tools.snapshot_deck import _card_from
    c = dataclasses.replace(_card_from(card), positions=frozenset({1}))
    lean = {"phase_bat": [0.22, 0.25, 0.0], "style_bat": [0.17, 0.0], "burn": [0.0, 0.05],
            "settle_offset": [-0.07, -0.26], "phase_bowl": [0.1, -0.1, 0.0],
            "phase_wkt": [-0.02, -0.02, 0.05]}
    mix = _mix()
    monkeypatch.setattr(X, "build", lambda conn: {
        "players": {(c.person_id, c.season_year): lean}, "pos_mix": mix})
    cards = {1: [c]}
    F.attach_situations(None, cards)
    got = cards[1][0]
    want = X.recentre(lean, {1}, mix)
    assert list(got.phase_bat) == want["phase_bat"] and list(got.burn) == want["burn"]
    assert list(got.style_bat) == want["style_bat"]
    assert list(got.settle_offset) == lean["settle_offset"]
    assert list(got.phase_bowl) == lean["phase_bowl"] and list(got.phase_wkt) == lean["phase_wkt"]
