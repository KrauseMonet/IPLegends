"""Bingo -- a 3x3 grid of "name a player who is both". [A182]

Three criteria down the side, three across the top, and each of the nine cells wants one
player who satisfies BOTH its row and its column. A criterion is a franchise ("played for
Mumbai Indians") or a career fact ("hit an IPL century", "overseas player").

A pure function of the puzzle facts (`game.puzzle_facts`) and a seed: no database, no
stored state. The same seed is the same grid, which is what makes a daily and a shareable
practice grid the same mechanism (A125's reasoning, and A62's).

RULES, ratified before building:

    - nine guesses in total, not nine per cell
    - a player may fill only ONE cell: once placed, he is spent
    - a cell closes the moment it is filled
    - a wrong guess costs a guess; so does nothing else
    - SCORE: a correct cell scores by how few players fit it, so a grid is won on its hard
      cells. Rarity here is the cell's own answer count, computed from the archive --
      not "what fraction of players picked him", which needs a results table and is
      deliberately left for later.

**The grid generator rejects, it does not repair.** It samples, measures every cell against
the facts, and keeps a grid only if no cell is empty-ish (`MIN_ANSWERS`) and few are barely
solvable (`MAX_HARD_CELLS`). Measured over 14,000 random draws, 83% clear a floor of three
answers, which is why rejection is cheap and no cleverer construction is needed.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable

from game.puzzle_facts import PlayerFacts

GUESSES = 9
MIN_ANSWERS = 3          # every cell has at least this many valid players
HARD_CELL = 4            # a cell with this many answers or fewer is "hard"
MAX_HARD_CELLS = 2       # ... and a grid may hold at most this many of them
RARE_CELL = 5            # a correct cell this small is marked rare in the shared grid
ROW_MIN_PLAYERS = 60     # only a franchise this big heads a ROW
COL_MIN_PLAYERS = 45     # ... and one this big heads a COLUMN. Below it (Kochi 20, Gujarat
                         # Lions 29, Rising Pune 34) a franchise is a quiz for historians,
                         # not a puzzle for a Thursday. Stat criteria are not filtered.
MAX_ATTEMPTS = 5000
# Daily seeds are date ordinals (about 740,000). Practice seeds start well above that so
# the two can never be mistaken for one another.
PRACTICE_SEED_FLOOR = 10_000_000
PRACTICE_SEED_CEIL = 2_000_000_000


class BingoError(ValueError):
    """A move the rules refuse. The message is for the player."""


@dataclass(frozen=True)
class Criterion:
    key: str
    label: str
    kind: str                                  # "franchise" | "stat"
    test: Callable[[PlayerFacts], bool] = field(compare=False, repr=False)
    # Two criteria of one group are not offered together: "Overseas" beside "Indian" on one
    # axis is a grid with half its cells empty by definition.
    group: str | None = None


def _franchise(name: str) -> Criterion:
    return Criterion(f"f:{name}", name, "franchise", lambda p, n=name: n in p.franchises)


STATS: tuple[Criterion, ...] = (
    Criterion("overseas", "Overseas player", "stat", lambda p: p.overseas is True, "nat"),
    Criterion("indian", "Indian player", "stat", lambda p: p.overseas is False, "nat"),
    Criterion("keeper", "Wicketkeeper", "stat", lambda p: p.keeper),
    Criterion("hundred", "Scored an IPL century", "stat", lambda p: p.hundreds >= 1),
    Criterion("hundreds2", "Scored 2+ IPL centuries", "stat", lambda p: p.hundreds >= 2),
    Criterion("haul4", "Took 4+ wickets in a match", "stat",
              lambda p: p.four_wicket_hauls >= 1),
    Criterion("season500", "500+ runs in a season", "stat",
              lambda p: p.best_season_runs >= 500),
    Criterion("season20w", "20+ wickets in a season", "stat",
              lambda p: p.best_season_wickets >= 20),
    Criterion("sixes100", "100+ IPL sixes", "stat", lambda p: p.sixes >= 100),
    Criterion("runs1000", "1,000+ IPL runs", "stat", lambda p: p.runs >= 1000),
    Criterion("wkts100", "100+ IPL wickets", "stat", lambda p: p.wickets >= 100),
    Criterion("y2008", "Played in 2008", "stat", lambda p: 2008 in p.seasons),
    Criterion("y2026", "Played in 2026", "stat", lambda p: 2026 in p.seasons),
    Criterion("seasons5", "Played 5+ seasons", "stat", lambda p: len(p.seasons) >= 5),
    Criterion("clubs3", "Played for 3+ franchises", "stat",
              lambda p: len(p.franchises) >= 3),
)


class Facts:
    """The player table and the criteria's memberships, computed once."""

    def __init__(self, players: list[PlayerFacts]):
        self.players = {p.person_id: p for p in players}
        names = sorted({f for p in players for f in p.franchises})
        self.criteria: tuple[Criterion, ...] = tuple(_franchise(n) for n in names) + STATS
        self.by_key = {c.key: c for c in self.criteria}
        self._members = {c.key: frozenset(p.person_id for p in players if c.test(p))
                         for c in self.criteria}

    def members(self, criterion: Criterion) -> frozenset[str]:
        return self._members[criterion.key]


@dataclass(frozen=True)
class Grid:
    seed: int
    rows: tuple[Criterion, ...]
    cols: tuple[Criterion, ...]
    cells: tuple[frozenset[str], ...]          # row-major: cell = row * 3 + col

    def points(self, cell: int) -> int:
        """What a correct cell is worth: 100 / sqrt(answers). Three answers is 58 and
        eighteen is 24, so the hard cells carry the score without the easy ones scoring
        nothing. Rounded so the number on the screen is the number added up."""
        return round(100 / math.sqrt(len(self.cells[cell])))

    def is_rare(self, cell: int) -> bool:
        return len(self.cells[cell]) <= RARE_CELL


def new_seed() -> int:
    return random.randrange(PRACTICE_SEED_FLOOR, PRACTICE_SEED_CEIL)


def daily_seed(day) -> int:
    """The date and nothing else -- not `hash()`, which CPython salts per process (A125)."""
    return day.toordinal()


def make_grid(facts: Facts, seed: int) -> Grid:
    rng = random.Random(f"bingo:{seed}")
    row_pool = [c for c in facts.criteria
                if c.kind == "franchise" and len(facts.members(c)) >= ROW_MIN_PLAYERS]
    col_pool = [c for c in facts.criteria
                if c.kind == "stat" or len(facts.members(c)) >= COL_MIN_PLAYERS]
    for _ in range(MAX_ATTEMPTS):
        rows = tuple(rng.sample(row_pool, 3))
        cols = tuple(rng.sample(col_pool, 3))
        if set(rows) & set(cols):
            continue
        groups = [c.group for c in cols if c.group]
        if len(groups) != len(set(groups)):
            continue
        cells = tuple(facts.members(r) & facts.members(c) for r in rows for c in cols)
        sizes = [len(x) for x in cells]
        if min(sizes) < MIN_ANSWERS or sum(s <= HARD_CELL for s in sizes) > MAX_HARD_CELLS:
            continue
        return Grid(seed, rows, cols, cells)
    raise RuntimeError(f"no playable grid in {MAX_ATTEMPTS} attempts for seed {seed}")


# --- play ---------------------------------------------------------------------------------

@dataclass
class State:
    placed: dict[int, str] = field(default_factory=dict)         # cell -> person_id
    wrong: list[tuple[int, str]] = field(default_factory=list)
    score: int = 0

    @property
    def guesses_used(self) -> int:
        return len(self.placed) + len(self.wrong)

    @property
    def guesses_left(self) -> int:
        return GUESSES - self.guesses_used

    @property
    def finished(self) -> bool:
        return len(self.placed) == 9 or self.guesses_left <= 0


def guess(grid: Grid, facts: Facts, state: State, cell: int, person_id: str) -> bool:
    """Apply one guess to `state` in place and say whether it was right. Raises BingoError
    for a move the rules refuse, which costs nothing and changes nothing."""
    if state.finished:
        raise BingoError("This grid is finished.")
    if not 0 <= cell < 9:
        raise BingoError("There is no such cell.")
    if person_id not in facts.players:
        raise BingoError("Nobody by that name has played in the IPL.")
    if cell in state.placed:
        raise BingoError("That cell is already filled.")
    if person_id in state.placed.values():
        raise BingoError(f"{facts.players[person_id].name} is already on the grid.")
    if (cell, person_id) in state.wrong:
        raise BingoError(f"You have already tried {facts.players[person_id].name} there.")
    if person_id in grid.cells[cell]:
        state.placed[cell] = person_id
        state.score += grid.points(cell)
        return True
    state.wrong.append((cell, person_id))
    return False


def replay(grid: Grid, facts: Facts, guesses: list[tuple[int, str]]) -> State:
    """The state after a list of guesses, from scratch. The client holds the list and the
    server re-derives everything from it, so nothing is stored (A62)."""
    state = State()
    for cell, person_id in guesses:
        guess(grid, facts, state, cell, person_id)
    return state


def reveal(grid: Grid, facts: Facts, per_cell: int = 6) -> list[dict]:
    """For each cell, how many players fit and the best-known few of them."""
    out = []
    for cell in grid.cells:
        ranked = sorted((facts.players[pid] for pid in cell),
                        key=lambda p: (-p.prominence, p.name, p.person_id))
        out.append({"n": len(cell), "sample": [p.name for p in ranked[:per_cell]]})
    return out


# --- sharing ------------------------------------------------------------------------------

SHARE_HOST = "iplegends.vercel.app/bingo"


def share_text(grid: Grid, state: State, label: str, *, daily: bool) -> str:
    """The result as a grid of squares, with NO names in it: everybody who plays the same
    grid is hunting the same players, so naming one would hand a reader the answer instead
    of the challenge (A129's rule for the daily). 🟩 a cell filled, 🟪 a RARE cell filled,
    ⬛ a cell left empty."""
    lines = [f"Almanack Bingo · {label}"]
    for r in range(3):
        row = ""
        for c in range(3):
            cell = r * 3 + c
            row += ("🟪" if grid.is_rare(cell) else "🟩") if cell in state.placed else "⬛"
        lines.append(row)
    lines.append(f"{len(state.placed)}/9 · {state.score} pts")
    lines.append(SHARE_HOST if daily else f"{SHARE_HOST}?seed={grid.seed}")
    return "\n".join(lines)
