"""SPEC 11/1.2. The HTTP surface: a thin, stateless layer over the draft and the engine.

Thin is the requirement, not an aspiration. Every rule this API appears to enforce -- the
overseas cap, the deal-time guarantee, the forward check, five bowling options, a keeper,
eleven eligible batting positions -- is enforced in `etl.feasibility` or `game`, and is
reached from here by calling that code rather than by restating it. Nothing in this module
decides anything about cricket.

It holds no session state. The deck and the state model are read once at boot and never
re-queried, so a request touches the database exactly never (SPEC 11.3).
"""

from __future__ import annotations

import os
import pathlib
import random
import time
from contextlib import asynccontextmanager, contextmanager
from typing import Literal

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from etl.feasibility import REROLL_KINDS, TWELVE_SIZE, XI_SIZE, Card
from game.__main__ import overseas_status
from game.season import (
    MATCHES_EACH, TEAMS, ImpactPick, JourneyAccumulator, Side, TossElect, play, run_cup,
    run_league, run_playoffs,
)
from game.simulator import load_model
from web import rooms
from web import season_session
from web import session as sess

# Populated at boot. The deck is 3,337 cards and takes a few seconds to build; the state
# model is two stored grids. Both are immutable for the life of the process.
STATE: dict = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_dotenv()
    # DIRECT_URL, not DATABASE_URL: this runs once at startup, not per request, so the
    # pooler buys nothing and the unpooled endpoint is the one guaranteed to serve DDL-free
    # reads without PgBouncer in the way.
    with psycopg.connect(os.environ["DIRECT_URL"]) as conn:
        from etl.feasibility import load_deck
        STATE["deck"] = load_deck(conn)
        STATE["model"] = load_model(conn)
        STATE["unrated"] = _load_unrated(conn)
    yield
    STATE.clear()


def _load_unrated(conn) -> dict[int, list]:
    """Squad members with no rating, per franchise-season, for DISPLAY ONLY.

    A65/A71 rate every squad member today (from full evidence, from a prior, or from
    reputation alone), so this query returns zero rows against the current archive. It
    stays as a defensive net rather than being deleted -- a revised archive could
    reintroduce a squad member with no discipline assignable at all (A27's shape: nothing
    about the appearance suggested a role, so `derive_squads` never gave him a band or a
    usage to rate), and a deal that silently dropped such a man would misrepresent the
    squad. Anyone this returns is carried as a plain dict and never as a `Card`,
    deliberately: a Card is the draft's currency, and one of these entering the deck would
    make an unrated player draftable.
    """
    rows = conn.execute(
        """
        select s.franchise_season_id, p.primary_name, s.role, s.batting_band,
               p.is_overseas, s.matches_played,
               max(i.balls) filter (where i.discipline = 'batting'),
               max(i.balls) filter (where i.discipline = 'bowling')
        from squad_members s
        join people p on p.person_id = s.person_id
        left join player_season_impact i
               on i.franchise_season_id = s.franchise_season_id
              and i.person_id = s.person_id
        where not exists (
            select 1 from player_season_rating r
            where r.franchise_season_id = s.franchise_season_id
              and r.person_id = s.person_id)
        group by 1, 2, 3, 4, 5, 6
        order by p.primary_name
        """
    ).fetchall()
    out: dict[int, list] = {}
    for fs_id, name, role, band, overseas, matches, faced, bowled in rows:
        bits = [f"{matches} match{'' if matches == 1 else 'es'}"]
        if faced:
            bits.append(f"{faced} faced")
        if bowled:
            bits.append(f"{bowled} bowled")
        out.setdefault(fs_id, []).append({
            "person_id": "", "name": name, "franchise": None, "season_year": None,
            "band": band, "role": role, "kind": "unrated", "rating": None,
            "overseas": overseas, "positions": [],
            "blocked": ", ".join(bits) + " · no batting or bowling role recorded",
        })
    return out


app = FastAPI(title="IPLegends", version="0.1.0", lifespan=lifespan)

STATIC = pathlib.Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """One page, served from disk. The client is a static file with no build step: it
    reads `/api/meta` for the shape rather than embedding a copy of it (A19)."""
    return FileResponse(STATIC / "index.html")


# --- wire format ----------------------------------------------------------------------

class CardOut(BaseModel):
    person_id: str
    name: str
    franchise: str | None
    season_year: int | None
    band: str | None
    role: str | None
    kind: str = Field(description="batter | bowler | allrounder | keeper | unrated")
    keeper_eligible: bool = Field(
        description="A77: counts toward the squad's keeper requirement -- true if "
                    "`role` is 'keeper' this season, OR he kept in some OTHER season "
                    "of his own career. Independent of `kind`/`role`, which always "
                    "reflect THIS season alone.")
    rating: int | None = Field(description="A58/A60's integer 70-99 card rating")
    overseas: bool | None = Field(description="null means unknown, never domestic (A23)")
    positions: list[int] = Field(
        default_factory=list,
        description="batting numbers this player is eligible for (A72); "
                    "empty only for the defensive-net 'unrated' case")
    blocked: str | None = Field(default=None, description="why he cannot be taken")
    bat_runs: int | None = Field(default=None, description="null means he never batted this season")
    bat_balls: int | None = None
    bat_strike_rate: float | None = None
    bowl_wickets: int | None = Field(default=None, description="null means he never bowled this season")
    bowl_runs: int | None = None
    bowl_balls: int | None = None
    bowl_economy: float | None = None


class DealOut(BaseModel):
    fs_id: int
    franchise: str | None
    season_year: int | None
    options: list[CardOut]
    blocked: list[CardOut] = []


class SessionOut(BaseModel):
    state: str
    picks_made: int
    picks_total: int = TWELVE_SIZE
    overseas_taken: int
    overseas_cap: int
    rerolls_used: int
    rerolls_allowed: int
    picks: list[CardOut] = Field(description="drafted so far, in draft order")
    order: list[CardOut | None] = Field(description=f"length {XI_SIZE}, position i+1")
    impact: CardOut | None
    errors: list[str]
    deal: DealOut | None
    squad_complete: bool
    playable: bool = Field(description="squad_complete and every legality rule satisfied")


class PickIn(BaseModel):
    index: int = Field(ge=0, description="an index into the deal's options")
    slot: int = Field(ge=1, description="1-11 a batting position, or 12 for Impact")


class RerollIn(BaseModel):
    kind: str = Field(description=f"one of {REROLL_KINDS}: \"team\" for any other "
                                   f"franchise-season, \"season\" for a different year "
                                   f"of the same franchise just dealt")


class RepositionIn(BaseModel):
    from_slot: int = Field(ge=1, description="1-11 a batting position, or 12 for Impact")
    to_slot: int = Field(ge=1, description="1-11 a batting position, or 12 for Impact")


class BatterOut(BaseModel):
    name: str
    runs: int
    balls: int
    out: bool
    faced_any: bool = Field(description="false means he never came in to bat at all")
    strike_rate: float | None = Field(
        default=None, description="null only when faced_any is false")
    is_impact: bool = Field(
        default=False, description="he is playing as this side's Impact Player")


class BowlerOut(BaseModel):
    name: str
    overs: str
    runs: int
    wickets: int
    economy: float
    is_impact: bool = Field(
        default=False, description="he is playing as this side's Impact Player")


class OverOut(BaseModel):
    over: int
    bowler: str
    runs: int = Field(description="cumulative innings runs after this over")
    wickets: int = Field(description="cumulative wickets after this over")
    balls: int = Field(description="cumulative balls after this over")
    over_runs: int = Field(description="runs scored THIS over alone")
    over_wickets: int = Field(description="wickets that fell THIS over alone")


class InningsOut(BaseModel):
    runs: int
    wickets: int
    overs: str
    extras: int
    batting: list[BatterOut] = Field(description="in batting order, all eleven")
    bowling: list[BowlerOut] = Field(description="the five who bowled at least one ball")
    commentary: list[str] = Field(
        default_factory=list, description="fall-of-wicket lines, in order")
    over_log: list[OverOut] = Field(
        default_factory=list,
        description="one entry per FULLY completed over, for an over-by-over reveal -- "
                    "a partial final over (all out, or target chased mid-over) has no "
                    "entry; the final scorecard already covers that moment")


class StandingOut(BaseModel):
    pos: int
    name: str
    short: str
    you: bool
    played: int
    won: int
    lost: int
    tied: int
    points: int
    nrr: float


class ResultOut(BaseModel):
    stage: str
    home: str
    away: str
    home_score: str
    away_score: str
    winner: str | None
    margin: str
    yours: bool
    home_innings: InningsOut | None = Field(
        default=None, description="the scorecard -- null only for a hand-built Result")
    away_innings: InningsOut | None = None
    toss_won_by_you: bool | None = Field(
        default=None, description="null for every match that isn't your own -- there "
                                   "is no toss concept at all for one between two "
                                   "other historical sides")
    toss_elected: str | None = Field(default=None, description="'bat' | 'bowl' | null")


class JourneySquadEntryOut(BaseModel):
    """One drafted player as the journey card shows him: who he actually was that season
    (franchise, season, so the card reads as a real squad rather than a roster of loose
    names) plus what he did in THIS simulated tournament -- distinct from `CardOut`'s
    `bat_runs`/`bowl_wickets`, which are his real archive figures from the one season he
    was drafted out of, not this playthrough. Null means he never batted/bowled in this
    tournament, same convention as `CardOut` (never a manufactured zero)."""

    person_id: str
    name: str
    franchise: str | None
    season_year: int | None
    kind: str = Field(description="batter | bowler | allrounder | keeper | unrated")
    sim_bat_runs: int | None = None
    sim_bat_balls: int | None = None
    sim_bowl_wickets: int | None = None
    sim_bowl_runs: int | None = None
    sim_bowl_balls: int | None = None


class PendingTossOut(BaseModel):
    kind: Literal["toss"] = "toss"
    stage: str
    opponent: str


class PendingImpactOut(BaseModel):
    kind: Literal["impact"] = "impact"
    stage: str
    opponent: str
    discipline: str = Field(description="'bat' | 'bowl' -- which of YOUR innings this "
                                         "affects (always your SECOND this match)")
    human_bats_first: bool
    first_innings: InningsOut = Field(description="your already-played first innings")
    your_xi: list[CardOut] = Field(
        description="your own drafted eleven, in batting order -- pick a slot (1-11) "
                     "to swap that player out for your Impact Player, or decline")


class SeasonProgressOut(BaseModel):
    state: str
    your_side: str
    table: list[StandingOut] = Field(
        description="empty until the league stage is fully resolved")
    your_results: list[ResultOut] = Field(description="your completed matches so far")
    playoffs: list[ResultOut] = Field(default_factory=list)
    matches_each: int = MATCHES_EACH
    teams: int = TEAMS
    pending: PendingTossOut | PendingImpactOut | None = Field(
        description="the decision awaiting you right now; null once complete")
    complete: bool
    # Populated only once complete -- the journey card's own numbers, folded into the
    # same replay rather than a second simulation. `played`/`won`/`lost`/`tied` are NOT
    # `table`'s row for "you": `journey_stats` adds however far the playoffs took the
    # side, which the fourteen-match league table alone does not count.
    champion: str | None = None
    you_champion: bool | None = None
    runs: int | None = None
    wickets: int | None = None
    played: int | None = None
    won: int | None = None
    lost: int | None = None
    tied: int | None = None
    top_scorer: str | None = None
    top_scorer_runs: int | None = None
    top_wicket_taker: str | None = None
    top_wicket_taker_wickets: int | None = None
    squad: list[JourneySquadEntryOut] | None = Field(
        default=None, description="the final twelve, in batting order then Impact, "
                     "each with this tournament's own simulated figures")


# --- rooms: create/join/lobby/start ------------------------------------------------------

class CreateRoomIn(BaseModel):
    format: str = Field(description="'final' (2 seats), 'cup' (4), or 'league' (10)")
    timer_seconds: int = Field(description="seconds per round: 15, 30, or 45")
    host_name: str = Field(min_length=1, max_length=40)


class JoinRoomIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class HostActionIn(BaseModel):
    player_id: str


class RoomPickIn(BaseModel):
    player_id: str
    index: int = Field(ge=0, description="an index into THIS seat's own current deal")
    slot: int = Field(ge=1, description="1-11 a batting position, or 12 for Impact")


class RoomPlayerOut(BaseModel):
    player_id: str
    name: str
    is_cpu: bool
    picks_made: int
    done: bool
    deal: DealOut | None = Field(
        default=None,
        description="only ever set for the seat whose turn it currently is; "
                     "`options` is populated only for that seat's own caller -- "
                     "everyone else sees franchise/season_year with options=[]")
    order: list[CardOut | None]
    impact: CardOut | None


class RoomStateOut(BaseModel):
    code: str
    format: str
    seats: int
    timer_seconds: int
    host_id: str
    status: str = Field(description="lobby | drafting | complete | failed")
    round: int = Field(description="len(moves) // seats -- which snake wave we're in")
    rounds_total: int = TWELVE_SIZE
    seconds_remaining: int
    active_player_id: str | None = Field(
        description="whose turn it currently is; null outside 'drafting'")
    players: list[RoomPlayerOut]
    failure_reason: str | None = None


class CreatedRoomOut(BaseModel):
    player_id: str
    room: RoomStateOut


class RoomMatchOut(BaseModel):
    format: str
    result: ResultOut | None = None            # "final"
    semis: list[ResultOut] | None = None       # "cup"
    final: ResultOut | None = None             # "cup"
    table: list[StandingOut] | None = None     # "league"
    playoffs: list[ResultOut] | None = None    # "league"
    champion: str | None = None                # "cup" | "league"


# --- mapping ---------------------------------------------------------------------------

def _kind(card: Card) -> str:
    """What the icon shows. `card.role` (A26) already applies a per-match-average balls
    threshold calibrated against players whose role is not in dispute, so it IS the
    "does this man's workload make him an all-rounder" test -- there is no second,
    stricter test to invent here.

    This used to be derived from `has_bat and has_bowl` instead, on the reasoning that a
    man rated in both disciplines is an all-rounder even if A26's averages called him one-
    dimensional. That held only while a discipline was rated at all exclusively when it
    cleared A33's volume floor (100 balls faced / 150 bowled) -- "has a rating" meant
    "substantial workload." A65 removed that floor everywhere, so every discipline with
    so much as one ball faced or bowled now gets a (heavily shrunk) rating, and the old
    test degenerated into "faced or bowled a ball at least once" -- which tagged Bumrah,
    Hazlewood and Cummins all-rounders off single-digit balls faced. Measured against the
    live deck: of 1,816 cards the old test called all-rounder, only 284 actually clear
    A26's thresholds; the other 1,532 are pure bowlers or batters who happened to face or
    bowl a token ball. A genuine dual-threat season (Narine 2024: 488 runs AND a rated
    bowling season) still clears A26's averages on its own, so nothing real is lost."""
    return card.role or "unrated"


def _card(card: Card, blocked: str | None = None) -> CardOut:
    return CardOut(
        person_id=card.person_id, name=card.name, franchise=card.franchise,
        season_year=card.season_year, band=card.band, role=card.role,
        kind=_kind(card), rating=card.display, overseas=card.overseas,
        keeper_eligible=card.keeper_eligible,
        positions=sorted(card.positions), blocked=blocked,
        bat_runs=card.bat_runs, bat_balls=card.bat_balls, bat_strike_rate=card.strike_rate,
        bowl_wickets=card.bowl_wickets, bowl_runs=card.bowl_runs, bowl_balls=card.bowl_balls,
        bowl_economy=card.economy,
    )


def _journey_entry(card: Card, acc: JourneyAccumulator) -> JourneySquadEntryOut:
    """`.get(person_id)` with no default -- None means he never faced/bowled a ball in
    THIS simulated tournament, same "unobserved stays null" convention `_card` already
    uses for the real archive figures (never a manufactured zero, A23/A71's rule)."""
    pid = card.person_id
    return JourneySquadEntryOut(
        person_id=pid, name=card.name, franchise=card.franchise,
        season_year=card.season_year, kind=_kind(card),
        sim_bat_runs=acc.runs.get(pid), sim_bat_balls=acc.balls_faced.get(pid),
        sim_bowl_wickets=acc.wickets.get(pid), sim_bowl_runs=acc.runs_conceded.get(pid),
        sim_bowl_balls=acc.balls_bowled.get(pid),
    )


def _session_out(s: sess.Session) -> SessionOut:
    from etl.feasibility import OVERSEAS_CAP, REROLLS_ALLOWED
    return SessionOut(
        state=s.state,
        picks_made=len(s.picks),
        overseas_taken=sum(1 for c in s.picks if c.overseas is True),
        overseas_cap=OVERSEAS_CAP,
        rerolls_used=s.rerolls_used,
        rerolls_allowed=REROLLS_ALLOWED,
        picks=[_card(c) for c in s.picks],
        order=[None if c is None else _card(c) for c in s.order],
        impact=None if s.impact is None else _card(s.impact),
        errors=list(s.errors),
        deal=None if s.deal is None else DealOut(
            fs_id=s.deal.fs_id, franchise=s.deal.franchise,
            season_year=s.deal.season_year,
            options=[_card(c) for c in s.deal.options],
            blocked=[_card(c, why) for c, why in (s.deal.blocked or [])]
                    + STATE["unrated"].get(s.deal.fs_id, []),
        ),
        squad_complete=s.squad_complete,
        playable=s.playable,
    )


def _load(state: str) -> sess.Session:
    try:
        seed, moves = sess.decode(state)
        return sess.replay(STATE["deck"], seed, moves)
    except sess.InvalidState as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- routes ------------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    deck = STATE.get("deck")
    return {
        "ok": deck is not None,
        "cards": sum(len(v) for v in deck.cards_by_fs.values()) if deck else 0,
        "franchise_seasons": len(deck.fs_ids) if deck else 0,
    }


@app.get("/api/meta")
def meta() -> dict:
    """Everything a front end needs to render the board before a game starts.

    Served rather than hardcoded in the client for the reason A19 gives about columns: a
    shape baked into JavaScript is a second copy of the rules in `etl.feasibility`, and the
    day one of these constants moves the board would show the old shape with nothing to
    catch it. [A72] `template` is gone -- there is no more slot template to serve.
    [A76] `lower_order_default` is gone too -- there is no single default band any more
    (a thin-evidence bowler falls to `tail`, but a thin-evidence batter never does), so a
    single constant could no longer describe the rule.
    """
    from etl.feasibility import BOWLERS_IN_TWELVE, IMPACT_SLOT, OVERSEAS_CAP, TWELVE_SIZE
    deck = STATE["deck"]
    seasons = {c.season_year for cards in deck.cards_by_fs.values() for c in cards}
    return {
        "xi_size": XI_SIZE,
        "twelve_size": TWELVE_SIZE,
        "positions": list(range(1, XI_SIZE + 1)),
        "impact_slot": IMPACT_SLOT,
        "bowlers_needed": BOWLERS_IN_TWELVE,
        "overseas_cap": OVERSEAS_CAP,
        "cards": sum(len(v) for v in deck.cards_by_fs.values()),
        "franchise_seasons": len(deck.fs_ids),
        "seasons": sorted(s for s in seasons if s),
    }


@app.post("/api/draft", response_model=SessionOut)
def new_draft(seed: int | None = None) -> SessionOut:
    """Start a game. Pass a seed to replay somebody else's exactly."""
    chosen = sess.new_seed() if seed is None else seed
    return _session_out(_load(sess.encode(chosen, ())))


@app.get("/api/draft/{state}", response_model=SessionOut)
def get_draft(state: str) -> SessionOut:
    return _session_out(_load(state))


@app.post("/api/draft/{state}/pick", response_model=SessionOut)
def pick(state: str, body: PickIn) -> SessionOut:
    """Take option `index` from the current deal, straight into `slot` (1-11 a batting
    position, or 12 for Impact). [A73] Pick and placement are one move -- there is no
    bench, so a single request settles both. `/reposition` (below) lets an already-placed
    pick trade slots with another one later in the same draft; it does not reopen a bench."""
    current = _load(state)
    if current.squad_complete:
        raise HTTPException(status_code=409, detail="this squad is already full")
    if not 0 <= body.index < len(current.deal.options):
        raise HTTPException(
            status_code=400,
            detail=f"index {body.index} is not among the "
                   f"{len(current.deal.options)} options dealt")
    seed, moves = sess.decode(state)
    new_moves = moves + (sess.Pick(body.index, body.slot),)
    try:
        return _session_out(sess.replay(STATE["deck"], seed, new_moves))
    except sess.InvalidState as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/draft/{state}/reroll", response_model=SessionOut)
def reroll(state: str, body: RerollIn) -> SessionOut:
    """Reject the current deal and see a different one for the same pick, without
    spending it. `kind="team"` deals any other franchise-season; `kind="season"` deals a
    different year of the SAME franchise just shown. Budget is `REROLLS_ALLOWED` for the
    whole draft; refused with a 400 once spent, the same way an out-of-range pick index
    is."""
    if body.kind not in REROLL_KINDS:
        raise HTTPException(
            status_code=400, detail=f"kind must be one of {REROLL_KINDS}, got {body.kind!r}")
    current = _load(state)
    if current.squad_complete:
        raise HTTPException(status_code=409, detail="this squad is already full")
    seed, moves = sess.decode(state)
    new_moves = moves + (sess.Reroll(body.kind),)
    try:
        return _session_out(sess.replay(STATE["deck"], seed, new_moves))
    except sess.InvalidState as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/draft/{state}/reposition", response_model=SessionOut)
def reposition(state: str, body: RepositionIn) -> SessionOut:
    """Move whoever is at `from_slot` to `to_slot`. `from_slot` must already hold a
    player; `to_slot` may be another occupied slot (a swap, both must be eligible for
    the other's position) or an open one (a plain move, freeing `from_slot` for a later
    pick -- e.g. dropping a flexible player out of an opener's slot to make room for one
    who can bat nowhere else). This moves nobody in or out of the twelve, only where
    already-drafted picks bat (or which one holds Impact). Only while the draft is still
    in progress: once the squad is complete an arrangement is final, the same way a pick
    is."""
    current = _load(state)
    if current.squad_complete:
        raise HTTPException(status_code=409, detail="this squad is already full")
    seed, moves = sess.decode(state)
    new_moves = moves + (sess.Reposition(body.from_slot, body.to_slot),)
    try:
        return _session_out(sess.replay(STATE["deck"], seed, new_moves))
    except sess.InvalidState as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _score(runs: int, wickets: int) -> str:
    return f"{runs}/{wickets}"


def _batter_out(b) -> BatterOut:
    return BatterOut(
        name=b.player.name, runs=b.runs, balls=b.balls, out=b.out, faced_any=b.faced_any,
        strike_rate=round(b.runs * 100 / b.balls, 1) if b.faced_any else None,
        is_impact=b.player.is_impact,
    )


def _bowler_out(bo) -> BowlerOut:
    return BowlerOut(
        name=bo.player.name, overs=bo.overs, runs=bo.runs, wickets=bo.wickets,
        economy=round(bo.runs * 6 / bo.balls, 2), is_impact=bo.player.is_impact,
    )


def _over_out(o) -> OverOut:
    return OverOut(over=o.over, bowler=o.bowler, runs=o.runs, wickets=o.wickets,
                   balls=o.balls, over_runs=o.over_runs, over_wickets=o.over_wickets)


def _innings_out(innings) -> InningsOut:
    return InningsOut(
        runs=innings.runs, wickets=innings.wickets, overs=innings.overs,
        extras=innings.extras,
        batting=[_batter_out(b) for b in innings.batting],
        # A bowler in the attack who never got an over (the innings ended first) has
        # nothing to show -- BOWLERS_IN_TWELVE (A50) means the pool is exactly five, not
        # every one of them is guaranteed a turn.
        bowling=[_bowler_out(bo) for bo in innings.bowling if bo.balls > 0],
        commentary=list(innings.commentary),
        over_log=[_over_out(o) for o in innings.over_log],
    )


def _result_out(r, you: Side) -> ResultOut:
    return ResultOut(
        stage=r.stage, home=r.home.short, away=r.away.short,
        home_score=_score(r.home_runs, r.home_wickets),
        away_score=_score(r.away_runs, r.away_wickets),
        winner=None if r.winner is None else r.winner.short,
        margin=r.margin, yours=(r.home is you or r.away is you),
        home_innings=_innings_out(r.home_innings) if r.home_innings else None,
        away_innings=_innings_out(r.away_innings) if r.away_innings else None,
        toss_won_by_you=r.toss_won_by_you, toss_elected=r.toss_elected,
    )


def _season_progress_out(state: str, replay: season_session.SeasonReplay
                          ) -> SeasonProgressOut:
    """Shared by all four season routes -- a `SeasonReplay` either carries a pending
    decision or is complete; this shapes whichever one into the wire format."""
    yours, season = replay.yours, replay.season
    your_results = [_result_out(r, yours) for r in season.results
                    if r.home is yours or r.away is yours]
    playoffs = [_result_out(r, yours) for r in season.playoffs
                if r.home is yours or r.away is yours]
    table = [StandingOut(pos=i, name=s.side.name, short=s.side.short, you=s.side.you,
                         played=s.played, won=s.won, lost=s.lost, tied=s.tied,
                         points=s.points, nrr=round(s.nrr, 3))
             for i, s in enumerate(season.table, 1)]

    pending: PendingTossOut | PendingImpactOut | None = None
    if replay.pending_kind == "toss":
        pending = PendingTossOut(stage=replay.pending_stage,
                                  opponent=replay.pending_opponent.name)
    elif replay.pending_kind == "impact":
        pending = PendingImpactOut(
            stage=replay.pending_stage, opponent=replay.pending_opponent.name,
            discipline=replay.pending_discipline,
            human_bats_first=replay.pending_human_bats_first,
            first_innings=_innings_out(replay.pending_first_innings),
            your_xi=[_card(c) for c in yours.xi],
        )

    if not replay.complete:
        return SeasonProgressOut(state=state, your_side=yours.name, table=table,
                                  your_results=your_results, playoffs=playoffs,
                                  pending=pending, complete=False)

    all_twelve = list(yours.xi) + ([yours.impact] if yours.impact is not None else [])
    stats = replay.journey
    return SeasonProgressOut(
        state=state, your_side=yours.name, table=table,
        your_results=your_results, playoffs=playoffs, pending=None, complete=True,
        champion=season.champion.name, you_champion=season.champion is yours,
        runs=stats.runs, wickets=stats.wickets,
        played=stats.played, won=stats.won, lost=stats.lost, tied=stats.tied,
        top_scorer=stats.top_scorer[0], top_scorer_runs=stats.top_scorer[1],
        top_wicket_taker=stats.top_wicket_taker[0],
        top_wicket_taker_wickets=stats.top_wicket_taker[1],
        squad=[_journey_entry(c, replay.stats) for c in all_twelve],
    )


def _replay_season_or_400(state: str, cursor: season_session.MoveCursor
                           ) -> season_session.SeasonReplay:
    """Gated on `playable`, not just `squad_complete` -- a completed draft whose own
    arrangement still fails a rule (should not happen once every pick is forward-checked,
    but is checked rather than assumed) is not a squad that can take the field. A bad
    move recorded against `cursor` (wrong type, out of range) surfaces as `sess.
    InvalidState` from deep inside the replay -- caught here and reported as a 400, the
    same convention every other `InvalidState` in this file already gets (the draft's
    own `pick()` route, for one), rather than a new one invented just for this route."""
    draft_state, _ = season_session.decode_full(state)
    player = _load(draft_state)
    if not player.squad_complete:
        raise HTTPException(status_code=409,
                            detail=f"{len(player.picks)} of {TWELVE_SIZE} picks made")
    if not player.playable:
        raise HTTPException(status_code=409, detail="; ".join(player.errors))
    deck, model = STATE["deck"], STATE["model"]
    try:
        return season_session.replay_season(deck, model, draft_state, cursor)
    except sess.InvalidState as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/season/{state}", response_model=SeasonProgressOut)
def season(state: str) -> SeasonProgressOut:
    """A full campaign: fourteen league matches, a table, then the playoffs -- for
    every match that is not the tracked human's own, pre-computed and instant, exactly
    as always. For the human's own matches, a real toss and a break-time Impact choice
    now pause the replay (`game.season.NeedToss`/`NeedImpact`) until `state` carries an
    answer -- read-only here, so a slow-polling or reloading client always sees
    wherever the season has actually gotten to, never a stale snapshot.

    `state` is `"{draft_state}~{season_moves}"` (`web/season_session.py`); a bare draft
    state (no `~`) is read as zero season moves, exactly the string the draft flow
    already hands back the moment a squad completes, so this route needs no separate
    "start the season" step.
    """
    draft_state, season_moves = season_session.decode_full(state)
    replay = _replay_season_or_400(state, season_session.recorded_moves(season_moves))
    return _season_progress_out(state, replay)


class TossIn(BaseModel):
    elects: Literal["bat", "bowl"]


class ImpactIn(BaseModel):
    slot: int | None = Field(
        default=None, ge=1, le=XI_SIZE,
        description="1-11: swap this member of your drafted XI out for the Impact "
                    "Player. null = decline")


class SkipIn(BaseModel):
    scope: Literal["this_match", "group_stage", "tournament"]


@app.post("/api/season/{state}/toss", response_model=SeasonProgressOut)
def season_toss(state: str, body: TossIn) -> SeasonProgressOut:
    """Only valid while a toss is actually pending -- `_replay_season_or_400` turns a
    submission at the wrong point (already answered, or not your side's toss to call)
    into a 400 via the cursor's own type-check, never a 500."""
    draft_state, season_moves = season_session.decode_full(state)
    cursor = season_session.recorded_moves(season_moves + (TossElect(body.elects),))
    replay = _replay_season_or_400(state, cursor)
    new_state = season_session.encode_full(draft_state, tuple(cursor.emitted))
    return _season_progress_out(new_state, replay)


@app.post("/api/season/{state}/impact", response_model=SeasonProgressOut)
def season_impact(state: str, body: ImpactIn) -> SeasonProgressOut:
    draft_state, season_moves = season_session.decode_full(state)
    cursor = season_session.recorded_moves(season_moves + (ImpactPick(body.slot),))
    replay = _replay_season_or_400(state, cursor)
    new_state = season_session.encode_full(draft_state, tuple(cursor.emitted))
    return _season_progress_out(new_state, replay)


@app.post("/api/season/{state}/skip", response_model=SeasonProgressOut)
def season_skip(state: str, body: SkipIn) -> SeasonProgressOut:
    """One mechanism behind all three bypasses (and `SIM_MODE='whole'`, which just
    calls `scope="tournament"` immediately instead of polling first): auto-resolve
    the declared default toss/Impact answer for whichever fixtures `scope` covers,
    recording a real move for each one rather than an ephemeral request flag -- so
    the resulting `state` alone replays the exact same completed matches again."""
    draft_state, season_moves = season_session.decode_full(state)
    if body.scope == "this_match":
        probe = _replay_season_or_400(state, season_session.recorded_moves(season_moves))
        if probe.complete:
            raise HTTPException(status_code=409, detail="the season is already complete")
        cursor = season_session.skip_this_match(season_moves, probe.pending_human_match_no)
    elif body.scope == "group_stage":
        cursor = season_session.skip_group_stage(season_moves)
    else:
        cursor = season_session.skip_tournament(season_moves)
    replay = _replay_season_or_400(state, cursor)
    new_state = season_session.encode_full(draft_state, tuple(cursor.emitted))
    return _season_progress_out(new_state, replay)


@app.get("/api/twelve/{state}")
def twelve(state: str) -> dict:
    """The final twelve as the DRAFTER arranged it -- not an algorithmic pick. [A72]
    Replaces `/api/xi`; no alias is kept, since the shape changed too much for a silent
    redirect to be safe. [A73] No bench any more -- every drafted player already occupies
    a slot, by construction."""
    s = _load(state)
    if not s.squad_complete:
        raise HTTPException(status_code=409, detail="squad is not full")
    all_twelve = [c for c in s.order if c is not None]
    if s.impact is not None:
        all_twelve.append(s.impact)
    return {
        "order": [None if c is None else _card(c).model_dump() for c in s.order],
        "impact": None if s.impact is None else _card(s.impact).model_dump(),
        "errors": list(s.errors),
        "overseas": overseas_status(all_twelve),
    }


# --- rooms ---------------------------------------------------------------------------
#
# Friends draft together, live, turn by turn, from one shared competitive pool -- Neon-
# backed (migrations 019/020), see `web.rooms`'s own docstring for why and for the
# shared-pool/snake-turn mechanic itself. Nothing here decides anything about cricket
# either: `web.rooms.replay_room` calls `etl.feasibility.eligible`/`could_still_complete`/
# `choose_slot` directly, so a room's picks are validated exactly the way a solo draft's
# are -- this module only shapes the response and redacts what a given caller may see.

@contextmanager
def _room_db():
    """A short-lived connection per room request, against the POOLED endpoint. Unlike the
    boot-time DIRECT_URL load (one connection, once, for the process's whole life), a room
    request is exactly the many-short-transactions pattern PgBouncer transaction-mode
    pooling exists for -- this is the first thing in the app that actually uses
    DATABASE_URL rather than DIRECT_URL."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        yield conn


def _room_player_out(player: rooms.RoomPlayer, seat: rooms.SeatProgress, *,
                      is_active: bool, caller_id: str | None,
                      pending_deal) -> RoomPlayerOut:
    """`deal` is non-null only for the currently active seat, and even then carries
    `options` only for the caller whose own seat this is -- see `RoomPlayerOut.deal`'s
    own description. Everyone else sees just franchise/season_year, never the options
    (this used to be sent to every caller for every seat; `GET /api/rooms/{code}` had
    no caller-identity parameter at all to redact by)."""
    deal = None
    if is_active and pending_deal is not None:
        fs_id, candidates = pending_deal
        franchise = candidates[0].franchise if candidates else None
        season_year = candidates[0].season_year if candidates else None
        show_options = player.player_id == caller_id
        deal = DealOut(
            fs_id=fs_id, franchise=franchise, season_year=season_year,
            options=[_card(c) for c in candidates] if show_options else [],
        )
    return RoomPlayerOut(
        player_id=player.player_id, name=player.name, is_cpu=player.is_cpu,
        picks_made=len(seat.picks), done=seat.done, deal=deal,
        order=[None if c is None else _card(c) for c in seat.order],
        impact=None if seat.impact is None else _card(seat.impact),
    )


def _room_state_out(room: rooms.Room, deck, caller_id: str | None = None) -> RoomStateOut:
    replay = rooms.replay_room(room, deck)
    remaining = 0
    if room.status == "drafting":
        remaining = max(0, round(room.timer_seconds - (time.time() - room.turn_started_at)))
    return RoomStateOut(
        code=room.code, format=room.format, seats=room.seats,
        timer_seconds=room.timer_seconds, host_id=room.host_id,
        status=room.status, round=room.round,
        seconds_remaining=remaining, active_player_id=replay.pending_seat_id,
        players=[
            _room_player_out(
                p, replay.seats[pid], is_active=(pid == replay.pending_seat_id),
                caller_id=caller_id, pending_deal=replay.pending_deal,
            )
            for pid, p in room.players.items()
        ],
        failure_reason=room.failure_reason,
    )


@app.post("/api/rooms", response_model=CreatedRoomOut)
def create_room(body: CreateRoomIn) -> CreatedRoomOut:
    with _room_db() as conn:
        try:
            room, player_id = rooms.create_room(
                conn, body.format, body.timer_seconds, body.host_name)
        except rooms.RoomError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return CreatedRoomOut(
            player_id=player_id,
            room=_room_state_out(room, STATE["deck"], caller_id=player_id))


@app.post("/api/rooms/{code}/join", response_model=CreatedRoomOut)
def join_room(code: str, body: JoinRoomIn) -> CreatedRoomOut:
    with _room_db() as conn:
        try:
            room, player_id = rooms.join_room(conn, code, body.name, STATE["deck"])
        except rooms.RoomError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return CreatedRoomOut(
            player_id=player_id,
            room=_room_state_out(room, STATE["deck"], caller_id=player_id))


@app.post("/api/rooms/{code}/start", response_model=RoomStateOut)
def start_room(code: str, body: HostActionIn) -> RoomStateOut:
    """Host-only. Fills every seat still empty with a CPU seat and starts the first
    turn's clock -- a CPU's own twelve is no longer drafted up front (see web.rooms'
    own docstring): it depends on what humans take at their own turns, so it resolves
    turn by turn like everyone else, instantly whenever its turn comes up."""
    with _room_db() as conn:
        try:
            room = rooms.start_room(conn, code, body.player_id, STATE["deck"])
        except rooms.RoomError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _room_state_out(room, STATE["deck"], caller_id=body.player_id)


@app.get("/api/rooms/{code}", response_model=RoomStateOut)
def get_room(code: str, player_id: str | None = None) -> RoomStateOut:
    """Poll target. Resolves any expired turn (auto-picking whoever timed out, or
    instantly resolving a CPU's turn) before returning, so a slow-polling client still
    sees an up-to-date room. `player_id` identifies the caller so only the currently
    active seat's own options are ever sent back to them -- everyone else sees that
    seat's franchise/season and every seat's team-so-far, never anyone's options."""
    with _room_db() as conn:
        try:
            room = rooms.room_state(conn, code, STATE["deck"])
        except rooms.RoomError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _room_state_out(room, STATE["deck"], caller_id=player_id)


@app.post("/api/rooms/{code}/pick", response_model=RoomStateOut)
def room_pick(code: str, body: RoomPickIn) -> RoomStateOut:
    with _room_db() as conn:
        try:
            room = rooms.submit_pick(
                conn, code, body.player_id, body.index, body.slot, STATE["deck"])
        except rooms.RoomError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _room_state_out(room, STATE["deck"], caller_id=body.player_id)


@app.get("/api/rooms/{code}/match", response_model=RoomMatchOut)
def room_match(code: str, player_id: str | None = None) -> RoomMatchOut:
    """Once every seat's twelve is drafted: a single match ('final'), a three-match
    knockout ('cup'), or a full league-plus-playoffs ('league') -- the same `play`/
    `run_cup`/`run_league`+`run_playoffs` the solo season already uses, just fed the
    room's own drafted sides instead of nine historical ones."""
    deck, model = STATE["deck"], STATE["model"]
    # Only the room lookup needs the connection -- released before the (up to ~3s)
    # match simulation below, which touches no database.
    with _room_db() as conn:
        try:
            room = rooms.room_state(conn, code, deck)
        except rooms.RoomError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    if room.status != "complete":
        raise HTTPException(status_code=409,
                            detail=f"room is not complete yet (status: {room.status})")

    sides = []
    you_side = None
    for pid, p, order, impact in rooms.room_sides(room, deck):
        short = "".join(w[0] for w in p.name.split())[:4].upper() or pid[:4].upper()
        side = Side(name=p.name, short=short, xi=order, impact=impact)
        if pid == player_id:
            you_side = side
        sides.append(side)
    you_side = you_side or sides[0]

    rng = random.Random(room.seed)

    if room.format == "final":
        r = play(model, sides[0], sides[1], rng)
        return RoomMatchOut(format="final", result=_result_out(r, you_side))

    if room.format == "cup":
        semi1, semi2, final = run_cup(model, sides, rng)
        return RoomMatchOut(
            format="cup",
            semis=[_result_out(semi1, you_side), _result_out(semi2, you_side)],
            final=_result_out(final, you_side),
            champion=final.winner.name if final.winner else None,
        )

    season = run_playoffs(model, run_league(model, sides, rng), rng)
    return RoomMatchOut(
        format="league",
        table=[StandingOut(pos=i, name=s.side.name, short=s.side.short,
                           you=s.side is you_side, played=s.played, won=s.won,
                           lost=s.lost, tied=s.tied, points=s.points, nrr=round(s.nrr, 3))
               for i, s in enumerate(season.table, 1)],
        playoffs=[_result_out(r, you_side) for r in season.playoffs],
        champion=season.champion.name,
    )
