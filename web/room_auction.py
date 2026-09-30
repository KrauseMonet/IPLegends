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
from web.rooms import CLOCK_GRACE_S, StaleMove

LOT_SECONDS = 15         # ratified by the user: fifteen seconds a lot...
BID_EXTEND = 5           # ...and five more for every bid
FILL_SECONDS = 20        # a fill-round choice
TWELVE_SECONDS = 90      # choosing a twelve from eighteen
RETAIN_SECONDS = 90      # [A140] choosing retentions, everybody at once
RTM_SECONDS = 15         # [A140] each Right to Match decision: play, raise, match

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
    phase: str      # retain | bid | rtm_use | rtm_raise | rtm_match | fill | twelve | complete | failed
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

    def waiting_on(self) -> list[str]:
        """The humans the room is waiting for right now."""
        if self.phase == "fill":
            return [self.pid_of[self.fill_team]] if self.fill_team in self.pid_of else []
        if self.phase == "twelve":
            return [pid for pid in self.team_of if pid not in self.twelves]
        if self.phase == "retain":
            return [pid for pid, i in self.team_of.items() if i not in self.retained]
        if self.phase.startswith("rtm"):
            return [self.pid_of[self.rtm_team]]
        return []

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
    key = (room.code, room.seed, len(room.moves), tuple(sorted(humans.items())), mega)
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        return hit
    result = _replay(room.seed, humans, room.moves, deck, mega)
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
            mega: bool = False) -> RoomAuctionReplay:
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
            state = RoomAuctionReplay(auction, team_of, "bid", lot=lot, round_no=round_no)
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
    if r.phase.startswith("rtm"):
        return RTM_SECONDS
    return {"bid": LOT_SECONDS, "fill": FILL_SECONDS, "twelve": TWELVE_SECONDS,
            "retain": RETAIN_SECONDS}.get(r.phase, 0)


def record(room, deck: Deck, move: dict, now: float | None = None) -> RoomAuctionReplay:
    """Append one move, then move the clock: a new stage starts a fresh clock, and a bid on
    the same lot adds BID_EXTEND to it. `room.turn_started_at` holds the current DEADLINE
    in an auction room (epoch seconds), not a start time."""
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
        room.turn_started_at = max(room.turn_started_at, now) + BID_EXTEND
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
