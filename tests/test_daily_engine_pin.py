"""[A259] A daily day keeps the engine numbers it was scored with. Days before the switch
replay with the frozen pre-A259 numbers in `data/engine_a160.json.gz`; days from it on use
the deck as it stands."""

from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest

from tools.snapshot_deck import deck_from, model_from, read_document
from web import daily as D

FIELDS = ("bat_scoring", "bat_dismissal", "bowl_scoring", "bowl_dismissal")


@pytest.fixture(scope="module")
def deck_model():
    doc = read_document()
    return deck_from(doc), model_from(doc)


def test_the_pin_covers_every_card_the_engine_plays(deck_model):
    deck, _ = deck_model
    pin = D.engine_a160()["cards"]
    missing = [(fs, c.person_id) for fs, cs in deck.cards_by_fs.items() for c in cs
               if any(getattr(c, f) is not None for f in FIELDS)
               and c.person_id not in pin.get(str(fs), {})]
    assert missing == []


def test_an_old_days_deck_carries_the_pinned_numbers_and_nothing_else_changes(deck_model):
    deck, _ = deck_model
    old = D.a160_deck(deck)
    pin = D.engine_a160()["cards"]
    differ = 0
    for fs, cards in deck.cards_by_fs.items():
        for now, then in zip(cards, old.cards_by_fs[fs]):
            vals = pin.get(str(fs), {}).get(now.person_id)
            if vals is not None:
                assert [getattr(then, f) for f in FIELDS] == vals
                differ += [getattr(now, f) for f in FIELDS] != vals
            for f in ("person_id", "display", "positions", "bat", "bowl", "keeper_eligible"):
                assert getattr(now, f) == getattr(then, f)
    assert differ > 3000            # A259 really moved the numbers an old day must not see


def test_an_old_days_model_centres_unrated_batters_on_the_old_season_average(deck_model):
    _, model = deck_model
    old = D.a160_model(model)
    pinned = {(d, int(y)): m for d, y, m in D.engine_a160()["season_mean_scoring"]}
    assert old.season_mean_scoring == pinned
    assert old.season_mean_scoring != model.season_mean_scoring
    assert old.grid is model.grid                  # nothing else of the model is touched


@pytest.mark.parametrize("offset, pinned", [(-1, True), (0, False), (5, False)])
def test_play_and_score_switches_engine_on_the_day(deck_model, monkeypatch, offset, pinned):
    deck, model = deck_model
    seen = {}

    def spy(full, mdl, day, player, state):
        seen["deck"], seen["model"] = full, mdl
        return "played"

    monkeypatch.setattr(D, "_play_and_score", spy)
    day = SimpleNamespace(
        challenge_date=D.ENGINE_TWO_LEVEL_FROM + datetime.timedelta(days=offset))
    assert D.play_and_score(deck, model, day, 1, "state") == "played"
    if pinned:
        assert seen["deck"] is D.a160_deck(deck)
        assert seen["model"].season_mean_scoring != model.season_mean_scoring
    else:
        assert seen["deck"] is deck and seen["model"] is model


def test_a_missing_pin_is_an_error_not_an_empty_pin(monkeypatch, tmp_path):
    monkeypatch.setattr(D, "ENGINE_A160_PIN", tmp_path / "absent.json.gz")
    D.engine_a160.cache_clear()
    try:
        with pytest.raises(OSError):
            D.engine_a160()
    finally:
        D.engine_a160.cache_clear()
