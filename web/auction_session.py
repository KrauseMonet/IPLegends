"""A single-player auction as a replayable string. [A136, phase 2; A138, phase 3]

Same shape as `web/session.py` (A62): nothing is stored. The state is the seed, the
human's franchise and one token per decision, and every request replays the auction from
scratch up to wherever it next needs the human.

    A{seed}-{SHORT}-{token}.{token}...       an open auction (everyone starts from scratch)
    AR{seed}-{SHORT}-{token}.{token}...      a mega auction: retentions and Right to Match

The two are different games and are told apart by the prefix, never by a token, so a state
saved before retentions existed replays exactly as it did.

Tokens, in the order the engine asks for them:

    k3,7,12  mega only, always first: the retained players, as indexes into the franchise's
             retention pool (`k` alone = keep nobody)
    450      a closed ceiling for one lot, in lakh (0 = not interested)
    450o     an OPEN ceiling: the human has bid 450 on this lot and may bid again
    z12      twelve closed zeros (a run of "not interested")
    pBA3     pass every lot still to come in set BA3, this round
    pall     pass every remaining lot, both rounds (and decline every Right to Match)
    r1 / r0  play / do not play a Right to Match card on your old player
    m1 / m0  match / do not match the winner's final raise
    x900     you won, the old franchise played a card: your final raise, in lakh
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
MEGA = "AR"


class InvalidState(ValueError):
    pass


def is_auction_state(state: str) -> bool:
    return state.startswith(PREFIX)


def is_mega(state: str) -> bool:
    return state.startswith(MEGA)


# --- encoding ----------------------------------------------------------------------------

_TOKEN = re.compile(r"^(?:\d+o?|z\d+|p[A-Z]{1,2}\d+|pall|f\d+|t\d+(?:,\d+){11}"
                    r"|k(?:\d+(?:,\d+)*)?|r[01]|m[01]|x\d+)$")


def encode(seed: int, short: str, tokens: list[str], mega: bool = False) -> str:
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
    return f"{MEGA if mega else PREFIX}{seed}-{short}-" + ".".join(folded)


def decode(state: str) -> tuple[int, str, list[str]]:
    m = re.fullmatch(r"AR?(\d+)-([A-Z]{2,4})-(.*)", state)
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


class _Pause(Exception):
    """The engine needs the human. `kind` is the phase the page shows."""

    def __init__(self, kind: str, **info):
        super().__init__(kind)
        self.kind, self.info = kind, info


class _Replayer(au.Human):
    def __init__(self, tokens: list[str], franchise_pool: list[Card] | None):
        self.tokens = tokens
        self.pos = 0
        self.passing: str | None = None     # a set code, or "all"
        self.passing_round: int | None = None
        self.pool = franchise_pool or []

    def _next(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _take(self, prefix: str, kind: str, **info) -> str:
        tok = self._next()
        if tok is None:
            raise _Pause(kind, **info)
        if not tok.startswith(prefix):
            raise InvalidState(f"expected {kind}, found {tok!r}")
        self.pos += 1
        return tok[len(prefix):]

    def retain(self, auction, team, pool):
        body = self._take("k", "retain")
        idx = [int(x) for x in body.split(",")] if body else []
        if any(i >= len(self.pool) for i in idx):
            raise InvalidState("no such player to retain")
        return [self.pool[i] for i in idx]

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
            raise _Pause("bid", auction=auction, lot=lot, round_no=round_no,
                         open_ceiling=None)
        if not tok[0].isdigit():
            raise InvalidState(f"expected a bid for lot {lot.index}, found {tok!r}")
        if tok.endswith("o"):
            if self.pos != len(self.tokens) - 1:
                raise InvalidState("an open bid can only be the last move")
            raise _Pause("bid", auction=auction, lot=lot, round_no=round_no,
                         open_ceiling=int(tok[:-1]))
        self.pos += 1
        return int(tok)

    def rtm_use(self, auction, team, lot, price, winner):
        if self.passing == "all":
            return False
        return self._take("r", "rtm_use", auction=auction, lot=lot, price=price,
                          other=winner) == "1"

    def rtm_match(self, auction, team, lot, price, winner):
        return self._take("m", "rtm_match", auction=auction, lot=lot, price=price,
                          other=winner) == "1"

    def rtm_raise(self, auction, team, lot, price, holder):
        return int(self._take("x", "rtm_raise", auction=auction, lot=lot, price=price,
                              other=holder))

    def fill_choice(self, auction, team, options):
        i = int(self._take("f", "fill", auction=auction, options=options))
        if i >= len(options):
            raise InvalidState("no such fill option")
        return options[i]


@dataclass
class Replay:
    seed: int
    short: str
    tokens: list[str]
    mega: bool
    auction: au.Auction | None
    phase: str      # retain | bid | rtm_use | rtm_match | rtm_raise | fill | twelve | ready
    lot: au.Lot | None = None
    round_no: int = 0
    open_ceiling: int | None = None
    fill_options: list[Card] = field(default_factory=list)
    retention_pool: list[Card] = field(default_factory=list)
    rtm_price: int | None = None
    rtm_other: au.Team | None = None
    twelve: tuple[list[Card], Card] | None = None     # chosen, once phase == "ready"
    suggestion: tuple[list[Card], Card] | None = None

    @property
    def state(self) -> str:
        return encode(self.seed, self.short, self.tokens, self.mega)

    @property
    def franchise(self) -> str:
        return dict(au.FRANCHISES)[self.short]

    @property
    def you(self) -> au.Team | None:
        if self.auction is None:
            return None
        return next(t for t in self.auction.teams if t.human)

    def preview(self) -> list[au.Bid]:
        """The bidding to show on the lot in front of the human. Empty during a Right to
        Match question: the engine does not yet keep the bids of a lot it is still
        deciding, so the page shows the hammer price alone there."""
        if self.lot is None or self.phase != "bid" or self.open_ceiling is None:
            return []
        return au.preview(self.auction, self.lot, au.ROUNDS[self.round_no], self.open_ceiling)


def replay(deck: Deck, state: str) -> Replay:
    seed, short, tokens = decode(state)
    mega = is_mega(state)
    pool = au.retention_pool(deck, dict(au.FRANCHISES)[short]) if mega else None
    policy = _Replayer(tokens, pool)
    base = dict(seed=seed, short=short, tokens=tokens, mega=mega)
    try:
        auction = au.run_auction(deck, seed, human_short=short, human=policy, mega=mega)
    except _Pause as p:
        info = p.info
        auction = info.get("auction")
        if p.kind == "retain":
            return Replay(**base, auction=None, phase="retain", retention_pool=pool)
        if p.kind == "bid":
            return Replay(**base, auction=auction, phase="bid", lot=info["lot"],
                          round_no=info["round_no"], open_ceiling=info["open_ceiling"])
        if p.kind == "fill":
            return Replay(**base, auction=auction, phase="fill",
                          fill_options=info["options"])
        return Replay(**base, auction=auction, phase=p.kind, lot=info["lot"],
                      rtm_price=info["price"], rtm_other=info["other"])
    except au.RetentionError as exc:
        raise InvalidState(str(exc)) from exc

    rest = tokens[policy.pos:]
    rest = [t for t in rest if not t.startswith("p")]     # a pass that outlived the lots
    you = next(t for t in auction.teams if t.human)
    chosen = au.best_twelve(you.squad)
    suggestion = au.arrange(chosen) if chosen else None
    if not rest:
        return Replay(**base, auction=auction, phase="twelve", suggestion=suggestion)
    if len(rest) != 1 or not rest[0].startswith("t"):
        raise InvalidState("moves left over after the auction ended")
    idx = [int(x) for x in rest[0][1:].split(",")]
    if len(set(idx)) != 12 or any(i >= len(you.squad) for i in idx):
        raise InvalidState("the twelve must be twelve different players from your squad")
    order, impact = [you.squad[i] for i in idx[:11]], you.squad[idx[11]]
    errors = order_errors(order, impact, you.squad)
    if errors:
        raise InvalidState("; ".join(errors))
    return Replay(**base, auction=auction, phase="ready", twelve=(order, impact),
                  suggestion=suggestion)


def _next_state(r: Replay, tokens: list[str]) -> str:
    return encode(r.seed, r.short, tokens, r.mega)


# --- moves -------------------------------------------------------------------------------


def new_state(seed: int, short: str, mega: bool = False) -> str:
    if short not in {s for s, _ in au.FRANCHISES}:
        raise InvalidState(f"unknown franchise {short}")
    return encode(seed, short, [], mega)


def retain(deck: Deck, state: str, indexes: list[int]) -> Replay:
    r = replay(deck, state)
    if r.phase != "retain":
        raise InvalidState("retentions are chosen before the auction opens")
    if len(set(indexes)) != len(indexes):
        raise InvalidState("each player once")
    token = "k" + ",".join(map(str, indexes))
    return replay(deck, _next_state(r, r.tokens + [token]))


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
        return replay(deck, _next_state(r, tokens))
    if ceiling < r.lot.base:
        raise InvalidState(f"the base price is {au.crore(r.lot.base)}")
    ceiling = min(ceiling, r.you.max_bid())
    log = au.preview(r.auction, r.lot, au.ROUNDS[r.round_no], ceiling)
    leading = bool(log) and log[-1].team == r.you.index
    tokens.append(str(ceiling) if (done or leading) else f"{ceiling}o")
    return replay(deck, _next_state(r, tokens))


def pass_lots(deck: Deck, state: str, scope: str) -> Replay:
    """Not interested: in this lot, the rest of this set, or everything left."""
    r = replay(deck, state)
    if r.phase != "bid":
        raise InvalidState("no lot is waiting for a bid")
    tokens = list(r.tokens)
    if r.open_ceiling is not None:
        tokens[-1] = tokens[-1][:-1]            # close what was bid; it stands
        if scope == "lot":
            return replay(deck, _next_state(r, tokens))
    elif scope == "lot":
        tokens.append("0")
        return replay(deck, _next_state(r, tokens))
    tokens.append("pall" if scope == "all" else f"p{r.lot.set_code}")
    return replay(deck, _next_state(r, tokens))


def rtm(deck: Deck, state: str, yes: bool, price: int | None = None) -> Replay:
    """Answer whichever Right to Match question is open: play the card, match the raise,
    or (as the winner) name a final raise -- `price`, or none if not `yes`."""
    r = replay(deck, state)
    if r.phase == "rtm_use":
        token = "r1" if yes else "r0"
    elif r.phase == "rtm_match":
        token = "m1" if yes else "m0"
    elif r.phase == "rtm_raise":
        raised = r.rtm_price if not yes or price is None else price
        if raised < r.rtm_price:
            raise InvalidState("a raise cannot be lower than the hammer price")
        token = f"x{raised}"
    else:
        raise InvalidState("no Right to Match is waiting")
    return replay(deck, _next_state(r, r.tokens + [token]))


def fill(deck: Deck, state: str, index: int) -> Replay:
    r = replay(deck, state)
    if r.phase != "fill":
        raise InvalidState("nothing to fill")
    if not 0 <= index < len(r.fill_options):
        raise InvalidState("no such player")
    return replay(deck, _next_state(r, r.tokens + [f"f{index}"]))


def choose_twelve(deck: Deck, state: str, order: list[int], impact: int) -> Replay:
    r = replay(deck, state)
    if r.phase not in ("twelve", "ready"):
        raise InvalidState("the auction is not over")
    tokens = [t for t in r.tokens if not t.startswith("t")]
    return replay(deck, _next_state(r, tokens + ["t" + ",".join(map(str, order + [impact]))]))


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
