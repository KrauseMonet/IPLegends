"""SPEC 11/1.2. A draft session as a seed plus the moves made, and nothing else.

The whole of this module exists to turn `run_draft`'s closed twelve-pick loop into
something a browser can walk one request at a time, WITHOUT forking the draft rules.

That constraint is the design. The overseas cap (A61), the deal-time guarantee (A40), the
forward check and the legality algebra (A72/A73) all live in `etl.feasibility`, and a
second implementation of any of them here would be a second place for them to drift --
exactly what A19 refuses for columns. So the loop is reused verbatim and the human is
injected as a POLICY that replays recorded picks and then stops.

[A73] A session now holds ONE kind of move, not two: a pick names both a candidate (an
index into the current deal) and the slot he bats at (a batting position or Impact),
because placement is final the moment a pick is made -- there is no bench, no separate
"place" step, and nothing to fold in afterward.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from etl.feasibility import (
    BOWLERS_IN_TWELVE, IMPACT_SLOT, OVERSEAS_CAP, POLICIES, REROLL_KINDS, REROLLS_ALLOWED,
    TWELVE_SIZE, XI_SIZE, Card, DraftState, Deck, RepositionRequested, RerollRequested,
    could_still_complete, order_errors, run_draft,
)


class InvalidState(ValueError):
    """The client sent a state this deck cannot produce. Never a 500."""


class _NeedChoice(Exception):
    """Raised out of the policy when the recorded picks run out.

    Control flow by exception, deliberately: `run_draft` owns the loop and has no notion
    of pausing, and the alternative is a re-entrant rewrite of the very code this module
    exists not to duplicate. [A73] Carries the `DraftState` `run_draft` already had in
    scope at the moment of pausing, so the paused `Session` is built directly from it --
    no second replay needed to reconstruct "the squad so far."
    """

    def __init__(self, candidates: list[Card], state: DraftState):
        self.candidates = candidates
        self.state = state


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
    options: list[Card]
    blocked: list[tuple[Card, str]] = None


@dataclass(frozen=True)
class Pick:
    """One move: take option `index` from the current deal, straight into `slot` (a
    batting position 1-11, or IMPACT_SLOT). [A73] There is no unplace and no separate
    place -- a pick is always both at once."""
    index: int
    slot: int


@dataclass(frozen=True)
class Reroll:
    """Reject the deal just shown and see a different franchise-season for the SAME
    pick, without spending it. `kind` is "team" (any other franchise-season) or "season"
    (a different year of the SAME franchise just dealt) -- see `REROLL_KINDS` and
    `etl.feasibility.RerollRequested`. Budget is `REROLLS_ALLOWED` for the whole draft,
    not per pick (original SPEC 1.1) -- enforced in `_policy` at replay time, the same
    way an out-of-range pick index is, so the budget is a fact about the state rather
    than something the client is trusted to track."""
    kind: str


@dataclass(frozen=True)
class Reposition:
    """Move whoever is at `from_slot` to `to_slot` -- swapping with whoever is already
    there, or simply relocating if `to_slot` is empty (which then frees `from_slot` for a
    LATER pick, e.g. dropping an opener down to make room for a position-locked new
    arrival). `12` names the Impact slot, same as everywhere else (`IMPACT_SLOT`).

    Consumed by `run_draft`'s own loop as `RepositionRequested` (etl/feasibility.py) --
    NOT a client-side rearrangement applied after the fact. That is the only way a move
    into an empty slot can work at all: `open_slots` has to change in the SAME state the
    deal-time guarantee and the forward check read, or a pick recorded right after this
    one would be validated against the pre-move slots and wrongly refused. See
    `RepositionRequested`'s own docstring for why this costs an attempt (an RNG draw) but
    never a pick, and cannot desync the slot-filling budget A73 relies on.

    Only valid while the draft is still in progress -- enforced by the API route that
    accepts a NEW one, not here: replay has no notion of "now", only of what a state
    string claims happened."""
    from_slot: int
    to_slot: int


Move = Pick | Reroll | Reposition


@dataclass(frozen=True)
class Session:
    seed: int
    moves: tuple[Move, ...]
    deal: Deal | None                      # None once all twelve are drafted
    picks: list[Card]                      # in draft order
    order: tuple[Card | None, ...]         # length XI_SIZE; order[i] bats at position i+1
    impact: Card | None
    errors: tuple[str, ...]                # order_errors(...); empty means playable

    @property
    def squad_complete(self) -> bool:
        return self.deal is None

    @property
    def playable(self) -> bool:
        return self.squad_complete and not self.errors

    @property
    def rerolls_used(self) -> int:
        return sum(1 for m in self.moves if isinstance(m, Reroll))

    @property
    def rerolls_remaining(self) -> int:
        return REROLLS_ALLOWED - self.rerolls_used

    @property
    def state(self) -> str:
        return encode(self.seed, self.moves)


# --- the state string -------------------------------------------------------------------
#
# `7-3:4.0:12.5:7` is the seed and the moves, in the open. It is NOT signed (SPEC 11.2's
# correction stands): every pick is an index into the options the server itself dealt, and
# every slot is checked against the eligibility the server itself computed, so replay
# either lands on a legal state or is rejected. There is no privilege to forge.
#
# `MOVE_CAP` bounds replay cost; it is not a cheat guard -- a state is validated by
# replaying it against the real deck, not by trusting its shape. Twelve picks plus, at
# most, REROLLS_ALLOWED reroll segments plus REPOSITIONS_ALLOWED reposition segments -- a
# state with more than that is malformed on its face, before replay even has to enforce
# either budget itself.

# Generous rather than measured: a reposition touches no RNG and costs replay nothing a
# Pick doesn't already cost, so this exists only to keep a state string bounded, not
# because twelve is a meaningful limit on how often a drafter might rearrange.
REPOSITIONS_ALLOWED = TWELVE_SIZE

MOVE_CAP = TWELVE_SIZE + REROLLS_ALLOWED + REPOSITIONS_ALLOWED

# One letter per `REROLL_KINDS` entry, e.g. "rt"/"rs" -- a reroll segment is always two
# characters and never contains ":", so it can't collide with a `index:slot` Pick segment.
_REROLL_CODE = {kind: kind[0] for kind in REROLL_KINDS}
_REROLL_KIND = {code: kind for kind, code in _REROLL_CODE.items()}
assert len(_REROLL_KIND) == len(REROLL_KINDS), "REROLL_KINDS must not share a first letter"


def encode(seed: int, moves: tuple[Move, ...]) -> str:
    parts = []
    for m in moves:
        if isinstance(m, Reroll):
            parts.append(f"r{_REROLL_CODE[m.kind]}")
        elif isinstance(m, Reposition):
            parts.append(f"m{m.from_slot}:{m.to_slot}")
        else:
            parts.append(f"{m.index}:{m.slot}")
    return f"{seed}-{'.'.join(parts)}" if moves else f"{seed}-"


def _parse_move(segment: str) -> Move:
    if segment.startswith("r"):
        code = segment[1:]
        if code not in _REROLL_KIND:
            raise ValueError(f"malformed reroll {segment!r}")
        return Reroll(_REROLL_KIND[code])
    if segment.startswith("m"):
        rest = segment[1:]
        if ":" not in rest:
            raise ValueError(f"malformed reposition {segment!r}: expected m<slot>:<slot>")
        from_part, _, to_part = rest.partition(":")
        from_slot, to_slot = int(from_part), int(to_part)
        if from_slot < 1 or to_slot < 1:
            raise ValueError("a reposition slot must be at least 1")
        return Reposition(from_slot, to_slot)
    if ":" not in segment:
        raise ValueError(f"malformed move {segment!r}: expected index:slot or a reroll")
    index_part, _, slot_part = segment.partition(":")
    index, slot = int(index_part), int(slot_part)
    if index < 0 or slot < 1:
        raise ValueError("negative index, or a slot below 1")
    return Pick(index, slot)


def decode(state: str) -> tuple[int, tuple[Move, ...]]:
    """Strict. An empty segment is a malformed state, not an omitted move.

    Skipping empties would make `7-1:4..2:5.` and `7-1:4.2:5` the same session, so two
    different URLs would silently mean one game. Rejecting is the same instinct as
    refusing to default an unobserved value (A23): a string we cannot read is not a
    string to guess at.
    """
    if "-" not in state:
        raise InvalidState(f"malformed state {state!r}: no seed separator")
    seed_part, _, move_part = state.partition("-")
    segments = move_part.split(".") if move_part else []
    try:
        seed = int(seed_part)
        moves = tuple(_parse_move(s) for s in segments)
    except ValueError as exc:
        raise InvalidState(f"malformed state {state!r}") from exc
    if seed < 0 or len(moves) > MOVE_CAP:
        raise InvalidState(f"state {state!r} is out of range")
    return seed, moves


# --- replay ---------------------------------------------------------------------------

def _slot_card(order, impact: Card | None, slot: int) -> Card | None:
    if slot == IMPACT_SLOT:
        return impact
    if not 1 <= slot <= XI_SIZE:
        raise InvalidState(f"slot {slot} does not exist")
    return order[slot - 1]


def _validate_reposition(move: Reposition, state: DraftState) -> None:
    """Checked here, against the exact `DraftState` `run_draft` is about to act on --
    `RepositionRequested` itself only asserts (see its docstring), it does not re-derive
    this. Replay is the only place a hand-crafted state string is ever verified (SPEC
    11.2, unsigned state), so this is where an illegal reposition has to be caught."""
    if move.from_slot == move.to_slot:
        raise InvalidState(f"slot {move.from_slot} cannot swap with itself")
    from_card = _slot_card(state.order, state.impact, move.from_slot)
    to_card = _slot_card(state.order, state.impact, move.to_slot)
    if from_card is None:
        raise InvalidState(f"slot {move.from_slot} is empty -- nothing to move")
    if to_card is not None:
        if move.to_slot not in from_card.slots or move.from_slot not in to_card.slots:
            raise InvalidState(
                f"{from_card.name} and {to_card.name} cannot swap slots "
                f"{move.from_slot}/{move.to_slot}: not eligible for each other's position")
    elif move.to_slot not in from_card.slots:
        raise InvalidState(
            f"{from_card.name} cannot move to slot {move.to_slot}: not eligible there")


def _policy(moves: tuple[Move, ...]):
    remaining_moves = iter(moves)
    rerolls_used = 0

    def policy(candidates: list[Card], state: DraftState,
               rng: random.Random) -> tuple[Card, int]:
        nonlocal rerolls_used
        try:
            move = next(remaining_moves)
        except StopIteration:
            raise _NeedChoice(candidates, state) from None
        if isinstance(move, Reroll):
            if move.kind not in REROLL_KINDS:
                raise InvalidState(f"unknown reroll kind {move.kind!r}")
            if rerolls_used >= REROLLS_ALLOWED:
                raise InvalidState(
                    f"no rerolls remaining ({REROLLS_ALLOWED} allowed per draft)")
            rerolls_used += 1
            raise RerollRequested(move.kind)
        if isinstance(move, Reposition):
            _validate_reposition(move, state)
            raise RepositionRequested(move.from_slot, move.to_slot)
        if not 0 <= move.index < len(candidates):
            raise InvalidState(
                f"choice {move.index} is not among the {len(candidates)} options dealt")
        card = candidates[move.index]
        open_here = card.slots & state.open_slots
        if move.slot not in open_here:
            raise InvalidState(
                f"{card.name} cannot be placed at {move.slot} right now "
                f"(open to him now: {sorted(open_here)})")
        return card, move.slot

    return policy


def replay(deck: Deck, seed: int, moves: tuple[Move, ...]) -> Session:
    """Rebuild a session from scratch on every request (SPEC 11.3).

    `Reposition` moves reach `run_draft` exactly like `Reroll` does -- as an exception
    `_policy` raises instead of returning a pick, caught inside `run_draft`'s own attempt
    loop and applied directly to its live `order`/`open_slots` (`RepositionRequested`,
    etl/feasibility.py). That is what lets a LATER pick in the same move list target a
    slot only a reposition freed: the very next `eligible()` call inside `run_draft` sees
    the update, because it IS the same state, not a snapshot rebuilt afterward.
    """
    rng = random.Random(seed)
    try:
        result = run_draft(deck, _policy(moves), rng)
    except _NeedChoice as pause:
        state = pause.state
        candidates = pause.candidates
        first = candidates[0]
        deal = Deal(first.fs_id, first.franchise, first.season_year, candidates,
                    _blocked(deck, first.fs_id, state))
        order = list(state.order)
        picks = list(state.picks)
        return Session(seed, moves, deal, picks, tuple(order), state.impact,
                       tuple(order_errors(order, state.impact, picks)))

    if not result.completed:
        # The guarantee re-drew to its cap and still found nothing. Check 12 asserts this
        # does not happen to a rational drafter; a human can strand where a rational one
        # would not, so it is reported rather than treated as impossible.
        raise InvalidState(
            f"this draft cannot be completed - stranded on {result.stranded_on}")
    return Session(seed, moves, None, result.picks, tuple(result.order), result.impact,
                   tuple(order_errors(result.order, result.impact, result.picks)))


def _blocked(deck: Deck, fs_id: int, state: DraftState) -> list[tuple[Card, str]]:
    """Everyone in the dealt squad who cannot be taken, and the reason.

    Reasons are ordered by which the drafter can do something about. Being already drafted
    is permanent; the overseas cap is permanent once spent; a card blocked by the forward
    check might still be reachable through a DIFFERENT future pick, so its reason is a
    best-effort diagnosis rather than a certainty -- the certainty is that taking this card
    now would not leave a legal twelve reachable, which is all the forward check itself
    claims.
    """
    takeable = {c.person_id for c in deck.cards_by_fs.get(fs_id, ())
                if (c.slots & state.open_slots)
                and c.person_id not in {p.person_id for p in state.picks}}
    drafted = {c.person_id for c in state.picks}
    overseas_taken = sum(1 for c in state.picks if c.overseas is True)
    full = overseas_taken >= OVERSEAS_CAP

    out: list[tuple[Card, str]] = []
    for card in deck.cards_by_fs.get(fs_id, ()):
        if card.person_id in takeable:
            continue
        if card.person_id in drafted:
            reason = "already drafted"
        elif card.overseas is True and full:
            reason = "overseas quota full"
        else:
            reason = _why_blocked(card, state)
        out.append((card, reason))
    return out


def _why_blocked(card: Card, state: DraftState) -> str:
    """Which single requirement taking this card would leave unreachable.

    Not a certified diagnosis -- `could_still_complete` is the certainty, this is a
    best-effort explanation of it. [A73] There is no "a batting position nobody else can
    fill" case any more: with exactly as many future picks as future open slots, position
    eligibility can never be the actual failure once a card has *somewhere* open to bat
    (checked first) -- only the keeper and bowling-depth counts can still be unreachable.
    """
    candidate_slots = card.slots & state.open_slots
    if not candidate_slots:
        return "no open batting position he's eligible for right now"
    remaining_after = state.remaining - 1
    new_keeper_have = state.keeper_have or card.keeper_eligible
    new_bowl_have = state.bowl_have + (1 if card.has_bowl else 0)
    if any(could_still_complete(state.open_slots - {slot}, new_keeper_have,
                                 new_bowl_have, remaining_after)
           for slot in candidate_slots):
        return "unavailable"  # should not happen: not in candidates but not blocked either
    if not new_keeper_have and remaining_after == 0:
        return "would leave your squad with no wicketkeeper at all"
    if new_bowl_have + remaining_after < BOWLERS_IN_TWELVE:
        return (f"would leave only {new_bowl_have} bowling option(s) with "
                f"{remaining_after} pick(s) left to find {BOWLERS_IN_TWELVE}")
    return "unavailable"


def new_seed(rng: random.Random | None = None) -> int:
    """A fresh game. Six digits keeps a shareable URL short and is far more than the 166
    franchise-seasons could ever distinguish anyway."""
    return (rng or random.Random()).randrange(1_000_000)


def replay_stream(deck: Deck, seed: int, moves: tuple[Move, ...],
                  rng: random.Random) -> None:
    """Advance `rng` exactly as the player's own draft did, and nothing more."""
    run_draft(deck, _policy(moves), rng)


def after_draft(deck: Deck, seed: int, moves: tuple[Move, ...]
                ) -> tuple[list[Card], random.Random]:
    """The house squad, plus the LIVE rng the match must continue from.

    One stream, not a second seed. `game.__main__` deals both squads and then plays the
    match from a single `Random`, so the same order is kept here: the player's draft
    advances the stream exactly as it did when they made their picks, the opponent is
    drafted next (still algorithmically), and the returned generator is handed to
    `play_innings`.
    """
    rng = random.Random(seed)
    run_draft(deck, _policy(moves), rng)
    result = run_draft(deck, POLICIES["rational"], rng)
    if not result.completed:
        raise InvalidState("the opponent could not be drafted from this deck")
    return result.picks, rng
