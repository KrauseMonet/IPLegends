"""Bingo (A182): the facts, the grid generator, the rules, the share line and the routes.

Runs on the two committed snapshots, no database. Where a figure could be wrong in a way
every internal check would agree with, it is pinned to the public record instead -- A22's
lesson, that the database is perfectly consistent with a misreading of cricket's own
conventions and only a person checking a scorecard can tell.
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from etl.franchise_map import canonical
from game import bingo, puzzle_facts
from tools import snapshot_deck
from web import bingo_routes

DOC = puzzle_facts.read_document()
DECK_DOC = snapshot_deck.read_document()
pytestmark = pytest.mark.skipif(DOC is None or DECK_DOC is None,
                                reason="puzzle facts / deck snapshot not committed")

SEEDS = range(1, 301)


@pytest.fixture(scope="module")
def facts():
    return bingo.Facts(puzzle_facts.players_from(DOC))


@pytest.fixture(scope="module")
def deck():
    return snapshot_deck.deck_from(DECK_DOC)


@pytest.fixture(scope="module")
def grids(facts):
    return [bingo.make_grid(facts, s) for s in SEEDS]


def by_name(facts, name):
    (p,) = [p for p in facts.players.values() if p.name == name]
    return p


# --- the facts ----------------------------------------------------------------------------

def test_franchises_are_the_fifteen_canonical_ones(facts):
    """Counting Delhi Daredevils and Delhi Capitals as two clubs cost 15 points of playable
    grids when it was measured, and would refuse a man who played for the same club."""
    clubs = {f for p in facts.players.values() for f in p.franchises}
    assert len(clubs) == 15
    assert clubs == {canonical(f) for f in clubs}


def test_figures_match_the_public_record(facts):
    """Checked against what the record says, not against another reading of our own
    database: Gayle's six IPL hundreds and 357 sixes, Malinga's 170 wickets, Warner's four
    hundreds. A wrong innings rule (counting a super over, splitting a match in two) moves
    these and no internal consistency check would notice."""
    gayle = by_name(facts, "CH Gayle")
    assert (gayle.hundreds, gayle.sixes) == (6, 357)
    assert by_name(facts, "SL Malinga").wickets == 170
    assert by_name(facts, "DA Warner").hundreds == 4


def test_franchise_membership_is_the_decks_own(facts, deck):
    """Re-derived from the deck's cards, a different route from the one that built the
    facts, so a wrong canonical mapping or a dropped card is seen."""
    expected: dict[str, set[str]] = {}
    for cards in deck.cards_by_fs.values():
        for c in cards:
            expected.setdefault(c.person_id, set()).add(canonical(c.franchise))
    assert set(expected) == set(facts.players)
    for pid, clubs in expected.items():
        assert set(facts.players[pid].franchises) == clubs


def test_counts_are_internally_ordered(facts):
    for p in facts.players.values():
        assert p.fifties >= p.hundreds
        assert p.best_season_runs <= p.runs
        assert p.best_season_wickets <= p.wickets
        assert p.seasons and p.franchises


def test_a_mid_season_move_sums_into_one_season():
    """A man traded mid-season has two cards for one season, and his season total is the
    SUM. No player has that shape in today's archive (measured: zero), so this runs on a
    synthetic deck -- on the real one the rule is inert and a test of it could not fail."""
    from etl.feasibility import Card, Deck

    class NoDeliveries:
        def execute(self, sql):
            return []

    def card(fs, club, runs, wkts):
        return Card(fs_id=fs, person_id="p1", name="P One", franchise=club, season_year=2014,
                    bat_runs=runs, bowl_wickets=wkts, overseas=False, keeper_eligible=False)

    deck = Deck(cards_by_fs={1: [card(1, "Mumbai Indians", 200, 3)],
                             2: [card(2, "Chennai Super Kings", 250, 4)]}, fs_ids=[1, 2])
    (p,) = puzzle_facts.build_players(NoDeliveries(), deck)
    assert p.best_season_runs == 450 and p.best_season_wickets == 7
    assert p.runs == 450 and p.seasons == (2014,)
    assert p.franchises == ("Chennai Super Kings", "Mumbai Indians")


# --- the generator ------------------------------------------------------------------------

def test_a_seed_is_the_grid(facts):
    a, b = bingo.make_grid(facts, 4242), bingo.make_grid(facts, 4242)
    assert [c.key for c in a.rows] == [c.key for c in b.rows]
    assert [c.key for c in a.cols] == [c.key for c in b.cols]
    assert a.cells == b.cells
    assert any(bingo.make_grid(facts, s).cells != a.cells for s in range(1, 20))


def test_the_grid_does_not_depend_on_the_process(facts):
    """`hash()` is salted per process in CPython, so a day derived from it would differ
    between two servers. Run the generator under different hash seeds and compare; an
    in-process assertion cannot see this, which is why it is a subprocess (A125)."""
    code = ("import sys; sys.path.insert(0, '.');"
            "from game import bingo, puzzle_facts as pf;"
            "f = bingo.Facts(pf.players_from(pf.read_document()));"
            "g = bingo.make_grid(f, 739898);"
            "print([c.key for c in g.rows], [c.key for c in g.cols],"
            " [sorted(c) for c in g.cells])")
    outs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env={**os.environ, "PYTHONHASHSEED": h}, check=True).stdout
            for h in ("1", "2", "3")}
    assert len(outs) == 1


def test_every_grid_is_playable(grids):
    for g in grids:
        sizes = [len(c) for c in g.cells]
        assert min(sizes) >= bingo.MIN_ANSWERS
        assert sum(s <= bingo.HARD_CELL for s in sizes) <= bingo.MAX_HARD_CELLS


def test_axes_are_chosen_by_the_rules(facts, grids):
    for g in grids:
        assert all(c.kind == "franchise" and len(facts.members(c)) >= bingo.ROW_MIN_PLAYERS
                   for c in g.rows)
        assert not set(g.rows) & set(g.cols)
        for c in g.cols:
            assert c.kind == "stat" or len(facts.members(c)) >= bingo.COL_MIN_PLAYERS
        groups = [c.group for c in g.cols if c.group]
        assert len(groups) == len(set(groups))        # Overseas and Indian never together


def test_a_cell_is_exactly_the_players_who_fit_both(facts, grids):
    """Each cell re-derived from the players themselves, one at a time."""
    for g in grids[:60]:
        for i, cell in enumerate(g.cells):
            r, c = g.rows[i // 3], g.cols[i % 3]
            assert cell == {p.person_id for p in facts.players.values() if r.test(p) and c.test(p)}


def test_the_daily_seed_is_the_date_and_practice_seeds_never_collide_with_it():
    assert bingo.daily_seed(datetime.date(2026, 10, 9)) == datetime.date(2026, 10, 9).toordinal()
    assert bingo.daily_seed(datetime.date(2100, 1, 1)) < bingo.PRACTICE_SEED_FLOOR
    assert all(bingo.new_seed() >= bingo.PRACTICE_SEED_FLOOR for _ in range(200))


# --- the rules ----------------------------------------------------------------------------

@pytest.fixture
def grid(facts):
    return bingo.make_grid(facts, 739898)


def wrong_for(grid, facts, cell):
    return next(p for p in sorted(facts.players) if p not in grid.cells[cell])


def test_a_right_guess_fills_the_cell_and_scores(grid, facts):
    st = bingo.State()
    pid = sorted(grid.cells[0])[0]
    assert bingo.guess(grid, facts, st, 0, pid) is True
    assert st.placed == {0: pid} and st.score == grid.points(0) and st.guesses_left == 8


def test_a_wrong_guess_costs_a_guess_and_scores_nothing(grid, facts):
    st = bingo.State()
    assert bingo.guess(grid, facts, st, 0, wrong_for(grid, facts, 0)) is False
    assert st.guesses_left == 8 and st.score == 0 and not st.placed


def test_a_player_can_fill_only_one_cell(grid, facts):
    both = sorted(grid.cells[0] & grid.cells[1])
    if not both:
        pytest.skip("this grid has no player who fits two cells")
    st = bingo.State()
    bingo.guess(grid, facts, st, 0, both[0])
    with pytest.raises(bingo.BingoError, match="already on the grid"):
        bingo.guess(grid, facts, st, 1, both[0])


def test_a_player_who_fits_two_cells_is_refused_on_the_second_on_a_real_grid(facts):
    """The test above skips when the grid has no overlap; this one finds a grid that does,
    so the used-once rule is exercised and not merely allowed to be vacuous."""
    for seed in range(1, 100):
        g = bingo.make_grid(facts, seed)
        for a in range(9):
            for b in range(a + 1, 9):
                both = sorted(g.cells[a] & g.cells[b])
                if both:
                    st = bingo.State()
                    bingo.guess(g, facts, st, a, both[0])
                    with pytest.raises(bingo.BingoError):
                        bingo.guess(g, facts, st, b, both[0])
                    return
    pytest.fail("no grid in 100 had one player fitting two cells")


def test_a_filled_cell_is_closed(grid, facts):
    st = bingo.State()
    a, b = sorted(grid.cells[0])[:2]
    bingo.guess(grid, facts, st, 0, a)
    with pytest.raises(bingo.BingoError, match="already filled"):
        bingo.guess(grid, facts, st, 0, b)


def test_the_same_wrong_guess_twice_is_refused_and_free(grid, facts):
    st = bingo.State()
    w = wrong_for(grid, facts, 0)
    bingo.guess(grid, facts, st, 0, w)
    with pytest.raises(bingo.BingoError, match="already tried"):
        bingo.guess(grid, facts, st, 0, w)
    assert st.guesses_left == 8


def test_a_refused_move_changes_nothing(grid, facts):
    st = bingo.State()
    for cell, pid in ((9, "x"), (0, "not-a-person")):
        before = (dict(st.placed), list(st.wrong), st.score)
        with pytest.raises(bingo.BingoError):
            bingo.guess(grid, facts, st, cell, pid)
        assert (st.placed, st.wrong, st.score) == before


def test_nine_guesses_end_the_game(grid, facts):
    st = bingo.State()
    losers = [p for p in sorted(facts.players) if p not in grid.cells[0]][:bingo.GUESSES]
    for i, pid in enumerate(losers):
        assert not st.finished
        bingo.guess(grid, facts, st, 0, pid)
    assert st.finished and st.guesses_left == 0
    with pytest.raises(bingo.BingoError, match="finished"):
        bingo.guess(grid, facts, st, 1, sorted(grid.cells[1])[0])


def test_filling_all_nine_ends_the_game_early(facts):
    for seed in range(1, 200):
        g = bingo.make_grid(facts, seed)
        # a one-to-one assignment of a distinct player to each cell, if one exists
        taken, plan = set(), {}
        for cell in sorted(range(9), key=lambda c: len(g.cells[c])):
            pick = next((p for p in sorted(g.cells[cell]) if p not in taken), None)
            if pick is None:
                break
            taken.add(pick); plan[cell] = pick
        if len(plan) == 9:
            st = bingo.State()
            for cell, pid in plan.items():
                bingo.guess(g, facts, st, cell, pid)
            assert st.finished and st.guesses_left == bingo.GUESSES - 9
            return
    pytest.fail("no grid in 200 could be filled completely")


def test_replay_equals_playing_the_moves_one_by_one(grid, facts):
    moves = [(0, wrong_for(grid, facts, 0)), (0, sorted(grid.cells[0])[0]),
             (4, sorted(grid.cells[4])[0])]
    st = bingo.State()
    for m in moves:
        bingo.guess(grid, facts, st, *m)
    assert bingo.replay(grid, facts, moves) == st


# --- scoring ------------------------------------------------------------------------------

def test_harder_cells_score_more(grids):
    pts = sorted({(len(g.cells[i]), g.points(i)) for g in grids for i in range(9)})
    sizes = [s for s, _ in pts]
    points = [p for _, p in pts]
    assert sizes == sorted(sizes)
    assert points == sorted(points, reverse=True)       # fewer answers, more points
    assert bingo.Grid(0, (), (), (frozenset("abc"),) * 9).points(0) == 58   # 100/sqrt(3)


# --- sharing ------------------------------------------------------------------------------

def test_the_share_line_shows_the_shape_and_names_nobody(grid, facts):
    st = bingo.State()
    for cell in (0, 4):
        bingo.guess(grid, facts, st, cell, sorted(grid.cells[cell])[0])
    text = bingo.share_text(grid, st, "9 Oct 2026", daily=True)
    rows = text.splitlines()
    assert rows[0] == "Almanack Bingo · 9 Oct 2026"
    assert sum(r.count("⬛") for r in rows[1:4]) == 7
    assert sum(r.count("🟩") + r.count("🟪") for r in rows[1:4]) == 2
    assert rows[4] == f"2/9 · {st.score} pts"
    assert rows[5] == "iplegends.vercel.app/bingo"
    for p in facts.players.values():
        assert p.name not in text                    # a spoiler is the whole failure here


def test_a_rare_cell_is_marked_and_an_ordinary_one_is_not(facts):
    for seed in range(1, 400):
        g = bingo.make_grid(facts, seed)
        rare = [i for i in range(9) if g.is_rare(i)]
        common = [i for i in range(9) if not g.is_rare(i)]
        if rare and common:
            st = bingo.State()
            bingo.guess(g, facts, st, rare[0], sorted(g.cells[rare[0]])[0])
            bingo.guess(g, facts, st, common[0], sorted(g.cells[common[0]] - set(st.placed.values()))[0])
            text = bingo.share_text(g, st, "x", daily=False)
            assert "🟪" in text and "🟩" in text
            assert text.endswith(f"?seed={g.seed}")
            return
    pytest.fail("no grid with both a rare and a common cell")


def test_reveal_counts_every_cell_and_lists_the_best_known_first(grid, facts):
    out = bingo.reveal(grid, facts, per_cell=3)
    assert [x["n"] for x in out] == [len(c) for c in grid.cells]
    best = max((facts.players[p] for p in grid.cells[8]), key=lambda p: (p.prominence, p.name))
    assert out[8]["sample"][0] == facts.players[
        sorted(grid.cells[8], key=lambda p: (-facts.players[p].prominence, facts.players[p].name, p))[0]].name
    assert best.prominence >= 0


# --- the routes ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(bingo_routes, "today", lambda: datetime.date(2026, 10, 9))
    app = FastAPI()
    app.include_router(bingo_routes.router)
    return TestClient(app)


def test_today_is_the_daily_grid_and_carries_no_answers(client):
    r = client.get("/api/bingo/today")
    assert r.status_code == 200
    d = r.json()
    assert d["daily"] and d["date"] == "2026-10-09" and d["seed"] == datetime.date(2026, 10, 9).toordinal()
    assert len(d["rows"]) == len(d["cols"]) == 3 and d["guesses"] == bingo.GUESSES
    assert "cells" not in d and "person" not in r.text      # nothing that names a player


def test_a_seeded_grid_is_cacheable_and_a_fresh_one_is_not(client):
    assert "s-maxage" in client.get("/api/bingo?seed=10000042").headers["cache-control"]
    fresh = client.get("/api/bingo")
    assert fresh.headers["cache-control"] == "no-store"
    assert fresh.json()["seed"] >= bingo.PRACTICE_SEED_FLOOR and not fresh.json()["daily"]


def test_play_replays_and_answers(client, facts):
    seed = 739898
    g = bingo.make_grid(facts, seed)
    w, right = wrong_for(g, facts, 0), sorted(g.cells[0])[0]
    first = client.post("/api/bingo/play", json={"seed": seed, "guess": [0, w]}).json()
    assert first["correct"] is False and first["state"]["guesses_left"] == 8
    second = client.post("/api/bingo/play",
                         json={"seed": seed, "guesses": [[0, w]], "guess": [0, right]}).json()
    assert second["correct"] is True
    st = second["state"]
    assert st["placed"][0]["cell"] == 0 and st["placed"][0]["answers"] == len(g.cells[0])
    assert st["score"] == g.points(0) and st["guesses_left"] == 7 and not st["finished"]
    replay = client.post("/api/bingo/play", json={"seed": seed, "guesses": [[0, w], [0, right]]}).json()
    assert replay["correct"] is None and replay["state"] == st


def test_a_move_the_rules_refuse_is_a_400_with_a_reason(client, facts):
    g = bingo.make_grid(facts, 739898)
    right = sorted(g.cells[0])[0]
    r = client.post("/api/bingo/play", json={"seed": 739898, "guesses": [[0, right]], "guess": [0, right]})
    assert r.status_code == 400 and "filled" in r.json()["detail"]
    r = client.post("/api/bingo/play", json={"seed": 739898, "guess": [0, "nobody"]})
    assert r.status_code == 400


def test_more_than_nine_guesses_are_rejected_outright(client, facts):
    ids = sorted(facts.players)[:10]
    r = client.post("/api/bingo/play", json={"seed": 739898, "guesses": [[0, p] for p in ids]})
    assert r.status_code == 422


def test_the_finished_state_carries_the_share_text(client, facts):
    g = bingo.make_grid(facts, 739898)
    losers = [p for p in sorted(facts.players) if p not in g.cells[0]][:9]
    r = client.post("/api/bingo/play", json={"seed": 739898, "guesses": [[0, p] for p in losers[:8]],
                                             "guess": [0, losers[8]]}).json()
    assert r["state"]["finished"] and r["state"]["share"].startswith("Almanack Bingo · 9 Oct 2026")


def test_players_list_names_everybody_and_hints_at_nothing(client, facts):
    d = client.get("/api/puzzles/players").json()
    assert len(d) == len(facts.players)
    assert set(d[0]) == {"id", "name"}


def test_answers_route_matches_the_grid(client, facts):
    g = bingo.make_grid(facts, 739898)
    d = client.get("/api/bingo/answers?seed=739898").json()
    assert [x["n"] for x in d] == [len(c) for c in g.cells]


def test_a_missing_snapshot_is_a_503_not_a_crash(client, monkeypatch):
    monkeypatch.setattr(bingo_routes, "_facts", lambda: None)
    assert client.get("/api/bingo/today").status_code == 503
