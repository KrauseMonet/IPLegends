"""[A321] Shareable stat cards: what a card says, and that every page wires one up.

The drawing is canvas and checked by eye; what a card SAYS is a pure function (`scTiles`,
`scSeries`) run here under node on real players from the compare file, and the wiring is read
off the committed pages: the button names this page's own player and the compare file by its
hash, so a card can never be drawn from a stale file.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AL = ROOT / "web" / "static" / "almanack"
JS = ROOT / "web" / "static" / "statcard.js"
DOC = json.loads((AL / "compare.json").read_text())
URL = "/static/almanack/compare.json?v=" + hashlib.sha256((AL / "compare.json").read_text().encode()).hexdigest()[:10]


def run(fn: str, slug):
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    src = JS.read_text()
    body = "\n".join(re.search(rf"function {n}\([\s\S]*?\n}}\n", src).group(0) for n in ("scTiles", "scSeries"))
    out = subprocess.run(["node", "-e", f"{body}\nconsole.log(JSON.stringify({fn}({json.dumps(DOC['players'][slug] if isinstance(slug, str) else slug)})))"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_a_batters_card_leads_with_runs_and_shows_the_record():
    tiles = dict(run("scTiles", "virat-kohli"))
    labels = [t[0] for t in run("scTiles", "virat-kohli")]
    assert labels == ["Matches", "Runs", "Strike rate", "Average", "Highest", "Player of the Match"]
    assert tiles["Runs"] == "9,336" and tiles["Highest"] == DOC["players"]["virat-kohli"]["hs_t"]


def test_a_bowlers_card_leads_with_wickets():
    labels = [t[0] for t in run("scTiles", "jasprit-bumrah")]
    assert labels == ["Matches", "Wickets", "Economy", "Best", "Average", "Player of the Match"]
    assert dict(run("scTiles", "jasprit-bumrah"))["Best"] == DOC["players"]["jasprit-bumrah"]["best_t"]


def test_an_all_rounder_shows_both_led_by_the_discipline_known_for():
    labels = [t[0] for t in run("scTiles", "andre-russell")]
    assert labels[1:5] == ["Runs", "Wickets", "Strike rate", "Economy"]
    labels = [t[0] for t in run("scTiles", "rashid-khan")]
    assert labels[1:5] == ["Wickets", "Runs", "Economy", "Strike rate"]


def test_a_missing_figure_is_a_dash_never_a_zero():
    nobody = {"m": 1, "pom": 0, "runs": 0, "wkts": 0, "role": "batter", "sr": None, "avg": None, "hs_t": None, "s": []}
    tiles = dict(run("scTiles", nobody))
    assert (tiles["Runs"], tiles["Strike rate"], tiles["Average"], tiles["Highest"]) == ("0", "–", "–", "–")


def test_the_season_bars_are_the_figure_the_card_leads_with():
    s = run("scSeries", "jasprit-bumrah")
    assert s["label"] == "Wickets each season"
    assert [b[1] for b in s["bars"]] == [x[2] for x in DOC["players"]["jasprit-bumrah"]["s"]]
    s = run("scSeries", "virat-kohli")
    assert s["label"] == "Runs each season" and [2016, 973] in [b[:2] for b in s["bars"]]


def test_every_player_page_has_its_own_card_button_and_loads_the_script_once():
    pages = sorted((AL / "players").glob("*.html"))
    for p in pages:
        text = p.read_text()
        assert f'data-src="{URL}" data-slug="{p.stem}" onclick="scPlayerCard(this)"' in text, p.name
        assert text.count('src="/static/statcard.js') == 1, p.name
    assert len(pages) > 800


def test_the_compare_page_loads_the_card_script_and_offers_the_card():
    assert (AL / "compare.html").read_text().count('src="/static/statcard.js') == 1
    assert "onclick=\"scCompareCard()\"" in (ROOT / "web" / "static" / "compare.js").read_text()
