"""[A319] A real match rebuilt from the archive, and the replay and what-if routes.

The counting rules are tested on hand-built balls, where each rule is obvious; the rebuilt
matches are tested against the public record (check 7's lesson: the figure being claimed, not a
neighbouring one) and against totals counted a different way from the same frozen file.
"""

from __future__ import annotations

import pytest

from game import real_match as RM
from tools import snapshot_matches


def doc_of(balls, people=("a", "b", "c", "d", "e", "f"), kinds=("caught", "run out", "bowled"), xi=None):
    m = {"id": "m", "day": "2020-01-01", "year": 2020, "ground": "G", "city": "C", "stage": None, "number": 1,
         "a": 1, "b": 2, "toss": None, "elect": None, "winner": 1, "result": "runs", "margin": 5, "by": "runs",
         "pom": None, "xi": xi or {}, "inns": [{"fs": 1, "so": 0, "p": list(people), "b": balls}]}
    return RM.Doc(list(kinds), {"1": ["X", 2020, "X Team"], "2": ["Y", 2020, "Y Team"]},
                  {p: p.upper() for p in people}, {"m": m})


def ball(over, st, ns, bw, runs=0, wd=0, nb=0, by=0, lb=0, out=-1, kind=-1):
    return [over, st, ns, bw, runs, wd, nb, by, lb, 0, out, kind]


def inn(balls, **kw):
    d = doc_of(balls, **kw)
    return RM.innings(d, d.matches["m"]["inns"][0])


def card(i, name):
    return next(c for c in i.batting if c.player.name == name)


def bowler(i, name):
    return next(c for c in i.bowling if c.player.name == name)


def test_a_wide_is_not_a_ball_faced_and_a_no_ball_is():
    i = inn([ball(0, 0, 1, 2, wd=1), ball(0, 0, 1, 2, runs=4, nb=1), ball(0, 0, 1, 2, runs=1)])
    a = card(i, "A")
    assert (a.runs, a.balls, a.fours) == (5, 2, 1)
    assert i.balls == 1 and i.extras == 2 and i.runs == 7


def test_a_bowler_is_charged_wides_and_no_balls_never_byes_or_leg_byes():
    i = inn([ball(0, 0, 1, 2, wd=2), ball(0, 0, 1, 2, nb=1), ball(0, 0, 1, 2, by=4), ball(0, 0, 1, 2, lb=1)])
    c = bowler(i, "C")
    assert (c.runs, c.balls) == (3, 2)
    assert i.runs == 8


def test_a_run_out_is_a_wicket_but_not_the_bowlers():
    i = inn([ball(0, 0, 1, 2, out=1, kind=1), ball(0, 0, 3, 2, out=0, kind=0)])
    assert i.wickets == 2 and bowler(i, "C").wickets == 1
    assert card(i, "B").out and card(i, "A").out


def test_the_batting_order_is_who_reached_the_crease_first_at_either_end():
    i = inn([ball(0, 1, 0, 2), ball(0, 3, 0, 2)])     # B on strike, A non-striker, then D replaces B
    assert [c.player.name for c in i.batting] == ["B", "A", "D"]


def test_a_named_player_who_never_batted_follows_as_did_not_bat():
    d = doc_of([ball(0, 0, 1, 2)], xi={"1": ["a", "b", "e", "f"]})
    i = RM.innings(d, d.matches["m"]["inns"][0], ["e", "f"])
    assert [c.player.name for c in i.batting] == ["A", "B", "E", "F"]
    assert not card(i, "E").faced_any


def test_a_wide_between_two_wickets_breaks_a_hat_trick_and_a_wicket_off_a_wide_counts():
    broken = inn([ball(0, 0, 1, 2, out=0, kind=2), ball(0, 3, 1, 2, wd=1),
                  ball(0, 3, 1, 2, out=3, kind=2), ball(0, 4, 1, 2, out=4, kind=2)])
    assert not [m for m in broken.milestones if m.kind == "hattrick"]
    counted = inn([ball(0, 0, 1, 2, wd=1, out=0, kind=0), ball(0, 3, 1, 2, out=3, kind=2),
                   ball(0, 4, 1, 2, out=4, kind=2)], people=("a", "b", "c", "d", "e", "f"),
                  kinds=("stumped", "run out", "bowled"))
    assert [m.kind for m in counted.milestones] == ["hattrick"]


def test_three_in_an_over_not_in_a_row_is_three_in_the_over():
    i = inn([ball(0, 0, 1, 2, out=0, kind=2), ball(0, 3, 1, 2, runs=1), ball(0, 1, 3, 2, out=1, kind=2),
             ball(0, 4, 3, 2, out=4, kind=2)])
    assert [m.kind for m in i.milestones] == ["three_in_over"]


def test_a_fifty_fires_on_the_ball_that_crosses_fifty():
    balls = [ball(0, 0, 1, 2, runs=6)] * 8 + [ball(1, 0, 1, 2, runs=4)]
    i = inn(balls)
    f = [m for m in i.milestones if m.kind == "fifty"]
    assert len(f) == 1 and f[0].faced == 9 and f[0].runs == 52 and f[0].balls == 9


def test_only_completed_overs_are_logged_and_the_crease_is_who_faces_next():
    full = [ball(0, 0, 1, 2, runs=1)] + [ball(0, 1, 0, 2)] * 5
    i = inn(full + [ball(1, 0, 1, 3, runs=2), ball(1, 0, 1, 3)])
    assert len(i.over_log) == 1
    o = i.over_log[0]
    assert (o.runs, o.over_runs, o.balls) == (1, 1, 6)
    assert o.striker.name == "A"                     # the next over's first ball is A's
    assert [(b.name, b.runs, b.balls) for b in o.batters] == [("A", 1, 1), ("B", 0, 5)]


def test_a_six_ball_last_over_is_logged():
    i = inn([ball(0, 0, 1, 2)] * 6)
    assert len(i.over_log) == 1


# --------------------------------------------------------------------------- the frozen archive

@pytest.fixture(scope="module")
def real():
    raw = snapshot_matches.read()
    if raw is None:
        pytest.skip("no real_matches snapshot")
    return RM.Doc.of(raw)


def final(real, year):
    return [m for m in real.matches.values() if m["year"] == year][-1]["id"]


def test_the_2016_final_reads_as_the_public_record(real):
    r = RM.result(real, final(real, 2016))
    assert r.stage == "Final"
    assert (r.home.name, r.home_runs, r.home_wickets) == ("Sunrisers Hyderabad", 208, 7)
    assert (r.away.name, r.away_runs, r.away_wickets) == ("Royal Challengers Bangalore", 200, 7)
    assert r.winner is r.home and r.margin.endswith("by 8 runs")
    got = {c.player.name: (c.runs, c.balls) for c in r.home_innings.batting + r.away_innings.batting}
    assert got["David Warner"] == (69, 38) and got["Chris Gayle"] == (76, 38) and got["Virat Kohli"] == (54, 35)
    assert len(r.home_innings.batting) == 11 and len(r.away_innings.batting) == 11


def test_gayles_175_is_rebuilt_as_175_not_out_off_66(real):
    hit = None
    for mid, m in real.matches.items():
        if m["day"] == "2013-04-23":
            r = RM.result(real, mid)
            hit = hit or next((c for c in r.home_innings.batting if c.runs == 175), None)
    assert hit and hit.balls == 66 and not hit.out and hit.sixes == 17


def test_every_innings_total_is_its_batters_plus_its_extras_and_its_overs_log_climbs_to_it(real):
    checked = 0
    for mid in real.matches:
        r = RM.result(real, mid)
        for i in (r.home_innings, r.away_innings):
            if i is None:
                continue
            assert sum(c.runs for c in i.batting) + i.extras == i.runs, mid
            assert sum(c.wickets for c in i.bowling) <= i.wickets, mid
            if i.over_log:
                runs = [o.runs for o in i.over_log]
                assert runs == sorted(runs) and runs[-1] <= i.runs, mid
                assert sum(o.over_runs for o in i.over_log) == i.over_log[-1].runs, mid
            checked += 1
    assert checked > 2400


def test_a_tied_match_carries_its_super_overs_and_the_eliminator_wins(real):
    ties = [mid for mid, m in real.matches.items() if m["result"] == "tie"]
    assert len(ties) == 16
    for mid in ties:
        r = RM.result(real, mid)
        assert r.super_overs and r.winner is not None
        last = r.super_overs[-1]
        if last.winner is None:          # a tied super over decided on boundaries (2014)
            assert "boundar" in r.margin, mid
        else:
            assert last.winner is r.winner, mid


# --------------------------------------------------------------------------- the routes

@pytest.fixture(scope="module")
def client(real):
    from fastapi.testclient import TestClient
    from web.app import app
    with TestClient(app) as c:
        yield c


def test_a_season_lists_every_match(client, real):
    j = client.get("/api/almanack/matches?year=2016").json()
    assert len(j["matches"]) == sum(1 for m in real.matches.values() if m["year"] == 2016)
    assert j["matches"][-1]["stage"] == "Final"


def test_a_match_names_both_elevens_in_the_real_batting_order(client, real):
    j = client.get(f"/api/almanack/match/{final(real, 2016)}").json()
    srh, rcb = j["sides"]
    assert srh["batted_first"] and not rcb["batted_first"]
    assert [p["name"] for p in rcb["players"][:3]] == ["Chris Gayle", "Virat Kohli", "AB de Villiers"]
    assert len(srh["players"]) == 11 and j["player_of_match"] == "Ben Cutting"
    assert j["result"]["home_score"] == "208/7"


def test_an_unknown_match_is_404(client):
    assert client.get("/api/almanack/match/nope").status_code == 404
    assert client.post("/api/almanack/whatif", json={"id": "nope"}).status_code == 404


def test_the_same_what_if_and_seed_play_the_same_match(client, real):
    body = {"id": final(real, 2016), "seed": 42}
    a, b = client.post("/api/almanack/whatif", json=body).json(), client.post("/api/almanack/whatif", json=body).json()
    assert a["result"]["home_score"] == b["result"]["home_score"] and a["seed"] == 42


def test_odds_do_not_depend_on_the_watched_seed(client, real):
    body = {"id": final(real, 2016), "swaps": []}
    o = client.post("/api/almanack/odds", json=body).json()
    assert o["home_wins"] + o["away_wins"] + o["ties"] == o["runs"] == 100
    from web import replay_routes
    replay_routes._odds.cache_clear()           # played again from nothing, not read from the cache
    assert client.post("/api/almanack/odds", json={**body, "seed": 7}).json() == o


def _side(client, real):
    j = client.get(f"/api/almanack/match/{final(real, 2016)}").json()
    return j["sides"]


def test_a_swap_takes_the_players_place_in_the_order(client, real):
    srh, rcb = _side(client, real)
    hayden = client.get("/api/almanack/cards?q=hayden").json()["cards"][0]
    body = {"id": final(real, 2016), "seed": 3,
            "swaps": [{"fs": srh["fs"], "out": srh["players"][0]["person"], "in_fs": hayden[0], "in_person": hayden[1]}]}
    j = client.post("/api/almanack/whatif", json=body).json()
    assert j["swaps"][0].startswith("Matthew Hayden") and j["swaps"][0].endswith("for David Warner")
    assert j["result"]["home_innings"]["batting"][0]["name"] == "Matthew Hayden"


def test_a_player_cannot_play_for_both_sides(client, real):
    srh, rcb = _side(client, real)
    body = {"id": final(real, 2016), "swaps": [{"fs": srh["fs"], "out": srh["players"][0]["person"],
                                                "in_fs": rcb["fs"], "in_person": rcb["players"][0]["person"]}]}
    r = client.post("/api/almanack/whatif", json=body)
    assert r.status_code == 400 and "both sides" in r.json()["detail"]


def test_a_side_left_with_four_bowlers_is_refused(client, real):
    srh, _rcb = _side(client, real)
    seen, bats = set(), []
    pool = [c for q in ("sharma", "pant", "gill", "samson", "rahane")
            for c in client.get(f"/api/almanack/cards?q={q}").json()["cards"]]
    for c in pool:
        if not c[7] and c[1] not in seen and c[1] not in {p["person"] for s in _side(client, real) for p in s["players"]}:
            seen.add(c[1])
            bats.append(c)
    bowlers = [p for p in srh["players"] if p["bowls"]]
    need = len(bowlers) - 4
    swaps = [{"fs": srh["fs"], "out": b["person"], "in_fs": c[0], "in_person": c[1]}
             for b, c in zip(bowlers[:need], bats)]
    assert len(swaps) == need >= 1
    r = client.post("/api/almanack/whatif", json={"id": final(real, 2016), "swaps": swaps})
    assert r.status_code == 400 and "fewer than 5 bowlers" in r.json()["detail"]


def test_a_card_search_needs_two_letters_and_finds_by_any_part_of_the_name(client):
    assert client.get("/api/almanack/cards?q=k").json()["cards"] == []
    names = {c[2] for c in client.get("/api/almanack/cards?q=villiers").json()["cards"]}
    assert names == {"AB de Villiers"}
