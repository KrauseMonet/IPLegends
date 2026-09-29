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


# --- the mega auction: retentions and Right to Match [A138] -------------------------------

def test_the_two_formats_are_told_apart_by_the_prefix_and_old_states_keep_replaying(deck):
    old = A.pass_lots(deck, A.new_state(5, "KKR"), "lot").state
    assert old.startswith("A5-") and not A.is_mega(old)
    assert A.replay(deck, old).auction.mega is False
    mega = A.new_state(5, "KKR", mega=True)
    assert mega.startswith("AR5-") and A.is_mega(mega)


def test_a_mega_auction_opens_on_the_retention_screen(deck):
    r = A.replay(deck, A.new_state(3, "RCB", mega=True))
    assert r.phase == "retain" and r.auction is None
    assert r.retention_pool and all(au.franchise_of(c) == "Royal Challengers Bengaluru"
                                    for c in r.retention_pool)


def test_retaining_charges_the_slabs_and_leaves_the_rest_as_cards(deck):
    r = A.retain(deck, A.new_state(3, "RCB", mega=True), [0, 1])
    you = r.you
    assert you.squad[:2] == [r.auction.teams[you.index].squad[0], you.squad[1]]
    assert you.paid[:2] == list(au.RETENTION_SLABS[:2])
    assert you.purse == au.PURSE - sum(au.RETENTION_SLABS[:2])
    assert you.rtm == au.RTM_PLACES - 2


def test_two_seasons_of_one_player_cannot_both_be_retained(deck):
    r = A.replay(deck, A.new_state(3, "RCB", mega=True))
    pool = r.retention_pool
    i, j = next((i, j) for i in range(len(pool)) for j in range(i + 1, len(pool))
                if pool[i].person_id == pool[j].person_id)
    with pytest.raises(A.InvalidState):
        A.retain(deck, r.state, [i, j])


def _walk_to(deck, state, phase, answer=True):
    """Pass on every lot until `phase` is asked, answering any other RTM question with
    `answer`. Returns the replay at that question, or None if the auction ended first."""
    r = A.replay(deck, state)
    while r.phase in ("bid", "rtm_use", "rtm_match", "rtm_raise"):
        if r.phase == phase:
            return r
        r = A.pass_lots(deck, r.state, "lot") if r.phase == "bid" else A.rtm(deck, r.state, answer)
    return None


def test_a_right_to_match_question_is_about_your_own_franchise_s_player(deck):
    start = A.retain(deck, A.new_state(3, "RCB", mega=True), [0, 1]).state
    r = _walk_to(deck, start, "rtm_use")
    assert r is not None
    assert au.franchise_of(r.lot.card) == "Royal Challengers Bengaluru"
    assert r.rtm_other.short != "RCB"


def test_a_right_to_match_question_shows_the_bidding_that_led_to_it(deck):
    """The bids shown with the question end at the hammer, in the winner's name, and are
    exactly what is recorded for the lot once the question is answered."""
    start = A.retain(deck, A.new_state(3, "RCB", mega=True), [0, 1]).state
    r = _walk_to(deck, start, "rtm_use")
    shown = r.preview()
    assert shown, "the bidding that led to the hammer is missing"
    assert shown[-1].team == r.rtm_other.index and shown[-1].price == r.rtm_price
    lot = r.lot
    after = A.rtm(deck, r.state, False)
    sale = next(s for s in after.auction.sales if s.lot.index == lot.index)
    assert sale.bids == shown


def test_using_and_matching_a_card_takes_the_player_and_spends_it(deck):
    start = A.retain(deck, A.new_state(3, "RCB", mega=True), [0, 1]).state
    r = _walk_to(deck, start, "rtm_use")
    lot, cards = r.lot, r.you.rtm
    r = A.rtm(deck, r.state, True)
    if r.phase == "rtm_match":
        r = A.rtm(deck, r.state, True)
    assert lot.card in r.you.squad
    assert r.you.rtm == cards - 1


def test_declining_a_card_leaves_the_player_with_the_winner(deck):
    start = A.retain(deck, A.new_state(3, "RCB", mega=True), [0, 1]).state
    r = _walk_to(deck, start, "rtm_use")
    lot, cards, winner = r.lot, r.you.rtm, r.rtm_other.short
    r = A.rtm(deck, r.state, False)
    # Every replay builds new Team objects, so look the winner up by name, not identity.
    winner_now = next(t for t in r.auction.teams if t.short == winner)
    assert lot.card not in r.you.squad and lot.card in winner_now.squad
    assert r.you.rtm == cards


def test_passing_on_everything_declines_every_card_without_asking(deck):
    r = A.retain(deck, A.new_state(3, "RCB", mega=True), [0, 1])
    r = A.pass_lots(deck, r.state, "all")
    assert r.phase in ("fill", "twelve")
    assert r.you.rtm == au.RTM_PLACES - 2


def test_as_the_winner_your_final_raise_is_recorded_and_cannot_undercut_the_hammer(deck):
    """Win a star outright, then face the old franchise's card."""
    for seed in range(30):
        r = A.retain(deck, A.new_state(seed, "CSK", mega=True), [])
        while r.phase == "bid" and (r.lot.card.display or 0) < 90:
            r = A.pass_lots(deck, r.state, "lot")
        guard = 0
        while r.phase == "bid" and guard < 40:
            r = A.bid(deck, r.state, r.you.max_bid(), done=True)
            guard += 1
            while r.phase == "bid" and (r.lot.card.display or 0) < 90:
                r = A.pass_lots(deck, r.state, "lot")
        if r.phase == "rtm_raise":
            break
    else:
        pytest.skip("no card was played against the human in 30 seeds")
    hammer = r.rtm_price
    with pytest.raises(A.InvalidState):
        A.rtm(deck, r.state, True, hammer - 5)
    after = A.rtm(deck, r.state, True, hammer + 100)
    assert f".x{hammer + 100}" in after.state
