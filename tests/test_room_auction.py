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


def skip_to_end(conn, code, deck):
    """[A171] The host skips to the end for the whole room (players no longer can)."""
    room = load(conn, code)
    if ra.replay(room, deck).phase == "bid":
        ra.submit(conn, code, deck, ra.pass_lot, room.host_id, "all")


def legacy_skip_set(conn, code, deck, pid):
    """One player's own Skip set, as logs written before A171 hold it. No route records
    this any more, so it is appended directly; the replay must still read it."""
    room = load(conn, code)
    r = ra.replay(room, deck)
    ra.record(room, deck, {"k": "pass_set", "seat": pid, "lot": r.lot.index, "r": r.round_no})
    rooms._save_room(conn, room)


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
        auto = {t.index: au.cpu_ceiling(t, r.lot, r.auction.seed, r.round_no, r.auction.mega)
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


def _rejoin_scenario(conn, deck, code, host, guest):
    """Walk lots with the guest always skipping the set, until one where the guest bids
    back in and the host then takes the lead. Returns the replay at that moment, with the
    lot still on screen, or None. Re-skipping before every lot means the guest is always
    under a Skip when he bids, which is the case room KF3LLG lost."""
    h, g = None, None
    for _ in range(60):
        r = ra.replay(load(conn, code), deck)
        if r.phase != "bid":
            return None
        h, g = r.team_of[host], r.team_of[guest]
        if r.skipping(g) is None:
            legacy_skip_set(conn, code, deck, guest)
            r = ra.replay(load(conn, code), deck)
            if r.phase != "bid" or r.skipping(g) is None:
                continue                       # the skip closed that lot; next one
        lot = r.lot.index
        assert r.skipping(g) == "set" and r.human_done(g, r.flags)
        if r.can_bid(r.auction.teams[g]) and r.leader != g:
            ra.submit(conn, code, deck, ra.bid, guest, r.next_price)
            r = ra.replay(load(conn, code), deck)
            if r.phase == "bid" and r.lot.index == lot and r.leader == g \
                    and r.can_bid(r.auction.teams[h]):
                ra.submit(conn, code, deck, ra.bid, host, r.next_price)
                r = ra.replay(load(conn, code), deck)
                if r.phase == "bid" and r.lot.index == lot and r.leader == h:
                    return r
        r = ra.replay(load(conn, code), deck)
        if r.phase == "bid" and r.lot.index == lot:
            for pid in (host, guest):
                if not r.human_done(r.team_of[pid], r.flags):
                    ra.submit(conn, code, deck, ra.pass_lot, pid, "lot")
                    r = ra.replay(load(conn, code), deck)
                    if r.phase != "bid" or r.lot.index != lot:
                        break
    return None


def test_bidding_after_a_skip_puts_you_back_in(deck, clock):
    """[A170] A Skip used to stand while the same player kept bidding, so the moment the
    other side took the lead every human counted as done and the lot was hammered with no
    chance to answer: room KF3LLG lost lots 8, 14, 16 and 29 that way (on lot 14 the guest
    bid four times, the host bid once, and it sold on the spot)."""
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    r = _rejoin_scenario(conn, deck, code, host, guest)
    assert r is not None, "never found a lot where the guest bid back in and was outbid"
    g = r.team_of[guest]
    assert r.skipping(g) is None
    assert not r.human_done(g, r.flags), "outbid, so the room must wait on the guest"
    assert r.leader == r.team_of[host]


def test_a_log_written_before_a_bid_could_undo_a_skip_still_replays(deck, clock):
    """Back then the lot closed the moment the bidder led and nothing was recorded, so the
    next move is for a later lot. That must read as the lot having closed -- a finished
    room (KF3LLG is one) is replayed every time its results are shown. A move for an
    EARLIER lot is still a corrupt log and still refused."""
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    r = _rejoin_scenario(conn, deck, code, host, guest)
    assert r is not None
    room = load(conn, code)
    lot, rn, leader, price = r.lot.index, r.round_no, r.leader, r.bids[-1].price
    old_log = room.moves + [{"k": "pass", "seat": host, "lot": lot + 1, "r": rn}]
    humans = {pid: p.franchise for pid, p in room.players.items() if not p.is_cpu}
    replayed = ra._replay(room.seed, humans, old_log, deck, False)
    sale = next(x for x in replayed.auction.sales if x.lot.index == lot)
    assert (sale.winner, sale.price) == (leader, price)
    assert replayed.lot.index > lot
    corrupt = room.moves + [{"k": "pass", "seat": host, "lot": lot - 1, "r": rn}]
    with pytest.raises(ra.AuctionRoomError, match="another lot"):
        ra._replay(room.seed, humans, corrupt, deck, False)


@pytest.mark.parametrize("scope", ["set", "all"])
def test_only_the_host_can_skip_ahead(deck, clock, scope):
    """[A171] Skipping a set or to the end moves the whole room on, so a player who is not
    the host is refused and nothing is recorded."""
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    before = list(load(conn, code).moves)
    with pytest.raises(ra.AuctionRoomError, match="only the host"):
        ra.submit(conn, code, deck, ra.pass_lot, guest, scope)
    assert load(conn, code).moves == before


def test_the_host_skipping_a_set_skips_it_for_everybody(deck, clock):
    """Every human passes the rest of the set at once, so the whole set resolves in the
    one request and the room lands on the next set -- nobody is left half-in it."""
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    r = ra.replay(load(conn, code), deck)
    skipped = r.lot.set_code
    ra.submit(conn, code, deck, ra.pass_lot, host, "set")
    after = ra.replay(load(conn, code), deck)
    assert after.phase != "bid" or after.lot.set_code != skipped
    rest = [x for x in r.auction.lots if x.set_code == skipped]
    assert all(any(s.lot.index == x.index for s in after.auction.sales) for x in rest)
    assert [m["k"] for m in load(conn, code).moves] == ["skip_set"]


def test_a_bid_tops_the_clock_up_and_a_new_lot_restarts_it(deck, clock):
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    before = load(conn, code).turn_started_at
    clock.now += 12                                   # 3 s left, under the bid window
    r = ra.replay(load(conn, code), deck)
    ra.submit(conn, code, deck, ra.bid, host, r.next_price)
    after = ra.replay(load(conn, code), deck)
    room = load(conn, code)
    if after.lot.index == r.lot.index:
        assert room.turn_started_at == pytest.approx(clock.now + ra.BID_WINDOW)
        assert room.turn_started_at > before
    else:
        assert room.turn_started_at == pytest.approx(clock.now + ra.LOT_SECONDS)


def test_rapid_bids_never_run_the_clock_past_its_window(deck, clock):
    """[A158] Adding time per bid let a fast bidding war push the deadline out without
    limit. A bid only tops the clock up, so however many land, at most LOT_SECONDS (or
    BID_WINDOW after the latest bid) is ever left."""
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    lot = ra.replay(load(conn, code), deck).lot.index
    bids = 0
    for _ in range(12):
        r = ra.replay(load(conn, code), deck)
        if r.phase != "bid" or r.lot.index != lot:
            break
        pid = next((p for p in (host, guest) if r.team_of[p] != r.leader
                    and r.can_bid(r.auction.teams[r.team_of[p]])), None)
        if pid is None:
            break
        ra.submit(conn, code, deck, ra.bid, pid, r.next_price)
        bids += 1
        clock.now += 0.5
        left = load(conn, code).turn_started_at - clock.now
        assert left <= ra.LOT_SECONDS
    assert bids >= 4, "the fixture never got a bidding war going"


def test_the_clock_closes_a_lot_nobody_finished(deck, clock):
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    first = ra.replay(load(conn, code), deck)
    clock.now += ra.LOT_SECONDS + rooms.CLOCK_GRACE_S + 1
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
    skip_to_end(conn, code, deck)
    for _ in range(80):
        room = load(conn, code)
        if room.status != "auctioning":
            break
        clock.now = room.turn_started_at + rooms.CLOCK_GRACE_S + 1
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
    skip_to_end(conn, code, deck)
    for _ in range(80):
        room = load(conn, code)
        r = ra.replay(room, deck)
        if r.phase == "twelve":
            break
        clock.now = room.turn_started_at + rooms.CLOCK_GRACE_S + 1
        rooms.room_state(conn, code, deck)
    order, impact = ra.suggestion(r.auction, r.team_of[host])
    order[0], order[1] = order[1], order[0]           # a legal swap of the openers
    ra.submit(conn, code, deck, ra.twelve, host, order, impact)
    with pytest.raises(ra.AuctionRoomError):
        ra.submit(conn, code, deck, ra.twelve, host, order, impact)
    clock.now = load(conn, code).turn_started_at + rooms.CLOCK_GRACE_S + 1
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
    ra.submit(conn, code, deck, ra.pass_lot, host, "set")
    room = load(conn, code)
    cached = ra.replay(room, deck)
    ra._CACHE.clear()
    fresh = ra.replay(room, deck)
    assert [(s.lot.index, s.winner, s.price) for s in cached.auction.sales] == \
        [(s.lot.index, s.winner, s.price) for s in fresh.auction.sales]
    assert (cached.lot.index, cached.bids) == (fresh.lot.index, fresh.bids)


# --- mega rooms: retentions and Right to Match [A140] -------------------------------------

def mega_room(conn, deck, clock, a="RCB", b="KKR"):
    room, host = rooms.create_room(conn, "league", 30, "Asha", game="mega")
    room, guest = rooms.join_room(conn, room.code, "Ben", deck)
    rooms.choose_franchise(conn, room.code, host, a)
    rooms.choose_franchise(conn, room.code, guest, b)
    rooms.start_room(conn, room.code, host, deck)
    return room.code, host, guest


def distinct(pool, n):
    out, seen = [], set()
    for i, c in enumerate(pool):
        if c.person_id not in seen:
            seen.add(c.person_id)
            out.append(i)
        if len(out) == n:
            break
    return out


def test_a_mega_room_opens_on_retentions_for_every_human(deck, clock):
    conn = FakeConn()
    code, host, guest = mega_room(conn, deck, clock)
    r = ra.replay(load(conn, code), deck)
    assert r.phase == "retain" and sorted(r.waiting_on()) == sorted([host, guest])
    assert load(conn, code).turn_started_at == pytest.approx(clock.now + ra.RETAIN_SECONDS)
    for pid in (host, guest):
        team = r.auction.teams[r.team_of[pid]]
        assert all(au.franchise_of(c) == team.franchise for c in r.pools[team.index])


def test_a_shared_legend_goes_to_the_franchise_with_his_better_season(deck, clock):
    """[A141] Gayle's best RCB season rates 99, his best KKR season 87. Ben (KKR) asks
    FIRST and claims a season rated higher than the one Asha (RCB) claims -- so neither
    first-come nor comparing the claimed seasons would give him to RCB. Only the rule as
    ratified (each franchise's best season of him) does."""
    conn = FakeConn()
    code, host, guest = mega_room(conn, deck, clock)      # Asha RCB, Ben KKR
    r = ra.replay(load(conn, code), deck)
    rcb, kkr = r.team_of[host], r.team_of[guest]
    gayle_rcb_2014 = next(i for i, c in enumerate(r.pools[rcb])
                          if c.name == "CH Gayle" and c.season_year == 2014)
    gayle_kkr = next(i for i, c in enumerate(r.pools[kkr]) if c.name == "CH Gayle")
    other_kkr = next(i for i, c in enumerate(r.pools[kkr]) if c.name != "CH Gayle")
    assert r.pools[kkr][gayle_kkr].display > r.pools[rcb][gayle_rcb_2014].display
    ra.submit(conn, code, deck, ra.retain, guest, [gayle_kkr, other_kkr])   # Ben first
    ra.submit(conn, code, deck, ra.retain, host, [gayle_rcb_2014])          # sealed: accepted
    after = ra.replay(load(conn, code), deck)
    rcb_t, kkr_t = after.auction.teams[rcb], after.auction.teams[kkr]
    assert "CH Gayle" in [c.name for c in rcb_t.squad[:rcb_t.retained]]
    assert "CH Gayle" not in [c.name for c in kkr_t.squad]
    # Ben keeps his other pick, at the FIRST slab now, and the lost place is a card.
    assert kkr_t.retained == 1 and kkr_t.paid[0] == au.RETENTION_SLABS[0]
    assert kkr_t.rtm == au.RTM_PLACES - 1
    assert [(c.name, w) for c, w in after.retention_lost[kkr]] == [("CH Gayle", rcb)]


def test_retentions_charge_the_slabs_and_the_rest_become_cards(deck, clock):
    conn = FakeConn()
    code, host, guest = mega_room(conn, deck, clock)
    r = ra.replay(load(conn, code), deck)
    ra.submit(conn, code, deck, ra.retain, host, distinct(r.pools[r.team_of[host]], 2))
    ra.submit(conn, code, deck, ra.retain, guest, [])
    r = ra.replay(load(conn, code), deck)
    rcb, kkr = r.auction.teams[r.team_of[host]], r.auction.teams[r.team_of[guest]]
    assert rcb.paid[:2] == list(au.RETENTION_SLABS[:2]) and rcb.rtm == au.RTM_PLACES - 2
    assert kkr.retained == 0 and kkr.rtm == au.RTM_PLACES
    assert not {c.person_id for c in rcb.squad} & {lot.card.person_id for lot in r.auction.lots}


def test_a_retention_nobody_submits_is_what_a_computer_team_would_keep(deck, clock):
    conn = FakeConn()
    code, host, guest = mega_room(conn, deck, clock)
    ra.submit(conn, code, deck, ra.retain, host, [])
    before = ra.replay(load(conn, code), deck)
    expected = ra.retention_suggestion(before, before.team_of[guest])
    clock.now = load(conn, code).turn_started_at + rooms.CLOCK_GRACE_S + 1
    rooms.room_state(conn, code, deck)
    assert load(conn, code).moves[-1] == {"k": "retain", "seat": guest, "picks": expected}


def walk_to_rtm(conn, code, deck, host, guest, clock, kinds=("rtm_use",)):
    """Both humans pass lot by lot until a Right to Match question in `kinds` appears."""
    for _ in range(600):
        r = ra.replay(load(conn, code), deck)
        if r.phase in kinds:
            return r
        if r.phase != "bid":
            return None
        for pid in (host, guest):
            rr = ra.replay(load(conn, code), deck)
            if rr.phase == "bid" and (rr.lot.index, rr.round_no) == (r.lot.index, r.round_no) \
                    and not rr.human_done(rr.team_of[pid], {}):
                ra.submit(conn, code, deck, ra.pass_lot, pid, "lot")
    return None


def started_mega(conn, deck, clock):
    code, host, guest = mega_room(conn, deck, clock)
    for pid in (host, guest):
        ra.submit(conn, code, deck, ra.retain, pid, [])
    return code, host, guest


def test_a_card_is_offered_to_the_franchise_the_season_was_played_for(deck, clock):
    conn = FakeConn()
    code, host, guest = started_mega(conn, deck, clock)
    r = walk_to_rtm(conn, code, deck, host, guest, clock)
    assert r is not None, "no Right to Match question in the whole auction"
    asked = r.auction.teams[r.rtm_team]
    assert asked.human and asked.franchise == au.franchise_of(r.lot.card)
    assert r.bids and r.bids[-1].team == r.rtm_other and r.bids[-1].price == r.rtm_price


def test_using_and_matching_a_card_takes_the_player_and_spends_the_card(deck, clock):
    conn = FakeConn()
    code, host, guest = started_mega(conn, deck, clock)
    r = walk_to_rtm(conn, code, deck, host, guest, clock)
    pid = r.pid_of[r.rtm_team]
    lot, cards = r.lot, r.auction.teams[r.rtm_team].rtm
    ra.submit(conn, code, deck, ra.rtm, pid, True)
    r2 = ra.replay(load(conn, code), deck)
    if r2.phase == "rtm_match":
        ra.submit(conn, code, deck, ra.rtm, pid, True)
    after = ra.replay(load(conn, code), deck)
    team = after.auction.teams[after.team_of[pid]]
    assert lot.card in team.squad and team.rtm == cards - 1


def test_a_card_nobody_answers_is_not_played(deck, clock):
    conn = FakeConn()
    code, host, guest = started_mega(conn, deck, clock)
    r = walk_to_rtm(conn, code, deck, host, guest, clock)
    pid = r.pid_of[r.rtm_team]
    clock.now = load(conn, code).turn_started_at + rooms.CLOCK_GRACE_S + 1
    rooms.room_state(conn, code, deck)
    assert load(conn, code).moves[-1] == {"k": "rtm_use", "seat": pid, "use": False}
    after = ra.replay(load(conn, code), deck)
    assert r.lot.card not in after.auction.teams[r.rtm_team].squad


def test_someone_who_skipped_to_the_end_is_never_asked_about_a_card(deck, clock):
    conn = FakeConn()
    code, host, guest = started_mega(conn, deck, clock)
    skip_to_end(conn, code, deck)
    r = ra.replay(load(conn, code), deck)
    assert r.phase in ("fill", "twelve")
    assert not any(m["k"].startswith("rtm") for m in load(conn, code).moves)


# --- late and repeated moves [A146] ---------------------------------------------------------

def test_a_bid_inside_the_grace_lands_on_the_lot_it_was_made_for(deck, clock):
    """A bid with a second on the clock reaches the server a round trip later; it used to
    find the lot closed. Inside CLOCK_GRACE_S it lands, and extends the clock."""
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    r = ra.replay(load(conn, code), deck)
    clock.now = load(conn, code).turn_started_at + rooms.CLOCK_GRACE_S / 2
    rooms.room_state(conn, code, deck)
    assert not any(m["k"] == "close" for m in load(conn, code).moves), "not closed yet"
    ra.submit(conn, code, deck, ra.bid, host, r.next_price, r.lot.index, au.ROUNDS[r.round_no])
    assert load(conn, code).moves[-1]["k"] == "bid"


def test_a_pass_for_a_closed_lot_is_refused_not_applied_to_the_next(deck, clock):
    """Without naming its lot, a pass that arrived after its lot closed passed on the NEXT
    player -- one the person never saw. It is refused as stale instead, the refusal carries
    the room as it now is, and the clock close it caught up is saved with nothing else."""
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    first = ra.replay(load(conn, code), deck)
    clock.now = load(conn, code).turn_started_at + rooms.CLOCK_GRACE_S + 1
    with pytest.raises(rooms.StaleMove) as refused:
        ra.submit(conn, code, deck, ra.pass_lot, host, "lot", first.lot.index,
                  au.ROUNDS[first.round_no])
    assert isinstance(refused.value, ra.AuctionRoomError), "still an auction refusal"
    now_open = ra.replay(refused.value.room, deck)
    assert now_open.lot.index != first.lot.index
    moves = load(conn, code).moves
    assert moves == [{"k": "close", "lot": first.lot.index, "r": first.round_no}], \
        "the close is saved and the refused pass is not"


def test_a_retried_pass_or_limit_records_nothing_twice(deck, clock):
    """A move whose response was lost is retried by the page. With the lot named it can
    only ever land on that lot, and a repeat is a no-op rather than a second move. Each
    repeat is made while the OTHER human is still deciding, so the lot is still open and
    it is the no-op, not the stale-lot refusal, being tested."""
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    r = ra.replay(load(conn, code), deck)
    lot, rnd = r.lot.index, au.ROUNDS[r.round_no]
    ra.submit(conn, code, deck, ra.pass_lot, host, "lot", lot, rnd)
    n = len(load(conn, code).moves)
    ra.submit(conn, code, deck, ra.pass_lot, host, "lot", lot, rnd)
    assert len(load(conn, code).moves) == n

    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    for _ in range(40):
        r = ra.replay(load(conn, code), deck)
        if r.can_bid(r.auction.teams[r.team_of[guest]]):
            break
        for pid in (host, guest):
            ra.submit(conn, code, deck, ra.pass_lot, pid, "lot")
    lot, rnd = r.lot.index, au.ROUNDS[r.round_no]
    ceiling = r.auction.teams[r.team_of[guest]].max_bid()
    ra.submit(conn, code, deck, ra.limit, guest, ceiling, lot, rnd)
    assert ra.replay(load(conn, code), deck).lot.index == lot, "the host is still deciding"
    n = len(load(conn, code).moves)
    ra.submit(conn, code, deck, ra.limit, guest, ceiling, lot, rnd)
    assert len(load(conn, code).moves) == n


def test_a_move_refused_inside_the_replay_is_never_saved(deck, clock):
    """`record` appends a move before replaying it, and a fill index out of range is only
    found by the replay. When the same request also caught up an expired clock, the
    catch-up is saved -- and without restoring the log, the refused move went with it, so
    every later replay of the room raised and the room was dead for everybody."""
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    skip_to_end(conn, code, deck)
    for _ in range(80):
        room = load(conn, code)
        if ra.replay(room, deck).phase == "fill":
            break
        clock.now = room.turn_started_at + rooms.CLOCK_GRACE_S + 1
        rooms.room_state(conn, code, deck)
    clock.now = load(conn, code).turn_started_at + rooms.CLOCK_GRACE_S + 1
    ahead = load(conn, code)
    ra.resolve(ahead, deck)                          # what the request will catch up to
    r = ra.replay(ahead, deck)
    if r.phase != "fill" or r.fill_team not in r.pid_of:
        pytest.skip("the clock's fill ended the humans' turns")
    with pytest.raises(ra.AuctionRoomError):
        ra.submit(conn, code, deck, ra.fill, r.pid_of[r.fill_team], 10**6)
    stored = load(conn, code)
    assert len(stored.moves) == len(ahead.moves), "the catch-up is saved..."
    ra._CACHE.clear()
    ra.replay(stored, deck)                          # ...and the room still replays


# --- a team the fill round cannot complete [A150] ------------------------------------------

def _careless_room(conn, deck, clock, seed, game="mega"):
    """Two humans who keep nobody, pass on everything and always take fill option 0 -- the
    shape that left a human squad with no legal twelve before A150."""
    room, host = rooms.create_room(conn, "league", 30, "Asha", game=game)
    room, guest = rooms.join_room(conn, room.code, "Ben", deck)
    rooms.choose_franchise(conn, room.code, host, "MI")
    rooms.choose_franchise(conn, room.code, guest, "CSK")
    conn.execute("update rooms set seed = %s where code = %s", (seed, room.code))
    rooms.start_room(conn, room.code, host, deck)
    code = room.code
    for _ in range(400):
        rm = load(conn, code)
        if rm.status != "auctioning":
            break
        r = ra.replay(rm, deck)
        if r.phase == "retain":
            for pid in r.waiting_on():
                ra.submit(conn, code, deck, ra.retain, pid, [])
        elif r.phase == "bid":
            skip_to_end(conn, code, deck)
        elif r.phase == "fill":
            ra.submit(conn, code, deck, ra.fill, r.pid_of[r.fill_team], 0)
        elif r.phase.startswith("rtm"):
            ra.submit(conn, code, deck, ra.rtm, r.pid_of[r.rtm_team], False)
        else:
            break
    return code, host, guest


def test_a_careless_room_can_still_field_every_twelve(deck, clock):
    """Seed 8 is the measured case: CSK's last fill place needed a wicketkeeper and none was
    left in the unsold lots or the register, so the squad ended one short with no legal
    twelve -- and the twelve step then raised on every poll, freezing the room for good.
    The fill round now widens to the whole deck when nothing else fits."""
    conn = FakeConn()
    code, host, guest = _careless_room(conn, deck, clock, seed=8)
    r = ra.replay(load(conn, code), deck)
    assert r.phase == "twelve", r.phase
    assert not r.auction.stranded
    assert all(r.auction.twelve(t) is not None for t in r.auction.teams)
    clock.now = load(conn, code).turn_started_at + rooms.CLOCK_GRACE_S + 1
    room = rooms.room_state(conn, code, deck)     # the twelve timeout: used to raise here
    assert room.status == "complete"
    assert len(rooms.room_sides(room, deck)) == 10


def test_a_team_that_still_cannot_field_a_twelve_ends_the_room_cleanly(deck, clock, monkeypatch):
    """The last line of defence: if a team is ever left without a legal twelve anyway, the
    room is marked failed with a reason for every seat -- never an exception on each poll."""
    real = ra.au.fill_options
    monkeypatch.setattr(ra.au, "fill_options",
                        lambda auction, team: [] if team.short == "CSK" else real(auction, team))
    ra._CACHE.clear()
    conn = FakeConn()
    code, host, guest = _careless_room(conn, deck, clock, seed=8)
    # Failed by the very move that left the team short -- not left 'auctioning' in a phase
    # no page can render until the next clock catch-up gets round to it.
    assert load(conn, code).status == "failed"
    clock.now = load(conn, code).turn_started_at + rooms.CLOCK_GRACE_S + 1
    room = rooms.room_state(conn, code, deck)
    assert room.status == "failed"
    assert "Chennai Super Kings could not field a legal twelve" in room.failure_reason
    ra._CACHE.clear()


def test_the_sets_list_every_lot_in_calling_order_with_how_each_went(deck, clock):
    """[A172] The "This set" / "All sets" panels. Checked against the auction's own lots
    and sales, not against the builder's output: every lot appears once, in its set, in
    the order it is called; a sold lot names its real buyer and price; the lot on the
    block is the one marked current, and its set is the only current set."""
    from web.app import _catalogue_out
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, clock)
    first = ra.replay(load(conn, code), deck).lot.set_code
    ra.submit(conn, code, deck, ra.pass_lot, host, "set")
    r = ra.replay(load(conn, code), deck)
    assert r.phase == "bid"
    out = _catalogue_out(r.auction, r.lot, r.phase)

    assert [x.lot for s in out.sets for x in s.lots] == [x.index for x in r.auction.lots]
    by_index = {x.index: x for x in r.auction.lots}
    for s in out.sets:
        assert all(by_index[x.lot].set_code == s.code for x in s.lots)
    assert len({s.code for s in out.sets}) == len(out.sets), "a set appears twice"

    sales = {x.lot.index: x for x in r.auction.sales}
    rows = {x.lot: x for s in out.sets for x in s.lots}
    for i, row in rows.items():
        sale = sales.get(i)
        if i == r.lot.index:
            assert row.status == "current"
        elif sale is None:
            assert row.status == "upcoming" and row.team is None
        elif sale.winner is None:
            assert row.status == "unsold"
        else:
            assert (row.status, row.team, row.price) == \
                ("sold", r.auction.teams[sale.winner].short, sale.price)
    skipped = next(s for s in out.sets if s.code == first)
    assert all(x.status in ("sold", "unsold") for x in skipped.lots)
    assert [s.code for s in out.sets if s.current] == [r.lot.set_code] == [out.current_set]
    assert any(x.status == "sold" for x in skipped.lots)


def test_the_sets_are_empty_until_retentions_are_done(deck, clock):
    from web.app import _catalogue_out
    conn = FakeConn()
    code, host, guest = mega_room(conn, deck, clock)
    r = ra.replay(load(conn, code), deck)
    assert r.phase == "retain"
    out = _catalogue_out(r.auction, r.lot, r.phase)
    assert out.sets == [] and out.current_set is None
