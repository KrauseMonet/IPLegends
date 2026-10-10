"""The puzzle games' signed-in half [A188]: recording a finished daily, and Common Ground's
server-clocked attempt. Everything else about the games (`/api/bingo/*`, `/api/guess/*`,
`/api/xi/*`, `/api/ground/*`) stays stateless and anonymous and is untouched.

**Signed in only, and that is the feature.** A leaderboard needs a stable identity to rank and a
primary key to hold one attempt per day to (migration 046). A signed-out visitor still plays every
game exactly as before; they just are not ranked, and the page says why.

**The page never says how it did.** `/api/puzzles/submit` takes the moves, `game.puzzle_results`
replays them against the day's puzzle and the replayed outcome is what is recorded.
"""

from __future__ import annotations

import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from game import bingo, ground, guess as guess_game, puzzle_results as pr, xi as xi_game
from web import auth, bingo_routes, db, ground_routes, puzzle_results, xi_routes

router = APIRouter()


def _account(request: Request) -> int:
    token = request.cookies.get(auth.COOKIE_NAME)
    account_id = auth.verify_session_cookie(token) if token is not None else None
    if account_id is None:
        raise HTTPException(status_code=401, detail="Sign in to have your result ranked.")
    return account_id


# --- an untimed result -----------------------------------------------------------------------

class CellGuess(BaseModel):
    cell: int = Field(ge=0, le=8)
    id: str = Field(max_length=64)


class SubmitIn(BaseModel):
    game: Literal["bingo", "guess", "xi"]
    seed: int = Field(ge=1)
    cells: list[CellGuess] = Field(default_factory=list, max_length=bingo.GUESSES)   # Bingo's moves
    ids: list[str] = Field(default_factory=list, max_length=xi_game.MAX_GUESSES)     # the others'
    gave_up: bool = False


class SubmitOut(BaseModel):
    recorded: bool          # false if this account had already finished this puzzle
    game: str
    date: str
    score: int
    solved: bool
    rank: int | None = None
    of: int = 0


def _outcome(body: SubmitIn) -> tuple[pr.Outcome, list]:
    facts = bingo_routes.facts()
    if body.game == "bingo":
        grid = bingo.make_grid(facts, body.seed)
        guesses = [(c.cell, c.id) for c in body.cells]
        return pr.verify_bingo(grid, facts, guesses), [{"cell": c, "id": i} for c, i in guesses]
    if body.game == "guess":
        answer = guess_game.mystery(facts.players, body.seed)
        return pr.verify_guess(facts.players, answer, list(body.ids), body.gave_up), list(body.ids)
    side = xi_game.pick_side(xi_routes.sides(), body.seed)
    return pr.verify_xi(facts.players, side, list(body.ids), body.gave_up), list(body.ids)


@router.post("/api/puzzles/submit", response_model=SubmitOut)
def submit(body: SubmitIn, request: Request, response: Response) -> SubmitOut:
    response.headers["Cache-Control"] = "no-store"
    account_id = _account(request)
    try:
        day = pr.daily_date(body.seed, bingo_routes.today())
        outcome, moves = _outcome(body)
    except pr.PuzzleResultError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with db.connection() as conn:
        new = puzzle_results.record(conn, account_id, day, outcome, moves)
        mine = puzzle_results.result_for(conn, account_id, body.game, day)
        rank, of = puzzle_results.rank_of(conn, body.game, day, account_id)
    # The stored result is what counts: a repeat submission reports the first one, never this one.
    return SubmitOut(recorded=new, game=body.game, date=day.isoformat(),
                     score=mine["score"] if mine else outcome.score,
                     solved=mine["solved"] if mine else outcome.solved, rank=rank, of=of)


# --- Common Ground, on the server's clock ----------------------------------------------------

class RankedOut(BaseModel):
    started: bool
    finished: bool = False
    late: bool = False                 # the guess that arrived came after the clock had run out
    remaining_ms: int = 0              # the SERVER's reading: the page's countdown is built from it
    state: ground_routes.StateOut | None = None
    rank: int | None = None
    of: int = 0


class RankedPlayIn(BaseModel):
    guess: str | None = Field(default=None, max_length=64)
    end: bool = False


class RankedPlayOut(RankedOut):
    hit: bool | None = None
    kind: str | None = None


def _today_seed() -> tuple[datetime.date, int]:
    day = bingo_routes.today()
    return day, bingo.daily_seed(day)


def _remaining(elapsed_ms: int) -> int:
    return max(0, ground.LIMIT_MS - elapsed_ms)


def _ranked_out(conn, account_id: int, day, seed: int, g: ground.Ground, puzzle: ground.Puzzle,
                view: dict | None, state: ground.State | None = None) -> RankedOut:
    if view is None:
        return RankedOut(started=False)
    if state is None:
        moves = [ground.Move(m["id"], int(m["t"])) for m in view["moves"]]
        end_t = (view.get("detail") or {}).get("end_t") if view["finished"] and view["ended"] else None
        state = ground.replay(g, puzzle, moves, end_t=end_t)
    rank = of = None
    if view["finished"]:
        rank, of = puzzle_results.rank_of(conn, "common", day, account_id, g)
    return RankedOut(started=True, finished=view["finished"], late=bool(view.get("late")),
                     remaining_ms=0 if view["finished"] else _remaining(view["elapsed_ms"]),
                     state=ground_routes._state_out(g, state, seed), rank=rank, of=of or 0)


@router.get("/api/ground/ranked", response_model=RankedOut)
def ranked_state(request: Request, response: Response) -> RankedOut:
    """Where this player's attempt at today's pair stands: not started, running (with the time
    left on the server's clock) or finished (with their rank)."""
    response.headers["Cache-Control"] = "no-store"
    account_id = _account(request)
    day, seed = _today_seed()
    g = ground_routes.ground()
    puzzle = g.puzzle(seed)
    with db.connection() as conn:
        return _ranked_out(conn, account_id, day, seed, g, puzzle,
                           puzzle_results.ranked_view(conn, account_id, day))


@router.post("/api/ground/ranked/start", response_model=RankedOut)
def ranked_start(request: Request, response: Response) -> RankedOut:
    """Press Start. The clock that counts begins here, on the database's clock, and pressing it
    again (a reload, a double click) reports the clock already running."""
    response.headers["Cache-Control"] = "no-store"
    account_id = _account(request)
    day, seed = _today_seed()
    g = ground_routes.ground()
    puzzle = g.puzzle(seed)
    with db.connection() as conn:
        view = puzzle_results.ranked_start(conn, account_id, day)
        return _ranked_out(conn, account_id, day, seed, g, puzzle, view)


@router.post("/api/ground/ranked/play", response_model=RankedPlayOut)
def ranked_play(body: RankedPlayIn, request: Request, response: Response) -> RankedPlayOut:
    response.headers["Cache-Control"] = "no-store"
    account_id = _account(request)
    day, seed = _today_seed()
    g = ground_routes.ground()
    puzzle = g.puzzle(seed)
    try:
        with db.connection() as conn:
            state, view, applied = puzzle_results.ranked_play(conn, g, puzzle, account_id, day,
                                                              guess_id=body.guess, end=body.end)
            out = _ranked_out(conn, account_id, day, seed, g, puzzle, view, state)
    except puzzle_results.OpenAttempt as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except pr.PuzzleResultError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    hit = kind = None
    if applied:
        hit, kind = state.hits[-1], state.kinds[-1]
    return RankedPlayOut(**out.model_dump(), hit=hit, kind=kind)


# --- the boards ------------------------------------------------------------------------------

BOARD_TOP = 20                  # rows shown; a player outside them is shown below as themselves
GAME_LABELS = {"bingo": "Bingo", "guess": "Guess the Player", "common": "Common Ground",
               "xi": "Name the XI", "overall": "Overall"}


class BoardRowOut(BaseModel):
    rank: int
    username: str
    kit: dict | None = None
    points: int
    solved: bool | None = None
    elapsed_ms: int | None = None
    found: int | None = None
    total: int | None = None
    wrong: int | None = None
    games: dict | None = None         # the overall board only: each game's points, share and rank
    played: int | None = None
    you: bool = False


class BoardOut(BaseModel):
    game: str
    label: str
    date: str
    finished: int                     # how many players are on the board
    rows: list[BoardRowOut]
    me: BoardRowOut | None = None     # the caller's own row when it is not among `rows`


def _soft_account(request: Request) -> int | None:
    token = request.cookies.get(auth.COOKIE_NAME)
    return auth.verify_session_cookie(token) if token is not None else None


def _board_date(raw: str | None) -> datetime.date:
    today = bingo_routes.today()
    if raw is None:
        return today
    try:
        day = datetime.date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="That is not a date.") from None
    if day > today:
        raise HTTPException(status_code=400, detail="That day has not happened yet.")
    return day


def _row_out(row: dict, mine: int | None, overall_board: bool) -> BoardRowOut:
    d = row.get("detail") or {}
    return BoardRowOut(
        rank=row["rank"], username=row["username"], kit=row["kit"],
        points=row["total"] if overall_board else row["points"],
        solved=None if overall_board else row["solved"], elapsed_ms=None if overall_board else row["elapsed_ms"],
        found=d.get("found"), total=d.get("total"), wrong=d.get("wrong"),
        games=row["games"] if overall_board else None, played=row.get("played"),
        you=mine is not None and row["account_id"] == mine)


@router.get("/api/puzzles/board/{game}", response_model=BoardOut)
def board(game: str, request: Request, response: Response, date: str | None = None) -> BoardOut:
    """One day's board for a game, or `overall` for the combined one. Public: a leaderboard is a
    thing to look at before you have played. If the caller is signed in and not among the top
    rows, their own row comes back as `me` so they can still find themselves."""
    if game not in GAME_LABELS:
        raise HTTPException(status_code=404, detail="No such board.")
    response.headers["Cache-Control"] = "private, max-age=10"
    day = _board_date(date)
    mine = _soft_account(request)
    g = ground_routes.ground()
    with db.connection() as conn:
        rows = (puzzle_results.overall(conn, day, g) if game == "overall"
                else puzzle_results.board(conn, game, day, g, limit=0))
    out = [_row_out(r, mine, game == "overall") for r in rows]
    top = out[:BOARD_TOP]
    me = next((r for r in out[BOARD_TOP:] if r.you), None)
    return BoardOut(game=game, label=GAME_LABELS[game], date=day.isoformat(), finished=len(out),
                    rows=top, me=me)
