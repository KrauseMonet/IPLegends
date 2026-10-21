"""Who bowls (A203): a bowler with real volume that season ranks ahead of one without.

Found through Build a Career's cap race: Adam Gilchrist, a keeper who bowled one over in real
2013, was rated as a frontline bowler and led the Purple Cap after five matches.
"""

from __future__ import annotations

import pytest

from etl.feasibility import Card
from game.__main__ import ATTACK_MIN_BALLS, attack, bowling_order, opposition_twelve
from game.season import super_over_bowler, Side
from tools.snapshot_deck import deck_from, model_from, read_document

DOC = read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="no deck snapshot")


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


def _thin(c):
    return c.bowl_balls is not None and c.bowl_balls < ATTACK_MIN_BALLS


def test_a_one_over_keeper_does_not_open_the_bowling(deck):
    fs = next(fs for fs, v in deck.cards_by_fs.items()
              if v[0].franchise == "Kings XI Punjab" and v[0].season_year == 2013)
    xi, _ = opposition_twelve(list(deck.cards_by_fs[fs]))
    five = [p.name for p in attack(xi, model_from(DOC))]
    assert "AC Gilchrist" not in five and len(five) == 5


def test_no_eleven_bowls_a_thin_season_while_a_real_bowler_waits(deck):
    """Across every franchise-season, within the eleven actually picked: a thin season
    bowls only when the eleven holds fewer than five real bowlers. (The squad can hold more
    than the eleven may field -- Mumbai 2025's Santner is kept out by the overseas cap, so
    Karn Sharma's 90 balls must bowl.)"""
    model = model_from(DOC)
    checked = 0
    for fs, squad in deck.cards_by_fs.items():
        xi, _ = opposition_twelve(list(squad))
        by_id = {c.person_id: c for c in xi}
        five = [by_id[p.person_id] for p in attack(xi, model)]
        real_in_xi = sum(c.has_bowl and not _thin(c) for c in xi)
        thin_bowling = sum(_thin(c) for c in five)
        assert thin_bowling == max(0, 5 - real_in_xi), (squad[0].franchise, squad[0].season_year)
        checked += 1
    assert checked == len(deck.cards_by_fs)


def test_within_each_group_the_better_bowler_comes_first():
    real = Card(fs_id=1, person_id="r", name="r", bowl=-0.05, bowl_balls=300)
    good = Card(fs_id=1, person_id="g", name="g", bowl=0.10, bowl_balls=300)
    thin = Card(fs_id=1, person_id="t", name="t", bowl=0.50, bowl_balls=6)
    unknown = Card(fs_id=1, person_id="u", name="u", bowl=0.01)        # no recorded volume
    order = sorted([thin, real, good, unknown], key=bowling_order)
    assert [c.name for c in order] == ["g", "u", "r", "t"]


def test_a_super_over_is_bowled_by_the_side_s_first_choice(deck):
    fs = next(fs for fs, v in deck.cards_by_fs.items()
              if v[0].franchise == "Kings XI Punjab" and v[0].season_year == 2013)
    xi, impact = opposition_twelve(list(deck.cards_by_fs[fs]))
    side = Side(name="KXP", short="KXP", xi=xi, impact=impact)
    first = min((c for c in xi if c.has_bowl), key=bowling_order)
    assert super_over_bowler(side).person_id == first.person_id
    assert not _thin(super_over_bowler(side))


def test_a_daily_keeps_the_order_it_was_scored_under(monkeypatch):
    """A day before A203's cut-off rebuilds its match with the OLD order, so its page never
    shows a scorecard that disagrees with its recorded result; a day after uses the new one."""
    import datetime
    from types import SimpleNamespace
    from web import daily

    thin = Card(fs_id=1, person_id="t", name="t", bowl=0.5, bowl_balls=6)
    seen = {}
    monkeypatch.setattr(daily, "_play_and_score",
                        lambda full, model, day, player, state: bowling_order(thin))
    before = SimpleNamespace(challenge_date=daily.BOWLING_ORDER_FROM - datetime.timedelta(days=1))
    after = SimpleNamespace(challenge_date=daily.BOWLING_ORDER_FROM)
    assert daily.play_and_score(None, None, before, 1, "") == (False, -0.5)
    assert daily.play_and_score(None, None, after, 1, "") == (True, -0.5)
    assert bowling_order(thin) == (True, -0.5)          # the switch does not leak out
