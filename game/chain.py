"""Teammate Chain -- how long can your IPL knowledge hold? [A184]

A daily starting player, and from him a chain: name a teammate, then a teammate of THAT
player, and so on. Nobody may appear twice. Each link shows the squad the two shared, and a
name that was never a teammate is a strike. Three strikes end the chain; its length is the
score. You may stop sooner and bank it.

**Why not "link A to B in the fewest steps"?** That was the plan, and measuring the real
graph killed it. The teammate graph is tiny: the average player has 54 teammates, and among
the 197 well-known players 25% of pairs are direct teammates, 74% share exactly one bridge
player, and 0.9% are three steps apart -- none is four. Banning franchises as stepping stones
(three banned clubs: 56% of pairs still one bridge) and forcing every link onto a new club
(identical distribution) did not change that. A puzzle whose answer is nearly always "one
mutual teammate" tests nothing. What a dense graph DOES test is whether you can keep naming
real teammates, which is what this does.

"Teammates" means the same franchise in the same season (`PlayerFacts.squads`), the way a
fan remembers a dressing room -- not "played in the same match".

A pure function of the puzzle facts and a seed, like Bingo and Guess the Player: no
database, no stored state, and the guess list is the whole state, replayed by the server on
each move (A62).
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field

from game.bingo import PRACTICE_SEED_FLOOR, daily_seed, new_seed
from game.guess import pool
from game.puzzle_facts import PlayerFacts

__all__ = ["daily_seed", "new_seed", "PRACTICE_SEED_FLOOR"]

STRIKES = 3
MAX_GUESSES = 120              # a bound on the replayed list, not a goal; well past any real run
SHARE_SQUARES = 40             # a longer chain is shown as its first forty and a count
SHARE_HOST = "iplegends.vercel.app/chain"
HINT_NAMES = 5                 # how many teammates are named once the chain is over


class ChainError(ValueError):
    """A move the rules refuse. The message is for the player."""


class World:
    """The players and who shared a squad with whom, built once."""

    def __init__(self, players: dict[str, PlayerFacts]):
        self.players = players
        self.by_squad: dict[str, set[str]] = defaultdict(set)
        for p in players.values():
            for sq in p.squads:
                self.by_squad[sq].add(p.person_id)

    def shared(self, a: str, b: str) -> list[str]:
        """The squads two players both belonged to, oldest first."""
        both = set(self.players[a].squads) & set(self.players[b].squads)
        return sorted(both, key=lambda sq: (int(sq.rsplit("|", 1)[1]), sq))

    def teammates(self, pid: str) -> set[str]:
        out: set[str] = set()
        for sq in self.players[pid].squads:
            out |= self.by_squad[sq]
        out.discard(pid)
        return out


def describe(squads: list[str]) -> list[str]:
    """Squads as readable lines, grouped by club: 'Chennai Super Kings 2010-2012, 2014'."""
    years: dict[str, list[int]] = defaultdict(list)
    # Oldest first, whatever order the caller used: clubs appear by their earliest year.
    for sq in sorted(squads, key=lambda sq: (int(sq.rsplit("|", 1)[1]), sq)):
        club, year = sq.rsplit("|", 1)
        years[club].append(int(year))
    out = []
    for club, ys in years.items():
        ys.sort()
        runs, start = [], ys[0]
        for prev, y in zip(ys, ys[1:] + [None]):
            if y is None or y != prev + 1:
                runs.append(str(start) if start == prev else f"{start}-{prev}")
                start = y
        out.append(f"{club} {', '.join(runs)}")
    return out


def start_player(players: dict[str, PlayerFacts], seed: int) -> PlayerFacts:
    """Where the chain begins. A daily seed walks a fixed shuffle of the recognisable players
    (as Guess the Player's does, with its OWN shuffle so the two games do not pair up);
    a practice seed draws at random. Never `hash()` (A125)."""
    names = pool(players)
    if not names:
        raise RuntimeError("no player is eligible to start a chain")
    if seed < PRACTICE_SEED_FLOOR:
        order = list(names)
        random.Random("chain-order").shuffle(order)
        return order[seed % len(order)]
    return random.Random(f"chain:{seed}").choice(names)


@dataclass
class State:
    chain: list[str]                                   # the start player, then each link
    shared: list[list[str]] = field(default_factory=list)       # per link: the squads shared
    guesses: list[str] = field(default_factory=list)
    wrong: list[tuple[str, str]] = field(default_factory=list)  # (the end he was tried from, him)
    stopped: bool = False

    @property
    def strikes(self) -> int:
        return len(self.wrong)

    @property
    def links(self) -> int:
        return len(self.chain) - 1

    @property
    def end(self) -> str:
        return self.chain[-1]

    @property
    def out_of_guesses(self) -> bool:
        return len(self.guesses) >= MAX_GUESSES

    @property
    def finished(self) -> bool:
        return self.stopped or self.strikes >= STRIKES or self.out_of_guesses

    def tried_here(self) -> set[str]:
        return {pid for end, pid in self.wrong if end == self.end}

    def clubs(self) -> int:
        """How many different franchises the chain has been through: a secondary stat."""
        seen = {sq.rsplit("|", 1)[0] for squads in self.shared for sq in squads}
        return len(seen)


def new_state(start: PlayerFacts) -> State:
    return State(chain=[start.person_id])


def guess(world: World, state: State, person_id: str) -> bool:
    """Apply one guess in place: True if it extended the chain, False if it was a strike.
    Raises ChainError for a move the rules refuse, which costs nothing and changes nothing."""
    if state.finished:
        raise ChainError("This chain is over.")
    if person_id not in world.players:
        raise ChainError("Nobody by that name has played in the IPL.")
    if person_id in state.chain:
        raise ChainError(f"{world.players[person_id].name} is already in your chain.")
    if person_id in state.tried_here():
        raise ChainError(f"You have already tried {world.players[person_id].name} here.")
    state.guesses.append(person_id)
    squads = world.shared(state.end, person_id)
    if squads:
        state.chain.append(person_id)
        state.shared.append(squads)
        return True
    state.wrong.append((state.end, person_id))
    return False


def replay(world: World, start: PlayerFacts, guesses: list[str], stopped: bool = False) -> State:
    state = new_state(start)
    for pid in guesses:
        guess(world, state, pid)
    if stopped and not state.finished:
        state.stopped = True
    return state


def missed(world: World, state: State) -> list[str]:
    """Teammates the chain's last player had that were not already in it, best known first --
    shown once the chain is over, so a strike teaches rather than only ends."""
    left = world.teammates(state.end) - set(state.chain)
    ranked = sorted((world.players[p] for p in left),
                    key=lambda p: (-p.prominence, p.name, p.person_id))
    return [p.name for p in ranked[:HINT_NAMES]]


# --- sharing ------------------------------------------------------------------------------

def share_text(world: World, state: State, label: str, *, daily: bool, seed: int) -> str:
    """The chain's rhythm as squares in the order the guesses were made -- 🟩 a link, 🟥 a
    strike -- and no names: everybody has the same starting player, and naming a link would
    hand a reader a teammate to copy (A129)."""
    # Rebuilt by replaying, so the order of links and strikes is exactly what happened.
    walk = new_state(world.players[state.chain[0]])
    marks = ["🟩" if guess(world, walk, pid) else "🟥" for pid in state.guesses]
    shown = "".join(marks[:SHARE_SQUARES])
    if len(marks) > SHARE_SQUARES:
        shown += f" +{len(marks) - SHARE_SQUARES}"
    plural = "link" if state.links == 1 else "links"
    lines = [f"Almanack Chain · {label} · {state.links} {plural}", shown or "⬛",
             (f"{state.clubs()} club" + ("" if state.clubs() == 1 else "s")) if state.links else "",
             SHARE_HOST if daily else f"{SHARE_HOST}?seed={seed}"]
    return "\n".join(line for line in lines if line)
