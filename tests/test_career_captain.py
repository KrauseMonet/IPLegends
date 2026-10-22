"""Captaincy that persists and the captain's bowling plan in Build a Career (A213)."""

from __future__ import annotations

import copy
import dataclasses
import random

import pytest
from fastapi.testclient import TestClient

from etl.franchise_map import canonical
from game import career as C
from tools.snapshot_deck import deck_from, model_from, read_document

DOC = read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="no deck snapshot")


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


@pytest.fixture(scope="module")
def captain_cp(deck, model):
    """A pace bowler's checkpoint after two seasons, handed the armband of his club -- as if
    he had taken it -- so the next season starts with him captain."""
    setup = C.Setup("Cap", "pace", 2013, False)
    moves, car = [], C.play_career(deck, model, 21, setup, [])
    while car.pending.kind != "offer" or car.pending.k < 2:
        moves.append(C.policy_move(car, "loyal", random.Random(0)))
        car = C.play_career(deck, model, 21, setup, moves)
    cp = copy.deepcopy(car.checkpoint)
    assert cp.current and not cp.squad, "the fixture must have a club and a regular place"
    cp.captain = cp.current
    return setup, cp, car


def _season(deck, model, setup, cp, first, plan_digits=None):
    """Play one season from `cp`: `first` is the offer move, then whatever comes is answered
    as a loyal player would, with `plan_digits` for a plan."""
    moves = [first]
    for _ in range(40):
        car = C.play_career(deck, model, 21, setup, moves, resume=cp)
        p = car.pending
        if p is None or (p.kind in ("offer", "mega") and p.k > cp.k):
            return car, moves
        if p.kind == "plan" and plan_digits is not None:
            moves.append("p" + plan_digits(p.plan_bowlers))
        else:
            moves.append(C.policy_move(car, "loyal", random.Random(0)))
    raise AssertionError("the season never finished")


def test_the_armband_rule():
    assert C.next_captain(True, False, "x") == "x"
    assert C.next_captain(True, True, "x") is None          # a poor season costs it
    assert C.next_captain(False, False, "x") is None


def test_a_captain_who_stays_leads_again_and_is_asked_for_a_plan(deck, model, captain_cp):
    setup, cp, car = captain_cp
    stay = next(i for i, o in enumerate(car.pending.offers) if o.stay)
    played, moves = _season(deck, model, setup, cp, f"o{stay}")
    assert any(m.startswith("p") for m in moves)
    rec = played.seasons[cp.k]
    assert rec.captain and rec.event != "captain"           # no card: he already holds it


def test_a_captain_who_moves_club_does_not(deck, model, captain_cp):
    setup, cp, car = captain_cp
    away = next(i for i, o in enumerate(car.pending.offers)
                if canonical(o.franchise) != cp.current)
    played, moves = _season(deck, model, setup, cp, f"o{away}")
    rec = played.seasons[cp.k]
    # captain only if the new club asks anew -- and only a captain is asked for a plan
    assert rec.captain == (rec.event == "captain" and rec.choice.startswith("Take"))
    assert rec.captain == any(m.startswith("p") for m in moves)


def test_a_plan_needs_one_digit_per_bowler(deck, model, captain_cp):
    setup, cp, car = captain_cp
    stay = next(i for i, o in enumerate(car.pending.offers) if o.stay)
    car2, moves = C.play_career(deck, model, 21, setup, [f"o{stay}"], resume=cp), [f"o{stay}"]
    for _ in range(5):
        if car2.pending.kind == "plan":
            break
        moves.append(C.policy_move(car2, "loyal", random.Random(0)))
        car2 = C.play_career(deck, model, 21, setup, moves, resume=cp)
    assert car2.pending.kind == "plan", "a captain must be asked for a plan"
    n = len(car2.pending.plan_bowlers)
    assert 5 <= n <= C.PLAN_MAX
    assert any(c.person_id == C.YOU_ID for c in car2.pending.plan_bowlers)
    for bad in ("p" + "0" * (n - 1), "p" + "4" * n, "x" + "0" * n):
        with pytest.raises(C.CareerError):
            C.play_career(deck, model, 21, setup, moves + [bad], resume=cp)


def _your_overs(career, cp_k):
    """The over numbers you bowled in that season's matches (0-based)."""
    overs = []
    for r in career.detail.results:
        for inn in (r.home_innings, r.away_innings):
            overs += [o.over for o in inn.over_log if o.bowler_id == C.YOU_ID]
    return overs


def test_the_plan_reaches_your_overs(deck, model, captain_cp):
    setup, cp, car = captain_cp
    stay = next(i for i, o in enumerate(car.pending.offers) if o.stay)

    def you_at(phase):
        return lambda bowlers: "".join(phase if c.person_id == C.YOU_ID else "0" for c in bowlers)

    death, _ = _season(deck, model, setup, cp, f"o{stay}", you_at("3"))
    power, _ = _season(deck, model, setup, cp, f"o{stay}", you_at("1"))
    d, p = _your_overs(death, cp.k), _your_overs(power, cp.k)
    assert d and p
    assert sum(o >= 15 for o in d) / len(d) > 0.5          # mostly at the death
    assert sum(o < 6 for o in p) / len(p) > 0.5             # mostly in the powerplay
    assert death.checkpoint.plan == {C.YOU_ID: "death"}     # remembered for next season


def test_only_a_captain_may_replan_at_a_key_moment(deck, model):
    setup = C.Setup("NotCap", "opener", 2014, False)
    moves, car = [], C.play_career(deck, model, 5, setup, [])
    for _ in range(80):
        if car.pending.kind == "moment":
            break
        moves.append(C.policy_move(car, "loyal", random.Random(0)))
        car = C.play_career(deck, model, 5, setup, moves)
    assert car.pending.kind == "moment"
    assert car.pending.plan_bowlers == []
    for bad in ("m1p00000", "m1p"):
        with pytest.raises(C.CareerError):
            C.play_career(deck, model, 5, setup, moves + [bad])


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-only-career-secret")


def test_the_api_offers_the_plan_without_borrowed_figures(deck, model, captain_cp):
    from web import career_routes as R
    setup, cp, car = captain_cp
    stay = next(i for i, o in enumerate(car.pending.offers) if o.stay)
    probe = C.play_career(deck, model, 21, setup, [], resume=cp)
    token = R.make_token(21, setup, probe, [])
    from web.app import app
    with TestClient(app) as client:
        d = client.post("/api/career/move", json={"token": token, "move": f"o{stay}"}).json()
        for _ in range(3):
            if d["pending"]["kind"] == "plan":
                break
            d = client.post("/api/career/move", json={"token": d["token"], "move": "e0"}).json()
        assert d["pending"]["kind"] == "plan"
        plan = d["pending"]["plan"]
        me = next(b for b in plan if b["you"])
        assert me["wickets"] is None and me["economy"] is None
        assert all(b["economy"] is not None for b in plan if not b["you"])
        digits = "".join("3" if b["you"] else "0" for b in plan)
        d = client.post("/api/career/move", json={"token": d["token"], "move": "p" + digits}).json()
        assert d["pending"]["kind"] != "plan"
