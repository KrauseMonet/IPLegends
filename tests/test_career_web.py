"""Build a Career's API (A199): the signed checkpoint, resuming from it, and the routes.

The checkpoint replaces replay-from-scratch, so the one property everything rests on is that
resuming from a checkpoint gives exactly the career a full replay gives -- checked here on
real seasons, through a JSON round trip, as the token carries it.
"""

from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from game import career as C
from tools.snapshot_deck import deck_from, model_from, read_document
from web import career_routes as R

DOC = read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="no deck snapshot")


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-only-career-secret")


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


@pytest.fixture(scope="module")
def client():
    from web.app import app
    with TestClient(app) as c:
        yield c


def _auto_moves(deck, model, seed, setup, seasons):
    """An automatic player's moves through `seasons` completed seasons."""
    moves = []
    while True:
        c = C.play_career(deck, model, seed, setup, moves)
        if c.complete or len(c.seasons) >= seasons and c.pending.kind == "offer":
            return moves, c
        moves.append(C.policy_move(c, "random", __import__("random").Random(len(moves))))


def test_resuming_from_a_checkpoint_is_the_same_career_as_a_replay(deck, model):
    setup = C.Setup("Tester", "spin", 2012)
    moves, full = _auto_moves(deck, model, 41, setup, 4)
    for stop in range(1, len(full.seasons)):
        # the moves that produced the first `stop` seasons, and the checkpoint after them
        prefix = []
        while len(C.play_career(deck, model, 41, setup, prefix).seasons) < stop or \
                C.play_career(deck, model, 41, setup, prefix).pending.kind != "offer":
            prefix.append(moves[len(prefix)])
        at = C.play_career(deck, model, 41, setup, prefix)
        cp = C.Checkpoint.from_dict(json.loads(json.dumps(at.checkpoint.to_dict())))
        resumed = C.play_career(deck, model, 41, setup, moves[len(prefix):], resume=cp)
        assert [vars(s) for s in resumed.seasons] == [vars(s) for s in full.seasons]
        assert (resumed.pending is None) == (full.pending is None)
        if full.pending is not None:
            assert resumed.pending.offers == full.pending.offers
        assert vars(resumed.checkpoint) == vars(full.checkpoint)


def _token(client, **kw):
    body = {"name": "Tester", "role": "opener", "debut": 2014, "seed": 77, **kw}
    r = client.post("/api/career/start", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_new_career_is_asked_for_a_franchise_and_names_its_two_spins(client):
    d = _token(client)
    assert d["pending"]["kind"] == "offer" and d["pending"]["year"] == 2014
    assert len(d["pending"]["offers"]) == C.OFFERS and not d["pending"]["can_retire"]
    assert d["start"]["name"] and d["ceiling"]["name"] and d["seasons"] == []


def test_a_season_is_played_one_move_at_a_time(client):
    """The seed is one whose first event asks a question, so the offer has to be carried
    beside the checkpoint until the answer arrives."""
    seed = next(s for s in range(1, 500) if len(C.draw_event(s, 0).options) > 1)
    d = _token(client, seed=seed)
    asked = False
    seasons = 0
    while seasons < 2:
        p = d["pending"]
        asked |= p["kind"] == "event"
        move = "o0" if p["kind"] == "offer" else "e0"
        r = client.post("/api/career/move", json={"token": d["token"], "move": move})
        assert r.status_code == 200, r.text
        d = r.json()
        if d["seasons"] and len(d["seasons"]) > seasons:
            seasons = len(d["seasons"])
            s = d["seasons"][-1]
            assert 70 <= s["card"] <= 99 and s["teams"] in (8, 9, 10)
    assert asked and d["pending"]["can_retire"] and d["pending"]["season_no"] == 3


def test_a_career_ends_with_a_summary_and_a_line_with_no_player_in_it(client):
    d = _token(client)
    while not d["seasons"]:
        p = d["pending"]
        move = "o0" if p["kind"] == "offer" else "e0"
        d = client.post("/api/career/move", json={"token": d["token"], "move": move}).json()
    if d["pending"]:
        d = client.post("/api/career/move", json={"token": d["token"], "move": "r"}).json()
    assert d["summary"]["seasons"] == 1 and d["pending"] is None
    assert d["summary"]["ending"] in ("retired", "released")
    assert d["start"]["name"] not in d["share"] and d["ceiling"]["name"] not in d["share"]
    assert "Tester" not in d["share"]
    after = client.post("/api/career/move", json={"token": d["token"], "move": "o0"})
    assert after.status_code == 409


def test_a_tampered_career_is_refused(client):
    d = _token(client)
    payload, sig = d["token"].rsplit(".", 1)
    body = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    body["cp"]["k"] = 5                                    # skip ahead five seasons
    forged = base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")
    for token in (f"{forged}.{sig}", f"{payload}.{'0' * len(sig)}", "nonsense"):
        r = client.post("/api/career/move", json={"token": token, "move": "o0"})
        assert r.status_code == 400


def test_a_career_signature_is_never_a_session_signature():
    """Domain separation, checked directly: the shapes of a cookie and a token differ too,
    so a format check would refuse a swap even without it -- this is what makes the swap
    impossible rather than merely unlikely."""
    from web import auth
    for payload in ("1.1700000000", "e30", "anything"):
        assert R._sign(payload) != auth._sign(payload)


def test_a_session_cookie_is_not_a_career_and_a_career_is_not_a_session(client):
    from web import auth
    cookie = auth.make_session_cookie(1)
    r = client.post("/api/career/move", json={"token": cookie, "move": "o0"})
    assert r.status_code == 400
    assert auth.verify_session_cookie(_token(client)["token"]) is None


@pytest.mark.parametrize("body", [
    {"name": "<b>x</b>"}, {"name": "   "}, {"role": "umpire"}, {"debut": 2007}, {"debut": 2018},
])
def test_a_career_that_cannot_exist_is_refused(client, body):
    r = client.post("/api/career/start",
                    json={"name": "Tester", "role": "opener", "debut": 2014, **body})
    assert r.status_code in (400, 422)


def test_a_move_that_does_not_fit_is_a_conflict_not_a_crash(client):
    d = _token(client)
    for move in ("r", "o9", "e0", "zz"):
        r = client.post("/api/career/move", json={"token": d["token"], "move": move})
        assert r.status_code == 409, move


def test_every_event_options_hint_says_what_its_numbers_do():
    for e in C.EVENTS:
        for o in e.options:
            h = R.option_hint(o).lower()
            assert ("lift this season" in h) == (o.season > 0)
            assert ("dip this season" in h) == (o.season < 0)
            assert ("better from here on" in h) == (o.permanent > 0)
            assert ("lasting cost" in h) == (o.permanent < 0)
            assert ("captain" in h) == o.captain


def test_a_suspended_club_is_named_when_there_is_no_offer_to_stay(client, deck):
    """Chennai have no team in 2016: a 2015 Chennai career is told why it cannot stay. The
    seed is chosen so Chennai do offer a 2015 contract, so the test always has a case."""
    seed = next(s for s in range(1, 500)
                if any("Chennai" in o.franchise for o in C.offers_for(deck, s, 0, 2015, None, None)))
    d = _token(client, debut=2015, seed=seed)
    while not d["seasons"]:
        p = d["pending"]
        move = (f"o{next(n for n, o in enumerate(p['offers']) if 'Chennai' in o['franchise'])}"
                if p["kind"] == "offer" else "e0")
        d = client.post("/api/career/move", json={"token": d["token"], "move": move}).json()
    assert d["seasons"][0]["franchise"] == "Chennai Super Kings"
    assert d["pending"]["club_gone"] == "Chennai Super Kings"
    assert not any(o["stay"] for o in d["pending"]["offers"])
    # and a club that does play the next year is not named
    later = _token(client, debut=2017, seed=seed)
    assert later["pending"]["club_gone"] is None


def test_hints_name_missed_matches_and_an_ending():
    for e in C.EVENTS:
        for o in e.options:
            h = R.option_hint(o).lower()
            assert (f"miss {o.missed} matches" in h) == (o.missed > 0)
            assert ("career ends" in h) == o.retire


def test_a_three_year_deal_through_the_api(client, deck):
    seed = next(s for s in range(1, 2000) if all(len(C.draw_event(s, k).options) == 1 for k in range(2)))
    d = _token(client, seed=seed, debut=2017)
    clubs_2018 = {C.canonical(deck.cards_by_fs[fs][0].franchise) for fs in C.year_fs_ids(deck, 2018)}
    i = next(n for n, o in enumerate(d["pending"]["offers"]) if C.canonical(o["franchise"]) in clubs_2018)
    assert d["pending"]["offers"][i]["price3"]
    d = client.post("/api/career/move", json={"token": d["token"], "move": f"c{i}"}).json()
    assert d["seasons"][0]["contract_year"] == 1
    o = d["pending"]["offers"]
    assert len(o) == 1 and o[0]["contract_year"] == 2 and o[0]["price3"] is None
    bad = client.post("/api/career/move", json={"token": d["token"], "move": "c0"})
    assert bad.status_code == 409


def test_an_event_card_reaches_the_page_with_its_real_name(client, deck):
    seed = next(s for s in range(1, 5000) if C.draw_event(s, 0).key == "mentor")
    d = _token(client, seed=seed)
    d = client.post("/api/career/move", json={"token": d["token"], "move": "o0"}).json()
    e = d["pending"]["event"]
    assert e["key"] == "mentor" and "{name}" not in e["text"] and "senior player" not in e["text"]
    names = {c.name for fs in C.year_fs_ids(deck, 2014) for c in deck.cards_by_fs[fs]}
    assert any(n in e["text"] for n in names)
