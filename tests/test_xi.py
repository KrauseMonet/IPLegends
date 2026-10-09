"""Name the XI (A185): the sides, the clues, the rules, the share line and the routes.

Runs on the committed facts snapshot, no database. The rules are exercised on hand-built
sides where the right answer is obvious by construction; the real sides are checked against
what the public record is certain of (the 2016 final's scorecard, the champions of each
season), because the database is perfectly consistent with a misreading of cricket's own
conventions and only the record can say otherwise (A22).
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from game import puzzle_facts as pf
from game import xi as X
from web import bingo_routes, xi_routes

DOC = pf.read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="puzzle facts not committed")

CHAMPIONS = {2008: "Rajasthan Royals", 2009: "Deccan Chargers", 2010: "Chennai Super Kings",
             2011: "Chennai Super Kings", 2012: "Kolkata Knight Riders", 2013: "Mumbai Indians",
             2014: "Kolkata Knight Riders", 2015: "Mumbai Indians", 2016: "Sunrisers Hyderabad",
             2017: "Mumbai Indians", 2018: "Chennai Super Kings", 2019: "Mumbai Indians",
             2020: "Mumbai Indians", 2021: "Chennai Super Kings", 2022: "Gujarat Titans",
             2023: "Chennai Super Kings", 2024: "Kolkata Knight Riders",
             2025: "Royal Challengers Bengaluru"}


@pytest.fixture(scope="module")
def players():
    return {p.person_id: p for p in pf.players_from(DOC)}


@pytest.fixture(scope="module")
def sides():
    return pf.sides_from(DOC)


def q(pid, pos=None, runs=None, balls=None, out=None, wk=None, conc=None, bb=None):
    return pf.XiPlayer(pid, pos, runs, balls, out, wk, conc, bb)


def make_side(n=11, opp=("o1", "o2"), **kw) -> pf.XiSide:
    base = dict(match_id="m1", fs_id=1, date="2016-05-29", season=2016, kinds=("final",),
                venue="Ground", city="City", team="Team A", team_franchise="Team A",
                opponent="Team B", opponent_franchise="Team B", result="Team A won by 8 runs",
                players=tuple(q(f"p{i}", i + 1, 10 * i, 5 * i, True) for i in range(n)),
                opposition=tuple(opp))
    base.update(kw)
    return pf.XiSide(**base)


@pytest.fixture
def world():
    ids = [f"p{i}" for i in range(13)] + ["o1", "o2", "zz"]
    people = {i: pf_player(i) for i in ids}
    return people, make_side()


def pf_player(pid):
    return pf.PlayerFacts(
        person_id=pid, name=f"Qwerty {pid}", franchises=("Mumbai Indians",), seasons=(2010,),
        overseas=False, keeper=False, runs=0, wickets=0, best_season_runs=0,
        best_season_wickets=0, hundreds=0, fifties=0, four_wicket_hauls=0, sixes=0)


# --- the real sides --------------------------------------------------------------------------

def test_there_are_enough_sides_and_each_is_well_formed(sides, players):
    assert len(sides) >= 200
    for s in sides:
        ids = [p.person_id for p in s.players]
        assert len(ids) == len(set(ids)) and all(i in players for i in ids)
        assert not set(ids) & set(s.opposition)              # nobody is on both sides
        assert set(s.kinds) <= {"final", "century", "super_over"} and s.kinds
        if s.season < pf.FIRST_IMPACT_SEASON:
            assert len(ids) == 11
        else:
            assert 11 <= len(ids) <= 13
        assert [p.position for p in s.players if p.position] == sorted(p.position for p in s.players if p.position)


def test_the_2016_final_matches_the_public_record(sides, players):
    """Warner 69 off 38 and Cutting 39* off 15 for Sunrisers; Gayle 76 off 38 and Kohli 54 off
    35 for Bangalore; Sunrisers won by 8 runs. Certain figures from the record, so a wrong
    balls rule or batting-order scan would show here and nowhere internal."""
    final = [s for s in sides if s.season == 2016 and "final" in s.kinds]
    assert len(final) == 2
    by = {s.team_franchise: s for s in final}
    srh, rcb = by["Sunrisers Hyderabad"], by["Royal Challengers Bengaluru"]
    assert srh.result == "Sunrisers Hyderabad won by 8 runs"

    def line(side, name):
        (row,) = [r for r in side.players if players[r.person_id].name == name]
        return row
    w = line(srh, "DA Warner")
    assert (w.position, w.runs, w.balls, w.out) == (1, 69, 38, True)
    c = line(srh, "BCJ Cutting")
    assert (c.runs, c.balls, c.out) == (39, 15, False)
    g, k = line(rcb, "CH Gayle"), line(rcb, "V Kohli")
    assert (g.position, g.runs, g.balls) == (1, 76, 38)
    assert (k.position, k.runs, k.balls) == (2, 54, 35)


def test_the_last_match_of_each_season_is_the_final_the_record_names(sides):
    """How a final is found (the season's last match by date) checked against the 18
    champions. Wherever a side of a season's final survived the rules, its winner is right."""
    covered = 0
    for year, champion in CHAMPIONS.items():
        finals = [s for s in sides if s.season == year and "final" in s.kinds]
        for s in finals:
            winner = s.team_franchise if s.result.startswith(s.team) else s.opponent_franchise
            assert winner == champion, (year, s.result)
        covered += bool(finals)
    assert covered >= 15


# --- which sides are usable --------------------------------------------------------------------

K = {f"p{i}" for i in range(15)}


def eleven(n=11):
    return {f"p{i}" for i in range(n)}


def test_before_2023_the_xi_is_exactly_the_eleven_named():
    assert pf.clean_side(2016, eleven(), set(), K)
    assert not pf.clean_side(2016, eleven(12), eleven(12), K)
    assert not pf.clean_side(2016, eleven(10), eleven(10), K)


def test_from_2023_only_a_side_where_everyone_named_played_is_used():
    assert pf.clean_side(2024, eleven(12), eleven(12), K)
    assert pf.clean_side(2024, eleven(11), eleven(11), K)
    assert not pf.clean_side(2024, eleven(12), eleven(11), K)       # a twelfth named, never played
    assert not pf.clean_side(2024, set(), set(), K)


def test_a_side_with_somebody_who_cannot_be_typed_is_not_used():
    with_ghost = (eleven() - {"p0"}) | {"ghost"}            # still exactly eleven names
    assert len(with_ghost) == 11
    assert pf.clean_side(2016, eleven(), set(), K)          # the same shape, all known: fine
    assert not pf.clean_side(2016, with_ghost, set(), K)


# --- the delivery scan ------------------------------------------------------------------------

def d(batter, non, bowler, runs=0, wides=0, nb=0, byes=0, lb=0, legal=True, credited=False,
      out=None, kind=None, mid="m", inn=1, fs=1):
    return (mid, inn, fs, batter, non, bowler, runs, wides, nb, byes, lb, legal, credited, out, kind)


def test_a_man_waiting_at_the_other_end_is_already_in_before_he_faces():
    """A is out on ball 2 and C walks in; B has not faced a ball. B was at the crease first, so
    the order is A, B, C -- not A, C, B, which is what reading only the striker gives."""
    rows = [d("A", "B", "X", runs=1, out=None), d("A", "B", "X", out="A", kind="bowled", credited=True),
            d("C", "B", "X", runs=4), d("B", "C", "X", runs=1)]
    order, _, _ = pf.scan_deliveries(rows)
    assert order[("m", 1)] == ["A", "B", "C"]


def test_a_wide_is_not_a_ball_faced_but_a_no_ball_is():
    rows = [d("A", "B", "X", wides=1, legal=False), d("A", "B", "X", nb=1, runs=2, legal=False),
            d("A", "B", "X", runs=1)]
    _, batting, _ = pf.scan_deliveries(rows)
    assert batting[("m", "A")][:2] == [3, 2]            # three off the bat, two balls faced


def test_a_dismissal_marks_the_man_out_and_a_retirement_does_not():
    rows = [d("A", "B", "X", out="A", kind="caught", credited=True),
            d("B", "C", "X", runs=1),                                # B faces a ball ...
            d("C", "B", "X", out="B", kind="retired hurt"),          # ... then retires
            d("C", "D", "X", out="C", kind="run out")]
    _, batting, _ = pf.scan_deliveries(rows)
    assert batting[("m", "A")][2] is True
    assert batting[("m", "B")][2] is False
    assert batting[("m", "C")][2] is True


def test_a_bowler_is_charged_wides_and_no_balls_but_not_byes_or_leg_byes():
    rows = [d("A", "B", "X", runs=4), d("A", "B", "X", wides=2, legal=False),
            d("A", "B", "X", nb=1, runs=1, legal=False), d("A", "B", "X", byes=3),
            d("A", "B", "X", lb=1), d("A", "B", "X", out="A", kind="bowled", credited=True)]
    _, _, bowling = pf.scan_deliveries(rows)
    wk, conceded, balls = bowling[("m", "X")]
    assert (wk, conceded, balls) == (1, 4 + 2 + (1 + 1), 4)      # 4 legal: 4, bye, leg-bye, wicket


def test_a_run_out_is_not_the_bowlers_wicket():
    rows = [d("A", "B", "X", out="A", kind="run out", credited=False)]
    _, _, bowling = pf.scan_deliveries(rows)
    assert bowling[("m", "X")][0] == 0


def test_two_innings_keep_their_own_batting_order():
    rows = [d("A", "B", "X", fs=1), d("P", "Q", "Y", fs=2, inn=2)]
    order, _, _ = pf.scan_deliveries(rows)
    assert order[("m", 1)] == ["A", "B"] and order[("m", 2)] == ["P", "Q"]


# --- the words ---------------------------------------------------------------------------------

@pytest.mark.parametrize("args,expected", [
    (("A", "runs", 8, "runs", False), "A won by 8 runs"),
    (("A", "wickets", 6, "wickets", False), "A won by 6 wickets"),
    (("A", "runs", 1, "runs", False), "A won by 1 run"),
    (("A", "wickets", 1, "wickets", False), "A won by 1 wicket"),
    (("A", "wickets", 4, "dls", False), "A won by 4 wickets (D/L)"),
    (("A", "tie", None, "eliminator", True), "Tied; A won the super over"),
    ((None, "tie", None, None, False), "Tied"),
    ((None, "no result", None, None, False), "No result"),
])
def test_result_text(args, expected):
    winner, rtype, margin, decided, so = args
    assert pf._result_text(winner, rtype, margin, decided, so) == expected


def test_overs_and_hints():
    assert [X.overs(b) for b in (24, 18, 6, 4, 1, 0, 23)] == ["4", "3", "1", "0.4", "0.1", "0", "3.5"]
    assert X.bat_hint(q("a", 1, 69, 38, True)) == "69 (38)"
    assert X.bat_hint(q("a", 1, 39, 15, False)) == "39 (15)*"
    assert X.bat_hint(q("a")) is None
    assert X.bowl_hint(q("a", wk=3, conc=45, bb=24)) == "3/45 (4)"
    assert X.bowl_hint(q("a", wk=0, conc=1, bb=1)) == "0/1 (0.1)"
    assert X.bowl_hint(q("a")) is None


def test_the_title_says_why_the_match_is_famous(sides):
    assert X.title(make_side(kinds=("final",))) == "2016 Final"
    assert "super over" in X.title(make_side(kinds=("super_over",)))
    assert "century" in X.title(make_side(kinds=("century",)))
    assert X.title(make_side(kinds=("century", "final"))) == "2016 Final"       # the final wins


# --- the pick ----------------------------------------------------------------------------------

def test_a_seed_is_the_side(sides):
    assert X.pick_side(sides, 739898).key == X.pick_side(sides, 739898).key
    assert len({X.pick_side(sides, s).key for s in range(10_000_000, 10_000_040)}) > 20


def test_the_daily_does_not_repeat_a_side_until_every_other_has_had_a_day(sides):
    n = len(sides)
    d0 = datetime.date(2026, 10, 9).toordinal()
    seen = [X.pick_side(sides, d0 + i).key for i in range(n)]
    assert len(set(seen)) == n
    assert X.pick_side(sides, d0 + n).key == seen[0]


def test_the_side_does_not_depend_on_the_process():
    code = ("import sys; sys.path.insert(0, '.');"
            "from game import xi as X, puzzle_facts as pf;"
            "S = pf.sides_from(pf.read_document());"
            "print(X.pick_side(S, 739898).key, X.pick_side(S, 10000007).key)")
    outs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env={**os.environ, "PYTHONHASHSEED": h}, check=True).stdout
            for h in ("1", "2", "3")}
    assert len(outs) == 1


# --- the rules ---------------------------------------------------------------------------------

def test_naming_a_player_fills_his_blank(world):
    people, side = world
    st = X.State()
    assert X.guess(people, side, st, "p3") == X.FOUND
    assert st.found == ["p3"] and not st.wrong


def test_a_stranger_is_a_mistake_and_so_is_a_man_from_the_other_side_but_it_says_which(world):
    people, side = world
    st = X.State()
    assert X.guess(people, side, st, "zz") == X.WRONG
    assert X.guess(people, side, st, "o1") == X.OTHER_SIDE
    assert st.wrong == [("zz", False), ("o1", True)] and st.mistakes_left == 3


def test_five_mistakes_end_it_and_it_then_refuses_moves(world):
    people, side = world
    st = X.State()
    for pid in ("zz", "o1", "o2", "p11", "p12"):                 # p11, p12 are not in this XI
        assert not X.finished(side, st)
        X.guess(people, side, st, pid)
    assert X.finished(side, st) and not X.solved(side, st)
    with pytest.raises(X.XiError, match="over"):
        X.guess(people, side, st, "p0")


def test_naming_all_eleven_solves_it_with_mistakes_to_spare(world):
    people, side = world
    st = X.State()
    for i in range(11):
        X.guess(people, side, st, f"p{i}")
    assert X.solved(side, st) and X.finished(side, st) and st.mistakes_left == X.MISTAKES


def test_a_repeat_is_refused_and_free(world):
    people, side = world
    st = X.State()
    X.guess(people, side, st, "p1")
    X.guess(people, side, st, "zz")
    for pid in ("p1", "zz"):
        with pytest.raises(X.XiError, match="already tried"):
            X.guess(people, side, st, pid)
    assert st.mistakes_left == 4 and st.found == ["p1"]


def test_an_unknown_name_is_refused_and_free(world):
    people, side = world
    st = X.State()
    with pytest.raises(X.XiError, match="Nobody"):
        X.guess(people, side, st, "nobody")
    assert not st.found and not st.wrong


def test_replay_equals_playing_the_moves_one_by_one(world):
    people, side = world
    moves = ["p1", "zz", "o1", "p4"]
    st = X.State()
    for m in moves:
        X.guess(people, side, st, m)
    assert X.replay(people, side, moves) == st


def test_giving_up_ends_it(world):
    people, side = world
    st = X.replay(people, side, ["p1"], gave_up=True)
    assert X.finished(side, st) and not X.solved(side, st)


def test_an_impact_era_side_of_twelve_needs_all_twelve(world):
    people, _ = world
    side = make_side(n=12, season=2024)
    st = X.State()
    for i in range(11):
        X.guess(people, side, st, f"p{i}")
    assert not X.finished(side, st)
    X.guess(people, side, st, "p11")
    assert X.solved(side, st)


# --- sharing -----------------------------------------------------------------------------------

def test_the_share_line_marks_named_slots_in_order_and_names_nobody(world):
    people, side = world
    st = X.replay(people, side, ["p0", "zz", "p2", "o1"])
    text = X.share_text(side, st, "9 Oct 2026", daily=True, seed=739898)
    lines = text.splitlines()
    assert lines[0] == "Fine Leg XI · Name the XI · 9 Oct 2026 · 2/11"
    assert lines[1] == "🟩⬛🟩" + "⬛" * 8
    assert lines[2] == "2 wrong" and lines[3] == "finelegxi.in/xi"
    for p in people.values():
        assert p.name not in text


def test_a_clean_run_has_no_wrong_line_and_a_practice_one_carries_its_seed(world):
    people, side = world
    st = X.replay(people, side, ["p0"], gave_up=True)
    text = X.share_text(side, st, "Puzzle 10000007", daily=False, seed=10000007)
    assert "wrong" not in text and text.endswith("/xi?seed=10000007")


def test_twelve_slots_make_twelve_squares(world):
    people, _ = world
    side = make_side(n=12, season=2024)
    st = X.replay(people, side, [], gave_up=True)
    assert X.share_text(side, st, "x", daily=True, seed=1).splitlines()[1] == "⬛" * 12


# --- the routes --------------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(bingo_routes, "today", lambda: datetime.date(2026, 10, 9))
    app = FastAPI()
    app.include_router(xi_routes.router)
    return TestClient(app)


def _today_side(sides):
    return X.pick_side(sides, datetime.date(2026, 10, 9).toordinal())


def test_the_puzzle_has_the_clues_and_none_of_the_names(client, sides, players):
    r = client.get("/api/xi/today")
    d = r.json()
    side = _today_side(sides)
    assert d["daily"] and d["size"] == len(side.players) == len(d["slots"])
    assert d["match"]["team"]["name"] == side.team and d["match"]["result"] == side.result
    assert all(s["name"] is None and s["status"] == "open" for s in d["slots"])
    assert any(s["bat"] for s in d["slots"])
    for p in side.players:
        assert players[p.person_id].name not in r.text
        assert p.person_id not in r.text


def test_a_seeded_puzzle_is_cacheable_and_a_fresh_one_is_not(client):
    assert "s-maxage" in client.get("/api/xi?seed=10000042").headers["cache-control"]
    fresh = client.get("/api/xi")
    assert fresh.headers["cache-control"] == "no-store" and fresh.json()["seed"] >= X.PRACTICE_SEED_FLOOR


def test_naming_a_player_over_http_fills_only_his_blank(client, sides, players):
    side = _today_side(sides)
    pid = side.players[2].person_id
    r = client.post("/api/xi/play", json={"seed": 739898, "guess": pid}).json()
    st = r["state"]
    assert r["result"] == "found" and st["found"] == 1 and not st["finished"]
    named = [s for s in st["slots"] if s["name"]]
    assert len(named) == 1 and named[0]["name"] == players[pid].name and named[0]["status"] == "found"
    others = [players[p.person_id].name for p in side.players if p.person_id != pid]
    assert not any(n in str(st) for n in others)            # nobody else leaked


def test_a_mistake_says_whether_he_played_for_the_other_side(client, sides, players):
    side = _today_side(sides)
    mate = side.opposition[0]
    stranger = next(p for p in sorted(players) if p not in side.opposition
                    and all(p != x.person_id for x in side.players))
    a = client.post("/api/xi/play", json={"seed": 739898, "guess": mate}).json()
    b = client.post("/api/xi/play", json={"seed": 739898, "guess": stranger}).json()
    assert a["result"] == "other_side" and a["state"]["wrong"][0]["other_side"] is True
    assert b["result"] == "wrong" and b["state"]["wrong"][0]["other_side"] is False


def test_giving_up_reveals_every_name_with_its_status(client, sides, players):
    side = _today_side(sides)
    got = side.players[0].person_id
    r = client.post("/api/xi/play", json={"seed": 739898, "guesses": [got], "give_up": True}).json()["state"]
    assert r["finished"] and not r["solved"] and r["share"]
    assert all(s["name"] for s in r["slots"])
    assert r["slots"][0]["status"] == "found" and {s["status"] for s in r["slots"][1:]} == {"missed"}


def test_the_whole_side_named_solves_it(client, sides):
    side = _today_side(sides)
    r = client.post("/api/xi/play", json={"seed": 739898, "guesses": [p.person_id for p in side.players[:-1]],
                                          "guess": side.players[-1].person_id}).json()["state"]
    assert r["solved"] and r["finished"] and r["found"] == r["size"] and r["share"]


def test_a_move_the_rules_refuse_is_a_400_with_a_reason(client, sides):
    pid = _today_side(sides).players[0].person_id
    r = client.post("/api/xi/play", json={"seed": 739898, "guesses": [pid], "guess": pid})
    assert r.status_code == 400 and "already tried" in r.json()["detail"]
    assert client.post("/api/xi/play", json={"seed": 739898, "guess": "nobody"}).status_code == 400


def test_an_oversized_guess_list_is_rejected_outright(client):
    r = client.post("/api/xi/play", json={"seed": 739898, "guesses": ["x"] * (X.MAX_GUESSES + 1)})
    assert r.status_code == 422


def test_missing_data_is_a_503_not_a_crash(client, monkeypatch):
    monkeypatch.setattr(xi_routes, "_sides", lambda: None)
    assert client.get("/api/xi/today").status_code == 503
