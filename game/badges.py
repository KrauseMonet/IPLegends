"""Puzzle badges -- what a player's finished dailies add up to [A188].

**Derived, never stored (A19).** A badge is a statement about a player's results, so it is
computed from them whenever it is shown. Stored, it would be a second copy of the results that a
later change to a rule (or to a score) would leave stale; derived, changing a threshold here
re-awards every badge correctly and nothing needs migrating.

Pure: no database, no clock. `web/puzzle_results.py` supplies the results and the days on which
the player made a rare pick, and the profile route turns the answer into JSON.

Thresholds are declared game design (like `REPUTATION` and `ALLROUNDER_RUNS`), each a named
constant: there is nothing to measure them against, only a judgement about what is worth a badge.
Each badge is a statement a player can verify by reading it, which is why none is a percentile.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable
from dataclasses import dataclass

GAMES = ("bingo", "guess", "xi", "common")

STREAKS = (7, 30, 100)                 # days running with any puzzle finished
SHARP_EYE_GUESSES = 2                  # Guess the Player found in this many or fewer
LIGHTNING_MS = 90_000                  # a whole Common Ground set inside a minute and a half
REGULAR_AT, VETERAN_AT = 25, 100       # puzzles finished, in total
RARE_SHARE_ONE_IN = 20                 # a pick fewer than 1 in this many players made


@dataclass(frozen=True)
class Result:
    """One finished daily, as stored (migration 046)."""
    game: str
    day: datetime.date
    score: int
    solved: bool
    elapsed_ms: int | None
    detail: dict


@dataclass(frozen=True)
class Badge:
    key: str
    name: str
    blurb: str
    group: str
    target: int                        # what earning it takes (1 for a one-off feat)
    progress: int                      # how far along, never above `target`
    earned_on: datetime.date | None    # the day it was first earned; None while locked

    @property
    def earned(self) -> bool:
        return self.earned_on is not None


def best_run(days: Iterable[datetime.date]) -> int:
    """The longest run of consecutive calendar days -- the next DAY, not the next day-of-month
    (31 August to 1 September is one day apart)."""
    run = best = 0
    prev = None
    for d in sorted(set(days)):
        run = run + 1 if prev is not None and (d - prev).days == 1 else 1
        best = max(best, run)
        prev = d
    return best


def run_reached(days: Iterable[datetime.date], n: int) -> datetime.date | None:
    """The day a run of `n` consecutive days was FIRST completed, or None if one never was."""
    run = 0
    prev = None
    for d in sorted(set(days)):
        run = run + 1 if prev is not None and (d - prev).days == 1 else 1
        if run >= n:
            return d
        prev = d
    return None


def _first(results: list[Result], test) -> datetime.date | None:
    days = sorted(r.day for r in results if test(r))
    return days[0] if days else None


def _nth(days: list[datetime.date], n: int) -> datetime.date | None:
    return days[n - 1] if len(days) >= n else None


def evaluate(results: list[Result], rare_pick_days: Iterable[datetime.date] = ()) -> list[Badge]:
    """Every badge, earned or not, in a fixed order, with progress toward the ones not yet won."""
    out: list[Badge] = []

    def add(key, name, blurb, group, target, progress, earned_on):
        out.append(Badge(key, name, blurb, group, target,
                         target if earned_on else min(progress, target), earned_on))

    by = lambda g: [r for r in results if r.game == g]
    puzzle_days = [r.day for r in results]
    run = best_run(puzzle_days)

    for n, name in zip(STREAKS, ("Week of puzzles", "Month of puzzles", "A hundred days")):
        add(f"streak_{n}", name, f"Finish a daily puzzle {n} days running.", "Streaks", n, run,
            run_reached(puzzle_days, n))

    add("perfect_grid", "Perfect grid", "Fill all nine Bingo squares without a wrong guess.", "Mastery", 1, 0,
        _first(by("bingo"), lambda r: r.solved and r.detail.get("wrong") == 0))
    add("sharp_eye", "Sharp eye",
        f"Find the mystery player in {SHARP_EYE_GUESSES} guesses or fewer.", "Mastery", 1, 0,
        _first(by("guess"), lambda r: r.solved and r.detail.get("guesses", 99) <= SHARP_EYE_GUESSES))
    add("full_house", "Full house", "Find every common teammate in Common Ground.", "Mastery", 1, 0,
        _first(by("common"), lambda r: r.solved))
    add("lightning", "Lightning",
        f"Find every common teammate in under {LIGHTNING_MS // 1000} seconds.", "Mastery", 1, 0,
        _first(by("common"), lambda r: r.solved and r.elapsed_ms is not None and r.elapsed_ms < LIGHTNING_MS))
    add("full_xi", "The full XI", "Name every player in a Name the XI side.", "Mastery", 1, 0,
        _first(by("xi"), lambda r: r.solved))
    add("flawless_xi", "Flawless XI", "Name the whole side without a single mistake.", "Mastery", 1, 0,
        _first(by("xi"), lambda r: r.solved and r.detail.get("wrong") == 0))

    per_day: dict[datetime.date, set[str]] = {}
    for r in results:
        per_day.setdefault(r.day, set()).add(r.game)
    add("grand_slam", "Grand slam", "Finish all four daily puzzles on the same day.", "Habit", 1, 0,
        next((d for d in sorted(per_day) if per_day[d] >= set(GAMES)), None))
    dates = sorted(r.day for r in results)
    for key, name, at in (("regular", "Regular", REGULAR_AT), ("veteran", "Veteran", VETERAN_AT)):
        add(key, name, f"Finish {at} daily puzzles.", "Habit", at, len(results), _nth(dates, at))

    rare = sorted(rare_pick_days)
    add("against_the_grain", "Against the grain",
        f"Make a correct pick that fewer than 1 player in {RARE_SHARE_ONE_IN} made.", "Rare", 1, 0,
        rare[0] if rare else None)
    return out
