"""A season-in-progress as a draft state plus the human's own live match choices, and
nothing else -- the same "replay from scratch every request" contract `web/session.py`
already promises for a draft, extended one layer up to cover a whole season.

Why this is its OWN module rather than folded into `web/session.py`'s `Move` union:
that grammar (`Pick`/`Reroll`/`Reposition`), its `MOVE_CAP`, and its `_policy` are all
tightly typed to the draft's own three move shapes and to `run_draft`'s specific loop.
`web/rooms.py` already set the precedent for NOT reusing it when a domain's shape
genuinely differs -- a room's shared pool got its own move log rather than being
squeezed into the draft's; a season's match-by-match decisions get their own here for
the same reason.

The combined identity is `"{draft_state}~{season_moves}"` -- `~` never appears in the
draft grammar, so it is an unambiguous top-level separator. A bare draft state (no `~`
at all) is read as "no season moves yet," which is exactly the state string the draft
flow already produces the moment a squad completes -- the season only grows a `~`
suffix once the first toss/Impact decision (live or auto-resolved by a skip) is made.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from etl.feasibility import XI_SIZE
from game.season import (
    MATCHES_EACH, TEAMS, TOSS_DEFAULT_ELECTS, ImpactPick, Innings, JourneyAccumulator,
    JourneyStats, NeedImpact, NeedToss, Season, Side, TossElect, historical_sides,
    journey_stats, run_league, run_playoffs,
)
from web import session as sess

# Upper bound, not a measured fact: at most MATCHES_EACH league matches plus the three
# playoff matches a side can actually reach (Q1 or Eliminator, then possibly Q2, then
# possibly the Final -- never all four), each contributing AT MOST two segments (a
# toss is only ever recorded when the human WINS it; an Impact choice only when he
# has an Impact Player to decide about at all) -- so this is generous headroom, not a
# tight count, the same spirit as `web/session.py`'s own `MOVE_CAP`.
SEASON_MOVE_CAP = (MATCHES_EACH + 3) * 2


# --- encoding ----------------------------------------------------------------------------

def _encode_move(m: TossElect | ImpactPick) -> str:
    if isinstance(m, TossElect):
        return "ta" if m.elects == "bat" else "to"
    return f"i{m.slot if m.slot is not None else 0}"


def encode_season(moves: tuple) -> str:
    return ".".join(_encode_move(m) for m in moves)


def _parse_season_move(segment: str) -> TossElect | ImpactPick:
    if segment == "ta":
        return TossElect("bat")
    if segment == "to":
        return TossElect("bowl")
    if segment.startswith("i"):
        try:
            n = int(segment[1:])
        except ValueError:
            raise ValueError(f"malformed impact move {segment!r}") from None
        if not 0 <= n <= XI_SIZE:
            raise ValueError(f"impact slot out of range: {segment!r}")
        return ImpactPick(None if n == 0 else n)
    raise ValueError(f"malformed season move {segment!r}")


def decode_season(s: str) -> tuple:
    if not s:
        return ()
    try:
        moves = tuple(_parse_season_move(seg) for seg in s.split("."))
    except ValueError as exc:
        raise sess.InvalidState(f"malformed season state {s!r}") from exc
    if len(moves) > SEASON_MOVE_CAP:
        raise sess.InvalidState(f"season state {s!r} is out of range")
    return moves


def decode_full(state: str) -> tuple[str, tuple]:
    """A bare draft state (no `~`) reads as zero season moves -- the same string the
    draft flow already hands back the moment a squad completes."""
    draft_part, sep, season_part = state.partition("~")
    return draft_part, (decode_season(season_part) if sep else ())


def encode_full(draft_state: str, season_moves: tuple) -> str:
    return f"{draft_state}~{encode_season(season_moves)}"


# --- the move cursor: one mechanism behind live play AND every skip hatch ----------------

class MoveCursor:
    """Replays `recorded` moves first; once they run out, for whichever human match
    comes next: if `auto(human_match_no)` says so, emits the declared default (`game.
    season.TOSS_DEFAULT_ELECTS` / decline) instead of pausing -- this one predicate is
    what backs every skip/bypass path, not three separate mechanisms. Otherwise reports
    "no move" (`None`), which `game.season`'s `_next_toss`/`_next_impact` turn into the
    match-level pause exception they always have been able to raise.

    Every move actually emitted -- whether replayed from `recorded` or freshly
    auto-resolved -- is appended to `self.emitted`, in order. That is the tuple a
    caller re-encodes into the new state string once the replay call this cursor backs
    finishes or pauses again; it is always exactly what a plain `RecordedMoves`-style
    replay of the SAME emitted sequence would reproduce, which is what keeps "the state
    string alone is the complete truth" (SPEC 11.2/11.3) holding after a skip.
    """

    def __init__(self, recorded: tuple, auto=lambda human_match_no: False):
        self._recorded = list(recorded)
        self._i = 0
        self._auto = auto
        self.emitted: list = []

    def _next(self, human_match_no: int, expected_type: type, make_default):
        if self._i < len(self._recorded):
            move = self._recorded[self._i]
            if not isinstance(move, expected_type):
                raise sess.InvalidState(
                    f"expected {expected_type.__name__} at position {self._i}, "
                    f"got {move!r}")
            self._i += 1
            self.emitted.append(move)
            return move
        if self._auto(human_match_no):
            move = make_default()
            self.emitted.append(move)
            return move
        return None

    def next_toss(self, human_match_no: int) -> TossElect | None:
        return self._next(human_match_no, TossElect,
                           lambda: TossElect(TOSS_DEFAULT_ELECTS))

    def next_impact(self, human_match_no: int) -> ImpactPick | None:
        return self._next(human_match_no, ImpactPick, lambda: ImpactPick(None))


def recorded_moves(moves: tuple) -> MoveCursor:
    """Plain live play: replay what's recorded, pause the instant it runs out."""
    return MoveCursor(moves)


def skip_this_match(moves: tuple, pending_match_no: int) -> MoveCursor:
    """Auto-resolve only the currently pending human match; pause normally at the next
    one. `_i < len(recorded)` is checked before `_auto` on every call (see `_next`), so
    matches already recorded keep replaying from `recorded` regardless of this
    predicate -- it only ever affects the one match nothing has been recorded for yet."""
    return MoveCursor(moves, auto=lambda n: n == pending_match_no)


def skip_group_stage(moves: tuple) -> MoveCursor:
    """Auto-resolve every remaining LEAGUE match; playoff matches still pause normally."""
    return MoveCursor(moves, auto=lambda n: n < MATCHES_EACH)


def skip_tournament(moves: tuple) -> MoveCursor:
    """Auto-resolve everything remaining, league or playoffs."""
    return MoveCursor(moves, auto=lambda n: True)


# --- replaying a whole season from scratch -----------------------------------------------

@dataclass
class SeasonReplay:
    """Everything `web/app.py` needs to build either a `SeasonProgressOut` with a
    pending decision, or a completed one -- rebuilt from scratch on every call
    (SPEC 11.3), exactly like `web.rooms.replay_room` at the fixture/match level
    instead of the draft-turn level."""

    yours: Side
    season: Season
    cursor: MoveCursor
    complete: bool
    pending_kind: str | None = None            # "toss" | "impact" | None
    pending_opponent: Side | None = None
    pending_stage: str | None = None
    pending_human_match_no: int | None = None
    pending_discipline: str | None = None       # impact only
    pending_first_innings: Innings | None = None    # impact only
    pending_human_bats_first: bool | None = None    # impact only
    stats: JourneyAccumulator | None = None     # complete only
    journey: JourneyStats | None = None         # complete only


def replay_season(deck, model, draft_state: str, cursor: MoveCursor) -> SeasonReplay:
    """Replay the draft (to rebuild the drafted squad and advance the rng exactly as it
    did), then the season, pausing at whichever of the human's own matches `cursor`
    cannot yet answer. Mirrors `web/app.py`'s existing `_load`/`season()` for the draft
    half verbatim -- nothing about how the squad is rebuilt changes.
    """
    seed, draft_moves = sess.decode(draft_state)

    # `replay_stream` (below) assumes a COMPLETE draft -- an incomplete one makes
    # `run_draft`'s own policy raise `sess._NeedChoice`, which `replay_stream` does not
    # catch (it is not built to pause, only to re-derive rng state from a finished
    # draft). `sess.replay` DOES handle that pause gracefully, so it must run first and
    # gate on `squad_complete`/`playable` before `replay_stream` is ever called.
    player = sess.replay(deck, seed, draft_moves)
    if not player.squad_complete:
        raise sess.InvalidState(f"{len(player.picks)} of {len(player.order) + 1} picks made")
    if not player.playable:
        raise sess.InvalidState("; ".join(player.errors))

    rng = random.Random(seed)
    sess.replay_stream(deck, seed, draft_moves, rng)

    yours = Side(name="Your eleven", short="YOU",
                 xi=list(player.order), impact=player.impact, you=True)
    opposition = historical_sides(deck, rng, TEAMS - 1)
    if len(opposition) < TEAMS - 1:
        raise sess.InvalidState("could not field a full league")
    sides = [yours] + opposition

    acc = JourneyAccumulator()
    try:
        league = run_league(model, sides, rng, track=yours, stats=acc, moves=cursor)
        season = run_playoffs(model, league, rng, track=yours, stats=acc, moves=cursor)
    except NeedToss as exc:
        return SeasonReplay(
            yours=yours, season=exc.season, cursor=cursor, complete=False,
            pending_kind="toss", pending_opponent=exc.match.opponent,
            pending_stage=exc.stage, pending_human_match_no=exc.human_match_no,
        )
    except NeedImpact as exc:
        return SeasonReplay(
            yours=yours, season=exc.season, cursor=cursor, complete=False,
            pending_kind="impact", pending_opponent=exc.match.opponent,
            pending_stage=exc.stage, pending_human_match_no=exc.human_match_no,
            pending_discipline=exc.match.discipline,
            pending_first_innings=exc.match.first,
            pending_human_bats_first=exc.match.human_bats_first,
        )

    return SeasonReplay(yours=yours, season=season, cursor=cursor, complete=True,
                         stats=acc, journey=journey_stats(season, yours, acc))
