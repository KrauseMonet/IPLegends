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
    {"k": "pass_set", "seat": pid, "lot": i, "r": round}            ...nor in the rest of this set
    {"k": "pass_all", "seat": pid, "lot": i, "r": round}            ...nor in anything left
    {"k": "close", "lot": i, "r": round}                            the lot's clock ran out
    {"k": "fill",  "seat": pid, "i": n}                             a fill-round choice
    {"k": "twelve", "seat": pid, "order": [...11], "impact": n}     the chosen twelve

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

LOT_SECONDS = 15         # ratified by the user: fifteen seconds a lot...
BID_EXTEND = 5           # ...and five more for every bid
FILL_SECONDS = 20        # a fill-round choice
TWELVE_SECONDS = 90      # choosing a twelve from eighteen


class AuctionRoomError(ValueError):
    """A move this room cannot accept -- refused with a 4xx, never a 500."""


@dataclass
class RoomAuctionReplay:
    auction: au.Auction
    team_of: dict[str, int]                 # human player_id -> team index
    phase: str                              # bid | fill | twelve | complete
    lot: au.Lot | None = None
    round_no: int = 0
    bids: list[au.Bid] = field(default_factory=list)
    proxies: dict[int, int] = field(default_factory=dict)
    passed: set[int] = field(default_factory=set)
    fill_team: int | None = None
    fill_options: list[Card] = field(default_factory=list)
    twelves: dict[str, tuple[list[Card], Card]] = field(default_factory=dict)

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

    def waiting_on(self) -> list[str]:
        """The humans the room is waiting for right now."""
        if self.phase == "fill":
            return [self.pid_of[self.fill_team]] if self.fill_team in self.pid_of else []
        if self.phase == "twelve":
            return [pid for pid in self.team_of if pid not in self.twelves]
        return []


# --- replay --------------------------------------------------------------------------------

_CACHE: "OrderedDict[tuple, RoomAuctionReplay]" = OrderedDict()
_CACHE_SIZE = 64


def replay(room, deck: Deck) -> RoomAuctionReplay:
    """The room's auction as of its move log. Cached per (room, seed, moves so far): the
    log only ever grows, so that triple names one state exactly, and every seat polling
    the room once a second would otherwise rebuild the same auction over and over."""
    humans = {pid: p.franchise for pid, p in room.players.items() if not p.is_cpu}
    key = (room.code, room.seed, len(room.moves), tuple(sorted(humans.items())))
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        return hit
    result = _replay(room.seed, humans, room.moves, deck)
    _CACHE[key] = result
    if len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return result


def _replay(seed: int, humans: dict[str, str], moves: list[dict], deck: Deck) -> RoomAuctionReplay:
    teams = au.make_teams(seed, humans=frozenset(humans.values()))
    by_short = {t.short: t.index for t in teams}
    team_of = {pid: by_short[short] for pid, short in humans.items()}
    lots = au.build_catalogue(deck, seed)
    listed = {lot.card.person_id for lot in lots}
    register = [c for c in au.draw_seasons(deck, seed) if c.person_id not in listed]
    auction = au.Auction(seed, lots, teams, register=register)
    human_idx = set(team_of.values())
    flags: dict[int, set] = {i: set() for i in human_idx}
    pos = 0

    for round_no, round_name in enumerate(au.ROUNDS):
        for lot in (auction.lots if round_no == 0 else auction.unsold):
            if all(t.open_places == 0 for t in teams):
                break
            state = RoomAuctionReplay(auction, team_of, "bid", lot=lot, round_no=round_no)
            auto = {t.index: au.cpu_ceiling(t, lot, seed, round_no)
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
                    raise AuctionRoomError(f"move {pos} is for another lot")
                pos += 1
                kind = mv["k"]
                if kind == "close":
                    closed = True
                    break
                h = team_of[mv["seat"]]
                team = teams[h]
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
            _commit(auction, lot, round_name, state.bids)

    # The fill round: every team still short takes a player at the minimum price,
    # fewest-players first, exactly as `game.auction._fill` does it.
    stranded: set[int] = set()
    while True:
        short = [t for t in teams if t.open_places > 0 and t.index not in stranded]
        if not short:
            break
        team = min(short, key=lambda t: (len(t.squad), t.index))
        taken = {c.person_id for t in teams for c in t.squad}
        pool = [lot.card for lot in auction.unsold] + auction.register
        options = sorted((c for c in pool if c.person_id not in taken
                          and team.may_buy(c) and team.purse >= au.MIN_PRICE),
                         key=lambda c: (-(c.display or 0), -c.rating, c.person_id))
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
            card = max(options, key=lambda c: au.need(team, c) * au.value_curve(c.display))
        team.squad.append(card)
        team.paid.append(au.MIN_PRICE)
        team.purse -= au.MIN_PRICE
        auction.fills += 1
        lot = next((x for x in auction.unsold if x.card is card),
                   au.Lot(-1, card, "REG", au.MIN_PRICE))
        auction.sales.append(au.Sale(lot, "fill", team.index, au.MIN_PRICE,
                                     [au.Bid(team.index, au.MIN_PRICE)]))

    # Every human picks a twelve, in any order.
    result = RoomAuctionReplay(auction, team_of, "twelve")
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
    return {"bid": LOT_SECONDS, "fill": FILL_SECONDS, "twelve": TWELVE_SECONDS}.get(r.phase, 0)


def record(room, deck: Deck, move: dict, now: float | None = None) -> RoomAuctionReplay:
    """Append one move, then move the clock: a new stage starts a fresh clock, and a bid on
    the same lot adds BID_EXTEND to it. `room.turn_started_at` holds the current DEADLINE
    in an auction room (epoch seconds), not a start time."""
    now = time.time() if now is None else now
    before = replay(room, deck)
    room.moves = room.moves + [move]
    after = replay(room, deck)                 # raises, leaving the log untouched, if invalid
    if after.phase == "complete":
        room.status = "complete"
    elif after.stage() != before.stage():
        room.turn_started_at = now + _stage_seconds(after)
    elif move["k"] in ("bid", "limit"):
        room.turn_started_at = max(room.turn_started_at, now) + BID_EXTEND
    return after


def _seat(r: RoomAuctionReplay, player_id: str) -> au.Team:
    if player_id not in r.team_of:
        raise AuctionRoomError("you are not bidding in this room")
    return r.auction.teams[r.team_of[player_id]]


def bid(room, deck: Deck, player_id: str, price: int) -> RoomAuctionReplay:
    r = replay(room, deck)
    team = _seat(r, player_id)
    if r.phase != "bid":
        raise AuctionRoomError("no lot is being bid on")
    if price != r.next_price:
        raise AuctionRoomError("outbid -- the price has moved")
    if not r.can_bid(team):
        raise AuctionRoomError("you cannot bid on this lot")
    return record(room, deck, {"k": "bid", "seat": player_id, "lot": r.lot.index,
                               "r": r.round_no, "p": price})


def limit(room, deck: Deck, player_id: str, maximum: int) -> RoomAuctionReplay:
    r = replay(room, deck)
    team = _seat(r, player_id)
    if r.phase != "bid":
        raise AuctionRoomError("no lot is being bid on")
    if not r.can_bid(team) or maximum < r.next_price:
        raise AuctionRoomError("that limit is below the next bid")
    if team.index in r.proxies and maximum < r.proxies[team.index]:
        raise AuctionRoomError("a limit once set cannot be lowered")
    return record(room, deck, {"k": "limit", "seat": player_id, "lot": r.lot.index,
                               "r": r.round_no, "max": maximum})


def pass_lot(room, deck: Deck, player_id: str, scope: str) -> RoomAuctionReplay:
    r = replay(room, deck)
    _seat(r, player_id)
    if r.phase != "bid":
        raise AuctionRoomError("no lot is being bid on")
    kind = {"lot": "pass", "set": "pass_set", "all": "pass_all"}[scope]
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


def resolve(room, deck: Deck, now: float | None = None) -> bool:
    """Catch the room up with the clock: close a lot whose time ran out, make an absent
    player's fill choice (the best option), submit an absent player's suggested twelve.
    One step per call that has work to do, looped here until nothing is overdue. Returns
    whether anything changed."""
    now = time.time() if now is None else now
    changed = False
    while room.status == "auctioning" and now > room.turn_started_at:
        r = replay(room, deck)
        if r.phase == "bid":
            record(room, deck, {"k": "close", "lot": r.lot.index, "r": r.round_no}, now)
        elif r.phase == "fill":
            record(room, deck, {"k": "fill", "seat": r.pid_of[r.fill_team], "i": 0}, now)
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
    then apply the move and save. A move the room refuses raises before anything is
    written; the clock catch-up it skipped is simply redone by the next request."""
    from web import rooms
    room = rooms._load_room(conn, code)
    if room.game != "auction":
        raise AuctionRoomError("this is not an auction room")
    resolve(room, deck)
    if room.status != "auctioning":
        raise AuctionRoomError("the auction is over")
    action(room, deck, player_id, *args)
    rooms._save_room(conn, room)
    return room


def start(room, deck: Deck, now: float | None = None) -> None:
    """The auction opens: the clock starts on whatever the room first waits for."""
    now = time.time() if now is None else now
    r = replay(room, deck)
    room.status = "complete" if r.phase == "complete" else "auctioning"
    room.turn_started_at = now + _stage_seconds(r)


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
