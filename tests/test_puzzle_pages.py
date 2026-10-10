"""The puzzle pages' ranking wiring (A188): what the browser sends is what the API accepts.

The pages are plain scripts with no test harness of their own, so a typo in a field name or a
route would fail only for a signed-in player on a finished daily -- silently, as a rank panel that
never appears. These read the scripts and compare them with the server's own definitions.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "web" / "static"

GAMES = {"bingo": ("bingo.html", "bingo.js"), "guess": ("guess.html", "guess.js"),
         "xi": ("xi.html", "xi.js")}


def test_every_game_page_has_a_slot_for_the_rank_and_the_board():
    for page in ("bingo.html", "guess.html", "xi.html", "commonground.html"):
        html = (STATIC / page).read_text()
        assert 'id="pzRank"' in html and 'id="pzBoard"' in html, page


def test_the_games_with_picks_have_a_slot_for_rarity_and_guess_the_player_does_not():
    for page in ("bingo.html", "xi.html", "commonground.html"):
        assert 'id="pzRarity"' in (STATIC / page).read_text(), page
    assert 'id="pzRarity"' not in (STATIC / "guess.html").read_text()
    assert "if (game === 'guess') return null;" in (STATIC / "puzzle.js").read_text()


def test_a_signed_out_finish_sends_the_picks_route_only_fields_it_accepts():
    from web.puzzle_routes import PicksIn
    for js in ("bingo.js", "xi.js"):
        call = re.search(r"pzRankDaily\('\w+', \{(.*?)\}, [\w.]+\)", (STATIC / js).read_text(), re.S).group(1)
        top = re.sub(r"\(\{[^}]*\}\)", "", call)
        assert set(re.findall(r"\b([a-z_]+):", top)) <= set(PicksIn.model_fields), js
    cg = re.search(r"pzCountPicks\('common', \{(.*?)\}\)", (STATIC / "commonground.js").read_text(), re.S).group(1)
    assert set(re.findall(r"\b([a-z_]+):", cg)) <= set(PicksIn.model_fields)
    assert "X-Daily-Device" in (STATIC / "puzzle.js").read_text()


@pytest.mark.parametrize("game", sorted(GAMES))
def test_each_untimed_game_submits_only_fields_the_api_accepts(game):
    from web.puzzle_routes import SubmitIn
    js = (STATIC / GAMES[game][1]).read_text()
    call = re.search(rf"pzRankDaily\('{game}', \{{(.*?)\}}, [\w.]+\)", js, re.S)
    assert call, f"{game} never submits its result"
    top = re.sub(r"\(\{[^}]*\}\)", "", call.group(1))        # Bingo's per-cell objects are CellGuess's fields
    fields = set(re.findall(r"\b([a-z_]+):", top))
    assert fields <= set(SubmitIn.model_fields), fields - set(SubmitIn.model_fields)
    assert "seed" in fields
    assert "score" not in fields and "points" not in fields       # the page never says how it did


def test_the_submitted_game_names_are_the_ones_the_table_accepts():
    from game.puzzle_results import GAMES as TABLE
    for game, (_, js) in GAMES.items():
        assert game in TABLE
        assert f"pzRankDaily('{game}'" in (STATIC / js).read_text()


def test_the_hub_tabs_are_exactly_the_boards_the_api_serves():
    from web.puzzle_routes import GAME_LABELS
    hub = (STATIC / "puzzles.html").read_text()
    tabs = re.findall(r'data-game="(\w+)" onclick="pzHubBoard', hub)
    assert tabs == ["overall", "bingo", "guess", "common", "xi"]
    assert set(tabs) == set(GAME_LABELS)


def test_the_shared_script_names_the_games_the_same_way_the_api_does():
    from web.puzzle_routes import GAME_LABELS
    js = (STATIC / "puzzle.js").read_text()
    names = dict(re.findall(r"(\w+): '([^']+)'", re.search(r"const PZ_GAME_NAMES = \{(.*?)\}", js, re.S).group(1)))
    assert names == GAME_LABELS


def test_every_route_the_browser_calls_for_ranking_exists():
    from web.app import app
    # `app.routes` hides included routers in this FastAPI; the schema lists every path.
    served = set(app.openapi()["paths"])
    js = "".join((STATIC / f).read_text() for f in ("puzzle.js", "commonground.js", "puzzles.js"))
    called = set(re.findall(r"['`](/api/(?:puzzles|ground)/[a-z/]+)", js))
    assert {"/api/puzzles/submit", "/api/puzzles/picks", "/api/ground/ranked", "/api/ground/ranked/start"} <= called
    for path in called:
        assert path.rstrip("/") in served or path.rstrip("/") + "/{game}" in served, path


def test_common_ground_uses_the_servers_clock_only_when_signed_in_on_the_daily():
    js = (STATIC / "commonground.js").read_text()
    assert "CG.ranked = CG.mode === 'daily' && CG.signedIn && !CG.peeked;" in js
    assert "/api/ground/ranked/start" in js and "/api/ground/ranked/play" in js
    # a signed-out finish blocks a ranked second look: the answers were on screen
    assert "cgSave({anonDone: p.date})" in js and "cgStore().anonDone === puzzle.date" in js
