"""[A318] The Almanack's pictures, on hand-built numbers, and their place on the real pages.

A picture must say what its numbers say: a bar's length is its share of the scale, a ring's
arcs add to the whole and its legend carries every count, a career chart has a bar per season.
And every figure a picture shows must also be on the page as text, which the page tests below
check on the committed files.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import almanack_viz as V
from tools import build_almanack as A

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "web" / "static"


@pytest.mark.parametrize("v,want", [(0, 1), (7, 10), (10, 10), (11, 20), (180, 200), (201, 500), (973, 1000)])
def test_a_chart_top_is_the_next_one_two_or_five(v, want):
    assert V.nice_max(v) == want


def test_a_par_bar_points_right_when_better_and_left_when_worse_at_its_share_of_the_scale():
    html = V.par_bars([("Powerplay", 20.0), ("Death", -10.0)], "x")
    rows = re.findall(r'<i class="(up|down)" style="width:([\d.]+)%"></i>', html)
    assert rows == [("up", "50.0"), ("down", "25.0")]      # largest value fills half the track
    assert "+20.0" in html and "−10.0" in html


def test_a_tiny_edge_does_not_fill_the_bar():
    html = V.par_bars([("Middle", 2.0)], "x")
    assert 'style="width:10.0%"' in html                    # scale never below 10


def test_a_par_chart_leaves_out_a_missing_value():
    assert V.par_bars([("Death", None)], "x") == ""


def test_a_ring_adds_to_the_whole_and_its_legend_carries_every_count():
    html = V.donut([("Caught", 6), ("Bowled", 3), ("LBW", 1)], "10", "dismissals")
    dashes = [float(a) for a in re.findall(r'stroke-dasharray="([\d.]+) ', html)]
    assert sum(dashes) == pytest.approx(100)
    assert dashes == pytest.approx([60, 30, 10])
    assert re.findall(r"<span>(\w+)</span><b>(\d+)</b>", html) == [("Caught", "6"), ("Bowled", "3"), ("LBW", "1")]
    offsets = [float(o) for o in re.findall(r'stroke-dashoffset="([-\d.]+)"', html)]
    assert offsets == pytest.approx([25, -35, -65])        # each arc starts where the last ended


def test_a_career_chart_has_a_bar_and_a_year_per_season_and_a_dot_per_card():
    pts = [V.SeasonPoint(2008 + i, "MI", "Mumbai Indians", 100 * i, i, 80 + i if i % 2 else None) for i in range(5)]
    html = V.career_chart(pts, ["runs", "wickets"])
    assert html.count('<g class="al-b crest-MI">') == 10      # five a metric
    assert html.count('class="al-yr"') == 5 and html.count('class="al-dot"') == 2
    assert 'data-metric="wickets" style="display:none"' in html
    assert '<g class="al-series" data-metric="runs">' in html   # the first metric shows


def test_a_career_chart_bar_height_is_its_share_of_the_top_line():
    pts = [V.SeasonPoint(2010, None, "X", 500, 0, None), V.SeasonPoint(2011, None, "X", 250, 0, None)]
    hs = [float(h) for h in re.findall(r'<rect [^>]*height="([\d.]+)"', V.career_chart(pts, ["runs"]))]
    assert hs == pytest.approx([150, 75])                  # 500 is the top line, 150 px high


def test_one_season_draws_no_chart():
    assert V.career_chart([V.SeasonPoint(2010, None, "X", 10, 0, None)], ["runs"]) == ""


def test_log_ticks_are_one_two_and_five_within_the_range():
    assert V.log_ticks(18, 600) == [20, 50, 100, 200, 500]
    ticks = V.log_ticks(18, 250000)
    assert all(18 <= t <= 250000 for t in ticks) and 0 < len(ticks) <= 6


def test_a_board_bar_is_each_row_as_a_share_of_the_leader():
    rows = [V.BoardRow("a", 100, "100", "", None, None), V.BoardRow("b", 25, "25", "", None, None)]
    assert re.findall(r'style="width:([\d.]+)%"', V.board(rows, "x")) == ["100.0", "25.0"]


# --------------------------------------------------------------------------- rivals, from the pairs

def _a(h2h):
    names = {"bat": "Batter", "n": "Nemesis", "f": "Fodder", "b": "Bunny", "t": "Tough", "x": "Other"}
    return SimpleNamespace(p=SimpleNamespace(h2h=h2h), name=lambda p: names[p], link=lambda p: names[p])


def test_the_nemesis_is_the_bowler_with_most_dismissals_and_ties_go_to_fewer_balls():
    h2h = [("bat", "n", 40, 30, 3), ("bat", "x", 20, 10, 3), ("bat", "f", 30, 60, 0)]
    html = A.rival_cards(_a(h2h), SimpleNamespace(person="bat"))
    assert "Nemesis</em><b>Other" in html                    # three each; 20 balls beats 40
    assert "Favourite bowler to face</em><b>Fodder" in html and "strike rate 200" in html


def test_no_nemesis_from_one_dismissal_and_no_favourite_under_the_strike_rate_bar():
    h2h = [("bat", "n", 40, 30, 1), ("bat", "f", 30, 36, 0)]          # SR 120 < 130
    assert A.rival_cards(_a(h2h), SimpleNamespace(person="bat")) == ""


def test_a_bowler_gets_a_bunny_and_a_toughest_batter():
    h2h = [("b", "bat", 30, 20, 4), ("t", "bat", 30, 66, 0), ("x", "bat", 10, 5, 1)]
    html = A.rival_cards(_a(h2h), SimpleNamespace(person="bat"))
    assert "Bunny</em><b>Bunny" in html and "Dismissed four times" in html
    assert "Toughest batter to bowl at</em><b>Tough" in html


# --------------------------------------------------------------------------- on the committed pages

def page(slug: str) -> str:
    return (STATIC / "almanack" / "players" / f"{slug}.html").read_text()


def test_a_players_chart_bars_are_the_season_tables_figures():
    text = page("virat-kohli")
    bars = dict(re.findall(r"<title>(\d{4}) · [^<]*? · ([\d,]+) runs", text))
    assert bars["2016"] == "973"
    from tests.test_almanack import section_table
    table = {r[0]: r[3] for r in section_table(text, "Season by season")}
    assert all(table[y] == v for y, v in bars.items())


def test_the_hero_card_carries_the_clubs_colours_and_keeps_the_tiles():
    text = page("jasprit-bumrah")
    assert re.search(r'<div class="al-hero crest-MI">', text)
    assert '<div class="al-tile"><b>' in text


def test_every_almanack_page_loads_the_script_once():
    for p in [STATIC / "almanack.html", *sorted((STATIC / "almanack").glob("*.html"))]:
        assert p.read_text().count('src="/static/almanack.js') == 1, p.name


def test_the_front_page_record_wall_agrees_with_the_facts_below_it():
    text = (STATIC / "almanack.html").read_text()
    wall = text.split('class="al-wall"', 1)[1].split("</section>", 1)[0]
    assert "<b class=\"al-num\">175*</b><span>Chris Gayle</span>" in wall
    assert "<b class=\"al-num\">6/12</b><span>Alzarri Joseph</span>" in wall
    from tests.test_almanack import visible
    assert "Chris Gayle's 175* off 66 balls" in visible(text)
