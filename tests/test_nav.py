"""The site's navigation (A186): the two-hero home page, the Puzzles hub and the puzzle bar.

Navigation is where a silent mistake is cheapest to make and slowest to notice: a link to a page
that does not exist, a puzzle that the bar forgot, a hub card that reads the wrong saved key and
says "Play" forever. Each of those is a test here.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "web" / "static"
PARTIALS = ROOT / "web" / "partials"

PUZZLE_PAGES = {"puzzles": "/puzzles", "bingo": "/bingo", "guess": "/guess", "commonground": "/common",
                "xi": "/xi", "flashback": "/flashback"}
DAILY_KEYS = {"/bingo": ("bingo.js", "BG_KEY"), "/guess": ("guess.js", "GP_KEY"),
              "/common": ("commonground.js", "CG_KEY"), "/xi": ("xi.js", "XI_KEY")}


def hrefs(text: str) -> list[str]:
    return re.findall(r'href="([^"#][^"]*)"', text)


def internal(urls):
    return [u for u in urls if u.startswith("/") and not u.startswith("/static/")]


def served_paths() -> set[str]:
    """Every page path the site answers: the app's own routes and vercel.json's."""
    from web.app import app
    paths = {getattr(r, "path", "") for r in app.routes}
    routes = json.loads((ROOT / "vercel.json").read_text())["routes"]
    return paths | {r["src"] for r in routes}


def test_every_link_in_the_shared_bars_goes_somewhere_real():
    paths = served_paths()
    for name in ("nav", "puzzlenav", "footer"):
        for url in internal(hrefs((PARTIALS / f"{name}.html").read_text())):
            assert url in paths, f"{name}.html links to {url}, which nothing serves"


def test_every_puzzle_page_is_served_by_the_app_and_by_vercel():
    app_paths = {getattr(r, "path", "") for r in __import__("web.app", fromlist=["app"]).app.routes}
    vercel = {r["src"] for r in json.loads((ROOT / "vercel.json").read_text())["routes"]}
    for path in PUZZLE_PAGES.values():
        assert path in app_paths, path
        assert path in vercel, path


def test_the_top_bar_keeps_draft_and_auction_first_and_does_not_grow():
    links = re.findall(r'<a href="([^"]+)" data-match="([^"]+)">([^<]+)</a>',
                       (PARTIALS / "nav.html").read_text())
    assert [text for _, _, text in links][:2] == ["Draft", "Auction"]       # the two heroes lead
    assert len(links) <= 6
    assert "Flashback" not in [text for _, _, text in links]               # it lives under Puzzles
    puzzles = [m for url, m, text in links if text == "Puzzles"]
    assert puzzles and all(p in puzzles[0].split() for p in PUZZLE_PAGES.values())


def test_every_puzzle_page_carries_the_puzzle_bar_and_no_other_page_does():
    for page in sorted(STATIC.glob("*.html")):
        has = "<!-- shell:puzzlenav -->" in page.read_text()
        assert has == (page.stem in PUZZLE_PAGES), page.name


def test_the_puzzle_bar_lists_every_puzzle_once():
    urls = hrefs((PARTIALS / "puzzlenav.html").read_text())
    assert sorted(urls) == sorted(PUZZLE_PAGES.values())


def test_the_home_page_has_two_heroes_and_one_quiet_puzzle_strip():
    html = (STATIC / "index.html").read_text()
    heroes = re.findall(r'<a class="mode big" href="([^"]+)"', html)
    assert heroes == ["#", "/auction"]                                       # Draft, then Auction
    assert 'onclick="newDraft(null)' in html.split('class="modes more"')[0]
    second = html.split('class="modes more"')[1].split("puzzle-strip")[0]
    assert sorted(re.findall(r'<a class="mode" href="([^"]+)"', second)) == ["/daily", "/rooms"]
    assert 'class="puzzle-strip" href="/puzzles"' in html
    assert not re.search(r'<a class="mode[^"]*" href="/(flashback|bingo|guess|common|xi)"', html)


@pytest.mark.parametrize("path,key_file", [(p, v) for p, v in DAILY_KEYS.items()])
def test_each_hub_card_reads_the_key_its_game_actually_saves_under(path, key_file):
    """The hub only reads saved days; a card whose key is a typo would say 'Play' for ever."""
    js_name, const = key_file
    game_js = (STATIC / js_name).read_text()
    saved_key = re.search(rf"const {const} = '([^']+)'", game_js).group(1)
    hub = (STATIC / "puzzles.html").read_text()
    card = re.search(rf'<a class="pz-card" href="{path}" data-key="([^"]+)"', hub)
    assert card and card.group(1) == saved_key


def test_the_hub_lists_the_four_dailies_and_flashback_and_nothing_else():
    hub = (STATIC / "puzzles.html").read_text()
    cards = re.findall(r'<a class="pz-card" href="([^"]+)"', hub)
    assert cards == ["/bingo", "/guess", "/common", "/xi", "/flashback"]


def test_every_daily_game_records_a_finished_day_the_hub_can_count():
    """The hub counts a day if the game's saved `days` list holds it, so each game must write
    that list when a puzzle finishes."""
    for js_name, const in (("bingo.js", "BG_KEY"), ("guess.js", "GP_KEY"),
                           ("commonground.js", "CG_KEY"), ("xi.js", "XI_KEY")):
        text = (STATIC / js_name).read_text()
        assert re.search(r"Save\(\{days: days\.concat\(", text), js_name


# --- the matcher that decides which bar item is lit --------------------------------------------

def nav_matches(spec: str, path: str) -> bool:
    src = (STATIC / "common.js").read_text()
    body = re.search(r"function navMatches\(spec, path\)\{[\s\S]*?\n\}", src).group(0)
    out = subprocess.run(["node", "-e", f"{body}; console.log(navMatches({json.dumps(spec)}, {json.dumps(path)}))"],
                         capture_output=True, text=True, check=True).stdout.strip()
    return out == "true"


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@needs_node
def test_the_top_bar_lights_puzzles_on_every_puzzle_page_and_only_there():
    links = re.findall(r'data-match="([^"]+)">([^<]+)</a>', (PARTIALS / "nav.html").read_text())
    spec = {text: m for m, text in links}["Puzzles"]
    for path in PUZZLE_PAGES.values():
        assert nav_matches(spec, path), path
    for path in ("/", "/draft", "/season", "/auction", "/rooms", "/daily", "/records", "/profile"):
        assert not nav_matches(spec, path), path


@needs_node
def test_exactly_one_item_of_the_puzzle_bar_is_lit_on_each_puzzle_page():
    items = re.findall(r'data-match="([^"]+)"', (PARTIALS / "puzzlenav.html").read_text())
    for path in PUZZLE_PAGES.values():
        assert sum(nav_matches(m, path) for m in items) == 1, path


@needs_node
def test_a_path_that_merely_starts_with_a_pages_name_does_not_light_it():
    assert nav_matches("/xi", "/xi") and nav_matches("/xi", "/xi/anything")
    assert not nav_matches("/xi", "/xiaomi") and not nav_matches("/guess", "/guessing")
