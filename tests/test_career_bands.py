"""Build a Career, A255: where your player bats and what he does there.

1. A keeper's or all-rounder's band comes from the ceiling spin, not the start.
2. A middle-order batter rated 90+ rises to No. 3, a finisher to No. 5.
3. A bowler who moves up bats in the band his squad lacks, coached by its best batter there.
4. A batter can be asked to keep wicket when his side's keeper is a weak batter, and keeps the
   gloves for good, never bowling again."""

from __future__ import annotations

import dataclasses
import random
import types

import pytest

from game import career as C
from tools.snapshot_deck import deck_from, model_from, read_document

COACH_TESTS = True           # conftest's `coach_off` leaves the coach on here
KEEPER_TESTS = True          # ...and `keeper_off` the keeper offer
DOC = read_document()
pytestmark = [pytest.mark.skipif(DOC is None, reason="no deck snapshot"),
              pytest.mark.usefixtures("no_incidents")]


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


def _band(c):
    return frozenset(c.positions or ())


def _setup(role, debut=2010):
    return C.Setup("Test", role, debut, False)


def _drive(deck, model, seed, setup, answer, limit=400):
    rng = random.Random(f"{seed}:policy")
    moves = []
    for _ in range(limit):
        career = C.play_career(deck, model, seed, setup, moves)
        if career.complete:
            return career, moves
        p = career.pending
        moves.append(f"t{answer(p.coach)}" if p.kind == "coach"
                     else C.policy_move(career, "strength", rng))
    raise AssertionError("career did not finish")


# --- 1. the band from the ceiling spin -------------------------------------------------------


@pytest.mark.parametrize("role", ["keeper", "allrounder"])
def test_a_keeper_or_allrounder_bats_in_his_ceilings_band(deck, model, role):
    seed = next(s for s in range(500)
                if _band(C.spin(deck, s, role)[0]) != _band(C.spin(deck, s, role)[1]))
    start, ceiling = C.spin(deck, seed, role)
    career = C.play_career(deck, model, seed, _setup(role), [])
    assert _band(career.start) == _band(ceiling) and career.start.band == ceiling.band
    # only where he bats moves: the start's own numbers are untouched
    assert career.start.bat_scoring == start.bat_scoring
    assert career.start.person_id == start.person_id


@pytest.mark.parametrize("role", ["keeper", "allrounder"])
def test_a_career_from_before_keeps_its_starts_band(deck, model, role):
    seed = next(s for s in range(500)
                if _band(C.spin(deck, s, role)[0]) != _band(C.spin(deck, s, role)[1]))
    start, _ = C.spin(deck, seed, role)
    old = C.Checkpoint.from_dict({k: v for k, v in C.Checkpoint().to_dict().items()
                                  if k != "ceiling_band"})
    assert old.ceiling_band is False
    career = C.play_career(deck, model, seed, _setup(role), [], resume=old)
    assert _band(career.start) == _band(start)


def test_other_roles_are_untouched(deck):
    for role in ("opener", "middle", "finisher", "pace", "spin"):
        start, ceiling = C.spin(deck, 4, role)
        assert C.banded(start, ceiling, role) is start


# --- 2. the peak rung -----------------------------------------------------------------------


def test_the_rise_for_each_band(deck):
    base = next(iter(deck.cards_by_fs.values()))[0]
    at = lambda slots, p: C.batting_number(dataclasses.replace(base, positions=slots), p)
    assert [at(C.TOP_SLOTS, p) for p in range(4)] == [3, 2, 1, 1]
    # a promotion counts the same in every band (A251's second mark kept the middle at 4),
    # so a career in progress keeps its number; the peak is one mark more
    assert [at(C.MIDDLE_SLOTS, p) for p in range(4)] == [5, 4, 4, 3]
    assert [at(C.FINISHER_SLOTS, p) for p in range(4)] == [7, 6, 6, 5]
    assert at(frozenset({8, 9, 10, 11}), 3) is None


def test_the_peak_needs_ninety(deck):
    base = next(iter(deck.cards_by_fs.values()))[0]
    mid = dataclasses.replace(base, positions=C.MIDDLE_SLOTS)
    top = dataclasses.replace(base, positions=C.TOP_SLOTS)
    assert C.PEAK_AT == 90
    assert C.marks_for(mid) == (80, 84, 90) and C.marks_for(top) == (80, 84)
    assert C.next_promotions(2, 89, 89, False, False, C.marks_for(mid)) == 2
    assert C.next_promotions(2, 90, 90, False, False, C.marks_for(mid)) == 3
    assert C.next_promotions(2, 95, 95, True, False, C.marks_for(mid)) == 2     # not when poor
    assert C.next_promotions(2, 99, 99, False, False, C.marks_for(top)) == 2    # an opener is at 1
    # lost only on A251's sharp decline
    assert C.next_promotions(3, 88, 95, True, False, C.marks_for(mid)) == 3
    assert C.next_promotions(3, 85, 95, True, True, C.marks_for(mid)) == 2


@pytest.mark.parametrize("role,peak", [("middle", 3), ("finisher", 5)])
def test_a_career_reaches_the_peak_rung_and_bats_there(deck, model, monkeypatch, role, peak):
    """The wiring: with the marks low enough that any career clears them, the season after the
    third mark bats at the peak number, read off the scorecards."""
    monkeypatch.setattr(C, "PROMOTE_AT", (60, 61))
    monkeypatch.setattr(C, "PEAK_AT", 62)
    monkeypatch.setattr(C, "COACH_ON", False)
    C._SEASON_CACHE.clear()
    try:
        career, _ = _drive(deck, model, 2, _setup(role), lambda c: 0)
    finally:
        C._SEASON_CACHE.clear()
    at = [r.bat_at for r in career.seasons if r.bat_at is not None]
    assert peak in at, at
    assert min(at) == peak                       # and never above it


def test_an_old_middle_career_on_its_second_mark_stays_at_four(deck):
    base = next(iter(deck.cards_by_fs.values()))[0]
    mid = dataclasses.replace(base, positions=C.MIDDLE_SLOTS)
    assert C.batting_number(mid, C.promotions_for(88)) == 4


# --- 3. a bowler moving up bats where the squad is short -------------------------------------


def _fake(cards):
    return types.SimpleNamespace(cards_by_fs={1: cards})


def _bat(deck, pid, slots, display, balls=200, role="batter", value=None):
    """A regular batter in `slots`; `value` is his batting value (dismissal multiplier 1)."""
    base = next(c for cs in deck.cards_by_fs.values() for c in cs
                if c.bat_scoring is not None and c.role == "batter")
    out = dataclasses.replace(base, person_id=pid, name=pid, positions=slots, display=display,
                              bat_balls=balls, role=role)
    if value is not None:
        out = dataclasses.replace(out, bat_scoring=value, bat_dismissal=1.0, bat=value)
    return out


def test_the_lacking_band_is_the_weaker_one(deck):
    m, f = C.MIDDLE_SLOTS, C.FINISHER_SLOTS
    assert C.lacking_band(_fake([_bat(deck, "a", m, 80, value=0.0),
                                 _bat(deck, "b", f, 80, value=0.1)]), 1) == "middle"
    assert C.lacking_band(_fake([_bat(deck, "a", m, 80, value=0.1),
                                 _bat(deck, "b", f, 80, value=0.0)]), 1) == "finisher"
    assert C.lacking_band(_fake([_bat(deck, "a", m, 80, value=0.1),
                                 _bat(deck, "b", f, 80, value=0.1)]), 1) == "finisher"
    # nobody regular in the middle: the middle is short
    assert C.lacking_band(_fake([_bat(deck, "a", m, 80, balls=40, value=0.5),
                                 _bat(deck, "b", f, 80, value=-0.3)]), 1) == "middle"
    # a bowler's batting is not the squad's batting
    assert C.lacking_band(_fake([_bat(deck, "a", m, 80, role="bowler", value=0.5),
                                 _bat(deck, "b", f, 80, value=-0.3)]), 1) == "middle"


def test_the_lacking_band_is_judged_on_batting(deck):
    """A finisher-band all-rounder's card carries his bowling; it is his batting that counts."""
    m, f = C.MIDDLE_SLOTS, C.FINISHER_SLOTS
    squad = [_bat(deck, "a", m, 78, value=0.05),
             _bat(deck, "b", f, 95, role="allrounder", value=-0.1)]
    assert C.lacking_band(_fake(squad), 1) == "finisher"


MIDDLE_SEED = 0             # (0 until A267, 2 until A268, 8 until A276) a spinner whose first trial is at a squad short in the middle
GLOVES_SEED = 0             # (2 until A270) a middle-order career the coach asks to keep wicket; re-find after a rating change
                            # (8 until re-found after A261: its 2011 trial squad now lacks the finisher band)


def _middle_short_fs(deck):
    return next(fs for fs in sorted(deck.cards_by_fs) if C.lacking_band(deck, fs) == "middle")


def test_a_bowler_moved_into_the_middle_bats_there_from_the_middles_level(deck, model):
    spinner = C.template_pools(deck, "spin")[0][0]
    rd = C.role_state(deck, model, 1, _setup("spin"), spinner, "allrounder", 0.0, "", 2012,
                      bat_band="middle")
    assert set(rd["positions"]) == set(C.MIDDLE_SLOTS) and rd["band"] == "middle"
    assert tuple(rd["base"]) == C.finisher_batting(model, 2012, "middle")
    assert C.finisher_batting(model, 2012, "middle") != C.finisher_batting(model, 2012)
    # the default is where every bowler went before
    rd = C.role_state(deck, model, 1, _setup("spin"), spinner, "allrounder", 0.0, "", 2012)
    assert set(rd["positions"]) == set(C.FINISHER_SLOTS)


def test_the_offer_names_the_band_and_its_best_batter(deck):
    fs = _middle_short_fs(deck)
    spinner = C.template_pools(deck, "spin")[0][0]
    offer = C.with_coach_mentor(deck, dict(C.COACH_TRIAL_BAT, target="allrounder"), fs,
                                spinner, C.Checkpoint())
    assert offer["band"] == "middle" and "four or five" in offer["text"]
    take = next(o for o in offer["options"] if o["take"])
    assert take["band"] == "middle"
    middle = [c for c in deck.cards_by_fs[fs] if _band(c) == C.MIDDLE_SLOTS
              and (c.bat_balls or 0) >= C.BAT_REGULAR_BALLS and c.bat_scoring is not None]
    if middle:
        assert take["mentor"]["id"] == max(middle, key=lambda c: (c.display, c.person_id)).person_id


def test_taking_the_trial_bats_in_the_band_the_squad_lacked(deck, model, monkeypatch):
    """With the marks low, a bowler's Rating has already 'earned' promotions it never batted
    for; the trial must still start at the foot of the band."""
    monkeypatch.setattr(C, "COACH_TRIAL_CHANCE", 1.0)
    monkeypatch.setattr(C, "COACH_NEED_CHANCE", 1.0)
    monkeypatch.setattr(C, "PROMOTE_AT", (60, 61))
    C._SEASON_CACHE.clear()
    career, _ = _drive(deck, model, MIDDLE_SEED, _setup("spin"), lambda c: 0)
    C._SEASON_CACHE.clear()
    trials = [r for r in career.seasons if r.role and r.role["trial"]]
    assert trials
    r = trials[0]
    fs = next(f for f in C.year_fs_ids(deck, r.year)
              if deck.cards_by_fs[f][0].franchise == r.franchise)
    assert C.lacking_band(deck, fs) == "middle", "the fixture must be short in the middle"
    assert set(r.role["positions"]) == set(C.MIDDLE_SLOTS)
    assert r.bat_at == C.FIRST_NUMBER[frozenset(r.role["positions"])]     # the foot of it


# --- 4. the gloves ---------------------------------------------------------------------------


def _keeper(deck, display=70, scoring=-0.5):
    base = next(c for cs in deck.cards_by_fs.values() for c in cs if c.role == "keeper")
    return dataclasses.replace(base, person_id="k", name="K", display=display,
                               bat_scoring=scoring, bat_dismissal=1.0, bat=scoring)


def test_a_weak_keeper_is_one_five_regular_batters_outbat(deck):
    you = _bat(deck, "you", C.MIDDLE_SLOTS, None)
    you = dataclasses.replace(you, person_id="career-you", bat_scoring=0.2, bat_dismissal=1.0)
    good = [dataclasses.replace(_bat(deck, f"b{i}", C.MIDDLE_SLOTS, 80), bat_scoring=0.1,
                                bat_dismissal=1.0) for i in range(5)]
    assert C.weak_keeper(_fake(good + [_keeper(deck)]), you, 1) == "K"
    assert C.weak_keeper(_fake(good[:4] + [_keeper(deck)]), you, 1) is None
    # not when he outbats you
    assert C.weak_keeper(_fake(good + [_keeper(deck, scoring=0.5)]), you, 1) is None
    worse = dataclasses.replace(you, bat_scoring=-0.9)
    assert C.weak_keeper(_fake(good + [_keeper(deck)]), worse, 1) is None


def test_the_offer_is_for_a_batter_still_in_his_role(deck):
    cp = C.Checkpoint(k=3)
    assert C.keeper_eligible(_setup("middle"), cp, 3, False)
    assert not C.keeper_eligible(_setup("middle"), cp, 0, False)         # not the first season
    assert not C.keeper_eligible(_setup("middle"), cp, 3, True)          # not a squad season
    assert not C.keeper_eligible(_setup("pace"), cp, 3, False)
    assert not C.keeper_eligible(_setup("keeper"), cp, 3, False)
    assert not C.keeper_eligible(_setup("middle"), C.Checkpoint(roles=["allrounder"]), 3, False)
    assert not C.keeper_eligible(_setup("middle"), C.Checkpoint(keeper_offers=C.KEEPER_OFFERS_MAX), 3, False)


def test_the_coach_asks_on_his_own_stream(deck):
    start = C.template_pools(deck, "middle")[0][0]
    seed = next(s for s in range(500)
                if random.Random(f"{s}:keeper:3").random() < C.KEEPER_ASK_CHANCE)
    cp = C.Checkpoint(k=3)
    card = C.coach_card(seed, _setup("middle"), start, cp, 3, False, keeper="RK Shaky")
    assert card["key"] == "keeper" and "RK Shaky" in card["text"]
    assert (C.coach_card(seed, _setup("middle"), start, cp, 3, False)
            or {}).get("key") != "keeper"
    # a season whose draw misses is not asked, even with a weak keeper
    miss = next(s for s in range(500)
                if random.Random(f"{s}:keeper:3").random() >= C.KEEPER_ASK_CHANCE)
    assert (C.coach_card(miss, _setup("middle"), start, cp, 3, False, keeper="RK Shaky")
            or {}).get("key") != "keeper"


def test_a_keeper_keeps_and_never_bowls(deck, model):
    bowling = next(c for c in C.template_pools(deck, "middle")[0] if c.bowl_scoring is not None)
    rd = C.role_state(deck, model, 1, _setup("middle"), bowling, C.KEEPER, 0.0, "", 2012)
    assert rd["kind"] == "keeper" and rd["add"] == "" and set(rd["positions"]) == set(bowling.positions)
    card = C.apply_role(bowling, rd, 0.5)
    assert card.role == "keeper" and card.keeper_eligible and not card.has_bowl
    assert card.bowl_scoring is None and card.bat_scoring == bowling.bat_scoring


def test_a_finisher_whose_template_was_an_allrounder_can_take_the_gloves(deck, model):
    """Found by the calibration: a finisher is drawn on batting alone, so his start can be a
    real all-rounder's season, and the gloves must not read his template's role."""
    start = next(c for c in C.template_pools(deck, "finisher")[0] if c.role == "allrounder")
    rd = C.role_state(deck, model, 1, _setup("finisher"), start, C.KEEPER, 0.0, "", 2012)
    assert rd["kind"] == "keeper" and set(rd["positions"]) == set(start.positions)


def test_your_side_keeps_you_behind_the_stumps(deck):
    fs = C.year_fs_ids(deck, 2015)[0]
    you = dataclasses.replace(_bat(deck, "career-you", C.MIDDLE_SLOTS, None), fs_id=fs,
                              role="keeper", keeper_eligible=True, bat_scoring=-0.9,
                              bat=-0.9, bowl=None, bowl_scoring=None, bowl_dismissal=None)
    order, impact = C.xi_with(you, list(deck.cards_by_fs[fs]))
    assert you in order
    assert [c.person_id for c in order if c.role == "keeper"] == [C.YOU_ID]


def test_taking_the_gloves_is_for_good(deck, model, monkeypatch):
    monkeypatch.setattr(C, "KEEPER_ASK_CHANCE", 1.0)
    monkeypatch.setattr(C, "WEAK_KEEPER", 0)
    monkeypatch.setattr(C, "COACH_TRIAL_CHANCE", 1.0)
    career, _ = _drive(deck, model, GLOVES_SEED, _setup("middle"),
                       lambda c: 0 if c["key"] == "keeper" else len(c["options"]) - 1)
    took = next(i for i, r in enumerate(career.seasons) if r.coach and r.coach["key"] == "keeper")
    after = career.seasons[took:]
    assert len(after) >= 2
    for r in after:
        assert r.role and r.role["role"] == C.KEEPER
        assert r.bowl_balls == 0
    for r in after[1:]:                          # and the coach never asks again
        assert not r.coach or r.coach["key"] not in ("trial_bowl", "keeper", "switch")
    assert career.role_now == C.KEEPER


def test_the_coach_asks_at_most_the_cap(deck, model, monkeypatch):
    monkeypatch.setattr(C, "KEEPER_ASK_CHANCE", 1.0)
    monkeypatch.setattr(C, "WEAK_KEEPER", 0)
    career, _ = _drive(deck, model, GLOVES_SEED, _setup("middle"), lambda c: len(c["options"]) - 1)
    asked = [r for r in career.seasons if r.coach and r.coach["key"] == "keeper"]
    assert len(asked) == 1        # A255's cap, stated: reading C.KEEPER_OFFERS_MAX could never fail (A207)
    assert all(r.role is None for r in career.seasons)        # declined, never a keeper


def test_new_fields_survive_a_checkpoint_round_trip():
    cp = C.Checkpoint(bat_band="middle", keeper_offers=1)
    back = C.Checkpoint.from_dict(cp.to_dict())
    assert (back.ceiling_band, back.bat_band, back.keeper_offers) == (True, "middle", 1)
