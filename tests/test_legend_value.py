"""[A234] A legend's value to a twelve: runs a match over an average player, from the four
numbers the engine plays (A160), and the lobby picker ordered by it."""

import contextlib
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
    assert C.legend_value(top) == pytest.approx(0.2 * (21.3 + 21.9 + 19.5) / 3)
    assert C.legend_value(top) > C.legend_value(finisher) > 0


def test_getting_out_more_costs_a_batter_and_taking_more_wickets_pays_a_bowler():
    assert C.legend_value(card(bat_dismissal=1.5)) < 0
    bowler = card(bat=None, bat_scoring=None, bowl=0.1, bowl_scoring=0.0, bowl_dismissal=1.5)
    assert C.legend_value(bowler) == pytest.approx(0.5 * C.LEAGUE_OUT_RATE * C.WICKET_RUNS * 24)


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
           "card": {"legend": {"rating": 90, "year": 2016, "card": _card_doc(good)}},
           "completed_at": datetime.datetime(2026, 10, 27)}
    plain = {**row, "key": "j", "card": {}}
    monkeypatch.setattr(R.db, "connection", contextlib.nullcontext)
    monkeypatch.setattr(R.career_results, "hall", lambda conn, account_id: [plain, row])
    from web.app import app
    with TestClient(app) as client:
        client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(1))
        got = client.get("/api/career/hall").json()
    assert got[0]["legend_value"] is None
    assert got[1]["legend_value"] == round(C.legend_value(good), 2)


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
    assert admin.legend_limit(17) == 5 == admin.legend_limit(4) == admin.LEGEND_PICKS
    # "7" is inside "17": whole ids only
    assert admin.legend_limit(7) is None and admin.legend_limit(1) is None


def test_the_admin_accounts_are_limited_when_no_list_is_set(no_limit_env, monkeypatch):
    monkeypatch.setenv(admin.ADMIN_ACCOUNT_IDS, "9")
    assert admin.legend_limit(9) == 5 and admin.legend_limit(8) is None
    monkeypatch.setenv(admin.LEGEND_LIMIT_ACCOUNT_IDS, "8")      # an explicit list replaces it
    assert admin.legend_limit(8) == 5 and admin.legend_limit(9) is None


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
    assert got == {7: 5, 8: None, None: None}


def _picker_js(call: str) -> list:
    body = re.search(r"function legendsByValue[\s\S]*?(?=\n// --- end legendsByValue)",
                     JS.read_text()).group(0)
    out = subprocess.run(["node", "-e", f"{body}; console.log(JSON.stringify({call}))"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


HALL7 = [{"key": k, "legend": True, "legend_value": v, "name": k.upper(),
          "card": {"legend": {"year": 2012, "rating": 99}}}
         for k, v in zip("abcdefg", (9.0, 3.0, 8.0, 7.0, 1.0, 6.0, 5.0))]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_a_limited_picker_lists_the_best_few_and_keeps_the_one_you_brought():
    def run(mine, limit):
        return _picker_js(f"legendChoices({json.dumps(HALL7)}, {json.dumps(mine)}, {limit})"
                          ".map(c => c.key)")
    assert run(None, 5) == ["a", "c", "d", "f", "g"]
    assert run({"name": "B", "year": 2012, "rating": 99}, 5) == ["a", "c", "d", "f", "g", "b"]
    assert run({"name": "A", "year": 2012, "rating": 99}, 5) == ["a", "c", "d", "f", "g"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_an_unlimited_picker_lists_every_legend():
    for limit in ("null", "undefined"):
        got = _picker_js(f"legendChoices({json.dumps(HALL7)}, null, {limit}).map(c => c.key)")
        assert got == ["a", "c", "d", "f", "g", "b", "e"]


def test_the_lobby_picker_is_fed_through_the_account_s_limit():
    body = re.search(r"function renderLobbyLegends[\s\S]*?\n}\n", JS.read_text()).group(0)
    assert "legendChoices(ROOM_HALL, r.legends[MY_PID], ME.legend_limit)" in body
    assert "legendsByValue(ROOM_HALL).map" not in body
