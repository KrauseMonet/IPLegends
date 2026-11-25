"""[A315] The Almanack: the committed pages, the rules that build them, and their wiring.

The rules are tested on hand-built rows (no database). The committed pages are tested as a
reader and a crawler meet them: the index policy, the sitemap, the links, the record against
the public one, and two figures each page computes by different routes that must agree.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest

from tools import build_almanack as A
from tools.build_history import Bat, Bowl

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "web" / "static"
PLAYERS = STATIC / "almanack" / "players"


def page(slug: str) -> str:
    return (PLAYERS / f"{slug}.html").read_text()


def visible(text: str) -> str:
    body = text.split("<article", 1)[1].split("</article>", 1)[0]
    body = re.sub(r"</?a\b[^>]*>", "", body)          # a link is part of its sentence
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body)))


def section_table(text: str, heading: str) -> list[list[str]]:
    """The rows of the first table after an <h2> or <h3> with this heading."""
    part = text.split(f">{heading}</h", 1)[1]
    body = part.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    return [[html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr)]
            for tr in re.findall(r"<tr>(.*?)</tr>", body)]


def matches_on(text: str) -> int:
    return int(re.search(r'<div class="al-tile"><b>([\d,]+)</b><span>Matches</span>', text).group(1).replace(",", ""))


def num(s: str) -> int:
    return int(s.replace(",", ""))


# --------------------------------------------------------------------------- rules, hand-built

def test_par_is_the_league_in_his_own_seasons_weighted_by_his_balls():
    # The league scored 1.0 a ball in 2010 and 2.0 in 2020. He faced 10 balls in 2010 and 30 in
    # 2020, so par for his 40 balls is 10 + 60 = 70 -- not 40 x the archive's mean.
    par = {(2010, "pp"): (100, 100, 5), (2020, "pp"): (400, 200, 10)}
    rows = [("x", 2010, "pp", 15, 10, 1), ("x", 2020, "pp", 60, 30, 2)]
    s = A._splits(rows, par)["x"]["pp"]
    assert (s.runs, s.balls, s.outs) == (75, 40, 3)
    assert s.par_runs == pytest.approx(70)
    assert s.par_outs == pytest.approx(0.5 + 1.5)
    assert s.above_par() == pytest.approx(100 * 5 / 40)


def test_a_slice_the_league_never_reached_adds_no_par():
    s = A._splits([("x", 2010, "death", 5, 3, 0)], {})["x"]["death"]
    assert s.par_runs == 0 and s.balls == 3


def test_batting_totals_count_a_duck_only_when_out_and_a_hundred_is_not_a_fifty():
    bats = [Bat("m1", 1, "x", runs=0, balls=1, out=True), Bat("m2", 1, "x", runs=0, balls=0, out=False),
            Bat("m3", 1, "x", runs=104, balls=60, out=True), Bat("m4", 1, "x", runs=104, balls=55, out=False),
            Bat("m5", 1, "x", runs=50, balls=40, out=True)]
    t = A._bat_totals(bats)
    assert t["ducks"] == 1
    assert (t["hundreds"], t["fifties"]) == (2, 1)
    assert t["hs"] == (104, True)              # the not-out wins a tie on runs
    assert A._hs(t) == "104*"


def test_the_highest_score_is_marked_not_out_only_when_it_was():
    t = A._bat_totals([Bat("m1", 1, "x", runs=104, balls=60, out=True), Bat("m2", 1, "x", runs=90, balls=50, out=False)])
    assert A._hs(t) == "104"
    t = A._bat_totals([Bat("m1", 1, "x", runs=60, balls=40, out=True), Bat("m2", 1, "x", runs=90, balls=50, out=False)])
    assert A._hs(t) == "90*"


def test_best_bowling_breaks_a_tie_on_wickets_by_fewer_runs():
    bowls = [Bowl("m1", 1, "x", balls=24, runs=30, wickets=3), Bowl("m2", 1, "x", balls=24, runs=18, wickets=3),
             Bowl("m3", 1, "x", balls=24, runs=10, wickets=2), Bowl("m4", 1, "x", balls=0, runs=0, wickets=0)]
    t = A._bowl_totals(bowls)
    assert t["best"] == (3, 18)
    assert t["inns"] == 3                      # an innings he never bowled in is not a bowling innings


def test_a_player_url_folds_accents_rather_than_dropping_letters():
    assert A.player_slug("Morné Morkel") == "morne-morkel"
    assert A.player_path("AB de Villiers") == "/almanack/players/ab-de-villiers"


def test_page_marks_noindex_only_when_asked():
    on = A.page("/x", "t", "d", "o", "h", "<p>b</p>", index=True)
    off = A.page("/x", "t", "d", "o", "h", "<p>b</p>", index=False)
    assert 'name="robots"' not in on
    assert off.count('<meta name="robots" content="noindex,follow">') == 1


# --------------------------------------------------------------------------- the committed pages

def all_pages() -> list[Path]:
    return sorted(PLAYERS.glob("*.html"))


def test_a_page_is_indexed_exactly_when_the_player_has_enough_matches():
    """Several hundred near-empty pages is what AdSense calls scaled content, so only careers of
    INDEX_MATCHES or more are indexed -- and every one of those is."""
    assert A.INDEX_MATCHES == 20
    indexed = thin = 0
    for p in all_pages():
        text = p.read_text()
        noindex = 'name="robots" content="noindex' in text
        assert noindex == (matches_on(text) < 20), p.name
        indexed += not noindex
        thin += noindex
    assert indexed > 300 and thin > 400       # Phase 0 measured 343 and 471, named-or-played moved it a little


def test_the_sitemap_lists_exactly_the_indexed_player_pages():
    sitemap = (STATIC / "sitemap.xml").read_text()
    listed = set(re.findall(r"/almanack/players/([a-z0-9-]+)</loc>", sitemap))
    want = {p.stem for p in all_pages() if 'content="noindex' not in p.read_text()}
    assert listed == want
    assert "/almanack</loc>" in sitemap and "/almanack/players</loc>" in sitemap


def test_every_almanack_link_on_any_page_resolves_to_a_page_that_exists():
    stems = {p.stem for p in all_pages()}
    files = [STATIC / "almanack.html", STATIC / "almanack" / "players.html"] + all_pages()
    seen = 0
    for f in files:
        for s in re.findall(r'href="/almanack/players/([a-z0-9-]+)"', f.read_text()):
            assert s in stems, (f.name, s)
            seen += 1
    assert seen > 3000


def test_the_index_lists_every_player_page_once():
    index = (STATIC / "almanack" / "players.html").read_text()
    links = re.findall(r'href="/almanack/players/([a-z0-9-]+)"', index)
    assert sorted(links) == sorted(p.stem for p in all_pages())


def test_routes_are_served_by_the_app_and_by_vercel():
    from web.app import app
    paths = set(app.openapi()["paths"]) | {r.path for r in app.routes if hasattr(r, "path")}
    for p in ("/almanack", "/almanack/players", "/almanack/players/{slug}"):
        assert p in paths, p
    vercel = json.loads((ROOT / "vercel.json").read_text())
    srcs = {r["src"] for r in vercel["routes"]}
    assert {"/almanack", "/almanack/players", "/almanack/players/([a-z0-9-]+)"} <= srcs
    catch_all = next(i for i, r in enumerate(vercel["routes"]) if r["src"] == "/(.*)" and "has" not in r)
    for i, r in enumerate(vercel["routes"]):
        if r["src"].startswith("/almanack"):
            assert i < catch_all, r["src"]        # a route after the catch-all is never reached


def test_the_app_serves_a_player_and_refuses_a_path():
    from fastapi.testclient import TestClient
    from web.app import app
    c = TestClient(app)
    assert c.get("/almanack/players/virat-kohli").status_code == 200
    assert c.get("/almanack/players/nobody-at-all").status_code == 404
    assert c.get("/almanack/players/..%2F..%2Fapp").status_code == 404


PRONOUN = re.compile(r"\b(he|his|him|himself|she|her|hers|herself)\b", re.I)


def test_no_sentence_gives_a_real_player_a_pronoun():
    for f in [STATIC / "almanack.html", STATIC / "almanack" / "players.html"] + all_pages():
        prose = " ".join(re.findall(r"<(?:p|li)[^>]*>(.*?)</(?:p|li)>", f.read_text(), re.S))
        hit = PRONOUN.search(html.unescape(re.sub(r"<[^>]+>", " ", prose)))
        assert not hit, (f.name, hit.group(0))


# --------------------------------------------------------------------------- the record, checked

def test_kohli_2016_reads_as_the_public_record():
    """Check 7's figure, not a neighbouring one (A22): 973 runs off 640 balls, four hundreds,
    highest 113 -- the record season, from the scorecards."""
    row = next(r for r in section_table(page("virat-kohli"), "Batting") if r[0] == "2016")
    assert (row[3], row[4], row[7], row[8]) == ("973", "640", "113", "4")


def test_a_match_counts_when_the_player_was_named_or_took_part():
    """Kohli to 2025: 266 here, against a published 267. The one missing is RCB v SRH on
    25 April 2017, abandoned without a ball and so absent from the archive (A12). Counting only
    the matches he batted or bowled in would read 262."""
    rows = section_table(page("virat-kohli"), "Season by season")
    assert sum(int(r[2]) for r in rows if int(r[0]) <= 2025) == 266


def test_the_front_page_facts_match_the_public_record():
    text = visible((STATIC / "almanack.html").read_text())
    assert "AB de Villiers has been named Player of the Match 25 times" in text
    assert "Chris Gayle's 175* off 66 balls against Pune Warriors on 23 April 2013" in text
    assert "Alzarri Joseph's 6/12 against Sunrisers Hyderabad on 6 April 2019" in text
    assert "Chris Gayle has hit 357 sixes" in text


def test_phase_runs_add_up_to_the_career_from_a_different_query():
    """The phase table comes from a phase GROUP BY; the career line from the per-innings
    scorecard rows. Where all three phases are printed they must sum to the career exactly."""
    checked = looses = 0
    for p in all_pages():
        text = p.read_text()
        if ">Against the field</h2>" not in text or ">Batting</h2>" not in text:
            continue
        part = text.split(">Against the field</h2>", 1)[1]
        if ">Batting</h3>" not in part:
            continue
        rows = section_table(part, "Batting")
        phases = [r for r in rows if r[0].split(" (")[0] in ("Powerplay", "Middle", "Death")]
        if len(phases) != 3:
            continue
        career = next(r for r in section_table(text, "Batting") if r[0] == "Career")
        loose = re.search(r'class="hx-note al-loose">([\d,]+) runs? off ([\d,]+) balls? batting', part)
        lr, lb = (num(loose.group(1)), num(loose.group(2))) if loose else (0, 0)
        assert sum(num(r[1]) for r in phases) + lr == num(career[3]), p.name
        assert sum(num(r[2]) for r in phases) + lb == num(career[4]), p.name
        looses += bool(loose)
        checked += 1
    assert checked > 100
    assert looses >= 5                       # the A17 innings do reach some pages: the rule acts


def test_every_season_row_names_a_franchise_the_player_has_a_season_page_for():
    rows = section_table(page("aaron-finch"), "Season by season")
    teams = {r[1] for r in rows}
    assert len(teams) >= 9                    # Finch's nine franchises, each a row
