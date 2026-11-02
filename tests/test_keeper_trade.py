"""[A244] A wicketkeeper by trade can fill the keeper place, site-wide.

`Card.keeper_eligible` used to mean "kept wicket in some IPL season" (A77). It now also
admits a keeper by trade, decided for every player from the public record in
`keepers.csv`. Robin Minz and Abishek Porel are the reported case: both keeper-batters,
neither ever behind the stumps in the IPL, so the old rule marked them wrong on Bingo's
Wicketkeeper square and left them unable to fill a draft's keeper place.

None of this is a CHECK in the database: the rule lives in `load_deck`, the CSV and the
daily's replay, so these tests are what protect it (CLAUDE.md's standing rule).
"""

from __future__ import annotations

import csv
import datetime

import pytest

from etl.derive_people import OVERRIDES
from game import puzzle_facts
from tools.snapshot_deck import deck_from, read_document
from web import daily

MINZ, POREL = "bafd0398", "ad3b6e95"          # keepers by trade, never kept in the IPL
DHONI, KOHLI = "4a8a2e3b", "ba607b88"        # an IPL keeper; never a keeper at all
GURBAZ = "0bacade8"                          # kept in the IPL


@pytest.fixture(scope="module")
def deck():
    doc = read_document()
    if doc is None:
        pytest.skip("no deck snapshot")
    return deck_from(doc)


def cards_of(deck, person_id):
    out = [c for cs in deck.cards_by_fs.values() for c in cs if c.person_id == person_id]
    assert out, person_id
    return out


def rows():
    with (OVERRIDES / "keepers.csv").open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# --- the decisions -------------------------------------------------------------------

def test_every_player_on_a_card_has_a_decided_row(deck):
    """Blank is undecided, and an undecided keeper reads as a no: every player who can
    reach a card must have been looked at."""
    decided = {r["person_id"]: r for r in rows()}
    people = {c.person_id for cs in deck.cards_by_fs.values() for c in cs}
    missing = sorted(p for p in people if decided.get(p, {}).get("is_keeper", "") == "")
    assert missing == []


def test_every_decision_says_where_it_came_from():
    for r in rows():
        if r["is_keeper"]:
            assert r["source"] in {"ipl", "web", "web-search", "ipl-bowler", "judgement"}, r


def test_somebody_who_kept_in_the_ipl_is_never_decided_against():
    """An IPL keeper is proved by the archive (A24/A52); a row may not overrule that."""
    for r in rows():
        if r["stumpings"] not in ("", "0"):
            assert r["is_keeper"] == "y", r["name"]


# --- the deck ------------------------------------------------------------------------

def test_a_keeper_by_trade_can_keep(deck):
    for pid in (MINZ, POREL):
        for c in cards_of(deck, pid):
            assert c.keeper_eligible, c
            assert c.keeper_ipl is False, c        # ... but did not under A77


def test_ipl_keepers_and_non_keepers_are_unchanged(deck):
    for pid, can in ((DHONI, True), (GURBAZ, True), (KOHLI, False)):
        for c in cards_of(deck, pid):
            assert c.keeper_eligible is can, c
            assert c.keeper_ipl is None, c         # the two rules agree


def test_the_season_role_is_not_touched(deck):
    """Who held the gloves for a squad is A24/A52's answer and stays it."""
    assert all(c.role != "keeper" for pid in (MINZ, POREL) for c in cards_of(deck, pid))


# --- the daily keeps the rule it was scored under ------------------------------------

def test_the_old_rule_deck_is_exactly_a77(deck):
    old = daily.ipl_keeper_deck(deck)
    for fs, cards in deck.cards_by_fs.items():
        for new, was in zip(cards, old.cards_by_fs[fs]):
            want = new.keeper_eligible if new.keeper_ipl is None else new.keeper_ipl
            assert was.keeper_eligible is want
            assert was.person_id == new.person_id
    assert any(c.keeper_eligible for c in cards_of(old, DHONI))
    assert not any(c.keeper_eligible for c in cards_of(old, MINZ))


@pytest.mark.parametrize("day,minz_can", [
    (daily.KEEPER_TRADE_FROM - datetime.timedelta(days=1), False),
    (daily.KEEPER_TRADE_FROM, True),
])
def test_replay_day_uses_the_rule_of_its_day(deck, monkeypatch, day, minz_can):
    seen = {}

    def spy(d, *args, **kwargs):
        seen["deck"] = d
        raise RuntimeError("stop")

    monkeypatch.setattr(daily.sess, "replay", spy)
    with pytest.raises(RuntimeError):
        daily.replay_day(deck, day, 1, deck.fs_ids[:16], ())
    got = seen["deck"].cards_by_fs
    assert all(c.keeper_eligible is minz_can
               for cs in got.values() for c in cs if c.person_id == MINZ)


# --- the puzzles ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def facts():
    doc = puzzle_facts.read_document()
    if doc is None:
        pytest.skip("no puzzle facts snapshot")
    return {p.person_id: p for p in puzzle_facts.players_from(doc)}


def test_a_keeper_by_trade_fits_the_wicketkeeper_square(facts):
    assert facts[MINZ].keeper and facts[POREL].keeper
    assert not facts[KOHLI].keeper


def test_a_keeper_batter_reads_keeper_on_the_role_tile(facts):
    assert facts[MINZ].role == facts[POREL].role == "keeper"
    assert facts[KOHLI].role == "batter"


# --- the two rules, without the snapshots --------------------------------------------

from etl.feasibility import keeper_rule
from game.puzzle_facts import career_role


@pytest.mark.parametrize("role,kept,trade,want", [
    ("keeper", False, False, (True, True)),      # keeps this season
    ("batter", True, False, (True, True)),       # kept in another IPL season (A77)
    ("batter", False, True, (True, False)),      # by trade only: new under A244
    ("batter", False, False, (False, False)),
    ("bowler", False, False, (False, False)),
])
def test_keeper_rule(role, kept, trade, want):
    assert keeper_rule(role, "p", {"p"} if kept else set(), {"p"} if trade else set()) == want


@pytest.mark.parametrize("role,keeper,want", [
    ("batter", True, "keeper"), ("batter", False, "batter"),
    ("allrounder", True, "allrounder"), ("bowler", True, "bowler"),
    ("keeper", True, "keeper"), (None, False, None),
])
def test_career_role(role, keeper, want):
    assert career_role(role, keeper) == want


# --- the auction's sets and the card's icon ------------------------------------------

from dataclasses import replace as _replace

from etl.feasibility import Card, reads_as_keeper
from game import auction


def _card(role, can_keep, style=None):
    return Card(1, "p", "P", band="top_order", role=role, keeper_eligible=can_keep,
                bowling_style=style, positions=frozenset({1, 2, 3}))


@pytest.mark.parametrize("role,can_keep,style,want", [
    ("keeper", True, None, "WK"),
    ("batter", True, None, "WK"),        # a keeper by trade is sold with the keepers
    ("batter", False, None, "BA"),
    ("allrounder", True, None, "AL"),    # keeps his own set; the glove badge marks him
    ("bowler", True, "pace", "FA"),
])
def test_auction_set(role, can_keep, style, want):
    assert auction._category(_card(role, can_keep, style)) == want


def test_minz_is_sold_with_the_keepers_and_shows_the_gloves(deck):
    from web.app import _kind
    for c in cards_of(deck, MINZ):
        assert reads_as_keeper(c) and auction._category(c) == "WK" and _kind(c) == "keeper"
    for c in cards_of(deck, KOHLI):
        assert auction._category(c) != "WK" and _kind(c) == c.role


def test_a_squad_with_two_keepers_wants_a_keeper_by_trade_less():
    """The demand curve treats a batter who can keep exactly like a listed keeper."""
    team = auction.Team(0, "T", "T")
    for i in range(2):
        team.squad.append(_replace(_card("keeper", True), person_id=f"k{i}"))
    trade = _card("batter", True)
    plain = _card("batter", False)
    assert auction.need(team, trade) < auction.need(team, plain)
