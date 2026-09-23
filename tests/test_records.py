"""game.records -- the archive-wide record book.

Pure function of a `Deck`, no database: a handful of hand-built `Card`s is the whole
fixture, matching tests/test_feasibility.py's own pattern for `team_rating`. Every test
here was verified to fail against the broken line it guards (floor removed, sort
direction flipped, wrong field read) before being accepted.
"""

from __future__ import annotations

from etl.feasibility import Card, Deck
from game.records import (
    BAT_BALLS_FLOOR,
    BOWL_BALLS_FLOOR,
    best_economy,
    best_strike_rate,
    most_runs,
    most_wickets,
    top_rated,
)


def card(person_id: str, name: str, **kw) -> Card:
    return Card(fs_id=kw.pop("fs_id", 1), person_id=person_id, name=name, **kw)


def deck_of(cards: list[Card]) -> Deck:
    by_fs: dict[int, list[Card]] = {}
    for c in cards:
        by_fs.setdefault(c.fs_id, []).append(c)
    return Deck(cards_by_fs=by_fs, fs_ids=sorted(by_fs))


def test_top_rated_sorts_descending_by_display():
    d = deck_of([
        card("a", "Low", display=60, role="batter"),
        card("b", "High", display=95, role="batter"),
        card("c", "Mid", display=80, role="batter"),
    ])
    rows = top_rated(d, limit=10)
    assert [r.name for r in rows] == ["High", "Mid", "Low"]


def test_top_rated_excludes_cards_with_no_rating():
    d = deck_of([card("a", "Rated", display=80, role="batter"), card("b", "Unrated", display=None)])
    rows = top_rated(d, limit=10)
    assert [r.name for r in rows] == ["Rated"]


def test_top_rated_role_filter_only_returns_that_role():
    d = deck_of([
        card("a", "Batter", display=90, role="batter"),
        card("b", "Bowler", display=95, role="bowler"),
    ])
    rows = top_rated(d, limit=10, role="batter")
    assert [r.name for r in rows] == ["Batter"]


def test_most_runs_is_a_plain_count_with_no_volume_floor():
    """A115's own argument: a count needs no floor. A four-innings cameo with more
    runs than a full-season player still outranks him here -- that's a real record,
    not noise the way a rate would be."""
    d = deck_of([
        card("a", "Big", display=80, role="batter", bat_runs=300, bat_balls=200),
        card("b", "Bigger", display=70, role="batter", bat_runs=350, bat_balls=40),
    ])
    rows = most_runs(d, limit=10)
    assert [r.name for r in rows] == ["Bigger", "Big"]


def test_most_wickets_is_a_plain_count_with_no_volume_floor():
    d = deck_of([
        card("a", "Few", display=80, role="bowler", bowl_wickets=10, bowl_balls=300),
        card("b", "Many", display=70, role="bowler", bowl_wickets=25, bowl_balls=60),
    ])
    rows = most_wickets(d, limit=10)
    assert [r.name for r in rows] == ["Many", "Few"]


def test_best_strike_rate_excludes_a_season_below_the_balls_floor():
    """A33's own floor (100 balls faced), reused rather than reinvented. A 3-ball
    cameo at strike rate 300 must not outrank a real 200-ball season."""
    d = deck_of([
        card("a", "Cameo", display=80, role="batter", bat_runs=9, bat_balls=3),
        card("b", "Real", display=80, role="batter", bat_runs=250, bat_balls=150),
    ])
    rows = best_strike_rate(d, limit=10)
    assert [r.name for r in rows] == ["Real"]
    assert BAT_BALLS_FLOOR == 100


def test_best_strike_rate_computed_correctly_and_sorted_descending():
    d = deck_of([
        card("a", "Slower", display=80, role="batter", bat_runs=200, bat_balls=150),
        card("b", "Faster", display=80, role="batter", bat_runs=300, bat_balls=150),
    ])
    rows = best_strike_rate(d, limit=10)
    assert rows[0].name == "Faster"
    assert rows[0].value == round(100 * 300 / 150, 1)


def test_best_economy_excludes_a_season_below_the_balls_floor():
    """A33's own bowling floor (150 legal balls). A single wicket-maiden over must not
    outrank a full season's economy."""
    d = deck_of([
        card("a", "OneOver", display=80, role="bowler", bowl_runs=0, bowl_balls=6, bowl_wickets=1),
        card("b", "FullSeason", display=80, role="bowler", bowl_runs=1000, bowl_balls=400, bowl_wickets=20),
    ])
    rows = best_economy(d, limit=10)
    assert [r.name for r in rows] == ["FullSeason"]
    assert BOWL_BALLS_FLOOR == 150


def test_best_economy_computed_correctly_and_sorted_ascending():
    """Lower economy is better, so this is the one board that sorts ascending rather
    than descending -- worth pinning explicitly since every other board here sorts the
    other way."""
    d = deck_of([
        card("a", "Expensive", display=80, role="bowler", bowl_runs=300, bowl_balls=200, bowl_wickets=10),
        card("b", "Tight", display=80, role="bowler", bowl_runs=150, bowl_balls=200, bowl_wickets=10),
    ])
    rows = best_economy(d, limit=10)
    assert rows[0].name == "Tight"
    assert rows[0].value == round(6 * 150 / 200, 2)
