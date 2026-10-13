"""Three to Make (A196): the target, the scoring, the board's order, the hidden figures, and the
server-clocked attempt.

The rules are tested on hand-built players, where every right answer is obvious by construction.
The real facts are used only where the claim is about them (the share line leaks no name, the
puzzle never carries a figure). The SQL half -- the clock, the hold, the sweep, the board -- runs
against a real scratch Postgres and is opt-in (IPLEGENDS_SCRATCH_DB), like
tests/test_puzzle_results.py. Each test runs in a transaction that is rolled back.
"""

from __future__ import annotations

import contextlib
import datetime
import itertools
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from game import bingo, puzzle_facts, puzzle_results as pr, trio
from game.puzzle_facts import PlayerFacts
from web import auth, bingo_routes, db, puzzle_results, puzzle_routes, trio_routes

DOC = puzzle_facts.read_document()
needs_facts = pytest.mark.skipif(DOC is None, reason="puzzle facts not committed")

TODAY = datetime.date(2026, 10, 9)
SEED = TODAY.toordinal()


def player(pid, runs=0, wickets=0, sixes=0, best=0, seasons=4, famous=True):
    """A player; `famous` puts him in the well-known pool (prominence >= 1000, 3+ seasons)."""
    return PlayerFacts(person_id=pid, name=f"Player {pid.upper()}", franchises=("Mumbai Indians",),
                       seasons=tuple(range(2010, 2010 + (seasons if famous else 1))), overseas=False,
                       keeper=False, runs=runs if famous else min(runs, 999), wickets=wickets,
                       best_season_runs=best, best_season_wickets=0, hundreds=0, fifties=0,
                       four_wicket_hauls=0, sixes=sixes)


def world(*players):
    return trio.Trio({p.person_id: p for p in players})


@pytest.fixture(scope="module")
def small():
    """Five known players, some sharing a figure (so the counting has multiplicities to get
    right); `h`, well known for runs but below the wickets, sixes and best-season floors, so the
    floor has someone to keep out; and two fringe players outside the pool altogether."""
    return world(player("a", runs=1500, wickets=50, sixes=60, best=400),
                 player("b", runs=2000, wickets=45, sixes=80, best=500),
                 player("c", runs=2000, wickets=60, sixes=40, best=450),
                 player("d", runs=3000, wickets=70, sixes=100, best=600),
                 player("e", runs=1200, wickets=41, sixes=41, best=380),
                 player("h", runs=1050, wickets=2, sixes=5, best=300),
                 player("f", runs=100, wickets=150, sixes=1, best=50, famous=False),
                 player("g", runs=5, wickets=0, sixes=0, best=5, famous=False))


# --- the target --------------------------------------------------------------------------------

def test_the_target_is_the_sum_of_three_distinct_known_players_above_the_stats_floor(small):
    for seed in range(SEED, SEED + 12):
        p = small.puzzle(seed)
        assert len(set(p.example)) == trio.PICKS
        assert p.target == sum(small.value(p.stat, x) for x in p.example)
        for x in p.example:
            assert small.value(p.stat, x) >= p.stat.floor          # a target built from men known for it
            assert x in {"a", "b", "c", "d", "e", "h"}              # never the two outside the pool
    assert "h" in small.makers["runs"] and all("h" not in small.makers[k] for k in ("wickets", "sixes", "best_season_runs"))


def test_the_daily_stat_rotates_through_all_four_and_a_seed_is_the_same_puzzle_every_time(small):
    stats = [small.puzzle(s).stat.key for s in range(SEED, SEED + len(trio.STATS))]
    assert sorted(stats) == sorted(s.key for s in trio.STATS)
    assert small.puzzle(SEED) == small.puzzle(SEED)
    practice = trio.PRACTICE_SEED_FLOOR + 77
    assert small.puzzle(practice) == small.puzzle(practice)


def test_the_exact_count_agrees_with_brute_force_including_shared_values(small):
    """Shared figures (b and c both 2,000 runs) exercise every branch of counting by value."""
    for stat in trio.STATS:
        sums = {sum(v) for v in itertools.combinations([small.value(stat, x) for x in small.players], 3)}
        for target in sorted(sums) + [1, 999_999]:
            brute = sum(1 for t in itertools.combinations(small.players, 3)
                        if sum(small.value(stat, x) for x in t) == target)
            assert small.exact_count(stat, target) == brute, (stat.key, target)


def test_a_triple_of_equal_values_counts_its_combinations():
    same = world(*(player(k, runs=1000) for k in "pqrst"))
    assert same.exact_count(trio.STAT_BY_KEY["runs"], 3000) == 10         # 5 choose 3


# --- an attempt and its score -------------------------------------------------------------------

def test_distance_is_how_far_the_three_land_either_side(small):
    p = trio.Puzzle(1, trio.STAT_BY_KEY["runs"], 5000, ("a", "b", "d"))
    over = trio.play(small, p, ["b", "c", "d"], 10_000)                   # 7,000
    short = trio.play(small, p, ["a", "e", "f"], 10_000)                  # 2,800
    third = trio.play(small, p, ["e", "c", "f"], 10_000)                  # 1,200 + 2,000 + 100 = 3,300
    assert over.distance == 2000 and over.total == 7000 and not over.exact
    assert short.distance == 2200
    assert third.distance == 1700
    hit = trio.play(small, p, ["a", "b", "f"], 10_000)                    # 1,500 + 2,000 + 100 = 3,600
    assert hit.distance == 1400
    bang = trio.play(small, trio.Puzzle(1, p.stat, 3600, p.example), ["f", "a", "b"], 5)
    assert bang.exact and bang.distance == 0 and bang.t == 5


def test_the_rules_refuse_a_trio_that_is_not_three_different_real_players(small):
    p = small.puzzle(SEED)
    for ids, why in ((["a", "b"], "three"), (["a", "b", "c", "d"], "three"), (["a", "a", "b"], "DIFFERENT"),
                     (["a", "b", "nobody"], "Nobody")):
        with pytest.raises(trio.TrioError, match=why):
            trio.play(small, p, ids, 1000)
    with pytest.raises(trio.TrioError, match="Time"):
        trio.play(small, p, ["a", "b", "c"], trio.LIMIT_MS + 1)


def test_a_clock_that_ran_out_with_no_trio_is_no_answer_and_scores_nothing(small):
    p = small.puzzle(SEED)
    r = trio.play(small, p, None, trio.LIMIT_MS + 5000)
    assert r.distance is None and r.ended == "time" and r.t == trio.LIMIT_MS and not r.picks
    assert trio.score(p, r) == 0


def test_exact_scores_the_maximum_and_any_miss_scores_less_never_below_zero(small):
    p = trio.Puzzle(1, trio.STAT_BY_KEY["runs"], 5000, ("a", "b", "d"))
    def sc(d):
        return trio.score(p, trio.Result(("a", "b", "c"), (0, 0, 0), 5000 + d, abs(d), 0, None))
    assert sc(0) == trio.MAX_SCORE
    assert sc(1) == trio.MAX_SCORE - 1                       # rounded UP: even one run off is below exact
    assert sc(50) == 990 and sc(-50) == 990
    assert sc(5000) == 0 and sc(9000) == 0


def test_the_board_ranks_distance_first_then_rarity_then_time():
    order = sorted([("slow exact", 0, 0, 80_000), ("fast exact", 0, 0, 10_000),
                    ("rare exact", 0, 120, 85_000), ("rare miss", 1, 150, 1_000),
                    ("no trio", None, 0, 90_000)],
                   key=lambda r: trio.rank_key(r[1], r[2], r[3]))
    assert [r[0] for r in order] == ["rare exact", "fast exact", "slow exact", "rare miss", "no trio"]


def test_the_web_board_uses_that_order_and_keeps_rarity_out_of_the_points():
    now = datetime.datetime(2026, 10, 9, 12)
    rows = [{"account_id": 1, "points": 999, "bonus": 150, "elapsed_ms": 5_000, "finished_at": now,
             "detail": {"distance": 1}},
            {"account_id": 2, "points": 1000, "bonus": 0, "elapsed_ms": 80_000, "finished_at": now,
             "detail": {"distance": 0}}]
    rows.sort(key=lambda r: puzzle_results.rank_key(r, "trio"))
    assert [r["account_id"] for r in rows] == [2, 1]
    # ...whereas the generic rule (points, then time) would have put the rarer, faster miss first
    # had rarity been added to its points: 999 + 150 > 1000.
    assert puzzle_results.rank_key(rows[1] | {"points": 1149}) < puzzle_results.rank_key(rows[0])


def test_a_verified_outcome_carries_the_distance_the_board_reads(small):
    p = trio.Puzzle(1, trio.STAT_BY_KEY["runs"], 3600, ("a", "b", "f"))
    out = pr.verify_trio(small, p, ["f", "a", "b"], 42_000)
    assert out.game == "trio" and out.solved and out.score == trio.MAX_SCORE and out.elapsed_ms == 42_000
    assert [x.person_id for x in out.picks] == ["a", "b", "f"]           # sorted: order picked is no clue
    d = out.detail()
    assert d["distance"] == 0 and d["sum"] == 3600 and d["target"] == 3600 and d["stat"] == "runs"
    empty = pr.verify_trio(small, p, None, 95_000)
    assert not empty.solved and empty.picks == () and empty.detail()["distance"] is None and empty.ended == "time"
    with pytest.raises(pr.PuzzleResultError):
        pr.verify_trio(small, p, ["a", "a", "b"], 1000)


# --- what the page is and is not told ------------------------------------------------------------

@needs_facts
def test_the_share_line_names_nobody():
    t = trio.Trio({p.person_id: p for p in puzzle_facts.players_from(DOC)})
    for seed in range(SEED, SEED + 8):
        p = t.puzzle(seed)
        r = trio.play(t, p, list(p.example), 30_000)
        text = trio.share_text(p, r, "9 Oct 2026", daily=True)
        assert "Bang on" in text and f"{p.target:,}" in text
        for pid in t.players:
            assert t.players[pid].name not in text, t.players[pid].name
            assert pid not in text, pid


@needs_facts
def test_the_puzzle_carries_no_players_figures_until_it_is_over(monkeypatch):
    monkeypatch.setattr(bingo_routes, "today", lambda: TODAY)
    app = FastAPI()
    app.include_router(trio_routes.router)
    client = TestClient(app)
    p = client.get("/api/trio/today").json()
    t = trio_routes.trio()
    example = t.puzzle(SEED).example
    assert set(p) == set(trio_routes.PuzzleOut.model_fields)
    assert not any(x in str(p) for x in example) and "value" not in str(p)
    done = client.post("/api/trio/play", json={"seed": SEED, "picks": list(example), "t": 12_000}).json()
    assert done["exact"] and done["distance"] == 0 and done["elapsed_ms"] == 12_000
    assert [x["value"] for x in done["picks"]] == [t.value(t.puzzle(SEED).stat, x) for x in example]
    assert done["exact_count"] >= 1
    assert client.post("/api/trio/play", json={"seed": SEED, "picks": None, "t": trio.LIMIT_MS}).json()["distance"] is None
    assert client.post("/api/trio/play", json={"seed": SEED, "picks": ["x", "x", "y"], "t": 0}).status_code == 400


# --- SQL: a real scratch Postgres ----------------------------------------------------------------

SCRATCH = os.environ.get("IPLEGENDS_SCRATCH_DB")
needs_pg = pytest.mark.skipif(
    not SCRATCH, reason="set IPLEGENDS_SCRATCH_DB to a throwaway, fully migrated database")


@pytest.fixture
def pg():
    import psycopg
    conn = psycopg.connect(SCRATCH)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def account(pg, name: str) -> int:
    return pg.execute("insert into accounts (username, email, password_hash) values (%s, %s, 'x') "
                      "returning account_id", (name, f"{name}@example.com")).fetchone()[0]


def age(pg, a, seconds: float):
    pg.execute("update puzzle_results set started_at = now() - %s * interval '1 second' "
               "where account_id = %s and game = 'trio'", (seconds, a))


@pytest.fixture
def puzzle(small):
    return small.puzzle(SEED)


@needs_pg
def test_the_table_accepts_three_to_make(pg):
    a = account(pg, "ava")
    puzzle_results.ranked_start(pg, a, TODAY, "trio")
    pg.execute("insert into puzzle_picks (game, day, slot, voter, person_id) values ('trio', %s, 'a', 'a:1', 'a')", (TODAY,))


@needs_pg
def test_locking_in_is_stamped_with_the_servers_clock_and_records_the_picks(pg, small, puzzle):
    a = account(pg, "ava")
    puzzle_results.ranked_start(pg, a, TODAY, "trio")
    age(pg, a, 40)
    view, mine = puzzle_results.trio_play(pg, small, puzzle, a, TODAY, list(puzzle.example), lock=True)
    assert mine and view["finished"]
    row = puzzle_results.result_for(pg, a, "trio", TODAY)
    assert 39_000 <= row["elapsed_ms"] <= 41_000 and row["solved"] and row["score"] == trio.MAX_SCORE
    assert row["detail"]["distance"] == 0
    got = {r[0] for r in pg.execute("select person_id from puzzle_picks where game = 'trio' and voter = %s", (f"a:{a}",))}
    assert got == set(puzzle.example)
    # one attempt: a second lock-in changes nothing
    _, again = puzzle_results.trio_play(pg, small, puzzle, a, TODAY, ["a", "b", "c"], lock=True)
    assert not again and puzzle_results.result_for(pg, a, "trio", TODAY)["detail"]["distance"] == 0


@needs_pg
def test_without_a_start_nothing_is_held_and_a_bad_trio_changes_nothing(pg, small, puzzle):
    a = account(pg, "ava")
    with pytest.raises(puzzle_results.OpenAttempt):
        puzzle_results.trio_play(pg, small, puzzle, a, TODAY, ["a"])
    puzzle_results.ranked_start(pg, a, TODAY, "trio")
    puzzle_results.trio_play(pg, small, puzzle, a, TODAY, ["a", "b"])
    with pytest.raises(pr.PuzzleResultError):
        puzzle_results.trio_play(pg, small, puzzle, a, TODAY, ["a", "a", "b"], lock=True)
    assert pg.execute("select moves, finished_at from puzzle_results where account_id = %s", (a,)).fetchone() == (["a", "b"], None)


@needs_pg
def test_a_full_trio_held_when_the_clock_runs_out_is_locked_in_at_the_limit(pg, small, puzzle):
    a = account(pg, "ava")
    puzzle_results.ranked_start(pg, a, TODAY, "trio")
    puzzle_results.trio_play(pg, small, puzzle, a, TODAY, list(puzzle.example))
    age(pg, a, 300)                                        # the tab was closed
    assert puzzle_results.trio_sweep(pg, small, TODAY) == 1
    row = puzzle_results.result_for(pg, a, "trio", TODAY)
    assert row["elapsed_ms"] == trio.LIMIT_MS and row["detail"]["distance"] == 0 and row["ended"] is None


@needs_pg
def test_less_than_a_trio_when_the_clock_runs_out_is_no_answer(pg, small, puzzle):
    a = account(pg, "ava")
    puzzle_results.ranked_start(pg, a, TODAY, "trio")
    puzzle_results.trio_play(pg, small, puzzle, a, TODAY, ["a", "b"])
    age(pg, a, 300)
    view, mine = puzzle_results.trio_play(pg, small, puzzle, a, TODAY, ["a", "b", "c"], lock=True)
    assert not mine and view["late"] and view["finished"]          # too late to count
    row = puzzle_results.result_for(pg, a, "trio", TODAY)
    assert row["score"] == 0 and row["detail"]["distance"] is None and row["ended"] == "time"
    assert not pg.execute("select 1 from puzzle_picks where voter = %s", (f"a:{a}",)).fetchall()


@needs_pg
def test_a_lock_in_in_the_last_two_seconds_still_counts_at_the_limit(pg, small, puzzle):
    a = account(pg, "ava")
    puzzle_results.ranked_start(pg, a, TODAY, "trio")
    age(pg, a, trio.LIMIT_SECONDS + 1)
    _, mine = puzzle_results.trio_play(pg, small, puzzle, a, TODAY, list(puzzle.example), lock=True)
    assert mine and puzzle_results.result_for(pg, a, "trio", TODAY)["elapsed_ms"] == trio.LIMIT_MS


@needs_pg
def test_the_board_breaks_a_tie_on_distance_by_rarity_before_time(pg, small, puzzle):
    """Three players exactly on the target. Two share a common trio; the third's trio is one
    nobody else reached for, and she was the SLOWEST -- rarity puts her first anyway. The tie
    between the other two goes to the quicker. A miss with the rarest trio of all is last."""
    import game.rarity as R
    ids = {n: account(pg, n) for n in ("ava", "ben", "cyd", "dee")}
    common = list(puzzle.example)
    exact_rare = None
    for t in itertools.combinations(sorted(small.players), 3):
        if sum(small.value(puzzle.stat, x) for x in t) == puzzle.target and set(t) != set(common):
            exact_rare = list(t)
            break
    assert exact_rare, "the fixture needs a second exact trio"
    plays = {"ava": (common, 20), "ben": (common, 10), "cyd": (exact_rare, 80), "dee": (None, 5)}
    for name, (trio_ids, secs) in plays.items():
        puzzle_results.ranked_start(pg, ids[name], TODAY, "trio")
        age(pg, ids[name], secs)
        if trio_ids is None:
            far = next(t for t in itertools.combinations(sorted(small.players), 3)
                       if sum(small.value(puzzle.stat, x) for x in t) != puzzle.target)
            puzzle_results.trio_play(pg, small, puzzle, ids[name], TODAY, list(far), lock=True)
        else:
            puzzle_results.trio_play(pg, small, puzzle, ids[name], TODAY, trio_ids, lock=True)
    # Enough signed-out voters to give rarity a sample, every one choosing the common trio.
    for k in range(R.MIN_VOTERS):
        for pid in common:
            pg.execute("insert into puzzle_picks (game, day, slot, voter, person_id) values ('trio', %s, %s, %s, %s)",
                       (TODAY, pid, f"d:{k:032x}", pid))
    rows = puzzle_results.board(pg, "trio", TODAY, t=small, limit=0)
    assert [r["username"] for r in rows] == ["cyd", "ben", "ava", "dee"]
    assert all(r["points"] == r["score"] for r in rows)              # rarity is never added in
    assert rows[0]["bonus"] > rows[1]["bonus"]


@pytest.fixture
def client(pg, monkeypatch, small):
    monkeypatch.setenv("SESSION_SECRET", "test-only-secret-do-not-use-in-real-life")
    monkeypatch.setattr(bingo_routes, "today", lambda: TODAY)
    monkeypatch.setattr(trio_routes, "trio", lambda: small)
    hand_built = bingo.Facts(list(small.players.values()))        # so the rarity panel can name them
    monkeypatch.setattr(bingo_routes, "facts", lambda: hand_built)

    @contextlib.contextmanager
    def one_connection():
        yield pg

    monkeypatch.setattr(db, "connection", one_connection)
    app = FastAPI()
    app.include_router(puzzle_routes.router)
    return TestClient(app)


@needs_pg
def test_the_ranked_routes_hide_the_figures_until_it_is_locked_in(client, pg, small, puzzle, monkeypatch):
    monkeypatch.setattr(trio_routes, "_exact", lambda _pid, stat, target: 7)
    assert client.get("/api/trio/ranked").status_code == 401
    a = account(pg, "ava")
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(a))
    assert client.get("/api/trio/ranked").json()["started"] is False
    assert client.post("/api/trio/ranked/play", json={"picks": ["a"]}).status_code == 409
    start = client.post("/api/trio/ranked/start").json()
    assert start["started"] and not start["finished"] and 0 < start["remaining_ms"] <= trio.LIMIT_MS
    held = client.post("/api/trio/ranked/play", json={"picks": ["a", "b"]}).json()
    assert [h["person_id"] for h in held["held"]] == ["a", "b"] and held["result"] is None
    assert "value" not in str(held)
    done = client.post("/api/trio/ranked/play", json={"picks": list(puzzle.example), "lock": True}).json()
    assert done["finished"] and done["result"]["exact"] and done["rank"] == 1 and done["of"] == 1
    assert all("value" in p for p in done["result"]["picks"])
    assert client.get("/api/trio/ranked").json()["result"]["distance"] == 0       # a reload sees it too
    board = client.get("/api/puzzles/board/trio").json()
    assert board["rows"][0]["distance"] == 0 and board["label"] == "Three to Make"
