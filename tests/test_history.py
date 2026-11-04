"""The written pages (A253): the season and franchise history, the guides, contact, sitemap.

They exist so a reader, and a search crawler, finds real text without running the game. The
ways they fail quietly are a page nothing routes to, a page the sitemap forgets, a table that
ranks a side above one that really finished ahead of it, and a figure that disagrees with the
public record. Each is a test. The pages are regenerated from the database by
`tools.build_history`, so these tests read the COMMITTED files and need no database.
"""

from __future__ import annotations

import html
import json
import pathlib
import re
import xml.etree.ElementTree as ET
from datetime import date

import pytest
from fastapi.testclient import TestClient

from tools import build_history as bh
from tools import build_sitemap
from web.app import app

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "web" / "static"
CLIENT = TestClient(app)

# The public record, not our reading of it (CLAUDE.md, check 7): every champion to 2025.
CHAMPIONS = {
    2008: "Rajasthan Royals", 2009: "Deccan Chargers", 2010: "Chennai Super Kings",
    2011: "Chennai Super Kings", 2012: "Kolkata Knight Riders", 2013: "Mumbai Indians",
    2014: "Kolkata Knight Riders", 2015: "Mumbai Indians", 2016: "Sunrisers Hyderabad",
    2017: "Mumbai Indians", 2018: "Chennai Super Kings", 2019: "Mumbai Indians",
    2020: "Mumbai Indians", 2021: "Chennai Super Kings", 2022: "Gujarat Titans",
    2023: "Chennai Super Kings", 2024: "Kolkata Knight Riders", 2025: "Royal Challengers Bengaluru",
}


def words_of(path: pathlib.Path) -> str:
    """What a crawler reads: the page with its scripts, styles and markup removed."""
    t = path.read_text()
    t = re.sub(r"<(script|style)\b.*?</\1>", " ", t, flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", t))


def history_files() -> list[pathlib.Path]:
    return sorted((STATIC / "seasons").glob("*.html")) + sorted((STATIC / "teams").glob("*.html"))


def written_files() -> list[pathlib.Path]:
    return (history_files() + sorted((STATIC / "guides").glob("*.html"))
            + [STATIC / n for n in ("seasons.html", "teams.html", "guides.html", "contact.html")])


def test_there_is_a_page_for_every_season_and_every_franchise():
    years = sorted(int(p.stem) for p in (STATIC / "seasons").glob("*.html"))
    assert years == list(range(2008, 2027))
    assert len(list((STATIC / "teams").glob("*.html"))) == 15


def test_every_written_page_is_served_by_the_app():
    for p in written_files():
        rel = p.relative_to(STATIC).with_suffix("")
        r = CLIENT.get(f"/{rel}")
        assert r.status_code == 200, rel
        assert "<h1" in r.text, rel


@pytest.mark.parametrize("path", ["/seasons/1999", "/teams/nobody", "/guides/..%2Fprofile",
                                  "/teams/Chennai-Super-Kings"])
def test_a_page_that_does_not_exist_is_a_404(path):
    assert CLIENT.get(path).status_code == 404


def test_every_written_page_is_routed_on_vercel():
    routes = [r for r in json.loads((ROOT / "vercel.json").read_text())["routes"] if "dest" in r]
    for p in written_files():
        url = "/" + str(p.relative_to(STATIC).with_suffix(""))
        hit = next((r for r in routes if re.fullmatch(r["src"], url)), None)
        assert hit is not None, url
        dest = re.sub(r"\$(\d)", lambda m: re.fullmatch(hit["src"], url).group(int(m.group(1))), hit["dest"])
        assert dest == "/web/static/" + str(p.relative_to(STATIC)), (url, dest)


def test_the_sitemap_lists_every_written_page_and_nothing_private():
    tree = ET.fromstring((STATIC / "sitemap.xml").read_text())
    locs = {e.text for e in tree.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")}
    for p in written_files():
        assert f"https://fineleg.app/{p.relative_to(STATIC).with_suffix('')}" in locs, p
    for private in ("/profile", "/admin", "/season", "/offline"):
        assert f"https://fineleg.app{private}" not in locs
    assert build_sitemap.main(check=True) == 0
    assert "Sitemap: https://fineleg.app/sitemap.xml" in (STATIC / "robots.txt").read_text()
    for path in ("/sitemap.xml", "/robots.txt"):
        assert CLIENT.get(path).status_code == 200


def test_every_sitemap_url_is_a_page_that_exists():
    tree = ET.fromstring((STATIC / "sitemap.xml").read_text())
    for loc in tree.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc"):
        assert CLIENT.get(loc.text.removeprefix("https://fineleg.app")).status_code == 200, loc.text


def test_each_written_page_has_real_text_for_a_reader():
    """The reason these pages exist: AdSense rejected the site for too little to read."""
    hubs = {STATIC / n for n in ("guides.html", "contact.html")}
    for p in written_files():
        n = len(words_of(p).split())
        assert n >= (250 if p in hubs else 450), (p.name, n)


def test_each_champion_is_the_one_the_public_record_names():
    for year, champ in CHAMPIONS.items():
        story = words_of(STATIC / "seasons" / f"{year}.html")
        assert re.search(rf"{re.escape(champ)} won their \w+ title", story) or \
            re.search(rf"\b{year}\b.*?{re.escape(champ)}", story), year
        index_row = re.search(rf"<b>{year}</b>.*?</tr>", (STATIC / "seasons.html").read_text(), re.S).group(0)
        champ_cell = re.findall(r'class="hx-team" href="/teams/([a-z-]+)"', index_row)[0]
        assert champ_cell == bh.slug(_canonical(champ)), year


def _canonical(name: str) -> str:
    from etl.franchise_map import canonical
    return canonical(name)


def test_kohli_2016_is_the_figure_the_record_holds():
    """A22's lesson: check the figure being claimed. 973 runs off 640 balls, 4 hundreds."""
    page = (STATIC / "seasons" / "2016.html").read_text()
    row = re.search(r"1\. Virat Kohli.*?</tr>", page, re.S).group(0)
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row)
    assert "973" in cells[1] and cells[4] == f"{100 * 973 / 640:.1f}" and cells[6] == "4/7"


def test_a_franchise_page_counts_every_name_it_played_under():
    page = words_of(STATIC / "teams" / "delhi-capitals.html")
    assert "Delhi Daredevils" in page and "Delhi Capitals" in page
    assert "Season by season" in page


# --- the table's rules, on a hand-built season --------------------------------------------

def _season(matches: list[tuple[int, int, int | None, str | None]]) -> bh.Record:
    """Four sides in 2016; each match is (a, b, winner or None, stage or None)."""
    fs = {i: (f"F{i}", 2016, f"Team {i}") for i in range(1, 5)}
    ms = {}
    for k, (a, b, w, stage) in enumerate(matches):
        ms[str(k)] = bh.Match(str(k), date(2016, 4, 1 + k), 2016, "Ground", "City", a, b, w,
                              "no result" if w is None else "runs", 5, None if w is None else "runs",
                              None, stage)
    return bh.Record(bh.Archive(fs, {}, ms, [], [], []))


def test_a_no_result_is_a_point_each_and_playoffs_are_not_league_points():
    rec = _season([(1, 2, 1, None), (3, 4, None, None), (1, 3, 3, "Final")])
    rows = {r["fs"]: r for r in rec.table(2016)}
    assert rows[1]["pts"] == 2 and rows[3]["pts"] == 1 and rows[4]["pts"] == 1
    assert rows[3]["p"] == 1, "a knockout match is not a league match"
    assert rows[1]["playoffs"] and rows[3]["playoffs"] and not rows[2]["playoffs"]


def test_level_on_points_a_side_that_went_through_is_listed_first():
    """It was ahead on net run rate, which the table cannot compute; alphabetical order put
    Royal Challengers Bengaluru seventh and marked qualified in 2024."""
    rec = _season([(1, 2, 2, None), (3, 4, 4, None), (2, 4, 4, "Qualifier 1"), (1, 3, 1, None)])
    order = [r["fs"] for r in rec.table(2016)]
    level = [fs for fs in order if rec.table(2016)[order.index(fs)]["pts"] == 2]
    assert level[0] in (2, 4) and level[1] in (2, 4), order


def test_the_finish_reads_off_the_final():
    rec = _season([(1, 2, 1, None), (3, 4, 3, "Qualifier 1"), (1, 3, 3, "Final")])
    assert [rec.finish(i) for i in (3, 1, 4, 2)] == ["Champions", "Runners-up", "Playoffs", "League stage"]


def test_the_wording_helpers():
    assert bh.poss("Mumbai Indians") == "Mumbai Indians'" and bh.poss("Virat Kohli") == "Virat Kohli's"
    assert bh.times(1) == "once" and bh.times(2) == "twice" and bh.times(5) == "five times"
    assert bh.overs(118) == "19.4" and bh.overs(120) == "20"
    assert bh.listing(["a", "b", "c"]) == "a, b and c"
    assert bh.slug("Royal Challengers Bengaluru") == "royal-challengers-bengaluru"
