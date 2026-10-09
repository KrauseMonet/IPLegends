"""Name the XI's API [A185]. Stateless, like the other puzzle games: the client holds the
guesses so far, each move sends them, and the server replays them and answers. No database --
the sides come from the committed facts snapshot.

The blanks' CLUES (batting position, runs, wickets) are in the puzzle; the NAMES are not, until
a man is named or the puzzle is over.
"""

from __future__ import annotations

import datetime
from functools import cache

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from game import bingo, puzzle_facts, xi as game
from web import bingo_routes, crests

router = APIRouter()

_FOREVER = "public, max-age=3600, s-maxage=86400"


@cache
def _sides() -> list[puzzle_facts.XiSide] | None:
    doc = puzzle_facts.read_document()
    return puzzle_facts.sides_from(doc) if doc else None


def sides() -> list[puzzle_facts.XiSide]:
    s = _sides()
    if not s:
        raise HTTPException(status_code=503, detail="The puzzle data is not available.")
    return s


class TeamOut(BaseModel):
    name: str
    crest: str | None = None


class MatchOut(BaseModel):
    title: str
    date: str
    venue: str
    result: str
    team: TeamOut                  # the side to be named
    opponent: TeamOut


class SlotOut(BaseModel):
    position: int | None           # batting order; None if he did not bat
    bat: str | None
    bowl: str | None
    name: str | None = None        # only once he is named, or the puzzle is over
    status: str = "open"           # open | found | missed


class PuzzleOut(BaseModel):
    seed: int
    label: str
    daily: bool
    date: str | None = None
    mistakes: int
    size: int
    match: MatchOut
    slots: list[SlotOut]


class WrongOut(BaseModel):
    person_id: str
    name: str
    other_side: bool


class StateOut(BaseModel):
    slots: list[SlotOut]
    found: int
    size: int
    wrong: list[WrongOut]
    mistakes_left: int
    finished: bool
    solved: bool
    share: str | None = None


class PlayIn(BaseModel):
    seed: int = Field(ge=1)
    guesses: list[str] = Field(default_factory=list, max_length=game.MAX_GUESSES)
    guess: str | None = None
    give_up: bool = False


class PlayOut(BaseModel):
    result: str | None = None       # found | other_side | wrong; None when no guess was sent
    state: StateOut


def _label(seed: int) -> tuple[str, bool, datetime.date | None]:
    if seed < game.PRACTICE_SEED_FLOOR:
        try:
            day = datetime.date.fromordinal(seed)
        except ValueError:
            raise HTTPException(status_code=400, detail="That is not a puzzle.") from None
        return f"{day.day} {day.strftime('%b %Y')}", True, day
    return f"Puzzle {seed}", False, None


def _slots(side, names: dict[str, str] | None, found: set[str], reveal: bool) -> list[SlotOut]:
    out = []
    for q in side.players:
        got = q.person_id in found
        out.append(SlotOut(
            position=q.position, bat=game.bat_hint(q), bowl=game.bowl_hint(q),
            name=names[q.person_id] if names and (got or reveal) else None,
            status="found" if got else ("missed" if reveal else "open")))
    return out


def _puzzle_out(seed: int) -> PuzzleOut:
    side = game.pick_side(sides(), seed)
    label, daily, day = _label(seed)
    return PuzzleOut(
        seed=seed, label=label, daily=daily, date=day.isoformat() if day else None,
        mistakes=game.MISTAKES, size=len(side.players),
        match=MatchOut(
            title=game.title(side), date=side.date, venue=", ".join(x for x in (side.venue, side.city) if x),
            result=side.result,
            team=TeamOut(name=side.team, crest=crests.crest_url(side.team, side.season)),
            opponent=TeamOut(name=side.opponent, crest=crests.crest_url(side.opponent, side.season))),
        slots=_slots(side, None, set(), False))


def _state_out(players, side, state: game.State, seed: int) -> StateOut:
    label, daily, _ = _label(seed)
    over = game.finished(side, state)
    names = {q.person_id: players[q.person_id].name for q in side.players}
    return StateOut(
        slots=_slots(side, names, set(state.found), over), found=len(state.found),
        size=len(side.players),
        wrong=[WrongOut(person_id=p, name=players[p].name, other_side=o) for p, o in state.wrong],
        mistakes_left=state.mistakes_left, finished=over, solved=game.solved(side, state),
        share=game.share_text(side, state, label, daily=daily, seed=seed) if over else None)


@router.get("/api/xi/today", response_model=PuzzleOut)
def xi_today(response: Response) -> PuzzleOut:
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=300"
    return _puzzle_out(bingo.daily_seed(bingo_routes.today()))


@router.get("/api/xi", response_model=PuzzleOut)
def xi_puzzle(response: Response, seed: int | None = None) -> PuzzleOut:
    """A puzzle by seed -- a shared link's, or a fresh practice one when none is given."""
    if seed is None:
        response.headers["Cache-Control"] = "no-store"
        seed = bingo.new_seed()
    else:
        response.headers["Cache-Control"] = _FOREVER
    return _puzzle_out(seed)


@router.post("/api/xi/play", response_model=PlayOut)
def xi_play(body: PlayIn, response: Response) -> PlayOut:
    """Replay the guesses so far and, if one is sent, apply it. A plain replay is how a
    returning player's page asks 'where was I'."""
    response.headers["Cache-Control"] = "no-store"
    players = bingo_routes.facts().players
    side = game.pick_side(sides(), body.seed)
    try:
        state = game.replay(players, side, list(body.guesses))
        result = game.guess(players, side, state, body.guess) if body.guess is not None else None
        if body.give_up and not game.finished(side, state):
            state.gave_up = True
    except game.XiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PlayOut(result=result, state=_state_out(players, side, state, body.seed))
