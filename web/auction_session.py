"""A single-player auction as a replayable string. [A136, phase 2]

Same shape as `web/session.py` (A62): nothing is stored. The state is the seed, the
human's franchise and one token per decision, and every request replays the auction from
scratch up to wherever it next needs the human.

    A{seed}-{SHORT}-{token}.{token}...

Tokens, in the order the engine asks for them:

    450      a closed ceiling for one lot, in lakh (0 = not interested)
    450o     an OPEN ceiling: the human has bid 450 on this lot and may bid again
    z12      twelve closed zeros (a run of "not interested")
    pBA3     pass every lot still to come in set BA3, this round
    pall     pass every remaining lot, both rounds
    f7       in the fill round, the 7th option offered
    t4,1,9,...  the twelve: eleven squad indexes in batting order, then the Impact Player

**An open token is a commitment, which is what stops the preview being a free peek.** A
human who bids ₹4.5 cr is shown the bidding that follows; to go on they must raise, and a
raise may only be LARGER than what they already bid. So there is no way to see how far the
computer teams would go without having bid that far oneself -- exactly the real auction's
rule, where a paddle once raised stays raised.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import game.auction as au
from etl.feasibility import Card, Deck, order_errors

PREFIX = "A"


class InvalidState(ValueError):
    pass


def is_auction_state(state: str) -> bool:
    return state.startswith(PREFIX)


# --- encoding ----------------------------------------------------------------------------

_TOKEN = re.compile(r"^(?:\d+o?|z\d+|p[A-Z]{1,2}\d+|pall|f\d+|t\d+(?:,\d+){11})$")


def encode(seed: int, short: str, tokens: list[str]) -> str:
    """Runs of closed zeros are folded into `zN`, so passing on a hundred lots costs a few
    characters rather than two hundred."""
    folded: list[str] = []
    for tok in tokens:
        if tok == "0":
            if folded and folded[-1].startswith("z"):
                folded[-1] = f"z{int(folded[-1][1:]) + 1}"
                continue
            folded.append("z1")
        else:
            folded.append(tok)
    return f"{PREFIX}{seed}-{short}-" + ".".join(folded)


def decode(state: str) -> tuple[int, str, list[str]]:
    m = re.fullmatch(r"A(\d+)-([A-Z]{2,4})-(.*)", state)
    if not m:
        raise InvalidState("not an auction state")
    seed, short, body = int(m.group(1)), m.group(2), m.group(3)
    if short not in {s for s, _ in au.FRANCHISES}:
        raise InvalidState(f"unknown franchise {short}")
    tokens: list[str] = []
    for tok in (body.split(".") if body else []):
        if not _TOKEN.match(tok):
            raise InvalidState(f"bad move {tok!r}")
        if tok.startswith("z"):
            tokens.extend(["0"] * int(tok[1:]))
        else:
            tokens.append(tok)
    return seed, short, tokens


# --- replay ------------------------------------------------------------------------------


class _NeedBid(Exception):
    def __init__(self, auction, lot, round_no, open_ceiling):
        self.auction, self.lot, self.round_no, self.open_ceiling = \
            auction, lot, round_no, open_ceiling


class _NeedFill(Exception):
    def __init__(self, auction, team, options):
        self.auction, self.team, self.options = auction, team, options


class _Replayer(au.Human):
    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.pos = 0
        self.passing: str | None = None     # a set code, or "all"
        self.passing_round: int | None = None

    def _next(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def ceiling(self, auction, team, lot, round_no):
        if self.passing == "all" or (self.passing == lot.set_code
                                     and self.passing_round == round_no):
            return 0
        self.passing = None
        tok = self._next()
        while tok is not None and tok.startswith("p"):
            self.pos += 1
            self.passing = "all" if tok == "pall" else tok[1:]
            self.passing_round = round_no
            if self.passing in ("all", lot.set_code):
                return 0
            tok = self._next()
        if tok is None:
            raise _NeedBid(auction, lot, round_no, None)
        if not tok[0].isdigit():
            raise InvalidState(f"expected a bid for lot {lot.index}, found {tok!r}")
        if tok.endswith("o"):
            if self.pos != len(self.tokens) - 1:
                raise InvalidState("an open bid can only be the last move")
            raise _NeedBid(auction, lot, round_no, int(tok[:-1]))
        self.pos += 1
        return int(tok)

    def fill_choice(self, auction, team, options):
        tok = self._next()
        if tok is None:
            raise _NeedFill(auction, team, options)
        if not tok.startswith("f"):
            raise InvalidState(f"expected a fill choice, found {tok!r}")
        i = int(tok[1:])
        if i >= len(options):
            raise InvalidState("no such fill option")
        self.pos += 1
        return options[i]


@dataclass
class Replay:
    seed: int
    short: str
    tokens: list[str]
    auction: au.Auction
    phase: str                          # "bid" | "fill" | "twelve" | "ready"
    lot: au.Lot | None = None
    round_no: int = 0
    open_ceiling: int | None = None
    fill_options: list[Card] = field(default_factory=list)
    twelve: tuple[list[Card], Card] | None = None     # chosen, once phase == "ready"
    suggestion: tuple[list[Card], Card] | None = None

    @property
    def state(self) -> str:
        return encode(self.seed, self.short, self.tokens)

    @property
    def you(self) -> au.Team:
        return next(t for t in self.auction.teams if t.human)

    def preview(self) -> list[au.Bid]:
        if self.lot is None or self.open_ceiling is None:
            return []
        return au.preview(self.auction, self.lot, au.ROUNDS[self.round_no], self.open_ceiling)


def replay(deck: Deck, state: str) -> Replay:
    seed, short, tokens = decode(state)
    policy = _Replayer(tokens)
    try:
        auction = au.run_auction(deck, seed, human_short=short, human=policy)
    except _NeedBid as e:
        return Replay(seed, short, tokens, e.auction, "bid", lot=e.lot, round_no=e.round_no,
                      open_ceiling=e.open_ceiling)
    except _NeedFill as e:
        return Replay(seed, short, tokens, e.auction, "fill", fill_options=e.options)

    rest = tokens[policy.pos:]
    rest = [t for t in rest if not t.startswith("p")]     # a pass that outlived the lots
    you = next(t for t in auction.teams if t.human)
    chosen = au.best_twelve(you.squad)
    suggestion = au.arrange(chosen) if chosen else None
    if not rest:
        return Replay(seed, short, tokens, auction, "twelve", suggestion=suggestion)
    if len(rest) != 1 or not rest[0].startswith("t"):
        raise InvalidState("moves left over after the auction ended")
    idx = [int(x) for x in rest[0][1:].split(",")]
    if len(set(idx)) != 12 or any(i >= len(you.squad) for i in idx):
        raise InvalidState("the twelve must be twelve different players from your squad")
    order, impact = [you.squad[i] for i in idx[:11]], you.squad[idx[11]]
    errors = order_errors(order, impact, you.squad)
    if errors:
        raise InvalidState("; ".join(errors))
    return Replay(seed, short, tokens, auction, "ready", twelve=(order, impact),
                  suggestion=suggestion)


# --- moves -------------------------------------------------------------------------------


def new_state(seed: int, short: str) -> str:
    if short not in {s for s, _ in au.FRANCHISES}:
        raise InvalidState(f"unknown franchise {short}")
    return encode(seed, short, [])


def bid(deck: Deck, state: str, ceiling: int, done: bool) -> Replay:
    """Raise to `ceiling` on the lot being offered. `done` closes it: the human is not
    going higher. A bid the human would win outright closes itself, since there is nothing
    further they could want to do."""
    r = replay(deck, state)
    if r.phase != "bid":
        raise InvalidState("no lot is waiting for a bid")
    tokens = list(r.tokens)
    if r.open_ceiling is not None:
        if ceiling < r.open_ceiling:
            raise InvalidState("a bid once made cannot be withdrawn")
        tokens.pop()
    if ceiling == 0:
        tokens.append("0")
        return replay(deck, encode(r.seed, r.short, tokens))
    if ceiling < r.lot.base:
        raise InvalidState(f"the base price is {au.crore(r.lot.base)}")
    ceiling = min(ceiling, r.you.max_bid())
    log = au.preview(r.auction, r.lot, au.ROUNDS[r.round_no], ceiling)
    leading = bool(log) and log[-1].team == r.you.index
    tokens.append(str(ceiling) if (done or leading) else f"{ceiling}o")
    return replay(deck, encode(r.seed, r.short, tokens))


def pass_lots(deck: Deck, state: str, scope: str) -> Replay:
    """Not interested: in this lot, the rest of this set, or everything left."""
    r = replay(deck, state)
    if r.phase != "bid":
        raise InvalidState("no lot is waiting for a bid")
    tokens = list(r.tokens)
    if r.open_ceiling is not None:
        tokens[-1] = tokens[-1][:-1]            # close what was bid; it stands
        if scope == "lot":
            return replay(deck, encode(r.seed, r.short, tokens))
    elif scope == "lot":
        tokens.append("0")
        return replay(deck, encode(r.seed, r.short, tokens))
    tokens.append("pall" if scope == "all" else f"p{r.lot.set_code}")
    return replay(deck, encode(r.seed, r.short, tokens))


def fill(deck: Deck, state: str, index: int) -> Replay:
    r = replay(deck, state)
    if r.phase != "fill":
        raise InvalidState("nothing to fill")
    if not 0 <= index < len(r.fill_options):
        raise InvalidState("no such player")
    return replay(deck, encode(r.seed, r.short, r.tokens + [f"f{index}"]))


def choose_twelve(deck: Deck, state: str, order: list[int], impact: int) -> Replay:
    r = replay(deck, state)
    if r.phase not in ("twelve", "ready"):
        raise InvalidState("the auction is not over")
    tokens = [t for t in r.tokens if not t.startswith("t")]
    return replay(deck, encode(r.seed, r.short,
                               tokens + ["t" + ",".join(map(str, order + [impact]))]))


def twelve_indexes(r: Replay, twelve: tuple[list[Card], Card]) -> tuple[list[int], int]:
    squad = r.you.squad
    pos = {id(c): i for i, c in enumerate(squad)}
    return [pos[id(c)] for c in twelve[0]], pos[id(twelve[1])]


# --- the season ---------------------------------------------------------------------------


def season_sides(deck: Deck, state: str):
    """Your twelve and the nine computer teams' best twelves, as season `Side`s. The
    season plays against the teams you bid against."""
    from game.season import Side
    r = replay(deck, state)
    if r.phase != "ready":
        raise InvalidState("pick your twelve before the season")
    order, impact = r.twelve
    yours = Side(name=r.you.franchise, short="YOU", xi=list(order), impact=impact, you=True)
    others = []
    for team in r.auction.teams:
        if team.human:
            continue
        arranged = r.auction.twelve(team)
        if arranged is None:
            raise InvalidState(f"{team.short} could not field a twelve")
        others.append(Side(name=team.franchise, short=team.short,
                           xi=list(arranged[0]), impact=arranged[1]))
    return yours, others, r.seed
