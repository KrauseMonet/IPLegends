"""The profile shows the five best inducted careers; every career is on /hall.

Runs the pages' own `renderHall` (profile.js) and `renderAllHall` (hall.js) under node, with
`hof.js` loaded first as the pages load it, and a fake `$` that records what is drawn.
Skipped without node, like the other JS tests.
"""

import json
import pathlib
import re
import shutil
import subprocess

import pytest

from web.app import app

STATIC = pathlib.Path(__file__).resolve().parent.parent / "web" / "static"
ROOT = STATIC.parent.parent
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

HARNESS = r"""
const html = {};
global.$ = sel => ({ set innerHTML(v){ html[sel] = v; }, set textContent(v){ html[sel] = v; } });
global.esc = s => String(s);
global.crestImg = () => '';
global.skillChips = () => '';
__SRC__
__CALL__(__LIST__);
console.log(JSON.stringify(html));
"""


def career(i: int, inducted: bool) -> dict:
    return {"name": f"Player {i}", "role_label": "Opening batter", "overseas": False,
            "debut": 2010, "seasons": 12, "runs": 5000 - i, "wickets": 0, "titles": 1,
            "peak_card": 90, "crests": [], "inducted": inducted, "honours": [], "card": {}}


def fn(src: str, name: str) -> str:
    return re.search(rf"(?:const HOF_SHOWN[^\n]*\n\n)?function {name}\([\s\S]*?\n}}\n", src).group(0)


def run(page: str, careers: list[dict]) -> dict:
    hof = (STATIC / "hof.js").read_text()
    if page == "profile":
        src = hof + fn((STATIC / "profile.js").read_text(), "renderHall")
        call = "renderHall"
    else:
        src = hof + fn((STATIC / "hall.js").read_text(), "renderAllHall")
        call = "renderAllHall"
    script = (HARNESS.replace("__SRC__", src).replace("__CALL__", call)
              .replace("__LIST__", json.dumps(careers)))
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def names(drawn: str) -> list[str]:
    return re.findall(r"<b>(Player \d+)</b>", drawn)


@needs_node
def test_the_profile_shows_the_five_best_inducted_careers_and_links_the_rest():
    careers = [career(i, inducted=i % 3 != 2) for i in range(12)]   # 8 inducted, 4 not
    drawn = run("profile", careers)["#hofList"]
    assert names(drawn) == ["Player 0", "Player 1", "Player 3", "Player 4", "Player 6"]
    assert 'href="/hall"' in drawn and "See all 12 careers" in drawn


@needs_node
def test_the_profile_shows_no_link_when_nothing_is_left_out():
    drawn = run("profile", [career(i, inducted=True) for i in range(3)])["#hofList"]
    assert len(names(drawn)) == 3
    assert 'href="/hall"' not in drawn


@needs_node
def test_the_profile_still_links_careers_that_are_not_inducted():
    drawn = run("profile", [career(i, inducted=False) for i in range(2)])["#hofList"]
    assert names(drawn) == []
    assert "See all 2 careers" in drawn


@needs_node
def test_the_hall_page_lists_every_career_inducted_first():
    careers = [career(i, inducted=i % 3 != 2) for i in range(12)]
    drawn = run("hall", careers)["#hofList"]
    assert len(names(drawn)) == 12
    inducted = [c["name"] for c in careers if c["inducted"]]
    assert names(drawn)[:8] == inducted
    assert "Other careers · 4" in drawn


def test_the_hall_page_is_served_and_kept_out_of_search():
    from fastapi.testclient import TestClient
    assert TestClient(app).get("/hall").status_code == 200
    routes = json.loads((ROOT / "vercel.json").read_text())["routes"]
    assert any(r.get("src") == "/hall" and r.get("dest", "").endswith("hall.html") for r in routes)
    assert "Disallow: /hall" in (STATIC / "robots.txt").read_text()
    page = (STATIC / "hall.html").read_text()
    assert page.index("/static/hof.js") < page.index("/static/hall.js")
    profile = (STATIC / "profile.html").read_text()
    assert profile.index("/static/hof.js") < profile.index("/static/profile.js")
