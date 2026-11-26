"""[A322] The stats explorer: the packed ball file, the rules that add it up, and the browser's
agreement with them.

The rules are tested on a hand-built innings where each one is obvious. The packed file is tested
against the player pages (unfiltered, every player's runs, average, wickets, highest score and
hundreds must be the page's) and against the public record. The browser's `exAggregate` is run
under node on the real file and must give exactly the Python reference's answer for every kind of
question, which is what lets the page's worked examples stand for what the page will show.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import subprocess
from array import array
from pathlib import Path

import pytest

from tools import snapshot_explorer as E

ROOT = Path(__file__).resolve().parent.parent
AL = ROOT / "web" / "static" / "almanack"


@pytest.fixture(scope="module")
def real():
    return E.read()


def person(index, name):
    return next(i for i, p in enumerate(index["people"]) if p[0] == name)


# --------------------------------------------------------------------------- the rules, hand-built

def tiny(balls, styles=(0, 0, 0, 1, 2)):
    """One innings of `balls`: (over, striker, non-striker, bowler, rb, wd, nb, other, kind, who, phase).
    People 0-2 bat, 3 bowls pace, 4 bowls spin. Kinds: 1 caught, 2 run out, 3 retired hurt."""
    index = {"n": len(balls), "kinds": ["caught", "run out", "retired hurt"],
             "people": [[f"P{i}", f"p{i}", s] for i, s in enumerate(styles)],
             "teams": ["A", "B"], "grounds": [["G", "g"]], "matches": [[2020, "2020-04-01", 0, 0, "m"]],
             "innings": [[0, 1, 0, 1, 0, len(balls)]]}
    cols = {name: array("B" if t == "B" else "H", [b[i] for b in balls]) for i, (name, t) in enumerate(E.COLS)}
    return index, cols


def ball(st=0, ns=1, bw=3, rb=0, wd=0, nb=0, other=0, kind=0, who=0, phase=1, over=0):
    return (over, st, ns, bw, rb, wd, nb, other, kind, who, phase)


def test_a_wide_is_not_a_ball_faced_and_a_no_ball_is():
    i, c = tiny([ball(wd=1), ball(rb=4, nb=1), ball(rb=1)])
    r = E.aggregate(i, c, {"mode": "bat", "by": "player"})[0]
    assert (r["runs"], r["balls"], r["fours"]) == (5, 2, 1)


def test_a_bowler_is_charged_wides_and_no_balls_never_byes():
    i, c = tiny([ball(wd=2), ball(nb=1), ball(other=4), ball()])
    r = E.aggregate(i, c, {"mode": "bowl", "by": "player"})[3]
    assert (r["runs"], r["balls"], r["dots"]) == (3, 2, 2)


def test_a_run_out_is_the_batters_dismissal_but_not_the_bowlers_wicket():
    i, c = tiny([ball(kind=2, who=2), ball(st=0, ns=2, kind=1, who=1)])
    bat = E.aggregate(i, c, {"mode": "bat", "by": "player"})
    assert bat[1]["outs"] == 1 and bat[1]["balls"] == 0 and bat[1]["inns"] == 1   # out at the other end
    assert bat[0]["outs"] == 1
    assert E.aggregate(i, c, {"mode": "bowl", "by": "player"})[3]["wkts"] == 1


def test_retired_hurt_is_not_a_dismissal():
    i, c = tiny([ball(kind=3, who=1)])
    assert E.aggregate(i, c, {"mode": "bat", "by": "player"})[0]["outs"] == 0


def test_a_batting_position_is_the_order_of_first_arrival_at_either_end():
    i, c = tiny([ball(st=1, ns=0, rb=1), ball(st=0, ns=1, rb=2), ball(st=2, ns=1, rb=3)])
    by = E.aggregate(i, c, {"mode": "bat", "by": "position"})
    assert by[1]["runs"] == 1 and by[2]["runs"] == 2 and by[3]["runs"] == 3   # P1 opened on strike


def test_filters_by_phase_and_by_the_type_of_bowling():
    i, c = tiny([ball(rb=6, phase=1), ball(rb=4, phase=3, bw=4)])
    assert E.aggregate(i, c, {"mode": "bat", "by": "player", "phase": 3})[0]["runs"] == 4
    assert E.aggregate(i, c, {"mode": "bat", "by": "player", "style": 2})[0]["runs"] == 4
    assert E.aggregate(i, c, {"mode": "bowl", "by": "style"})[1]["runs"] == 6


def test_a_bowlers_team_is_the_side_bowling():
    i, c = tiny([ball(rb=1)])
    assert list(E.aggregate(i, c, {"mode": "bowl", "by": "team"})) == [1]
    assert list(E.aggregate(i, c, {"mode": "bat", "by": "team"})) == [0]
    assert E.aggregate(i, c, {"mode": "bowl", "by": "player", "team": 0}) == {}


def test_a_highest_score_not_out_is_marked_and_fifties_and_hundreds_are_per_innings():
    i, c = tiny([ball(rb=6)] * 9)
    r = E.aggregate(i, c, {"mode": "bat", "by": "player"})[0]
    assert (r["hs"], r["hs_not"], r["fifties"], r["hundreds"]) == (54, True, 1, 0)


# --------------------------------------------------------------------------- the real file

def test_unfiltered_every_player_is_the_player_pages_player(real):
    index, cols = real
    bat = E.aggregate(index, cols, {"mode": "bat", "by": "player"})
    bowl = E.aggregate(index, cols, {"mode": "bowl", "by": "player"})
    cmp = json.loads((AL / "compare.json").read_text())["players"]
    checked = 0
    for i, (_name, slug, _s) in enumerate(index["people"]):
        if slug not in cmp:
            continue
        p, b, w = cmp[slug], bat.get(i), bowl.get(i)
        assert (b["runs"] if b else 0) == p["runs"], slug
        assert (round(b["runs"] / b["outs"], 2) if b and b["outs"] else None) == p["avg"], slug
        assert (w["wkts"] if w else 0) == p["wkts"], slug
        assert (b["hundreds"] if b else 0) == p["h100"], slug
        checked += 1
    assert checked > 800


def test_kohli_2016_and_gayles_175_are_the_public_record(real):
    index, cols = real
    k = E.aggregate(index, cols, {"mode": "bat", "by": "season", "player": person(index, "Virat Kohli")})[2016]
    assert (k["runs"], k["balls"], k["hundreds"], k["hs"]) == (973, 640, 4, 113)
    g = E.aggregate(index, cols, {"mode": "bat", "by": "player", "from": 2013, "to": 2013})[person(index, "Chris Gayle")]
    assert (g["hs"], g["hs_not"]) == (175, True)


def test_the_file_holds_every_ball_but_super_overs(real):
    index, cols = real
    assert index["n"] == sum(i[5] for i in index["innings"]) == len(cols["over"]) == 295557


# --------------------------------------------------------------------------- the browser agrees

QUERIES = [
    {"mode": "bat", "by": "player"},
    {"mode": "bowl", "by": "player"},
    {"mode": "bat", "by": "season", "phase": 3},
    {"mode": "bowl", "by": "team", "style": 2},
    {"mode": "bat", "by": "opponent", "from": 2018, "to": 2022},
    {"mode": "bowl", "by": "ground", "inn": 2},
    {"mode": "bat", "by": "position", "stage": 2},
    {"mode": "bowl", "by": "position"},
    {"mode": "bat", "by": "style", "team": 3},
    {"mode": "bowl", "by": "innings", "opp": 5},
    {"mode": "bat", "by": "stage", "pos": 3},
    {"mode": "bowl", "by": "phase", "stage": 1},
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_browser_adds_up_exactly_as_the_reference_does(real, tmp_path):
    index, cols = real
    js = (ROOT / "web" / "static" / "explorer.js").read_text()
    import re
    fns = "\n".join(re.search(rf"function {n}\([\s\S]*?\n}}\n", js).group(0) for n in ("exAggregate", "exFinish"))
    consts = "\n".join(l for l in js.splitlines() if l.startswith(("const EX_NOT_OUT", "const EX_NOT_BOWLERS")))
    (tmp_path / "q.json").write_text(json.dumps(QUERIES))
    script = f"""
const fs = require('fs'), zlib = require('zlib');
{consts}
{fns}
const idx = JSON.parse(fs.readFileSync({json.dumps(str(E.INDEX))}, 'utf8'));
const raw = zlib.gunzipSync(fs.readFileSync({json.dumps(str(E.DATA))}));
const buf = raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength);
const c = {{}};
for (const [name, kind, off] of idx.cols) c[name] = kind === 'u8' ? new Uint8Array(buf, off, idx.n) : new Uint16Array(buf, off, idx.n);
const qs = JSON.parse(fs.readFileSync({json.dumps(str(tmp_path / 'q.json'))}, 'utf8'));
console.log(JSON.stringify(qs.map(q => exAggregate(idx, c, q))));
"""
    got = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    for q, js_res in zip(QUERIES, got):
        want = {str(k): v for k, v in E.aggregate(index, cols, q).items()}
        assert js_res == want, q
        assert want, q                                  # every question has an answer to compare


# --------------------------------------------------------------------------- the page

def test_the_page_loads_both_files_by_their_own_hash():
    page = (AL / "explorer.html").read_text()
    ih = hashlib.sha256(E.INDEX.read_text().encode()).hexdigest()[:10]
    dh = hashlib.sha256(E.DATA.read_bytes()).hexdigest()[:10]
    assert f'data-index="/static/almanack/explorer.json?v={ih}"' in page
    assert f'data-data="/static/almanack/explorer.bin.gz?v={dh}"' in page


def test_each_worked_example_is_on_the_page_with_a_button_that_asks_the_same_question():
    from tools import almanack_explorer as X
    page = (AL / "explorer.html").read_text()
    for title, _s, q, _n in X.EXAMPLES:
        assert f"<h3>{title}</h3>" in page
        assert json.dumps(q, separators=(",", ":")).replace('"', "&quot;") in page


def test_the_explorer_is_routed_linked_and_in_the_sitemap():
    from fastapi.testclient import TestClient
    from web.app import app
    assert TestClient(app).get("/almanack/explorer").status_code == 200
    assert "<loc>https://fineleg.app/almanack/explorer</loc>" in (ROOT / "web" / "static" / "sitemap.xml").read_text()
    assert 'href="/almanack/explorer"' in (ROOT / "web" / "static" / "almanack.html").read_text()


def test_the_data_file_is_small_enough_to_load_on_a_phone():
    assert E.DATA.stat().st_size < 1_000_000 and E.INDEX.stat().st_size < 300_000
    assert gzip.decompress(E.DATA.read_bytes())                 # and it is a gzip the browser can open
