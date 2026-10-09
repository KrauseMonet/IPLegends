"""Teammate Chain's API [A184]. Stateless, like Bingo's and Guess the Player's: the client
holds the guesses so far, each move sends them, and the server replays them and answers.
No database -- the squads come from the committed facts snapshot.
"""

from __future__ import annotations

import datetime
from functools import cache

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from game import bingo, chain as game
from web import bingo_routes, crests

router = APIRouter()

_FOREVER = "public, max-age=3600, s-maxage=86400"


@cache
def _world(players_id: int) -> game.World:
    """Built once per set of players. Keyed on the dict's identity so that a test which
    swaps the facts gets a fresh index rather than a stale one."""
    return game.World(bingo_routes.facts().players)


def world() -> game.World:
    return _world(id(bingo_routes.facts().players))


class StepOut(BaseModel):
    person_id: str
    name: str
    shared: list[str] = []          # squads shared with the player before him (empty for the start)
    crests: list[str] = []          # his clubs, as crests


class PuzzleOut(BaseModel):
    seed: int
    label: str
    daily: bool
    date: str | None = None
    strikes: int
    start: StepOut


class StateOut(BaseModel):
    chain: list[StepOut]
    links: int
    clubs: int
    strikes: int
    strikes_left: int
    tried_here: list[str]           # people already tried from the chain's current end
    finished: bool
    stopped: bool
    missed: list[str] = []          # teammates of the last player, named once it is over
    share: str | None = None


class PlayIn(BaseModel):
    seed: int = Field(ge=1)
    guesses: list[str] = Field(default_factory=list, max_length=game.MAX_GUESSES)
    guess: str | None = None
    stop: bool = False


class PlayOut(BaseModel):
    linked: bool | None = None      # None when no new guess was sent
    state: StateOut


def _label(seed: int) -> tuple[str, bool, datetime.date | None]:
    if seed < game.PRACTICE_SEED_FLOOR:
        try:
            day = datetime.date.fromordinal(seed)
        except ValueError:
            raise HTTPException(status_code=400, detail="That is not a chain.") from None
        return f"{day.day} {day.strftime('%b %Y')}", True, day
    return f"Chain {seed}", False, None


def _crests(p) -> list[str]:
    return [c for c in (crests.franchise_crest(f) for f in p.franchises) if c]


def _step(p, shared: list[str] | None = None) -> StepOut:
    return StepOut(person_id=p.person_id, name=p.name, shared=game.describe(shared or []),
                   crests=_crests(p))


def _start(seed: int):
    return game.start_player(bingo_routes.facts().players, seed)


def _puzzle_out(seed: int) -> PuzzleOut:
    label, daily, day = _label(seed)
    return PuzzleOut(seed=seed, label=label, daily=daily, date=day.isoformat() if day else None,
                     strikes=game.STRIKES, start=_step(_start(seed)))


def _state_out(w: game.World, state: game.State, seed: int) -> StateOut:
    label, daily, _ = _label(seed)
    steps = [_step(w.players[state.chain[0]])]
    steps += [_step(w.players[pid], sq) for pid, sq in zip(state.chain[1:], state.shared)]
    over = state.finished
    return StateOut(
        chain=steps, links=state.links, clubs=state.clubs(), strikes=state.strikes,
        strikes_left=game.STRIKES - state.strikes, tried_here=sorted(state.tried_here()),
        finished=over, stopped=state.stopped,
        missed=game.missed(w, state) if over else [],
        share=game.share_text(w, state, label, daily=daily, seed=seed) if over else None)


@router.get("/api/chain/today", response_model=PuzzleOut)
def chain_today(response: Response) -> PuzzleOut:
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=300"
    return _puzzle_out(bingo.daily_seed(bingo_routes.today()))


@router.get("/api/chain", response_model=PuzzleOut)
def chain_puzzle(response: Response, seed: int | None = None) -> PuzzleOut:
    """A chain by seed -- a shared link's, or a fresh practice one when none is given."""
    if seed is None:
        response.headers["Cache-Control"] = "no-store"
        seed = bingo.new_seed()
    else:
        response.headers["Cache-Control"] = _FOREVER
    return _puzzle_out(seed)


@router.post("/api/chain/play", response_model=PlayOut)
def chain_play(body: PlayIn, response: Response) -> PlayOut:
    """Replay the guesses so far and, if one is sent, apply it. A plain replay is how a
    returning player's page asks 'where was I'."""
    response.headers["Cache-Control"] = "no-store"
    w = world()
    start = _start(body.seed)
    try:
        state = game.replay(w, start, list(body.guesses))
        linked = game.guess(w, state, body.guess) if body.guess is not None else None
        if body.stop and not state.finished:
            state.stopped = True
    except game.ChainError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PlayOut(linked=linked, state=_state_out(w, state, body.seed))
