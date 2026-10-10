"""Rarity (A191): the pure rules, and their wiring into results, boards and the signed-out picks.

The rules are integer arithmetic on counts, so every number below is worked by hand. The wiring
runs against a real scratch Postgres (opt-in, like tests/test_puzzle_results.py); each test is a
rolled-back transaction.
"""

from __future__ import annotations

import contextlib
import datetime
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from game import bingo, puzzle_facts, puzzle_results as pr, rarity
from game.rarity import Counts
from web import auth, bingo_routes, db, puzzle_results, puzzle_routes

TODAY = datetime.date(2026, 10, 9)
SEED = TODAY.toordinal()
DEVICE = "0123456789abcdef" * 2


def counts(game="bingo", *, slot="0", chose=1, den=20, others=0):
    """`den` voters filled `slot`, `chose` of them with player 'p'; `others` more elsewhere."""
    c = Counts()
    c.by_pick[(slot, "p")] = chose
    c.by_slot[slot] = den
    c.voters = den + others
    return c


# --- the rules -------------------------------------------------------------------------------

def test_no_rarity_until_a_slot_has_enough_voters():
    for den in (1, 5, rarity.MIN_VOTERS - 1):
        assert rarity.bonus_for("bingo", "0", "p", counts(chose=1, den=den)) == 0
        assert rarity.share("bingo", "0", "p", counts(chose=1, den=den)) is None
    assert rarity.bonus_for("bingo", "0", "p", counts(chose=1, den=rarity.MIN_VOTERS)) > 0
    assert rarity.share("bingo", "0", "p", counts(chose=1, den=rarity.MIN_VOTERS)) == 0.05


def test_a_pick_nobody_else_made_earns_almost_the_most_and_a_universal_one_nothing():
    assert rarity.bonus_for("bingo", "0", "p", counts(chose=1, den=20)) == 48         # 50 * 19/20 = 47.5, half up
    assert rarity.bonus_for("bingo", "0", "p", counts(chose=20, den=20)) == 0
    assert rarity.bonus_for("bingo", "0", "p", counts(chose=10, den=20)) == 25
    assert rarity.bonus_for("bingo", "0", "p", counts(chose=1, den=1000)) == 50       # a crowd: the bonus tops out at the cap


def test_the_rounding_is_half_up_not_bankers():
    # 3 of 20 chose him: 50 * 17/20 = 42.5 -> 43. Python's round() gives 42.
    assert rarity.bonus_for("bingo", "0", "p", counts(chose=3, den=20)) == 43
    assert rarity.bonus_for("bingo", "0", "p", counts(chose=5, den=20)) == 38         # 37.5 -> 38


def test_a_pick_with_no_recorded_votes_is_treated_as_unique_not_as_an_error():
    assert rarity.bonus_for("bingo", "0", "nobody", counts(chose=1, den=20)) == 50


def test_a_count_larger_than_its_denominator_cannot_make_a_negative_bonus():
    assert rarity.bonus_for("bingo", "0", "p", counts(chose=25, den=20)) == 0


def test_bingo_is_measured_against_the_cell_and_the_other_games_against_the_day():
    c = Counts()
    c.by_pick[("3", "p")] = 4
    c.by_slot["3"] = 25                      # 25 filled cell 3, so there are plenty to compare with
    c.voters = 40
    assert rarity.denominator("bingo", "3", c) == 25
    assert rarity.denominator("bingo", "4", c) == 0 and rarity.bonus_for("bingo", "4", "p", c) == 0   # nobody filled cell 4
    for game in ("xi", "common"):
        assert rarity.denominator(game, "3", c) == 40


def test_fixed_answer_games_share_is_the_part_of_the_day_who_found_him():
    c = Counts()
    c.by_pick[("p", "p")] = 8
    c.voters = 40
    assert rarity.share("common", "p", "p", c) == 0.2
    assert rarity.bonus_for("common", "p", "p", c) == 40                            # 50 * 32/40


def test_the_whole_result_is_the_sum_of_its_picks():
    c = Counts()
    c.by_pick.update({("0", "a"): 1, ("1", "b"): 20})
    c.by_slot.update({"0": 20, "1": 20})
    assert rarity.total_bonus("bingo", [["0", "a", 58], ["1", "b", 24]], c) == 48 + 0
    assert rarity.total_bonus("bingo", [], c) == 0


def test_the_stored_base_never_enters_the_bonus():
    c = counts(chose=1, den=20)
    assert rarity.total_bonus("bingo", [["0", "p", 0]], c) == rarity.total_bonus("bingo", [["0", "p", 999]], c)


# --- the wiring: a real scratch Postgres ------------------------------------------------------

SCRATCH = os.environ.get("IPLEGENDS_SCRATCH_DB")
needs_pg = pytest.mark.skipif(not SCRATCH, reason="set IPLEGENDS_SCRATCH_DB to a throwaway, fully migrated database")
DOC = puzzle_facts.read_document()
needs_facts = pytest.mark.skipif(DOC is None, reason="puzzle facts not committed")


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


def crowd(pg, game, slot, person_id, n, day=TODAY, prefix="a"):
    """n device voters who each made this one pick."""
    for i in range(n):
        voter = "d:" + f"{prefix}{i:031x}"[:32]
        pg.execute("insert into puzzle_picks (game, day, slot, voter, person_id) values (%s, %s, %s, %s, %s)",
                   (game, day, slot, voter, person_id))


def outcome(game="bingo", score=100, picks=()):
    return pr.Outcome(game, score, True, 9, 0, 9, 9, tuple(picks))


@needs_pg
def test_counts_add_up_the_days_picks_by_pick_by_slot_and_by_voter(pg):
    crowd(pg, "bingo", "0", "a", 3)
    crowd(pg, "bingo", "0", "b", 2, prefix="b")
    crowd(pg, "bingo", "1", "a", 1, prefix="c")
    crowd(pg, "bingo", "0", "z", 5, day=TODAY - datetime.timedelta(days=1), prefix="d")   # another day
    c = puzzle_results.counts(pg, "bingo", TODAY)
    assert c.by_pick == {("0", "a"): 3, ("0", "b"): 2, ("1", "a"): 1}
    assert c.by_slot == {"0": 5, "1": 1} and c.voters == 6
    assert puzzle_results.counts(pg, "xi", TODAY).voters == 0


@needs_pg
def test_a_voter_in_several_slots_is_one_voter(pg):
    a = account(pg, "ava")
    puzzle_results.record(pg, a, TODAY, outcome(picks=[pr.Pick("0", "p"), pr.Pick("1", "q"), pr.Pick("2", "r")]), [])
    assert puzzle_results.counts(pg, "bingo", TODAY).voters == 1


@needs_pg
def test_the_board_adds_the_rarity_bonus_and_it_can_change_who_wins(pg):
    a, b = account(pg, "ava"), account(pg, "ben")
    crowd(pg, "bingo", "0", "common-pick", 25)
    puzzle_results.record(pg, a, TODAY, outcome(score=200, picks=[pr.Pick("0", "common-pick", 50)]), [])
    puzzle_results.record(pg, b, TODAY, outcome(score=200, picks=[pr.Pick("0", "rare-pick", 50)]), [])
    rows = puzzle_results.board(pg, "bingo", TODAY)
    by = {r["username"]: r for r in rows}
    assert by["ben"]["bonus"] == 48 and by["ben"]["points"] == 248         # 1 of 26 chose him
    assert by["ava"]["bonus"] == 2 and by["ava"]["points"] == 202          # 26 of 27 chose his
    assert [r["username"] for r in rows] == ["ben", "ava"]                 # level on base, rarity decides


@needs_pg
def test_a_small_sample_leaves_every_score_exactly_its_base(pg):
    a = account(pg, "ava")
    puzzle_results.record(pg, a, TODAY, outcome(score=321, picks=[pr.Pick("0", "p", 50)]), [])
    row = puzzle_results.board(pg, "bingo", TODAY)[0]
    assert row["bonus"] == 0 and row["points"] == row["score"] == 321


@needs_pg
def test_a_game_that_counts_no_picks_never_gets_a_bonus(pg):
    a = account(pg, "ava")
    puzzle_results.record(pg, a, TODAY, outcome(game="guess", score=800), [])
    assert puzzle_results.board(pg, "guess", TODAY)[0]["bonus"] == 0


@needs_pg
def test_a_signed_out_browsers_picks_count_once_and_a_repeat_changes_nothing(pg):
    picks = [pr.Pick("0", "p"), pr.Pick("1", "q")]
    voter = puzzle_results.device_voter(DEVICE)
    assert puzzle_results.record_device_picks(pg, "bingo", TODAY, voter, picks) is True
    assert puzzle_results.record_device_picks(pg, "bingo", TODAY, voter, picks) is False
    assert pg.execute("select count(*) from puzzle_picks").fetchone()[0] == 2


@needs_pg
def test_signing_in_after_playing_signed_out_counts_the_person_once(pg):
    voter = puzzle_results.device_voter(DEVICE)
    puzzle_results.record_device_picks(pg, "bingo", TODAY, voter, [pr.Pick("0", "p")])
    a = account(pg, "ava")
    puzzle_results.record(pg, a, TODAY, outcome(picks=[pr.Pick("0", "p", 50)]), [], device_id=DEVICE)
    assert pg.execute("select voter from puzzle_picks").fetchall() == [(f"a:{a}",)]
    other = "f" * 32                                   # a different browser's picks are not touched
    puzzle_results.record_device_picks(pg, "bingo", TODAY, f"d:{other}", [pr.Pick("0", "q")])
    b = account(pg, "ben")
    puzzle_results.record(pg, b, TODAY, outcome(picks=[pr.Pick("0", "q", 50)]), [], device_id=DEVICE)
    assert {r[0] for r in pg.execute("select voter from puzzle_picks")} == {f"a:{a}", f"a:{b}", f"d:{other}"}


def test_a_device_id_must_be_exactly_32_lowercase_hex():
    for bad in (None, "", "abc", "g" * 32, "A" * 32, "0" * 31, "0" * 33):
        with pytest.raises(pr.PuzzleResultError):
            puzzle_results.device_voter(bad)
    assert puzzle_results.device_voter(DEVICE) == "d:" + DEVICE


# --- the routes ------------------------------------------------------------------------------

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


@pytest.fixture(scope="module")
def facts():
    return bingo.Facts(puzzle_facts.players_from(DOC))


def solution(facts):
    from tests.test_puzzle_results import _bingo_solution
    return _bingo_solution(facts, SEED)


@needs_pg
@needs_facts
def test_a_signed_out_finish_is_counted_and_reports_how_rare_it_was(client, pg, facts):
    grid, sol = solution(facts)
    body = {"game": "bingo", "seed": SEED, "cells": [{"cell": c, "id": sol[c]} for c in range(9)]}
    r = client.post("/api/puzzles/picks", json=body, headers={"X-Daily-Device": DEVICE})
    assert r.status_code == 200 and r.json()["counted"] is True
    out = r.json()["rarity"]
    assert out["voters"] == 1 and out["enough"] is False and out["bonus"] == 0 and len(out["picks"]) == 9
    assert all(p["share"] is None for p in out["picks"]) and out["picks"][0]["name"]
    again = client.post("/api/puzzles/picks", json=body, headers={"X-Daily-Device": DEVICE}).json()
    assert again["counted"] is False
    assert pg.execute("select count(*) from puzzle_picks").fetchone()[0] == 9


@needs_pg
@needs_facts
def test_a_signed_in_player_is_never_counted_twice_by_the_picks_route(client, pg, facts):
    grid, sol = solution(facts)
    a = account(pg, "ava")
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(a))
    body = {"game": "bingo", "seed": SEED, "cells": [{"cell": c, "id": sol[c]} for c in range(9)]}
    r = client.post("/api/puzzles/picks", json=body, headers={"X-Daily-Device": DEVICE}).json()
    assert r["counted"] is False and r["rarity"]["voters"] == 0
    assert pg.execute("select count(*) from puzzle_picks").fetchone()[0] == 0


@needs_pg
@needs_facts
def test_the_picks_route_refuses_a_missing_device_a_forgery_and_the_wrong_day(client, pg, facts):
    grid, sol = solution(facts)
    body = {"game": "bingo", "seed": SEED, "cells": [{"cell": c, "id": sol[c]} for c in range(9)]}
    assert client.post("/api/puzzles/picks", json=body).status_code == 400                         # no device
    assert client.post("/api/puzzles/picks", json=body, headers={"X-Daily-Device": "nope"}).status_code == 400
    forged = {**body, "cells": [{"cell": 0, "id": "nobody"}]}
    assert client.post("/api/puzzles/picks", json=forged, headers={"X-Daily-Device": DEVICE}).status_code == 400
    part = {**body, "cells": body["cells"][:3]}
    assert client.post("/api/puzzles/picks", json=part, headers={"X-Daily-Device": DEVICE}).status_code == 400
    for seed in (bingo.PRACTICE_SEED_FLOOR + 1, SEED - 4, SEED + 2):
        assert client.post("/api/puzzles/picks", json={**body, "seed": seed},
                           headers={"X-Daily-Device": DEVICE}).status_code == 400, seed
    assert client.post("/api/puzzles/picks", json={**body, "game": "guess"},
                       headers={"X-Daily-Device": DEVICE}).status_code == 422                       # guess has no picks
    assert pg.execute("select count(*) from puzzle_picks").fetchone()[0] == 0


@needs_pg
@needs_facts
def test_once_a_cell_has_a_crowd_a_rare_answer_scores_its_bonus_end_to_end(client, pg, facts):
    grid, sol = solution(facts)
    alt = next(p for p in sorted(grid.cells[0]) if p != sol[0])
    crowd(pg, "bingo", "0", alt, 22)                         # 22 other players all chose the same answer
    a = account(pg, "ava")
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(a))
    body = {"game": "bingo", "seed": SEED, "cells": [{"cell": c, "id": sol[c]} for c in range(9)]}
    r = client.post("/api/puzzles/submit", json=body).json()
    expected = rarity.bonus_for("bingo", "0", sol[0], puzzle_results.counts(pg, "bingo", TODAY))
    assert expected == 48                                    # 1 of 23 chose it: 50 * 22/23 = 47.8
    assert r["bonus"] == 48 and r["points"] == r["score"] + 48 and r["rarity"]["enough"] is True
    first = next(p for p in r["rarity"]["picks"] if p["slot"] == "0")
    assert first["bonus"] == 48 and first["share"] == pytest.approx(1 / 23)
    assert all(p["bonus"] == 0 for p in r["rarity"]["picks"] if p["slot"] != "0")    # one voter each: no sample


@needs_pg
@needs_facts
def test_a_repeat_submission_reports_the_stored_picks_not_the_new_ones(client, pg, facts):
    grid, sol = solution(facts)
    a = account(pg, "ava")
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(a))
    body = {"game": "bingo", "seed": SEED, "cells": [{"cell": c, "id": sol[c]} for c in range(9)]}
    first = client.post("/api/puzzles/submit", json=body).json()
    misses = [next(p for p in sorted(facts.players) if p not in grid.cells[c]) for c in range(9)]
    second = client.post("/api/puzzles/submit", json={**body, "cells": [{"cell": c, "id": misses[c]} for c in range(9)]}).json()
    assert second["recorded"] is False and second["score"] == first["score"]
    assert [p["person_id"] for p in second["rarity"]["picks"]] == [p["person_id"] for p in first["rarity"]["picks"]]
