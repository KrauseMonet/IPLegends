"""Who bats, and where (A208): an opposition eleven picks batters with real volume that
season first, and bats them in A76's batting-role order rather than the per-season band.

Found by the user: Harshal Patel batting at three and Zaheer Khan at four.
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest

from etl.feasibility import Card
from game.__main__ import (BAT_MIN_BALLS, batting_choice, opposition_order,
                           opposition_twelve, opposition_xi)
from tools.snapshot_deck import deck_from, read_document

DOC = read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="no deck snapshot")


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


def _squad(deck, franchise, year):
    return next(list(v) for v in deck.cards_by_fs.values()
                if v[0].franchise == franchise and v[0].season_year == year)


def _thin(c):
    return c.bat_balls is not None and c.bat_balls < BAT_MIN_BALLS


def test_a_tail_ender_with_an_odd_band_bats_in_the_tail(deck):
    """Zaheer Khan 2011's squad band is `top_order`; A76 says 8-11, and so does the record."""
    squad = _squad(deck, "Royal Challengers Bangalore", 2011)
    zak = next(c for c in squad if c.name == "Z Khan")
    assert zak.band == "top_order" and min(zak.positions) == 8      # the fixture's shape
    order, _ = opposition_twelve(squad)
    assert zak in order and order.index(zak) + 1 >= 8


def test_every_side_bats_in_role_order(deck):
    for squad in deck.cards_by_fs.values():
        order, _ = opposition_twelve(list(squad))
        tops = [min(c.positions) for c in order]
        assert tops == sorted(tops), (squad[0].franchise, squad[0].season_year)


def test_mumbai_2024_picks_its_real_batters(deck):
    """Under the per-ball pick, L Wood's three balls outranked Rohit Sharma's 278."""
    xi = opposition_xi(_squad(deck, "Mumbai Indians", 2024))
    names = {c.name for c in xi}
    assert {"RG Sharma", "Tilak Varma", "TH David"} <= names
    assert "L Wood" not in names


def test_no_thin_batter_is_picked_while_a_real_one_waits():
    """The five batting places go to real volume first, whatever the per-ball rating."""
    keeper = Card(fs_id=1, person_id="k", name="k", role="keeper", bat=0.0, bat_balls=300,
                  positions=frozenset({1, 2, 3}))
    bowlers = [Card(fs_id=1, person_id=f"b{i}", name=f"b{i}", role="bowler", bowl=0.1,
                    bowl_balls=300, positions=frozenset({8, 9, 10, 11})) for i in range(5)]
    real = [Card(fs_id=1, person_id=f"r{i}", name=f"r{i}", role="batter", bat=-0.1 - i / 100,
                 bat_balls=250, positions=frozenset({3, 4, 5})) for i in range(5)]
    cameo = Card(fs_id=1, person_id="c", name="c", role="bowler", bat=0.6, bat_balls=3,
                 positions=frozenset({8, 9, 10, 11}))
    xi = opposition_xi([keeper, *bowlers, *real, cameo])
    assert cameo not in xi and all(r in xi for r in real)


def test_within_a_role_real_volume_then_rating_comes_first():
    pos = frozenset({1, 2, 3})
    good = Card(fs_id=1, person_id="g", name="g", bat=0.2, bat_balls=400, positions=pos)
    real = Card(fs_id=1, person_id="r", name="r", bat=0.0, bat_balls=400, positions=pos)
    thin = Card(fs_id=1, person_id="t", name="t", bat=0.9, bat_balls=10, positions=pos)
    unknown = Card(fs_id=1, person_id="u", name="u", bat=0.1, positions=pos)   # no volume
    tail = Card(fs_id=1, person_id="x", name="x", bat=1.5, bat_balls=500,
                positions=frozenset({8, 9, 10, 11}))
    order = opposition_order([tail, thin, real, good, unknown])
    assert [c.name for c in order] == ["g", "u", "r", "t", "x"]


def test_a_daily_keeps_the_batting_it_was_scored_under(monkeypatch):
    """A day before A208's cut-off rebuilds its match with the OLD batting, so its page never
    shows a scorecard that disagrees with its recorded result; a day after uses the new."""
    from web import daily

    thin = Card(fs_id=1, person_id="t", name="t", bat=0.5, bat_balls=6)
    # an old band that says open, a role that says tail -- and the reverse
    by_band = Card(fs_id=1, person_id="a", name="a", band="opener", positions=frozenset({8, 9}))
    by_role = Card(fs_id=1, person_id="b", name="b", band="tail", positions=frozenset({1, 2}))

    def probe(full, model, day, player, state):
        return batting_choice(thin), [c.name for c in opposition_order([by_role, by_band])]

    monkeypatch.setattr(daily, "_play_and_score", probe)
    before = SimpleNamespace(challenge_date=daily.BATTING_ORDER_FROM - datetime.timedelta(days=1))
    after = SimpleNamespace(challenge_date=daily.BATTING_ORDER_FROM)
    assert daily.play_and_score(None, None, before, 1, "") == ((False, -0.5), ["a", "b"])
    assert daily.play_and_score(None, None, after, 1, "") == ((True, -0.5), ["b", "a"])
    assert probe(None, None, None, 1, "") == ((True, -0.5), ["b", "a"])   # nothing leaks
