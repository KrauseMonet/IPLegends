"""Skill drills and a mentor's skill in Build a Career (A281)."""

import dataclasses
import json
import random

import pytest

from game import career as C
from game import career_skills as CS
from game import simulator as S
from tools.snapshot_deck import deck_from, model_from, read_document

DRILL_TESTS = True          # conftest leaves the drills on for this module
COACH_TESTS = True
DOC = read_document()
pytestmark = [pytest.mark.skipif(DOC is None, reason="no deck snapshot"),
              pytest.mark.usefixtures("no_incidents")]


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


def _card(**kw):
    base = dict(fs_id=1, person_id=C.YOU_ID, name="N", bat=0.0, bowl=None, role="batter",
                positions=frozenset({3, 4, 5}), bat_scoring=0.0, bat_dismissal=1.0,
                bowl_scoring=None, bowl_dismissal=None, bowling_style=None)
    base.update(kw)
    return C.Card(**base)


# --- the offsets: centred where they land, sized in real spreads -----------------------------

@pytest.mark.parametrize("band,positions", [("top", {1, 2, 3}), ("middle", {3, 4, 5}),
                                            ("finisher", {5, 6, 7})])
def test_a_spin_drill_is_better_against_spin_worse_against_pace_and_nets_to_nothing(band, positions):
    card = _card(positions=frozenset(positions))
    off = C.drill_offsets(card, {"spin": 2.0})["style_bat"]
    p = {"top": 0.363, "middle": 0.454, "finisher": 0.443}[band]        # measured, stated
    assert off[0] - off[1] == pytest.approx(2 * 0.06)
    assert p * off[0] + (1 - p) * off[1] == pytest.approx(0, abs=1e-12)
    assert off[0] > 0 > off[1]
    pace = C.drill_offsets(card, {"pace": 1.0})["style_bat"]
    assert pace[1] - pace[0] == pytest.approx(0.06)


def test_learning_to_bat_time_is_slower_new_faster_set_and_nets_to_nothing():
    card = _card(positions=frozenset({5, 6, 7}))
    new, set_ = C.drill_offsets(card, {"burn": 1.0})["burn"]
    q = 0.616
    assert set_ - new == pytest.approx(0.036)
    assert q * new + (1 - q) * set_ == pytest.approx(0, abs=1e-12)
    assert new < 0 < set_


def test_the_yorker_moves_wickets_to_the_death_and_nets_to_nothing():
    card = _card(role="bowler", bowl=0.0, bowl_scoring=0.0, bowl_dismissal=1.0,
                 bowling_style="pace", bat_scoring=None, positions=frozenset({9, 10, 11}))
    pp, mid, death = C.drill_offsets(card, {"death_wkts": 2.0})["phase_wkt"]
    r = 0.433
    assert death - pp == pytest.approx(2 * 0.05) and pp == mid
    assert r * death + (1 - r) * pp == pytest.approx(0, abs=1e-12)


def test_a_drill_needs_the_discipline_it_works_on():
    assert "style_bat" not in C.drill_offsets(_card(bat_scoring=None), {"spin": 1.0})
    assert "phase_wkt" not in C.drill_offsets(_card(), {"death_wkts": 1.0})


def test_drills_add_to_the_lean_already_there_and_off_changes_nothing(monkeypatch):
    card = _card(style_bat=(0.01, -0.02))
    got = C.with_drills(card, {"spin": 1.0})
    off = C.drill_offsets(card, {"spin": 1.0})["style_bat"]
    assert got.style_bat == pytest.approx((0.01 + off[0], -0.02 + off[1]))
    assert C.with_drills(card, {}) is card
    monkeypatch.setattr(C, "DRILLS_ON", False)
    assert C.with_drills(card, {"spin": 2.0}) is card


# --- which drill the coach offers, and when ------------------------------------------------

def test_the_drill_fits_where_you_bat_how_you_play_and_what_you_bowl():
    opener = _card(positions=frozenset({1, 2, 3}))
    middle = _card(positions=frozenset({3, 4, 5}))
    seamer = _card(role="bowler", bowl=0.0, bowl_scoring=0.0, bowl_dismissal=1.0,
                   bowling_style="pace", positions=frozenset({9, 10, 11}))
    spinner = dataclasses.replace(seamer, bowling_style="spin")
    assert C.drill_for(opener, {}, {}) == "pace"
    assert C.drill_for(middle, {}, {}) == "spin"
    assert C.drill_for(middle, {"temper": -1.0}, {}) == "burn", "an anchor learns to bat time"
    assert C.drill_for(middle, {"temper": 1.0}, {}) == "spin"
    assert C.drill_for(seamer, {}, {}) == "death_wkts"
    assert C.drill_for(spinner, {}, {}) is None, "a spinner's job is the new ball (A248)"
    assert C.drill_for(middle, {}, {"spin": 2.0}) == "burn", "the next one once it is built"
    assert C.drill_for(middle, {}, {"spin": 2.0, "burn": 2.0}) is None


def test_the_offer_is_seeded_bounded_and_off_with_the_coach(monkeypatch):
    you = _card(positions=frozenset({3, 4, 5}))
    cp = C.Checkpoint()
    ks = [k for k in range(1, 40) if C.drill_card(11, k, cp, you, False) is not None]
    assert ks and all(C.drill_card(11, k, cp, you, False) == C.drill_card(11, k, cp, you, False)
                      for k in ks)
    assert C.drill_card(11, 0, cp, you, False) is None, "not in the first season"
    assert C.drill_card(11, ks[0], cp, you, True) is None, "not in a squad season"
    assert C.drill_card(11, ks[0], dataclasses.replace(cp, drill_offers=2), you, False) is None
    card = C.drill_card(11, ks[0], cp, you, False)
    assert card["drill"] == "spin" and [o["take"] for o in card["options"]] == [True, False]
    monkeypatch.setattr(C, "COACH_ON", False)
    assert C.drill_card(11, ks[0], cp, you, False) is None


def test_a_mentor_passes_on_his_clearest_real_skill_only_when_it_is_clear():
    you = _card()
    spin_man = _card(person_id="m", style_bat=(0.10, -0.06))               # 0.16 apart: 1.3 sd
    assert C.mentor_drill(spin_man, you) == "spin"
    pace_man = _card(person_id="m", style_bat=(-0.10, 0.06))
    assert C.mentor_drill(pace_man, you) == "pace"
    faint = _card(person_id="m", style_bat=(0.05, -0.05))                  # 0.10 apart: 0.8 sd
    assert C.mentor_drill(faint, you) is None
    burner = _card(person_id="m", style_bat=(0.05, -0.05), burn=(-0.06, 0.04))   # 1.4 sd
    assert C.mentor_drill(burner, you) == "burn"
    assert C.mentor_drill(spin_man, _card(bat_scoring=None, role="bowler")) is None
    assert C.mentor_drill(None, you) is None


def test_the_checkpoint_keeps_the_drills():
    cp = C.Checkpoint(drills={"spin": 1.0, "burn": 2.0}, drill_offers=2)
    back = C.Checkpoint.from_dict(json.loads(json.dumps(cp.to_dict())))
    assert back.drills == {"spin": 1.0, "burn": 2.0} and back.drill_offers == 2


# --- the engine plays it ------------------------------------------------------------------------

def test_a_spin_drilled_batter_scores_more_off_spin_and_less_off_pace_and_the_same_in_all(model):
    def run(drills):
        me = C.with_drills(_card(positions=frozenset({3, 4, 5})), drills)
        from game.__main__ import to_player
        p = to_player(me, model)
        avg = [S.Player(name=f"a{i}", bat=0.0, bowl=0.0, person_id=f"a{i}",
                        bowling_style="spin" if i % 2 else "pace") for i in range(11)]
        side = list(avg)
        side[3] = p
        col = CS.Collector(C.YOU_ID)
        with S.ball_log(col):
            for k in range(2500):
                S.play_innings(model, side, avg[:5], random.Random(k))
        return col.acc[(C.YOU_ID, "bat")]
    base, drilled = run({}), run({"spin": 2.0})
    edge = lambda a, t: a[t].resid / a[t].n
    assert edge(drilled, "spin") - edge(base, "spin") > 0.04
    assert edge(drilled, "pace") - edge(base, "pace") < -0.02
    assert abs(edge(drilled, "all") - edge(base, "all")) < 0.012, "a drill moves where, not how much"


# --- a real career ------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def drilled(deck, model):
    """A middle-order career that takes the coach's drills, every season's card as played and
    every Player it became. Built before the per-test switches, so it sets them itself."""
    seen, played = [], []
    real_cache, real_play = C._cached_season, S.play_innings

    def cache_spy(deck_, model_, seed, k, you, record, key, *a, **kw):
        seen.append((you, dict(record.drills_built)))
        return real_cache(deck_, model_, seed, k, you, record, key, *a, **kw)

    def play_spy(model_, batting, bowling, *a, **kw):
        played.extend(p for p in batting if p.person_id == C.YOU_ID)
        return real_play(model_, batting, bowling, *a, **kw)

    from game import season as SE
    with pytest.MonkeyPatch.context() as mp:
        for name, v in (("COACH_ON", True), ("PHASE_ON", False), ("KEEPER_ASK_CHANCE", 0.0),
                        ("LEANS_ON", False), ("DRILLS_ON", True)):
            mp.setattr(C, name, v)
        mp.setattr(C, "_cached_season", cache_spy)
        mp.setattr(S, "play_innings", play_spy)
        mp.setattr(SE, "play_innings", play_spy)
        C._SEASON_CACHE.clear()
        car, _ = C.auto_career(deck, model, 1, C.Setup("N", "middle", 2010), "strength")
    C._SEASON_CACHE.clear()
    return car, seen, played


def test_a_taken_drill_is_built_and_kept_and_never_past_its_cap(drilled):
    car, _, _ = drilled
    took = [r for r in car.seasons if r.coach and r.coach.get("key") == "drill" and r.coach["take"]]
    assert took, "the fixture takes a drill"
    after = car.seasons[-1].drills_built
    assert sum(after.values()) >= 1 and all(v <= 2.0 for v in after.values())


def test_the_season_is_played_with_the_drills_built(drilled):
    """Leans are off here, so every lean the card and the engine carry is the drills' own."""
    _, seen, played = drilled
    fields = ("style_bat", "burn", "phase_wkt")
    assert any(d for _, d in seen), "a season is played with a drill"
    for you, d in seen:
        want = C.drill_offsets(you, d)
        for f in fields:
            got = getattr(you, f)
            assert got == (tuple(round(x, 6) for x in want[f]) if f in want else None), f
    cards = {tuple(getattr(you, f) for f in fields) for you, _ in seen}
    engine = {tuple(getattr(p, f) for f in fields) for p in played}
    assert engine <= cards and any(any(x) for x in engine), "and the engine got them"


MENTOR_CASES = [("pace", 4, "Lasith Malinga", "death_wkts"),     # found by search; re-find
                ("middle", 21, "Shikhar Dhawan", "pace")]       # after an engine change


@pytest.mark.parametrize("role,seed,mentor,drill", MENTOR_CASES)
def test_a_mentor_teaches_his_skill_in_a_real_career(deck, model, role, seed, mentor, drill):
    car, _ = C.auto_career(deck, model, seed, C.Setup("N", role, 2010), "strength")
    i = next(i for i, r in enumerate(car.seasons)
             if any(t.startswith(f"{mentor} also ") for t in r.notes))
    r = car.seasons[i]
    before = car.seasons[i - 1].drills_built.get(drill, 0.0) if i else 0.0
    assert not (r.coach and r.coach.get("drill") == drill), "the lesson is the mentor's alone"
    assert r.drills_built.get(drill, 0.0) == min(2.0, before + 1.0)


def test_no_drill_text_gives_your_player_a_pronoun():
    """A228: the player is the person at the keyboard, so nothing about him says he/his."""
    import re
    texts = [C.drill_words({k: 1.0}) for k in C.DRILL_STEP]
    texts = [t for ts in texts for t in ts] + list(C.MENTOR_DRILL_WORDS.values())
    texts += [str(v) for v in C.DRILL_TEXT.values()]
    for t in texts:
        assert not re.search(r"\b(he|his|him|she|her)\b", t, re.I), t


def test_a_legend_keeps_the_drills_his_season_had_built(drilled):
    """[A283] Leans are off in this module, so every lean on the legend is a drill's."""
    car, seen, _ = drilled
    fields = ("style_bat", "burn", "phase_wkt")
    measured = {r.year for r in car.seasons if r.measured}
    you = max((y for y, _ in seen if y.season_year in measured and any(getattr(y, f) for f in fields)),
              key=lambda y: y.season_year)          # the latest season played with a drill on
    best, card = C.legend_card(car, you.season_year)   # a season asked for by name (A272)
    assert any(getattr(card, f) for f in fields), "the legend carries the drill"
    for f in fields:
        assert getattr(card, f) == getattr(you, f), f
