"""[A259, A267] A daily day keeps the engine numbers it was scored with. Days before A259's
switch replay with the frozen pre-A259 numbers in `data/engine_a160.json.gz`; days from it
until A267's switch with A259's own numbers in `data/engine_a259.json.gz`; days from A267's
switch on use the deck as it stands."""

from __future__ import annotations

import dataclasses
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
    assert differ > 1000            # the pin is not today's numbers
    # A259 really moved the numbers an old day must not see: its own pin against A160's
    # (since A267 today's engine replays the season again, so it sits close to A160's)
    a259 = D.engine_a259()["cards"]
    moved = sum(vals != a259.get(fs, {}).get(p) for fs, cs in pin.items() for p, vals in cs.items())
    assert moved > 3000


def test_an_old_days_model_centres_unrated_batters_on_the_old_season_average(deck_model):
    _, model = deck_model
    old = D.a160_model(model)
    pinned = {(d, int(y)): m for d, y, m in D.engine_a160()["season_mean_scoring"]}
    assert old.season_mean_scoring == pinned
    assert old.season_mean_scoring != model.season_mean_scoring
    assert old.grid is model.grid                  # nothing else of the model is touched


@pytest.mark.parametrize("day_of, pinned", [
    (lambda: D.ENGINE_TWO_LEVEL_FROM - datetime.timedelta(days=1), "a160"),
    (lambda: D.ENGINE_TWO_LEVEL_FROM, "a259"),
    (lambda: D.REPLAY_ENGINE_FROM - datetime.timedelta(days=1), "a259"),
    (lambda: D.REPLAY_ENGINE_FROM, None),
    (lambda: D.REPLAY_ENGINE_FROM + datetime.timedelta(days=5), None),
])
def test_play_and_score_switches_engine_on_the_day(deck_model, monkeypatch, day_of, pinned):
    deck, model = deck_model
    seen = {}

    def spy(full, mdl, day, player, state):
        seen["deck"], seen["model"] = full, mdl
        return "played"

    monkeypatch.setattr(D, "_play_and_score", spy)
    day = SimpleNamespace(challenge_date=day_of())
    assert D.play_and_score(deck, model, day, 1, "state") == "played"
    if pinned == "a160":
        # [A260] an old day gets both pins: no situations, and the pre-A259 numbers
        # [A262] and a nationality corrected later is undone first
        assert seen["deck"] is D.a160_deck(D.plain_deck(
            D.nationality_deck(deck, day.challenge_date)))
        assert seen["model"].settle == {} and seen["model"].intent == {}
    elif pinned == "a259":
        # [A267] situations on (they shipped with A259's numbers), A259's numbers pinned
        base = D.nationality_deck(deck, day.challenge_date)
        if day.challenge_date < D.LEANS_CENTRED_FROM:     # [A268] and A260's batting leans
            base = D.a260_leans_deck(base)
        assert seen["deck"] is D.a259_deck(base)
        assert seen["model"].intent == model.intent
    else:
        assert seen["deck"] is deck and seen["model"] is model
    if pinned:
        assert seen["model"].season_mean_scoring != model.season_mean_scoring


def test_the_a259_pin_covers_every_card_and_differs_from_today(deck_model):
    """[A267] Every card the engine plays is in the A259 pin, and the pin is not simply
    today's numbers -- a pin that matched the deck would protect nothing."""
    deck, _ = deck_model
    pin = D.engine_a259()["cards"]
    cards = [(fs, c) for fs, cs in deck.cards_by_fs.items() for c in cs
             if any(getattr(c, f) is not None for f in FIELDS)]
    assert all(c.person_id in pin.get(str(fs), {}) for fs, c in cards)
    differ = sum(pin[str(fs)][c.person_id] != [getattr(c, f) for f in FIELDS] for fs, c in cards)
    assert differ > len(cards) / 2
    old = D.a259_deck(deck)
    c0 = next(c for c in old.cards_by_fs[cards[0][0]] if c.person_id == cards[0][1].person_id)
    assert [getattr(c0, f) for f in FIELDS] == pin[str(cards[0][0])][c0.person_id]


def test_a_missing_a259_pin_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(D, "ENGINE_A259_PIN", tmp_path / "absent.json.gz")
    D.engine_a259.cache_clear()
    try:
        with pytest.raises(OSError):
            D.engine_a259()
    finally:
        D.engine_a259.cache_clear()


def test_a_missing_pin_is_an_error_not_an_empty_pin(monkeypatch, tmp_path):
    monkeypatch.setattr(D, "ENGINE_A160_PIN", tmp_path / "absent.json.gz")
    D.engine_a160.cache_clear()
    try:
        with pytest.raises(OSError):
            D.engine_a160()
    finally:
        D.engine_a160.cache_clear()


def test_an_old_day_plays_with_no_situations(deck_model):
    """[A260, A261] No card carries a lean and the model has no curve and no intent."""
    deck, model = deck_model
    plain = D.plain_deck(deck)
    cards = [c for cs in plain.cards_by_fs.values() for c in cs]
    assert all(getattr(c, f) is None for c in cards for f in D.SITUATION_FIELDS)
    # [A265] named, not read off the tuple, so dropping it from the tuple cannot pass
    assert all(c.phase_wkt is None for c in cards)
    now = [c for cs in deck.cards_by_fs.values() for c in cs]
    assert sum(c.phase_bat is not None for c in now) > 2000      # the pin really clears them
    pm = D.plain_model(model)
    assert pm.settle == {} and pm.intent == {} and model.settle and model.intent
    assert pm.grid is model.grid


@pytest.mark.parametrize("offset, plain", [(-1, True), (0, False)])
def test_the_situations_switch_on_their_own_day(deck_model, monkeypatch, offset, plain):
    deck, model = deck_model
    seen = {}
    monkeypatch.setattr(D, "_play_and_score",
                        lambda full, mdl, day, player, state: seen.update(deck=full, model=mdl))
    monkeypatch.setattr(D, "ENGINE_TWO_LEVEL_FROM", datetime.date(2000, 1, 1))
    monkeypatch.setattr(D, "REPLAY_ENGINE_FROM", datetime.date(2000, 1, 1))
    day = SimpleNamespace(challenge_date=D.SITUATIONS_FROM + datetime.timedelta(days=offset))
    D.play_and_score(deck, model, day, 1, "state")
    assert (seen["deck"] is D.plain_deck(D.nationality_deck(deck, day.challenge_date))) == plain
    assert (seen["model"].intent == {}) == plain


# --------------------------------------------------------------------------- [A268]
LEANS = ("phase_bat", "style_bat", "burn")


def test_the_a260_lean_pin_covers_every_lean_and_differs_from_today(deck_model):
    deck, _ = deck_model
    pin = D.leans_a260()["cards"]
    cards = [(fs, c) for fs, cs in deck.cards_by_fs.items() for c in cs
             if any(getattr(c, f) is not None for f in LEANS)]
    assert all(c.person_id in pin.get(str(fs), {}) for fs, c in cards)
    now = lambda c: [list(getattr(c, f)) if getattr(c, f) is not None else None for f in LEANS]
    differ = sum(pin[str(fs)][c.person_id] != now(c) for fs, c in cards)
    assert differ > len(cards) / 2


def test_an_a260_days_deck_carries_the_old_leans_and_nothing_else(deck_model):
    deck, _ = deck_model
    old = D.a260_leans_deck(deck)
    pin = D.leans_a260()["cards"]
    for fs, cards in deck.cards_by_fs.items():
        for now, then in zip(cards, old.cards_by_fs[fs]):
            vals = pin.get(str(fs), {}).get(now.person_id)
            if vals is None:
                assert then is now
                continue
            assert [list(getattr(then, f)) if getattr(then, f) is not None else None
                    for f in LEANS] == vals
            assert all(isinstance(getattr(then, f), (tuple, type(None))) for f in LEANS)
            assert dataclasses.replace(then, **{f: getattr(now, f) for f in LEANS}) == now


def test_a_missing_a260_lean_pin_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(D, "LEANS_A260_PIN", tmp_path / "absent.json.gz")
    D.leans_a260.cache_clear()
    try:
        with pytest.raises(OSError):
            D.leans_a260()
    finally:
        D.leans_a260.cache_clear()


@pytest.mark.parametrize("offset, a260", [(0, True), (2, True), (3, False)])
def test_the_lean_centring_switches_on_its_own_day(deck_model, monkeypatch, offset, a260):
    """Days from SITUATIONS_FROM until LEANS_CENTRED_FROM keep A260's leans. The real dates
    leave that window empty (A268 shipped before SITUATIONS_FROM); widened here to test it."""
    deck, model = deck_model
    seen = {}
    monkeypatch.setattr(D, "_play_and_score",
                        lambda full, mdl, day, player, state: seen.update(deck=full, model=mdl))
    monkeypatch.setattr(D, "ENGINE_TWO_LEVEL_FROM", datetime.date(2000, 1, 1))
    monkeypatch.setattr(D, "REPLAY_ENGINE_FROM", datetime.date(2000, 1, 1))
    monkeypatch.setattr(D, "LEANS_CENTRED_FROM", D.SITUATIONS_FROM + datetime.timedelta(days=3))
    day = SimpleNamespace(challenge_date=D.SITUATIONS_FROM + datetime.timedelta(days=offset))
    D.play_and_score(deck, model, day, 1, "state")
    base = D.nationality_deck(deck, day.challenge_date)
    assert (seen["deck"] is D.a260_leans_deck(base)) == a260
    assert (seen["deck"] is base) == (not a260)
    assert seen["model"] is model
