"""Bingo's API [A182]. A router of its own, so the 4,000-line `web/app.py` does not grow.

Stateless, like the draft (A62): the client holds the list of guesses so far and sends it
with each new one; the server replays the lot against the grid and answers. There is
nothing to store and no account to need, so a Bingo request touches NO database -- the
players come from the committed `data/puzzle_facts.json.gz` snapshot.

Nothing here is a secret worth guarding. There is no leaderboard, so the answers being
one `/answers` call away costs nobody anything; the replay is for the rules (nine
guesses, a player used once), which a friendly client would honour anyway but the server
should not have to trust.
"""

from __future__ import annotations

import datetime
from functools import cache

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from game import bingo, puzzle_facts
from web import crests

router = APIRouter()


def today() -> datetime.date:
    """The challenge date in UTC -- one shared day boundary, as the daily's (A128)."""
    return datetime.datetime.now(datetime.timezone.utc).date()


@cache
def _facts() -> bingo.Facts | None:
    doc = puzzle_facts.read_document()
    return bingo.Facts(puzzle_facts.players_from(doc)) if doc else None


def facts() -> bingo.Facts:
    f = _facts()
    if f is None:
        raise HTTPException(status_code=503, detail="The puzzle data is not available.")
    return f


class AxisOut(BaseModel):
    key: str
    label: str
    kind: str
    crest: str | None = None


class GridOut(BaseModel):
    seed: int
    label: str
    daily: bool
    date: str | None = None
    guesses: int
    rows: list[AxisOut]
    cols: list[AxisOut]


class PlacedOut(BaseModel):
    cell: int
    person_id: str
    name: str
    points: int
    answers: int      # how many players fit this cell: the cell's rarity, shown once solved


class WrongOut(BaseModel):
    cell: int
    person_id: str
    name: str


class StateOut(BaseModel):
    placed: list[PlacedOut]
    wrong: list[WrongOut]
    guesses_left: int
    score: int
    finished: bool
    share: str | None = None


class PlayIn(BaseModel):
    seed: int = Field(ge=1)
    guesses: list[tuple[int, str]] = Field(default_factory=list, max_length=bingo.GUESSES)
    guess: tuple[int, str] | None = None


class PlayOut(BaseModel):
    correct: bool | None = None       # None when no new guess was sent (a plain replay)
    state: StateOut


class PlayerOut(BaseModel):
    id: str
    name: str


class RevealCellOut(BaseModel):
    n: int
    sample: list[str]


def _axis(c: bingo.Criterion) -> AxisOut:
    return AxisOut(key=c.key, label=c.label, kind=c.kind,
                   crest=crests.franchise_crest(c.label) if c.kind == "franchise" else None)


def _label(seed: int, daily: bool, date: datetime.date | None) -> str:
    return f"{date.day} {date.strftime('%b %Y')}" if daily and date else f"Grid {seed}"


def _grid_out(grid: bingo.Grid, *, date: datetime.date | None = None) -> GridOut:
    return GridOut(seed=grid.seed, label=_label(grid.seed, date is not None, date),
                   daily=date is not None, date=date.isoformat() if date else None,
                   guesses=bingo.GUESSES, rows=[_axis(c) for c in grid.rows],
                   cols=[_axis(c) for c in grid.cols])


def _state_out(grid: bingo.Grid, f: bingo.Facts, state: bingo.State) -> StateOut:
    daily = grid.seed < bingo.PRACTICE_SEED_FLOOR
    day = datetime.date.fromordinal(grid.seed) if daily else None
    return StateOut(
        placed=[PlacedOut(cell=cell, person_id=pid, name=f.players[pid].name,
                          points=grid.points(cell), answers=len(grid.cells[cell]))
                for cell, pid in sorted(state.placed.items())],
        wrong=[WrongOut(cell=cell, person_id=pid, name=f.players[pid].name)
               for cell, pid in state.wrong],
        guesses_left=state.guesses_left, score=state.score, finished=state.finished,
        share=bingo.share_text(grid, state, _label(grid.seed, daily, day), daily=daily)
        if state.finished else None)


def _grid(seed: int) -> bingo.Grid:
    try:
        return bingo.make_grid(facts(), seed)
    except RuntimeError as exc:                     # pragma: no cover -- 83% of draws pass
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# A grid is a pure function of its seed, so a seeded one never changes and can sit at the
# edge; today's is the only one that rolls over, and only at midnight UTC.
_FOREVER = "public, max-age=3600, s-maxage=86400"


@router.get("/api/bingo/today", response_model=GridOut)
def bingo_today(response: Response) -> GridOut:
    day = today()
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=300"
    return _grid_out(_grid(bingo.daily_seed(day)), date=day)


@router.get("/api/bingo", response_model=GridOut)
def bingo_grid(response: Response, seed: int | None = None) -> GridOut:
    """A grid by seed -- a shared link's, or a fresh practice one when none is given."""
    if seed is None:
        response.headers["Cache-Control"] = "no-store"
        seed = bingo.new_seed()
    else:
        response.headers["Cache-Control"] = _FOREVER
    day = None
    if seed < bingo.PRACTICE_SEED_FLOOR:
        try:
            day = datetime.date.fromordinal(seed)
        except ValueError:
            raise HTTPException(status_code=400, detail="That is not a grid.") from None
    return _grid_out(_grid(seed), date=day)


@router.get("/api/puzzles/players", response_model=list[PlayerOut])
def bingo_players(response: Response) -> list[PlayerOut]:
    """Everybody who can be guessed, for a puzzle's search box -- shared by every puzzle
    game, which is why it is not under /api/bingo. Names only: a hint such as the clubs a
    man played for would answer the question being asked."""
    response.headers["Cache-Control"] = "public, max-age=3600, s-maxage=86400"
    return [PlayerOut(id=p.person_id, name=p.name)
            for p in sorted(facts().players.values(), key=lambda p: (p.name, p.person_id))]


@router.post("/api/bingo/play", response_model=PlayOut)
def bingo_play(body: PlayIn, response: Response) -> PlayOut:
    """Replay the guesses so far and, if one is sent, apply it. A plain replay (no new
    guess) is how a returning player's page asks 'where was I'."""
    response.headers["Cache-Control"] = "no-store"
    f = facts()
    grid = _grid(body.seed)
    try:
        state = bingo.replay(grid, f, list(body.guesses))
        correct = bingo.guess(grid, f, state, *body.guess) if body.guess else None
    except bingo.BingoError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PlayOut(correct=correct, state=_state_out(grid, f, state))


@router.get("/api/bingo/answers", response_model=list[RevealCellOut])
def bingo_answers(response: Response, seed: int) -> list[RevealCellOut]:
    """How many players fit each cell and the best-known few. Offered when a grid is
    finished or given up."""
    response.headers["Cache-Control"] = _FOREVER
    return [RevealCellOut(**x) for x in bingo.reveal(_grid(seed), facts())]
