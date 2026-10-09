"""Name the XI -- a famous match's scorecard with the names blanked. [A185]

A side of a final, a century match or a super-over thriller is shown by its scorecard with every
NAME removed: each blank keeps its batting position and what he did ("69 (38)", "3/45 (4)").
Name the players. Anyone who played for the side fills his own blank; anyone else is a
mistake, five of which end it. A man who played for the OTHER side that day is still a
mistake, but the game says so, since that is the memory worth sharpening.

A pure function of the puzzle facts and a seed, like the other puzzle games: no database, no
stored state, and the guess list is the whole state, replayed by the server on each move (A62).

**Which sides, and why some are left out.** Before the Impact Player (2023) the archive names
exactly the 11 who played. From 2023 it names a twelfth who may or may not have taken the
field, so only sides where every named man took part are used -- otherwise a player who was
never on the field would be marked a wrong answer for one who was. 254 of 298 sides qualify
(finals, century matches and super overs), which is eight months of dailies, and the 44 that
do not are dropped rather than guessed at. An Impact-era side has twelve names, and says so.

**The daily walks a fixed shuffle of the sides** so none returns until all have had a day, as
Guess the Player's and Teammate Chain's do, with its own key.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from game.bingo import PRACTICE_SEED_FLOOR, daily_seed, new_seed
from game.puzzle_facts import PlayerFacts, XiPlayer, XiSide

__all__ = ["daily_seed", "new_seed", "PRACTICE_SEED_FLOOR"]

MISTAKES = 5
MAX_GUESSES = 40               # a bound on the replayed list: 13 found plus 5 wrong is the most
SHARE_HOST = "iplegends.vercel.app/xi"

FOUND, OTHER_SIDE, WRONG = "found", "other_side", "wrong"


class XiError(ValueError):
    """A move the rules refuse. The message is for the player."""


def pick_side(sides: list[XiSide], seed: int) -> XiSide:
    """The side to name. A daily seed walks a fixed shuffle of every side; a practice seed
    draws at random. Never `hash()` (A125)."""
    if not sides:
        raise RuntimeError("no side is available to name")
    ordered = sorted(sides, key=lambda x: x.key)
    if seed < PRACTICE_SEED_FLOOR:
        random.Random("xi-order").shuffle(ordered)
        return ordered[seed % len(ordered)]
    return random.Random(f"xi:{seed}").choice(ordered)


def overs(balls: int) -> str:
    return str(balls // 6) if balls % 6 == 0 else f"{balls // 6}.{balls % 6}"


def bat_hint(q: XiPlayer) -> str | None:
    if q.runs is None:
        return None
    return f"{q.runs} ({q.balls}){'' if q.out else '*'}"


def bowl_hint(q: XiPlayer) -> str | None:
    if q.wickets is None:
        return None
    return f"{q.wickets}/{q.conceded} ({overs(q.bowl_balls)})"


def title(side: XiSide) -> str:
    if "final" in side.kinds:
        return f"{side.season} Final"
    if "super_over" in side.kinds:
        return f"{side.season} · decided in a super over"
    return f"{side.season} · a century was scored"


@dataclass
class State:
    found: list[str] = field(default_factory=list)                 # in the order he was named
    wrong: list[tuple[str, bool]] = field(default_factory=list)    # (person_id, played for the other side)
    gave_up: bool = False

    @property
    def mistakes_left(self) -> int:
        return MISTAKES - len(self.wrong)


def finished(side: XiSide, state: State) -> bool:
    return (state.gave_up or len(state.found) == len(side.players)
            or len(state.wrong) >= MISTAKES)


def solved(side: XiSide, state: State) -> bool:
    return len(state.found) == len(side.players)


def guess(players: dict[str, PlayerFacts], side: XiSide, state: State, person_id: str) -> str:
    """Apply one guess in place. Returns FOUND, OTHER_SIDE or WRONG; the last two cost a
    mistake. Raises XiError for a move the rules refuse, which costs nothing and changes
    nothing."""
    if finished(side, state):
        raise XiError("This one is over.")
    if person_id not in players:
        raise XiError("Nobody by that name has played in the IPL.")
    if person_id in state.found or any(person_id == p for p, _ in state.wrong):
        raise XiError(f"You have already tried {players[person_id].name}.")
    if any(q.person_id == person_id for q in side.players):
        state.found.append(person_id)
        return FOUND
    other = person_id in side.opposition
    state.wrong.append((person_id, other))
    return OTHER_SIDE if other else WRONG


def replay(players: dict[str, PlayerFacts], side: XiSide, guesses: list[str],
           gave_up: bool = False) -> State:
    state = State()
    for pid in guesses:
        guess(players, side, state, pid)
    if gave_up and not finished(side, state):
        state.gave_up = True
    return state


# --- sharing ------------------------------------------------------------------------------

def share_text(side: XiSide, state: State, label: str, *, daily: bool, seed: int) -> str:
    """The slots in batting order, 🟩 named and ⬛ not, and the mistake count -- and no names:
    everybody is naming the same XI, so one name would hand a reader a slot (A129)."""
    got = set(state.found)
    squares = "".join("🟩" if q.person_id in got else "⬛" for q in side.players)
    lines = [f"Almanack Name the XI · {label} · {len(state.found)}/{len(side.players)}", squares]
    if state.wrong:
        lines.append(f"{len(state.wrong)} wrong")
    lines.append(SHARE_HOST if daily else f"{SHARE_HOST}?seed={seed}")
    return "\n".join(lines)
