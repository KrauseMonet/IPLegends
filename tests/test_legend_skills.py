"""[A284] Only a career built WITH skills can bring a legend into a room. Older careers keep
their data (the Hall of Fame still lists them) and simply cannot be brought."""

from __future__ import annotations

import contextlib
import datetime
import os

import pytest

from game import career as C
from tests.test_room_auction import Clock, load
from tests.test_room_legends import _legend_room
from tests.test_rooms import FakeConn
from tools.snapshot_deck import _card_doc, deck_from, model_from, read_document
from web import accounts, auth, career_results as CR, career_routes as R
from web import room_auction as ra
from web import rooms

DOC = read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="no deck snapshot")
SCRATCH = os.environ.get("IPLEGENDS_SCRATCH_DB")
needs_pg = pytest.mark.skipif(
    not SCRATCH, reason="set IPLEGENDS_SCRATCH_DB to a throwaway, fully migrated database")


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-only-career-secret")


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def career(deck):
    car, _ = C.auto_career(deck, model_from(DOC), 5, C.Setup("Legend Kid", "opener", 2015, False),
                           "strength")
    return car


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(ra.time, "time", c)
    monkeypatch.setattr(rooms.time, "time", c)
    return c


def test_a_card_is_built_with_skills_only_when_it_lists_one():
    assert CR.has_skills({"skills": [["anchor", 1, 3]]})
    for card in ({"skills": []}, {"skills": None}, {}, None):
        assert not CR.has_skills(card)


def test_the_saved_card_of_a_career_lists_the_skills_it_earned(career):
    assert CR.has_skills(CR.card(career)) == bool(C.career_skills.signature(career.seasons))


def _row(key, skills):
    # a stored legend is a card document: a real one, since the route values it
    legend = CR.legend(_CAREER[0])
    card = {"legend": legend, "extra_legends": [legend]}
    if skills is not None:
        card["skills"] = skills
    return {"key": key, "name": "T", "role": "opener", "debut": 2015, "overseas": False,
            "seasons": 10, "matches": 140, "runs": 100, "wickets": 0, "ending": "archive",
            "titles": 0, "peak_card": 90, "peak_year": 2020, "card": card,
            "completed_at": datetime.datetime(2026, 11, 8)}


_CAREER: list = []


def test_the_hall_keeps_every_career_but_offers_only_skilled_legends(career, monkeypatch):
    from fastapi.testclient import TestClient
    from web.app import app
    _CAREER[:] = [career]
    rows = [_row("a" * 64, [["anchor", 1, 4]]), _row("b" * 64, []), _row("c" * 64, None)]
    monkeypatch.setattr(R.db, "connection", contextlib.nullcontext)
    monkeypatch.setattr(R.career_results, "hall", lambda conn, account_id: rows)
    with TestClient(app) as client:
        client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(1))
        got = {h["key"]: h for h in client.get("/api/career/hall").json()}
    assert set(got) == {"a" * 64, "b" * 64, "c" * 64}                 # nothing is deleted
    assert got["a" * 64]["legend"] and got["a" * 64]["legend_value"] is not None
    assert len(got["a" * 64]["extras"]) == 1
    for k in ("b" * 64, "c" * 64):                                    # kept, not offered
        h = got[k]
        assert not h["legend"] and h["legend_value"] is None and h["extras"] == []
        assert h["card"]["legend"]["year"]                            # the data is still there


# --- the database: the room route reads the signed-in account's own career ------------------


@pytest.fixture
def pg():
    import psycopg
    conn = psycopg.connect(SCRATCH)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _save(pg, career, name, skills):
    me = accounts.create_account(pg, name, f"{name}@example.com", "password1").account_id
    key = CR.natural_key(R.make_token(1, C.Setup("T", "opener", 2015, False), career, []))
    CR.save(pg, me, key, career)
    from psycopg.types.json import Jsonb
    pg.execute("update career_results set card = jsonb_set(card, '{skills}', %s)"
               " where account_id = %s", (Jsonb(skills), me))
    return me, key


@needs_pg
def test_a_career_with_no_skills_cannot_bring_a_legend_but_stays_saved(pg, career):
    me, key = _save(pg, career, "noskills", [])
    assert CR.legend_of(pg, me, key) is None
    assert CR.legend_of(pg, me, CR.extra_key(key, career.seasons[0].year)) is None
    assert CR.count(pg, me) == 1 and CR.hall(pg, me)[0]["card"]["legend"]      # data kept


@needs_pg
def test_a_career_with_skills_brings_its_legend(pg, career):
    me, key = _save(pg, career, "withskills", [["anchor", 1, 3]])
    assert CR.legend_of(pg, me, key) == CR.legend(career)


@needs_pg
def test_a_career_saved_before_skills_existed_has_no_skills_key(pg, career):
    me, key = _save(pg, career, "oldcareer", [])
    pg.execute("update career_results set card = card - 'skills' where account_id = %s", (me,))
    assert CR.legend_of(pg, me, key) is None


# --- the room ------------------------------------------------------------------------------


def test_a_room_refuses_a_legend_that_did_not_pass_the_skills_check(deck, career):
    conn = FakeConn()
    room, host = rooms.create_room(conn, "league", 30, "Asha", game="auction", legends=True)
    with pytest.raises(rooms.RoomError, match="skills"):
        rooms.set_legend(conn, room.code, host, CR.legend(career))        # no `skilled` mark
    rooms.set_legend(conn, room.code, host, {**CR.legend(career), "skilled": True})
    assert host in load(conn, room.code).legends


def test_a_legend_chosen_before_the_rule_is_dropped_at_the_start(deck, clock, career):
    """A lobby that already held an older legend when this shipped starts without it."""
    conn = FakeConn()
    legend = {**CR.legend(career), "skilled": True}
    code, host, guest = _legend_room(conn, deck, legend, start=False)
    room = load(conn, code)
    room.legends = {host: CR.legend(career),                            # unmarked: older
                    guest: {**CR.legend(career), "name": "New Kid", "skilled": True}}
    rooms._save_room(conn, room)
    rooms.start_room(conn, code, host, deck)
    r = ra.replay(load(conn, code), deck)
    assert r.auction.teams[r.team_of[host]].legend is None
    assert r.auction.teams[r.team_of[guest]].legend is not None


def test_the_lobby_does_not_show_an_older_legend(deck, career):
    from web.app import _room_legends_out
    conn = FakeConn()
    code, host, guest = _legend_room(conn, deck, {**CR.legend(career), "skilled": True},
                                     start=False)
    room = load(conn, code)
    room.legends[guest] = CR.legend(career)                             # unmarked, older
    assert set(_room_legends_out(room, host)) == {host}


def _post_legend(monkeypatch, legend_of_result, captured):
    from fastapi.testclient import TestClient
    import web.app as A
    monkeypatch.setattr(A, "_db", contextlib.nullcontext)
    monkeypatch.setattr(A.career_results, "legend_of", lambda conn, account_id, key: legend_of_result)

    def set_legend(conn, code, player_id, legend):
        captured.append(legend)
        raise rooms.RoomError("stop here")                    # the room is not the subject
    monkeypatch.setattr(A.rooms, "set_legend", set_legend)
    client = TestClient(A.app)
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(1))
    return client.post("/api/rooms/ABCDEF/legend", json={"player_id": "p", "career": "a" * 64})


def test_the_route_stamps_a_legend_that_passed_and_refuses_one_that_did_not(career, monkeypatch):
    seen: list = []
    refused = _post_legend(monkeypatch, None, seen)
    assert refused.status_code == 400 and "skills" in refused.json()["detail"] and seen == []
    _post_legend(monkeypatch, CR.legend(career), seen)
    assert seen[0]["skilled"] is True and seen[0]["career"] == "a" * 64
