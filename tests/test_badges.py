"""Puzzle badges (A188): every badge's rule on hand-built results, the streak arithmetic, and the
SQL that finds a player's rare picks -- checked against `game.rarity` on real rows.

The rules are pure, so most of this needs no database; the rare-pick query and the route are SQL
and run against a real scratch Postgres (opt-in, like tests/test_puzzle_results.py).
"""

from __future__ import annotations

import contextlib
import datetime
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from game import badges as B
from game import puzzle_results as pr
from game import rarity
from web import auth, bingo_routes, db, puzzle_results, puzzle_routes

D = datetime.date
TODAY = D(2026, 10, 9)


def res(game="bingo", day=TODAY, score=100, solved=True, elapsed=None, **detail):
    return B.Result(game, day, score, solved, elapsed, detail)


def badge(results, key, rare=()):
    return next(b for b in B.evaluate(results, rare) if b.key == key)


# --- streaks ---------------------------------------------------------------------------------

def test_a_run_is_consecutive_calendar_days_not_consecutive_days_of_the_month():
    assert B.best_run([D(2026, 8, 31), D(2026, 9, 1)]) == 2            # 31 -> 1 is one day apart
    assert B.best_run([D(2026, 1, 30), D(2026, 2, 1)]) == 1            # 30 -> 1 is two
    assert B.best_run([]) == 0
    assert B.best_run([TODAY, TODAY]) == 1                              # a day counts once however many puzzles


def test_the_best_run_is_the_longest_not_the_latest():
    days = [D(2026, 3, d) for d in (1, 2, 3, 4, 5)] + [D(2026, 3, 9), D(2026, 3, 10)]
    assert B.best_run(days) == 5


def test_a_run_is_first_reached_on_the_day_it_is_completed():
    days = [D(2026, 3, d) for d in range(1, 11)]
    assert B.run_reached(days, 7) == D(2026, 3, 7)
    assert B.run_reached(days, 10) == D(2026, 3, 10) and B.run_reached(days, 11) is None
    assert B.run_reached([D(2026, 3, 1), D(2026, 3, 3)], 2) is None


def test_the_streak_badges_need_the_full_run_and_show_progress_until_then():
    six = [res(day=D(2026, 3, d)) for d in range(1, 7)]
    wk = badge(six, "streak_7")
    assert not wk.earned and wk.progress == 6 and wk.target == 7 and wk.earned_on is None
    seven = six + [res(day=D(2026, 3, 7))]
    wk = badge(seven, "streak_7")
    assert wk.earned and wk.earned_on == D(2026, 3, 7) and wk.progress == 7
    assert not badge(seven, "streak_30").earned and badge(seven, "streak_30").progress == 7


def test_a_streak_broken_by_a_missed_day_does_not_carry_over():
    days = [res(day=D(2026, 3, d)) for d in (1, 2, 3, 4, 5, 6, 8, 9)]
    assert not badge(days, "streak_7").earned and badge(days, "streak_7").progress == 6


def test_any_puzzle_counts_toward_a_streak():
    mixed = [res(game=g, day=D(2026, 3, i + 1)) for i, g in enumerate(B.GAMES * 2)]
    assert badge(mixed, "streak_7").progress == 7 and badge(mixed, "streak_7").earned


# --- the single-feat badges ------------------------------------------------------------------

def test_a_perfect_grid_is_a_full_grid_with_nothing_wrong():
    assert badge([res("bingo", solved=True, wrong=0)], "perfect_grid").earned
    assert not badge([res("bingo", solved=True, wrong=1)], "perfect_grid").earned
    assert not badge([res("bingo", solved=False, wrong=0)], "perfect_grid").earned
    assert not badge([res("xi", solved=True, wrong=0)], "perfect_grid").earned          # another game's result


def test_a_sharp_eye_finds_him_in_two_guesses_or_fewer():
    assert badge([res("guess", guesses=1)], "sharp_eye").earned
    assert badge([res("guess", guesses=2)], "sharp_eye").earned
    assert not badge([res("guess", guesses=3)], "sharp_eye").earned
    assert not badge([res("guess", solved=False, guesses=2)], "sharp_eye").earned       # two wrong guesses is not finding him


def test_a_full_house_needs_the_whole_set_and_lightning_needs_it_fast():
    assert badge([res("common", solved=True, elapsed=200_000)], "full_house").earned
    assert not badge([res("common", solved=False, elapsed=10_000)], "full_house").earned
    assert badge([res("common", solved=True, elapsed=B.LIGHTNING_MS - 1)], "lightning").earned
    assert not badge([res("common", solved=True, elapsed=B.LIGHTNING_MS)], "lightning").earned     # exactly 90 s is not under it
    assert not badge([res("common", solved=False, elapsed=1_000)], "lightning").earned
    assert not badge([res("common", solved=True, elapsed=None)], "lightning").earned


def test_the_full_xi_and_the_flawless_xi():
    assert badge([res("xi", solved=True, wrong=3)], "full_xi").earned
    assert not badge([res("xi", solved=True, wrong=3)], "flawless_xi").earned
    assert badge([res("xi", solved=True, wrong=0)], "flawless_xi").earned
    assert not badge([res("xi", solved=False, wrong=0)], "full_xi").earned


def test_a_badge_is_earned_on_the_first_day_it_was_won_not_the_latest():
    rs = [res("bingo", day=D(2026, 5, 9), wrong=0), res("bingo", day=D(2026, 3, 2), wrong=0),
          res("bingo", day=D(2026, 7, 1), wrong=0)]
    assert badge(rs, "perfect_grid").earned_on == D(2026, 3, 2)


def test_a_grand_slam_is_all_four_on_one_day():
    four = [res(game=g, day=D(2026, 3, 4)) for g in B.GAMES]
    assert badge(four, "grand_slam").earned_on == D(2026, 3, 4)
    assert not badge(four[:3], "grand_slam").earned
    spread = [res(game=g, day=D(2026, 3, i + 1)) for i, g in enumerate(B.GAMES)]
    assert not badge(spread, "grand_slam").earned


def test_the_habit_badges_count_every_finished_puzzle_and_are_earned_on_the_nth():
    rs = [res(game=B.GAMES[i % 4], day=D(2026, 1, 1) + datetime.timedelta(days=i // 4)) for i in range(B.REGULAR_AT)]
    reg = badge(rs, "regular")
    assert reg.earned and reg.earned_on == rs[-1].day and reg.progress == B.REGULAR_AT
    assert not badge(rs[:-1], "regular").earned and badge(rs[:-1], "regular").progress == B.REGULAR_AT - 1
    assert not badge(rs, "veteran").earned and badge(rs, "veteran").progress == B.REGULAR_AT


def test_against_the_grain_is_earned_on_the_first_rare_day():
    assert badge([], "against_the_grain", rare=[D(2026, 4, 2), D(2026, 3, 9)]).earned_on == D(2026, 3, 9)
    assert not badge([], "against_the_grain").earned


# --- the whole set ---------------------------------------------------------------------------

def test_nothing_is_earned_without_results_and_every_badge_is_listed_once_in_a_fixed_order():
    out = B.evaluate([])
    assert not any(b.earned for b in out) and all(b.progress == 0 for b in out)
    keys = [b.key for b in out]
    assert len(keys) == len(set(keys)) == 13
    assert keys == [b.key for b in B.evaluate([res()])]
    assert {b.group for b in out} == {"Streaks", "Mastery", "Habit", "Rare"}


def test_progress_never_exceeds_the_target_and_an_earned_badge_is_full():
    rs = [res(day=D(2026, 1, 1) + datetime.timedelta(days=i), wrong=0) for i in range(40)]
    rs += [res("guess", guesses=1), res("common", solved=True, elapsed=1000), res("xi", wrong=0)]
    out = B.evaluate(rs, [D(2026, 1, 1)])
    assert sum(b.earned for b in out) >= 9                      # enough earned to make the rule bite
    for b in out:
        assert 0 <= b.progress <= b.target
        if b.earned:
            assert b.progress == b.target, b.key


def test_the_blurbs_state_the_thresholds_the_rules_use():
    by = {b.key: b.blurb for b in B.evaluate([])}
    assert str(B.LIGHTNING_MS // 1000) in by["lightning"] and str(B.SHARP_EYE_GUESSES) in by["sharp_eye"]
    assert str(B.REGULAR_AT) in by["regular"] and str(B.VETERAN_AT) in by["veteran"]
    assert str(B.RARE_SHARE_ONE_IN) in by["against_the_grain"]
    assert all(str(n) in by[f"streak_{n}"] for n in B.STREAKS)


# --- SQL: a real scratch Postgres --------------------------------------------------------------

SCRATCH = os.environ.get("IPLEGENDS_SCRATCH_DB")
needs_pg = pytest.mark.skipif(not SCRATCH, reason="set IPLEGENDS_SCRATCH_DB to a throwaway, fully migrated database")


@pytest.fixture
def pg():
    import psycopg
    conn = psycopg.connect(SCRATCH)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def account(pg, name):
    return pg.execute("insert into accounts (username, email, password_hash) values (%s, %s, 'x') "
                      "returning account_id", (name, f"{name}@example.com")).fetchone()[0]


def pick(pg, game, day, slot, voter, person_id):
    pg.execute("insert into puzzle_picks (game, day, slot, voter, person_id) values (%s, %s, %s, %s, %s)",
               (game, day, slot, voter, person_id))


def devices(n, prefix):
    return ["d:" + f"{prefix}{i:031x}"[:32] for i in range(n)]


def outcome(game="bingo", score=100, solved=True, wrong=0, elapsed=None, picks=()):
    return pr.Outcome(game, score, solved, 9 + wrong, wrong, 9, 9 if solved else 3, tuple(picks), elapsed_ms=elapsed)


@needs_pg
def test_account_results_are_this_accounts_finished_results_only_oldest_first(pg):
    a, b = account(pg, "ava"), account(pg, "ben")
    for day, game in ((TODAY, "xi"), (TODAY - datetime.timedelta(days=3), "bingo")):
        puzzle_results.record(pg, a, day, outcome(game=game, wrong=2), [])
    puzzle_results.record(pg, b, TODAY, outcome(game="guess"), [])
    puzzle_results.ranked_start(pg, a, TODAY)                               # open: not a result yet
    rows = puzzle_results.account_results(pg, a)
    assert [(r.game, r.day) for r in rows] == [("bingo", TODAY - datetime.timedelta(days=3)), ("xi", TODAY)]
    assert rows[0].detail["wrong"] == 2 and rows[0].solved is True


@needs_pg
def test_rare_pick_days_agree_with_the_rarity_rules_on_real_rows(pg):
    """The SQL writes `game.rarity`'s counting rule a second time; this builds boundary cases and
    asserts the two answer alike."""
    a = account(pg, "ava")
    voter = f"a:{a}"
    d = lambda n: TODAY - datetime.timedelta(days=n)
    # day 0: Bingo cell 0, 40 voters, ava's pick made by exactly 2 -> 5%: on the line, rare
    for v in devices(38, "a"):
        pick(pg, "bingo", d(0), "0", v, "popular")
    pick(pg, "bingo", d(0), "0", voter, "niche"); pick(pg, "bingo", d(0), "0", devices(1, "b")[0], "niche")
    # day 1: the same, but 3 of 40 made it -> 7.5%: not rare
    for v in devices(37, "c"):
        pick(pg, "bingo", d(1), "0", v, "popular")
    pick(pg, "bingo", d(1), "0", voter, "niche")
    for v in devices(2, "d"):
        pick(pg, "bingo", d(1), "0", v, "niche")
    # day 2: Name the XI, 30 voters on the day, ava found a man only 1 of them found -> rare
    for k, v in enumerate(devices(29, "e")):
        pick(pg, "xi", d(2), "0", v, "opener")
    pick(pg, "xi", d(2), "7", voter, "tailender"); pick(pg, "xi", d(2), "0", voter, "opener")
    # day 3: rare by share but only 19 voters: no sample, not rare
    for v in devices(18, "f"):
        pick(pg, "bingo", d(3), "0", v, "popular")
    pick(pg, "bingo", d(3), "0", voter, "niche")
    # day 4: Bingo cell 0 has only 10 voters, but cell 1 the same day has 50 -- a cell is measured
    # against ITS OWN voters, so a niche pick in cell 0 is not rare (10 is no sample), however
    # busy the rest of the grid was
    for v in devices(9, "1"):
        pick(pg, "bingo", d(4), "0", v, "popular")
    pick(pg, "bingo", d(4), "0", voter, "niche")
    for v in devices(50, "2"):
        pick(pg, "bingo", d(4), "1", v, "other")
    got = puzzle_results.rare_pick_days(pg, a)
    assert got == [d(2), d(0)]
    # and the python rule says the same, pick by pick
    expect = set()
    for game, day, slot, pid in pg.execute("select game, day, slot, person_id from puzzle_picks where voter = %s", (voter,)).fetchall():
        c = puzzle_results.counts(pg, game, day)
        s = rarity.share(game, slot, pid, c)
        if s is not None and s <= 1 / B.RARE_SHARE_ONE_IN:
            expect.add(day)
    assert set(got) == expect


@needs_pg
def test_a_rare_share_among_too_few_voters_is_not_rare_however_loose_the_threshold(pg, monkeypatch):
    """With the usual 1-in-20 the sample rule is implied (one pick in 19 is above 5%), so loosen
    the share and check the sample still stands on its own."""
    monkeypatch.setattr(B, "RARE_SHARE_ONE_IN", 5)               # up to one pick in five is 'rare'
    a = account(pg, "ava")
    for v in devices(18, "a"):
        pick(pg, "bingo", TODAY, "0", v, "popular")
    pick(pg, "bingo", TODAY, "0", f"a:{a}", "niche")             # 1 of 19: 5% <= 20%, but 19 is no sample
    assert puzzle_results.rare_pick_days(pg, a) == []
    pick(pg, "bingo", TODAY, "0", devices(1, "9")[0], "popular")  # now 20 voters
    assert puzzle_results.rare_pick_days(pg, a) == [TODAY]


@needs_pg
def test_another_players_rare_picks_are_not_this_players(pg):
    a, b = account(pg, "ava"), account(pg, "ben")
    for v in devices(30, "a"):
        pick(pg, "bingo", TODAY, "0", v, "popular")
    pick(pg, "bingo", TODAY, "0", f"a:{b}", "niche")
    assert puzzle_results.rare_pick_days(pg, a) == [] and puzzle_results.rare_pick_days(pg, b) == [TODAY]


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


@needs_pg
def test_the_record_needs_a_sign_in(client):
    assert client.get("/api/puzzles/me").status_code == 401


@needs_pg
def test_the_record_adds_up_a_players_puzzles_and_their_badges(client, pg):
    a = account(pg, "ava")
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(a))
    for i in range(3):
        day = TODAY - datetime.timedelta(days=i)
        puzzle_results.record(pg, a, day, outcome(game="bingo", score=200 + i, wrong=0), [])
    puzzle_results.record(pg, a, TODAY, outcome(game="common", score=640, wrong=1, elapsed=70_000), [])
    out = client.get("/api/puzzles/me").json()
    assert out["finished"] == 4 and out["days"] == 3 and out["streak"] == 3 and out["best_streak"] == 3
    assert out["games"]["bingo"] == {"played": 3, "solved": 3, "best": 202}
    assert out["games"]["common"] == {"played": 1, "solved": 1, "best": 640} and out["games"]["xi"]["played"] == 0
    by = {b["key"]: b for b in out["badges"]}
    assert by["perfect_grid"]["earned"] and by["perfect_grid"]["earned_on"] == (TODAY - datetime.timedelta(days=2)).isoformat()
    assert by["lightning"]["earned"] and by["full_house"]["earned"] and not by["flawless_xi"]["earned"]
    assert by["streak_7"]["progress"] == 3 and not by["streak_7"]["earned"] and by["streak_7"]["earned_on"] is None
    assert [b["key"] for b in out["badges"]] == [b.key for b in B.evaluate([])]


@needs_pg
def test_a_new_account_has_a_record_of_zeros_and_nothing_earned(client, pg):
    a = account(pg, "ava")
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(a))
    out = client.get("/api/puzzles/me").json()
    assert (out["finished"], out["days"], out["streak"], out["best_streak"]) == (0, 0, 0, 0)
    assert not any(b["earned"] for b in out["badges"])
