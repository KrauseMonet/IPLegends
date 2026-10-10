"""Common Ground -- who played with BOTH of them? [A188]

Two well-known players are shown, and in four minutes you name everyone who was a teammate
of both. Each one found scores; a wrong guess costs a little; and finding the whole set wins
a bonus for every second left on the clock. It replaces Teammate Chain (A184).

**Why a chosen pair and not any pair?** Measured over all 19,306 pairs of the 197 well-known
players: the median pair has 17 common teammates and a quarter have 30 or more, which nobody
can name in four minutes, and 0.9% have none. "Name them all" only means something when the
set is small enough to finish, so the puzzle is drawn only from pairs whose full set holds
`MIN_ANSWERS`-`MAX_ANSWERS` players -- 3,037 of the 19,306, about eight years of dailies.
EVERY member of the set is a valid answer, not only the famous ones, so nobody is stuck on a
name the game will not accept; and at least `MIN_KNOWN` of them are well known, so the set is
never all fringe players.

"Teammates" means the same franchise in the same season (`PlayerFacts.squads`), the way a fan
remembers a dressing room -- not "played in the same match". Two players can be common
teammates across different years: he played with A in 2010 and with B in 2016.

**The clock lives outside this module.** A guess carries `t`, milliseconds since the clock
started, and this module only checks that the times are in order and inside the limit. For an
anonymous practice game the browser supplies them; a ranked attempt replaces them with the
server's own (A188 phase 2). Either way the scoring is a pure function of the moves and the
puzzle, so a result can be recomputed from what was stored.

Stateless like Bingo and Guess the Player: no database, and the move list is the whole state,
replayed by the server on each request (A62).
"""

from __future__ import annotations

import datetime
import random
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cached_property

from game.bingo import PRACTICE_SEED_FLOOR, daily_seed, new_seed
from game.guess import pool
from game.puzzle_facts import PlayerFacts
from game.site import SITE_HOST, SITE_NAME

__all__ = ["daily_seed", "new_seed", "PRACTICE_SEED_FLOOR"]

LIMIT_SECONDS = 240
LIMIT_MS = LIMIT_SECONDS * 1000
POINTS_PER_TEAMMATE = 100
PENALTY_PER_WRONG = 20          # so the search box cannot simply be spammed
BONUS_PER_SECOND_LEFT = 1       # paid only for finding the whole set
MIN_ANSWERS, MAX_ANSWERS = 4, 8
MIN_KNOWN, KNOWN_PROMINENCE = 3, 300    # at least three answers a fan would place
MAX_GUESSES = 60                # a bound on the replayed list, well past any real attempt
SHARE_HOST = f"{SITE_HOST}/common"

ONLY_A, ONLY_B, NEITHER = "only_a", "only_b", "neither"


class GroundError(ValueError):
    """A move the rules refuse. The message is for the player."""


# --- who is a teammate ----------------------------------------------------------------------

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

    def common(self, a: str, b: str) -> set[str]:
        """Everyone who was a teammate of both. Neither of the two can be in it: nobody is his own
        teammate (`teammates` drops him), so `a` is missing from a's side and `b` from b's."""
        return self.teammates(a) & self.teammates(b)


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


# --- the puzzle ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Puzzle:
    a: str
    b: str
    answers: frozenset[str]


class Ground:
    """A World plus the pairs a puzzle can be drawn from -- built once per set of facts."""

    def __init__(self, players: dict[str, PlayerFacts]):
        self.world = World(players)
        self.players = players

    @cached_property
    def pairs(self) -> list[tuple[str, str]]:
        """Every playable pair, in a fixed order (ids sorted, a before b)."""
        ids = sorted(p.person_id for p in pool(self.players))
        mates = {i: self.world.teammates(i) for i in ids}
        out = []
        for k, a in enumerate(ids):
            for b in ids[k + 1:]:
                common = (mates[a] & mates[b]) - {a, b}
                if not MIN_ANSWERS <= len(common) <= MAX_ANSWERS:
                    continue
                known = sum(self.players[x].prominence >= KNOWN_PROMINENCE for x in common)
                if known >= MIN_KNOWN:
                    out.append((a, b))
        return out

    @cached_property
    def order(self) -> list[tuple[str, str]]:
        """The pairs in the fixed shuffled order the daily walks."""
        order = list(self.pairs)
        random.Random("ground-order").shuffle(order)
        return order

    def puzzle(self, seed: int) -> Puzzle:
        """A daily seed walks a fixed shuffle of the pairs, so none returns until all have had
        a day; a practice seed draws at random. Never `hash()` (A125)."""
        if not self.pairs:
            raise RuntimeError("no pair of players has a playable set of common teammates")
        if seed < PRACTICE_SEED_FLOOR:
            a, b = self.order[seed % len(self.order)]
        else:
            a, b = random.Random(f"ground:{seed}").choice(self.pairs)
        if random.Random(f"ground-flip:{seed}").random() < 0.5:
            a, b = b, a                       # which of the two is shown first is not a clue
        return Puzzle(a, b, frozenset(self.world.common(a, b)))


# --- one attempt -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Move:
    id: str
    t: int          # milliseconds since the clock started


@dataclass
class State:
    puzzle: Puzzle
    moves: list[Move] = field(default_factory=list)        # every guess, in order
    hits: list[bool] = field(default_factory=list)         # parallel to `moves`
    kinds: list[str | None] = field(default_factory=list)  # for a miss: how it missed
    ended: str | None = None                               # None, "gave_up" or "time"
    ended_t: int = 0

    @property
    def total(self) -> int:
        return len(self.puzzle.answers)

    @property
    def found(self) -> list[Move]:
        return [m for m, h in zip(self.moves, self.hits) if h]

    @property
    def wrong(self) -> list[tuple[Move, str]]:
        return [(m, k) for m, h, k in zip(self.moves, self.hits, self.kinds) if not h]

    @property
    def complete(self) -> bool:
        return len(self.found) == self.total

    @property
    def finished(self) -> bool:
        return self.complete or self.ended is not None or len(self.moves) >= MAX_GUESSES

    @property
    def last_t(self) -> int:
        return self.moves[-1].t if self.moves else 0

    @property
    def elapsed_ms(self) -> int:
        """How long the attempt took: to the last teammate if it finished the set, otherwise to
        the moment it ended -- the limit if the clock ran out, the player's own stop if not."""
        if self.complete:
            return self.found[-1].t
        return max(self.ended_t, self.last_t)       # `end` clamps a time-up to the limit


def new_state(puzzle: Puzzle) -> State:
    return State(puzzle)


def guess(g: Ground, state: State, person_id: str, t: int) -> bool:
    """Apply one guess in place: True if he was a teammate of both. Raises GroundError for a
    move the rules refuse, which costs nothing and changes nothing."""
    if state.finished:
        raise GroundError("This puzzle is over.")
    if person_id not in g.players:
        raise GroundError("Nobody by that name has played in the IPL.")
    p = state.puzzle
    if person_id in (p.a, p.b):
        raise GroundError(f"{g.players[person_id].name} is one of the two you are linking.")
    if any(m.id == person_id for m in state.moves):
        raise GroundError(f"You have already tried {g.players[person_id].name}.")
    if t < state.last_t:
        raise GroundError("Those guesses are out of order.")
    if t > LIMIT_MS:
        raise GroundError("Time is up.")
    hit = person_id in p.answers
    kind = None
    if not hit:
        with_a = bool(g.world.shared(p.a, person_id))
        with_b = bool(g.world.shared(p.b, person_id))
        kind = ONLY_A if with_a and not with_b else ONLY_B if with_b and not with_a else NEITHER
    state.moves.append(Move(person_id, t))
    state.hits.append(hit)
    state.kinds.append(kind)
    return hit


def end(state: State, t: int) -> None:
    """The attempt stops: the clock ran out (`t` at the limit) or the player gave up. A no-op
    on a finished attempt, so a late 'time is up' cannot disturb a set that was completed."""
    if state.finished:
        return
    if t < state.last_t:
        raise GroundError("Those guesses are out of order.")
    state.ended = "time" if t >= LIMIT_MS else "gave_up"
    state.ended_t = min(t, LIMIT_MS)


def replay(g: Ground, puzzle: Puzzle, moves: list[Move], *, end_t: int | None = None) -> State:
    state = new_state(puzzle)
    for m in moves:
        guess(g, state, m.id, m.t)
    if end_t is not None:
        end(state, end_t)
    return state


# --- scoring ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Score:
    found: int
    wrong: int
    total: int
    base: int
    penalty: int
    bonus: int

    @property
    def points(self) -> int:
        return max(0, self.base - self.penalty) + self.bonus


def score(state: State) -> Score:
    """A pure function of the moves: 100 for each teammate, 20 off for each wrong guess (never
    below nothing), and -- only if the whole set was found -- a point per second left."""
    found, wrong = len(state.found), len(state.wrong)
    bonus = 0
    if state.complete:
        bonus = max(0, (LIMIT_MS - state.found[-1].t) // 1000) * BONUS_PER_SECOND_LEFT
    return Score(found=found, wrong=wrong, total=state.total,
                 base=found * POINTS_PER_TEAMMATE, penalty=wrong * PENALTY_PER_WRONG, bonus=bonus)


def clock(ms: int) -> str:
    s = max(0, ms) // 1000
    return f"{s // 60}:{s % 60:02d}"


# --- once it is over -------------------------------------------------------------------------

def answer_lines(g: Ground, state: State, pid: str) -> tuple[list[str], list[str]]:
    """The squads this player shared with A, and with B, as readable lines."""
    p = state.puzzle
    return describe(g.world.shared(p.a, pid)), describe(g.world.shared(p.b, pid))


def unfound(g: Ground, state: State) -> list[str]:
    """The teammates that were not named, best known first -- shown once it is over so a
    miss teaches rather than only ends."""
    got = {m.id for m in state.found}
    left = [g.players[i] for i in state.puzzle.answers if i not in got]
    left.sort(key=lambda p: (-p.prominence, p.name, p.person_id))
    return [p.person_id for p in left]


# --- sharing ---------------------------------------------------------------------------------

def share_text(state: State, label: str, *, daily: bool, seed: int) -> str:
    """The attempt as squares -- 🟩 a teammate, 🟥 a miss, ⬛ one never found -- and the score.
    No names: everybody has the same pair, and a name would hand a reader an answer (A129)."""
    marks = ["🟩" if h else "🟥" for h in state.hits]
    marks += ["⬛"] * (state.total - len(state.found))
    sc = score(state)
    tail = f" · {clock(state.found[-1].t)}" if state.complete else ""
    lines = [f"{SITE_NAME} · Common Ground · {label}",
             f"{sc.found}/{sc.total} · {sc.points} pts{tail}", "".join(marks),
             SHARE_HOST if daily else f"{SHARE_HOST}?seed={seed}"]
    return "\n".join(lines)


def label_for(seed: int) -> tuple[str, bool, datetime.date | None]:
    if seed < PRACTICE_SEED_FLOOR:
        try:
            day = datetime.date.fromordinal(seed)
        except ValueError:
            raise GroundError("That is not a puzzle.") from None
        return f"{day.day} {day.strftime('%b %Y')}", True, day
    return f"Practice {seed}", False, None
