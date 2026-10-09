"""Guess the Player -- a Wordle-style daily: find the mystery IPL player in eight guesses. [A183]

Each guess is any player who has appeared in the IPL, and answers with a row of tiles that
compare him with the mystery man: the clubs they share, their role, their country, how they
bowl, when they debuted and last played, and their career runs and wickets. Green is the
same, yellow is close, grey is not, and an arrow on a number says whether the mystery
player's is higher or lower.

A pure function of the puzzle facts (`game.puzzle_facts`) and a seed, like Bingo (A182): no
database, no stored state, and the guesses so far are the whole game state, replayed by the
server on each move (A62).

**The mystery player is chosen from the recognisable.** Anybody can be GUESSED, but the
answer is drawn only from men with a real body of work (`POOL_MIN_PROMINENCE`, three or more
seasons), because a puzzle whose answer is a one-season substitute is not a puzzle but a
lottery. Measured: 197 qualify, which is the whole answer pool -- six and a half months of
dailies before any player returns.

**The daily walks a fixed shuffle of that pool** rather than drawing at random, so no
player returns until every other has had a day. A random draw per date would repeat one
within weeks (the birthday problem at 240 names). The practice seeds draw at random.

**An unknown is never a clue.** A tile whose value the archive does not hold would be a
claim about a man the data cannot make, so such a tile reads "unknown" and is never green or
grey (A23). Nothing is unknown today -- every field is filled -- but the type allows it and
the comparison handles it, because a revised archive can reintroduce one.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from game.bingo import PRACTICE_SEED_FLOOR, daily_seed, new_seed
from game.puzzle_facts import PlayerFacts
from game.site import SITE_HOST, SITE_NAME

__all__ = ["daily_seed", "new_seed", "PRACTICE_SEED_FLOOR"]

GUESSES = 8
POOL_MIN_PROMINENCE = 1000    # runs + 20 * wickets. Measured: at 800 the pool held men a fan
                              # has to be lucky to place (Jakati, Botha, Iqbal Abdulla); 1000 keeps
                              # 197 and loses a few well-known short careers (Kumble, Gibbs)
POOL_MIN_SEASONS = 3
CLOSE_YEARS = 2               # a debut or a last season within this is "close"
CLOSE_RATIO = 0.25            # career runs or wickets within a quarter of the answer
CLOSE_RUNS_FLOOR = 100        # ... but never tighter than this many runs,
CLOSE_WICKETS_FLOOR = 10      # ... or this many wickets: a tail-ender's 12 runs is not "far"
CURRENT_SEASON = 2026         # "last played" of a man still in the league
SHARE_HOST = f"{SITE_HOST}/guess"

ROLE_LABEL = {"batter": "Batter", "bowler": "Bowler", "allrounder": "All-rounder",
              "keeper": "Keeper"}
STYLE_LABEL = {"pace": "Pace", "spin": "Spin", None: "Doesn't bowl"}

MATCH, CLOSE, MISS, UNKNOWN = "match", "close", "miss", "unknown"
SQUARE = {MATCH: "🟩", CLOSE: "🟨", MISS: "⬛", UNKNOWN: "⬛"}


class GuessError(ValueError):
    """A move the rules refuse. The message is for the player."""


@dataclass(frozen=True)
class Tile:
    key: str
    label: str
    value: str                      # what the GUESSED player's tile says
    status: str                     # match | close | miss | unknown
    arrow: str | None = None        # "up": the mystery's is higher; "down": lower
    clubs: tuple[tuple[str, bool], ...] = ()    # teams tile only: (franchise, shared?)


def eligible(p: PlayerFacts) -> bool:
    return p.prominence >= POOL_MIN_PROMINENCE and len(p.seasons) >= POOL_MIN_SEASONS


def pool(players: dict[str, PlayerFacts]) -> list[PlayerFacts]:
    return sorted((p for p in players.values() if eligible(p)), key=lambda p: p.person_id)


def mystery(players: dict[str, PlayerFacts], seed: int) -> PlayerFacts:
    """The player to find. A daily seed walks a fixed shuffle of the pool; a practice seed
    draws at random. Both are pure functions of the seed and never of `hash()` (A125)."""
    names = pool(players)
    if not names:
        raise RuntimeError("no player is eligible to be the mystery")
    if seed < PRACTICE_SEED_FLOOR:
        order = list(names)
        random.Random("guess-order").shuffle(order)
        return order[seed % len(order)]
    return random.Random(f"guess:{seed}").choice(names)


def _number(label: str, key: str, guess: int, answer: int, close: int,
            fmt=lambda n: f"{n:,}") -> Tile:
    if guess == answer:
        return Tile(key, label, fmt(guess), MATCH)
    arrow = "up" if answer > guess else "down"
    return Tile(key, label, fmt(guess), CLOSE if abs(guess - answer) <= close else MISS, arrow)


def _same(label: str, key: str, guess: str | None, answer: str | None, show) -> Tile:
    if guess is None or answer is None:
        return Tile(key, label, "Unknown", UNKNOWN)
    return Tile(key, label, show(guess), MATCH if guess == answer else MISS)


def compare(guess: PlayerFacts, answer: PlayerFacts) -> list[Tile]:
    """Eight tiles: how `guess` stands against the mystery player."""
    shared = set(guess.franchises) & set(answer.franchises)
    clubs = tuple((f, f in answer.franchises) for f in guess.franchises)
    if set(guess.franchises) == set(answer.franchises):
        team_status = MATCH
    else:
        team_status = CLOSE if shared else MISS
    tiles = [
        Tile("teams", "Teams", ", ".join(guess.franchises), team_status, clubs=clubs),
        _same("Role", "role", guess.role, answer.role, lambda r: ROLE_LABEL.get(r, r)),
        _same("Country", "country", guess.country, answer.country, lambda c: c),
        Tile("bowling", "Bowling", STYLE_LABEL.get(guess.bowling_style, "Unknown"),
             MATCH if guess.bowling_style == answer.bowling_style else MISS),
        _number("Debut", "debut", guess.seasons[0], answer.seasons[0], CLOSE_YEARS, str),
        _number("Last season", "last", guess.seasons[-1], answer.seasons[-1], CLOSE_YEARS,
                lambda y: "Active" if y >= CURRENT_SEASON else str(y)),
        _number("Runs", "runs", guess.runs, answer.runs,
                max(CLOSE_RUNS_FLOOR, int(CLOSE_RATIO * answer.runs))),
        _number("Wickets", "wickets", guess.wickets, answer.wickets,
                max(CLOSE_WICKETS_FLOOR, int(CLOSE_RATIO * answer.wickets))),
    ]
    return tiles


@dataclass
class State:
    guesses: list[str]
    solved: bool = False
    gave_up: bool = False

    @property
    def guesses_left(self) -> int:
        return GUESSES - len(self.guesses)

    @property
    def finished(self) -> bool:
        return self.solved or self.gave_up or self.guesses_left <= 0


def guess(players: dict[str, PlayerFacts], answer: PlayerFacts, state: State,
          person_id: str) -> list[Tile]:
    """Apply one guess in place and return his tiles. Raises GuessError for a move the rules
    refuse, which costs nothing and changes nothing."""
    if state.finished:
        raise GuessError("This puzzle is finished.")
    if person_id not in players:
        raise GuessError("Nobody by that name has played in the IPL.")
    if person_id in state.guesses:
        raise GuessError(f"You have already guessed {players[person_id].name}.")
    state.guesses.append(person_id)
    if person_id == answer.person_id:
        state.solved = True
    return compare(players[person_id], answer)


def replay(players: dict[str, PlayerFacts], answer: PlayerFacts,
           guesses: list[str], gave_up: bool = False) -> State:
    state = State([])
    for pid in guesses:
        guess(players, answer, state, pid)
    if gave_up and not state.finished:
        state.gave_up = True
    return state


# --- sharing ------------------------------------------------------------------------------

def share_text(players: dict[str, PlayerFacts], answer: PlayerFacts, state: State,
               label: str, *, daily: bool, seed: int) -> str:
    """One row of squares per guess, and NO names -- everybody playing the same puzzle is
    hunting the same man, so naming him (or anybody who narrowed it down) would hand a reader
    the answer instead of the challenge (A129). `X/8` when it was not found."""
    score = f"{len(state.guesses)}/{GUESSES}" if state.solved else f"X/{GUESSES}"
    lines = [f"{SITE_NAME} · Guess the Player · {label} · {score}"]
    for pid in state.guesses:
        lines.append("".join(SQUARE[t.status] for t in compare(players[pid], answer)))
    lines.append(SHARE_HOST if daily else f"{SHARE_HOST}?seed={seed}")
    return "\n".join(lines)
