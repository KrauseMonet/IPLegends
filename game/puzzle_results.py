"""What a finished daily puzzle is worth, recomputed from the moves [A188].

The page never says how it did. It sends the moves it made, this module replays them against
the day's puzzle and returns an `Outcome`, and that -- not anything the client claimed -- is what
gets recorded. It is the daily challenge's rule (`web/daily.py`'s `mark`): a fabricated submission
either fails to replay or replays into a real result, so no signature is needed.

Pure functions of already-built puzzles: no database, no web framework, no clock. The caller
builds the day's grid, side or pair from the seed and hands it in, which keeps this testable from
hand-built players and lets the four games share one `Outcome`.

The score is each game's OWN (`bingo.State.score`, `guess.points`, `xi.points`, `ground.score`).
Rarity -- how few other players picked the same answer -- is deliberately not here: it depends on
everybody else's picks, so it is derived when a board is read and never stored in a result.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from game import bingo, ground, guess as guess_game, xi

GAMES = ("bingo", "guess", "xi", "common")
PICK_GAMES = ("bingo", "xi", "common")      # the games whose picks are counted for rarity
SUBMIT_GRACE_DAYS = 1                       # a puzzle opened before midnight UTC may finish after it


class PuzzleResultError(ValueError):
    """A submission the rules refuse. The message is for the player."""


@dataclass(frozen=True)
class Pick:
    slot: str          # Bingo: the cell; Name the XI: the batting position; Common Ground: the player
    person_id: str
    base: int = 0      # what this pick scored by itself (Bingo's answer-count points); else 0


@dataclass(frozen=True)
class Outcome:
    game: str
    score: int
    solved: bool                      # the whole puzzle was completed
    guesses: int                      # every guess made, right or wrong
    wrong: int
    total: int                        # how many there were to find
    found: int
    picks: tuple[Pick, ...]           # the correct ones, in a fixed order
    elapsed_ms: int | None = None     # only a timed game has one
    ended: str | None = None          # None, "gave_up" or "time"

    @property
    def perfect(self) -> bool:
        """Completed with nothing wrong -- the shape every badge about flawlessness asks for."""
        return self.solved and self.wrong == 0

    def detail(self) -> dict:
        """The summary stored beside the moves: what the board and the badges read without a
        replay. Picks are [slot, person_id, base]."""
        return {"guesses": self.guesses, "wrong": self.wrong, "found": self.found,
                "total": self.total, "picks": [[p.slot, p.person_id, p.base] for p in self.picks]}


def daily_date(seed: int, today: datetime.date) -> datetime.date:
    """The date a daily seed stands for, if it is one that can still be ranked: today's, or
    yesterday's for somebody who began before midnight UTC. A practice seed, a date in the future
    and an old one are all refused -- only the day everybody is playing is a leaderboard."""
    if seed >= bingo.PRACTICE_SEED_FLOOR:
        raise PuzzleResultError("Only the daily puzzle is ranked.")
    try:
        day = datetime.date.fromordinal(seed)
    except ValueError:
        raise PuzzleResultError("That is not a puzzle.") from None
    if day > today:
        raise PuzzleResultError("That puzzle has not opened yet.")
    if (today - day).days > SUBMIT_GRACE_DAYS:
        raise PuzzleResultError("That day is closed.")
    return day


class NotFinished(PuzzleResultError):
    """The moves replay cleanly but the puzzle is not over, so there is nothing to record yet."""


# --- Bingo -----------------------------------------------------------------------------------

def verify_bingo(grid: bingo.Grid, facts: bingo.Facts, guesses: list[tuple[int, str]]) -> Outcome:
    try:
        state = bingo.replay(grid, facts, guesses)
    except bingo.BingoError as exc:
        raise PuzzleResultError(str(exc)) from exc
    if not state.finished:
        raise NotFinished("This grid is not finished.")
    picks = tuple(Pick(str(cell), pid, grid.points(cell)) for cell, pid in sorted(state.placed.items()))
    return Outcome("bingo", state.score, len(state.placed) == 9, state.guesses_used, len(state.wrong),
                   9, len(state.placed), picks)


# --- Guess the Player ------------------------------------------------------------------------

def verify_guess(players, answer, ids: list[str], gave_up: bool = False) -> Outcome:
    try:
        state = guess_game.replay(players, answer, ids, gave_up=gave_up)
    except guess_game.GuessError as exc:
        raise PuzzleResultError(str(exc)) from exc
    if not state.finished:
        raise NotFinished("This puzzle is not finished.")
    n = len(state.guesses)
    return Outcome("guess", guess_game.points(state), state.solved, n, n - 1 if state.solved else n,
                   1, 1 if state.solved else 0, (), ended="gave_up" if state.gave_up else None)


# --- Name the XI -----------------------------------------------------------------------------

def verify_xi(players, side, ids: list[str], gave_up: bool = False) -> Outcome:
    try:
        state = xi.replay(players, side, ids, gave_up=gave_up)
    except xi.XiError as exc:
        raise PuzzleResultError(str(exc)) from exc
    if not xi.finished(side, state):
        raise NotFinished("This one is not over.")
    slot_of = {q.person_id: str(k) for k, q in enumerate(side.players)}
    picks = tuple(sorted((Pick(slot_of[pid], pid) for pid in state.found), key=lambda p: int(p.slot)))
    return Outcome("xi", xi.points(side, state), xi.solved(side, state), len(state.found) + len(state.wrong),
                   len(state.wrong), len(side.players), len(state.found), picks,
                   ended="gave_up" if state.gave_up else None)


# --- Common Ground ---------------------------------------------------------------------------

def verify_common(g: ground.Ground, puzzle: ground.Puzzle, moves: list[ground.Move],
                  end_t: int | None = None) -> Outcome:
    """`moves` carry the SERVER's clock in a ranked attempt, so `t` here is trusted; an anonymous
    attempt's `t` is the page's and never reaches this function."""
    try:
        state = ground.replay(g, puzzle, moves, end_t=end_t)
    except ground.GroundError as exc:
        raise PuzzleResultError(str(exc)) from exc
    if not state.finished:
        raise NotFinished("This attempt is not over.")
    sc = ground.score(state)
    picks = tuple(Pick(m.id, m.id) for m in sorted(state.found, key=lambda m: m.id))
    return Outcome("common", sc.points, state.complete, len(state.moves), sc.wrong, state.total, sc.found,
                   picks, elapsed_ms=state.elapsed_ms, ended=None if state.complete else state.ended)
