"""etl.feasibility.team_rating -- the squad-review screen's own three numbers.

Declared, not measured (CLAUDE.md's own standing category, alongside REPUTATION and
ALLROUNDER_RUNS): there is no separated batting-only or bowling-only rating per card to
average, so `batting`/`bowling` are means restricted by `role`, with an all-rounder
counted toward BOTH deliberately. These tests pin that formula directly, with no
database and no deck -- a dozen hand-built `Card`s is the whole fixture.
"""

from __future__ import annotations

from etl.feasibility import Card, team_rating


def card(display: int | None, role: str | None) -> Card:
    return Card(fs_id=1, person_id=f"p-{id(object())}", name="X", display=display, role=role)


def test_overall_is_the_plain_mean_of_every_cards_display():
    cards = [card(80, "batter"), card(90, "bowler"), card(70, "keeper")]
    r = team_rating(cards)
    assert r.overall == round((80 + 90 + 70) / 3)


def test_batting_and_bowling_split_by_role():
    cards = [
        card(80, "batter"), card(90, "batter"),   # batting only
        card(70, "bowler"), card(60, "bowler"),   # bowling only
        card(50, "keeper"),                       # batting only
    ]
    r = team_rating(cards)
    assert r.batting == round((80 + 90 + 50) / 3)
    assert r.bowling == round((70 + 60) / 2)
    assert r.overall == round((80 + 90 + 70 + 60 + 50) / 5)


def test_an_allrounder_counts_toward_both_sub_averages_deliberately():
    """The documented double-count: one blended rating has no split to divide between
    the two disciplines, so an all-rounder's `display` feeds both `batting` and
    `bowling` in full, not half of each."""
    cards = [card(80, "batter"), card(60, "bowler"), card(100, "allrounder")]
    r = team_rating(cards)
    assert r.batting == round((80 + 100) / 2)
    assert r.bowling == round((60 + 100) / 2)
    assert r.overall == round((80 + 60 + 100) / 3)


def test_a_squad_with_no_bowlers_gets_a_null_bowling_rating_not_zero():
    """A33/A43's own rule about an empty bucket: no evidence is None, never a
    fabricated 0 that would read as "this squad's bowling is terrible" instead of
    "this squad has nobody who bowls"."""
    cards = [card(80, "batter"), card(70, "keeper")]
    r = team_rating(cards)
    assert r.bowling is None
    assert r.batting is not None
    assert r.overall is not None


def test_cards_with_no_rating_at_all_are_excluded_everywhere():
    cards = [card(80, "batter"), card(None, "bowler")]
    r = team_rating(cards)
    assert r.overall == 80
    assert r.bowling is None


def test_an_empty_squad_is_null_across_the_board_not_a_crash():
    r = team_rating([])
    assert r.overall is None and r.batting is None and r.bowling is None
