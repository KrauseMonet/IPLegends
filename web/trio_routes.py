"""Three to Make's API [A196]. Stateless like the other puzzle games: the page sends its trio
and the server scores it. No database -- the stats come from the committed facts snapshot.

**The stats stay hidden until the trio is locked in.** The puzzle carries the target and the
stat's name and nothing about any player's figure; the figures arrive only with a finished
result. (A determined player can still look a number up elsewhere: that is what the clock is
for, and in a ranked attempt the clock is the server's -- `puzzle_routes`.)

Anonymous play keeps its own clock: the page says when the trio was locked in, and the server
checks only that it is inside the limit. Never ranked.
"""

from __future__ import annotations

from functools import cache

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from game import bingo, trio as game
from web import bingo_routes, crests

router = APIRouter()

_FOREVER = "public, max-age=3600, s-maxage=86400"


@cache
def _trio(players_id: int) -> game.Trio:
    """Built once per set of players; keyed on the dict's identity so a test that swaps the facts
    gets a fresh one."""
    return game.Trio(bingo_routes.facts().players)


def trio() -> game.Trio:
    return _trio(id(bingo_routes.facts().players))


@cache
def _exact(players_id: int, stat: str, target: int) -> int:
    return trio().exact_count(game.STAT_BY_KEY[stat], target)


class PuzzleOut(BaseModel):
    seed: int
    label: str
    daily: bool
    date: str | None = None
    stat: str                       # the PlayerFacts field
    stat_label: str                 # "IPL career runs"
    unit: str
    target: int
    limit_seconds: int
    picks: int


class PickOut(BaseModel):
    person_id: str
    name: str
    value: int                      # his figure for the day's stat -- only ever in a finished result
    crests: list[str] = []


class ResultOut(BaseModel):
    picks: list[PickOut]
    total: int | None               # the trio's sum; None if no trio was locked in
    distance: int | None
    exact: bool
    verdict: str                    # "Bang on", "12 short", "No trio in time"
    score: int                      # 0-1000 closeness, the overall board's number
    elapsed_ms: int
    example: list[PickOut]          # one trio that makes the target exactly
    exact_count: int                # how many trios, from every player, hit it exactly
    share: str


class PlayIn(BaseModel):
    seed: int = Field(ge=1)
    picks: list[str] | None = Field(default=None, max_length=game.PICKS)   # None: the clock ran out
    t: int = Field(ge=0, le=game.LIMIT_MS)


def _pick(g: game.Trio, stat: game.Stat, pid: str) -> PickOut:
    p = g.players[pid]
    return PickOut(person_id=pid, name=p.name, value=g.value(stat, pid),
                   crests=[c for c in (crests.franchise_crest(f) for f in p.franchises) if c])


def puzzle_out(seed: int) -> PuzzleOut:
    try:
        label, daily, day = game.label_for(seed)
    except game.TrioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    p = trio().puzzle(seed)
    return PuzzleOut(seed=seed, label=label, daily=daily, date=day.isoformat() if day else None,
                     stat=p.stat.key, stat_label=p.stat.label, unit=p.stat.unit, target=p.target,
                     limit_seconds=game.LIMIT_SECONDS, picks=game.PICKS)


def result_out(seed: int, result: game.Result) -> ResultOut:
    g = trio()
    p = g.puzzle(seed)
    label, daily, _ = game.label_for(seed)
    return ResultOut(
        picks=[_pick(g, p.stat, pid) for pid in result.picks], total=result.total,
        distance=result.distance, exact=result.exact, verdict=game.verdict(result, p),
        score=game.score(p, result), elapsed_ms=result.t,
        example=[_pick(g, p.stat, pid) for pid in p.example],
        exact_count=_exact(id(g.players), p.stat.key, p.target),
        share=game.share_text(p, result, label, daily=daily))


@router.get("/api/trio/today", response_model=PuzzleOut)
def trio_today(response: Response) -> PuzzleOut:
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=300"
    return puzzle_out(bingo.daily_seed(bingo_routes.today()))


@router.get("/api/trio", response_model=PuzzleOut)
def trio_puzzle(response: Response, seed: int | None = None) -> PuzzleOut:
    """A puzzle by seed -- a shared link's, or a fresh practice one when none is given."""
    if seed is None:
        response.headers["Cache-Control"] = "no-store"
        seed = bingo.new_seed()
    else:
        response.headers["Cache-Control"] = _FOREVER
    return puzzle_out(seed)


@router.post("/api/trio/play", response_model=ResultOut)
def trio_play(body: PlayIn, response: Response) -> ResultOut:
    """Score a trio locked in at `t` ms (the page's own clock), or a clock that ran out with
    no trio (`picks` null). The figures come back only here, once it is over."""
    response.headers["Cache-Control"] = "no-store"
    g = trio()
    try:
        game.label_for(body.seed)
        result = game.play(g, g.puzzle(body.seed), body.picks, body.t)
    except game.TrioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result_out(body.seed, result)
