"""Teammate Chain (A184): who counts as a teammate, the rules, the share line and the routes.

The rules are exercised on hand-built players, where who shared a squad with whom is obvious
by construction; a few real pairs are pinned where the public record is certain. No database.
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from game import chain as C
from game import guess as G
from game import puzzle_facts
from web import bingo_routes, chain_routes

DOC = puzzle_facts.read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="puzzle facts not committed")


@pytest.fixture(scope="module")
def players():
    return {p.person_id: p for p in puzzle_facts.players_from(DOC)}


@pytest.fixture(scope="module")
def real(players):
    return C.World(players)


def pf(pid, squads, name=None, **kw) -> puzzle_facts.PlayerFacts:
    franchises = tuple(sorted({s.split("|")[0] for s in squads}))
    seasons = tuple(sorted({int(s.split("|")[1]) for s in squads}))
    base = dict(person_id=pid, name=name or pid.upper(), franchises=franchises, seasons=seasons,
                overseas=False, keeper=False, runs=100, wickets=0, best_season_runs=100,
                best_season_wickets=0, hundreds=0, fifties=0, four_wicket_hauls=0, sixes=0,
                role="batter", country="India", bowling_style=None, squads=tuple(sorted(squads)))
    base.update(kw)
    return puzzle_facts.PlayerFacts(**base)


MI13, MI14, CSK13 = "Mumbai Indians|2013", "Mumbai Indians|2014", "Chennai Super Kings|2013"


@pytest.fixture
def small():
    """a--b share MI 2013; b--c share MI 2014; a and c never share; d is with nobody."""
    players = {
        "a": pf("a", [MI13], name="Qwertyalpha"), "b": pf("b", [MI13, MI14], name="Qwertybravo"),
        "c": pf("c", [MI14], name="Qwertycharlie"), "d": pf("d", [CSK13], name="Qwertydelta"),
        "e": pf("e", [MI13, CSK13], name="Qwertyecho"),
    }
    return C.World(players), players


# --- who is a teammate -----------------------------------------------------------------------

def test_teammates_share_a_club_AND_a_season(small):
    w, _ = small
    assert w.shared("a", "b") == [MI13]
    assert w.shared("a", "c") == []                      # same club, different seasons
    assert w.shared("a", "d") == []                      # same season, different clubs
    assert w.teammates("b") == {"a", "c", "e"}
    assert "b" not in w.teammates("b")


def test_teammate_is_symmetric_and_shared_squads_are_oldest_first(small):
    w, _ = small
    assert w.shared("a", "b") == w.shared("b", "a")
    assert w.shared("b", "e") == [MI13]
    both = C.World({"x": pf("x", [MI14, MI13]), "y": pf("y", [MI13, MI14])})
    assert both.shared("x", "y") == [MI13, MI14]


def test_describe_groups_by_club_and_merges_consecutive_years():
    out = C.describe(["Mumbai Indians|2013", "Chennai Super Kings|2012", "Chennai Super Kings|2010",
                      "Chennai Super Kings|2011", "Chennai Super Kings|2014"])
    assert out == ["Chennai Super Kings 2010-2012, 2014", "Mumbai Indians 2013"]
    assert C.describe(["Mumbai Indians|2013"]) == ["Mumbai Indians 2013"]
    assert C.describe([]) == []


def test_real_pairs_the_record_is_certain_about(real, players):
    by = {p.name: p.person_id for p in players.values()}
    assert real.shared(by["MS Dhoni"], by["SK Raina"])             # Chennai, from the first season
    assert not real.shared(by["MS Dhoni"], by["CH Gayle"])         # never at a club together
    assert not real.shared(by["V Kohli"], by["MS Dhoni"])          # RCB against CSK and Rising Pune
    assert by["V Kohli"] not in real.teammates(by["MS Dhoni"])


def test_squads_are_the_decks_own_franchise_seasons(players):
    """Re-derived from the deck's cards, a different route from the one that built the facts,
    so a dropped card or a wrong canonical mapping is seen."""
    from etl.franchise_map import canonical
    from tools import snapshot_deck
    deck = snapshot_deck.deck_from(snapshot_deck.read_document())
    expected: dict[str, set[str]] = {}
    for cards in deck.cards_by_fs.values():
        for c in cards:
            expected.setdefault(c.person_id, set()).add(f"{canonical(c.franchise)}|{c.season_year}")
    assert set(expected) == set(players)
    for pid, squads in expected.items():
        assert set(players[pid].squads) == squads and players[pid].squads == tuple(sorted(squads))


# --- the start --------------------------------------------------------------------------------

def test_the_chain_starts_from_the_recognisable_and_a_seed_is_the_start(players):
    names = {p.person_id for p in G.pool(players)}
    assert all(C.start_player(players, s).person_id in names for s in range(739000, 739060))
    assert C.start_player(players, 739898).person_id == C.start_player(players, 739898).person_id


def test_the_daily_does_not_repeat_a_start_until_every_other_has_had_a_day(players):
    n = len(G.pool(players))
    d0 = datetime.date(2026, 10, 9).toordinal()
    seen = [C.start_player(players, d0 + i).person_id for i in range(n)]
    assert len(set(seen)) == n
    assert C.start_player(players, d0 + n).person_id == seen[0]


def test_it_does_not_start_where_guess_the_player_ends(players):
    """Two games keyed to the same date should not hand out the same player."""
    days = range(739898, 739898 + 60)
    same = sum(C.start_player(players, d).person_id == G.mystery(players, d).person_id for d in days)
    assert same < 10


def test_the_start_does_not_depend_on_the_process():
    code = ("import sys; sys.path.insert(0, '.');"
            "from game import chain as C, puzzle_facts as pf;"
            "P = {p.person_id: p for p in pf.players_from(pf.read_document())};"
            "print(C.start_player(P, 739898).person_id, C.start_player(P, 10000007).person_id)")
    outs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env={**os.environ, "PYTHONHASHSEED": h}, check=True).stdout
            for h in ("1", "2", "3")}
    assert len(outs) == 1


# --- the rules ---------------------------------------------------------------------------------

def test_a_teammate_extends_the_chain_and_records_the_squad(small):
    w, p = small
    st = C.new_state(p["a"])
    assert C.guess(w, st, "b") is True
    assert st.chain == ["a", "b"] and st.shared == [[MI13]] and st.links == 1 and st.strikes == 0


def test_a_non_teammate_is_a_strike_and_the_chain_stays_put(small):
    w, p = small
    st = C.new_state(p["a"])
    assert C.guess(w, st, "c") is False
    assert st.chain == ["a"] and st.strikes == 1 and st.links == 0 and not st.finished


def test_three_strikes_end_the_chain_and_it_then_refuses_moves(small):
    w, p = small
    people = {**p, "f": pf("f", ["Delhi Capitals|2009"])}
    w = C.World(people)
    st = C.new_state(p["a"])
    for pid in ("c", "d"):
        C.guess(w, st, pid)
    assert not st.finished and st.strikes == 2
    C.guess(w, st, "f")
    assert st.finished and st.strikes == C.STRIKES
    with pytest.raises(C.ChainError, match="over"):
        C.guess(w, st, "b")


def test_nobody_twice_and_a_wrong_guess_is_not_repeated_from_the_same_end(small):
    w, p = small
    st = C.new_state(p["a"])
    C.guess(w, st, "b")
    with pytest.raises(C.ChainError, match="already in your chain"):
        C.guess(w, st, "a")
    with pytest.raises(C.ChainError, match="already in your chain"):
        C.guess(w, st, "b")
    C.guess(w, st, "d")                                  # a strike from b
    with pytest.raises(C.ChainError, match="already tried"):
        C.guess(w, st, "d")
    assert st.strikes == 1 and st.guesses == ["b", "d"]


def test_a_wrong_guess_may_be_tried_again_from_a_new_end(small):
    """'d' was no teammate of 'b', but is a teammate of 'e' (CSK 2013): once the chain has
    moved on, the earlier miss no longer blocks him."""
    w, p = small
    st = C.new_state(p["a"])
    C.guess(w, st, "b")
    C.guess(w, st, "d")                                  # strike from b
    assert C.guess(w, st, "e") is True                   # b and e share MI 2013
    assert "d" not in st.tried_here()
    assert C.guess(w, st, "d") is True                   # e and d share CSK 2013


def test_a_refused_move_costs_nothing(small):
    w, p = small
    st = C.new_state(p["a"])
    for pid in ("nobody", "a"):
        with pytest.raises(C.ChainError):
            C.guess(w, st, pid)
    assert st.guesses == [] and st.strikes == 0


def test_stopping_ends_it_and_banks_the_length(small):
    w, p = small
    st = C.replay(w, p["a"], ["b", "c"], stopped=True)
    assert st.finished and st.links == 2 and st.strikes == 0


def test_replay_equals_playing_the_moves_one_by_one(small):
    w, p = small
    moves = ["b", "d", "c"]
    st = C.new_state(p["a"])
    for m in moves:
        C.guess(w, st, m)
    assert C.replay(w, p["a"], moves) == st


def test_clubs_counts_franchises_not_squads(small):
    w, p = small
    st = C.replay(w, p["a"], ["b", "c"])                 # MI 2013 then MI 2014: one club
    assert st.clubs() == 1
    assert C.replay(w, p["a"], ["e", "d"]).clubs() == 2  # MI 2013 then CSK 2013


def test_the_guess_cap_ends_a_run():
    people = {"s": pf("s", [MI13])}
    for i in range(C.MAX_GUESSES + 1):
        people[f"p{i}"] = pf(f"p{i}", [MI13])
    w = C.World(people)
    st = C.new_state(people["s"])
    for i in range(C.MAX_GUESSES):
        C.guess(w, st, f"p{i}")
    assert st.finished and st.links == C.MAX_GUESSES


def test_missed_names_unused_teammates_best_known_first(small):
    big = {"a": pf("a", [MI13], runs=5000, name="Startman"), "z": pf("z", [MI13], runs=9000, name="Star"),
           "y": pf("y", [MI13], runs=10, name="Nobody")}
    for i in range(8):
        big[f"m{i}"] = pf(f"m{i}", [MI13], runs=100 * (i + 1))
    w = C.World(big)
    st = C.new_state(big["a"])
    C.guess(w, st, "m7")
    out = C.missed(w, st)
    assert out[0] == "Star" and len(out) == C.HINT_NAMES and "m7" not in out
    assert "Nobody" not in out                           # least known are the ones left off
    assert "Startman" not in out                         # nor is anybody already in the chain,
    #                                                      however well known he is


# --- sharing -----------------------------------------------------------------------------------

def test_the_share_line_is_the_rhythm_of_the_run_with_no_names(small):
    w, p = small
    st = C.replay(w, p["a"], ["c", "b", "d", "c"])       # strike, link, strike, link
    text = C.share_text(w, st, "9 Oct 2026", daily=True, seed=739898)
    lines = text.splitlines()
    assert lines[0] == "Almanack Chain · 9 Oct 2026 · 2 links"
    assert lines[1] == "🟥🟩🟥🟩"
    assert lines[2] == "1 club"                          # singular, not "1 clubs"
    assert lines[-1] == "iplegends.vercel.app/chain"
    for person in p.values():
        assert person.name not in text


def test_one_link_is_singular_and_a_practice_chain_carries_its_seed(small):
    w, p = small
    st = C.replay(w, p["a"], ["b"], stopped=True)
    text = C.share_text(w, st, "Chain 10000007", daily=False, seed=10000007)
    assert text.splitlines()[0].endswith("· 1 link")
    assert text.endswith("/chain?seed=10000007")
    two = C.replay(w, p["a"], ["e", "d"], stopped=True)
    assert "2 clubs" in C.share_text(w, two, "x", daily=True, seed=1)


def test_a_long_run_is_capped_in_the_share_line():
    people = {"s": pf("s", [MI13])}
    for i in range(C.SHARE_SQUARES + 7):
        people[f"p{i}"] = pf(f"p{i}", [MI13])
    w = C.World(people)
    st = C.replay(w, people["s"], [f"p{i}" for i in range(C.SHARE_SQUARES + 7)], stopped=True)
    row = C.share_text(w, st, "x", daily=False, seed=1).splitlines()[1]
    assert row == "🟩" * C.SHARE_SQUARES + " +7"


def test_a_run_with_no_guesses_still_shares_something(small):
    w, p = small
    st = C.new_state(p["a"]); st.stopped = True
    assert C.share_text(w, st, "x", daily=True, seed=1).splitlines()[1] == "⬛"


# --- the routes --------------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(bingo_routes, "today", lambda: datetime.date(2026, 10, 9))
    app = FastAPI()
    app.include_router(chain_routes.router)
    return TestClient(app)


def _start(players, seed=739898):
    return C.start_player(players, seed)


def test_today_names_the_start_and_nothing_else(client, players):
    d = client.get("/api/chain/today").json()
    assert d["daily"] and d["date"] == "2026-10-09" and d["strikes"] == C.STRIKES
    assert d["start"]["name"] == _start(players).name and d["start"]["shared"] == []


def test_a_seeded_chain_is_cacheable_and_a_fresh_one_is_not(client):
    assert "s-maxage" in client.get("/api/chain?seed=10000042").headers["cache-control"]
    fresh = client.get("/api/chain")
    assert fresh.headers["cache-control"] == "no-store" and fresh.json()["seed"] >= C.PRACTICE_SEED_FLOOR


def test_a_link_and_a_strike_over_http(client, players, real):
    start = _start(players)
    mate = sorted(real.teammates(start.person_id))[0]
    stranger = next(p for p in sorted(players) if p != start.person_id
                    and p not in real.teammates(start.person_id))
    r = client.post("/api/chain/play", json={"seed": 739898, "guess": mate}).json()
    st = r["state"]
    assert r["linked"] is True and st["links"] == 1 and st["chain"][1]["shared"]
    r = client.post("/api/chain/play", json={"seed": 739898, "guesses": [mate], "guess": stranger}).json()
    assert r["linked"] is False and r["state"]["strikes"] == 1
    assert r["state"]["tried_here"] == [stranger] and r["state"]["links"] == 1
    assert stranger not in [s["person_id"] for s in r["state"]["chain"]]


def test_the_run_ends_on_the_third_strike_with_the_share_and_the_missed_names(client, players, real):
    start = _start(players)
    strangers = [p for p in sorted(players) if p != start.person_id
                 and p not in real.teammates(start.person_id)][:3]
    r = client.post("/api/chain/play", json={"seed": 739898, "guesses": strangers[:2],
                                             "guess": strangers[2]}).json()["state"]
    assert r["finished"] and r["strikes"] == 3 and r["strikes_left"] == 0
    assert r["share"].startswith("Almanack Chain · 9 Oct 2026 · 0 links") and r["missed"]


def test_stopping_banks_the_chain(client, players, real):
    start = _start(players)
    mate = sorted(real.teammates(start.person_id))[0]
    r = client.post("/api/chain/play", json={"seed": 739898, "guesses": [mate], "stop": True}).json()["state"]
    assert r["finished"] and r["stopped"] and r["links"] == 1 and r["share"]


def test_an_unfinished_run_reveals_no_hint_and_no_share(client, players, real):
    start = _start(players)
    mate = sorted(real.teammates(start.person_id))[0]
    st = client.post("/api/chain/play", json={"seed": 739898, "guess": mate}).json()["state"]
    assert st["missed"] == [] and st["share"] is None and not st["finished"]


def test_a_move_the_rules_refuse_is_a_400_with_a_reason(client, players, real):
    start = _start(players)
    r = client.post("/api/chain/play", json={"seed": 739898, "guess": start.person_id})
    assert r.status_code == 400 and "already in your chain" in r.json()["detail"]
    assert client.post("/api/chain/play", json={"seed": 739898, "guess": "nobody"}).status_code == 400


def test_an_oversized_guess_list_is_rejected_outright(client):
    r = client.post("/api/chain/play", json={"seed": 739898, "guesses": ["x"] * (C.MAX_GUESSES + 1)})
    assert r.status_code == 422


def test_a_missing_snapshot_is_a_503_not_a_crash(client, monkeypatch):
    monkeypatch.setattr(bingo_routes, "_facts", lambda: None)
    assert client.post("/api/chain/play", json={"seed": 739898}).status_code == 503
