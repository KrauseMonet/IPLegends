"""Pass boosts in Build a Career [A310]: a second ceiling spin, one release survived, an extra
focus step -- one switch, set by the server only for a pass holder, carried in the signed
token, and marked on the legend so a room can refuse it."""

from __future__ import annotations

import dataclasses
import random

import pytest
from fastapi.testclient import TestClient

from game import career as C
from game import career_focus as F
from tests.test_rooms import FakeConn
from tools.snapshot_deck import deck_from, model_from, read_document
from web import career_results, career_routes as R, rooms

DOC = read_document()
pytestmark = [pytest.mark.skipif(DOC is None, reason="no deck snapshot"),
              pytest.mark.usefixtures("no_incidents")]


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


# --- the second spin ----------------------------------------------------------------------

@pytest.mark.parametrize("role", ["opener", "middle", "pace", "spin", "allrounder", "keeper"])
def test_a_boosted_spin_keeps_the_better_of_two_ceilings_and_the_first_is_the_plain_one(
        deck, role):
    better = 0
    for seed in range(1, 41):
        start, plain = C.spin(deck, seed, role)
        s2, kept, other = C.spins(deck, seed, role, True)
        assert s2 == start
        assert plain in (kept, other)                       # the plain ceiling is one of the two
        assert C.main_rating(kept, role) >= C.main_rating(other, role)
        assert C.main_rating(kept, role) > C.main_rating(start, role) or kept == plain
        better += C.main_rating(kept, role) > C.main_rating(plain, role)
    assert better >= 5, "the second spin must sometimes win, or the boost does nothing"


def test_an_unboosted_spin_draws_no_second_ceiling(deck):
    assert C.spins(deck, 7, "opener", False)[2] is None


# --- one release survived -----------------------------------------------------------------
#
# Careless careers are mostly released after their tenth season (A294), too late for a quick
# fixture, so the club's decision is faked: it lets the player go after EVERY season. The
# boost turns the first of those into a warning season and only the first.

def _always_released(monkeypatch):
    monkeypatch.setattr(C, "release_decision",
                        lambda released, rating, prime, standing: (True, None))


def _play(deck, model, setup, seed=5):
    car, _ = C.auto_career(deck, model, seed, setup, "loyal")
    return car


def _warned(r) -> bool:
    return any("one more season to prove yourself" in n for n in r.notes)


def test_a_boosted_career_survives_its_first_release_once(deck, model, monkeypatch):
    _always_released(monkeypatch)
    plain = _play(deck, model, C.Setup("T", "middle", 2010))
    boosted = _play(deck, model, C.Setup("T", "middle", 2010, boosted=True))
    assert len(plain.seasons) == 1 and plain.released
    assert len(boosted.seasons) == 2 and boosted.released
    assert _warned(boosted.seasons[0]) and not _warned(boosted.seasons[1])
    assert boosted.checkpoint.reprieved is True


def test_an_unboosted_career_is_never_reprieved(deck, model, monkeypatch):
    _always_released(monkeypatch)
    plain = _play(deck, model, C.Setup("T", "pace", 2012))
    assert not any(_warned(r) for r in plain.seasons)
    assert plain.checkpoint.reprieved is False


def _same_ceiling(monkeypatch):
    """Both careers draw the plain spins, so the boost's other effects can be compared."""
    real = C.spins
    monkeypatch.setattr(C, "spins", lambda deck, seed, role, boosted: real(deck, seed, role, False))


def test_the_reprieve_survives_a_checkpoint(deck, model):
    cp = C.Checkpoint(k=2, permanent=0.0, current=None, last_display=None, squad=True,
                      seasons=[], reprieved=True)
    assert C.Checkpoint.from_dict(cp.to_dict()).reprieved is True
    old = cp.to_dict()
    del old["reprieved"]
    assert C.Checkpoint.from_dict(old).reprieved is False


# --- an extra focus step ------------------------------------------------------------------

@pytest.fixture
def focus_on():
    was, F.FOCUS_ON = F.FOCUS_ON, True
    yield
    F.FOCUS_ON = was


def _focus_career(deck, model, seed, setup, focus):
    moves, rng = [], random.Random(seed)
    while True:
        car = C.play_career(deck, model, seed, setup, moves)
        if car.complete or len(car.seasons) >= 6:
            return car
        p = car.pending
        if p.kind == "focus":
            opts = p.focus["options"]
            moves.append(f"f{opts.index(focus)}" if focus in opts else "f0")
        else:
            moves.append(C.policy_move(car, "strength", rng))


@pytest.mark.parametrize("power", [1.0, 2.0])
def test_only_a_met_focus_gets_the_extra_step(power):
    met = F.Result("spin", 3, 2, True, F.step(True, False) * power, False)
    missed = F.Result("spin", 1, 2, False, F.step(False, False) * power, False)
    assert C.boosted_focus(met, power, True).built == met.built + C.BOOST_FOCUS_STEPS * power
    assert C.boosted_focus(missed, power, True).built == missed.built
    assert C.boosted_focus(met, power, False).built == met.built
    assert C.BOOST_FOCUS_STEPS == 1.0


def test_a_boosted_career_builds_the_extra_step_into_its_season(deck, model, monkeypatch,
                                                                 focus_on):
    """Wiring: the first met season of a boosted career records one more step than the same
    season unboosted (nothing differs before it)."""
    _same_ceiling(monkeypatch)
    setup = C.Setup("T", "middle", 2012)
    plain = _focus_career(deck, model, 3, setup, "spin")
    boosted = _focus_career(deck, model, 3, dataclasses.replace(setup, boosted=True), "spin")
    a, b = next((a.focus, b.focus) for a, b in zip(plain.seasons, boosted.seasons)
                if a.focus and a.focus["done"])
    assert b["built"] == pytest.approx(a["built"] + F.FOCUS_BY_KEY["spin"].power)


# --- the server sets it, the token carries it, the legend is marked -------------------------

@pytest.fixture(scope="module")
def _app_client():
    from web.app import app
    with TestClient(app) as c:                 # the context runs startup: the deck loads
        yield c


@pytest.fixture
def client(_app_client, monkeypatch):
    monkeypatch.setattr(R, "_dynasty_for_start", lambda request: ("", 0, ()))
    return _app_client


def _start(client, **extra):
    return client.post("/api/career/start", json={"name": "Kiran Bose", "role": "opener",
                                                  "debut": 2010, **extra})


def test_without_a_pass_the_boost_is_refused(client, monkeypatch):
    monkeypatch.setattr(R, "_has_pass", lambda request: False)
    r = _start(client, boosted=True)
    assert r.status_code == 403
    assert _start(client).status_code == 200


def test_a_pass_holders_boosted_career_carries_the_boost_in_its_token(client, monkeypatch):
    monkeypatch.setattr(R, "_has_pass", lambda request: True)
    d = _start(client, boosted=True).json()
    assert d["boosted"] is True and d["other_ceiling"] is not None
    _seed, setup, _cp, _ = R.read_token(d["token"])
    assert setup.boosted is True
    plain = _start(client).json()
    assert plain["boosted"] is False and plain["other_ceiling"] is None


def test_a_token_from_before_the_boost_reads_unboosted():
    import base64
    import json
    body = {"v": R.TOKEN_VERSION, "seed": 5,
            "setup": ["Kiran", "opener", 2010, False, None, "", "", [], "", "R", "R", "",
                      "", 0, []],
            "cp": C.Checkpoint(k=0, permanent=0.0, current=None, last_display=None,
                               squad=False, seasons=[]).to_dict(), "partial": []}
    payload = base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")
    assert R.decode_payload(payload)[1].boosted is False


def test_a_boosted_careers_legend_is_marked_and_a_room_refuses_it(deck, model):
    setup = C.Setup("T", "opener", 2010, boosted=True)
    car, _ = C.auto_career(deck, model, 4, setup, "strength")
    leg = career_results.legend(car)
    assert leg is not None and leg["boosted"] is True
    assert career_results.card(car)["boosted"] is True
    plain_leg = career_results.legend(dataclasses.replace(car, setup=dataclasses.replace(
        setup, boosted=False)))
    assert "boosted" not in plain_leg

    # [A312] with the room's pass perks off a boosted legend is refused; on (the default),
    # it comes in, marked
    conn = FakeConn()
    room, host = rooms.create_room(conn, "league", 15, "Host", game="auction", legends=True)
    rooms.set_perks(conn, room.code, host, False)
    with pytest.raises(rooms.RoomError, match="boosts"):
        rooms.set_legend(conn, room.code, host, {**leg, "skilled": True})
    rooms.set_legend(conn, room.code, host, {**plain_leg, "skilled": True})
    rooms.set_perks(conn, room.code, host, True)
    rooms.set_legend(conn, room.code, host, {**leg, "skilled": True})
