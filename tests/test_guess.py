"""Guess the Player (A183): the tiles, the pool, the daily walk, the rules, the share line and
the routes. Runs on the committed facts snapshot, no database.

The tile rules are exercised on hand-built players, where the right answer is obvious by
construction, rather than on the real archive, where a wrong boundary could hide inside a
figure nobody remembers.
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from game import guess as G
from game import puzzle_facts
from web import bingo_routes, guess_routes

DOC = puzzle_facts.read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="puzzle facts not committed")


@pytest.fixture(scope="module")
def players():
    return {p.person_id: p for p in puzzle_facts.players_from(DOC)}


def pf(pid="x", name="X", franchises=("Mumbai Indians",), seasons=(2010, 2015), runs=1000,
       wickets=50, role="batter", country="India", style="pace", **kw) -> puzzle_facts.PlayerFacts:
    base = dict(person_id=pid, name=name, franchises=franchises, seasons=seasons,
                overseas=country != "India", keeper=False, runs=runs, wickets=wickets,
                best_season_runs=runs, best_season_wickets=wickets, hundreds=0, fifties=0,
                four_wicket_hauls=0, sixes=0, role=role, country=country, bowling_style=style)
    base.update(kw)
    return puzzle_facts.PlayerFacts(**base)


def tile(guess, answer, key):
    return {t.key: t for t in G.compare(guess, answer)}[key]


# --- the facts the game reads ----------------------------------------------------------------

def test_every_player_has_the_fields_the_tiles_need(players):
    for p in players.values():
        assert p.role in {"batter", "bowler", "allrounder", "keeper"}
        assert p.country
        assert p.bowling_style in {"pace", "spin", None}


def test_figures_match_the_public_record(players):
    """Pinned to what the record says, only where it is certain: Kohli debuted in 2008 and
    has played for one club; Dhoni kept wicket for Chennai and the two Rising Pune seasons;
    Malinga is a Sri Lankan fast bowler. (Not Malinga's debut year: whether he played the
    first season is exactly the sort of figure that is easy to claim and wrong.)"""
    by = {p.name: p for p in players.values()}
    assert by["V Kohli"].seasons[0] == 2008 and by["V Kohli"].franchises == ("Royal Challengers Bengaluru",)
    assert by["MS Dhoni"].role == "keeper"
    assert set(by["MS Dhoni"].franchises) == {"Chennai Super Kings", "Rising Pune Supergiant"}
    assert (by["SL Malinga"].country, by["SL Malinga"].bowling_style) == ("Sri Lanka", "pace")


def test_career_role_is_the_most_common_season_role_with_a_fixed_tie_break():
    class Fake:
        def __init__(self, rows): self.rows = rows
        def execute(self, sql): return self.rows
    rows = [("a", "batter", 3), ("a", "bowler", 2), ("b", "batter", 2), ("b", "allrounder", 2),
            ("c", "keeper", 2), ("c", "batter", 2), ("d", "bowler", 4)]
    out = puzzle_facts._career_roles(Fake(rows))
    assert out == {"a": "batter", "b": "allrounder", "c": "keeper", "d": "bowler"}
    assert out == puzzle_facts._career_roles(Fake(list(reversed(rows))))     # order of rows irrelevant


# --- the tiles ---------------------------------------------------------------------------------

def test_teams_match_when_identical_close_when_shared_miss_when_disjoint():
    a = pf(franchises=("Chennai Super Kings", "Mumbai Indians"))
    assert tile(pf(franchises=("Mumbai Indians", "Chennai Super Kings")), a, "teams").status == G.MATCH
    t = tile(pf(franchises=("Chennai Super Kings", "Delhi Capitals")), a, "teams")
    assert t.status == G.CLOSE and t.clubs == (("Chennai Super Kings", True), ("Delhi Capitals", False))
    assert tile(pf(franchises=("Delhi Capitals",)), a, "teams").status == G.MISS


@pytest.mark.parametrize("key,field,good,bad", [
    ("role", "role", "bowler", "batter"), ("country", "country", "England", "India")])
def test_a_categorical_tile_matches_or_misses(key, field, good, bad):
    answer = pf(**{field: good})
    assert tile(pf(**{field: good}), answer, key).status == G.MATCH
    assert tile(pf(**{field: bad}), answer, key).status == G.MISS


def test_an_unknown_value_is_never_a_clue():
    """A guess or an answer the archive cannot classify says UNKNOWN: it must not read as a
    match (a claim) or a miss (also a claim). A23."""
    for field in ("role", "country"):
        key = field
        for g, a in ((pf(**{field: None}), pf()), (pf(), pf(**{field: None})),
                     (pf(**{field: None}), pf(**{field: None}))):
            t = tile(g, a, key)
            assert t.status == G.UNKNOWN and t.value == "Unknown"


def test_bowling_none_means_does_not_bowl_and_two_non_bowlers_match():
    assert tile(pf(style=None), pf(style=None), "bowling").status == G.MATCH
    assert tile(pf(style=None), pf(style="spin"), "bowling").status == G.MISS
    assert tile(pf(style="spin"), pf(style="spin"), "bowling").status == G.MATCH
    assert tile(pf(style=None), pf(style=None), "bowling").value == "Doesn't bowl"


def test_a_number_tile_points_toward_the_answer():
    """'up' means the MYSTERY player's number is higher than the guess's."""
    answer = pf(seasons=(2012, 2020))
    low = tile(pf(seasons=(2008, 2016)), answer, "debut")
    high = tile(pf(seasons=(2016, 2022)), answer, "debut")
    assert low.arrow == "up" and high.arrow == "down"
    assert tile(pf(seasons=(2012, 2016)), answer, "debut").arrow is None


def test_close_years_boundary_is_inclusive():
    answer = pf(seasons=(2012, 2020))
    for year, status in ((2012, G.MATCH), (2014, G.CLOSE), (2010, G.CLOSE),
                         (2015, G.MISS), (2009, G.MISS)):
        assert tile(pf(seasons=(year, 2020)), answer, "debut").status == status, year


def test_the_last_season_of_a_current_player_reads_active():
    assert tile(pf(seasons=(2010, 2026)), pf(), "last").value == "Active"
    assert tile(pf(seasons=(2010, 2024)), pf(), "last").value == "2024"


def test_runs_closeness_is_a_quarter_of_the_answer_but_never_under_the_floor():
    answer = pf(runs=4000)                         # a quarter is 1,000
    assert tile(pf(runs=3000), answer, "runs").status == G.CLOSE
    assert tile(pf(runs=2999), answer, "runs").status == G.MISS
    small = pf(runs=60)                            # a quarter is 15, but the floor is 100
    assert tile(pf(runs=150), small, "runs").status == G.CLOSE
    assert tile(pf(runs=161), small, "runs").status == G.MISS


def test_wickets_closeness_has_its_own_floor():
    answer = pf(wickets=4)
    assert tile(pf(wickets=14), answer, "wickets").status == G.CLOSE
    assert tile(pf(wickets=15), answer, "wickets").status == G.MISS


def test_a_player_compared_with_himself_is_all_green():
    for p in (pf(), pf(role=None, country=None, style=None)):
        tiles = G.compare(p, p)
        assert len(tiles) == 8 and all(t.arrow is None for t in tiles)
        assert {t.status for t in tiles} <= {G.MATCH, G.UNKNOWN}
    assert {t.status for t in G.compare(pf(), pf())} == {G.MATCH}


# --- the pool and the daily walk ---------------------------------------------------------------

def test_the_pool_is_the_recognisable(players):
    """Anchored on who is and is not in it, not on the threshold constant: a test that only
    compared the pool with `POOL_MIN_PROMINENCE` would move with the constant and pass when
    it was broken to zero."""
    pool = G.pool(players)
    names = {p.name for p in pool}
    assert {"V Kohli", "MS Dhoni", "SL Malinga", "CH Gayle", "JJ Bumrah"} <= names
    assert 150 <= len(pool) < len(players) // 2          # the recognisable, not everybody
    least = sorted(players.values(), key=lambda p: (p.prominence, p.person_id))[:100]
    assert not any(p in pool for p in least)             # a one-season substitute is never the answer
    assert all(len(p.seasons) >= G.POOL_MIN_SEASONS for p in pool)


def test_a_seed_is_the_mystery(players):
    assert G.mystery(players, 739898).person_id == G.mystery(players, 739898).person_id
    assert len({G.mystery(players, s).person_id for s in range(10_000_000, 10_000_040)}) > 20


def test_the_daily_does_not_repeat_a_player_until_every_other_has_had_a_day(players):
    n = len(G.pool(players))
    d0 = datetime.date(2026, 10, 9).toordinal()
    seen = [G.mystery(players, d0 + i).person_id for i in range(n)]
    assert len(set(seen)) == n
    assert G.mystery(players, d0 + n).person_id == seen[0]       # then the cycle begins again


def test_the_mystery_does_not_depend_on_the_process():
    """`hash()` is salted per process, so a day derived from it would differ between two
    servers; only a subprocess under different hash seeds can see that (A125)."""
    code = ("import sys; sys.path.insert(0, '.');"
            "from game import guess as G, puzzle_facts as pf;"
            "P = {p.person_id: p for p in pf.players_from(pf.read_document())};"
            "print(G.mystery(P, 739898).person_id, G.mystery(P, 10000007).person_id)")
    outs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env={**os.environ, "PYTHONHASHSEED": h}, check=True).stdout
            for h in ("1", "2", "3")}
    assert len(outs) == 1


# --- the rules ---------------------------------------------------------------------------------

@pytest.fixture
def world():
    people = {f"p{i}": pf(pid=f"p{i}", name=f"Player {i}") for i in range(12)}
    return people, people["p0"]


def test_finding_him_solves_it(world):
    people, answer = world
    st = G.State([])
    G.guess(people, answer, st, "p3")
    assert not st.solved and st.guesses_left == 7
    G.guess(people, answer, st, "p0")
    assert st.solved and st.finished


def test_eight_guesses_end_the_game(world):
    people, answer = world
    st = G.State([])
    for i in range(1, 9):
        assert not st.finished
        G.guess(people, answer, st, f"p{i}")
    assert st.finished and not st.solved and st.guesses_left == 0
    with pytest.raises(G.GuessError, match="finished"):
        G.guess(people, answer, st, "p9")


def test_the_same_guess_twice_is_refused_and_free(world):
    people, answer = world
    st = G.State([])
    G.guess(people, answer, st, "p1")
    with pytest.raises(G.GuessError, match="already guessed"):
        G.guess(people, answer, st, "p1")
    assert st.guesses_left == 7


def test_an_unknown_player_is_refused_and_free(world):
    people, answer = world
    st = G.State([])
    with pytest.raises(G.GuessError, match="Nobody"):
        G.guess(people, answer, st, "nobody")
    assert st.guesses == []


def test_replay_equals_playing_the_moves_one_by_one(world):
    people, answer = world
    moves = ["p4", "p5", "p0"]
    st = G.State([])
    for m in moves:
        G.guess(people, answer, st, m)
    assert G.replay(people, answer, moves) == st


def test_giving_up_ends_it_without_solving(world):
    people, answer = world
    st = G.replay(people, answer, ["p1"], gave_up=True)
    assert st.finished and not st.solved and st.gave_up


# --- sharing -----------------------------------------------------------------------------------

def test_the_share_line_is_squares_per_guess_and_names_nobody(players):
    answer = G.mystery(players, 739898)
    others = [p.person_id for p in players.values() if p.person_id != answer.person_id][:2]
    st = G.replay(players, answer, others + [answer.person_id])
    text = G.share_text(players, answer, st, "9 Oct 2026", daily=True, seed=739898)
    lines = text.splitlines()
    assert lines[0] == "Fine Leg XI · Guess the Player · 9 Oct 2026 · 3/8"
    assert len(lines) == 1 + 3 + 1
    assert all(len(r) == 8 and set(r) <= set("🟩🟨⬛") for r in lines[1:4])
    assert lines[3] == "🟩" * 8                         # the last row is the answer himself
    assert lines[4] == "finelegxi.in/guess"
    for p in players.values():
        assert p.name not in text                     # a spoiler is the whole failure here


def test_a_failed_puzzle_reads_x_and_a_practice_one_carries_its_seed(players):
    answer = G.mystery(players, 10000007)
    wrong = [p.person_id for p in players.values() if p.person_id != answer.person_id][:8]
    st = G.replay(players, answer, wrong)
    text = G.share_text(players, answer, st, "Puzzle 10000007", daily=False, seed=10000007)
    assert text.splitlines()[0].endswith("· X/8")
    assert text.endswith("/guess?seed=10000007")


# --- the routes --------------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(bingo_routes, "today", lambda: datetime.date(2026, 10, 9))
    app = FastAPI()
    app.include_router(guess_routes.router)
    return TestClient(app)


def mystery_of(players, seed=739898):
    return G.mystery(players, seed)


def test_today_names_no_player(client, players):
    r = client.get("/api/guess/today")
    d = r.json()
    assert d["daily"] and d["date"] == "2026-10-09" and d["seed"] == datetime.date(2026, 10, 9).toordinal()
    assert d["guesses"] == G.GUESSES
    answer = mystery_of(players)
    assert answer.name not in r.text and answer.person_id not in r.text


def test_a_seeded_puzzle_is_cacheable_and_a_fresh_one_is_not(client):
    assert "s-maxage" in client.get("/api/guess?seed=10000042").headers["cache-control"]
    fresh = client.get("/api/guess")
    assert fresh.headers["cache-control"] == "no-store"
    assert fresh.json()["seed"] >= G.PRACTICE_SEED_FLOOR and not fresh.json()["daily"]


def test_a_wrong_guess_returns_tiles_and_keeps_the_answer_hidden(client, players):
    answer = mystery_of(players)
    wrong = next(p for p in sorted(players) if p != answer.person_id)
    r = client.post("/api/guess/play", json={"seed": 739898, "guess": wrong}).json()
    st = r["state"]
    assert r["correct"] is False and st["guesses_left"] == 7 and not st["finished"]
    assert st["answer"] is None and st["share"] is None
    assert len(st["rows"]) == 1 and len(st["rows"][0]["tiles"]) == 8
    assert answer.name not in str(r)


def test_the_right_guess_finishes_and_reveals(client, players):
    answer = mystery_of(players)
    r = client.post("/api/guess/play", json={"seed": 739898, "guess": answer.person_id}).json()
    st = r["state"]
    assert r["correct"] is True and st["solved"] and st["finished"]
    assert st["answer"]["name"] == answer.name
    assert st["share"].startswith("Fine Leg XI · Guess the Player · 9 Oct 2026 · 1/8")


def test_play_replays_the_guesses_so_far(client, players):
    answer = mystery_of(players)
    wrong = [p for p in sorted(players) if p != answer.person_id][:3]
    r = client.post("/api/guess/play", json={"seed": 739898, "guesses": wrong}).json()
    assert r["correct"] is None and len(r["state"]["rows"]) == 3 and r["state"]["guesses_left"] == 5


def test_give_up_reveals_the_player(client, players):
    answer = mystery_of(players)
    r = client.post("/api/guess/play", json={"seed": 739898, "give_up": True}).json()["state"]
    assert r["finished"] and not r["solved"] and r["answer"]["name"] == answer.name
    assert r["share"].splitlines()[0].endswith("· X/8")


def test_a_move_the_rules_refuse_is_a_400_with_a_reason(client, players):
    some = sorted(players)[0]
    r = client.post("/api/guess/play", json={"seed": 739898, "guesses": [some], "guess": some})
    assert r.status_code == 400 and "already guessed" in r.json()["detail"]
    assert client.post("/api/guess/play", json={"seed": 739898, "guess": "nobody"}).status_code == 400


def test_more_than_eight_guesses_are_rejected_outright(client, players):
    ids = sorted(players)[:9]
    assert client.post("/api/guess/play", json={"seed": 739898, "guesses": ids}).status_code == 422


def test_a_finished_puzzle_refuses_more_guesses(client, players):
    answer = mystery_of(players)
    wrong = [p for p in sorted(players) if p != answer.person_id][:8]
    extra = next(p for p in sorted(players) if p not in wrong)
    r = client.post("/api/guess/play", json={"seed": 739898, "guesses": wrong, "guess": extra})
    assert r.status_code == 400 and "finished" in r.json()["detail"]


def test_a_missing_snapshot_is_a_503_not_a_crash(client, monkeypatch):
    monkeypatch.setattr(bingo_routes, "_facts", lambda: None)
    assert client.post("/api/guess/play", json={"seed": 739898}).status_code == 503
