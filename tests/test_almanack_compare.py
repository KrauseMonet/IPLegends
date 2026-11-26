"""[A320] Compare two players: the figures in the file, the rows it shows, and the page.

The rows and which way is better are defined once, in `tools.almanack_compare`, and travel in
the file. The page's own judging (`compare.js`) is run under node against the Python rules on
real players, so the two cannot disagree about who leads a row or which rows are shown.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tools import almanack_compare as C

ROOT = Path(__file__).resolve().parent.parent
AL = ROOT / "web" / "static" / "almanack"
DOC = json.loads((AL / "compare.json").read_text())
P = DOC["players"]


def tile(slug: str, label: str) -> int:
    text = (AL / "players" / f"{slug}.html").read_text()
    return int(re.search(rf'<div class="al-tile"><b>([\d,]+)</b><span>{label}</span>', text).group(1).replace(",", ""))


@pytest.mark.parametrize("slug", ["virat-kohli", "jasprit-bumrah", "andre-russell", "ms-dhoni"])
def test_the_figures_are_the_player_pages_figures(slug):
    p = P[slug]
    assert p["m"] == tile(slug, "Matches") and p["runs"] == tile(slug, "Runs") and p["wkts"] == tile(slug, "Wickets")


def test_the_figures_read_as_the_public_record():
    assert P["chris-gayle"]["hs_t"] == "175*" and P["chris-gayle"]["sixes"] == 357
    assert [s[1] for s in P["virat-kohli"]["s"] if s[0] == 2016] == [973]
    assert P["alzarri-joseph"]["best_t"] == "6/12"
    assert P["ab-de-villiers"]["pom"] == 25


def test_rates_are_worked_from_the_totals():
    k = P["virat-kohli"]
    assert k["sr"] is not None and k["avg"] is not None
    b = P["jasprit-bumrah"]
    assert b["avg"] is None or b["avg"] > 0
    assert P["jasprit-bumrah"]["econ"] is not None


def test_a_better_figure_is_higher_or_lower_as_its_row_says():
    x, y = {"econ": 7.1, "runs": 10}, {"econ": 8.0, "runs": 20}
    assert C.leader(x, y, "econ", "low") == -1 and C.leader(x, y, "runs", "high") == 1
    assert C.leader({"avg": None}, {"avg": 30}, "avg", "high") == 0       # nothing to compare is no lead
    assert C.leader({"m": 5}, {"m": 5}, "m", "high") == 0


def test_best_figures_rank_more_wickets_first_then_fewer_runs():
    assert C.leader(P["jasprit-bumrah"], P["lasith-malinga"], "best", "high") == -1   # 5/10 beats 5/13
    assert P["jasprit-bumrah"]["best_t"] == "5/10" and P["lasith-malinga"]["best_t"] == "5/13"


def test_two_bowlers_get_no_batting_rows_and_two_batters_no_bowling_rows():
    rows = {r[2] for r in C.shown_rows(P["jasprit-bumrah"], P["lasith-malinga"])}
    assert "bat" not in rows and "bowl" in rows
    rows = {r[2] for r in C.shown_rows(P["ms-dhoni"], P["ab-de-villiers"])}
    assert "bowl" not in rows and "bat" in rows


def test_the_file_carries_the_rows_this_module_defines():
    assert DOC["rows"] == [list(r) for r in C.ROWS]
    assert DOC["bat_from"] == C.BAT_ROWS_FROM and DOC["bowl_from"] == C.BOWL_ROWS_FROM


def test_the_page_loads_the_file_by_its_own_hash_and_the_head_to_head_file():
    page = (AL / "compare.html").read_text()
    text = (AL / "compare.json").read_text()
    assert f'data-src="/static/almanack/compare.json?v={hashlib.sha256(text.encode()).hexdigest()[:10]}"' in page
    assert re.search(r'data-h2h="/static/almanack/h2h\.json\?v=[0-9a-f]{10}"', page)


def test_the_written_comparison_is_the_default_pair_and_every_famous_pair_exists():
    page = (AL / "compare.html").read_text()
    a, b = C.DEFAULT
    assert f">{P[a]['name']} v {P[b]['name']}</h2>" in page
    for x, y in C.FAMOUS:
        assert x in P and y in P, (x, y)
        assert f"/almanack/compare?a={x}&amp;b={y}" in page


def test_every_player_page_links_to_its_comparison():
    for slug in ("virat-kohli", "jasprit-bumrah"):
        assert f'href="/almanack/compare?a={slug}"' in (AL / "players" / f"{slug}.html").read_text()


def test_the_compare_page_is_routed_and_in_the_sitemap():
    from fastapi.testclient import TestClient
    from web.app import app
    assert TestClient(app).get("/almanack/compare").status_code == 200
    assert "<loc>https://fineleg.app/almanack/compare</loc>" in (ROOT / "web" / "static" / "sitemap.xml").read_text()


# --------------------------------------------------------------------------- the page agrees with this module

PAIRS = [("virat-kohli", "rohit-sharma"), ("jasprit-bumrah", "lasith-malinga"), ("andre-russell", "hardik-pandya"),
         ("ms-dhoni", "ab-de-villiers"), ("sunil-narine", "rashid-khan"), ("shubman-gill", "yashasvi-jaiswal"),
         ("rishabh-pant", "rashid-khan")]          # a one-sided row: Pant never bowled


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_page_shows_the_same_rows_and_the_same_leader_as_this_module():
    src = (ROOT / "web" / "static" / "compare.js").read_text()
    fns = "\n".join(re.search(rf"function {n}\([\s\S]*?\n}}\n", src).group(0) for n in ("cmpLeader", "cmpRows"))
    script = (f"const CMP = {{doc: {json.dumps({k: DOC[k] for k in ('rows', 'bat_from', 'bowl_from')})}}};\n{fns}\n"
              f"const P = {json.dumps({s: P[s] for pair in PAIRS for s in pair})};\n"
              f"const pairs = {json.dumps(PAIRS)};\n"
              "console.log(JSON.stringify(pairs.map(([a, b]) => cmpRows(P[a], P[b]).map(r => "
              "[r[0], cmpLeader(P[a], P[b], r[0], r[3])]))));")
    got = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    want = [[[r[0], C.leader(P[a], P[b], r[0], r[3])] for r in C.shown_rows(P[a], P[b])] for a, b in PAIRS]
    assert got == want
    assert any(lead != 0 for pair in got for _k, lead in pair)
