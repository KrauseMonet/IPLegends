"""[A272] Extra legend seasons: a saved career may carry seasons other than its best-graded
one as friends'-auction legends, asked for by year, and the lobby picker always lists them.

The SQL tests are opt-in, as in tests/test_career_results.py: point IPLEGENDS_SCRATCH_DB at a
THROWAWAY, fully migrated database. Each runs in a transaction that is rolled back.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from game import career as C
from tools.snapshot_deck import deck_from, model_from, read_document
from web import accounts, career_results as CR, career_routes as R

DOC = read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="no deck snapshot")
SCRATCH = os.environ.get("IPLEGENDS_SCRATCH_DB")
needs_pg = pytest.mark.skipif(
    not SCRATCH, reason="set IPLEGENDS_SCRATCH_DB to a throwaway, fully migrated database")
JS = Path(__file__).resolve().parents[1] / "web" / "static" / "room.js"


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-only-career-secret")


@pytest.fixture(scope="module")
def finished():
    deck, model = deck_from(DOC), model_from(DOC)
    seed, setup = 7, C.Setup("T allrounder", "allrounder", 2014, False)
    career, _ = C.auto_career(deck, model, seed, setup, "strength")
    return seed, setup, career


def _other_year(career):
    """A measured season that is NOT the career's legend season."""
    best = C.legend_card(career)[0].year
    return next(r for r in career.seasons if r.measured and r.year != best)


def test_a_named_year_cards_that_season_with_its_own_numbers(finished):
    _, _, career = finished
    r = _other_year(career)
    rec, card = C.legend_card(career, r.year)
    assert rec is r and card.display == r.display
    want = dict(zip(("bat_scoring", "bat_dismissal", "bowl_scoring", "bowl_dismissal"), r.measured))
    assert all(getattr(card, k) == v for k, v in want.items() if v is not None)
    assert CR.legend(career, r.year)["year"] == r.year
    assert CR.legend(career)["year"] != r.year              # the best season is unchanged


def test_a_year_with_no_measured_season_has_no_legend(finished):
    _, _, career = finished
    assert C.legend_card(career, 1990) is None
    assert CR.legend(career, 1990) is None


def test_the_payload_decodes_to_the_same_career_as_the_signed_token(finished):
    seed, setup, career = finished
    token = R.make_token(seed, setup, career, [])
    assert R.decode_payload(token.rsplit(".", 1)[0]) == R.read_token(token)


def test_the_saved_card_keeps_every_measured_season(finished):
    """[A273] Every measured season is kept, carded as its own legend, so any season can be
    added later without the career's token."""
    _, _, career = finished
    kept = CR.card(career)["season_legends"]
    measured = [r for r in career.seasons if r.measured]
    assert len(measured) > 1
    assert [x["year"] for x in kept] == [r.year for r in measured]
    assert all(x == CR.legend(career, x["year"]) for x in kept)


# --- the database --------------------------------------------------------------------------


@pytest.fixture
def pg():
    import psycopg
    conn = psycopg.connect(SCRATCH)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _saved(pg, finished, name):
    seed, setup, career = finished
    me = accounts.create_account(pg, name, f"{name}@example.com", "password1").account_id
    key = CR.natural_key(R.make_token(seed, setup, career, []))
    CR.save(pg, me, key, career)
    return me, key, career


@needs_pg
def test_an_extra_season_is_brought_by_its_own_key(pg, finished):
    me, key, career = _saved(pg, finished, "extraowner")
    y = _other_year(career).year
    CR.add_extra_legend(pg, me, key, y)
    assert CR.legend_of(pg, me, CR.extra_key(key, y)) == CR.legend(career, y)
    assert CR.legend_of(pg, me, key) == CR.legend(career)          # the best season still
    assert CR.legend_of(pg, me, CR.extra_key(key, 1990)) is None
    assert CR.legend_of(pg, me, key + "@x") is None
    other = accounts.create_account(pg, "extrathief", "t@example.com", "password1").account_id
    assert CR.legend_of(pg, other, CR.extra_key(key, y)) is None


@needs_pg
def test_adding_a_year_twice_keeps_one_and_the_legend_s_own_year_is_refused(pg, finished):
    me, key, career = _saved(pg, finished, "extratwice")
    y = _other_year(career).year
    CR.add_extra_legend(pg, me, key, y)
    CR.add_extra_legend(pg, me, key, y)
    (extras,), = pg.execute("select card -> 'extra_legends' from career_results"
                            " where account_id = %s", (me,)).fetchall()
    assert [x["year"] for x in extras] == [y]
    with pytest.raises(ValueError):
        CR.add_extra_legend(pg, me, key, C.legend_card(career)[0].year)
    with pytest.raises(ValueError):
        CR.add_extra_legend(pg, me, "0" * 64, y)          # a career not saved here


@needs_pg
def test_a_season_is_added_from_the_saved_card_without_a_token(pg, finished):
    me, key, career = _saved(pg, finished, "notoken")
    # the LATEST non-legend season: the first stored one would let "take any entry" pass
    best = C.legend_card(career)[0].year
    y = max(r.year for r in career.seasons if r.measured and r.year != best)
    assert y != CR.card(career)["season_legends"][0]["year"]
    assert CR.add_extra_legend(pg, me, key, y) == CR.legend(career, y)
    assert CR.legend_of(pg, me, CR.extra_key(key, y)) == CR.legend(career, y)


@needs_pg
def test_a_career_saved_before_every_season_was_kept_needs_its_token(pg, finished):
    me, key, career = _saved(pg, finished, "oldsave")
    pg.execute("update career_results set card = card - 'season_legends'"
               " where account_id = %s", (me,))
    y = _other_year(career).year
    with pytest.raises(ValueError, match="token"):
        CR.add_extra_legend(pg, me, key, y)
    assert CR.add_extra_legend(pg, me, key, y, career) == CR.legend(career, y)


@needs_pg
def test_the_hall_reads_the_card_without_every_season(pg, finished):
    """Every season is ~0.8 kB; a hundred careers' worth must not travel on each hall read."""
    me, key, career = _saved(pg, finished, "hallslim")
    (row,) = CR.hall(pg, me)
    assert "season_legends" not in row["card"] and row["card"]["legend"] == CR.legend(career)


# --- the picker ----------------------------------------------------------------------------


def _picker_js(call: str):
    body = re.search(r"function legendsByValue[\s\S]*?(?=\n// --- end legendsByValue)",
                     JS.read_text()).group(0)
    out = subprocess.run(["node", "-e", f"{body}; console.log(JSON.stringify({call}))"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _entry(key, value, extras=()):
    return {"key": key, "legend": True, "legend_value": value, "name": key.upper(),
            "legend_group": "All-rounders", "legend_group_order": 2,
            "card": {"legend": {"year": 2015, "rating": 98}}, "extras": list(extras)}


# four all-rounders by value 9, 8, 7, 6; the weakest career carries a 2014 extra worth 5
HALL = [_entry("a", 9.0), _entry("b", 8.0), _entry("c", 7.0),
        _entry("d", 6.0, [{"key": "d@2014", "name": "D", "legend_value": 5.0,
                           "legend_group": "All-rounders", "legend_group_order": 2,
                           "card": {"legend": {"year": 2014, "rating": 90}}}])]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_an_extra_is_listed_past_the_cut_in_its_group():
    got = _picker_js(f"legendChoices({json.dumps(HALL)}, null, 3)"
                     ".map(g => [g.role, g.total, g.items.map(c => c.key)])")
    assert got == [["All-rounders", 4, ["a", "b", "c", "d@2014"]]]
    unlimited = _picker_js(f"legendChoices({json.dumps(HALL)}, null, null)[0].items.map(c => c.key)")
    assert unlimited == ["a", "b", "c", "d", "d@2014"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_a_brought_extra_is_the_one_marked_not_its_career():
    got = _picker_js(f"legendChoices({json.dumps(HALL)}, {{career: 'd@2014'}}, 3)[0].items"
                     ".filter(c => legendIsMine(c, {career: 'd@2014'})).map(c => c.key)")
    assert got == ["d@2014"]


def test_the_hall_route_sends_each_extra_with_its_own_key_and_value(finished, monkeypatch):
    import contextlib
    import datetime
    from fastapi.testclient import TestClient
    from web import auth
    from web.app import app
    _, _, career = finished
    y = _other_year(career).year
    card = CR.card(career)
    card["extra_legends"] = [CR.legend(career, y)]
    row = {"key": "k" * 64, "name": "T", "role": "allrounder", "debut": 2014, "overseas": False,
           "seasons": 10, "matches": 140, "runs": 100, "wickets": 0, "ending": "archive",
           "titles": 0, "peak_card": 90, "peak_year": 2020, "card": card,
           "completed_at": datetime.datetime(2026, 11, 8)}
    monkeypatch.setattr(R.db, "connection", contextlib.nullcontext)
    monkeypatch.setattr(R.career_results, "hall", lambda conn, account_id: [row])
    client = TestClient(app)
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(1))
    (h,) = client.get("/api/career/hall").json()
    (x,) = h["extras"]
    assert x["key"] == CR.extra_key(row["key"], y) and x["card"]["legend"]["year"] == y
    bat, bowl = C.legend_values(C.legend_card(career, y)[1])
    assert x["legend_value"] == round(bat + (bowl or 0.0), 2)
    assert h["legend_value"] != x["legend_value"]                # its own value, not the career's


def test_a_room_accepts_an_extra_season_s_key_and_refuses_anything_else():
    """[A272] The lobby brings an extra season by '<key>@<year>'; the request's own pattern
    refused it, so bringing 2014 failed before the server ever looked the season up."""
    from pydantic import ValidationError
    from web.app import RoomLegendIn
    key = "a" * 64
    for ok in (key, CR.extra_key(key, 2014), None):
        assert RoomLegendIn(player_id="p", career=ok).career == ok
    for bad in (key + "@14", key + "@2014x", "a" * 63, key + "@"):
        with pytest.raises(ValidationError):
            RoomLegendIn(player_id="p", career=bad)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_a_list_shaped_refusal_is_worded_not_printed_as_an_object():
    src = (Path(__file__).resolve().parents[1] / "web" / "static" / "common.js").read_text()
    body = re.search(r"function detailText[\s\S]*?\n}\n", src).group(0)
    call = ("[detailText([{msg: 'String should match pattern'}], 'x'), "
            "detailText('Sign in first', 'x'), detailText(undefined, 'Bad')]")
    out = subprocess.run(["node", "-e", f"{body}; console.log(JSON.stringify({call}))"],
                         capture_output=True, text=True, check=True).stdout
    listed, plain, missing = json.loads(out)
    assert "object" not in listed.lower() and "String should match pattern" in listed
    assert plain == "Sign in first" and missing == "Bad"
