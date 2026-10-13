"""Three to Make -- name three players whose stats add up to a target. [A196]

A daily target ("4,246 IPL career runs") and ninety seconds. Pick three different players
whose figures for that stat add up as close to it as you can. Their figures stay hidden until
you lock in, so it is a memory game rather than a sum. The target is always the total of
three real well-known players, so an exact answer always exists.

**Distance decides the board, and rarity and time only break ties.** Measured before building:
an exact answer is never unique. Over 300 targets, the median career-runs target had 3,336
exact trios among all 804 players (149 among the 197 well known), and wickets and sixes had tens
of thousands. So a board ranked on distance alone would be a tie for first between everybody
who knew the numbers. Ties go to the trio of players fewer others picked (`game.rarity`), then
to the faster lock-in. Rarity is a TIE-BREAK here, never added to the score: an exact trio of
famous names always beats an unusual trio that missed.

**The stat rotates** between career runs, career wickets, career sixes and best single-season
runs. Hundreds, fifties and four-wicket hauls were measured and left out: their numbers are so
small that millions of trios hit any target and nearly everybody would tie.

Pure like the other puzzle games: a function of the puzzle facts and a seed, no database. The
clock is the caller's (the page's in practice, the server's in a ranked attempt; see
`web/puzzle_results.py`).
"""

from __future__ import annotations

import datetime
import random
from collections import Counter
from dataclasses import dataclass
from functools import cached_property

from game.bingo import PRACTICE_SEED_FLOOR, daily_seed, new_seed
from game.guess import pool
from game.puzzle_facts import PlayerFacts
from game.site import SITE_HOST, SITE_NAME

__all__ = ["daily_seed", "new_seed", "PRACTICE_SEED_FLOOR"]

LIMIT_SECONDS = 90
LIMIT_MS = LIMIT_SECONDS * 1000
PICKS = 3
MAX_SCORE = 1000              # an exact trio
SHARE_HOST = f"{SITE_HOST}/trio"


@dataclass(frozen=True)
class Stat:
    key: str                  # the PlayerFacts field
    label: str                # "IPL career runs"
    unit: str                 # "runs"
    floor: int                # a player must have at least this to be one of the three that make a target


# The order is the daily rotation. Floors keep a target made of men known for THIS stat: a
# wickets target built from Kohli's 4 wickets would be a riddle, not a memory.
STATS = (
    Stat("runs", "IPL career runs", "runs", 1000),
    Stat("wickets", "IPL career wickets", "wickets", 40),
    Stat("sixes", "IPL career sixes", "sixes", 40),
    Stat("best_season_runs", "runs in their best IPL season", "runs", 350),
)
STAT_BY_KEY = {s.key: s for s in STATS}


class TrioError(ValueError):
    """A move the rules refuse. The message is for the player."""


@dataclass(frozen=True)
class Puzzle:
    seed: int
    stat: Stat
    target: int
    example: tuple[str, str, str]     # one trio that makes it exactly: the three it was built from


class Trio:
    """The players, built once per set of facts."""

    def __init__(self, players: dict[str, PlayerFacts]):
        self.players = players

    def value(self, stat: Stat, pid: str) -> int:
        return getattr(self.players[pid], stat.key)

    @cached_property
    def makers(self) -> dict[str, list[str]]:
        """Per stat, the well-known players a target may be built from, in a fixed order."""
        known = pool(self.players)
        return {s.key: sorted(p.person_id for p in known if getattr(p, s.key) >= s.floor) for s in STATS}

    def puzzle(self, seed: int) -> Puzzle:
        """A daily seed rotates the stat day by day; a practice seed draws one. Either way the
        target is the total of three distinct well-known players, so an exact answer exists.
        Never `hash()` (A125)."""
        if seed < PRACTICE_SEED_FLOOR:
            stat = STATS[seed % len(STATS)]
        else:
            stat = random.Random(f"trio-stat:{seed}").choice(STATS)
        makers = self.makers[stat.key]
        if len(makers) < PICKS:
            raise RuntimeError(f"too few players to build a {stat.key} target")
        three = tuple(sorted(random.Random(f"trio:{seed}").sample(makers, PICKS)))
        return Puzzle(seed, stat, sum(self.value(stat, p) for p in three), three)

    @cached_property
    def _counts(self) -> dict[str, Counter]:
        return {s.key: Counter(getattr(p, s.key) for p in self.players.values()) for s in STATS}

    def exact_count(self, stat: Stat, target: int) -> int:
        """How many trios of distinct players (from every player, not only the well known) hit
        `target` exactly. Counted over VALUES with their multiplicities, so it is O(values^2)
        rather than O(players^3)."""
        n = self._counts[stat.key]
        vals = sorted(n)
        total = 0
        for i, a in enumerate(vals):
            if 3 * a > target:
                break
            for b in vals[i:]:
                c = target - a - b
                if c < b:
                    break
                if c not in n:
                    continue
                if a < b < c:
                    total += n[a] * n[b] * n[c]
                elif a == b < c:
                    total += n[a] * (n[a] - 1) // 2 * n[c]
                elif a < b == c:
                    total += n[a] * n[b] * (n[b] - 1) // 2
                else:
                    total += n[a] * (n[a] - 1) * (n[a] - 2) // 6
        return total


# --- one attempt -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Result:
    picks: tuple[str, ...]            # as chosen; empty if the clock ran out first
    values: tuple[int, ...]
    total: int | None
    distance: int | None              # None: no trio was locked in
    t: int                            # ms from the start of the clock to the lock-in (or the limit)
    ended: str | None                 # None: locked in; "time": the clock ran out with no trio

    @property
    def exact(self) -> bool:
        return self.distance == 0


def check_picks(g: Trio, ids: list[str], *, complete: bool) -> tuple[str, ...]:
    """Three different players who have played, or (`complete=False`) up to three -- the
    selection a ranked attempt holds while the clock runs."""
    if len(ids) > PICKS:
        raise TrioError("Pick three players.")
    if complete and len(ids) != PICKS:
        raise TrioError("Pick three players.")
    if len(set(ids)) != len(ids):
        raise TrioError("Pick three DIFFERENT players.")
    for pid in ids:
        if pid not in g.players:
            raise TrioError("Nobody by that name has played in the IPL.")
    return tuple(ids)


def play(g: Trio, puzzle: Puzzle, ids: list[str] | None, t: int) -> Result:
    """Lock in `ids` at `t` ms, or (`ids` None) the clock ran out with no trio locked in."""
    if t < 0:
        raise TrioError("That time is not possible.")
    if ids is None:
        return Result((), (), None, None, min(t, LIMIT_MS), "time")
    if t > LIMIT_MS:
        raise TrioError("Time is up.")
    picks = check_picks(g, ids, complete=True)
    values = tuple(g.value(puzzle.stat, p) for p in picks)
    total = sum(values)
    return Result(picks, values, total, abs(total - puzzle.target), t, None)


def score(puzzle: Puzzle, result: Result) -> int:
    """How close, out of `MAX_SCORE`: 1000 for exact, and down by the miss as a share of the
    target (rounded UP, so any miss scores below an exact trio). This is what the overall board
    puts on one scale with the other games; the game's own board ranks on the distance itself."""
    if result.distance is None:
        return 0
    off = -(-MAX_SCORE * result.distance // puzzle.target)     # ceil
    return max(0, MAX_SCORE - off)


def rank_key(distance: int | None, rarity_bonus: int, elapsed_ms: int | None) -> tuple:
    """Best first: nearer the target, then the rarer trio, then the quicker lock-in. No trio
    sorts after every trio."""
    return (distance is None, distance if distance is not None else 0, -rarity_bonus,
            elapsed_ms if elapsed_ms is not None else LIMIT_MS)


# --- words -----------------------------------------------------------------------------------

def fmt(n: int) -> str:
    return f"{n:,}"


def verdict(result: Result, puzzle: Puzzle) -> str:
    if result.distance is None:
        return "No trio in time"
    if result.distance == 0:
        return "Bang on"
    over = "over" if result.total > puzzle.target else "short"
    return f"{fmt(result.distance)} {over}"


def clock(ms: int) -> str:
    s = max(0, ms) // 1000
    return f"{s // 60}:{s % 60:02d}"


def share_text(puzzle: Puzzle, result: Result, label: str, *, daily: bool) -> str:
    """The target and how close, never the three names: everybody has the same target, and the
    names would hand a reader an answer (A129)."""
    mark = "🎯" if result.exact else "⏱️" if result.distance is None else "📏"
    lines = [f"{SITE_NAME} · Three to Make · {label}",
             f"{fmt(puzzle.target)} {puzzle.stat.label}",
             f"{mark} {verdict(result, puzzle)}" + ("" if result.distance is None else f" · {clock(result.t)}"),
             SHARE_HOST if daily else f"{SHARE_HOST}?seed={puzzle.seed}"]
    return "\n".join(lines)


def label_for(seed: int) -> tuple[str, bool, datetime.date | None]:
    if seed < PRACTICE_SEED_FLOOR:
        try:
            day = datetime.date.fromordinal(seed)
        except ValueError:
            raise TrioError("That is not a puzzle.") from None
        return f"{day.day} {day.strftime('%b %Y')}", True, day
    return f"Practice {seed}", False, None
