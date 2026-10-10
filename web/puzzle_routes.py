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

from game import badges, bingo, ground, guess as guess_game, puzzle_results as pr, rarity, xi as xi_game
from web import auth, bingo_routes, daily as daily_lib, db, ground_routes, puzzle_results, xi_routes

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


class RarityPickOut(BaseModel):
    slot: str
    person_id: str
    name: str
    share: float | None = None        # the part of the field that made this pick; None while the sample is small
    bonus: int = 0


class RarityOut(BaseModel):
    voters: int
    min_voters: int
    rarity_max: int
    enough: bool                      # at least one pick has a big enough sample to be scored
    bonus: int                        # the whole result's rarity bonus
    picks: list[RarityPickOut]


class SubmitOut(BaseModel):
    recorded: bool          # false if this account had already finished this puzzle
    game: str
    date: str
    score: int              # the game's own score for the moves
    bonus: int = 0          # rarity, on top (0 until a slot has the voters for it)
    points: int = 0         # score + bonus: what the board ranks
    solved: bool
    rank: int | None = None
    of: int = 0
    rarity: RarityOut | None = None


DEVICE_HEADER = "X-Daily-Device"


def _rarity_out(conn, game: str, day, picks) -> RarityOut | None:
    """Rarity for a result's correct picks, named. None for a game whose picks are not counted."""
    if game not in pr.PICK_GAMES:
        return None
    raw = puzzle_results.rarity_of(conn, game, day, picks)
    players = bingo_routes.facts().players
    return RarityOut(**{**raw, "picks": [RarityPickOut(**p, name=players[p["person_id"]].name)
                                         for p in raw["picks"]]})


def _picks_from_detail(detail: dict) -> list[pr.Pick]:
    return [pr.Pick(slot, pid, base) for slot, pid, base in (detail or {}).get("picks", [])]


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
    device = request.headers.get(DEVICE_HEADER)
    with db.connection() as conn:
        new = puzzle_results.record(conn, account_id, day, outcome, moves, device_id=device)
        mine = puzzle_results.result_for(conn, account_id, body.game, day)
        row, of = puzzle_results.standing(conn, body.game, day, account_id)
        # The stored result is what counts: a repeat submission reports the first one, never this one.
        stored = _picks_from_detail(mine["detail"]) if mine else list(outcome.picks)
        rar = _rarity_out(conn, body.game, day, stored)
    return SubmitOut(recorded=new, game=body.game, date=day.isoformat(),
                     score=mine["score"] if mine else outcome.score,
                     bonus=row["bonus"] if row else 0, points=row["points"] if row else outcome.score,
                     solved=mine["solved"] if mine else outcome.solved,
                     rank=row["rank"] if row else None, of=of, rarity=rar)


# --- a signed-out player's picks, for rarity -------------------------------------------------

class MoveIn(BaseModel):
    id: str = Field(max_length=64)
    t: int = Field(ge=0, le=ground.LIMIT_MS)


class PicksIn(BaseModel):
    game: Literal["bingo", "xi", "common"]
    seed: int = Field(ge=1)
    cells: list[CellGuess] = Field(default_factory=list, max_length=bingo.GUESSES)
    ids: list[str] = Field(default_factory=list, max_length=xi_game.MAX_GUESSES)
    moves: list[MoveIn] = Field(default_factory=list, max_length=ground.MAX_GUESSES)   # Common Ground's
    end_t: int | None = Field(default=None, ge=0, le=ground.LIMIT_MS)
    gave_up: bool = False


class PicksOut(BaseModel):
    counted: bool            # false for a repeat, or for somebody signed in (their result carries them)
    rarity: RarityOut | None = None


def _picks_outcome(body: PicksIn) -> pr.Outcome:
    facts = bingo_routes.facts()
    if body.game == "bingo":
        grid = bingo.make_grid(facts, body.seed)
        return pr.verify_bingo(grid, facts, [(c.cell, c.id) for c in body.cells])
    if body.game == "xi":
        side = xi_game.pick_side(xi_routes.sides(), body.seed)
        return pr.verify_xi(facts.players, side, list(body.ids), body.gave_up)
    g = ground_routes.ground()
    return pr.verify_common(g, g.puzzle(body.seed), [ground.Move(m.id, m.t) for m in body.moves],
                            end_t=body.end_t)


@router.post("/api/puzzles/picks", response_model=PicksOut)
def picks(body: PicksIn, request: Request, response: Response) -> PicksOut:
    """Count a SIGNED-OUT player's correct picks toward rarity, and say how rare they were.

    They are never ranked: a pick is a row of counts with a browser's random id, so nothing here
    can put anybody on a board. The moves are replayed exactly as a ranked result's are, so a
    forged list either fails to replay or replays into a real set of picks. Somebody signed in
    is not counted here at all -- their result carries their picks -- so the same person cannot
    count twice by being on two routes."""
    response.headers["Cache-Control"] = "no-store"
    try:
        day = pr.daily_date(body.seed, bingo_routes.today())
        outcome = _picks_outcome(body)
        signed_in = _soft_account(request) is not None
        voter = None if signed_in else puzzle_results.device_voter(request.headers.get(DEVICE_HEADER))
    except pr.PuzzleResultError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with db.connection() as conn:
        counted = False if voter is None else puzzle_results.record_device_picks(
            conn, body.game, day, voter, outcome.picks)
        rar = _rarity_out(conn, body.game, day, outcome.picks)
    return PicksOut(counted=counted, rarity=rar)


# --- Common Ground, on the server's clock ----------------------------------------------------

class RankedOut(BaseModel):
    started: bool
    finished: bool = False
    late: bool = False                 # the guess that arrived came after the clock had run out
    remaining_ms: int = 0              # the SERVER's reading: the page's countdown is built from it
    state: ground_routes.StateOut | None = None
    rank: int | None = None
    of: int = 0
    points: int = 0                    # what the board ranks: the score plus the rarity bonus
    bonus: int = 0
    rarity: RarityOut | None = None


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
    rank = of = rar = None
    points = bonus = 0
    if view["finished"]:
        row, of = puzzle_results.standing(conn, "common", day, account_id, g)
        if row is not None:
            rank, points, bonus = row["rank"], row["points"], row["bonus"]
        rar = _rarity_out(conn, "common", day, [pr.Pick(m.id, m.id) for m in state.found])
    return RankedOut(started=True, finished=view["finished"], late=bool(view.get("late")),
                     remaining_ms=0 if view["finished"] else _remaining(view["elapsed_ms"]),
                     state=ground_routes._state_out(g, state, seed), rank=rank, of=of or 0,
                     points=points, bonus=bonus, rarity=rar)


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


# --- a player's own puzzle record ------------------------------------------------------------

class BadgeOut(BaseModel):
    key: str
    name: str
    blurb: str
    group: str
    target: int
    progress: int
    earned: bool
    earned_on: str | None = None


class GameRecordOut(BaseModel):
    played: int
    solved: int
    best: int                         # the game's own score, rarity aside


class PuzzleRecordOut(BaseModel):
    finished: int
    days: int                         # distinct days with a puzzle finished
    streak: int
    best_streak: int
    games: dict[str, GameRecordOut]
    badges: list[BadgeOut]


@router.get("/api/puzzles/me", response_model=PuzzleRecordOut)
def my_record(request: Request, response: Response) -> PuzzleRecordOut:
    """What the signed-in player has done in the daily puzzles, and the badges that adds up to.
    Derived from their results every time it is asked (A19): nothing about a badge is stored."""
    response.headers["Cache-Control"] = "no-store"
    account_id = _account(request)
    with db.connection() as conn:
        results = puzzle_results.account_results(conn, account_id)
        rare = puzzle_results.rare_pick_days(conn, account_id)
    days = sorted({r.day for r in results})
    streak, best = daily_lib.streaks(days, bingo_routes.today())
    games = {}
    for game in badges.GAMES:
        mine = [r for r in results if r.game == game]
        games[game] = GameRecordOut(played=len(mine), solved=sum(r.solved for r in mine),
                                    best=max((r.score for r in mine), default=0))
    return PuzzleRecordOut(
        finished=len(results), days=len(days), streak=streak, best_streak=best, games=games,
        badges=[BadgeOut(key=b.key, name=b.name, blurb=b.blurb, group=b.group, target=b.target,
                         progress=b.progress, earned=b.earned,
                         earned_on=b.earned_on.isoformat() if b.earned_on else None)
                for b in badges.evaluate(results, rare)])
