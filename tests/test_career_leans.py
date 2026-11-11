"""Build a Career's own player plays his templates' leans (A277): spin or pace, slow burn,
his settled share, and where his wickets come."""

import dataclasses

import pytest

from game import career as C
from game import simulator as S
from tools.snapshot_deck import deck_from, model_from, read_document

LEANS_TESTS = True          # conftest leaves the leans on for this module
DOC = read_document()
pytestmark = [pytest.mark.skipif(DOC is None, reason="no deck snapshot"),
              pytest.mark.usefixtures("no_incidents")]
# stated, not read from the code's own constant, so dropping one from the code fails a test
ALL = ("style_bat", "burn", "settle_offset", "phase_wkt")


def test_the_four_leans_a_career_card_carries():
    assert set(C.LEAN_FIELDS_BAT) == {"style_bat", "burn", "settle_offset"}
    assert set(C.LEAN_FIELDS_BOWL) == {"phase_wkt"}


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


def _spins(deck, role, need):
    """The first seed whose two spins both carry every lean in `need`."""
    for seed in range(200):
        start, top = C.spin(deck, seed, role)
        start = C.banded(start, top, role)
        if all(getattr(start, f) and getattr(top, f) for f in need):
            return seed, start, top
    raise AssertionError(f"no {role} career whose spins carry {need}")


def _you(start, top, t, role, rd=None):
    return C.ability(start, top, t, setup=C.Setup("N", role, 2015), fs_id=start.fs_id,
                     year=2015, franchise=None, role=rd)


def _by_hand(a, b, t):
    return tuple(round(x + (y - x) * t, 6) for x, y in zip(a, b))


@pytest.mark.parametrize("t", [0.0, 0.3, 1.0])
def test_the_leans_are_the_two_spins_mixed_by_t(deck, t):
    _, start, top = _spins(deck, "allrounder", ALL)
    you = _you(start, top, t, "allrounder")
    for f in ALL:
        assert getattr(you, f) == pytest.approx(_by_hand(getattr(start, f), getattr(top, f), t),
                                                abs=2e-6), f


def test_a_t_past_one_is_held_at_the_ceilings_lean(deck):
    _, start, top = _spins(deck, "middle", C.LEAN_FIELDS_BAT)
    assert _you(start, top, 1.4, "middle").burn == pytest.approx(top.burn, abs=2e-6)


def test_a_missing_lean_counts_as_none_and_two_missing_are_none():
    assert C._mix_lean(None, (0.02, -0.04), 0.5) == (0.01, -0.02)
    assert C._mix_lean((0.02, -0.04), None, 0.5) == (0.01, -0.02)
    assert C._mix_lean(None, None, 0.5) is None
    assert C._mix_lean((0.0, 0.0), (0.0, 0.0), 0.5) is None, "a lean of nothing is no lean"


def test_off_means_the_card_is_exactly_as_before(deck, monkeypatch):
    _, start, top = _spins(deck, "allrounder", ALL)
    on = _you(start, top, 0.5, "allrounder")
    monkeypatch.setattr(C, "LEANS_ON", False)
    off = _you(start, top, 0.5, "allrounder")
    assert all(getattr(off, f) is None for f in ALL)
    assert dataclasses.replace(on, **{f: None for f in ALL}) == off


def test_a_discipline_taken_up_in_a_changed_role_carries_no_template_lean(deck):
    _, start, top = _spins(deck, "allrounder", ALL)
    card = _you(start, top, 0.5, "allrounder")
    bare = dataclasses.replace(card, **{f: None for f in ALL})
    took_bat = C.with_leans(bare, start, top, 0.5, added="bat")
    assert all(getattr(took_bat, f) is None for f in C.LEAN_FIELDS_BAT)
    assert took_bat.phase_wkt == card.phase_wkt, "the discipline he always had keeps its lean"
    took_bowl = C.with_leans(bare, start, top, 0.5, added="bowl")
    assert took_bowl.phase_wkt is None and took_bowl.burn == card.burn


def test_the_gloves_take_the_bowling_lean_with_the_bowling(deck):
    _, start, top = _spins(deck, "middle", C.LEAN_FIELDS_BAT)
    if start.bowl_scoring is None or not start.phase_wkt:
        start = dataclasses.replace(start, bowl=0.0, bowl_scoring=0.0, bowl_dismissal=1.0,
                                    phase_wkt=(0.01, 0.0, -0.01))
    assert _you(start, top, 0.5, "middle").phase_wkt is not None
    rd = {"role": C.KEEPER, "kind": "keeper", "positions": sorted(start.positions),
          "band": start.band, "style": None, "add": "", "base": None, "top": None,
          "second": 0.0, "trial": False, "full_time": False}
    gloved = _you(start, top, 0.5, "middle", rd)
    assert gloved.bowl_scoring is None and gloved.phase_wkt is None
    assert gloved.burn is not None, "his batting keeps its lean"


def test_a_batting_only_role_has_no_bowling_lean(deck):
    # the finisher is the batting-only role (an opener keeps his part-time bowling, A206)
    assert C.ROLES["finisher"].get("batting_only")
    for seed in range(400):
        start, top = C.spin(deck, seed, "finisher")
        if start.phase_wkt:
            break
    else:
        raise AssertionError("no finisher template that bowled")
    you = _you(start, top, 0.5, "finisher")
    assert you.phase_wkt is None and you.burn is not None


@pytest.fixture(scope="module")
def played(deck, model):
    """One real career with the leans on, every season's cache key and every Player your
    card became. Built before the function-scoped switches, so it sets them itself."""
    keys, seen = [], []
    real_cache, real_play = C._cached_season, S.play_innings

    def cache_spy(deck_, model_, seed, k, you, record, key, *a, **kw):
        keys.append((you, key))
        return real_cache(deck_, model_, seed, k, you, record, key, *a, **kw)

    def play_spy(model_, batting, bowling, *a, **kw):
        seen.extend(p for p in list(batting) + list(bowling) if p.person_id == C.YOU_ID)
        return real_play(model_, batting, bowling, *a, **kw)

    with pytest.MonkeyPatch.context() as mp:
        for name, v in (("COACH_ON", False), ("PHASE_ON", False), ("KEEPER_ASK_CHANCE", 0.0),
                        ("LEANS_ON", True)):
            mp.setattr(C, name, v)
        mp.setattr(C, "_cached_season", cache_spy)
        from game import season as SE
        mp.setattr(S, "play_innings", play_spy)
        mp.setattr(SE, "play_innings", play_spy)    # imported there by name
        C._SEASON_CACHE.clear()
        car, _ = C.auto_career(deck, model, 3, C.Setup("N", "allrounder", 2017), "loyal")
    C._SEASON_CACHE.clear()
    return car, keys, seen


def test_a_legend_plays_the_leans_his_best_season_was_played_with(played):
    """[A283] A legend's measured halves are his card plus that season's luck, measured
    against the distribution those leans shaped, so he carries them into a room."""
    car, keys, _ = played
    best, card = C.legend_card(car)
    you = next(y for y, _ in keys if y.season_year == best.year)
    assert any(getattr(card, f) for f in ALL), "the legend carries leans"
    for f in ALL:
        assert getattr(card, f) == getattr(you, f), f


def test_the_leans_are_part_of_the_seasons_cache_key(played):
    _, keys, _ = played
    assert keys
    for you, key in keys:
        for f in ALL:
            v = getattr(you, f)
            assert v is None or v in key, f"{f} is not in the season's cache key"
    assert any(getattr(you, f) for you, _ in keys for f in ALL), "the career carries leans"


def test_the_leans_reach_the_engine_in_a_played_season(played):
    _, keys, seen = played
    assert seen, "your player took the field"
    cards = {you for you, _ in keys}
    played_as = {(p.style_bat, p.burn, p.settle_offset, p.phase_wkt) for p in seen}
    assert played_as <= {(c.style_bat, c.burn, c.settle_offset, c.phase_wkt) for c in cards}
    assert any(any(x) for x in played_as), "and played them"
