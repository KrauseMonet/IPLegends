"""SPEC 11. A draft session as a seed plus the choices made, and nothing else.

The whole of this module exists to turn `run_draft`'s closed fifteen-pick loop into
something a browser can walk one request at a time, WITHOUT forking the draft rules.

That constraint is the design. The overseas cap (A61), the deal-time guarantee (A40) and
the scarcity heuristic all live in `run_draft`, and a second implementation of the loop
here would be a second place for them to drift -- exactly what A19 refuses for columns and
what check 12's `TEMPLATE` import refuses for the template. So the loop is reused verbatim
and the human is injected as a POLICY that replays recorded choices and then stops.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass

from etl.feasibility import (
    OVERSEAS_CAP, POLICIES, SQUAD_SIZE, TEMPLATE, Card, Deck, run_draft,
)


class InvalidState(ValueError):
    """The client sent a state this deck cannot produce. Never a 500."""


class _NeedChoice(Exception):
    """Raised out of the policy when the recorded choices run out.

    Control flow by exception, deliberately: `run_draft` owns the loop and has no notion
    of pausing, and the alternative is a re-entrant rewrite of the very code this module
    exists not to duplicate.
    """

    def __init__(self, pairs: list[tuple[Card, str]]):
        self.pairs = pairs


@dataclass(frozen=True)
class Deal:
    """One franchise-season offered to the drafter -- the WHOLE squad, not just the part
    that can be taken.

    A list showing only the pickable men hides the shape of the squad: you cannot see that
    Chennai 2010 had Dhoni if your keeper is already named, so the deal looks thin rather
    than spent. `blocked` carries everyone else with the reason, so the roster reads as a
    roster and the constraint reads as a constraint.
    """

    fs_id: int
    franchise: str | None
    season_year: int | None
    options: list[tuple[Card, str]]
    blocked: list[tuple[Card, str]] = None


@dataclass(frozen=True)
class Session:
    seed: int
    choices: tuple[int, ...]
    deal: Deal | None            # None once the squad is full
    squad: list[tuple[Card, str]]

    @property
    def complete(self) -> bool:
        return self.deal is None

    @property
    def state(self) -> str:
        return encode(self.seed, self.choices)


# --- the state string -----------------------------------------------------------------
#
# `7-0.3.14` is the seed and the choices, in the open. It is NOT signed, and that is a
# correction to SPEC 11 as first written, which called for an HMAC.
#
# The state turned out to be self-validating: every choice is an index into the options the
# server itself deals, so replay either lands on a legal pick or the index is out of range
# and the state is rejected. There is no privilege to forge - a player may already pick any
# seed by asking for another draft, and the score is computed by the server from the state
# rather than submitted with it. A signature would have protected nothing and read as though
# it protected something, which is worse than leaving it off.
#
# It goes back on the day a result outlives the request that produced it - a leaderboard
# means a client could otherwise submit a state it never played through.

def encode(seed: int, choices: tuple[int, ...] | list[int]) -> str:
    return f"{seed}-{'.'.join(str(c) for c in choices)}" if choices else f"{seed}-"


def decode(state: str) -> tuple[int, tuple[int, ...]]:
    """Strict. An empty segment is a malformed state, not an omitted choice.

    Skipping empties would make `7-1..2.` and `7-1.2` the same session, so two different
    URLs would silently mean one game - and the first would look like it had lost a pick.
    Rejecting is the same instinct as refusing to default an unobserved value (A23): a
    string we cannot read is not a string to guess at.
    """
    if "-" not in state:
        raise InvalidState(f"malformed state {state!r}: no seed separator")
    seed_part, _, choice_part = state.partition("-")
    segments = choice_part.split(".") if choice_part else []
    try:
        seed = int(seed_part)
        choices = tuple(int(c) for c in segments)
    except ValueError as exc:
        raise InvalidState(f"malformed state {state!r}") from exc
    if seed < 0 or len(choices) > SQUAD_SIZE:
        raise InvalidState(f"state {state!r} is out of range")
    if any(c < 0 for c in choices):
        raise InvalidState(f"state {state!r} has a negative choice")
    return seed, choices


# --- replay ---------------------------------------------------------------------------

def _policy(choices: tuple[int, ...]):
    remaining = iter(choices)

    def policy(pairs, scarcity):
        try:
            index = next(remaining)
        except StopIteration:
            raise _NeedChoice(pairs) from None
        if not 0 <= index < len(pairs):
            raise InvalidState(
                f"choice {index} is not among the {len(pairs)} options that were dealt")
        return pairs[index]

    return policy


def replay(deck: Deck, seed: int, choices: tuple[int, ...]) -> Session:
    """Rebuild a session from scratch. ~3 ms, so it is done on every request (SPEC 11.3)."""
    rng = random.Random(seed)
    try:
        result = run_draft(deck, _policy(choices), rng)
    except _NeedChoice as pause:
        # The drafter is mid-draft: `run_draft` unwound, so the picks it had made are gone
        # and are rebuilt here from the same choices, one pick shorter.
        squad = _picks_so_far(deck, seed, choices)
        pairs = pause.pairs
        first = pairs[0][0]
        return Session(seed, choices,
                       Deal(first.fs_id, first.franchise, first.season_year, pairs,
                            _blocked(deck, first.fs_id, squad, pairs)),
                       squad)

    if not result.completed:
        # The guarantee re-drew to its cap and still found nothing. Check 12 asserts this
        # does not happen to a rational drafter; a human can strand where a rational one
        # would not, so it is reported rather than treated as impossible.
        raise InvalidState(
            f"this draft cannot be completed - stranded on {result.stranded_on}")
    return Session(seed, choices, None, result.picks)


def _blocked(deck: Deck, fs_id: int, squad: list[tuple[Card, str]],
             pairs: list[tuple[Card, str]]) -> list[tuple[Card, str]]:
    """Everyone in the dealt squad who cannot be taken, and the reason.

    Reasons are ordered by which the drafter can do something about. Being already drafted
    is permanent; the overseas cap is permanent once spent; a filled place may still open
    up in the sense that another card fills a different one. Reported in that order so the
    most final reason wins rather than whichever is checked first.
    """
    takeable = {c.person_id for c, _ in pairs}
    drafted = {c.person_id for c, _ in squad}
    overseas_taken = sum(1 for c, _ in squad if c.overseas is True)
    filled = Counter(slot for _, slot in squad)
    unfilled = {s for s, n in TEMPLATE.items() if filled[s] < n}

    out: list[tuple[Card, str]] = []
    for card in deck.cards_by_fs.get(fs_id, ()):
        if card.person_id in takeable:
            continue
        if card.person_id in drafted:
            reason = "already drafted"
        elif card.overseas is True and overseas_taken >= OVERSEAS_CAP:
            reason = "overseas quota full"
        elif not (card.slots & unfilled):
            reason = "no place open"
        else:
            # Shouldn't happen: if a slot is open and he is not blocked, he is takeable.
            reason = "unavailable"
        out.append((card, reason))
    return out


def _picks_so_far(deck: Deck, seed: int, choices: tuple[int, ...]) -> list[tuple[Card, str]]:
    """The squad after `choices`, obtained by replaying one pick short of the pause.

    Costs a second replay rather than threading a mutable list through `run_draft`, which
    would mean changing the signature every consumer of the draft shares.
    """
    picks: list[tuple[Card, str]] = []
    if not choices:
        return picks
    captured: list[tuple[Card, str]] = []

    def watcher(pairs, scarcity):
        try:
            index = next(it)
        except StopIteration:
            raise _NeedChoice(pairs) from None
        captured.append(pairs[index])
        return pairs[index]

    it = iter(choices)
    try:
        run_draft(deck, watcher, random.Random(seed))
    except _NeedChoice:
        pass
    return captured


def new_seed(rng: random.Random | None = None) -> int:
    """A fresh game. Six digits keeps a shareable URL short and is far more than the 166
    franchise-seasons could ever distinguish anyway."""
    return (rng or random.Random()).randrange(1_000_000)


def replay_stream(deck: Deck, seed: int, choices: tuple[int, ...],
                  rng: random.Random) -> None:
    """Advance `rng` exactly as the player's own draft did, and nothing more.

    The season needs the generator positioned where the draft left it, so that a state
    reproduces the whole campaign. Kept separate from `after_draft` because the season
    deals its own opposition rather than drafting one.
    """
    run_draft(deck, _policy(choices), rng)


def after_draft(deck: Deck, seed: int, choices: tuple[int, ...]
                ) -> tuple[list[tuple[Card, str]], random.Random]:
    """The house squad, plus the LIVE rng the match must continue from.

    One stream, not a second seed. `game.__main__` deals both squads and then plays the
    match from a single `Random`, so the same order is kept here: the player's draft
    advances the stream exactly as it did when they made the picks, the opponent is drafted
    next, and the returned generator is handed to `play_innings`. Returning the rng rather
    than the seed is what makes that possible - re-seeding here would silently give the API
    a different match from the CLI for the same seed, with nothing to notice it.
    """
    rng = random.Random(seed)
    run_draft(deck, _policy(choices), rng)
    result = run_draft(deck, POLICIES["rational"], rng)
    if not result.completed:
        raise InvalidState("the opponent could not be drafted from this deck")
    return result.picks, rng
