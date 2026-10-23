"""Mega-auction years in Build a Career (A212): every contract ends; your club may offer to
retain you at a slab and you decide; otherwise the auction, at a base price you choose."""

from __future__ import annotations

import random

import pytest
from fastapi.testclient import TestClient

from etl.franchise_map import canonical
from game import career as C
from game.auction import LOYALTY, next_price
from tools.snapshot_deck import deck_from, model_from, read_document

DOC = read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="no deck snapshot")

# Found by search: a middle-order batter whose 2011 market value cannot draw a ₹2 cr base,
# and whose 2010 was NOT poor -- so the squad season the test sees is the late signing's alone.
# [A214] Re-found when clubs began pricing on your Rating blended with last season's grade.
UNSOLD = (6, C.Setup("X", "middle", 2010, False), ["o0", "m1"])


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


def _walk(deck, model, seed, setup, policy="random"):
    """Every pending decision of an automatic career, with the moves that reached it."""
    rng, moves, seen = random.Random(seed), [], []
    while True:
        car = C.play_career(deck, model, seed, setup, moves)
        if car.complete:
            return seen
        seen.append((car.pending, list(moves), car))
        moves.append(C.policy_move(car, policy, rng))


def test_a_mega_year_after_debut_asks_retain_or_auction_and_no_other_year_does(deck, model):
    setup = C.Setup("X", "opener", 2010, False)
    for pending, _, _ in _walk(deck, model, 7, setup, "strength"):
        if pending.kind in ("offer", "mega"):
            mega = pending.year in C.MEGA_YEARS and pending.k > 0
            assert (pending.kind == "mega") == mega, (pending.year, pending.kind)
    debut = C.play_career(deck, model, 3, C.Setup("X", "opener", 2011, False), [])
    assert debut.pending.kind == "offer"           # a debut is a signing, not an auction


def test_a_mega_year_ends_a_three_year_deal(deck, model):
    setup = C.Setup("X", "opener", 2009, False)
    moves = ["c0"]
    car = C.play_career(deck, model, 7, setup, moves)
    while car.pending.kind != "offer" and car.pending.kind != "mega" or car.pending.k < 2:
        moves.append(C.policy_move(car, "loyal", random.Random(0)))
        car = C.play_career(deck, model, 7, setup, moves)
    assert car.seasons[1].contract_year == 2          # 2010 was year two of the deal
    assert car.pending.kind == "mega" and car.pending.year == 2011
    assert car.checkpoint.contract is None


def test_retention_slabs_and_who_offers(deck):
    assert [C.retention_slab(c) for c in (84, 85, 88, 89, 92, 93, 99)] == [
        None, 1100, 1100, 1400, 1400, 1800, 1800]
    csk = canonical("Chennai Super Kings")
    offer = C.mega_offer(deck, 2014, csk, 90)
    assert offer.retain.price == 1400 and canonical(offer.retain.franchise) == csk
    assert C.mega_offer(deck, 2014, csk, 84).retain is None
    assert C.mega_offer(deck, 2016, csk, 95).retain is None      # no Chennai in 2016
    assert offer.bases[offer.suggested] == 150                    # the auction's base for a 90


def test_your_old_club_pays_the_loyalty_premium(deck):
    csk = canonical("Chennai Super Kings")
    mine = C.mega_ceilings(deck, 5, 3, 2014, csk, 88)
    nobody = C.mega_ceilings(deck, 5, 3, 2014, None, 88)
    for fs in mine:
        if canonical(deck.cards_by_fs[fs][0].franchise) == csk:
            # both are truncated to whole lakh, so the premium is checked to within that
            assert mine[fs] > nobody[fs] and abs(mine[fs] - nobody[fs] * LOYALTY) <= 2
        else:
            assert mine[fs] == nobody[fs]
    assert sum(mine[fs] != nobody[fs] for fs in mine) == 1


@pytest.mark.parametrize("card,base", [(80, 50), (88, 100), (93, 200), (76, 30)])
def test_the_bidding_climbs_the_ladder_to_the_second_best_ceiling(deck, card, base):
    """The winner could pay the price; nobody else could pay the next step up."""
    year, k, seed = 2018, 5, 11
    res = C.mega_auction(deck, seed, k, year, None, card, base)
    ceilings = C.mega_ceilings(deck, seed, k, year, None, card)
    by_club = {deck.cards_by_fs[fs][0].franchise: c for fs, c in ceilings.items()}
    assert res.sold
    prices = [p for _, _, p in res.bids]
    assert prices[0] == base and all(b == next_price(a) for a, b in zip(prices, prices[1:]))
    assert res.price == prices[-1]                    # sold at the last bid, not the ceiling
    assert by_club[res.franchise] >= res.price
    assert all(c < next_price(res.price) for f, c in by_club.items() if f != res.franchise)


def test_a_base_nobody_meets_is_unsold_and_a_squad_season(deck, model):
    seed, setup, moves = UNSOLD
    before = C.play_career(deck, model, seed, setup, moves)
    assert before.pending.kind == "mega" and before.pending.mega.retain is None
    assert not before.pending.squad, "the fixture's 2010 must not have been poor"
    ceilings = C.mega_ceilings(deck, seed, before.pending.k, before.pending.year,
                               before.checkpoint.current,
                               C.market_for(before.start, before.ceiling, before.checkpoint))
    assert max(ceilings.values()) < 200, "the fixture must leave a ₹2 cr base unmet"
    moves = moves + ["b0"]
    car = C.play_career(deck, model, seed, setup, moves)
    assert car.auction.sold is False and car.auction.price == 200 and car.auction.bids == []
    while car.pending is not None and car.pending.kind in ("event", "moment"):
        moves.append(C.policy_move(car, "loyal", random.Random(0)))
        car = C.play_career(deck, model, seed, setup, moves)
    rec = car.seasons[-1]
    assert rec.year == 2011 and rec.squad and rec.price == 200 and rec.mega.startswith("Unsold")
    # the late signing is not a poor season: next year's place depends on 2011's form alone
    assert car.checkpoint.squad == C.form_after(False, rec.percentile, C.poor_bar("middle"))[0]


def test_staying_needs_an_offer(deck, model):
    seed, setup, moves = UNSOLD
    with pytest.raises(C.CareerError):
        C.play_career(deck, model, seed, setup, moves + ["k"])


def test_taking_the_retention_keeps_you_at_the_slab(deck, model):
    setup = C.Setup("X", "opener", 2010, False)
    found = next(((p, m) for p, m, _ in _walk(deck, model, 7, setup, "strength")
                  if p.kind == "mega" and p.mega.retain), None)
    assert found, "the fixture must reach a retention offer"
    pending, moves = found
    car = C.play_career(deck, model, 7, setup, moves + ["k"])
    assert car.auction.retained and car.auction.price == pending.mega.retain.price
    assert canonical(car.auction.franchise) == canonical(pending.mega.retain.franchise)


# --- the API -------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-only-career-secret")


def test_the_api_offers_the_choice_and_plays_back_the_auction():
    from web.app import app
    with TestClient(app) as client:
        d = client.post("/api/career/start", json={"name": "Mega", "role": "middle", "debut": 2010,
                                                   "seed": UNSOLD[0]}).json()
        for m in UNSOLD[2]:
            d = client.post("/api/career/move", json={"token": d["token"], "move": m}).json()
        p = d["pending"]
        assert p["kind"] == "mega" and p["mega"]["retain"] is None and p["can_retire"]
        assert p["mega"]["bases"][0] == "₹2.00 cr" and p["mega"]["retain_card"] == C.RETAIN_CARD
        d = client.post("/api/career/move", json={"token": d["token"], "move": "b0"}).json()
        a = d["auction"]
        assert a["sold"] is False and a["price"] == "₹2.00 cr" and a["crest"]
