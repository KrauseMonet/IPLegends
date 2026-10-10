"""Puzzle results (A188): replaying a finished daily into an outcome, the one-row-per-day record,
the server-clocked Common Ground attempt, the board, and the routes that carry them.

The first half needs no database: `game.puzzle_results` is a pure replay, tested against the real
facts. The second half is SQL -- constraints, one-row-per-day, the clock, the sweep, the order of a
board -- which a fake connection could not check, so it runs against a real scratch Postgres and
is opt-in, like tests/test_room_pg.py and tests/test_client_failures.py (set IPLEGENDS_SCRATCH_DB
to a throwaway, fully migrated database). Each test runs in a transaction that is rolled back.
"""

from __future__ import annotations

import contextlib
import datetime
import os
import pathlib
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from game import bingo, ground, guess as guess_game, puzzle_facts, puzzle_results as pr, xi as xi_game
from web import auth, bingo_routes, db, ground_routes, puzzle_results, puzzle_routes, xi_routes

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOC = puzzle_facts.read_document()
needs_facts = pytest.mark.skipif(DOC is None, reason="puzzle facts not committed")

TODAY = datetime.date(2026, 10, 9)
SEED = TODAY.toordinal()


@pytest.fixture(scope="module")
def facts():
    return bingo.Facts(puzzle_facts.players_from(DOC))


@pytest.fixture(scope="module")
def sides():
    return puzzle_facts.sides_from(DOC)


@pytest.fixture(scope="module")
def real():
    return ground.Ground({p.person_id: p for p in puzzle_facts.players_from(DOC)})


# --- the day a seed stands for -----------------------------------------------------------------

def test_only_today_and_yesterday_can_be_ranked():
    assert pr.daily_date(SEED, TODAY) == TODAY
    assert pr.daily_date(SEED - 1, TODAY) == TODAY - datetime.timedelta(days=1)
    for seed, why in ((SEED - 2, "closed"), (SEED + 1, "not opened"), (bingo.PRACTICE_SEED_FLOOR + 5, "daily")):
        with pytest.raises(pr.PuzzleResultError, match=why):
            pr.daily_date(seed, TODAY)


# --- Bingo -------------------------------------------------------------------------------------

def _match(cells: list[set[str]]) -> dict[int, str]:
    """One distinct player per cell, by augmenting paths -- no greedy choice can strand a cell."""
    owner: dict[str, int] = {}

    def place(cell: int, seen: set[str]) -> bool:
        for pid in sorted(cells[cell]):
            if pid in seen:
                continue
            seen.add(pid)
            if pid not in owner or place(owner[pid], seen):
                owner[pid] = cell
                return True
        return False

    for c in range(len(cells)):
        assert place(c, set()), "no way to fill every cell"
    return {cell: pid for pid, cell in owner.items()}


def _bingo_solution(facts, seed):
    grid = bingo.make_grid(facts, seed)
    return grid, _match(grid.cells)


@needs_facts
def test_a_filled_grid_replays_to_its_own_score_and_picks(facts):
    grid, sol = _bingo_solution(facts, SEED)
    guesses = [(cell, sol[cell]) for cell in range(9)]
    out = pr.verify_bingo(grid, facts, guesses)
    assert out.solved and out.perfect and out.found == 9 and out.wrong == 0 and out.guesses == 9
    assert out.score == sum(grid.points(c) for c in range(9)) == bingo.replay(grid, facts, guesses).score
    assert [p.slot for p in out.picks] == [str(c) for c in range(9)]
    assert [p.base for p in out.picks] == [grid.points(c) for c in range(9)]


@needs_facts
def test_a_grid_that_ran_out_of_guesses_is_finished_but_not_solved(facts):
    grid, sol = _bingo_solution(facts, SEED)
    stranger = next(p for p in sorted(facts.players) if p not in grid.cells[0])
    wrong = [(0, stranger)] + [(0, p) for p in sorted(facts.players) if p not in grid.cells[0] and p != stranger][:8]
    out = pr.verify_bingo(grid, facts, wrong)
    assert not out.solved and not out.perfect and out.found == 0 and out.wrong == 9 and out.score == 0


@needs_facts
def test_a_grid_still_in_play_is_not_recorded(facts):
    grid, sol = _bingo_solution(facts, SEED)
    with pytest.raises(pr.NotFinished):
        pr.verify_bingo(grid, facts, [(0, sol[0])])


@needs_facts
def test_a_move_the_rules_refuse_is_a_refused_result(facts):
    grid, sol = _bingo_solution(facts, SEED)
    with pytest.raises(pr.PuzzleResultError, match="already filled"):
        pr.verify_bingo(grid, facts, [(0, sol[0]), (0, sol[1])])
    with pytest.raises(pr.PuzzleResultError, match="Nobody"):
        pr.verify_bingo(grid, facts, [(0, "nobody")])


# --- Guess the Player ---------------------------------------------------------------------------

@needs_facts
def test_the_score_is_a_hundred_per_guess_left(facts):
    answer = guess_game.mystery(facts.players, SEED)
    others = [p for p in sorted(facts.players) if p != answer.person_id]
    for k in (1, 3, 8):
        out = pr.verify_guess(facts.players, answer, others[:k - 1] + [answer.person_id])
        assert out.solved and out.score == 100 * (guess_game.GUESSES + 1 - k)
        assert out.guesses == k and out.wrong == k - 1 and out.perfect is (k == 1)


@needs_facts
def test_a_missed_or_abandoned_puzzle_scores_nothing(facts):
    answer = guess_game.mystery(facts.players, SEED)
    others = [p for p in sorted(facts.players) if p != answer.person_id]
    missed = pr.verify_guess(facts.players, answer, others[:guess_game.GUESSES])
    assert not missed.solved and missed.score == 0 and missed.wrong == guess_game.GUESSES
    quit_ = pr.verify_guess(facts.players, answer, others[:2], gave_up=True)
    assert not quit_.solved and quit_.score == 0 and quit_.ended == "gave_up"


@needs_facts
def test_a_guess_puzzle_in_play_is_not_recorded(facts):
    answer = guess_game.mystery(facts.players, SEED)
    with pytest.raises(pr.NotFinished):
        pr.verify_guess(facts.players, answer, [sorted(facts.players)[0]])


# --- Name the XI --------------------------------------------------------------------------------

@needs_facts
def test_a_complete_side_scores_a_hundred_each_and_a_bonus_for_mistakes_unused(facts, sides):
    side = xi_game.pick_side(sides, SEED)
    ids = [q.person_id for q in side.players]
    out = pr.verify_xi(facts.players, side, ids)
    assert out.solved and out.perfect and out.found == len(ids) == out.total
    assert out.score == 100 * len(ids) + 20 * xi_game.MISTAKES
    assert [p.slot for p in out.picks] == [str(k) for k in range(len(ids))]
    stranger = next(p for p in sorted(facts.players) if p not in ids and p not in side.opposition)
    one_slip = pr.verify_xi(facts.players, side, [stranger] + ids)
    assert one_slip.solved and not one_slip.perfect
    assert one_slip.score == 100 * len(ids) - 20 + 20 * (xi_game.MISTAKES - 1)


@needs_facts
def test_a_partial_side_is_worth_what_was_named_less_the_mistakes_and_never_below_nothing(facts, sides):
    side = xi_game.pick_side(sides, SEED)
    ids = [q.person_id for q in side.players]
    strangers = [p for p in sorted(facts.players) if p not in ids and p not in side.opposition][:2]
    out = pr.verify_xi(facts.players, side, ids[:3] + strangers, gave_up=True)
    assert not out.solved and out.score == 300 - 40 and out.ended == "gave_up"
    assert pr.verify_xi(facts.players, side, strangers, gave_up=True).score == 0
    with pytest.raises(pr.NotFinished):
        pr.verify_xi(facts.players, side, ids[:3])


# --- Common Ground ------------------------------------------------------------------------------

@needs_facts
def test_a_completed_pair_scores_as_the_game_scores_it_with_its_elapsed_time(real):
    puzzle = real.puzzle(SEED)
    moves = [ground.Move(pid, 5000 * (k + 1)) for k, pid in enumerate(sorted(puzzle.answers))]
    out = pr.verify_common(real, puzzle, moves)
    n = len(moves)
    assert out.solved and out.perfect and out.found == n == out.total and out.elapsed_ms == 5000 * n
    assert out.score == 100 * n + (ground.LIMIT_MS - 5000 * n) // 1000 and out.ended is None
    assert {p.person_id for p in out.picks} == puzzle.answers


@needs_facts
def test_the_clock_running_out_is_recorded_as_time(real):
    puzzle = real.puzzle(SEED)
    first = sorted(puzzle.answers)[0]
    out = pr.verify_common(real, puzzle, [ground.Move(first, 3000)], end_t=ground.LIMIT_MS)
    assert not out.solved and out.ended == "time" and out.elapsed_ms == ground.LIMIT_MS and out.score == 100
    with pytest.raises(pr.NotFinished):
        pr.verify_common(real, puzzle, [ground.Move(first, 3000)])


# --- the outcome itself ------------------------------------------------------------------------

def test_perfect_means_complete_with_nothing_wrong():
    base = dict(game="bingo", score=1, guesses=9, total=9, picks=())
    assert pr.Outcome(solved=True, wrong=0, found=9, **base).perfect
    assert not pr.Outcome(solved=True, wrong=1, found=9, **base).perfect
    assert not pr.Outcome(solved=False, wrong=0, found=8, **base).perfect


def test_the_detail_is_what_the_board_and_the_badges_read():
    out = pr.Outcome("bingo", 120, True, 10, 1, 9, 9, (pr.Pick("0", "p1", 58), pr.Pick("1", "p2", 62)))
    assert out.detail() == {"guesses": 10, "wrong": 1, "found": 9, "total": 9,
                            "picks": [["0", "p1", 58], ["1", "p2", 62]]}


# --- the migration and the code agree ----------------------------------------------------------

def _migration() -> str:
    return (ROOT / "migrations" / "046_puzzle_results.sql").read_text()


def _table(name: str) -> str:
    return re.search(rf"create table {name} \((.*?)\n\);", _migration(), re.S).group(1)


def test_the_games_the_code_can_write_are_the_games_the_table_accepts():
    results = set(re.findall(r"'(\w+)'", re.search(r"game\s+text not null check \(game in \(([^)]*)\)", _table("puzzle_results")).group(1)))
    picks = set(re.findall(r"'(\w+)'", re.search(r"game\s+text not null check \(game in \(([^)]*)\)", _table("puzzle_picks")).group(1)))
    assert results == set(pr.GAMES) and picks == set(pr.PICK_GAMES)


def test_the_endings_the_code_can_write_are_the_endings_the_table_accepts():
    ended = set(re.findall(r"'(\w+)'", re.search(r"ended\s+text check \(ended in \(([^)]*)\)", _table("puzzle_results")).group(1)))
    assert ended == {"gave_up", "time"}


def test_a_voter_is_an_account_or_a_32_hex_browser_and_nothing_else():
    pattern = re.search(r"voter\s+text not null check \(voter ~ '([^']+)'\)", _table("puzzle_picks")).group(1)
    for ok in ("a:1", "a:12345", "d:" + "0123456789abcdef" * 2):
        assert re.fullmatch(pattern, ok), ok
    for bad in ("", "a:", "a:x", "d:abc", "d:" + "g" * 32, "d:" + "0" * 31, "x:1", "a:1 "):
        assert not re.fullmatch(pattern, bad), bad


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


def outcome(game="bingo", score=100, solved=True, wrong=0, picks=(), elapsed=None, ended=None):
    return pr.Outcome(game, score, solved, 9 + wrong, wrong, 9, 9 if solved else 3, tuple(picks),
                      elapsed_ms=elapsed, ended=ended)


@needs_pg
def test_a_result_is_recorded_once_and_a_repeat_changes_nothing(pg):
    a = account(pg, "ava")
    assert puzzle_results.record(pg, a, TODAY, outcome(score=400), [{"cell": 0, "id": "p"}]) is True
    assert puzzle_results.record(pg, a, TODAY, outcome(score=900), []) is False
    row = puzzle_results.result_for(pg, a, "bingo", TODAY)
    assert row["score"] == 400 and row["solved"] is True
    assert pg.execute("select moves from puzzle_results where account_id = %s", (a,)).fetchone()[0] == [{"cell": 0, "id": "p"}]


@needs_pg
def test_the_same_account_may_play_every_game_and_every_day(pg):
    a = account(pg, "ava")
    for game in pr.GAMES:
        assert puzzle_results.record(pg, a, TODAY, outcome(game=game), [])
    assert puzzle_results.record(pg, a, TODAY - datetime.timedelta(days=1), outcome(), [])
    assert pg.execute("select count(*) from puzzle_results where account_id = %s", (a,)).fetchone()[0] == 5


@needs_pg
def test_picks_are_recorded_for_the_games_that_count_them_and_only_those(pg):
    a = account(pg, "ava")
    picks = [pr.Pick("0", "p1", 50), pr.Pick("1", "p2", 40)]
    puzzle_results.record(pg, a, TODAY, outcome(game="bingo", picks=picks), [])
    puzzle_results.record(pg, a, TODAY, outcome(game="guess", picks=picks), [])
    got = pg.execute("select game, slot, voter, person_id from puzzle_picks order by game, slot").fetchall()
    assert got == [("bingo", "0", f"a:{a}", "p1"), ("bingo", "1", f"a:{a}", "p2")]


@needs_pg
def test_a_repeat_submission_does_not_double_count_the_picks(pg):
    a = account(pg, "ava")
    picks = [pr.Pick("0", "p1", 50)]
    puzzle_results.record(pg, a, TODAY, outcome(picks=picks), [])
    puzzle_results.record(pg, a, TODAY, outcome(picks=[pr.Pick("0", "p9", 50)]), [])
    assert pg.execute("select person_id from puzzle_picks").fetchall() == [("p1",)]


@needs_pg
def test_deleting_an_account_takes_its_results_and_nothing_else_with_it(pg):
    a, b = account(pg, "ava"), account(pg, "ben")
    for who in (a, b):
        puzzle_results.record(pg, who, TODAY, outcome(), [])
    pg.execute("delete from accounts where account_id = %s", (a,))
    assert [r[0] for r in pg.execute("select account_id from puzzle_results")] == [b]


@needs_pg
def test_the_table_refuses_what_the_rules_forbid(pg):
    import psycopg
    a = account(pg, "ava")
    bad = [
        ("insert into puzzle_results (account_id, game, day) values (%s, 'chess', %s)", (a, TODAY)),
        ("insert into puzzle_results (account_id, game, day, ended) values (%s, 'bingo', %s, 'bored')", (a, TODAY)),
        # finished with no score, and a score with no finish: the two halves of one rule
        ("insert into puzzle_results (account_id, game, day, finished_at) values (%s, 'bingo', %s, now())", (a, TODAY)),
        ("insert into puzzle_results (account_id, game, day, score, solved) values (%s, 'bingo', %s, 5, true)", (a, TODAY)),
        ("insert into puzzle_results (account_id, game, day, moves) values (%s, 'bingo', %s, '{}')", (a, TODAY)),
        ("insert into puzzle_picks (game, day, slot, voter, person_id) values ('guess', %s, '0', 'a:1', 'p')", (TODAY,)),
        ("insert into puzzle_picks (game, day, slot, voter, person_id) values ('bingo', %s, '0', 'someone', 'p')", (TODAY,)),
    ]
    for sql, params in bad:
        with pytest.raises(psycopg.errors.CheckViolation):
            pg.execute("savepoint s")
            try:
                pg.execute(sql, params)
            finally:
                pg.execute("rollback to savepoint s")


# --- the timed attempt, on the database's clock ------------------------------------------------

def age(pg, a, seconds: float):
    """Pretend the player pressed Start `seconds` ago, inside the test's own transaction."""
    pg.execute("update puzzle_results set started_at = now() - %s * interval '1 second' "
               "where account_id = %s and game = 'common'", (seconds, a))


@needs_pg
@needs_facts
def test_pressing_start_twice_does_not_restart_the_clock(pg, real):
    a = account(pg, "ava")
    puzzle_results.ranked_start(pg, a, TODAY)
    age(pg, a, 90)
    again = puzzle_results.ranked_start(pg, a, TODAY)
    assert 89_000 <= again["elapsed_ms"] <= 91_000 and not again["finished"]


@needs_pg
@needs_facts
def test_a_guess_is_stamped_with_the_servers_clock_not_the_pages(pg, real):
    a = account(pg, "ava")
    puzzle = real.puzzle(SEED)
    first, second = sorted(puzzle.answers)[:2]
    puzzle_results.ranked_start(pg, a, TODAY)
    age(pg, a, 30)
    state, view, applied = puzzle_results.ranked_play(pg, real, puzzle, a, TODAY, guess_id=first)
    assert applied and 29_000 <= state.found[0].t <= 31_000
    age(pg, a, 75)
    state, _, _ = puzzle_results.ranked_play(pg, real, puzzle, a, TODAY, guess_id=second)
    assert 74_000 <= state.found[1].t <= 76_000 and not state.finished
    stored = pg.execute("select moves from puzzle_results where account_id = %s", (a,)).fetchone()[0]
    assert [m["id"] for m in stored] == [first, second] and stored[1]["t"] >= stored[0]["t"]


@needs_pg
@needs_facts
def test_finding_the_whole_set_finishes_it_on_the_servers_time_and_records_the_picks(pg, real):
    a = account(pg, "ava")
    puzzle = real.puzzle(SEED)
    answers = sorted(puzzle.answers)
    puzzle_results.ranked_start(pg, a, TODAY)
    age(pg, a, 100)
    for pid in answers:
        state, view, _ = puzzle_results.ranked_play(pg, real, puzzle, a, TODAY, guess_id=pid)
    assert state.complete and view["finished"]
    row = puzzle_results.result_for(pg, a, "common", TODAY)
    n = len(answers)
    assert row["solved"] and row["score"] == 100 * n + (ground.LIMIT_MS - row["elapsed_ms"]) // 1000
    assert 99_000 <= row["elapsed_ms"] <= 101_000
    picks = {r[0] for r in pg.execute("select person_id from puzzle_picks where voter = %s", (f"a:{a}",))}
    assert picks == set(answers)


@needs_pg
@needs_facts
def test_a_guess_in_the_last_two_seconds_still_lands_and_closes_it_at_the_limit(pg, real):
    a = account(pg, "ava")
    puzzle = real.puzzle(SEED)
    first = sorted(puzzle.answers)[0]
    puzzle_results.ranked_start(pg, a, TODAY)
    age(pg, a, 241)
    state, view, applied = puzzle_results.ranked_play(pg, real, puzzle, a, TODAY, guess_id=first)
    assert applied and state.found[0].t == ground.LIMIT_MS
    assert view["finished"] and view["ended"] == "time"
    row = puzzle_results.result_for(pg, a, "common", TODAY)
    assert row["ended"] == "time" and row["elapsed_ms"] == ground.LIMIT_MS and row["score"] == 100


@needs_pg
@needs_facts
def test_a_guess_after_the_grace_is_too_late_and_the_finish_is_still_written(pg, real):
    a = account(pg, "ava")
    puzzle = real.puzzle(SEED)
    answers = sorted(puzzle.answers)
    puzzle_results.ranked_start(pg, a, TODAY)
    age(pg, a, 20)
    puzzle_results.ranked_play(pg, real, puzzle, a, TODAY, guess_id=answers[0])
    age(pg, a, 300)
    state, view, applied = puzzle_results.ranked_play(pg, real, puzzle, a, TODAY, guess_id=answers[1])
    assert not applied and view["late"] and len(state.found) == 1
    row = puzzle_results.result_for(pg, a, "common", TODAY)
    assert row["ended"] == "time" and row["score"] == 100 and row["detail"]["end_t"] == ground.LIMIT_MS


@needs_pg
@needs_facts
def test_giving_up_banks_what_was_found_at_the_moment_it_was_given_up(pg, real):
    a = account(pg, "ava")
    puzzle = real.puzzle(SEED)
    puzzle_results.ranked_start(pg, a, TODAY)
    age(pg, a, 20)
    puzzle_results.ranked_play(pg, real, puzzle, a, TODAY, guess_id=sorted(puzzle.answers)[0])
    age(pg, a, 61)
    state, view, _ = puzzle_results.ranked_play(pg, real, puzzle, a, TODAY, end=True)
    row = puzzle_results.result_for(pg, a, "common", TODAY)
    assert state.ended == "gave_up" and row["ended"] == "gave_up" and 60_000 <= row["elapsed_ms"] <= 62_000


@needs_pg
@needs_facts
def test_a_refused_move_changes_nothing_and_a_finished_attempt_takes_no_more(pg, real):
    a = account(pg, "ava")
    puzzle = real.puzzle(SEED)
    with pytest.raises(puzzle_results.OpenAttempt):
        puzzle_results.ranked_play(pg, real, puzzle, a, TODAY, guess_id="x")       # never started
    puzzle_results.ranked_start(pg, a, TODAY)
    with pytest.raises(pr.PuzzleResultError, match="one of the two"):
        puzzle_results.ranked_play(pg, real, puzzle, a, TODAY, guess_id=puzzle.a)
    assert pg.execute("select moves from puzzle_results where account_id = %s", (a,)).fetchone()[0] == []
    puzzle_results.ranked_play(pg, real, puzzle, a, TODAY, end=True)
    state, view, applied = puzzle_results.ranked_play(pg, real, puzzle, a, TODAY, guess_id=sorted(puzzle.answers)[0])
    assert not applied and view["finished"] and state.moves == []


@needs_pg
@needs_facts
def test_an_abandoned_attempt_still_takes_its_place_once_the_clock_has_run_out(pg, real):
    a, b = account(pg, "ava"), account(pg, "ben")
    puzzle = real.puzzle(SEED)
    for who in (a, b):
        puzzle_results.ranked_start(pg, who, TODAY)
    puzzle_results.ranked_play(pg, real, puzzle, a, TODAY, guess_id=sorted(puzzle.answers)[0])
    age(pg, a, 30)       # still running: must NOT be swept
    age(pg, b, 400)      # closed tab, long past the limit
    rows = puzzle_results.board(pg, "common", TODAY, real)
    assert [r["username"] for r in rows] == ["ben"] and rows[0]["score"] == 0
    assert puzzle_results.ranked_view(pg, a, TODAY)["finished"] is False
    assert pg.execute("select ended, elapsed_ms from puzzle_results where account_id = %s", (b,)).fetchone() == ("time", ground.LIMIT_MS)


# --- the board -----------------------------------------------------------------------------------

@needs_pg
def test_the_board_is_best_first_then_quickest_then_earliest(pg):
    names = ["ava", "ben", "cyrus", "dee"]
    ids = {n: account(pg, n) for n in names}
    puzzle_results.record(pg, ids["ava"], TODAY, outcome(game="common", score=300, elapsed=90_000), [])
    puzzle_results.record(pg, ids["ben"], TODAY, outcome(game="common", score=500, elapsed=200_000), [])
    puzzle_results.record(pg, ids["cyrus"], TODAY, outcome(game="common", score=500, elapsed=120_000), [])
    puzzle_results.record(pg, ids["dee"], TODAY, outcome(game="common", score=300, elapsed=90_000), [])
    rows = puzzle_results.board(pg, "common", TODAY)
    assert [r["username"] for r in rows] == ["cyrus", "ben", "ava", "dee"]        # 500s by time; equal 300s by who finished first
    assert [r["rank"] for r in rows] == [1, 2, 3, 4]
    assert puzzle_results.rank_of(pg, "common", TODAY, ids["ben"]) == (2, 4)
    assert puzzle_results.rank_of(pg, "bingo", TODAY, ids["ben"]) == (None, 0)


@needs_pg
def test_the_board_is_per_game_and_per_day_and_shows_only_finished_attempts(pg):
    a, b = account(pg, "ava"), account(pg, "ben")
    puzzle_results.record(pg, a, TODAY, outcome(game="bingo", score=10), [])
    puzzle_results.record(pg, a, TODAY, outcome(game="xi", score=99), [])
    puzzle_results.record(pg, b, TODAY - datetime.timedelta(days=1), outcome(game="bingo", score=500), [])
    puzzle_results.ranked_start(pg, b, TODAY)                                  # open: not on any board
    assert [r["score"] for r in puzzle_results.board(pg, "bingo", TODAY)] == [10]
    assert [r["score"] for r in puzzle_results.board(pg, "xi", TODAY)] == [99]
    assert puzzle_results.board(pg, "common", TODAY) == []


@needs_pg
def test_the_board_limit_cuts_the_list_but_never_a_players_true_rank(pg):
    ids = [account(pg, f"player{i}") for i in range(6)]
    for i, who in enumerate(ids):
        puzzle_results.record(pg, who, TODAY, outcome(score=100 * i), [])
    assert len(puzzle_results.board(pg, "bingo", TODAY, limit=3)) == 3
    assert puzzle_results.rank_of(pg, "bingo", TODAY, ids[0]) == (6, 6)


# --- the overall board (pure) ------------------------------------------------------------------

def brow(account_id, points, rank, when=1, name=None):
    return {"account_id": account_id, "username": name or f"u{account_id}", "kit": None,
            "points": points, "rank": rank, "finished_at": when}


def test_the_overall_board_is_a_share_of_the_days_best_in_each_game():
    boards = {"bingo": [brow(1, 200, 1), brow(2, 100, 2)],
              "guess": [brow(2, 800, 1), brow(3, 400, 2)],
              "xi": [], "common": []}
    out = puzzle_results.combine(boards)
    by = {r["account_id"]: r for r in out}
    assert by[1]["total"] == 100 and by[2]["total"] == 50 + 100 and by[3]["total"] == 50
    assert [r["account_id"] for r in out] == [2, 1, 3] and [r["rank"] for r in out] == [1, 2, 3]
    assert by[2]["games"]["bingo"] == {"points": 100, "share": 50, "rank": 2} and by[2]["played"] == 2


def test_a_game_nobody_scored_in_is_worth_nothing_not_a_division_by_zero():
    out = puzzle_results.combine({"guess": [brow(1, 0, 1), brow(2, 0, 2)], "bingo": [brow(1, 50, 1)]})
    assert {r["account_id"]: r["total"] for r in out} == {1: 100, 2: 0}


def test_equal_totals_go_to_whoever_played_more_games_then_finished_first():
    boards = {"bingo": [brow(1, 100, 1, when=5), brow(2, 100, 1, when=2), brow(3, 100, 1, when=9)],
              "guess": [brow(3, 0, 1, when=9)]}
    out = puzzle_results.combine(boards)
    assert [r["account_id"] for r in out] == [3, 2, 1]          # 3 played two games; then 2 finished before 1


def test_an_empty_day_has_an_empty_overall_board():
    assert puzzle_results.combine({g: [] for g in pr.GAMES}) == []


# --- the routes ----------------------------------------------------------------------------------

@pytest.fixture
def client(pg, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-only-secret-do-not-use-in-real-life")
    monkeypatch.setattr(bingo_routes, "today", lambda: TODAY)

    @contextlib.contextmanager
    def one_connection():
        yield pg

    monkeypatch.setattr(db, "connection", one_connection)
    app = FastAPI()
    app.include_router(puzzle_routes.router)
    return TestClient(app)


def sign_in(client, pg, name="ava") -> int:
    a = account(pg, name)
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(a))
    return a


def bingo_body(facts, seed=SEED):
    grid, sol = _bingo_solution(facts, seed)
    return {"game": "bingo", "seed": seed, "cells": [{"cell": c, "id": sol[c]} for c in range(9)]}


@needs_pg
@needs_facts
def test_a_signed_out_visitor_is_told_to_sign_in_for_every_ranked_route(client, facts):
    assert client.post("/api/puzzles/submit", json=bingo_body(facts)).status_code == 401
    assert client.get("/api/ground/ranked").status_code == 401
    assert client.post("/api/ground/ranked/start").status_code == 401
    assert client.post("/api/ground/ranked/play", json={}).status_code == 401


@needs_pg
@needs_facts
def test_submitting_a_finished_grid_records_it_and_reports_the_rank(client, pg, facts):
    a = sign_in(client, pg)
    r = client.post("/api/puzzles/submit", json=bingo_body(facts)).json()
    grid, _ = _bingo_solution(facts, SEED)
    assert r["recorded"] is True and r["solved"] is True and r["rank"] == 1 and r["of"] == 1
    assert r["score"] == sum(grid.points(c) for c in range(9)) and r["date"] == "2026-10-09"
    assert pg.execute("select count(*) from puzzle_picks where voter = %s", (f"a:{a}",)).fetchone()[0] == 9


@needs_pg
@needs_facts
def test_a_second_submission_reports_the_first_and_does_not_rewrite_it(client, pg, facts):
    sign_in(client, pg)
    first = client.post("/api/puzzles/submit", json=bingo_body(facts)).json()
    grid, sol = _bingo_solution(facts, SEED)
    misses = [next(p for p in sorted(facts.players) if p not in grid.cells[c]) for c in range(9)]
    worse = {"game": "bingo", "seed": SEED, "cells": [{"cell": c, "id": misses[c]} for c in range(9)]}
    again = client.post("/api/puzzles/submit", json=worse).json()
    assert again["recorded"] is False and again["score"] == first["score"] and again["solved"] is True


@needs_pg
@needs_facts
def test_the_server_scores_the_moves_and_nothing_the_page_says_about_them(client, pg, facts):
    """There is no score field to send; and moves that do not replay are refused outright."""
    sign_in(client, pg)
    body = bingo_body(facts)
    r = client.post("/api/puzzles/submit", json={**body, "score": 99999})
    assert r.status_code == 200 and r.json()["score"] != 99999
    assert client.post("/api/puzzles/submit", json={**body, "cells": [{"cell": 0, "id": "nobody"}]}).status_code == 400


@needs_pg
@needs_facts
def test_an_unfinished_a_practice_an_old_and_a_future_puzzle_are_all_refused(client, pg, facts):
    sign_in(client, pg)
    body = bingo_body(facts)
    assert client.post("/api/puzzles/submit", json={**body, "cells": body["cells"][:3]}).status_code == 400
    for seed in (bingo.PRACTICE_SEED_FLOOR + 1, SEED - 5, SEED + 3):
        r = client.post("/api/puzzles/submit", json={**body, "seed": seed})
        assert r.status_code == 400, seed
    assert pg.execute("select count(*) from puzzle_results").fetchone()[0] == 0


@needs_pg
@needs_facts
def test_guess_and_xi_results_over_http(client, pg, facts, sides):
    a = sign_in(client, pg)
    answer = guess_game.mystery(facts.players, SEED)
    r = client.post("/api/puzzles/submit", json={"game": "guess", "seed": SEED, "ids": [answer.person_id]}).json()
    assert r["recorded"] and r["solved"] and r["score"] == 800
    side = xi_game.pick_side(sides, SEED)
    ids = [q.person_id for q in side.players]
    r = client.post("/api/puzzles/submit", json={"game": "xi", "seed": SEED, "ids": ids}).json()
    assert r["recorded"] and r["solved"] and r["score"] == 100 * len(ids) + 100
    games = {row[0] for row in pg.execute("select game from puzzle_results where account_id = %s", (a,))}
    assert games == {"guess", "xi"}


@needs_pg
@needs_facts
def test_common_ground_ranked_over_http_from_start_to_finish(client, pg, real):
    a = sign_in(client, pg)
    assert client.get("/api/ground/ranked").json() == {"started": False, "finished": False, "late": False,
                                                       "remaining_ms": 0, "state": None, "rank": None, "of": 0}
    assert client.post("/api/ground/ranked/play", json={"guess": "x"}).status_code == 409    # no Start yet
    started = client.post("/api/ground/ranked/start").json()
    assert started["started"] and not started["finished"] and started["remaining_ms"] > 235_000
    pg.execute("update puzzle_results set started_at = now() - interval '45 seconds' where account_id = %s", (a,))
    resumed = client.get("/api/ground/ranked").json()
    assert 190_000 <= resumed["remaining_ms"] <= 196_000                     # the server's clock, not the page's
    puzzle = real.puzzle(SEED)
    answers = sorted(puzzle.answers)
    first = client.post("/api/ground/ranked/play", json={"guess": answers[0]}).json()
    assert first["hit"] is True and first["state"]["score"]["found"] == 1
    miss = next(p for p in sorted(real.players) if p not in puzzle.answers and p not in (puzzle.a, puzzle.b))
    wrong = client.post("/api/ground/ranked/play", json={"guess": miss}).json()
    assert wrong["hit"] is False and wrong["kind"] in ("only_a", "only_b", "neither")
    for pid in answers[1:]:
        done = client.post("/api/ground/ranked/play", json={"guess": pid}).json()
    assert done["finished"] and done["state"]["reason"] == "all" and done["rank"] == 1 and done["of"] == 1
    assert done["state"]["score"]["wrong"] == 1 and done["state"]["share"]
    again = client.post("/api/ground/ranked/play", json={"guess": answers[0]}).json()
    assert again["finished"] and again["hit"] is None                        # nothing more is taken


@needs_pg
@needs_facts
def test_a_refused_ranked_move_is_a_400_and_costs_nothing(client, pg, real):
    sign_in(client, pg)
    client.post("/api/ground/ranked/start")
    puzzle = real.puzzle(SEED)
    r = client.post("/api/ground/ranked/play", json={"guess": puzzle.a})
    assert r.status_code == 400 and "one of the two" in r.json()["detail"]
    assert client.get("/api/ground/ranked").json()["state"]["wrong"] == []


@needs_pg
def test_a_board_is_public_ranked_and_marks_the_caller(client, pg):
    ids = {n: account(pg, n) for n in ("ava", "ben", "cyrus")}
    for n, score in (("ava", 300), ("ben", 900), ("cyrus", 600)):
        puzzle_results.record(pg, ids[n], TODAY, outcome(score=score, picks=[pr.Pick("0", "p", 5)]), [])
    anon = client.get("/api/puzzles/board/bingo").json()
    assert [r["username"] for r in anon["rows"]] == ["ben", "cyrus", "ava"] and anon["finished"] == 3
    assert anon["date"] == "2026-10-09" and anon["label"] == "Bingo" and not any(r["you"] for r in anon["rows"])
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(ids["cyrus"]))
    mine = client.get("/api/puzzles/board/bingo").json()
    assert [r["you"] for r in mine["rows"]] == [False, True, False]
    assert mine["rows"][0]["points"] == 900 and mine["rows"][0]["found"] == 9


@needs_pg
def test_a_player_outside_the_top_rows_is_still_shown_as_themselves(client, pg, monkeypatch):
    monkeypatch.setattr(puzzle_routes, "BOARD_TOP", 2)
    ids = [account(pg, f"player{i}") for i in range(4)]
    for i, who in enumerate(ids):
        puzzle_results.record(pg, who, TODAY, outcome(score=100 * (i + 1)), [])
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(ids[0]))
    out = client.get("/api/puzzles/board/bingo").json()
    assert len(out["rows"]) == 2 and out["finished"] == 4
    assert out["me"]["rank"] == 4 and out["me"]["you"] is True
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(ids[3]))
    assert client.get("/api/puzzles/board/bingo").json()["me"] is None       # already in the rows


@needs_pg
def test_the_overall_board_over_http(client, pg):
    a, b = account(pg, "ava"), account(pg, "ben")
    puzzle_results.record(pg, a, TODAY, outcome(game="bingo", score=200), [])
    puzzle_results.record(pg, b, TODAY, outcome(game="bingo", score=100), [])
    puzzle_results.record(pg, b, TODAY, outcome(game="guess", score=800), [])
    puzzle_results.record(pg, a, TODAY, outcome(game="xi", score=1000), [])
    puzzle_results.record(pg, b, TODAY, outcome(game="common", score=600, elapsed=80_000), [])
    out = client.get("/api/puzzles/board/overall").json()
    assert [(r["username"], r["points"], r["played"]) for r in out["rows"]] == [("ben", 250, 3), ("ava", 200, 2)]
    assert set(out["rows"][0]["games"]) == {"bingo", "guess", "common"} and set(out["rows"][1]["games"]) == {"bingo", "xi"}
    assert out["rows"][0]["games"]["guess"]["share"] == 100 and out["label"] == "Overall"


@needs_pg
def test_a_past_days_board_is_kept_and_an_unknown_or_future_one_is_refused(client, pg):
    a = account(pg, "ava")
    yesterday = TODAY - datetime.timedelta(days=1)
    puzzle_results.record(pg, a, yesterday, outcome(score=77), [])
    out = client.get(f"/api/puzzles/board/bingo?date={yesterday.isoformat()}").json()
    assert out["rows"][0]["points"] == 77 and out["date"] == yesterday.isoformat()
    assert client.get("/api/puzzles/board/bingo").json()["rows"] == []
    assert client.get("/api/puzzles/board/chess").status_code == 404
    assert client.get("/api/puzzles/board/bingo?date=2099-01-01").status_code == 400
    assert client.get("/api/puzzles/board/bingo?date=tomorrow").status_code == 400
