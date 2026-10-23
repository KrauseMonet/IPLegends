"""Absences in Build a Career (A216): by tier and passport, a star's capped, an event card's
replacing the random one, and every absence with a reason."""

from __future__ import annotations

import random

import pytest

from game import career as C
from tools.snapshot_deck import deck_from, model_from, read_document

DOC = read_document()


def test_availability_follows_the_rating_tier_and_passport():
    assert C.availability_shares(None, False) is C.AVAILABILITY_SHARES
    assert C.availability_shares(90, False) is C.AVAILABILITY_BY_TIER[(88, False)]
    assert C.availability_shares(85, True) is C.AVAILABILITY_BY_TIER[(82, True)]
    assert C.availability_shares(75, False) is C.AVAILABILITY_BY_TIER[(0, False)]


def test_a_star_loses_at_most_a_quarter_of_the_league_to_chance():
    cap = int(C.STAR_MAX_ABSENCE * 14)
    for seed in range(300):
        block, _ = C.absence(seed, 3, 14, rating=95, overseas=True, role="pace")
        assert len(block) <= cap
    assert max(len(C.absence(s, 3, 14, rating=75, role="pace")[0]) for s in range(300)) > cap


def test_an_event_cards_absence_replaces_the_random_one():
    for seed in range(200):
        block, reason = C.absence(seed, 2, 14, extra=4, rating=80, role="batter")
        assert len(block) == 4 and reason is None
        sq, _ = C.absence(seed, 2, 14, squad=True, extra=2, rating=80)
        base, _ = C.absence(seed, 2, 14, squad=True, rating=80)
        assert len(sq) == max(2, len(base))
    # without a Rating (older callers) the old rule still adds them
    assert len(C.missed_block(5, 2, 14, extra=4)) == min(14, len(C.missed_block(5, 2, 14)) + 4)


def test_every_random_absence_has_a_reason_and_duty_sits_at_an_end():
    seen = set()
    for seed in range(400):
        block, reason = C.absence(seed, 1, 14, rating=80, overseas=True, role="spin")
        assert (reason is not None) == bool(block)
        if reason:
            seen.add(reason)
        if reason == "international duty":
            assert block.start == 0 or block.stop == 14
    assert "international duty" in seen and seen & set(C.INJURIES["spin"])
    for seed in range(200):
        _, reason = C.absence(seed, 1, 14, rating=80, overseas=False, role="spin")
        assert reason != "international duty"


# --- the wiring: a season really reads your Rating, passport and kind -----------------------


@pytest.fixture(scope="module")
def deck_model():
    if DOC is None:
        pytest.skip("no deck snapshot")
    return deck_from(DOC), model_from(DOC)


def test_a_season_draws_its_absence_with_your_rating_passport_and_kind(deck_model, monkeypatch):
    deck, model = deck_model
    seen = {}

    def spy(seed, k, n, squad=False, extra=0, form_drop=0.0, rating=None, overseas=False,
            role=None):
        seen.update(rating=rating, overseas=overseas, role=role)
        return range(0, 3), "a back spasm"

    monkeypatch.setattr(C, "absence", spy)
    start, ceiling = C.spin(deck, 3, "spin")
    fs = C.year_fs_ids(deck, 2014)[2]
    c0 = deck.cards_by_fs[fs][0]
    you = C.ability(start, ceiling, 0.6, setup=C.Setup("You", "spin", 2012, True), fs_id=fs,
                    year=2014, franchise=c0.franchise)
    year = C.simulate_year(deck, model, 3, 2, you, rating=91)
    assert seen == {"rating": 91, "overseas": True, "role": "spin"}
    assert year.absence_reason == "a back spasm" and year.missed >= 3


def test_a_careers_season_card_says_why_you_missed_matches(deck_model, monkeypatch):
    deck, model = deck_model
    monkeypatch.setattr(C, "absence", lambda *a, **kw: (range(0, 2), "a split webbing"))
    C._SEASON_CACHE.clear()
    try:
        setup, moves, rng = C.Setup("You", "spin", 2012), [], random.Random(1)
        while True:
            career = C.play_career(deck, model, 5, setup, moves)
            if career.seasons:
                break
            moves.append(C.policy_move(career, "strength", rng))
    finally:
        C._SEASON_CACHE.clear()
    first = career.seasons[0]
    assert first.absence_reason == "a split webbing"
    n = first.missed - first.dropped
    assert n >= 2
    assert f"You missed {n} league matches: a split webbing." in first.notes
