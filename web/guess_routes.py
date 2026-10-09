"""Guess the Player's API [A183]. Stateless, like Bingo's (A182): the client holds the list
of guesses so far, each move sends it, and the server replays it and answers. No database --
the players come from the committed facts snapshot.

The mystery player is never sent until the puzzle is finished. There is no leaderboard, so
the line is drawn at honesty rather than security: `give_up` ends a puzzle and names the
player, and a visitor who calls it on a first move has simply given up.
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from game import bingo, guess as game
from web import bingo_routes, crests

router = APIRouter()

_FOREVER = "public, max-age=3600, s-maxage=86400"


class ClubOut(BaseModel):
    franchise: str
    shared: bool
    crest: str | None = None


class TileOut(BaseModel):
    key: str
    label: str
    value: str
    status: str
    arrow: str | None = None
    clubs: list[ClubOut] = []


class GuessRowOut(BaseModel):
    person_id: str
    name: str
    tiles: list[TileOut]


class PuzzleOut(BaseModel):
    seed: int
    label: str
    daily: bool
    date: str | None = None
    guesses: int


class AnswerOut(BaseModel):
    person_id: str
    name: str
    tiles: list[TileOut]


class StateOut(BaseModel):
    rows: list[GuessRowOut]
    guesses_left: int
    solved: bool
    finished: bool
    answer: AnswerOut | None = None     # only once the puzzle is over
    share: str | None = None


class PlayIn(BaseModel):
    seed: int = Field(ge=1)
    guesses: list[str] = Field(default_factory=list, max_length=game.GUESSES)
    guess: str | None = None
    give_up: bool = False


class PlayOut(BaseModel):
    correct: bool | None = None         # None when no new guess was sent
    state: StateOut


def _label(seed: int) -> tuple[str, bool, datetime.date | None]:
    if seed < game.PRACTICE_SEED_FLOOR:
        try:
            day = datetime.date.fromordinal(seed)
        except ValueError:
            raise HTTPException(status_code=400, detail="That is not a puzzle.") from None
        return f"{day.day} {day.strftime('%b %Y')}", True, day
    return f"Puzzle {seed}", False, None


def _tiles(tiles: list[game.Tile]) -> list[TileOut]:
    return [TileOut(key=t.key, label=t.label, value=t.value, status=t.status, arrow=t.arrow,
                    clubs=[ClubOut(franchise=f, shared=s, crest=crests.franchise_crest(f))
                           for f, s in t.clubs]) for t in tiles]


def _puzzle_out(seed: int) -> PuzzleOut:
    label, daily, day = _label(seed)
    return PuzzleOut(seed=seed, label=label, daily=daily, date=day.isoformat() if day else None,
                     guesses=game.GUESSES)


def _state_out(players, answer, state: game.State, seed: int) -> StateOut:
    label, daily, _ = _label(seed)
    rows = [GuessRowOut(person_id=pid, name=players[pid].name,
                        tiles=_tiles(game.compare(players[pid], answer)))
            for pid in state.guesses]
    over = state.finished
    return StateOut(
        rows=rows, guesses_left=state.guesses_left, solved=state.solved, finished=over,
        answer=AnswerOut(person_id=answer.person_id, name=answer.name,
                         tiles=_tiles(game.compare(answer, answer))) if over else None,
        share=game.share_text(players, answer, state, label, daily=daily, seed=seed)
        if over else None)


@router.get("/api/guess/today", response_model=PuzzleOut)
def guess_today(response: Response) -> PuzzleOut:
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=300"
    return _puzzle_out(bingo.daily_seed(bingo_routes.today()))


@router.get("/api/guess", response_model=PuzzleOut)
def guess_puzzle(response: Response, seed: int | None = None) -> PuzzleOut:
    """A puzzle by seed -- a shared link's, or a fresh practice one when none is given. The
    response names no player, so it is safe to cache."""
    if seed is None:
        response.headers["Cache-Control"] = "no-store"
        seed = bingo.new_seed()
    else:
        response.headers["Cache-Control"] = _FOREVER
    return _puzzle_out(seed)


@router.post("/api/guess/play", response_model=PlayOut)
def guess_play(body: PlayIn, response: Response) -> PlayOut:
    """Replay the guesses so far and, if one is sent, apply it. A plain replay is how a
    returning player's page asks 'where was I'."""
    response.headers["Cache-Control"] = "no-store"
    players = bingo_routes.facts().players
    answer = game.mystery(players, body.seed)
    try:
        state = game.replay(players, answer, list(body.guesses))
        correct = None
        if body.guess is not None:
            game.guess(players, answer, state, body.guess)
            correct = state.solved
        if body.give_up and not state.finished:
            state.gave_up = True
    except game.GuessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PlayOut(correct=correct, state=_state_out(players, answer, state, body.seed))
