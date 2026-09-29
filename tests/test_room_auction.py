"""Auction rooms (A139): several people and the computer franchises bidding live.

Runs on `tests/test_rooms.py`'s `FakeConn` and the committed deck snapshot, with the clock
driven by hand so a lot's fifteen seconds cost nothing.
"""

from __future__ import annotations

import pytest

import game.auction as au
from etl.feasibility import order_errors
from tests.test_rooms import FakeConn
from tools import snapshot_deck
from web import room_auction as ra
from web import rooms

DOC = snapshot_deck.read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="no deck snapshot committed")


@pytest.fixture(scope="module")
def deck():
    return snapshot_deck.deck_from(DOC)


class Clock:
    def __init__(self, start=1_000_000.0):
        self.now = start

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(ra.time, "time", c)
    monkeypatch.setattr(rooms.time, "time", c)
    return c


def two_human_room(conn, deck, clock, a="MI", b="CSK"):
    room, host = rooms.create_room(conn, "league", 30, "Asha", game="auction")
    room, guest = rooms.join_room(conn, room.code, "Ben", deck)
    rooms.choose_franchise(conn, room.code, host, a)
    rooms.choose_franchise(conn, room.code, guest, b)
    rooms.start_room(conn, room.code, host, deck)
    return room.code, host, guest


def load(conn, code):
    return rooms._load_room(conn, code, lock=False)


# --- the lobby ----------------------------------------------------------------------------

def test_an_auction_room_is_league_only(conn=None):
    conn = FakeConn()
    with pytest.raises(rooms.RoomError):
        rooms.create_room(conn, "cup", 30, "Asha", game="auction")
    room, _ = rooms.create_room(conn, "league", 30, "Asha", game="auction")
    assert room.game == "auction"


def test_two_seats_cannot_hold_the_same_franchise(deck):
    conn = FakeConn()
    room, host = rooms.create_room(conn, "league", 30, "Asha", game="auction")
    room, guest = rooms.join_room(conn, room.code, "Ben", deck)
    rooms.choose_franchise(conn, room.code, host, "MI")
    with pytest.raises(rooms.RoomError):
        rooms.choose_franchise(conn, room.code, guest, "MI")


def test_a_draft_room_has_no_franchises(deck):
    conn = FakeConn()
    room, host = rooms.create_room(conn, "league", 30, "Asha")
    with pytest.raises(rooms.RoomError):
        rooms.choose_franchise(conn, room.code, host, "MI")


def test_starting_fills_the_other_eight_franchises_with_computer_bidders(deck, clock):
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    room = load(conn, code)
    assert room.status == "auctioning"
    assert sorted(p.franchise for p in room.players.values()) == sorted(s for s, _ in au.FRANCHISES)
    cpus = [p for p in room.players.values() if p.is_cpu]
    assert len(cpus) == 8 and all(p.name == dict(au.FRANCHISES)[p.franchise] for p in cpus)
    assert room.turn_started_at == pytest.approx(clock.now + ra.LOT_SECONDS)


# --- a lot ---------------------------------------------------------------------------------

def test_a_bid_must_name_the_price_it_raises_to(deck, clock):
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    r = ra.replay(load(conn, code), deck)
    stale = r.bids[-1].price if r.bids else r.lot.base - 5
    # "outbid" specifically: the replay would refuse a stale bid anyway, but only this
    # check says why, and it is the message a player in a live room actually sees.
    with pytest.raises(ra.AuctionRoomError, match="outbid"):
        ra.submit(conn, code, deck, ra.bid, host, stale)


def test_the_computer_teams_answer_a_human_bid_at_once(deck, clock):
    """Exactly the reply the engine gives, not merely "something happened": the first
    version of this test accepted either the human leading OR a reply, and so passed with
    the computer teams never replying at all. It now waits for a lot the computer teams
    are keen on and checks the whole exchange against `continue_bidding`."""
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    for _ in range(40):
        r = ra.replay(load(conn, code), deck)
        auto = {t.index: au.cpu_ceiling(t, r.lot, r.auction.seed, r.round_no)
                for t in r.auction.teams if not t.human}
        if r.can_bid(r.auction.teams[r.team_of[host]]) \
                and max(auto.values()) > au.next_price(r.next_price):
            break
        for pid in (host, guest):
            ra.submit(conn, code, deck, ra.pass_lot, pid, "lot")
    else:
        pytest.skip("no contested lot in 40")
    ra.submit(conn, code, deck, ra.bid, host, r.next_price)
    after = ra.replay(load(conn, code), deck)
    assert after.lot.index == r.lot.index, "the guest has not decided, so the lot is open"
    expected = au.continue_bidding(r.lot, r.bids + [au.Bid(r.team_of[host], r.next_price)],
                                   auto, r.auction.seed, r.round_no)
    assert after.bids == expected
    assert after.leader != r.team_of[host], "a computer team wanted him more"


def test_a_lot_closes_the_moment_every_human_is_done(deck, clock):
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    first = ra.replay(load(conn, code), deck)
    ra.submit(conn, code, deck, ra.pass_lot, host, "lot")
    mid = ra.replay(load(conn, code), deck)
    assert mid.lot.index == first.lot.index, "one human still to decide"
    ra.submit(conn, code, deck, ra.pass_lot, guest, "lot")
    after = ra.replay(load(conn, code), deck)
    assert (after.lot.index, after.round_no) != (first.lot.index, first.round_no)
    assert not any(m.get("k") == "close" for m in load(conn, code).moves), \
        "an early close is derived from the log, never recorded"


def test_a_bid_extends_the_clock_and_a_new_lot_restarts_it(deck, clock):
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    before = load(conn, code).turn_started_at
    clock.now += 10
    r = ra.replay(load(conn, code), deck)
    ra.submit(conn, code, deck, ra.bid, host, r.next_price)
    after = ra.replay(load(conn, code), deck)
    room = load(conn, code)
    if after.lot.index == r.lot.index:
        assert room.turn_started_at == pytest.approx(before + ra.BID_EXTEND)
    else:
        assert room.turn_started_at == pytest.approx(clock.now + ra.LOT_SECONDS)


def test_the_clock_closes_a_lot_nobody_finished(deck, clock):
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    first = ra.replay(load(conn, code), deck)
    clock.now += ra.LOT_SECONDS + 1
    rooms.room_state(conn, code, deck)
    room = load(conn, code)
    assert room.moves[-1] == {"k": "close", "lot": first.lot.index, "r": first.round_no}
    assert ra.replay(room, deck).lot.index != first.lot.index


def test_a_limit_bids_for_its_owner_up_to_it_and_no_further(deck, clock):
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    r = ra.replay(load(conn, code), deck)
    cap = r.next_price + 300
    ra.submit(conn, code, deck, ra.limit, host, cap)
    ra.submit(conn, code, deck, ra.pass_lot, guest, "lot")
    room = load(conn, code)
    rr = ra.replay(room, deck)
    sale = next(s for s in rr.auction.sales if s.lot.index == r.lot.index)
    mi = rr.team_of[host]
    assert all(b.price <= cap for b in sale.bids if b.team == mi)
    assert any(b.team == mi for b in sale.bids)


def test_a_limit_once_set_cannot_be_lowered(deck, clock):
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    r = ra.replay(load(conn, code), deck)
    ra.submit(conn, code, deck, ra.limit, host, r.next_price + 500)
    r2 = ra.replay(load(conn, code), deck)
    if r2.phase == "bid" and r2.lot.index == r.lot.index:
        with pytest.raises(ra.AuctionRoomError):
            ra.submit(conn, code, deck, ra.limit, host, r.next_price + 100)


def test_someone_not_seated_cannot_bid(deck, clock):
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    r = ra.replay(load(conn, code), deck)
    with pytest.raises(ra.AuctionRoomError):
        ra.submit(conn, code, deck, ra.bid, "__cpu_RCB__", r.next_price)


# --- the whole room ------------------------------------------------------------------------

def run_to_the_end(conn, code, deck, clock):
    """Both humans pass on everything; the clock settles the fill round and the twelves."""
    room = load(conn, code)
    for pid in [p for p, x in room.players.items() if not x.is_cpu]:
        r = ra.replay(load(conn, code), deck)
        if r.phase == "bid":
            ra.submit(conn, code, deck, ra.pass_lot, pid, "all")
    for _ in range(80):
        room = load(conn, code)
        if room.status != "auctioning":
            break
        clock.now = room.turn_started_at + 1
        rooms.room_state(conn, code, deck)
    return load(conn, code)


def test_a_room_whose_humans_skip_everything_still_completes_with_legal_twelves(deck, clock):
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    room = run_to_the_end(conn, code, deck, clock)
    assert room.status == "complete"
    sides = rooms.room_sides(room, deck)
    assert len(sides) == 10
    r = ra.replay(room, deck)
    for pid, player, order, impact in sides:
        team = next(t for t in r.auction.teams if t.short == player.franchise)
        assert len(team.squad) == au.SQUAD_SIZE
        assert order_errors(order, impact, team.squad) == []


def test_a_human_s_chosen_twelve_is_the_one_that_plays(deck, clock):
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    for pid in (host, guest):
        ra.submit(conn, code, deck, ra.pass_lot, pid, "all")
    for _ in range(80):
        room = load(conn, code)
        r = ra.replay(room, deck)
        if r.phase == "twelve":
            break
        clock.now = room.turn_started_at + 1
        rooms.room_state(conn, code, deck)
    order, impact = ra.suggestion(r.auction, r.team_of[host])
    order[0], order[1] = order[1], order[0]           # a legal swap of the openers
    ra.submit(conn, code, deck, ra.twelve, host, order, impact)
    with pytest.raises(ra.AuctionRoomError):
        ra.submit(conn, code, deck, ra.twelve, host, order, impact)
    clock.now = load(conn, code).turn_started_at + 1
    rooms.room_state(conn, code, deck)
    room = load(conn, code)
    assert room.status == "complete"
    side = next(s for s in rooms.room_sides(room, deck) if s[0] == host)
    squad = ra.replay(room, deck).auction.teams[ra.replay(room, deck).team_of[host]].squad
    assert side[2] == [squad[i] for i in order]


def test_replaying_the_log_from_scratch_gives_the_same_auction(deck, clock):
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    r = ra.replay(load(conn, code), deck)
    ra.submit(conn, code, deck, ra.bid, host, r.next_price)
    ra.submit(conn, code, deck, ra.pass_lot, guest, "set")
    room = load(conn, code)
    cached = ra.replay(room, deck)
    ra._CACHE.clear()
    fresh = ra.replay(room, deck)
    assert [(s.lot.index, s.winner, s.price) for s in cached.auction.sales] == \
        [(s.lot.index, s.winner, s.price) for s in fresh.auction.sales]
    assert (cached.lot.index, cached.bids) == (fresh.lot.index, fresh.bids)
