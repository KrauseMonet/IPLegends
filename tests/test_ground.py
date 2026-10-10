"""Common Ground (A188): who counts as a common teammate, which pairs are playable, the rules,
the scoring, the share line and the routes.

The rules are exercised on hand-built players, where who shared a squad with whom is obvious by
construction; the pair rules are re-derived by a second route over the real facts, and a few real
pairs are pinned where the public record is certain. No database.
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from game import ground as G
from game import puzzle_facts
from web import bingo_routes, ground_routes

DOC = puzzle_facts.read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="puzzle facts not committed")


@pytest.fixture(scope="module")
def players():
    return {p.person_id: p for p in puzzle_facts.players_from(DOC)}


@pytest.fixture(scope="module")
def real(players):
    return G.Ground(players)


def pf(pid, squads, name=None, **kw) -> puzzle_facts.PlayerFacts:
    franchises = tuple(sorted({s.split("|")[0] for s in squads}))
    seasons = tuple(sorted({int(s.split("|")[1]) for s in squads}))
    base = dict(person_id=pid, name=name or pid.upper(), franchises=franchises, seasons=seasons,
                overseas=False, keeper=False, runs=100, wickets=0, best_season_runs=100,
                best_season_wickets=0, hundreds=0, fifties=0, four_wicket_hauls=0, sixes=0,
                role="batter", country="India", bowling_style=None, squads=tuple(sorted(squads)))
    base.update(kw)
    return puzzle_facts.PlayerFacts(**base)


MI13, MI14, MI15, MI16 = (f"Mumbai Indians|{y}" for y in (2013, 2014, 2015, 2016))
RCB15, CSK13 = "Royal Challengers Bengaluru|2015", "Chennai Super Kings|2013"


@pytest.fixture
def small():
    """The pair is a (MI 2013, RCB 2015) and b (MI 2016, RCB 2015). Who was with them:
      x  MI13 + MI16  -- a teammate of a in 2013 and of b in 2016: common, in DIFFERENT years
      o  MI13 + MI16  -- the same
      y  RCB15, m RCB15 -- common, the same squad as both
      z  MI13 only    -- a teammate of a, never of b
      w  MI16 only    -- a teammate of b, never of a
      n  CSK13        -- of neither
    """
    people = {
        "a": pf("a", [MI13, RCB15], name="Qwertyalpha"), "b": pf("b", [MI16, RCB15], name="Qwertybravo"),
        "x": pf("x", [MI13, MI16], name="Qwertyxray"), "o": pf("o", [MI13, MI16], name="Qwertyoscar"),
        "y": pf("y", [RCB15], name="Qwertyyankee"), "m": pf("m", [RCB15], name="Qwertymike"),
        "z": pf("z", [MI13], name="Qwertyzulu"), "w": pf("w", [MI16], name="Qwertywhiskey"),
        "n": pf("n", [CSK13], name="Qwertynovember"),
    }
    g = G.Ground(people)
    return g, people, G.Puzzle("a", "b", frozenset(g.world.common("a", "b")))


# --- who is a teammate, and who is a COMMON teammate -----------------------------------------

def test_teammates_share_a_club_AND_a_season(small):
    g, _, _ = small
    assert g.world.shared("a", "z") == [MI13]
    assert g.world.shared("a", "w") == []               # a's club, but b's year
    assert g.world.shared("a", "n") == []               # the same season, a different club
    assert "a" not in g.world.teammates("a")


def test_a_common_teammate_may_have_played_with_each_in_a_different_year(small):
    g, _, puzzle = small
    assert puzzle.answers == {"x", "o", "y", "m"}
    assert "x" in puzzle.answers and g.world.shared("a", "x") == [MI13] and g.world.shared("b", "x") == [MI16]


def test_only_a_teammate_of_both_counts_and_never_the_pair_themselves(small):
    g, _, puzzle = small
    assert not {"z", "w", "n"} & puzzle.answers
    assert "a" not in puzzle.answers and "b" not in puzzle.answers


def test_shared_squads_are_oldest_first_and_symmetric():
    both = G.World({"x": pf("x", [MI14, MI13]), "y": pf("y", [MI13, MI14])})
    assert both.shared("x", "y") == [MI13, MI14] == both.shared("y", "x")


def test_describe_groups_by_club_and_merges_consecutive_years():
    out = G.describe(["Mumbai Indians|2013", "Chennai Super Kings|2012", "Chennai Super Kings|2010",
                      "Chennai Super Kings|2011", "Chennai Super Kings|2014"])
    assert out == ["Chennai Super Kings 2010-2012, 2014", "Mumbai Indians 2013"]
    assert G.describe(["Mumbai Indians|2013"]) == ["Mumbai Indians 2013"] and G.describe([]) == []


def test_real_pairs_the_record_is_certain_about(real, players):
    by = {p.name: p.person_id for p in players.values()}
    w = real.world
    assert w.shared(by["MS Dhoni"], by["SK Raina"])           # Chennai, from the first season
    assert not w.shared(by["MS Dhoni"], by["CH Gayle"])       # never at a club together
    # Dhoni and Raina were together at Chennai for a decade, so most of Chennai is common to them.
    assert len(w.common(by["MS Dhoni"], by["SK Raina"])) >= 20
    # Kohli and Gayle shared Bengaluru 2011-2017, so Gayle's and Kohli's common set holds de Villiers.
    assert by["AB de Villiers"] in w.common(by["V Kohli"], by["CH Gayle"])


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
        assert set(players[pid].squads) == squads


# --- which pairs are playable -----------------------------------------------------------------

def _fringe(pid):
    return pf(pid, [MI13], runs=10)


def _famous(pid):
    return pf(pid, [MI13], runs=500)


def _pair_world(extra):
    """Two players in the recognisable pool (three seasons, 5,000 runs) plus `extra` common mates."""
    people = {pid: pf(pid, [MI13, MI14, MI15], runs=5000) for pid in ("p1", "p2")}
    people.update({p.person_id: p for p in extra})
    return G.Ground(people)


def test_a_pair_needs_between_four_and_eight_common_teammates():
    for n, playable in ((3, False), (4, True), (8, True), (9, False)):
        g = _pair_world([_famous(f"f{i}") for i in range(n)])
        assert bool(g.pairs) is playable, n


def test_a_pair_whose_common_teammates_are_all_fringe_is_not_playable():
    assert not _pair_world([_fringe(f"f{i}") for i in range(6)]).pairs
    mixed = _pair_world([_famous("k0"), _famous("k1"), _famous("k2")] + [_fringe(f"f{i}") for i in range(3)])
    assert mixed.pairs == [("p1", "p2")]                 # three a fan would place is enough


def test_a_fringe_common_teammate_is_still_a_valid_answer():
    g = _pair_world([_famous("k0"), _famous("k1"), _famous("k2"), _fringe("tail")])
    assert "tail" in g.puzzle(10000001).answers


def test_every_real_pair_is_playable_and_the_set_is_the_independent_one(real, players):
    """Re-derived by a second route: build each player's teammates by pairing the members of
    every squad directly, instead of through `World`, and recount the playable pairs."""
    by_squad: dict[str, set[str]] = {}
    for p in players.values():
        for sq in p.squads:
            by_squad.setdefault(sq, set()).add(p.person_id)
    mates: dict[str, set[str]] = {pid: set() for pid in players}
    for members in by_squad.values():
        for a in members:
            for b in members:
                if a != b:
                    mates[a].add(b)
    ids = sorted(p.person_id for p in G.pool(players))
    expected = []
    for k, a in enumerate(ids):
        for b in ids[k + 1:]:
            common = mates[a] & mates[b]
            if G.MIN_ANSWERS <= len(common) <= G.MAX_ANSWERS and \
                    sum(players[x].prominence >= G.KNOWN_PROMINENCE for x in common) >= G.MIN_KNOWN:
                expected.append((a, b))
    assert real.pairs == expected and len(expected) > 2000


def test_a_puzzles_answers_are_never_the_pair_and_always_four_to_eight(real):
    d0 = datetime.date(2026, 10, 9).toordinal()
    for seed in range(d0, d0 + 150):
        p = real.puzzle(seed)
        assert G.MIN_ANSWERS <= len(p.answers) <= G.MAX_ANSWERS and p.a != p.b
        assert p.a not in p.answers and p.b not in p.answers


def test_the_daily_does_not_repeat_a_pair_until_every_other_has_had_a_day(real):
    n = len(real.pairs)
    d0 = datetime.date(2026, 10, 9).toordinal()
    seen = [frozenset((real.puzzle(d0 + i).a, real.puzzle(d0 + i).b)) for i in range(n)]
    assert len(set(seen)) == n
    assert frozenset((real.puzzle(d0 + n).a, real.puzzle(d0 + n).b)) == seen[0]


def test_which_player_is_shown_first_is_not_a_clue(real):
    d0 = datetime.date(2026, 10, 9).toordinal()
    firsts = [real.puzzle(d0 + i).a < real.puzzle(d0 + i).b for i in range(200)]
    assert 60 < sum(firsts) < 140


def test_a_practice_seed_is_a_pure_function_of_itself(real):
    assert real.puzzle(10000007) == real.puzzle(10000007)
    assert any(real.puzzle(10000007) != real.puzzle(s) for s in range(10000008, 10000020))


def test_the_pair_does_not_depend_on_the_process():
    code = ("import sys; sys.path.insert(0, '.');"
            "from game import ground as G, puzzle_facts as pf;"
            "g = G.Ground({p.person_id: p for p in pf.players_from(pf.read_document())});"
            "a = g.puzzle(739898); b = g.puzzle(10000007);"
            "print(a.a, a.b, sorted(a.answers), b.a, b.b, sorted(b.answers))")
    outs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env={**os.environ, "PYTHONHASHSEED": h}, check=True).stdout
            for h in ("1", "2", "3")}
    assert len(outs) == 1


# --- the rules --------------------------------------------------------------------------------

def play(g, puzzle, *moves, end_t=None):
    return G.replay(g, puzzle, [G.Move(i, t) for i, t in moves], end_t=end_t)


def test_a_common_teammate_is_a_hit_and_a_miss_says_how_it_missed(small):
    g, _, puzzle = small
    st = G.new_state(puzzle)
    assert G.guess(g, st, "x", 1000) is True
    assert G.guess(g, st, "z", 2000) is False and st.kinds[-1] == G.ONLY_A
    assert G.guess(g, st, "w", 3000) is False and st.kinds[-1] == G.ONLY_B
    assert G.guess(g, st, "n", 4000) is False and st.kinds[-1] == G.NEITHER
    assert [m.id for m in st.found] == ["x"] and len(st.wrong) == 3


def test_the_pair_themselves_cannot_be_guessed(small):
    g, _, puzzle = small
    st = G.new_state(puzzle)
    for pid in ("a", "b"):
        with pytest.raises(G.GroundError, match="one of the two"):
            G.guess(g, st, pid, 1000)
    assert st.moves == []


def test_a_player_cannot_be_tried_twice_right_or_wrong(small):
    g, _, puzzle = small
    st = play(g, puzzle, ("x", 1000), ("z", 2000))
    for pid in ("x", "z"):
        with pytest.raises(G.GroundError, match="already tried"):
            G.guess(g, st, pid, 3000)
    assert len(st.moves) == 2


def test_an_unknown_player_and_a_backwards_clock_are_refused_at_no_cost(small):
    g, _, puzzle = small
    st = play(g, puzzle, ("x", 5000))
    with pytest.raises(G.GroundError, match="Nobody"):
        G.guess(g, st, "nobody", 6000)
    with pytest.raises(G.GroundError, match="out of order"):
        G.guess(g, st, "y", 4999)
    assert len(st.moves) == 1


def test_a_guess_after_the_limit_is_refused(small):
    g, _, puzzle = small
    st = G.new_state(puzzle)
    G.guess(g, st, "x", G.LIMIT_MS)                      # on the very last millisecond: allowed
    with pytest.raises(G.GroundError, match="Time is up"):
        G.guess(g, st, "y", G.LIMIT_MS + 1)


def test_finding_the_whole_set_finishes_it_and_it_then_refuses_moves(small):
    g, _, puzzle = small
    st = play(g, puzzle, ("x", 1), ("o", 2), ("y", 3), ("m", 4))
    assert st.complete and st.finished
    with pytest.raises(G.GroundError, match="over"):
        G.guess(g, st, "z", 5)


def test_ending_it_records_why_and_never_disturbs_a_completed_set(small):
    g, _, puzzle = small
    assert play(g, puzzle, ("x", 1000), end_t=G.LIMIT_MS).ended == "time"
    quit_ = play(g, puzzle, ("x", 1000), end_t=90_000)
    assert quit_.ended == "gave_up" and quit_.finished
    done = play(g, puzzle, ("x", 1), ("o", 2), ("y", 3), ("m", 4), end_t=G.LIMIT_MS)
    assert done.complete and done.ended is None          # the late 'time is up' changed nothing


def test_the_elapsed_time_is_the_last_find_the_limit_or_the_moment_it_was_given_up(small):
    g, _, puzzle = small
    assert play(g, puzzle, ("x", 1), ("o", 2), ("y", 3), ("m", 40_000)).elapsed_ms == 40_000
    assert play(g, puzzle, ("x", 1000), end_t=G.LIMIT_MS).elapsed_ms == G.LIMIT_MS
    assert play(g, puzzle, ("x", 1000), end_t=90_000).elapsed_ms == 90_000


def test_the_guess_cap_ends_it():
    people = {"a": pf("a", [MI13]), "b": pf("b", [MI13]), "real": pf("real", [MI13])}
    for i in range(G.MAX_GUESSES + 1):
        people[f"p{i}"] = pf(f"p{i}", [CSK13])
    g = G.Ground(people)
    st = G.new_state(G.Puzzle("a", "b", frozenset({"real"})))
    for i in range(G.MAX_GUESSES - 1):
        G.guess(g, st, f"p{i}", i)
    assert not st.finished
    G.guess(g, st, f"p{G.MAX_GUESSES - 1}", G.MAX_GUESSES)
    assert st.finished and not st.complete


def test_replay_equals_playing_the_moves_one_by_one(small):
    g, _, puzzle = small
    st = G.new_state(puzzle)
    for pid, t in (("x", 1000), ("z", 2000), ("y", 3000)):
        G.guess(g, st, pid, t)
    assert G.replay(g, puzzle, [G.Move("x", 1000), G.Move("z", 2000), G.Move("y", 3000)]) == st


# --- the scoring -------------------------------------------------------------------------------

def test_a_hundred_a_teammate_and_twenty_off_for_each_wrong_guess(small):
    g, _, puzzle = small
    sc = G.score(play(g, puzzle, ("x", 1), ("z", 2), ("n", 3), ("y", 4)))
    assert (sc.found, sc.wrong, sc.base, sc.penalty, sc.bonus, sc.points) == (2, 2, 200, 40, 0, 160)


def test_the_penalty_can_never_take_a_score_below_nothing(small):
    g, _, puzzle = small
    st = G.new_state(puzzle)
    for k, pid in enumerate(("z", "w", "n")):
        G.guess(g, st, pid, k)
    assert G.score(st).points == 0
    people = dict(small[1])
    for i in range(10):
        people[f"q{i}"] = pf(f"q{i}", [CSK13])
    g2 = G.Ground(people)
    st = G.new_state(G.Puzzle("a", "b", puzzle.answers))
    G.guess(g2, st, "x", 1)
    for i in range(10):
        G.guess(g2, st, f"q{i}", 2 + i)
    assert G.score(st).points == 0                       # 100 found, 200 of penalties: floored


def test_the_whole_set_earns_a_point_per_second_left_and_only_then(small):
    g, _, puzzle = small
    four = play(g, puzzle, ("x", 1), ("o", 2), ("y", 3), ("m", 100_000))
    sc = G.score(four)
    assert sc.bonus == 140 and sc.points == 400 + 140    # 240 s - 100 s
    three = play(g, puzzle, ("x", 1), ("o", 2), ("y", 3), end_t=G.LIMIT_MS)
    assert G.score(three).bonus == 0 and G.score(three).points == 300


def test_the_bonus_counts_whole_seconds_only(small):
    g, _, puzzle = small
    t = 100_999
    assert G.score(play(g, puzzle, ("x", 1), ("o", 2), ("y", 3), ("m", t))).bonus == 139
    assert G.score(play(g, puzzle, ("x", 1), ("o", 2), ("y", 3), ("m", G.LIMIT_MS))).bonus == 0


def test_a_wrong_guess_reduces_the_whole_set_score_but_not_the_bonus(small):
    g, _, puzzle = small
    sc = G.score(play(g, puzzle, ("z", 1), ("x", 2), ("o", 3), ("y", 4), ("m", 60_000)))
    assert sc.points == (400 - 20) + 180


# --- once it is over ---------------------------------------------------------------------------

def test_unfound_lists_the_teammates_not_named_best_known_first():
    people = {"a": pf("a", [MI13]), "b": pf("b", [MI13])}
    for i, runs in enumerate((100, 9000, 500, 50)):
        people[f"k{i}"] = pf(f"k{i}", [MI13], runs=runs, name=f"Mate{i}")
    g = G.Ground(people)
    puzzle = G.Puzzle("a", "b", frozenset(g.world.common("a", "b")))
    st = play(g, puzzle, ("k0", 1000), end_t=5000)
    assert G.unfound(g, st) == ["k1", "k2", "k3"]


def test_answer_lines_name_the_squads_shared_with_each_player(small):
    g, _, puzzle = small
    assert G.answer_lines(g, G.new_state(puzzle), "x") == (["Mumbai Indians 2013"], ["Mumbai Indians 2016"])


# --- sharing -----------------------------------------------------------------------------------

def test_the_share_line_is_the_rhythm_the_score_and_no_names(small):
    g, people, puzzle = small
    st = play(g, puzzle, ("z", 1000), ("x", 2000), ("o", 3000), end_t=G.LIMIT_MS)
    lines = G.share_text(st, "9 Oct 2026", daily=True, seed=739898).splitlines()
    assert lines[0] == "Fine Leg XI · Common Ground · 9 Oct 2026"
    assert lines[1] == f"2/4 · {G.score(st).points} pts"
    assert lines[2] == "🟥🟩🟩⬛⬛"                           # miss, two finds, two never found
    assert lines[-1] == "finelegxi.in/common"
    text = "\n".join(lines)
    for person in people.values():
        assert person.name not in text


def test_a_completed_set_shows_how_fast_and_a_practice_pair_carries_its_seed(small):
    g, _, puzzle = small
    st = play(g, puzzle, ("x", 1), ("o", 2), ("y", 3), ("m", 95_000))
    text = G.share_text(st, "Practice 10000007", daily=False, seed=10000007)
    assert text.splitlines()[1].endswith("· 1:35") and text.endswith("/common?seed=10000007")
    assert "⬛" not in text


def test_the_label_names_a_date_or_a_practice_pair():
    assert G.label_for(datetime.date(2026, 10, 9).toordinal()) == ("9 Oct 2026", True, datetime.date(2026, 10, 9))
    assert G.label_for(10000007) == ("Practice 10000007", False, None)


# --- the routes --------------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(bingo_routes, "today", lambda: datetime.date(2026, 10, 9))
    app = FastAPI()
    app.include_router(ground_routes.router)
    return TestClient(app)


SEED = datetime.date(2026, 10, 9).toordinal()


def test_today_names_the_pair_the_count_and_the_rules_but_never_the_answers(client, real):
    d = client.get("/api/ground/today").json()
    p = real.puzzle(SEED)
    assert d["daily"] and d["date"] == "2026-10-09" and d["limit_seconds"] == G.LIMIT_SECONDS
    assert {d["a"]["person_id"], d["b"]["person_id"]} == {p.a, p.b} and d["total"] == len(p.answers)
    assert (d["points_per_teammate"], d["penalty_per_wrong"], d["bonus_per_second"]) == (100, 20, 1)
    for k in ("answers", "found", "unfound"):
        assert k not in d


def test_a_seeded_pair_is_cacheable_and_a_fresh_one_is_not(client):
    assert "s-maxage" in client.get("/api/ground?seed=10000042").headers["cache-control"]
    fresh = client.get("/api/ground")
    assert fresh.headers["cache-control"] == "no-store" and fresh.json()["seed"] >= G.PRACTICE_SEED_FLOOR


def _answers(real):
    return sorted(real.puzzle(SEED).answers)


def test_a_hit_and_a_miss_over_http(client, real, players):
    p = real.puzzle(SEED)
    hit = _answers(real)[0]
    miss = next(i for i in sorted(players) if i not in p.answers and i not in (p.a, p.b))
    r = client.post("/api/ground/play", json={"seed": SEED, "guess": {"id": hit, "t": 4000}}).json()
    assert r["hit"] is True and r["state"]["score"]["points"] == 100
    assert r["state"]["found"][0]["with_a"] and r["state"]["found"][0]["with_b"]
    r = client.post("/api/ground/play", json={"seed": SEED, "guesses": [{"id": hit, "t": 4000}],
                                              "guess": {"id": miss, "t": 9000}}).json()
    assert r["hit"] is False and r["kind"] in ("only_a", "only_b", "neither")
    assert r["state"]["score"]["points"] == 80 and r["state"]["wrong"][0]["person_id"] == miss
    assert not r["state"]["finished"] and r["state"]["unfound"] == [] and r["state"]["share"] is None


def test_the_whole_set_finishes_it_with_the_bonus_the_reveal_and_the_share(client, real):
    moves = [{"id": pid, "t": 1000 * (k + 1)} for k, pid in enumerate(_answers(real))]
    st = client.post("/api/ground/play", json={"seed": SEED, "guesses": moves[:-1], "guess": moves[-1]}
                     ).json()["state"]
    n = len(moves)
    assert st["finished"] and st["reason"] == "all" and st["elapsed_ms"] == 1000 * n
    assert st["score"]["bonus"] == G.LIMIT_SECONDS - n and st["score"]["points"] == 100 * n + G.LIMIT_SECONDS - n
    assert st["unfound"] == [] and st["share"].startswith("Fine Leg XI · Common Ground · 9 Oct 2026")


def test_time_up_and_giving_up_end_it_and_reveal_who_was_missed(client, real):
    first = _answers(real)[0]
    body = {"seed": SEED, "guesses": [{"id": first, "t": 2000}]}
    out = client.post("/api/ground/play", json={**body, "end": True, "end_t": G.LIMIT_MS}).json()["state"]
    assert out["finished"] and out["reason"] == "time" and out["elapsed_ms"] == G.LIMIT_MS
    assert len(out["unfound"]) == len(_answers(real)) - 1 and out["share"]
    quit_ = client.post("/api/ground/play", json={**body, "end": True, "end_t": 60_000}).json()["state"]
    assert quit_["reason"] == "gave_up" and quit_["elapsed_ms"] == 60_000


def test_a_move_the_rules_refuse_is_a_400_with_a_reason(client, real):
    p = real.puzzle(SEED)
    r = client.post("/api/ground/play", json={"seed": SEED, "guess": {"id": p.a, "t": 1}})
    assert r.status_code == 400 and "one of the two" in r.json()["detail"]
    assert client.post("/api/ground/play", json={"seed": SEED, "guess": {"id": "nobody", "t": 1}}).status_code == 400
    out_of_order = {"seed": SEED, "guesses": [{"id": _answers(real)[0], "t": 9000}],
                    "guess": {"id": _answers(real)[1], "t": 100}}
    assert client.post("/api/ground/play", json=out_of_order).status_code == 400


def test_a_time_past_the_limit_is_rejected_outright(client, real):
    r = client.post("/api/ground/play", json={"seed": SEED, "guess": {"id": _answers(real)[0],
                                                                       "t": G.LIMIT_MS + 1}})
    assert r.status_code == 422


def test_an_oversized_move_list_is_rejected_outright(client):
    r = client.post("/api/ground/play", json={"seed": SEED, "guesses": [{"id": "x", "t": 0}] * (G.MAX_GUESSES + 1)})
    assert r.status_code == 422


def test_a_missing_snapshot_is_a_503_not_a_crash(client, monkeypatch):
    monkeypatch.setattr(bingo_routes, "_facts", lambda: None)
    assert client.post("/api/ground/play", json={"seed": SEED}).status_code == 503


def test_the_old_chain_address_still_lands_somewhere():
    from web.app import app
    r = TestClient(app).get("/chain", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "/common"
