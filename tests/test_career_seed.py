"""[A235] Choosing a career's seed is for admins: anybody else's request carrying one is
refused, and without one everybody still gets a career."""

import pytest
from fastapi.testclient import TestClient

from tools.snapshot_deck import read_document
from web import auth
from web import career_routes as R

REAL_GATE = R.seed_allowed          # captured before conftest opens the gate for the suite
pytestmark = pytest.mark.skipif(read_document() is None, reason="no deck snapshot")
BODY = {"name": "Seedy", "role": "opener", "debut": 2012}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-only-seed-secret")
    monkeypatch.setenv("ADMIN_ACCOUNT_IDS", "7")
    monkeypatch.setattr(R, "seed_allowed", REAL_GATE)
    from web.app import app
    with TestClient(app) as c:
        yield c


def test_a_signed_out_player_cannot_choose_a_seed(client):
    assert client.post("/api/career/start", json={**BODY, "seed": 54408}).status_code == 403


def test_a_player_who_is_not_an_admin_cannot_choose_a_seed(client):
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(8))
    assert client.post("/api/career/start", json={**BODY, "seed": 54408}).status_code == 403


def test_without_a_seed_anybody_still_starts_a_career(client):
    assert client.post("/api/career/start", json=BODY).status_code == 200


def test_an_admin_chooses_the_seed_and_gets_its_spins(client):
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(7))
    a = client.post("/api/career/start", json={**BODY, "seed": 54408}).json()
    b = client.post("/api/career/start", json={**BODY, "seed": 54408}).json()
    assert a["ceiling"] == b["ceiling"] and a["start"] == b["start"]
    assert a["ceiling"]["name"] == "V Suryavanshi" and a["ceiling"]["year"] == 2026
