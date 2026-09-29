"""The auction: ten franchises, a ₹120 crore purse each, eighteen players bought, twelve
played. [A136]

Everything here is a pure function of (deck, seed, what the human decided). Nothing is
stored and nothing touches the database, so an auction replays exactly from a seed and a
list of numbers -- A62's shape, carried over from the draft.

Money is held in LAKH throughout (100 lakh = ₹1 crore) so every price is an int and no
rounding decision is ever made twice.

**One number per lot is the whole human decision, in either bidding style.** The computer
teams' willingness to pay is fixed BEFORE a lot opens (it depends on their squads and
purses, never on what the human does to this lot), and every tie-break inside a lot is a
pure hash of (seed, lot, price, leader). So a human clicking "bid" step by step and a
human who sets "I'll go to ₹X" up front produce the same bid log, up to the moment the
clicker stops -- and the clicker's last bid IS their X. `tests/test_auction.py` pins that
equivalence, because both UIs are built on it.

**The rule that stops a team getting stuck.** A bid is legal only if afterwards the team
can still (1) afford every empty place at the minimum price and (2) still form a legal
twelve out of its squad plus the best possible future buys. (2) is `twelve_feasible`, the
auction's version of `could_still_complete` -- but with eighteen bought and twelve played
the squad is no longer the twelve, so position MATCHING is back (A73 could delete it; this
cannot). It stays cheap because A76 gives every card one of only four batting bands.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable

from etl.feasibility import (
    BOWLERS_IN_TWELVE, IMPACT_SLOT, OVERSEAS_CAP, TWELVE_SIZE, XI_SIZE, Card, Deck,
)
from etl.franchise_map import canonical

# --- the rules --------------------------------------------------------------------------

PURSE = 12_000                  # ₹120 crore, in lakh. Ratified by the user; not tuned.
SQUAD_SIZE = 18                 # bought; TWELVE_SIZE of them play
SQUAD_OVERSEAS_CAP = 6          # the real rule is 8 in 25; 6 in 18 keeps the proportion
MIN_PRICE = 30                  # the lowest base price, and what the fill round charges

# Base price by card rating (A58's 70-99 face value). A declared game-design table, in the
# same category as REPUTATION -- the real auction's own tiers, mapped onto our scale.
BASE_PRICE_TIERS = (            # (minimum display, base price in lakh)
    (94, 200), (90, 150), (87, 125), (84, 100), (81, 75), (78, 50), (75, 40), (0, 30),
)

TEAMS = 10
FRANCHISES = (                  # the ten current franchises: (short, canonical name)
    ("CSK", "Chennai Super Kings"), ("MI", "Mumbai Indians"),
    ("RCB", "Royal Challengers Bengaluru"), ("KKR", "Kolkata Knight Riders"),
    ("RR", "Rajasthan Royals"), ("DC", "Delhi Capitals"),
    ("SRH", "Sunrisers Hyderabad"), ("PBKS", "Punjab Kings"),
    ("GT", "Gujarat Titans"), ("LSG", "Lucknow Super Giants"),
)

# Lots offered; 180 must sell. Split by nationality in the proportion squads can absorb:
# an eighteen holds at most six overseas players, so a third of what sells is overseas.
# Ranking one pool by rating alone gave a catalogue 60% overseas (the strongest seasons
# skew that way), every squad filled its six by mid-auction, and 95 overseas lots went
# unsold while two teams were left with nobody domestic to buy at all.
DOMESTIC_LOTS = 170
OVERSEAS_LOTS = 90
MARQUEE_SIZE = 12
SET_SIZE = 12

# --- retentions and Right to Match [A138] -------------------------------------------------
#
# The real 2025 mega-auction rules, adapted where the archive cannot support them. A team
# may keep players from its OWN franchise's nineteen seasons before the auction -- one
# season per person, chosen by the team -- at the real capped-player slabs, in order. Every
# retention place not used becomes a Right to Match card. The real rules also allow two
# uncapped players at ₹4 cr; the archive holds no international-cap data to say who is
# uncapped, and a ₹4 cr slot open to anybody would retain a 99 for a sixth of his price, so
# that slot is left out rather than approximated.
RETENTION_SLABS = (1800, 1400, 1100, 1800, 1400)
MAX_RETENTIONS = len(RETENTION_SLABS)
RTM_PLACES = 6                  # retentions + RTM cards <= 6, as in 2025

# --- money helpers ----------------------------------------------------------------------


def base_price(card: Card) -> int:
    d = card.display or 0
    return next(price for floor, price in BASE_PRICE_TIERS if d >= floor)


def increment(price: int) -> int:
    """The official ladder: ₹5L steps below ₹1 cr, ₹10L to ₹2 cr, ₹20L to ₹3 cr, ₹25L
    above. Keyed on the CURRENT price, as the auctioneer calls it."""
    if price < 100:
        return 5
    if price < 200:
        return 10
    if price < 300:
        return 20
    return 25


def next_price(price: int) -> int:
    return price + increment(price)


def crore(lakh: int) -> str:
    return f"₹{lakh / 100:.2f} cr" if lakh >= 100 else f"₹{lakh}L"


# --- deterministic hashing --------------------------------------------------------------
#
# A `random.Random` per bid step would work and costs ~20µs to construct; a calibration run
# makes millions of these draws. splitmix64 is the standard cheap, well-mixed integer hash.

_MASK = (1 << 64) - 1


def _mix(*parts: int) -> int:
    h = 0x9E3779B97F4A7C15
    for p in parts:
        h = (h ^ (p & _MASK)) & _MASK
        h = (h + 0x9E3779B97F4A7C15) & _MASK
        z = h
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK
        h = z ^ (z >> 31)
    return h


def _unit(*parts: int) -> float:
    """A uniform draw in [0, 1) that is a pure function of its arguments."""
    return _mix(*parts) / 2 ** 64


def _pid(person_id: str) -> int:
    """A stable integer for a person id. Never `hash()`: CPython salts str hashes per
    process, which is exactly A125's bug."""
    return int.from_bytes(person_id.encode()[:8].ljust(8, b"\0"), "big") ^ len(person_id)


# --- the legality algebra ---------------------------------------------------------------

# A76 gives every card exactly one of four bands; the Impact slot takes anybody.
_BAND_OF_MIN = {1: 0, 3: 1, 5: 2, 8: 3}
_BAND_SLOTS = (
    frozenset({1, 2, 3, IMPACT_SLOT}), frozenset({3, 4, 5, IMPACT_SLOT}),
    frozenset({5, 6, 7, IMPACT_SLOT}), frozenset({8, 9, 10, 11, IMPACT_SLOT}),
)
_BAND_CAP = tuple(len(s) for s in _BAND_SLOTS)

# Hall's theorem: real cards can be given distinct slots iff, for every set of bands, the
# cards in them number no more than the slots those bands can reach. Sixteen subsets.
_HALL = tuple(
    (mask, len(frozenset().union(*(_BAND_SLOTS[b] for b in range(4) if mask >> b & 1))))
    for mask in range(1, 16)
)


def band(card: Card) -> int:
    return _BAND_OF_MIN[min(card.positions)]


def _matchable(counts: tuple[int, int, int, int]) -> bool:
    return all(sum(counts[b] for b in range(4) if mask >> b & 1) <= cap
               for mask, cap in _HALL)


def _type_code(card: Card) -> int:
    return (band(card) << 3) | (card.has_bowl << 2) | (card.keeper_eligible << 1) \
        | (card.overseas is True)


def _signature(squad: list[Card]) -> tuple[int, ...]:
    counts = [0] * 32
    for c in squad:
        counts[_type_code(c)] += 1
    return tuple(counts)


def twelve_feasible(squad: list[Card], wildcards: int) -> bool:
    """Can a legal twelve be formed from `squad` plus `wildcards` future buys, each assumed
    the best possible (bats anywhere, bowls, keeps, domestic)?

    A wildcard dominates a real card in every respect, so the best case uses as many as
    there are and exactly `12 - wildcards` real cards. The optimism is the same one A73
    already lives with and is measured, not assumed: the fill round reports every time a
    future buy turns out not to exist.
    """
    return _feasible(_signature(squad), max(0, wildcards))


@lru_cache(maxsize=200_000)
def _feasible(counts32: tuple[int, ...], wildcards: int) -> bool:
    real = max(0, TWELVE_SIZE - wildcards)
    wild = TWELVE_SIZE - real
    # state: (top, middle, finisher, tail, overseas, bowlers capped at 5, keeper)
    states = {(0, 0, 0, 0, 0, 0, False)}
    for code, count in enumerate(counts32):
        if not count:
            continue
        b, bowls, keeps, ov = code >> 3, code >> 2 & 1, code >> 1 & 1, code & 1
        grown = set()
        for st in states:
            n = st[0] + st[1] + st[2] + st[3]
            for k in range(count + 1):
                if n + k > real or st[b] + k > _BAND_CAP[b] or st[4] + k * ov > OVERSEAS_CAP:
                    break
                bands = list(st[:4])
                bands[b] += k
                grown.add((*bands, st[4] + k * ov, min(BOWLERS_IN_TWELVE, st[5] + k * bowls),
                           st[6] or (k > 0 and bool(keeps))))
        states = grown
    return any(
        sum(st[:4]) == real and _matchable(st[:4])
        and st[5] + wild >= BOWLERS_IN_TWELVE and (st[6] or wild >= 1)
        for st in states
    )


def _card_value(c: Card) -> float:
    """What auto-pick maximises: the card's face value, tie-broken by the engine's own
    rating so the choice is deterministic."""
    return (c.display or 0) + 0.01 * c.rating


def best_twelve(squad: list[Card]) -> list[Card] | None:
    """The legal twelve with the highest total face value, found exactly. None if the squad
    cannot field one. The same (band, overseas, bowlers, keeper) state as `_feasible`, but
    over individual cards because two cards of one type can differ in value."""
    best: dict[tuple, tuple[float, tuple[int, ...]]] = {(0, 0, 0, 0, 0, 0, False): (0.0, ())}
    for i, c in enumerate(squad):
        b, ov = band(c), int(c.overseas is True)
        for st, (val, chosen) in list(best.items()):
            if sum(st[:4]) >= TWELVE_SIZE or st[b] >= _BAND_CAP[b] or st[4] + ov > OVERSEAS_CAP:
                continue
            bands = list(st[:4])
            bands[b] += 1
            key = (*bands, st[4] + ov, min(BOWLERS_IN_TWELVE, st[5] + c.has_bowl),
                   st[6] or c.keeper_eligible)
            cand = (val + _card_value(c), chosen + (i,))
            if key not in best or cand[0] > best[key][0]:
                best[key] = cand
    finals = [(val, chosen) for st, (val, chosen) in best.items()
              if sum(st[:4]) == TWELVE_SIZE and _matchable(st[:4])
              and st[5] >= BOWLERS_IN_TWELVE and st[6]]
    if not finals:
        return None
    _, chosen = max(finals)
    return [squad[i] for i in chosen]


def _perfect_order(xi: list[Card]) -> list[Card] | None:
    """Eleven cards into positions 1-11, one each, or None. Plain augmenting paths."""
    owner: dict[int, int] = {}

    def place(i: int, seen: set[int]) -> bool:
        for slot in sorted(xi[i].positions):
            if slot in seen:
                continue
            seen.add(slot)
            if slot not in owner or place(owner[slot], seen):
                owner[slot] = i
                return True
        return False

    for i in range(len(xi)):
        if not place(i, set()):
            return None
    order = [xi[owner[s]] for s in range(1, XI_SIZE + 1)]
    # Better batters higher, wherever both players may bat in each other's place.
    changed = True
    while changed:
        changed = False
        for i in range(XI_SIZE):
            for j in range(i + 1, XI_SIZE):
                a, b = order[i], order[j]
                if (_bat(b) > _bat(a) and (i + 1) in b.positions
                        and (j + 1) in a.positions):
                    order[i], order[j] = b, a
                    changed = True
    return order


def _bat(c: Card) -> float:
    return c.bat if c.has_bat else -9.9


def arrange(twelve: list[Card]) -> tuple[list[Card], Card] | None:
    """A batting order and an Impact Player for a legal twelve.

    The Impact Player is a SPECIALIST where one can be spared: he comes on at the innings
    break for one discipline only (A78), so a pure batter or bowler loses nothing by it
    while an all-rounder or keeper loses half his value. The keeper stays in the eleven,
    and an eleven that still holds five bowlers is preferred, though a legal twelve may
    lean on the Impact Player for the fifth (A133's `with_bowling_depth`).
    """
    ranked = sorted(twelve, key=lambda c: (c.role not in ("batter", "bowler"),
                                           -_card_value(c)))
    for need_five in (True, False):
        for impact in ranked:
            xi = [c for c in twelve if c is not impact]
            if not any(c.keeper_eligible for c in xi):
                continue
            if need_five and sum(c.has_bowl for c in xi) < BOWLERS_IN_TWELVE:
                continue
            order = _perfect_order(xi)
            if order is not None:
                return order, impact
    return None


# --- the catalogue ----------------------------------------------------------------------


@dataclass(frozen=True)
class Lot:
    index: int
    card: Card
    set_code: str
    base: int


def _category(c: Card) -> str:
    if c.role == "keeper":
        return "WK"
    if c.role == "allrounder":
        return "AL"
    if c.role == "bowler":
        # Unknown style gets its own set rather than joining pace (A23).
        return {"pace": "FA", "spin": "SP"}.get(c.bowling_style, "BO")
    return "BA"


_CATEGORY_ORDER = ("BA", "AL", "WK", "FA", "SP", "BO")


def draw_seasons(deck: Deck, seed: int) -> list[Card]:
    """One season of every player, chosen at random per auction, strongest first. The
    catalogue is the top of this list; the rest is the register the fill round draws on."""
    by_person: dict[str, list[Card]] = {}
    for fs_id in sorted(deck.cards_by_fs):
        for c in deck.cards_by_fs[fs_id]:
            by_person.setdefault(c.person_id, []).append(c)
    drawn = []
    for pid in sorted(by_person):
        seasons = by_person[pid]
        drawn.append(seasons[_mix(seed, _pid(pid), 1) % len(seasons)])
    drawn.sort(key=lambda c: (-(c.display or 0), -c.rating, c.person_id))
    return drawn


def build_catalogue(deck: Deck, seed: int, exclude: frozenset[str] = frozenset()) -> list[Lot]:
    """The strongest DOMESTIC_LOTS domestic and OVERSEAS_LOTS overseas draws, in sets run
    the way the real auction runs them: marquee first, then a set of each category in
    turn, then the second set of each... `exclude` is everyone already retained."""
    drawn = [c for c in draw_seasons(deck, seed) if c.person_id not in exclude]
    pool = ([c for c in drawn if c.overseas is not True][:DOMESTIC_LOTS]
            + [c for c in drawn if c.overseas is True][:OVERSEAS_LOTS])
    pool.sort(key=lambda c: (-(c.display or 0), -c.rating, c.person_id))

    marquee, rest = pool[:MARQUEE_SIZE], pool[MARQUEE_SIZE:]
    sets: list[tuple[str, list[Card]]] = [("M1", marquee)]
    grouped = {cat: [c for c in rest if _category(c) == cat] for cat in _CATEGORY_ORDER}
    round_no = 1
    while any(grouped.values()):
        for cat in _CATEGORY_ORDER:
            chunk, grouped[cat] = grouped[cat][:SET_SIZE], grouped[cat][SET_SIZE:]
            if chunk:
                sets.append((f"{cat}{round_no}", chunk))
        round_no += 1

    lots: list[Lot] = []
    for code, cards in sets:
        # Within a set the order is drawn, as the real auctioneer's is.
        for c in sorted(cards, key=lambda c: _mix(seed, _pid(c.person_id), 2)):
            lots.append(Lot(len(lots), c, code, base_price(c)))
    return lots


# --- teams ------------------------------------------------------------------------------

PERSONALITIES = ("aggressive", "balanced", "value")


@dataclass
class Team:
    index: int
    short: str
    franchise: str
    human: bool = False
    personality: str = "balanced"
    purse: int = PURSE
    squad: list[Card] = field(default_factory=list)
    paid: list[int] = field(default_factory=list)
    retained: int = 0               # how many of `squad` were kept before the auction
    rtm: int = 0                    # Right to Match cards left

    @property
    def open_places(self) -> int:
        return SQUAD_SIZE - len(self.squad)

    @property
    def overseas(self) -> int:
        return sum(c.overseas is True for c in self.squad)

    def max_bid(self) -> int:
        """The most this team may pay for the next player and still afford every place
        left after it at the minimum price."""
        return self.purse - MIN_PRICE * max(0, self.open_places - 1)

    def may_buy(self, card: Card) -> bool:
        """Every rule except price: room in the squad, room under the overseas cap, and a
        legal twelve still reachable with the card added."""
        if self.open_places <= 0:
            return False
        if card.overseas is True and self.overseas >= SQUAD_OVERSEAS_CAP:
            return False
        if any(c.person_id == card.person_id for c in self.squad):
            return False
        return twelve_feasible(self.squad + [card], self.open_places - 1)


def make_teams(seed: int, human_short: str | None = None,
               humans: frozenset[str] = frozenset()) -> list[Team]:
    """The ten franchises. `human_short` is the single player's; `humans` is a room's,
    where several franchises are people. A computer team's personality depends only on
    the seed and its index, never on who else is human."""
    teams = []
    for i, (short, name) in enumerate(FRANCHISES):
        personality = PERSONALITIES[_mix(seed, i, 3) % len(PERSONALITIES)]
        teams.append(Team(i, short, name, human=(short == human_short or short in humans),
                          personality=personality))
    return teams


# --- how a computer team values a player ------------------------------------------------
#
# Declared game-design constants, tuned by `tools.auction_calibration` against measured
# targets (every team legal, purses spent, prices ordered by quality) -- not by feel.

VALUE_AT_70 = 180.0             # lakh; A in V(d) = A * exp(K * (d - 70))
VALUE_GROWTH = 0.08             # K
NOISE = 0.20                    # each team's view of a player varies by +/- this
PURSE_PRESSURE = 0.8            # how strongly money left per place moves the ceiling
LOYALTY = 1.15                  # a franchise pays more for its own former players

# No single player takes more than this share of a purse. Without it the only way the
# sweep could get purses spent was an exponential curve steep enough to send stars to
# ₹65-106 cr -- over half a purse on one man, where the real auction's record is under
# ₹30 cr -- after which the teams that paid it limped through the fill round. A declared
# ceiling on the market, like the real auction's own purse arithmetic imposes in practice.
# COMPUTER TEAMS ONLY, ratified by the user: a human may pay whatever they like, and the
# reserve rule is the only thing that limits them.
MAX_SHARE = 0.25

BAND_TARGET = (4, 3, 4, 5)      # top / middle / finisher / tail wanted in a squad of 18
KEEPERS_WANTED = 2
BOWLERS_WANTED = 7


def value_curve(display: int | None) -> float:
    return VALUE_AT_70 * math.exp(VALUE_GROWTH * ((display or 70) - 70))


def need(team: Team, card: Card) -> float:
    m = 1.0
    b = band(card)
    have = sum(band(c) == b for c in team.squad)
    if have < BAND_TARGET[b]:
        m *= min(1.3, 1.0 + 0.1 * (BAND_TARGET[b] - have))
    elif have >= BAND_TARGET[b] + 2:
        m *= 0.55
    keepers = sum(c.keeper_eligible for c in team.squad)
    if card.keeper_eligible:
        if keepers == 0:
            m *= 1.4
        elif keepers >= KEEPERS_WANTED and card.role == "keeper":
            m *= 0.8
    if card.has_bowl:
        bowlers = sum(c.has_bowl for c in team.squad)
        if bowlers < BOWLERS_WANTED:
            m *= 1.0 + 0.05 * (BOWLERS_WANTED - bowlers)
    if card.overseas is True and team.overseas >= OVERSEAS_CAP:
        m *= 0.7
    return m


def _personality(team: Team, card: Card) -> float:
    d = card.display or 70
    if team.personality == "aggressive":
        return 1.2 if d >= 88 else 1.0
    if team.personality == "value":
        return 0.85 if d >= 88 else (1.1 if d < 82 else 1.0)
    return 1.0


def _loyal(team: Team, card: Card) -> float:
    try:
        return LOYALTY if card.franchise and canonical(card.franchise) == team.franchise else 1.0
    except RuntimeError:
        return 1.0


def cpu_ceiling(team: Team, lot: Lot, seed: int, round_no: int) -> int:
    """The most a computer team will pay for this lot, fixed before the lot opens.

    Deliberately blind to anything the human does to THIS lot, which is what makes the two
    bidding styles the same game (module docstring)."""
    card = lot.card
    if not team.may_buy(card):
        return 0
    per_place = (team.purse - MIN_PRICE * team.open_places) / max(1, team.open_places)
    pressure = max(0.3, min(2.5, per_place / (PURSE / SQUAD_SIZE))) ** PURSE_PRESSURE
    noise = 1.0 + NOISE * (2 * _unit(seed, team.index, lot.index, round_no, 4) - 1)
    value = (value_curve(card.display) * need(team, card) * pressure
             * _personality(team, card) * _loyal(team, card) * noise)
    ceiling = min(int(value), team.max_bid(), int(MAX_SHARE * PURSE))
    return ceiling if ceiling >= lot.base else 0


# --- retentions ---------------------------------------------------------------------------

RETAIN_MIN = 90                 # a computer team keeps its own legends rated at least this
RETAIN_COUNT = {"aggressive": 5, "balanced": 4, "value": 3}
RTM_PREMIUM = 1.05              # how far past its own ceiling a team goes for its old player


def franchise_of(card: Card) -> str | None:
    try:
        return canonical(card.franchise) if card.franchise else None
    except RuntimeError:
        return None


def retention_pool(deck: Deck, franchise: str) -> list[Card]:
    """Every season a franchise ever had, strongest first. A team keeps at most one season
    of any one person, and chooses which."""
    pool = [c for cards in deck.cards_by_fs.values() for c in cards
            if franchise_of(c) == franchise]
    pool.sort(key=lambda c: (-(c.display or 0), -c.rating, c.person_id, c.season_year or 0))
    return pool


def retention_errors(team: Team, chosen: list[Card]) -> list[str]:
    """Why a set of retentions is not allowed, or nothing. Checked for the computer teams
    as well as the human -- a rule enforced for one side only is half a rule."""
    errors = []
    if len(chosen) > MAX_RETENTIONS:
        errors.append(f"at most {MAX_RETENTIONS} retentions")
    if len({c.person_id for c in chosen}) != len(chosen):
        errors.append("one season per player")
    if any(franchise_of(c) != team.franchise for c in chosen):
        errors.append("only your own franchise's players")
    if sum(c.overseas is True for c in chosen) > SQUAD_OVERSEAS_CAP:
        errors.append("too many overseas players")
    if not twelve_feasible(chosen, SQUAD_SIZE - len(chosen)):
        errors.append("no legal twelve could be built around them")
    return errors


def retain(team: Team, chosen: list[Card]) -> None:
    for card, price in zip(chosen, RETENTION_SLABS):
        team.squad.append(card)
        team.paid.append(price)
        team.purse -= price
    team.retained = len(chosen)
    team.rtm = RTM_PLACES - len(chosen)


def cpu_retain(team: Team, pool: list[Card], taken: set[str]) -> list[Card]:
    """Keep the best season of each of the franchise's own legends, up to what the team's
    personality allows. A declared rule, measured by the calibration like the rest."""
    best: dict[str, Card] = {}
    for c in pool:                   # pool is strongest first, so the first seen is best
        if c.person_id not in taken:
            best.setdefault(c.person_id, c)
    chosen: list[Card] = []
    for c in best.values():
        if len(chosen) >= RETAIN_COUNT[team.personality] or (c.display or 0) < RETAIN_MIN:
            break
        if not retention_errors(team, chosen + [c]):
            chosen.append(c)
    return chosen


# --- Right to Match -----------------------------------------------------------------------


def rtm_holder(auction: "Auction", lot: Lot, winner: int) -> Team | None:
    """The franchise this season was played for, if it still holds a card and could take
    the player. A season played for Deccan or Kochi has nobody to come back for it."""
    home = franchise_of(lot.card)
    for team in auction.teams:
        if (team.franchise == home and team.index != winner and team.rtm > 0
                and team.may_buy(lot.card)):
            return team
    return None


def _ladder_at_most(price: int, floor: int) -> int:
    """The highest price on the bidding ladder that is at most `price`, from `floor` up."""
    p = floor
    while next_price(p) <= price:
        p = next_price(p)
    return p


def cpu_rtm_limit(team: Team, lot: Lot, ceiling: int) -> int:
    """How far a computer team will go to take back its own player with a card."""
    return min(int(ceiling * RTM_PREMIUM), team.max_bid())


# --- a lot ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Bid:
    team: int
    price: int


@dataclass
class Sale:
    lot: Lot
    round: str                  # "main" | "accelerated" | "fill"
    winner: int | None          # team index, None if unsold
    price: int
    bids: list[Bid]
    rtm: "RtmEvent | None" = None


@dataclass(frozen=True)
class RtmEvent:
    """A Right to Match, as it happened: `holder` played the card after the hammer at
    `hammer`, the winner made a final raise to `raised_to` (equal to `hammer` if none),
    and the holder matched it or did not."""
    holder: int
    hammer: int
    raised_to: int
    matched: bool


def bid_log(lot: Lot, ceilings: dict[int, int], seed: int, round_no: int,
            human: int | None = None) -> list[Bid]:
    """The ascending auction for one lot, given every team's ceiling.

    The lot opens at its base price. At each step whoever is willing to go to the next
    price and is not already leading may bid; the human, if willing, goes first (they are
    the one clicking), otherwise the next bidder is a pure hash of where the lot stands.
    Ends when nobody but the leader is willing."""
    return continue_bidding(lot, [], ceilings, seed, round_no, human)


def continue_bidding(lot: Lot, bids: list[Bid], ceilings: dict[int, int], seed: int,
                     round_no: int, human: int | None = None) -> list[Bid]:
    """`bid_log`, resumed from wherever `bids` left the lot. A room needs this: several
    humans bid by hand, so a lot is not one call but a series of them, each human bid
    followed by whatever the automatic bidders (`ceilings`) do in reply. The tie-break is
    the same hash of (lot, price, leader), so resuming never changes what would have
    happened -- `bid_log` is exactly this from an empty log."""
    bids = list(bids)
    leader: int | None = bids[-1].team if bids else None
    price = bids[-1].price if bids else lot.base
    while True:
        target = lot.base if leader is None else next_price(price)
        willing = sorted(t for t, cap in ceilings.items() if t != leader and cap >= target)
        if not willing:
            return bids
        if human is not None and human in willing:
            chosen = human
        else:
            chosen = willing[_mix(seed, lot.index, round_no, target, -1 if leader is None
                                  else leader, 5) % len(willing)]
        bids.append(Bid(chosen, target))
        leader, price = chosen, target


# --- the auction ------------------------------------------------------------------------


class Human:
    """What a human has decided, asked for one lot at a time. The web layer replays
    recorded numbers and pauses when it runs out; the calibration supplies a strategy."""

    def ceiling(self, auction: "Auction", team: Team, lot: Lot, round_no: int) -> int:
        return 0

    def fill_choice(self, auction: "Auction", team: Team, options: list[Card]) -> Card:
        return max(options, key=_card_value)

    def retain(self, auction: "Auction", team: Team, pool: list[Card]) -> list[Card]:
        return []

    def rtm_use(self, auction: "Auction", team: Team, lot: Lot, price: int,
                winner: Team) -> bool:
        """Your old player just sold to `winner` for `price`. Play a card?"""
        return False

    def rtm_raise(self, auction: "Auction", team: Team, lot: Lot, price: int,
                  holder: Team) -> int:
        """You won, and `holder` played a card. Your one final raise (price = none)."""
        return price

    def rtm_match(self, auction: "Auction", team: Team, lot: Lot, price: int,
                  winner: Team) -> bool:
        """`winner` raised to `price` after your card. Match it?"""
        return False


ROUNDS = ("main", "accelerated")


@dataclass
class Auction:
    seed: int
    lots: list[Lot]
    teams: list[Team]
    mega: bool = False              # retentions and Right to Match [A138]
    sales: list[Sale] = field(default_factory=list)
    register: list[Card] = field(default_factory=list)  # drawn, never catalogued
    stranded: list[int] = field(default_factory=list)   # teams the fill round could not finish
    fills: int = 0                                      # players the fill round handed out
    # The lot being decided, while a Right to Match is asked about it -- so a paused
    # auction can still show the bidding that led to the question.
    current_bids: list[Bid] = field(default_factory=list)

    @property
    def unsold(self) -> list[Lot]:
        sold = {s.lot.index for s in self.sales if s.winner is not None}
        return [lot for lot in self.lots if lot.index not in sold]

    def twelve(self, team: Team) -> tuple[list[Card], Card] | None:
        chosen = best_twelve(team.squad)
        return arrange(chosen) if chosen else None


def lot_ceilings(auction: Auction, lot: Lot, round_name: str,
                 human_ceiling: int = 0) -> dict[int, int]:
    """Every team's ceiling for this lot. The human's is whatever they asked for, clipped
    to what the reserve rule lets them pay -- and never to the computer teams' cap."""
    round_no = ROUNDS.index(round_name)
    ceilings: dict[int, int] = {}
    for team in auction.teams:
        if team.human:
            cap = min(human_ceiling, team.max_bid()) if team.may_buy(lot.card) else 0
            ceilings[team.index] = cap if cap >= lot.base else 0
        else:
            ceilings[team.index] = cpu_ceiling(team, lot, auction.seed, round_no)
    return ceilings


def human_index(auction: Auction) -> int | None:
    return next((t.index for t in auction.teams if t.human), None)


def preview(auction: Auction, lot: Lot, round_name: str, human_ceiling: int) -> list[Bid]:
    """The bid log this lot WOULD produce at `human_ceiling`, changing nothing. What a
    human who has bid, and may bid again, is shown."""
    return bid_log(lot, lot_ceilings(auction, lot, round_name, human_ceiling), auction.seed,
                   ROUNDS.index(round_name), human_index(auction))


def _offer(auction: Auction, lot: Lot, round_name: str, human: Human | None) -> Sale:
    round_no = ROUNDS.index(round_name)
    wanted = 0
    hi = human_index(auction)
    if hi is not None and human is not None and auction.teams[hi].may_buy(lot.card):
        wanted = human.ceiling(auction, auction.teams[hi], lot, round_no)
    ceilings = lot_ceilings(auction, lot, round_name, wanted)
    bids = bid_log(lot, ceilings, auction.seed, round_no, hi)
    if not bids:
        return Sale(lot, round_name, None, 0, bids)
    buyer, price = bids[-1].team, bids[-1].price
    event = None
    auction.current_bids = bids
    if auction.mega:
        buyer, price, event = _right_to_match(auction, lot, buyer, price, ceilings, human)
    team = auction.teams[buyer]
    team.squad.append(lot.card)
    team.paid.append(price)
    team.purse -= price
    return Sale(lot, round_name, buyer, price, bids, event)


def _right_to_match(auction: Auction, lot: Lot, winner_idx: int, hammer: int,
                    ceilings: dict[int, int], human: Human | None):
    """The 2025 rule. The player's old franchise may play a card; the winner then makes ONE
    final raise; the old franchise matches it and takes the player, or the winner has him
    at the raised price. A card is spent only when it is matched."""
    winner = auction.teams[winner_idx]
    holder = rtm_holder(auction, lot, winner_idx)
    if holder is None or holder.max_bid() < hammer:
        return winner_idx, hammer, None

    def asks(team: Team) -> bool:
        return team.human and human is not None

    limit = 0 if asks(holder) else cpu_rtm_limit(holder, lot, ceilings[holder.index])
    use = (human.rtm_use(auction, holder, lot, hammer, winner) if asks(holder)
           else limit >= hammer)
    if not use:
        return winner_idx, hammer, None

    if asks(winner):
        wanted = human.rtm_raise(auction, winner, lot, hammer, holder)
    else:
        # Halfway to what the winner would have paid: enough to make the card cost
        # something, not so much that a declined match leaves the winner badly overpaid.
        wanted = hammer + (max(hammer, ceilings[winner_idx]) - hammer) // 2
    raised = _ladder_at_most(min(max(wanted, hammer), winner.max_bid()), hammer)

    if raised > holder.max_bid():
        matched = False
    elif raised == hammer:
        matched = True
    elif asks(holder):
        matched = human.rtm_match(auction, holder, lot, raised, winner)
    else:
        matched = limit >= raised
    event = RtmEvent(holder.index, hammer, raised, matched)
    if matched:
        holder.rtm -= 1
        return holder.index, raised, event
    return winner_idx, raised, event


class RetentionError(ValueError):
    pass


def _retentions(deck: Deck, seed: int, teams: list[Team], human: Human | None) -> set[str]:
    """Every team keeps its players before the auction opens, the human first so a legend
    two franchises share is never taken out from under them. Returns who was kept."""
    taken: set[str] = set()
    order = sorted(teams, key=lambda t: (not t.human, t.index))
    for team in order:
        pool = [c for c in retention_pool(deck, team.franchise) if c.person_id not in taken]
        if team.human and human is not None:
            chosen = human.retain(None, team, pool)
            errors = retention_errors(team, chosen)
            if errors or any(c.person_id in taken for c in chosen):
                raise RetentionError("; ".join(errors) or "already retained elsewhere")
        else:
            chosen = cpu_retain(team, pool, taken)
        retain(team, chosen)
        taken |= {c.person_id for c in chosen}
    return taken


def run_auction(deck: Deck, seed: int, human_short: str | None = None,
                human: Human | None = None,
                on_sale: Callable[[Sale], None] | None = None,
                mega: bool = False) -> Auction:
    teams = make_teams(seed, human_short)
    kept: set[str] = set()
    if mega:
        kept = _retentions(deck, seed, teams, human)
    lots = build_catalogue(deck, seed, frozenset(kept))
    listed = {lot.card.person_id for lot in lots} | kept
    register = [c for c in draw_seasons(deck, seed) if c.person_id not in listed]
    auction = Auction(seed, lots, teams, mega=mega, register=register)
    for round_name in ROUNDS:
        lots = auction.lots if round_name == "main" else auction.unsold
        for lot in lots:
            if all(t.open_places == 0 for t in auction.teams):
                break
            sale = _offer(auction, lot, round_name, human)
            auction.sales.append(sale)
            if on_sale:
                on_sale(sale)
    _fill(auction, human)
    return auction


def _fill(auction: Auction, human: Human | None) -> None:
    """Every team still short takes a player at the minimum price, fewest-players first --
    from the unsold lots, and then from the rest of the register, as the real auction's
    long list does. A net, not a round anyone should rely on: `fills` counts every player
    it hands out, and a team it cannot finish is recorded in `stranded` rather than
    papered over."""
    while True:
        short = [t for t in auction.teams if t.open_places > 0 and t.index not in auction.stranded]
        if not short:
            return
        team = min(short, key=lambda t: (len(t.squad), t.index))
        taken = {c.person_id for t in auction.teams for c in t.squad}
        pool = [lot.card for lot in auction.unsold] + auction.register
        options = sorted((c for c in pool if c.person_id not in taken
                          and team.may_buy(c) and team.purse >= MIN_PRICE),
                         key=lambda c: (-(c.display or 0), -c.rating, c.person_id))
        if not options:
            auction.stranded.append(team.index)
            continue
        if team.human and human is not None:
            card = human.fill_choice(auction, team, options)
        else:
            card = max(options, key=lambda c: need(team, c) * value_curve(c.display))
        lot = next((lot for lot in auction.unsold if lot.card is card),
                   Lot(-1, card, "REG", MIN_PRICE))
        team.squad.append(card)
        team.paid.append(MIN_PRICE)
        team.purse -= MIN_PRICE
        auction.fills += 1
        auction.sales.append(Sale(lot, "fill", team.index, MIN_PRICE,
                                  [Bid(team.index, MIN_PRICE)]))
