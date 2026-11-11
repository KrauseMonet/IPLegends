"""[A234] A legend's value to a twelve: runs a match over an average player, from the four
numbers the engine plays (A160), and the lobby picker ordered by it."""

import contextlib
import dataclasses
import datetime
import json
import pathlib
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from etl.feasibility import Card
from game import career as C
from game.__main__ import ATTACK_MIN_BALLS


def card(**kw):
    base = dict(fs_id=0, person_id="p", name="P", bat=0.1, positions=frozenset({1, 2, 3}),
                bat_scoring=0.0, bat_dismissal=1.0)
    return Card(**{**base, **kw})


def test_an_average_card_is_worth_nothing():
    assert C.legend_value(card()) == 0


def test_batting_is_worth_more_higher_up_the_order():
    top = card(bat_scoring=0.2)
    finisher = card(bat_scoring=0.2, positions=frozenset({5, 6, 7}))
    assert C.legend_value(top) == pytest.approx(0.2 * (21.6 + 22.1 + 20.3) / 3)   # A269's table at 1.0
    assert C.legend_value(top) > C.legend_value(finisher) > 0


def test_getting_out_more_costs_a_batter_and_taking_more_wickets_pays_a_bowler():
    assert C.legend_value(card(bat_dismissal=1.5)) < 0
    bowler = card(bat=None, bat_scoring=None, bowl=0.1, bowl_scoring=0.0, bowl_dismissal=1.5)
    assert C.legend_value(bowler) == pytest.approx(0.5 * C.LEAGUE_OUT_RATE * C.WICKET_RUNS * 24)


# [A269] An anchor faces more balls than his position's average, so his scoring counts over
# more of them -- the shortcut A234 used (one exposure a position, a linear wicket price)
# missed by up to 6 runs a match once A267 replayed real seasons.

def test_getting_out_less_also_means_facing_more_balls():
    anchor = card(bat_scoring=0.3, bat_dismissal=0.5)
    assert C.legend_value(anchor) > C.legend_value(card(bat_scoring=0.3)) + C.legend_value(
        card(bat_dismissal=0.5))      # the two halves are not additive: survival multiplies
    assert C._at_dismissal(C.BALLS_FACED_BY[1], 0.1) == C.BALLS_FACED_BY[1][0]   # flat beyond
    assert C._at_dismissal(C.BALLS_FACED_BY[1], 9.0) == C.BALLS_FACED_BY[1][-1]
    mid = C._at_dismissal(C.BALLS_FACED_BY[1], (0.85 * 1.0) ** 0.5)               # log midpoint
    assert mid == pytest.approx((C.BALLS_FACED_BY[1][5] + C.BALLS_FACED_BY[1][6]) / 2)


def test_the_value_is_what_the_engine_plays():
    """External to the formula: the batter played in an otherwise average side, paired
    against an average player on the same random draws, lands on the table's value and not
    on the old shortcut's (9.5 here, against the table's 15.1)."""
    import random
    import statistics
    from game.simulator import Player, play_innings, match_conditions
    from tools.snapshot_deck import read_document, model_from

    doc = read_document()
    if doc is None:
        pytest.skip("no deck snapshot")
    model = model_from(doc)
    anchor = card(bat_scoring=0.3, bat_dismissal=0.5, positions=frozenset({1}))
    diffs = []
    for i in range(800):
        xi = [Player(f"b{k}", 0.0, 0.0) for k in range(11)]
        cond = match_conditions(random.Random(i * 7 + 1))
        base = play_innings(model, xi, xi[:5], random.Random(i), conditions=cond).runs
        xi[0] = Player("L", 0.0, 0.0, bat_scoring=0.3, bat_dismissal=0.5)
        diffs.append(play_innings(model, xi, xi[:5], random.Random(i), conditions=cond).runs - base)
    old_shortcut = (0.3 - (0.5 - 1) * C.LEAGUE_OUT_RATE * C.WICKET_RUNS) * 21.3
    assert statistics.fmean(diffs) == pytest.approx(C.legend_value(anchor), abs=1.5)
    assert abs(statistics.fmean(diffs) - old_shortcut) > 3


def test_thin_bowling_is_not_counted_because_he_would_not_bowl():
    real = card(bowl=0.1, bowl_scoring=0.3, bowl_dismissal=1.0)
    thin = card(bowl=0.1, bowl_scoring=0.3, bowl_dismissal=1.0, bowl_balls=ATTACK_MIN_BALLS - 1)
    volume = card(bowl=0.1, bowl_scoring=0.3, bowl_dismissal=1.0, bowl_balls=ATTACK_MIN_BALLS)
    assert C.legend_value(real) == pytest.approx(0.3 * 24)
    assert C.legend_value(volume) == pytest.approx(0.3 * 24)
    assert C.legend_value(thin) == 0


def test_the_hall_route_carries_each_legends_value(monkeypatch):
    from tools.snapshot_deck import _card_doc
    from web import career_routes as R, auth

    monkeypatch.setenv("SESSION_SECRET", "test-only-legend-secret")
    good = card(bat_scoring=0.3)
    row = {"key": "k", "name": "T", "role": "opener", "debut": 2016, "overseas": False,
           "seasons": 1, "matches": 14, "runs": 500, "wickets": 0, "titles": 0,
           "peak_card": 90, "peak_year": 2016, "ending": "archive",
           "card": {"skills": [["anchor", 1, 1]],      # [A284] built with skills
                    "legend": {"rating": 90, "year": 2016, "role": "opener",
                               "card": _card_doc(good)}},
           "completed_at": datetime.datetime(2026, 10, 27)}
    plain = {**row, "key": "j", "card": {}}
    monkeypatch.setattr(R.db, "connection", contextlib.nullcontext)
    monkeypatch.setattr(R.career_results, "hall", lambda conn, account_id: [plain, row])
    from web.app import app
    with TestClient(app) as client:
        client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(1))
        got = client.get("/api/career/hall").json()
    assert got[0]["legend_value"] is None and got[0]["legend_group"] is None
    assert got[1]["legend_value"] == round(C.legend_value(good), 2)
    # [A269] the halves and the group ride along; an opener has no bowling to count
    assert got[1]["legend_bat"] == got[1]["legend_value"] and got[1]["legend_bowl"] is None
    assert (got[1]["legend_group"], got[1]["legend_group_order"]) == ("Batters", 0)


JS = pathlib.Path(__file__).resolve().parent.parent / "web" / "static" / "room.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_picker_lists_legends_most_valuable_first():
    body = re.search(r"function legendsByValue[\s\S]*?(?=\n// --- end legendsByValue)",
                     JS.read_text()).group(0)
    hall = [{"key": "a", "legend": True, "legend_value": 6.6},
            {"key": "b", "legend": False, "legend_value": None},
            {"key": "c", "legend": True, "legend_value": 7.8},
            {"key": "d", "legend": True, "legend_value": -2.0},
            {"key": "e", "legend": True, "legend_value": 7.2}]
    out = subprocess.run(["node", "-e", f"{body}; console.log(JSON.stringify("
                          f"legendsByValue({json.dumps(hall)}).map(c => c.key)))"],
                         capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == ["c", "e", "a", "d"]


# --- [A240] the five-legend picker is one account's choice, not every player's ---------------

from web import accounts as accounts_mod, admin  # noqa: E402


@pytest.fixture
def no_limit_env(monkeypatch):
    for var in (admin.LEGEND_LIMIT_ACCOUNT_IDS, admin.ADMIN_ACCOUNT_IDS, admin.FALLBACK_ADMIN_VAR):
        monkeypatch.delenv(var, raising=False)


def test_nobody_is_limited_unless_an_account_is_named(no_limit_env):
    assert admin.legend_limit(7) is None and admin.legend_limit(None) is None


def test_the_named_accounts_are_limited_and_others_are_not(no_limit_env, monkeypatch):
    monkeypatch.setenv(admin.LEGEND_LIMIT_ACCOUNT_IDS, " 17, 4 ,x, ")
    assert admin.legend_limit(17) == 3 == admin.legend_limit(4) == admin.LEGEND_PICKS
    # "7" is inside "17": whole ids only
    assert admin.legend_limit(7) is None and admin.legend_limit(1) is None


def test_the_admin_accounts_are_limited_when_no_list_is_set(no_limit_env, monkeypatch):
    monkeypatch.setenv(admin.ADMIN_ACCOUNT_IDS, "9")
    assert admin.legend_limit(9) == 3 and admin.legend_limit(8) is None
    monkeypatch.setenv(admin.LEGEND_LIMIT_ACCOUNT_IDS, "8")      # an explicit list replaces it
    assert admin.legend_limit(8) == 3 and admin.legend_limit(9) is None


def test_me_reports_the_limit_for_that_account_only(no_limit_env, monkeypatch):
    from web import app as app_mod
    from web import auth
    monkeypatch.setenv(admin.LEGEND_LIMIT_ACCOUNT_IDS, "7")
    monkeypatch.setattr(app_mod, "_db", contextlib.nullcontext)
    monkeypatch.setattr(accounts_mod, "get_account", lambda conn, i: accounts_mod.Account(
        account_id=i, username=f"u{i}", email="e@x.y"))
    with TestClient(app_mod.app) as client:
        got = {}
        for account in (7, 8):
            client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(account))
            got[account] = client.get("/api/auth/me").json()["legend_limit"]
        client.cookies.clear()
        got[None] = client.get("/api/auth/me").json()["legend_limit"]
    assert got == {7: 3, 8: None, None: None}


def _picker_js(call: str) -> list:
    body = re.search(r"function legendsByValue[\s\S]*?(?=\n// --- end legendsByValue)",
                     JS.read_text()).group(0)
    out = subprocess.run(["node", "-e", f"{body}; console.log(JSON.stringify({call}))"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


# [A254] seven legends in three roles: by value a 9.0, 8.0, 7.0, 6.0, 5.0, 3.0, 1.0
ROLE = {"a": "Opening batter", "b": "Spinner", "c": "Spinner", "d": "Opening batter",
        "e": "Opening batter", "f": "Fast bowler", "g": "Spinner"}
HALL7 = [{"key": k, "legend": True, "legend_value": v, "name": k.upper(), "role_label": "Finisher",
          "card": {"legend": {"year": 2012, "rating": 99, "role_label": ROLE[k]}}}
         for k, v in zip("abcdefg", (9.0, 3.0, 8.0, 7.0, 1.0, 6.0, 5.0))]


def _groups(mine, limit):
    return _picker_js(f"legendChoices({json.dumps(HALL7)}, {json.dumps(mine)}, {limit})"
                      ".map(g => [g.role, g.total, g.items.map(c => c.key)])")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_picker_groups_by_the_legend_s_role_best_role_first():
    # the season's role decides the group, not the career's creation role ("Finisher")
    assert _groups(None, "null") == [["Opening batter", 3, ["a", "d", "e"]],
                                     ["Spinner", 3, ["c", "g", "b"]],
                                     ["Fast bowler", 1, ["f"]]]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_a_limited_picker_lists_the_best_in_each_role_and_keeps_the_one_you_brought():
    assert _groups(None, 1) == [["Opening batter", 3, ["a"]], ["Spinner", 3, ["c"]],
                                ["Fast bowler", 1, ["f"]]]
    assert _groups({"name": "B", "year": 2012, "rating": 99}, 1) == [
        ["Opening batter", 3, ["a"]], ["Spinner", 3, ["c", "b"]], ["Fast bowler", 1, ["f"]]]
    assert _groups(None, 2)[0] == ["Opening batter", 3, ["a", "d"]]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_an_unlimited_picker_lists_every_legend():
    got = _picker_js(f"legendChoices({json.dumps(HALL7)}, null, undefined)"
                     ".flatMap(g => g.items.map(c => c.key))")
    assert sorted(got) == list("abcdefg")


def test_the_lobby_picker_is_fed_through_the_account_s_limit():
    body = re.search(r"function renderLobbyLegends[\s\S]*?\n}\n", JS.read_text()).group(0)
    assert "legendChoices(ROOM_HALL, r.legends[MY_PID], ME.legend_limit)" in body
    assert "legendsByValue(ROOM_HALL).map" not in body


# --- [A269] the two halves apart, and five groups in a fixed order ---------------------------

def test_the_two_halves_add_up_to_the_value_and_each_is_its_own_discipline():
    allround = card(bat_scoring=0.2, bowl=0.1, bowl_scoring=0.1, bowl_dismissal=1.5)
    bat, bowl = C.legend_values(allround)
    assert bat == pytest.approx(C.legend_value(card(bat_scoring=0.2)))
    assert bowl == pytest.approx((0.1 + 0.5 * C.LEAGUE_OUT_RATE * C.WICKET_RUNS) * C.BALLS_BOWLED)
    assert C.legend_value(allround) == pytest.approx(bat + bowl)
    # bowling that would not be bowled is None, not a zero that reads as average bowling
    assert C.legend_values(card(bat_scoring=0.2))[1] is None
    assert C.legend_values(card(bowl=0.1, bowl_scoring=0.3, bowl_dismissal=1.0, bowl_balls=40))[1] is None


def test_every_career_role_has_a_group_and_a_full_time_bowler_is_an_all_rounder():
    groups = {role: C.legend_group(role) for role in C.ROLES}
    assert groups == {"opener": (0, "Batters"), "middle": (0, "Batters"),
                      "finisher": (0, "Batters"), "keeper": (1, "Wicketkeeper-batters"),
                      "allrounder": (2, "All-rounders"), "spin": (3, "Spinners"),
                      "pace": (4, "Fast bowlers")}
    # career_results.legend stores a full-time bowler's role as "allrounder" (A238)
    assert C.legend_group("allrounder") == (2, "All-rounders")
    assert C.legend_group("nonsense") == (5, "Other")


GROUPED = [{"key": k, "legend": True, "legend_value": v, "name": k.upper(),
            "legend_group": g, "legend_group_order": o,
            "card": {"legend": {"year": 2012, "rating": 99}}}
           for k, v, g, o in (("p", 12.0, "Fast bowlers", 4), ("b1", 3.0, "Batters", 0),
                              ("a", 9.0, "All-rounders", 2), ("b2", 5.0, "Batters", 0),
                              ("b3", 4.0, "Batters", 0), ("b4", 8.0, "Batters", 0))]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_groups_come_in_the_server_s_order_not_by_their_best_legend():
    got = _picker_js(f"legendChoices({json.dumps(GROUPED)}, null, 3)"
                     ".map(g => [g.role, g.total, g.items.map(c => c.key)])")
    assert got == [["Batters", 4, ["b4", "b2", "b3"]], ["All-rounders", 1, ["a"]],
                   ["Fast bowlers", 1, ["p"]]]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_an_all_rounder_shows_his_batting_and_bowling_apart_and_others_one_value():
    allr = {"legend_value": 10.3, "legend_bat": 6.1, "legend_bowl": 4.2,
            "legend_group": "All-rounders"}
    thin = {**allr, "legend_value": 6.1, "legend_bowl": None}
    batter = {"legend_value": 8.0, "legend_bat": 8.0, "legend_bowl": None,
              "legend_group": "Batters"}
    got = _picker_js("[" + ",".join(f"legendValueText({json.dumps(c)})"
                                    for c in (allr, thin, batter)) + "]")
    assert got == [" · bat +6.1 · bowl +4.2 = +10.3 runs a match",
                   " · bat +6.1 · bowl — = +6.1 runs a match",
                   " · +8.0 runs a match"]


# --- [A269] the legend you brought is marked by its career, not by name, year and rating ---

def test_the_room_remembers_which_career_and_tells_only_its_own_seat(monkeypatch):
    import types
    from web import app as app_mod, auth, rooms

    stored = {}

    def capture(conn, code, player_id, legend):
        stored.update(legend)
        raise rooms.RoomError("stop here")
    monkeypatch.setenv("SESSION_SECRET", "test-only-legend-secret")
    monkeypatch.setattr(app_mod, "_db", contextlib.nullcontext)
    monkeypatch.setattr(app_mod.career_results, "legend_of",
                        lambda conn, account_id, key: {"name": "K", "rating": 99, "year": 2015,
                                                       "role_label": "All-rounder", "card": {}})
    monkeypatch.setattr(rooms, "set_legend", capture)
    with TestClient(app_mod.app) as client:
        client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(1))
        r = client.post("/api/rooms/ABC123/legend", json={"player_id": "p1", "career": "a" * 64})
    assert r.status_code == 400 and "stop here" in r.text, r.text
    assert stored["career"] == "a" * 64

    room = types.SimpleNamespace(players={"p1": 0, "p2": 0}, legends={
        "p1": {**stored}, "p2": {**stored, "career": "b" * 64}, "gone": stored})
    out = app_mod._room_legends_out(room, "p1")
    assert out["p1"].career == "a" * 64 and out["p2"].career is None and "gone" not in out
    assert app_mod._room_legends_out(room, None)["p1"].career is None


SAME = [{"key": k, "legend": True, "legend_value": v, "name": "K", "legend_group": "All-rounders",
         "legend_group_order": 2, "card": {"legend": {"year": 2015, "rating": 99}}}
        for k, v in (("k1", 20.0), ("k2", 19.0), ("k3", 18.0), ("k4", 17.0))]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_one_of_several_alike_legends_is_marked_by_its_career_key():
    mine = {"name": "K", "year": 2015, "rating": 99, "career": "k4"}
    got = _picker_js(f"[legendChoices({json.dumps(SAME)}, {json.dumps(mine)}, 3)[0].items.map(c => c.key),"
                     f" {json.dumps(SAME)}.filter(c => legendIsMine(c, {json.dumps(mine)})).map(c => c.key)]")
    assert got == [["k1", "k2", "k3", "k4"], ["k4"]]
    # a room from before the key still finds him by name, year and rating
    old = {"name": "K", "year": 2015, "rating": 99}
    assert _picker_js(f"{json.dumps(SAME)}.filter(c => legendIsMine(c, {json.dumps(old)})).length") == 4


def test_his_settled_share_is_valued_as_the_shift_of_level_the_engine_plays():
    """[A283] The engine takes his settled share off every ball (A260): aim minus its first
    half, dismissal odds times exp(-second). The value reads the same shift."""
    import math
    plain = card(bat_scoring=0.10, bat_dismissal=0.9, positions=frozenset({3, 4}))
    lean = dataclasses.replace(plain, settle_offset=(0.04, 0.08))
    want = C.legend_bat_value(0.10 - 0.04, 0.9 * math.exp(-0.08), plain.positions)
    assert C.legend_values(lean)[0] == pytest.approx(want)
    assert C.legend_values(lean)[0] < C.legend_values(plain)[0]
    # a lean that only moves WHERE runs come is worth nothing in the formula
    moved = dataclasses.replace(plain, style_bat=(0.05, -0.04), burn=(-0.03, 0.02))
    assert C.legend_values(moved)[0] == C.legend_values(plain)[0]


def test_a_stored_legend_keeps_its_leans_through_the_room(monkeypatch):
    """[A283] The room builds a legend from the saved card document; leans survive the trip
    and come back as tuples (a frozen card must hash)."""
    from tools.snapshot_deck import _card_doc
    from web import room_auction as RA
    c = dataclasses.replace(card(bat_scoring=0.1, bat_dismissal=0.9, positions=frozenset({2})),
                            style_bat=(0.05, -0.04), settle_offset=(0.01, 0.02))

    class P: is_cpu = False
    class Room: players = {"p1": P()}; legends = {"p1": {"name": "N", "card": _card_doc(c)}}
    got = RA.legend_cards(Room())["p1"]
    assert got.style_bat == (0.05, -0.04) and got.settle_offset == (0.01, 0.02)
    hash(got)
