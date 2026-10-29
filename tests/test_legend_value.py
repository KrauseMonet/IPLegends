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
