"""The auction's replayable state (A136, phase 2). No database: the committed deck snapshot.

The rule most worth protecting here is that a BID IS A COMMITMENT. A human shown the
bidding that follows their ₹2 cr can only go on by raising; if they could lower or withdraw
it, the preview would be a free look at how far the computer teams go, which no real
auction room allows and which would make the game trivially exploitable.
"""

from __future__ import annotations

import pytest

import game.auction as au
from etl.feasibility import order_errors
from tools import snapshot_deck
from web import auction_session as A

DOC = snapshot_deck.read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="no deck snapshot committed")


@pytest.fixture(scope="module")
def deck():
    return snapshot_deck.deck_from(DOC)


def to_twelve_phase(deck, seed=11, short="KKR"):
    r = A.pass_lots(deck, A.new_state(seed, short), "all")
    while r.phase == "fill":
        r = A.fill(deck, r.state, 0)
    return r


# --- the string --------------------------------------------------------------------------

def test_encode_folds_runs_of_passes_and_decodes_back():
    toks = ["0", "0", "0", "150", "0", "220o"]
    state = A.encode(9, "MI", toks)
    assert state == "A9-MI-z3.150.z1.220o"
    assert A.decode(state) == (9, "MI", toks)


@pytest.mark.parametrize("bad", ["A9-XYZ-", "A9-MI-12x", "B9-MI-", "A9-MI-t1,2"])
def test_a_malformed_state_is_refused(bad):
    with pytest.raises(A.InvalidState):
        A.decode(bad)


def test_an_open_bid_anywhere_but_last_is_refused(deck):
    with pytest.raises(A.InvalidState):
        A.replay(deck, "A5-KKR-150o.0")


# --- bidding -----------------------------------------------------------------------------

def test_a_bid_once_made_cannot_be_withdrawn(deck):
    r = A.replay(deck, A.new_state(5, "KKR"))
    r = A.bid(deck, r.state, r.lot.base + 100, done=False)
    assert r.open_ceiling == r.lot.base + 100, "expected the computer teams to outbid"
    with pytest.raises(A.InvalidState):
        A.bid(deck, r.state, r.lot.base, done=False)


def test_a_preview_never_shows_the_human_above_what_they_committed(deck):
    r = A.replay(deck, A.new_state(5, "KKR"))
    r = A.bid(deck, r.state, r.lot.base + 100, done=False)
    human = r.you.index
    assert all(b.price <= r.open_ceiling for b in r.preview() if b.team == human)


def test_letting_a_lot_go_keeps_the_bid_that_was_made(deck):
    r = A.replay(deck, A.new_state(5, "KKR"))
    lot = r.lot
    r = A.bid(deck, r.state, lot.base + 100, done=False)
    shown = r.preview()
    assert any(b.team == r.you.index for b in shown)
    r = A.pass_lots(deck, r.state, "lot")
    sale = next(s for s in r.auction.sales if s.lot.index == lot.index)
    # Everything already shown to the human is exactly what was recorded.
    assert sale.bids == shown
    assert r.lot.index != lot.index


def test_a_bid_that_wins_outright_closes_itself(deck):
    r = A.replay(deck, A.new_state(5, "KKR"))
    lot = r.lot
    r = A.bid(deck, r.state, r.you.max_bid(), done=False)
    assert r.open_ceiling is None
    assert lot.card in r.you.squad


def test_a_limit_bid_is_clipped_to_what_the_reserve_allows(deck):
    r = A.replay(deck, A.new_state(5, "KKR"))
    r = A.bid(deck, r.state, 10 ** 7, done=True)
    assert r.you.purse >= au.MIN_PRICE * (au.SQUAD_SIZE - len(r.you.squad))


def test_skipping_a_set_passes_only_that_set(deck):
    r = A.replay(deck, A.new_state(5, "KKR"))
    code = r.lot.set_code
    r = A.pass_lots(deck, r.state, "set")
    assert r.phase == "bid" and r.lot.set_code != code
    skipped = [s for s in r.auction.sales if s.lot.set_code == code]
    assert skipped and all(s.winner != r.you.index for s in skipped)


def test_clicking_up_and_setting_a_limit_reach_the_same_auction(deck):
    """Phase 1's equivalence, now through the string: raise step by step to X, or bid X
    in one go, and the rest of the auction is identical."""
    start = A.replay(deck, A.new_state(21, "RR"))
    target = start.lot.base + 400
    once = A.bid(deck, start.state, target, done=True)

    r, price = start, start.lot.base
    while r.phase == "bid" and r.lot.index == start.lot.index:
        r = A.bid(deck, r.state, price, done=price >= target)
        price = min(target, au.next_price(price))
    assert [(s.lot.index, s.winner, s.price) for s in r.auction.sales] == \
        [(s.lot.index, s.winner, s.price) for s in once.auction.sales]


# --- the end of the auction --------------------------------------------------------------

def test_passing_on_everything_still_ends_with_eighteen_and_a_suggestion(deck):
    r = to_twelve_phase(deck)
    assert r.phase == "twelve"
    assert len(r.you.squad) == au.SQUAD_SIZE
    order, impact = r.suggestion
    assert order_errors(order, impact, r.you.squad) == []


def test_an_illegal_twelve_is_refused(deck):
    r = to_twelve_phase(deck)
    order, impact = A.twelve_indexes(r, r.suggestion)
    order[0], order[10] = order[10], order[0]       # a tailender opens, an opener bats 11
    with pytest.raises(A.InvalidState):
        A.choose_twelve(deck, r.state, order, impact)


def test_the_season_is_played_against_the_teams_bid_against(deck):
    r = to_twelve_phase(deck)
    order, impact = A.twelve_indexes(r, r.suggestion)
    r = A.choose_twelve(deck, r.state, order, impact)
    assert r.phase == "ready"
    yours, others, seed = A.season_sides(deck, r.state)
    assert yours.you and len(yours.xi) == 11
    assert sorted(o.short for o in others) == sorted(
        s for s, _ in au.FRANCHISES if s != "KKR")
    squads = {t.short: {c.person_id for c in t.squad} for t in r.auction.teams}
    for side in others:
        assert {c.person_id for c in side.xi + [side.impact]} <= squads[side.short]
