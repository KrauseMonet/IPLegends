"""Saved careers and the Hall of Fame (A210, migration 049).

The SQL tests are opt-in, as in tests/test_admin.py: point IPLEGENDS_SCRATCH_DB at a THROWAWAY
database with every migration applied. Each runs in a transaction that is rolled back.
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import os

import pytest
from fastapi.testclient import TestClient

from etl.franchise_map import canonical
from game import career as C
from tools.snapshot_deck import deck_from, model_from, read_document
from web import accounts, career_results as CR, career_routes as R

DOC = read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="no deck snapshot")
SCRATCH = os.environ.get("IPLEGENDS_SCRATCH_DB")
needs_pg = pytest.mark.skipif(
    not SCRATCH, reason="set IPLEGENDS_SCRATCH_DB to a throwaway, fully migrated database")


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-only-career-secret")


@pytest.fixture(scope="module")
def finished():
    """Two finished careers, a batter's and a bowler's, with their signed tokens."""
    deck, model = deck_from(DOC), model_from(DOC)
    out = {}
    for role, seed in (("opener", 7), ("pace", 1)):
        setup = C.Setup(f"T {role}", role, 2016, False)
        career, _ = C.auto_career(deck, model, seed, setup, "strength")
        out[role] = (seed, setup, career)
    return out


def _token(f):
    seed, setup, career = f
    return R.make_token(seed, setup, career, [])


def test_the_natural_key_is_the_payload_not_the_signature(finished):
    token = _token(finished["opener"])
    payload = token.rsplit(".", 1)[0]
    assert CR.natural_key(token) == hashlib.sha256(payload.encode()).hexdigest()
    assert CR.natural_key(payload + ".another-signature") == CR.natural_key(token)
    assert CR.natural_key(_token(finished["pace"])) != CR.natural_key(token)


def test_the_card_carries_the_milestones_and_the_timeline(finished):
    _, _, career = finished["opener"]
    c = CR.card(career)
    assert c["milestones"] == [[y, t] for y, t in career.summary.milestones]
    assert [t["year"] for t in c["timeline"]] == [r.year for r in career.seasons]
    assert c["timeline"][-1]["card"] == career.seasons[-1].display


def test_legacy_puts_a_wicket_at_twenty_runs():
    assert CR.legacy(1500, 0) < CR.legacy(0, 100) == 2000


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


def _account(pg, name):
    return accounts.create_account(pg, name, f"{name}@example.com", "password1").account_id


@needs_pg
def test_saving_twice_keeps_one_row(pg, finished):
    me = _account(pg, "careersaver")
    token = _token(finished["opener"])
    _, _, career = finished["opener"]
    assert CR.save(pg, me, CR.natural_key(token), career) is True
    assert CR.save(pg, me, CR.natural_key(token), career) is False
    assert CR.count(pg, me) == 1
    other = _account(pg, "othersaver")
    assert CR.save(pg, other, CR.natural_key(token), career) is True      # per account


@needs_pg
def test_the_hall_of_fame_is_ordered_by_legacy_and_reads_back(pg, finished):
    me = _account(pg, "hallorder")
    for role in ("opener", "pace"):
        CR.save(pg, me, CR.natural_key(_token(finished[role])), finished[role][2])
    rows = CR.hall(pg, me)
    want = sorted((finished[r][2] for r in ("opener", "pace")),
                  key=lambda c: -CR.legacy(c.summary.runs, c.summary.wickets))
    assert [r["runs"] for r in rows] == [c.summary.runs for c in want]
    top = rows[0]
    assert top["card"]["milestones"] == [[y, t] for y, t in want[0].summary.milestones]
    # [A214] the peak column holds the career's best RATING (its best season grade rides
    # in the card), since the Rating is the career's own number
    assert top["peak_card"] == want[0].summary.peak_rating
    assert top["card"]["peak_rating"] == want[0].summary.peak_rating


@needs_pg
def test_a_legend_is_read_only_from_its_own_accounts_careers(pg, finished):
    """[A215] A room asks for a career by key, and the server reads it for the SIGNED-IN
    account: another account's key gives nothing, so nobody brings a legend they did not earn."""
    me, other = _account(pg, "legendowner"), _account(pg, "legendthief")
    token = _token(finished["opener"])
    CR.save(pg, me, CR.natural_key(token), finished["opener"][2])
    legend = CR.legend_of(pg, me, CR.natural_key(token))
    assert legend == CR.legend(finished["opener"][2])
    assert legend["rating"] == finished["opener"][2].summary.peak_rating
    assert CR.legend_of(pg, other, CR.natural_key(token)) is None
    assert CR.legend_of(pg, me, "0" * 64) is None


@needs_pg
def test_deleting_an_account_deletes_its_careers(pg, finished):
    me = _account(pg, "careergone")
    CR.save(pg, me, CR.natural_key(_token(finished["pace"])), finished["pace"][2])
    pg.execute("delete from accounts where account_id = %s", (me,))
    (n,), = pg.execute("select count(*) from career_results where account_id = %s",
                       (me,)).fetchall()
    assert n == 0


# --- the routes, with no database ----------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    from web.app import app
    with TestClient(app) as c:
        yield c


def _sign_in(client, account_id):
    from web import auth
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(account_id))


def test_saving_needs_a_signed_in_player_and_a_finished_career(client, finished):
    client.cookies.clear()
    assert client.post("/api/career/save", json={"token": _token(finished["opener"])}).status_code == 401
    _sign_in(client, 1)
    going = client.post("/api/career/start", json={"name": "Going", "role": "opener", "debut": 2012,
                                                   "seed": 3}).json()
    r = client.post("/api/career/save", json={"token": going["token"]})
    assert r.status_code == 409
    forged = going["token"][:-4] + "abcd"
    assert client.post("/api/career/save", json={"token": forged}).status_code == 400
    client.cookies.clear()


def test_the_hall_route_places_careers_on_the_real_lists(client, finished, monkeypatch):
    """Ranks are derived when read, from the puzzle facts; crests follow the clubs in order."""
    _, _, career = finished["opener"]
    row = {"key": "k", "name": "T", "role": "opener", "debut": 2016, "overseas": False,
           "seasons": career.summary.seasons, "matches": career.summary.matches,
           "runs": career.summary.runs, "wickets": 0, "titles": 0, "peak_card": 90,
           "peak_year": 2020, "ending": "archive", "card": CR.card(career),
           "completed_at": datetime.datetime(2026, 10, 22)}
    monkeypatch.setattr(R.db, "connection", contextlib.nullcontext)
    monkeypatch.setattr(R.career_results, "hall", lambda conn, account_id: [row])
    _sign_in(client, 1)
    got = client.get("/api/career/hall").json()
    client.cookies.clear()
    real = [p.runs for p in R._facts()]
    assert got[0]["runs_rank"] == 1 + sum(x > career.summary.runs for x in real)
    # one crest a club: Delhi Daredevils and Delhi Capitals are one franchise
    clubs = {canonical(r.franchise) for r in career.seasons}
    assert len(got[0]["crests"]) == len(clubs)
