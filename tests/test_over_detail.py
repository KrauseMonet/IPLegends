"""[A309] An over of the Manhattan, opened: who bowled it and what each batter made of it.

The engine records each completed over's batters as their figures now less their figures
when the over began -- a side effect read from the loop, never a draw. What can go wrong is
the bookkeeping, so the scripted cases are worked out by hand, the real innings are checked
against the engine's own per-ball log (which shares no code with the tally), and the
tooltip's own function is run under node against the payload the API serves.
"""

from __future__ import annotations

import json
import pathlib
import random
import re
import shutil
import subprocess
from collections import defaultdict

import pytest

from etl.feasibility import BOWLERS_IN_TWELVE
from game.simulator import OFF_THE_BAT, Player, ball_log, play_innings

_VALUES = (-1.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
W, DOT, ONE, FOUR, SIX = 0, 1, 2, 5, 7     # outcome indices, as `draw` returns them


class _Script:
    """Plays a fixed sequence of outcomes, one per legal delivery, then dots."""

    wide_rate, wide_runs, extras_rate = 0.0, 1.0, 0.0

    def __init__(self, outcomes):
        self._left = list(outcomes)

    def state(self, over, wickets):
        k = self._left.pop(0) if self._left else DOT
        return tuple(1.0 if i == k else 0.0 for i in range(8)), _VALUES


def _innings(outcomes, overs=20):
    bat = [Player(f"p{i}", 0.0, person_id=f"id{i}") for i in range(11)]
    bowl = [Player(f"w{i}", 0.0, bowl=0.0, person_id=f"wid{i}")
            for i in range(BOWLERS_IN_TWELVE)]
    return play_innings(_Script(outcomes), bat, bowl, random.Random(0), overs=overs)


def _rows(over):
    return [(b.position, b.name, b.runs, b.balls, b.fours, b.sixes, b.out) for b in over.batters]


def test_an_over_with_a_wicket_and_a_new_batter_worked_by_hand():
    """p0 takes a single, p1 hits a four and is out next ball, p2 comes in: a dot, a six
    and a single. Three batters, in the order they came in; p1 is out IN this over."""
    inn = _innings([ONE, FOUR, W, DOT, SIX, ONE])
    o = inn.over_log[0]
    assert _rows(o) == [(0, "p0", 1, 1, 0, 0, False),
                        (1, "p1", 4, 2, 1, 0, True),
                        (2, "p2", 7, 3, 0, 1, False)]
    assert sum(b.runs for b in o.batters) == o.over_runs == 12


def test_a_batter_who_faced_nothing_is_left_out_and_an_earlier_wicket_is_not_this_overs():
    """Over 2 is six dots to one man: his partner faced nothing and is not listed, and p1,
    out in over 1, appears nowhere in over 2."""
    inn = _innings([ONE, FOUR, W, DOT, SIX, ONE])
    o = inn.over_log[1]
    assert len(o.batters) == 1
    b = o.batters[0]
    assert (b.runs, b.balls, b.out) == (0, 6, False)
    assert b.name != "p1"


def test_runs_are_this_overs_alone_not_the_batters_running_total():
    """p0 faces all six of over 1 (fours) and the second over too: his entry in over 2 is
    what he did in over 2, not his 24 + whatever."""
    inn = _innings([FOUR] * 6 + [DOT] * 6 + [ONE] * 6)
    first, third = inn.over_log[0], inn.over_log[2]
    assert _rows(first) == [(0, "p0", 24, 6, 6, 0, False)]
    # over 3: singles, so the two batters share it three balls each, a run a ball
    assert sorted((b.name, b.runs, b.balls) for b in third.batters) == [("p0", 3, 3), ("p1", 3, 3)]


def test_two_wickets_in_an_over_bring_in_two_new_men():
    inn = _innings([W, W, DOT, DOT, DOT, DOT])
    names = [(b.name, b.balls, b.out) for b in inn.over_log[0].batters]
    assert names == [("p0", 1, True), ("p2", 1, True), ("p3", 4, False)]


# --- real innings, checked against the engine's own per-ball log -------------------------

@pytest.fixture(scope="module")
def model():
    from tools import snapshot_deck
    doc = snapshot_deck.read_document()
    if doc is None:
        pytest.skip("no deck snapshot")
    return snapshot_deck.model_from(doc)


def _real_innings(model, seed):
    rng = random.Random(seed)
    bat = [Player(f"b{i}", rng.uniform(-0.3, 0.4), person_id=f"b{i}",
                  bat_scoring=rng.uniform(-0.2, 0.3)) for i in range(11)]
    bowl = [Player(f"o{i}", 0.0, bowl=0.0, person_id=f"o{i}") for i in range(BOWLERS_IN_TWELVE)]
    tally: dict = defaultdict(lambda: [0, 0, 0, 0, False])

    def log(over, wickets, striker, bowler, outcome, par):
        t = tally[(over, striker.player.person_id)]
        t[1] += 1
        if outcome == 0:
            t[4] = True
            return
        runs = OFF_THE_BAT[outcome - 1]
        t[0] += runs
        t[2] += runs == 4
        t[3] += runs == 6

    with ball_log(log):
        inn = play_innings(model, bat, bowl, random.Random(seed))
    return inn, tally


@pytest.mark.parametrize("seed", range(12))
def test_every_logged_over_matches_the_engines_own_ball_log(model, seed):
    inn, tally = _real_innings(model, seed)
    assert inn.over_log, "a twenty-over innings logs its overs"
    for o in inn.over_log:
        mine = {b.person_id: [b.runs, b.balls, b.fours, b.sixes, b.out] for b in o.batters}
        theirs = {pid: v for (over, pid), v in tally.items() if over == o.over}
        assert mine == theirs, o.over
        # the over's extras are what the batters did not score, never negative
        assert o.over_runs - sum(b.runs for b in o.batters) >= 0
        assert sum(b.balls for b in o.batters) == 6


@pytest.mark.parametrize("seed", range(12))
def test_each_batters_overs_add_up_to_his_card_less_any_unlogged_last_over(model, seed):
    inn, tally = _real_innings(model, seed)
    logged = {o.over for o in inn.over_log}
    for pos, card in enumerate(inn.batting):
        runs = sum(b.runs for o in inn.over_log for b in o.batters if b.position == pos)
        rest = sum(v[0] for (over, pid), v in tally.items()
                   if pid == card.player.person_id and over not in logged)
        assert runs + rest == card.runs
        for o in inn.over_log:
            for b in o.batters:
                assert inn.batting[b.position].player.person_id == b.person_id


# --- the API and the page -------------------------------------------------------------------

def test_the_api_names_a_batter_by_his_place_in_its_own_batting_list():
    import web.app as A
    inn = _innings([ONE, FOUR, W, DOT, SIX, ONE])
    out = A._innings_out(inn).model_dump()
    first = out["over_log"][0]
    assert [(out["batting"][b["pos"]]["name"], b["runs"], b["balls"], b["out"])
            for b in first["batters"]] == [("p0", 1, 1, False), ("p1", 4, 2, True),
                                            ("p2", 7, 3, False)]
    assert first["batters"][1]["fours"] == 1 and first["batters"][2]["sixes"] == 1


JS = pathlib.Path(__file__).resolve().parent.parent / "web" / "static" / "reveal.js"
COMMON = pathlib.Path(__file__).resolve().parent.parent / "web" / "static" / "common.js"


def _tip_html(over: dict, batting: list) -> str:
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    src, common = JS.read_text(), COMMON.read_text()
    take = lambda text, name: re.search(rf"function {name}\([\s\S]*?\n}}\n", text).group(0)
    body = "\n".join([take(common, "esc"), take(common, "shortName"),
                      take(src, "youTag"), take(src, "overTipHtml")])
    script = (f"{body}\nconsole.log(JSON.stringify(overTipHtml({json.dumps(over)}, "
              f"{{batting: {json.dumps(batting)}}})))")
    return json.loads(subprocess.run(["node", "-e", script], capture_output=True,
                                     text=True, check=True).stdout)


def test_the_tip_names_the_bowler_each_batter_and_the_extras():
    over = {"over": 6, "bowler": "Jasprit Bumrah", "over_runs": 13, "over_wickets": 1,
            "batters": [{"pos": 2, "runs": 10, "balls": 4, "fours": 1, "sixes": 1, "out": False},
                        {"pos": 3, "runs": 1, "balls": 2, "fours": 0, "sixes": 0, "out": True}]}
    batting = [{"name": "a"}, {"name": "b"}, {"name": "Virat Kohli"},
               {"name": "<img src=x onerror=1>", "you": True}]
    html = _tip_html(over, batting)
    assert "Over 7" in html and "J Bumrah" in html and "13 runs" in html and "1 wkt" in html
    kohli = html.index("V Kohli")
    assert "<strong>10</strong> <em>(4)</em>" in html[kohli:] and "1×4 1×6" in html[kohli:]
    # shortName cuts "<img" to its initial, so the name arrives as "&lt; src=x onerror=1&gt;"
    assert "onerror=1&gt;" in html and "< src" not in html, "a name is text, never markup"
    assert 'class="out you"' in html and "you-pill" in html
    assert '<span class="sb-tip-bits">out</span>' in html, "the dismissal is said, not only coloured"
    # 13 in the over, 11 off the bat: two extras
    assert "Extras</b><span class=\"sb-tip-fig\"><strong>2</strong>" in html


def test_an_over_with_no_extras_shows_no_extras_row():
    over = {"over": 0, "bowler": "B Kumar", "over_runs": 4, "over_wickets": 0,
            "batters": [{"pos": 0, "runs": 4, "balls": 6, "fours": 1, "sixes": 0, "out": False}]}
    html = _tip_html(over, [{"name": "Rohit Sharma"}])
    assert "Extras" not in html and "R Sharma" in html and "4 runs" in html
