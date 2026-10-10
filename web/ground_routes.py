"""Common Ground's API [A188]. Stateless, like Bingo's and Guess the Player's: the client holds
the moves so far, each request sends them, and the server replays them and answers. No
database -- the squads come from the committed facts snapshot.

The clock is the client's in this anonymous mode: each guess carries the milliseconds since the
clock started, and the server checks only that they are in order and inside the limit. A
ranked attempt takes its times from the server instead (a later phase of A188).
"""

from __future__ import annotations

from functools import cache

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from game import bingo, ground as game
from web import bingo_routes, crests

router = APIRouter()

_FOREVER = "public, max-age=3600, s-maxage=86400"


@cache
def _ground(players_id: int) -> game.Ground:
    """Built once per set of players. Keyed on the dict's identity so that a test which swaps
    the facts gets a fresh index rather than a stale one."""
    return game.Ground(bingo_routes.facts().players)


def ground() -> game.Ground:
    return _ground(id(bingo_routes.facts().players))


class PlayerOut(BaseModel):
    person_id: str
    name: str
    crests: list[str] = []          # his clubs, as crests


class AnswerOut(PlayerOut):
    with_a: list[str] = []          # the squads he shared with the first player
    with_b: list[str] = []          # ... and with the second
    t: int | None = None            # when it was found, in ms (None for one that was not)


class WrongOut(BaseModel):
    person_id: str
    name: str
    kind: str                       # only_a, only_b or neither
    t: int


class ScoreOut(BaseModel):
    found: int
    wrong: int
    total: int
    base: int
    penalty: int
    bonus: int
    points: int


class PuzzleOut(BaseModel):
    seed: int
    label: str
    daily: bool
    date: str | None = None
    a: PlayerOut
    b: PlayerOut
    total: int                      # how many players were a teammate of both
    limit_seconds: int
    points_per_teammate: int
    penalty_per_wrong: int
    bonus_per_second: int


class StateOut(BaseModel):
    found: list[AnswerOut]
    wrong: list[WrongOut]
    score: ScoreOut
    finished: bool
    reason: str | None = None       # all, time or gave_up -- once it is over
    elapsed_ms: int = 0
    unfound: list[AnswerOut] = []   # named once it is over
    share: str | None = None


class MoveIn(BaseModel):
    id: str = Field(max_length=64)
    t: int = Field(ge=0, le=game.LIMIT_MS)


class PlayIn(BaseModel):
    seed: int = Field(ge=1)
    guesses: list[MoveIn] = Field(default_factory=list, max_length=game.MAX_GUESSES)
    guess: MoveIn | None = None
    end: bool = False
    end_t: int | None = Field(default=None, ge=0, le=game.LIMIT_MS)


class PlayOut(BaseModel):
    hit: bool | None = None         # None when no new guess was sent
    kind: str | None = None         # for a miss: only_a, only_b or neither
    state: StateOut


def _crests(p) -> list[str]:
    return [c for c in (crests.franchise_crest(f) for f in p.franchises) if c]


def _player(g: game.Ground, pid: str) -> PlayerOut:
    p = g.players[pid]
    return PlayerOut(person_id=pid, name=p.name, crests=_crests(p))


def _answer(g: game.Ground, state: game.State, pid: str, t: int | None = None) -> AnswerOut:
    p = g.players[pid]
    with_a, with_b = game.answer_lines(g, state, pid)
    return AnswerOut(person_id=pid, name=p.name, crests=_crests(p), with_a=with_a, with_b=with_b, t=t)


def _puzzle(seed: int) -> tuple[game.Ground, game.Puzzle]:
    g = ground()
    return g, g.puzzle(seed)


def _puzzle_out(seed: int) -> PuzzleOut:
    try:
        label, daily, day = game.label_for(seed)
    except game.GroundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    g, p = _puzzle(seed)
    return PuzzleOut(seed=seed, label=label, daily=daily, date=day.isoformat() if day else None,
                     a=_player(g, p.a), b=_player(g, p.b), total=len(p.answers),
                     limit_seconds=game.LIMIT_SECONDS, points_per_teammate=game.POINTS_PER_TEAMMATE,
                     penalty_per_wrong=game.PENALTY_PER_WRONG, bonus_per_second=game.BONUS_PER_SECOND_LEFT)


def _reason(state: game.State) -> str | None:
    if state.complete:
        return "all"
    return state.ended


def _state_out(g: game.Ground, state: game.State, seed: int) -> StateOut:
    label, daily, _ = game.label_for(seed)
    sc = game.score(state)
    over = state.finished
    return StateOut(
        found=[_answer(g, state, m.id, m.t) for m in state.found],
        wrong=[WrongOut(person_id=m.id, name=g.players[m.id].name, kind=k, t=m.t)
               for m, k in state.wrong],
        score=ScoreOut(found=sc.found, wrong=sc.wrong, total=sc.total, base=sc.base,
                       penalty=sc.penalty, bonus=sc.bonus, points=sc.points),
        finished=over, reason=_reason(state) if over else None,
        elapsed_ms=state.elapsed_ms if over else 0,
        unfound=[_answer(g, state, pid) for pid in game.unfound(g, state)] if over else [],
        share=game.share_text(state, label, daily=daily, seed=seed) if over else None)


@router.get("/api/ground/today", response_model=PuzzleOut)
def ground_today(response: Response) -> PuzzleOut:
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=300"
    return _puzzle_out(bingo.daily_seed(bingo_routes.today()))


@router.get("/api/ground", response_model=PuzzleOut)
def ground_puzzle(response: Response, seed: int | None = None) -> PuzzleOut:
    """A puzzle by seed -- a shared link's, or a fresh practice one when none is given."""
    if seed is None:
        response.headers["Cache-Control"] = "no-store"
        seed = bingo.new_seed()
    else:
        response.headers["Cache-Control"] = _FOREVER
    return _puzzle_out(seed)


@router.post("/api/ground/play", response_model=PlayOut)
def ground_play(body: PlayIn, response: Response) -> PlayOut:
    """Replay the moves so far and, if one is sent, apply it. A plain replay is how a
    returning player's page asks 'where was I'."""
    response.headers["Cache-Control"] = "no-store"
    g, puzzle = _puzzle(body.seed)
    try:
        state = game.replay(g, puzzle, [game.Move(m.id, m.t) for m in body.guesses])
        hit = kind = None
        if body.guess is not None:
            hit = game.guess(g, state, body.guess.id, body.guess.t)
            kind = state.kinds[-1]
        if body.end:
            game.end(state, body.end_t if body.end_t is not None else state.last_t)
    except game.GroundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PlayOut(hit=hit, kind=kind, state=_state_out(g, state, body.seed))
