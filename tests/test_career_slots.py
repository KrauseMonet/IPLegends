"""Cloud career slots and watching any match, for pass holders (A306).

The rules are: saving needs a pass and a token this site signed; listing, opening and
deleting need only the account (a lapsed pass never locks anybody out of careers they
kept); one account can never reach another's slots; and a pass holder's season carries
every match's over log while everybody else's carries scorecards only. The SQL is checked
against a REAL Postgres when IPLEGENDS_SCRATCH_DB points at a throwaway, fully migrated
database; everything else needs none.
"""

from __future__ import annotations

import contextlib
import datetime
import os
import pathlib
import random
import re

import pytest
from fastapi.testclient import TestClient

from game import career as C
from tools.snapshot_deck import deck_from, model_from, read_document
from web import accounts, auth
from web import career_routes as R
from web import career_slots as S
from web import premium as P

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOC = read_document()
TODAY = datetime.date(2026, 12, 1)
SETUP = dict(name="Slot Tester", role="opener", debut=2012)


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-only-slot-secret")


@pytest.fixture
def client():
    from web.app import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


def _no_db():
    raise AssertionError("the database was reached")


# --- no database ------------------------------------------------------------------------------

def test_the_slot_count_is_the_migrations_check():
    sql = (ROOT / "migrations" / "059_career_slots.sql").read_text()
    lo, hi = map(int, re.search(r"check \(slot between (\d+) and (\d+)\)", sql).groups())
    assert (lo, hi) == (1, S.SLOTS) and S.SLOTS == 5
    for bad in (0, 6, -1):
        with pytest.raises(S.SlotError):
            S.check_slot(bad)
    assert [S.check_slot(n) for n in (1, 5)] == [1, 5]


def test_a_token_is_stored_compressed_and_comes_back_whole():
    token = "eyJ2IjoxLCJzZWVkIjo" * 400 + ".sig"
    blob = S.pack(token)
    assert S.unpack(blob) == token and len(blob) < len(token) / 3


@pytest.mark.skipif(DOC is None, reason="no deck snapshot")
@pytest.mark.usefixtures("no_incidents")
def test_the_summary_is_read_off_the_career_itself(client):
    d = client.post("/api/career/start", json=SETUP).json()
    seed, setup, cp, _ = R.read_token(d["token"])
    s = S.summary_of(setup, cp, C.ROLES[setup.role]["label"])
    assert (s.name, s.role, s.year, s.seasons, s.finished, s.rating) == (
        "Slot Tester", "Opening batter", 2012, 0, False, None)


def test_the_summary_follows_the_seasons_played():
    from types import SimpleNamespace as NS
    setup = NS(name="Late", debut=2010)
    two = [NS(year=2010, rating=83), NS(year=2011, rating=78)]
    s = S.summary_of(setup, NS(seasons=two, ended=None), "Spinner")
    assert (s.year, s.seasons, s.finished, s.rating) == (2011, 2, False, 83)   # the peak, not the last
    s = S.summary_of(setup, NS(seasons=two, ended="retired"), "Spinner")
    assert s.finished is True
    s = S.summary_of(setup, NS(seasons=[NS(year=2010, rating=0)], ended=None), "Spinner")
    assert s.rating is None                      # 0 is an older token's "no Rating", never shown


def test_every_slot_route_needs_an_account(client, monkeypatch):
    monkeypatch.setattr(R.db, "connection", _no_db)
    assert client.get("/api/career/slots").status_code == 401
    assert client.post("/api/career/slots/1", json={"token": "x.y"}).status_code == 401
    assert client.post("/api/career/slots/1/open").status_code == 401
    assert client.delete("/api/career/slots/1").status_code == 401


@pytest.mark.skipif(DOC is None, reason="no deck snapshot")
@pytest.mark.usefixtures("no_incidents")
def test_a_bad_slot_or_a_forged_token_is_refused_before_the_database(client, monkeypatch):
    monkeypatch.setattr(R.db, "connection", _no_db)
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(3))
    for n in (0, 6):
        assert client.post(f"/api/career/slots/{n}", json={"token": "a.b"}).status_code == 400
        assert client.post(f"/api/career/slots/{n}/open").status_code == 400
        assert client.delete(f"/api/career/slots/{n}").status_code == 400
    # A real career's payload under a signature this site never made: it decodes perfectly,
    # so only the signature check can refuse it.
    client.cookies.clear()
    real = client.post("/api/career/start", json=SETUP).json()["token"]
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(3))
    forged = real.rsplit(".", 1)[0] + ".bm90LXNpZ25lZC1oZXJl"
    assert client.post("/api/career/slots/1", json={"token": forged}).status_code == 400


def test_a_move_that_plays_no_season_never_asks_about_a_pass(monkeypatch):
    monkeypatch.setattr(R.db, "connection", _no_db)

    class Req:
        cookies = {auth.COOKIE_NAME: auth.make_session_cookie(3)}

    class NoSeason:
        detail = None

    assert R._watch_all(Req(), NoSeason()) is False


def _first_season(client, deck, model, cookie=None):
    """Post moves through the API until a response carries a season's matches."""
    if cookie is not None:
        client.cookies.set(auth.COOKIE_NAME, cookie)
    d = client.post("/api/career/start", json={**SETUP, "seed": 11}).json()
    setup = C.Setup(SETUP["name"], SETUP["role"], SETUP["debut"], False)
    moves = []
    for _ in range(12):
        c = C.play_career(deck, model, 11, setup, moves, R._facts())
        m = C.policy_move(c, "random", random.Random(len(moves)))
        d = client.post("/api/career/move", json={"token": d["token"], "move": m}).json()
        moves.append(m)
        if d.get("season_play"):
            return d["season_play"]
    raise AssertionError("no season was played")


@pytest.mark.skipif(DOC is None, reason="no deck snapshot")
@pytest.mark.usefixtures("no_incidents")
def test_a_pass_holders_season_keeps_every_over_log_and_nobody_elses_does(
        client, deck, model, monkeypatch):
    plain = _first_season(client, deck, model)
    assert plain["cards_full"] is False and plain["cards"]
    assert all(not (c["home_innings"] or {}).get("over_log") for c in plain["cards"])
    client.cookies.clear()

    asked = []
    monkeypatch.setattr(R.db, "connection", contextlib.nullcontext)
    monkeypatch.setattr(R.premium_lib, "has_premium",
                        lambda conn, a, today: asked.append(a) or True)
    full = _first_season(client, deck, model, cookie=auth.make_session_cookie(42))
    assert full["cards_full"] is True and 42 in asked
    league = [c for c in full["cards"] if c["stage"] == "league"]
    assert league and all(c["home_innings"]["over_log"] for c in league)
    # the scorecards themselves are the same matches either way
    assert [c["home"] for c in full["cards"]] == [c["home"] for c in plain["cards"]]


def test_the_page_offers_a_watch_only_where_the_season_carries_the_over_log():
    js = (ROOT / "web" / "static" / "career.js").read_text()
    row = re.search(r"function crFeedRow[\s\S]*?\n}", js).group(0)
    assert "crCardsFull()" in row and "l.watch == null" in row
    assert "sp.cards_full" in re.search(r"function crCardsFull[\s\S]*?\n}", js).group(0)
    page = (ROOT / "web" / "static" / "career.html").read_text()
    assert 'id="crSlots"' in page


# --- the SQL, against a real Postgres (opt-in) -------------------------------------------

SCRATCH = os.environ.get("IPLEGENDS_SCRATCH_DB")
needs_pg = pytest.mark.skipif(
    not SCRATCH, reason="set IPLEGENDS_SCRATCH_DB to a throwaway, fully migrated database")


@pytest.fixture
def pg():
    import psycopg
    conn = psycopg.connect(SCRATCH)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def wired(pg, client, monkeypatch):
    @contextlib.contextmanager
    def same_conn():
        yield pg
    monkeypatch.setattr(R.db, "connection", same_conn)
    monkeypatch.setattr(R, "_today", lambda: TODAY)
    return client


def _account(pg, name):
    return accounts.create_account(pg, name, f"{name}@example.com", "password1").account_id


def _give_pass(pg, account):
    boss = _account(pg, f"boss_{account}")
    row = P.order(pg, account, "season", TODAY)
    row = P.claim(pg, account, row.pass_id, f"{account:012d}")
    return P.approve(pg, boss, row.pass_id, TODAY)


@needs_pg
@pytest.mark.skipif(DOC is None, reason="no deck snapshot")
@pytest.mark.usefixtures("no_incidents")
def test_saving_needs_a_pass_and_then_lists_opens_replaces_and_deletes(pg, wired):
    client = wired
    a = _account(pg, "slot_owner")
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(a))
    token = client.post("/api/career/start", json=SETUP).json()["token"]

    assert client.get("/api/career/slots").json() == {"premium": False, "max": 5, "slots": []}
    r = client.post("/api/career/slots/2", json={"token": token})
    assert r.status_code == 403 and "pass" in r.json()["detail"]
    (n,), = pg.execute("select count(*) from career_slots where account_id = %s", (a,))
    assert n == 0

    _give_pass(pg, a)
    saved = client.post("/api/career/slots/2", json={"token": token}).json()
    assert (saved["slot"], saved["name"], saved["role"], saved["seasons"]) == (
        2, "Slot Tester", "Opening batter", 0)
    listed = client.get("/api/career/slots").json()
    assert listed["premium"] is True and [s["slot"] for s in listed["slots"]] == [2]

    opened = client.post("/api/career/slots/2/open").json()
    assert opened["name"] == "Slot Tester"
    assert R.read_token(opened["token"])[0] == R.read_token(token)[0]     # the same seed

    other = client.post("/api/career/start", json={**SETUP, "name": "Second One"}).json()["token"]
    client.post("/api/career/slots/2", json={"token": other})
    assert [s["name"] for s in client.get("/api/career/slots").json()["slots"]] == ["Second One"]

    assert client.delete("/api/career/slots/2").json() == {"deleted": 2}
    assert client.delete("/api/career/slots/2").status_code == 404
    assert client.post("/api/career/slots/2/open").status_code == 404


@needs_pg
@pytest.mark.skipif(DOC is None, reason="no deck snapshot")
@pytest.mark.usefixtures("no_incidents")
def test_a_lapsed_pass_can_still_open_and_delete_but_not_save(pg, wired):
    client = wired
    a = _account(pg, "slot_lapsed")
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(a))
    token = client.post("/api/career/start", json=SETUP).json()["token"]
    held = _give_pass(pg, a)
    assert client.post("/api/career/slots/1", json={"token": token}).status_code == 200
    P.revoke(pg, held.account_id, held.pass_id)
    assert client.get("/api/career/slots").json()["premium"] is False
    assert client.post("/api/career/slots/3", json={"token": token}).status_code == 403
    assert client.post("/api/career/slots/1/open").status_code == 200
    assert client.delete("/api/career/slots/1").status_code == 200


@needs_pg
@pytest.mark.skipif(DOC is None, reason="no deck snapshot")
@pytest.mark.usefixtures("no_incidents")
def test_one_account_never_reaches_anothers_slots(pg, wired):
    client = wired
    owner, stranger = _account(pg, "slot_mine"), _account(pg, "slot_theirs")
    _give_pass(pg, owner)
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(owner))
    token = client.post("/api/career/start", json=SETUP).json()["token"]
    client.post("/api/career/slots/1", json={"token": token})
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(stranger))
    assert client.get("/api/career/slots").json()["slots"] == []
    assert client.post("/api/career/slots/1/open").status_code == 404
    assert client.delete("/api/career/slots/1").status_code == 404
    (n,), = pg.execute("select count(*) from career_slots where account_id = %s", (owner,))
    assert n == 1


@needs_pg
def test_slots_go_when_the_account_goes(pg):
    a = _account(pg, "slot_gone")
    S.save(pg, a, 1, "tok.sig", S.Summary("X", "Spinner", 2012, 0, False, None))
    pg.execute("delete from accounts where account_id = %s", (a,))
    (n,), = pg.execute("select count(*) from career_slots where account_id = %s", (a,))
    assert n == 0
