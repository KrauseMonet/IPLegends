"""[A262] A nationality corrected after days were played: the new value everywhere, the old
one on any daily day before the correction.

Kevon Cooper (Trinidad, Rajasthan Royals 2012-14) was hand-filled as India. He is a West
Indies player, which is the nation every Caribbean player in the archive carries.
"""
from __future__ import annotations

import csv
import datetime

import pytest

from etl.derive_people import OVERRIDES
from game import puzzle_facts
from tools.snapshot_deck import deck_from, model_from, read_document
from web import daily

COOPER = "557153ca"
SINCE, WAS = daily.NATIONALITY_FIXES[COOPER]


@pytest.fixture(scope="module")
def deck():
    doc = read_document()
    if doc is None:
        pytest.skip("no deck snapshot")
    return deck_from(doc)


def cooper_cards(deck):
    out = [c for cs in deck.cards_by_fs.values() for c in cs if c.person_id == COOPER]
    assert len(out) == 3   # Rajasthan Royals 2012, 2013, 2014
    return out


def test_the_override_says_west_indies():
    with (OVERRIDES / "nationality.csv").open(newline="", encoding="utf-8") as fh:
        row = next(r for r in csv.DictReader(fh) if r["person_id"] == COOPER)
    assert row["nationality"] == "West Indies"


def test_his_cards_are_overseas(deck):
    assert all(c.overseas is True for c in cooper_cards(deck))


def test_the_puzzles_know_his_country():
    doc = puzzle_facts.read_document()
    if doc is None:
        pytest.skip("no puzzle facts snapshot")
    p = next(p for p in puzzle_facts.players_from(doc) if p.person_id == COOPER)
    assert p.overseas is True and p.country == "West Indies"


def test_an_old_day_sees_the_old_value_and_nothing_else_moves(deck):
    old = daily.nationality_deck(deck, SINCE - datetime.timedelta(days=1))
    assert all(c.overseas is WAS for c in cooper_cards(old))
    for fs, cards in deck.cards_by_fs.items():
        for new, was in zip(cards, old.cards_by_fs[fs]):
            if new.person_id != COOPER:
                assert was is new


def test_a_day_from_the_correction_on_is_the_deck_itself(deck):
    assert daily.nationality_deck(deck, SINCE) is deck


@pytest.mark.parametrize("day,overseas", [
    (SINCE - datetime.timedelta(days=1), WAS),
    (SINCE, True),
])
def test_replay_day_drafts_with_the_value_of_its_day(deck, monkeypatch, day, overseas):
    seen = {}

    def spy(d, *args, **kwargs):
        seen["deck"] = d
        raise RuntimeError("stop")

    monkeypatch.setattr(daily.sess, "replay", spy)
    with pytest.raises(RuntimeError):
        daily.replay_day(deck, day, 1, deck.fs_ids[:16], ())
    assert all(c.overseas is overseas for c in cooper_cards(seen["deck"]))


@pytest.mark.parametrize("day,overseas", [
    (SINCE - datetime.timedelta(days=1), WAS),
    (SINCE, True),
])
def test_the_opposition_is_built_with_the_value_of_its_day(deck, monkeypatch, day, overseas):
    """An opposition eleven obeys the overseas cap as well, and four stored days had a
    Rajasthan squad of his as the opposition."""
    seen = {}

    def spy(full, *args, **kwargs):
        seen["deck"] = full
        raise RuntimeError("stop")

    monkeypatch.setattr(daily, "_play_and_score", spy)
    stub = daily.Day(day, 0, None, deck.fs_ids[:16])
    with pytest.raises(RuntimeError):
        daily.play_and_score(deck, model_from(read_document()), stub, 1, "")
    assert all(c.overseas is overseas for c in cooper_cards(seen["deck"]))
