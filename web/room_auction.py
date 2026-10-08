"""An auction room: several people and the computer franchises bidding live. [A139]

The room machinery is `web/rooms.py`'s, unchanged in shape: one shared, ordered move log
(`rooms.moves`), the whole auction rebuilt from it on every request, and anything whose
clock has run out resolved by whichever request arrives next. Nothing runs in the
background (A62).

What is different from the single-player auction (`game.auction.run_auction`) is who
decides a lot. There, the one human's whole decision is a single number (their limit),
because the computer teams' limits are fixed before the lot opens and the human bids
first. With several people bidding against each other a lot is a sequence of events:

    {"k": "bid",   "seat": pid, "lot": i, "r": round, "p": price}   raise to the named price
    {"k": "limit", "seat": pid, "lot": i, "r": round, "max": m}     bid for me up to m
    {"k": "pass",  "seat": pid, "lot": i, "r": round}               not interested
    {"k": "skip_set", "seat": host, "lot": i, "r": round}           the HOST skips the rest of
                                                                    this set for everybody
    {"k": "skip_all", "seat": host, "lot": i, "r": round}           ...or everything left
    {"k": "pass_set" | "pass_all", ...}                             one player's own skip: no
                                                                    longer accepted (A171), still
                                                                    replayed from older logs
    {"k": "close", "lot": i, "r": round}                            the lot's clock ran out
    {"k": "fill",  "seat": pid, "i": n}                             a fill-round choice
    {"k": "twelve", "seat": pid, "order": [...11], "impact": n}     the chosen twelve

[A174] With the room's trade window on (`rooms.trades`) and two or more people bidding,
the fill round is followed by a trade window before the twelves:

    {"k": "offer",  "seat": pid, "to": pid, "give": person, "get": person}  one for one
    {"k": "answer", "seat": pid, "offer": n, "yes": bool}           accept or decline offer n
    {"k": "withdraw", "seat": pid, "offer": n}                      take your own offer back
    {"k": "ready",  "seat": pid, "on": bool}                        done trading (or not)
    {"k": "trade_end", "seat": host}                                the host closes the window
    {"k": "trade_close"}                                            the window's clock ran out

After every human event the AUTOMATIC bidders reply at once: every computer team, from a
limit fixed when the lot opened, and every human who set a limit. So all the people in the
room see the same exchange, and a human can only ever answer it.

**A lot closes the moment every human is done with it** -- passed, set a limit, leading,
or simply unable to buy -- which is decided from the log alone and so needs no recorded
move. Only a close forced by the CLOCK is recorded (`close`), because time is the one input
the log would otherwise lack. Without the early close, 260 lots at fifteen seconds each is
over an hour.

A bid names the price it raises to, so a bid made against a price somebody has already
beaten is refused as "outbid" rather than applied to a room it no longer describes -- the
same reasoning A119 applied to a room's version counter.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field

import game.auction as au
from etl.feasibility import Card, Deck, order_errors
from web.rooms import CLOCK_GRACE_S, StaleMove

LOT_SECONDS = 15         # ratified by the user: fifteen seconds a lot...
BID_WINDOW = 10          # ...and a bid guarantees everyone this long to answer it [A158]
FILL_SECONDS = 20        # a fill-round choice
TWELVE_SECONDS = 150     # choosing a twelve from eighteen (was 90; A171)
RETAIN_SECONDS = 150     # [A140] choosing retentions, everybody at once (was 90; A171)
RTM_SECONDS = 15         # [A140] each Right to Match decision: play, raise, match
TRADE_SECONDS = 120      # [A174] the trade window, closed early by the host or by everyone ready
TRADES_PER_TEAM = 2      # [A174] ratified by the user
MAX_OPEN_OFFERS = 3      # [A174] one seat's open offers at once, so nobody floods the others

AUCTION_GAMES = ("auction", "mega")   # 'mega' adds retentions and Right to Match [A140]


def is_auction(room) -> bool:
    return room.game in AUCTION_GAMES


class AuctionRoomError(ValueError):
    """A move this room cannot accept -- refused with a 4xx, never a 500."""


class StaleAuctionMove(StaleMove, AuctionRoomError):
    """`submit`'s refusal: still an AuctionRoomError to anyone catching one, and a
    `rooms.StaleMove` carrying the caught-up room to the route that answers it [A146]."""


@dataclass
class RoomAuctionReplay:
    auction: au.Auction
    team_of: dict[str, int]                 # human player_id -> team index
    phase: str      # retain | bid | rtm_use | rtm_raise | rtm_match | fill | trade | twelve | complete | failed
    lot: au.Lot | None = None
    round_no: int = 0
    bids: list[au.Bid] = field(default_factory=list)
    proxies: dict[int, int] = field(default_factory=dict)
    passed: set[int] = field(default_factory=set)
    fill_team: int | None = None
    fill_options: list[Card] = field(default_factory=list)
    twelves: dict[str, tuple[list[Card], Card]] = field(default_factory=dict)
    # [A140] mega rooms: the retention phase, and a Right to Match being decided
    pools: dict[int, list[Card]] = field(default_factory=dict)
    retained: dict[int, list[Card]] = field(default_factory=dict)
    rtm_team: int | None = None             # the human being asked
    rtm_other: int | None = None            # the winner (use, match) or holder (raise)
    rtm_price: int | None = None
    failed_team: int | None = None          # [A150] a team with no legal twelve
    # [A170] Each human's standing passes ("all", or (set, round)), so the API can say
    # that a player is skipping -- `passed` alone is only this lot.
    flags: dict[int, set] = field(default_factory=dict)
    # [A174] the trade window: every offer made (by number), every trade completed, who
    # has said they are done, and how many trades each team has used. `trades` stays on
    # every later phase too, so the room can announce a trade that closed the window.
    offers: list["Offer"] = field(default_factory=list)
    trades: list["Trade"] = field(default_factory=list)
    trade_ready: set[int] = field(default_factory=set)

    @property
    def pid_of(self) -> dict[int, str]:
        return {i: pid for pid, i in self.team_of.items()}

    @property
    def next_price(self) -> int | None:
        if self.lot is None:
            return None
        return au.next_price(self.bids[-1].price) if self.bids else self.lot.base

    @property
    def leader(self) -> int | None:
        return self.bids[-1].team if self.bids else None

    def stage(self) -> tuple:
        """What the room is waiting on, as a comparable value: when it changes, a new
        clock starts."""
        if self.phase == "bid":
            return ("bid", self.round_no, self.lot.index)
        if self.phase == "fill":
            return ("fill", self.fill_team, len(self.auction.teams[self.fill_team].squad))
        if self.phase.startswith("rtm"):
            return (self.phase, self.round_no, self.lot.index)
        return (self.phase,)

    def can_bid(self, team: au.Team) -> bool:
        return (self.phase == "bid" and team.may_buy(self.lot.card)
                and team.max_bid() >= self.next_price)

    def human_done(self, team_index: int, flags: dict[int, set]) -> bool:
        team = self.auction.teams[team_index]
        return (team_index in self.passed or team_index in self.proxies
                or self.leader == team_index or not self.can_bid(team)
                or "all" in flags.get(team_index, set())
                or (self.lot.set_code, self.round_no) in flags.get(team_index, set()))

    def skipping(self, team_index: int) -> str | None:
        """[A170] 'all' or 'set' when this human is passing on the current lot because of
        an earlier Skip, None otherwise. A bid or limit clears it."""
        f = self.flags.get(team_index, set())
        if "all" in f:
            return "all"
        if self.lot is not None and (self.lot.set_code, self.round_no) in f:
            return "set"
        return None

    def waiting_on(self) -> list[str]:
        """The humans the room is waiting for right now."""
        if self.phase == "fill":
            return [self.pid_of[self.fill_team]] if self.fill_team in self.pid_of else []
        if self.phase == "twelve":
            return [pid for pid in self.team_of if pid not in self.twelves]
        if self.phase == "retain":
            return [pid for pid, i in self.team_of.items() if i not in self.retained]
        if self.phase == "trade":
            return [pid for pid, i in self.team_of.items() if i not in self.trade_ready]
        if self.phase.startswith("rtm"):
            return [self.pid_of[self.rtm_team]]
        return []

    def trades_used(self, team_index: int) -> int:
        return sum(team_index in (t.a, t.b) for t in self.trades)

    def arrived(self, team_index: int) -> set[str]:
        """People who came to this team in a trade: they may not be traded on."""
        out = set()
        for t in self.trades:
            if t.a == team_index:
                out.add(t.b_gave.person_id)
            elif t.b == team_index:
                out.add(t.a_gave.person_id)
        return out

    def offer_errors(self, a: int, b: int, give: Card, get: Card) -> list[str]:
        """[A174] Every rule of the window, from `a`'s side. `game.auction.trade_errors` is
        the squad half; the rest is the window's own."""
        teams = self.auction.teams
        errors = []
        if a == b:
            return ["you cannot trade with yourself"]
        if b not in self.team_of.values():
            return ["trades are between the people in the room, not the computer franchises"]
        if self.trades_used(a) >= TRADES_PER_TEAM:
            errors.append(f"you have used your {TRADES_PER_TEAM} trades")
        if self.trades_used(b) >= TRADES_PER_TEAM:
            errors.append(f"{teams[b].short} has used its {TRADES_PER_TEAM} trades")
        if give.person_id in self.arrived(a):
            errors.append(f"{give.name} arrived in a trade and cannot be traded on")
        if get.person_id in self.arrived(b):
            errors.append(f"{get.name} arrived in a trade and cannot be traded on")
        return errors + au.trade_errors(teams[a], teams[b], give, get)

    @property
    def retention_lost(self) -> dict[int, list[tuple[Card, int]]]:
        """[A141] Legends each human claimed and lost to a better season elsewhere."""
        return getattr(self.auction, "retention_lost", {})


# --- replay --------------------------------------------------------------------------------

_CACHE: "OrderedDict[tuple, RoomAuctionReplay]" = OrderedDict()
_CACHE_SIZE = 64


def replay(room, deck: Deck) -> RoomAuctionReplay:
    """The room's auction as of its move log. Cached per (room, seed, moves so far): the
    log only ever grows, so that triple names one state exactly, and every seat polling
    the room once a second would otherwise rebuild the same auction over and over."""
    humans = {pid: p.franchise for pid, p in room.players.items() if not p.is_cpu}
    mega = room.game == "mega"
    trades = bool(getattr(room, "trades", False))
    key = (room.code, room.seed, len(room.moves), tuple(sorted(humans.items())), mega, trades)
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        return hit
    result = _replay(room.seed, humans, room.moves, deck, mega, trades)
    _CACHE[key] = result
    if len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return result


def settle_shared(retained: dict[int, list[Card]], pools: dict[int, list[Card]], seed: int):
    """[A141] A legend two humans both claimed goes to the franchise with his BETTER
    SEASON -- the higher-rated of that franchise's own seasons of him, whichever season
    either side actually asked to keep. Ratified by the user in place of first-come, which
    rewarded whoever clicked fastest.

    Ties go to the higher-rated season actually claimed, then to a hash of the room's seed:
    a tie is rare (it needs two franchises with equally rated best seasons of one man) and
    must still be decided the same way on every replay.

    Returns ({team: the retentions it keeps, in its own order}, {team: [(card, winner)]}).
    A loser keeps the rest in the order they chose them, so their slabs close up and the
    place they lost becomes a Right to Match card."""
    claims: dict[str, list[tuple[int, Card]]] = {}
    for t, cards in retained.items():
        for c in cards:
            claims.setdefault(c.person_id, []).append((t, c))
    lost: dict[int, list[tuple[Card, int]]] = {}
    for pid, contenders in claims.items():
        if len(contenders) < 2:
            continue

        def best(team_index):
            return max(((x.display or 0), x.rating)
                       for x in pools[team_index] if x.person_id == pid)

        winner = max(contenders, key=lambda tc: (best(tc[0]), ((tc[1].display or 0),
                                                               tc[1].rating),
                                                 au._mix(seed, au._pid(pid), tc[0], 7)))[0]
        for t, c in contenders:
            if t != winner:
                lost.setdefault(t, []).append((c, winner))
    settled = {t: [c for c in cards if not any(c is lc for lc, _ in lost.get(t, []))]
               for t, cards in retained.items()}
    return settled, lost


@dataclass
class Offer:
    """[A174] One offer: team `a` gives `give` to team `b` for `get`. Open until answered,
    withdrawn, or VOID -- an offer goes void the moment either side completes some other
    trade, because the squads it was made against no longer exist, and accepting it would
    apply a swap neither person saw."""
    n: int
    a: int
    b: int
    give: Card
    get: Card
    # `state`, not `status`: tests/test_room_schema.py reads every status assignment in
    # this file as a ROOM status, which is the guard it exists to be.
    state: str = "open"             # open | accepted | declined | withdrawn | void


@dataclass
class Trade:
    """[A174] A completed trade, in the order trades completed."""
    n: int
    offer: int
    a: int
    b: int
    a_gave: Card
    b_gave: Card
    a_paid: int                     # what `a` had paid for the player it gave
    b_paid: int


def _person(squad: list[Card], person_id) -> Card:
    card = next((c for c in squad if c.person_id == person_id), None)
    if card is None:
        raise AuctionRoomError("that player is not in the squad")
    return card


def _window_over(state: RoomAuctionReplay, humans: set[int]) -> bool:
    """[A174] The window closes itself once everyone has said they are done, or once no two
    people can still trade with each other -- decided from the log alone, like a lot that
    every human is done with, so neither needs a recorded move."""
    if humans <= state.trade_ready:
        return True
    return sum(state.trades_used(h) < TRADES_PER_TEAM for h in humans) < 2


class _Pause(Exception):
    def __init__(self, kind: str, team: int, other: int, price: int):
        super().__init__(kind)
        self.kind, self.team, self.other, self.price = kind, team, other, price


class _RoomRtm(au.Human):
    """The humans' side of a Right to Match, read from the room's log. Handed to the
    engine's own `_right_to_match`, so the rule is the single-player one exactly -- only
    where each human answer comes from differs."""

    def __init__(self, moves, pos, team_of, flags):
        self.moves, self.pos, self.pid_of = moves, pos, {i: p for p, i in team_of.items()}
        self.flags = flags

    def _take(self, kind, team, other, price):
        if self.pos >= len(self.moves):
            raise _Pause(kind, team.index, other.index, price)
        mv = self.moves[self.pos]
        if mv.get("k") != kind or mv.get("seat") != self.pid_of[team.index]:
            raise AuctionRoomError(f"move {self.pos}: expected {team.short}'s {kind}")
        self.pos += 1
        return mv

    def rtm_use(self, auction, team, lot, price, winner):
        # Someone who skipped to the end is not held up by questions: exactly single
        # player's `pall`, which declines every card without asking.
        if "all" in self.flags.get(team.index, set()):
            return False
        return bool(self._take("rtm_use", team, winner, price)["use"])

    def rtm_raise(self, auction, team, lot, price, holder):
        return int(self._take("rtm_raise", team, holder, price)["price"])

    def rtm_match(self, auction, team, lot, price, winner):
        return bool(self._take("rtm_match", team, winner, price)["yes"])


_RESERVE: dict[int, list[Card]] = {}


def _reserve(deck: Deck) -> list[Card]:
    """Every card in the deck, built once per deck rather than once per replay."""
    key = id(deck)
    if key not in _RESERVE:
        _RESERVE.clear()
        _RESERVE[key] = au.reserve_of(deck)
    return _RESERVE[key]


def _replay(seed: int, humans: dict[str, str], moves: list[dict], deck: Deck,
            mega: bool = False, trades: bool = False) -> RoomAuctionReplay:
    teams = au.make_teams(seed, humans=frozenset(humans.values()))
    by_short = {t.short: t.index for t in teams}
    team_of = {pid: by_short[short] for pid, short in humans.items()}
    human_idx = set(team_of.values())
    pos = 0

    kept: set[str] = set()
    lost: dict = {}
    if mega:
        # [A140, A141] Retentions, everybody at once and SEALED: nobody sees anyone
        # else's picks, so speed decides nothing. Once every human is in, a legend two of
        # them claimed goes to the franchise with his better season (`settle_shared`);
        # the computer teams then keep theirs from what is left.
        pools = {t.index: au.retention_pool(deck, t.franchise) for t in teams}
        retained: dict[int, list[Card]] = {}
        while len(retained) < len(human_idx):
            if pos >= len(moves):
                return RoomAuctionReplay(au.Auction(seed, [], teams), team_of, "retain",
                                         pools=pools, retained=dict(retained))
            mv = moves[pos]
            pos += 1
            h = team_of.get(mv.get("seat"))
            if mv.get("k") != "retain" or h is None or h in retained:
                raise AuctionRoomError(f"move {pos - 1}: expected a retention")
            if any(not 0 <= i < len(pools[h]) for i in mv["picks"]):
                raise AuctionRoomError("no such player to retain")
            chosen = [pools[h][i] for i in mv["picks"]]
            errors = au.retention_errors(teams[h], chosen)
            if errors:
                raise AuctionRoomError("; ".join(errors))
            retained[h] = chosen
        settled, lost = settle_shared(retained, pools, seed)
        for h, chosen in settled.items():
            au.retain(teams[h], chosen)
            kept |= {c.person_id for c in chosen}
        for t in teams:
            if not t.human:
                chosen = au.cpu_retain(t, [c for c in pools[t.index] if c.person_id not in kept],
                                       kept)
                au.retain(t, chosen)
                kept |= {c.person_id for c in chosen}

    lots = au.build_catalogue(deck, seed, frozenset(kept))
    listed = {lot.card.person_id for lot in lots} | kept
    register = [c for c in au.draw_seasons(deck, seed) if c.person_id not in listed]
    auction = au.Auction(seed, lots, teams, mega=mega, register=register,
                         reserve=_reserve(deck))
    if mega:
        auction.retention_lost = lost
    flags: dict[int, set] = {i: set() for i in human_idx}

    for round_no, round_name in enumerate(au.ROUNDS):
        for lot in (auction.lots if round_no == 0 else auction.unsold):
            if all(t.open_places == 0 for t in teams):
                break
            state = RoomAuctionReplay(auction, team_of, "bid", lot=lot, round_no=round_no,
                                      flags=flags)
            auto = {t.index: au.cpu_ceiling(t, lot, seed, round_no, mega)
                    for t in teams if not t.human}

            def reply():
                state.bids = au.continue_bidding(lot, state.bids, {**auto, **state.proxies},
                                                 seed, round_no)

            reply()                           # the computer teams open the bidding
            closed = False
            while not all(state.human_done(h, flags) for h in human_idx):
                if pos >= len(moves):
                    return state              # waiting on the people
                mv = moves[pos]
                if mv.get("lot") != lot.index or mv.get("r") != round_no:
                    if _earlier(mv, lot.index, round_no):
                        raise AuctionRoomError(f"move {pos} is for another lot")
                    # [A170] A log written before a bid could undo a pass: back then this
                    # lot closed the moment the bidder led, with nothing recorded, and the
                    # next move is for whatever came after it. `submit` only ever records
                    # a move for the lot on screen, so a log written since never gets here.
                    break
                pos += 1
                kind = mv["k"]
                if kind == "close":
                    closed = True
                    break
                if kind in ("skip_set", "skip_all"):
                    # [A171] The host's skip is the ROOM's: every human passes this lot and
                    # the rest of the set (or everything), so nobody is left half-in a set
                    # the others have abandoned -- the shape A170 had to untangle.
                    mark = (lot.set_code, round_no) if kind == "skip_set" else "all"
                    for x in human_idx:
                        state.passed.add(x)
                        flags[x].add(mark)
                    reply()
                    continue
                h = team_of[mv["seat"]]
                team = teams[h]
                if kind in ("bid", "limit"):
                    # [A170] Bidding puts you back in. A pass, a skipped set or a skip to
                    # the end used to stand while the same player kept bidding, so the
                    # moment the other side led the lot was hammered with no chance to
                    # answer -- room KF3LLG lost lots 8, 14, 16 and 29 that way.
                    state.passed.discard(h)
                    flags[h].discard((lot.set_code, round_no))
                    flags[h].discard("all")
                if kind == "bid":
                    if not state.can_bid(team) or mv["p"] != state.next_price:
                        raise AuctionRoomError(f"move {pos - 1}: not a valid bid")
                    state.bids.append(au.Bid(h, mv["p"]))
                elif kind == "limit":
                    state.proxies[h] = min(mv["max"], team.max_bid())
                else:
                    state.passed.add(h)
                    if kind == "pass_set":
                        flags[h].add((lot.set_code, round_no))
                    elif kind == "pass_all":
                        flags[h].add("all")
                reply()
            if not closed and pos < len(moves) and moves[pos].get("k") == "close" \
                    and moves[pos].get("lot") == lot.index and moves[pos].get("r") == round_no:
                pos += 1                      # a clock close that raced the last human
            if not (mega and state.bids):
                _commit(auction, lot, round_name, state.bids)
                continue
            policy = _RoomRtm(moves, pos, team_of, flags)
            auction.current_bids = state.bids
            try:
                buyer, price, event = au._right_to_match(
                    auction, lot, state.bids[-1].team, state.bids[-1].price,
                    {**auto, **state.proxies}, policy)
            except _Pause as p:
                state.phase = p.kind
                state.rtm_team, state.rtm_other, state.rtm_price = p.team, p.other, p.price
                return state
            pos = policy.pos
            team = teams[buyer]
            team.squad.append(lot.card)
            team.paid.append(price)
            team.purse -= price
            auction.sales.append(au.Sale(lot, round_name, buyer, price, list(state.bids), event))

    # The fill round: every team still short takes a player at the minimum price,
    # fewest-players first, from `game.auction.fill_options` -- the one rule both the
    # single-player auction and a room use, so the two cannot drift [A150].
    stranded: set[int] = set()
    while True:
        short = [t for t in teams if t.open_places > 0 and t.index not in stranded]
        if not short:
            break
        team = min(short, key=lambda t: (len(t.squad), t.index))
        options = au.fill_options(auction, team)
        if not options:
            stranded.add(team.index)
            auction.stranded.append(team.index)
            continue
        if team.human:
            if pos >= len(moves):
                return RoomAuctionReplay(auction, team_of, "fill", fill_team=team.index,
                                         fill_options=options)
            mv = moves[pos]
            if mv.get("k") != "fill" or team_of.get(mv.get("seat")) != team.index:
                raise AuctionRoomError(f"move {pos}: expected {team.short}'s fill choice")
            pos += 1
            if not 0 <= mv["i"] < len(options):
                raise AuctionRoomError("no such fill option")
            card = options[mv["i"]]
        else:
            card = max(options, key=lambda c: au.need(team, c) * au.value_curve(c.display, mega))
        team.squad.append(card)
        team.paid.append(au.MIN_PRICE)
        team.purse -= au.MIN_PRICE
        auction.fills += 1
        lot = next((x for x in auction.unsold if x.card is card),
                   au.Lot(-1, card, "REG", au.MIN_PRICE))
        auction.sales.append(au.Sale(lot, "fill", team.index, au.MIN_PRICE,
                                     [au.Bid(team.index, au.MIN_PRICE)]))

    # [A150] The last line of defence. `fill_options` widens to the whole deck, so a team
    # left without a legal twelve should not happen -- but if one ever is, the room ENDS
    # with a reason rather than raising on every poll (the twelve step and `room_sides`
    # both need a legal twelve for every team, human or computer, and would crash).
    # `twelve_feasible(squad, 0)` is the exact legality test (no wildcard places left to
    # assume anything about), and ~500x cheaper than building the best twelve itself --
    # measured 0.03 ms a team against 16 ms, on every uncached replay of this phase.
    unfieldable = [t for t in teams if not au.twelve_feasible(t.squad, 0)]
    if unfieldable:
        return RoomAuctionReplay(auction, team_of, "failed", failed_team=unfieldable[0].index)

    # [A174] The trade window, between the people only, if the room has one.
    window = RoomAuctionReplay(auction, team_of, "trade")
    if trades and len(human_idx) >= 2:
        pos = _trade_window(window, moves, pos, human_idx, team_of)
        if pos is None:
            return window

    # Every human picks a twelve, in any order.
    result = RoomAuctionReplay(auction, team_of, "twelve", offers=window.offers,
                               trades=window.trades, trade_ready=window.trade_ready)
    while pos < len(moves):
        mv = moves[pos]
        pos += 1
        if mv.get("k") != "twelve" or mv.get("seat") not in team_of:
            raise AuctionRoomError(f"move {pos - 1}: expected a twelve")
        squad = teams[team_of[mv["seat"]]].squad
        result.twelves[mv["seat"]] = _twelve_from(squad, mv["order"], mv["impact"])
    if len(result.twelves) == len(team_of):
        result.phase = "complete"
    return result


def _trade_window(state: RoomAuctionReplay, moves: list[dict], pos: int,
                  humans: set[int], team_of: dict[str, int]) -> int | None:
    """Replay the trade window into `state`. Returns where the twelves' moves begin, or
    None while the window is still open."""
    teams = state.auction.teams
    while not _window_over(state, humans):
        if pos >= len(moves):
            return None
        mv = moves[pos]
        kind = mv.get("k")
        if kind not in ("offer", "answer", "withdraw", "ready", "trade_end", "trade_close"):
            raise AuctionRoomError(f"move {pos}: expected a trade-window move")
        pos += 1
        if kind in ("trade_end", "trade_close"):
            break
        h = team_of.get(mv.get("seat"))
        if h is None:
            raise AuctionRoomError(f"move {pos - 1}: not a seat in this room")
        if kind == "ready":
            if mv.get("on", True):
                state.trade_ready.add(h)
            else:
                state.trade_ready.discard(h)
            continue
        if kind == "offer":
            b = team_of.get(mv.get("to"))
            if b is None:
                raise AuctionRoomError("trades are between the people in the room")
            give = _person(teams[h].squad, mv["give"])
            get = _person(teams[b].squad, mv["get"])
            errors = state.offer_errors(h, b, give, get)
            if errors:
                raise AuctionRoomError("; ".join(errors))
            state.offers.append(Offer(len(state.offers), h, b, give, get))
            continue
        n = mv.get("offer")
        if not isinstance(n, int) or not 0 <= n < len(state.offers):
            raise AuctionRoomError("no such offer")
        offer = state.offers[n]
        if offer.state != "open":
            raise AuctionRoomError("that offer is no longer on the table")
        if kind == "withdraw":
            if offer.a != h:
                raise AuctionRoomError("only the side that made an offer can withdraw it")
            offer.state = "withdrawn"
            continue
        if offer.b != h:                               # an answer
            raise AuctionRoomError("that offer was not made to you")
        if not mv.get("yes"):
            offer.state = "declined"
            continue
        a, b = teams[offer.a], teams[offer.b]
        a_paid, b_paid = au.apply_trade(a, b, offer.give, offer.get)
        offer.state = "accepted"
        state.trades.append(Trade(len(state.trades), offer.n, offer.a, offer.b,
                                  offer.give, offer.get, a_paid, b_paid))
        # Every other open offer either side was party to was made against squads that no
        # longer exist.
        for o in state.offers:
            if o.state == "open" and {o.a, o.b} & {offer.a, offer.b}:
                o.state = "void"
    return pos


def _earlier(mv: dict, lot_index: int, round_no: int) -> bool:
    """A bidding move for a lot this replay has already passed: a corrupt log, not an
    old one. Moves with no lot (Right to Match, fill, twelve) are never earlier."""
    if "lot" not in mv:
        return False
    return (mv.get("r", 0), mv["lot"]) < (round_no, lot_index)


def _commit(auction: au.Auction, lot: au.Lot, round_name: str, bids: list[au.Bid]) -> None:
    if not bids:
        auction.sales.append(au.Sale(lot, round_name, None, 0, bids))
        return
    team = auction.teams[bids[-1].team]
    team.squad.append(lot.card)
    team.paid.append(bids[-1].price)
    team.purse -= bids[-1].price
    auction.sales.append(au.Sale(lot, round_name, team.index, bids[-1].price, list(bids)))


def _twelve_from(squad: list[Card], order: list[int], impact: int):
    idx = list(order) + [impact]
    if len(idx) != 12 or len(set(idx)) != 12 or any(not 0 <= i < len(squad) for i in idx):
        raise AuctionRoomError("the twelve must be twelve different players from your squad")
    xi, imp = [squad[i] for i in order], squad[impact]
    errors = order_errors(xi, imp, squad)
    if errors:
        raise AuctionRoomError("; ".join(errors))
    return xi, imp


def suggestion(auction: au.Auction, team_index: int) -> tuple[list[int], int] | None:
    team = auction.teams[team_index]
    chosen = au.best_twelve(team.squad)
    arranged = au.arrange(chosen) if chosen else None
    if arranged is None:
        return None
    pos = {id(c): i for i, c in enumerate(team.squad)}
    return [pos[id(c)] for c in arranged[0]], pos[id(arranged[1])]


# --- moves ---------------------------------------------------------------------------------


def _stage_seconds(r: RoomAuctionReplay) -> int:
    if r.phase.startswith("rtm"):
        return RTM_SECONDS
    return {"bid": LOT_SECONDS, "fill": FILL_SECONDS, "twelve": TWELVE_SECONDS,
            "retain": RETAIN_SECONDS, "trade": TRADE_SECONDS}.get(r.phase, 0)


def record(room, deck: Deck, move: dict, now: float | None = None) -> RoomAuctionReplay:
    """Append one move, then move the clock: a new stage starts a fresh clock, and a bid on
    the same lot tops the clock up to BID_WINDOW seconds if it had less -- never adds to it.
    [A158] Adding five a bid made a rapid war run the clock out to minutes; a floor gives
    every bid the same time to be answered however fast the bids before it came.
    `room.turn_started_at` holds the current DEADLINE in an auction room (epoch seconds),
    not a start time."""
    now = time.time() if now is None else now
    before = replay(room, deck)
    room.moves = room.moves + [move]
    after = replay(room, deck)                 # raises if invalid; `submit` restores the log
    if after.phase == "failed":
        _fail(room, after)
    elif after.phase == "complete":
        room.status = "complete"
    elif after.stage() != before.stage():
        room.turn_started_at = now + _stage_seconds(after)
    elif move["k"] in ("bid", "limit"):
        room.turn_started_at = max(room.turn_started_at, now + BID_WINDOW)
    return after


def _fail(room, r: RoomAuctionReplay) -> None:
    """[A150] End the room: a team that cannot field a legal twelve cannot play a season,
    and every step after this one needs its twelve. Reported to every seat, never a crash."""
    team = r.auction.teams[r.failed_team]
    room.status = "failed"
    room.failure_reason = (f"{team.franchise} could not field a legal twelve from its squad, "
                           f"so the season cannot be played")


def _seat(r: RoomAuctionReplay, player_id: str) -> au.Team:
    if player_id not in r.team_of:
        raise AuctionRoomError("you are not bidding in this room")
    return r.auction.teams[r.team_of[player_id]]


def _check_lot(r: RoomAuctionReplay, lot: int | None, round_name: str | None) -> None:
    """[A146] A bid, limit or pass is ABOUT one lot. Without saying which, a click that
    arrives after its lot closed -- by the clock, or because the last other person passed
    -- landed on the NEXT one: a pass on a player never seen, a limit on the wrong man, and
    even a bid, whenever the next lot happened to open at the same price. The page sends
    the lot it was showing; an old client that sends nothing is checked as before."""
    if r.phase != "bid":
        raise AuctionRoomError("that lot has closed")
    if lot is not None and (lot != r.lot.index
                            or (round_name is not None and round_name != au.ROUNDS[r.round_no])):
        raise AuctionRoomError("that lot has closed")


def bid(room, deck: Deck, player_id: str, price: int, lot: int | None = None,
        round_name: str | None = None) -> RoomAuctionReplay:
    r = replay(room, deck)
    team = _seat(r, player_id)
    _check_lot(r, lot, round_name)
    if price != r.next_price:
        # Your own bid already in (a retry after a lost response) is not an error.
        if r.bids and r.bids[-1].team == team.index and r.bids[-1].price == price:
            return r
        raise AuctionRoomError("outbid -- the price has moved")
    if not r.can_bid(team):
        raise AuctionRoomError("you cannot bid on this lot")
    return record(room, deck, {"k": "bid", "seat": player_id, "lot": r.lot.index,
                               "r": r.round_no, "p": price})


def limit(room, deck: Deck, player_id: str, maximum: int, lot: int | None = None,
          round_name: str | None = None) -> RoomAuctionReplay:
    r = replay(room, deck)
    team = _seat(r, player_id)
    _check_lot(r, lot, round_name)
    if r.proxies.get(team.index) == min(maximum, team.max_bid()):
        return r                              # already set: a retried request, not a move
    if not r.can_bid(team) or maximum < r.next_price:
        raise AuctionRoomError("that limit is below the next bid")
    if team.index in r.proxies and maximum < r.proxies[team.index]:
        raise AuctionRoomError("a limit once set cannot be lowered")
    return record(room, deck, {"k": "limit", "seat": player_id, "lot": r.lot.index,
                               "r": r.round_no, "max": maximum})


def pass_lot(room, deck: Deck, player_id: str, scope: str, lot: int | None = None,
             round_name: str | None = None) -> RoomAuctionReplay:
    r = replay(room, deck)
    team = _seat(r, player_id)
    _check_lot(r, lot, round_name)
    if scope == "lot" and team.index in r.passed:
        return r                              # already passed: a retried request
    if scope != "lot" and player_id != room.host_id:
        # [A171] Skipping a set or skipping to the end moves the whole room on, so it is
        # the host's call alone, like advancing a round.
        raise AuctionRoomError("only the host can skip ahead")
    kind = {"lot": "pass", "set": "skip_set", "all": "skip_all"}[scope]
    return record(room, deck, {"k": kind, "seat": player_id, "lot": r.lot.index,
                               "r": r.round_no})


def fill(room, deck: Deck, player_id: str, index: int) -> RoomAuctionReplay:
    r = replay(room, deck)
    team = _seat(r, player_id)
    if r.phase != "fill" or r.fill_team != team.index:
        raise AuctionRoomError("it is not your fill-round choice")
    return record(room, deck, {"k": "fill", "seat": player_id, "i": index})


def twelve(room, deck: Deck, player_id: str, order: list[int], impact: int) -> RoomAuctionReplay:
    r = replay(room, deck)
    _seat(r, player_id)
    if r.phase != "twelve":
        raise AuctionRoomError("the auction is not over")
    if player_id in r.twelves:
        raise AuctionRoomError("your twelve is already in")
    return record(room, deck, {"k": "twelve", "seat": player_id, "order": list(order),
                               "impact": impact})


def retain(room, deck: Deck, player_id: str, picks: list[int]) -> RoomAuctionReplay:
    r = replay(room, deck)
    team = _seat(r, player_id)
    if r.phase != "retain":
        raise AuctionRoomError("retentions are chosen before the auction opens")
    if team.index in r.retained:
        raise AuctionRoomError("your retentions are already in")
    pool = r.pools[team.index]
    if any(not 0 <= i < len(pool) for i in picks) or len(set(picks)) != len(picks):
        raise AuctionRoomError("no such player to retain")
    chosen = [pool[i] for i in picks]
    # No clash check here any more [A141]: retentions are sealed, and a legend two people
    # both claim is settled by his better season once everyone is in.
    errors = au.retention_errors(team, chosen)
    if errors:
        raise AuctionRoomError("; ".join(errors))
    return record(room, deck, {"k": "retain", "seat": player_id, "picks": list(picks)})


def rtm(room, deck: Deck, player_id: str, yes: bool, price: int | None = None
        ) -> RoomAuctionReplay:
    """Answer whichever Right to Match question is being asked of this seat."""
    r = replay(room, deck)
    team = _seat(r, player_id)
    if not r.phase.startswith("rtm") or r.rtm_team != team.index:
        raise AuctionRoomError("no Right to Match is waiting on you")
    if r.phase == "rtm_use":
        move = {"k": "rtm_use", "seat": player_id, "use": bool(yes)}
    elif r.phase == "rtm_match":
        move = {"k": "rtm_match", "seat": player_id, "yes": bool(yes)}
    else:
        raised = r.rtm_price if (not yes or price is None) else price
        if raised < r.rtm_price:
            raise AuctionRoomError("a raise cannot be lower than the hammer price")
        move = {"k": "rtm_raise", "seat": player_id, "price": raised}
    return record(room, deck, move)


def _trade_seat(room, deck: Deck, player_id: str) -> tuple[RoomAuctionReplay, int]:
    r = replay(room, deck)
    team = _seat(r, player_id)
    if r.phase != "trade":
        raise AuctionRoomError("the trade window is closed")
    return r, team.index


def offer(room, deck: Deck, player_id: str, to: str, give: str, get: str) -> RoomAuctionReplay:
    """[A174] Offer `give` (one of yours, by person id) for `get` (one of theirs)."""
    r, h = _trade_seat(room, deck, player_id)
    b = r.team_of.get(to)
    if b is None:
        raise AuctionRoomError("trades are between the people in the room, "
                               "not the computer franchises")
    for o in r.offers:
        if (o.state == "open" and o.a == h and o.b == b and o.give.person_id == give
                and o.get.person_id == get):
            return r                          # already on the table: a retried request
    if sum(o.state == "open" and o.a == h for o in r.offers) >= MAX_OPEN_OFFERS:
        raise AuctionRoomError(f"you have {MAX_OPEN_OFFERS} offers open already; "
                               f"withdraw one first")
    teams = r.auction.teams
    errors = r.offer_errors(h, b, _person(teams[h].squad, give), _person(teams[b].squad, get))
    if errors:
        raise AuctionRoomError("; ".join(errors))
    return record(room, deck, {"k": "offer", "seat": player_id, "to": to, "give": give,
                               "get": get})


def answer(room, deck: Deck, player_id: str, n: int, yes: bool) -> RoomAuctionReplay:
    r, h = _trade_seat(room, deck, player_id)
    if not 0 <= n < len(r.offers):
        raise AuctionRoomError("no such offer")
    o = r.offers[n]
    if o.b != h:
        raise AuctionRoomError("that offer was not made to you")
    if o.state == ("accepted" if yes else "declined"):
        return r                              # a retried request
    if o.state != "open":
        raise AuctionRoomError("that offer is no longer on the table")
    return record(room, deck, {"k": "answer", "seat": player_id, "offer": n, "yes": bool(yes)})


def withdraw(room, deck: Deck, player_id: str, n: int) -> RoomAuctionReplay:
    r, h = _trade_seat(room, deck, player_id)
    if not 0 <= n < len(r.offers) or r.offers[n].a != h:
        raise AuctionRoomError("that is not your offer")
    if r.offers[n].state == "withdrawn":
        return r
    if r.offers[n].state != "open":
        raise AuctionRoomError("that offer is no longer on the table")
    return record(room, deck, {"k": "withdraw", "seat": player_id, "offer": n})


def trade_ready(room, deck: Deck, player_id: str, on: bool) -> RoomAuctionReplay:
    r, h = _trade_seat(room, deck, player_id)
    if (h in r.trade_ready) == bool(on):
        return r
    return record(room, deck, {"k": "ready", "seat": player_id, "on": bool(on)})


def end_trades(room, deck: Deck, player_id: str) -> RoomAuctionReplay:
    """The host closes the window for everyone, like a skip [A171]."""
    r, _h = _trade_seat(room, deck, player_id)
    if player_id != room.host_id:
        raise AuctionRoomError("only the host can close the trade window")
    return record(room, deck, {"k": "trade_end", "seat": player_id})


def trade_check(r: RoomAuctionReplay, player_id: str, to: str, give: str, get: str) -> dict:
    """[A174] What an offer would do, before it is made: the reasons it would be refused,
    and the change to both sides' best twelve, per player. Read-only."""
    if r.phase != "trade":
        return {"errors": ["the trade window is closed"]}
    h, b = r.team_of.get(player_id), r.team_of.get(to)
    if h is None or b is None:
        return {"errors": ["trades are between the people in the room"]}
    teams = r.auction.teams
    try:
        g, t = _person(teams[h].squad, give), _person(teams[b].squad, get)
    except AuctionRoomError as exc:
        return {"errors": [str(exc)]}
    errors = r.offer_errors(h, b, g, t)
    if errors:
        return {"errors": errors}
    return {"errors": [], **swap_deltas(teams[h], teams[b], g, t)}


def swap_deltas(a: au.Team, b: au.Team, give: Card, get: Card) -> dict:
    """Both sides' best-twelve change, in rating points per player, if `a` gives `give`."""
    before_a, before_b = au.twelve_value(a.squad), au.twelve_value(b.squad)
    after_a = au.twelve_value([c for c in a.squad if c is not give] + [get])
    after_b = au.twelve_value([c for c in b.squad if c is not get] + [give])
    return {"you": round(after_a - before_a, 2), "them": round(after_b - before_b, 2)}


def retention_suggestion(r: RoomAuctionReplay, team_index: int) -> list[int]:
    """What a computer team would keep for this franchise: the timeout's answer."""
    team = r.auction.teams[team_index]
    pool = r.pools[team_index]
    chosen = au.cpu_retain(team, pool, set())      # sealed: nobody else's picks are known
    return [pool.index(c) for c in chosen]


def resolve(room, deck: Deck, now: float | None = None) -> bool:
    """Catch the room up with the clock: close a lot whose time ran out, make an absent
    player's fill choice (the best option), submit an absent player's suggested twelve.
    One step per call that has work to do, looped here until nothing is overdue. Returns
    whether anything changed."""
    now = time.time() if now is None else now
    changed = False
    # [A146] Closed only once the grace is also gone, so a bid made in the lot's last
    # second still lands on it rather than finding the next lot open.
    while room.status == "auctioning" and now > room.turn_started_at + CLOCK_GRACE_S:
        r = replay(room, deck)
        if r.phase == "failed":
            _fail(room, r)
            changed = True
            break
        if r.phase == "bid":
            record(room, deck, {"k": "close", "lot": r.lot.index, "r": r.round_no}, now)
        elif r.phase == "fill":
            record(room, deck, {"k": "fill", "seat": r.pid_of[r.fill_team], "i": 0}, now)
        elif r.phase == "retain":
            for pid in r.waiting_on():
                rr = replay(room, deck)
                record(room, deck, {"k": "retain", "seat": pid,
                                    "picks": retention_suggestion(rr, rr.team_of[pid])}, now)
        elif r.phase == "rtm_use":
            record(room, deck, {"k": "rtm_use", "seat": r.pid_of[r.rtm_team], "use": False}, now)
        elif r.phase == "rtm_raise":
            record(room, deck, {"k": "rtm_raise", "seat": r.pid_of[r.rtm_team],
                                "price": r.rtm_price}, now)
        elif r.phase == "rtm_match":
            record(room, deck, {"k": "rtm_match", "seat": r.pid_of[r.rtm_team], "yes": False},
                   now)
        elif r.phase == "trade":
            record(room, deck, {"k": "trade_close"}, now)
        elif r.phase == "twelve":
            for pid in r.waiting_on():
                order, impact = suggestion(r.auction, r.team_of[pid])
                record(room, deck, {"k": "twelve", "seat": pid, "order": order,
                                    "impact": impact}, now)
        else:
            break
        changed = True
    return changed


def submit(conn, code: str, deck: Deck, action, player_id: str, *args):
    """One human move, under the room's row lock: catch the room up with the clock first
    (so a bid lands on the lot that is REALLY open, not one whose time already ran out),
    then apply the move and save.

    A move the room refuses raises `rooms.StaleMove` carrying the caught-up room [A146],
    and that catch-up is SAVED first if it changed anything: a late bid is exactly the
    moment the lot's close most needs recording, and the page is handed the room as it
    really is so it can redraw at once rather than on its next poll."""
    from web import rooms
    room = rooms._load_room(conn, code)
    if not is_auction(room):
        raise AuctionRoomError("this is not an auction room")
    before = rooms._mutable_state(room)
    resolve(room, deck)
    # `record` appends to the log BEFORE validating it, so a refused move leaves itself on
    # the object; put the caught-up state back before anything is saved.
    caught_up = (room.moves, room.status, room.turn_started_at)
    try:
        if room.status != "auctioning":
            raise AuctionRoomError("the auction is over")
        action(room, deck, player_id, *args)
    except AuctionRoomError as exc:
        room.moves, room.status, room.turn_started_at = caught_up
        if rooms._mutable_state(room) != before:
            rooms._save_room(conn, room)
        raise StaleAuctionMove(str(exc), room) from exc
    rooms._save_room(conn, room)
    return room


def start(room, deck: Deck, now: float | None = None) -> None:
    """The auction opens: the clock starts on whatever the room first waits for."""
    now = time.time() if now is None else now
    r = replay(room, deck)
    room.status = "complete" if r.phase == "complete" else "auctioning"
    room.turn_started_at = now + _stage_seconds(r)
    if r.phase == "failed":
        _fail(room, r)


def room_sides(room, deck: Deck):
    """(player_id, RoomPlayer, order, impact) for every seat -- what the match phase needs,
    exactly the shape `web.rooms.room_sides` returns for a draft room."""
    from web.rooms import RoomPlayer
    r = replay(room, deck)
    out = []
    for pid, p in room.players.items():
        team = next(t for t in r.auction.teams if t.short == p.franchise)
        if pid in r.twelves:
            order, impact = r.twelves[pid]
            name = f"{p.name} ({team.short})"
        else:
            order, impact = r.auction.twelve(team)
            name = team.franchise
        out.append((pid, RoomPlayer(p.player_id, name, p.is_cpu, p.franchise),
                    list(order), impact))
    return out
