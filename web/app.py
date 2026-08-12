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
import time
from contextlib import asynccontextmanager, contextmanager
from typing import Literal

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from etl.feasibility import REROLL_KINDS, TWELVE_SIZE, XI_SIZE, Card, team_rating
from game.__main__ import overseas_status
from game.season import (
    MATCHES_EACH, TEAMS, ImpactPick, JourneyAccumulator, Side, TossElect, tournament_leaders,
)
from game.simulator import load_model
from web import accounts
from web import auth
from web import room_match as room_match_lib
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


# A request that can't get the database (a dead/very slow connect) or can't get a row
# lock it needs (genuinely stuck behind another request against the same room, past
# `_db`'s own `lock_timeout`) used to just hang until whatever the hosting platform's
# own request timeout eventually killed it -- diagnosed as the cause of drafts
# intermittently freezing on a pick, with no error shown and the button stuck. Both
# bounds are real now (`_db`, originally room-only, now also used by the accounts/auth
# routes); these two handlers are what turns hitting one into a fast, clean 503 the
# client can show and retry from, instead of a raw 500 or a silent hang. `retry_after`
# is a plain hint, not enforced -- the client-side timeout and retry are what actually
# act on it (web/static/common.js's `api()`).
@app.exception_handler(psycopg.errors.LockNotAvailable)
def _lock_not_available(_request, _exc) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": "This room is busy right now -- try again in a moment."},
        headers={"Retry-After": "2"},
    )


@app.exception_handler(psycopg.OperationalError)
def _db_unavailable(_request, _exc) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": "Couldn't reach the database right now -- try again in a moment."},
        headers={"Retry-After": "3"},
    )


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Home only -- the draft, season and room screens are their own pages/routes
    below, each a static file with no build step, reading `/api/meta` for the shape
    rather than embedding a copy of it (A19)."""
    return FileResponse(STATIC / "index.html")


@app.get("/draft", include_in_schema=False)
def draft_page() -> FileResponse:
    """State lives in the URL hash alone (a draft state, optionally `?mode=memory`
    folded into the hash) -- this route always serves the same file; the client reads
    the hash itself to resume or bounce back home if there's nothing to resume."""
    return FileResponse(STATIC / "draft.html")


@app.get("/season", include_in_schema=False)
def season_page() -> FileResponse:
    """`?enter=` (whole/groupstage/reveal) is a one-time navigation instruction read
    once at boot and then stripped from the URL; the season's own state lives in the
    hash alone from then on, exactly like /draft."""
    return FileResponse(STATIC / "season.html")


@app.get("/rooms", include_in_schema=False)
def rooms_page() -> FileResponse:
    """Create/join only -- a successful create or join saves {code, player_id} to
    localStorage and navigates to /rooms/{code}, which is the live room itself. An
    optional `?code=` (room.html's own redirect target when a session can't be
    resumed) pre-fills the join tab client-side."""
    return FileResponse(STATIC / "rooms.html")


@app.get("/rooms/{code}", include_in_schema=False)
def room_page(code: str) -> FileResponse:
    """`code` is accepted only so the route matches at all -- exactly the /ads.txt
    pattern's own bare-FileResponse convention. All real state is read client-side:
    the URL's own code, a localStorage session, and the room's live poll. No overlap
    with /rooms above -- the two never share a segment count, unlike the existing
    /api/rooms/open vs /api/rooms/{code} pair, which needs declaration order for
    exactly that reason."""
    return FileResponse(STATIC / "room.html")


@app.get("/ads.txt", include_in_schema=False)
def ads_txt() -> FileResponse:
    """AdSense (and the IAB spec it follows) requires this at the site ROOT, never
    under `/static` -- `/static/ads.txt` does not satisfy the crawler, hence a
    dedicated route rather than relying on the StaticFiles mount below."""
    return FileResponse(STATIC / "ads.txt", media_type="text/plain")


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
    # Populated only once squad_complete -- the squad-review screen's own three
    # numbers (etl.feasibility.team_rating), null beforehand rather than a number
    # computed over a still-partial twelve.
    overall_rating: int | None = None
    batting_rating: int | None = None
    bowling_rating: int | None = None


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
    you_home: bool | None = Field(
        default=None, description="null unless `yours` -- true if the CALLER's own side "
                    "batted/bowled as the home side, so a per-viewer win/loss badge can "
                    "be computed without hardcoding which of home/away is 'you'")
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
    # Orange/Purple Cap -- the WHOLE field's own leaders, every side that played a
    # ball, not just `top_scorer`/`top_wicket_taker` above which are your own twelve's.
    orange_cap: str | None = None
    orange_cap_runs: int | None = None
    purple_cap: str | None = None
    purple_cap_wickets: int | None = None
    # The squad-review screen's own overall number, carried through to the journey
    # card (etl.feasibility.team_rating) -- batting/bowling aren't needed again here,
    # only what the user asked the journey card to show.
    overall_rating: int | None = None
    squad: list[JourneySquadEntryOut] | None = Field(
        default=None, description="the final twelve, in batting order then Impact, "
                     "each with this tournament's own simulated figures")


# --- rooms: create/join/lobby/start ------------------------------------------------------

class CreateRoomIn(BaseModel):
    format: str = Field(description="'final' (2 seats), 'cup' (4), or 'league' (10)")
    timer_seconds: int = Field(description="seconds per round: 15, 30, or 45")
    host_name: str = Field(min_length=1, max_length=40)
    draft_mode: str = Field(
        default="stat",
        description="'stat' (rating badges, clickable stats) or 'memory' (neither) -- "
                    "chosen once here by the host, binding on every seat in the room; "
                    "the join flow has no draft_mode of its own")
    is_open: bool = Field(
        default=False,
        description="true lists this room publicly (GET /api/rooms/open) so anyone can "
                    "join without the code; false (default) is code-only, same as every "
                    "room before this field existed")


class JoinRoomIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class HostActionIn(BaseModel):
    player_id: str


class KickPlayerIn(BaseModel):
    player_id: str = Field(description="the HOST's own id, for authorisation")
    target_id: str = Field(description="the seat to remove")


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
    # Populated only once `done` -- the squad-review screen's own three numbers
    # (etl.feasibility.team_rating), same as solo's SessionOut fields of the same name.
    overall_rating: int | None = None
    batting_rating: int | None = None
    bowling_rating: int | None = None


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
    draft_mode: str = Field(
        description="'stat' | 'memory' -- the host's own choice at creation, binding "
                    "on every seat; every client renders this room under it, not its "
                    "own local preference")
    is_open: bool = Field(
        description="the host's own choice at creation -- true if this room is listed "
                    "publicly for anyone to join without the code")


class OpenRoomOut(BaseModel):
    """One row of the public browse list (GET /api/rooms/open) -- just enough to show
    and join it, never a seated player's own progress."""

    code: str
    format: str
    seats_filled: int
    seats_total: int
    timer_seconds: int
    draft_mode: str
    host_name: str


class CreatedRoomOut(BaseModel):
    player_id: str
    room: RoomStateOut


class RoomMatchResultOut(BaseModel):
    stage: str
    result: ResultOut


class RoomCurrentMatchOut(BaseModel):
    """One fixture in the room's CURRENTLY OPEN round. Several of these can appear at
    once -- a cup's two semis, a league's Qualifier 1 + Eliminator -- since independent
    fixtures now resolve in parallel rather than one at a time. `result` is null until
    this fixture's own toss is answered; everything else about it (a name, b name) is
    known from the moment the round opens."""
    stage: str
    a_name: str
    b_name: str
    a_pid: str
    b_pid: str
    pending_toss_winner_pid: str | None = Field(
        default=None, description="null once this fixture's toss is answered")
    you_decide_toss: bool = Field(
        description="true only for the seat that actually won THIS fixture's toss, "
                    "while it's still pending")
    result: ResultOut | None = None


class RoomMatchOut(BaseModel):
    format: str
    results: list[RoomMatchResultOut]
    table: list[StandingOut] | None = None      # "league" only, progressive
    complete: bool
    champion: str | None = None                 # the room's own overall winner, any format
    you_champion: bool | None = None
    round_label: str | None = Field(
        default=None, description="e.g. 'Semi-finals', 'Qualifiers' -- null once complete")
    current_matches: list[RoomCurrentMatchOut] = Field(
        default_factory=list,
        description="every fixture open in the current round, pending and resolved "
                    "alike -- empty only for the league's own round-robin, which has "
                    "nothing interactive in it, or once the room is complete")
    advance_ready: bool = Field(
        description="true once every fixture in current_matches has resolved and the "
                    "round is not the terminal one")
    you_decide_advance: bool = Field(
        default=False, description="true only for the room's host, while advance_ready")
    league_revealed: int | None = Field(
        default=None, description="how many of the league's own round-robin fixtures "
                    "have been revealed so far -- null unless this room is a 'league' "
                    "whose group stage is still being paced through")
    league_total: int | None = Field(
        default=None, description="the group stage's own fixture count, alongside "
                    "league_revealed -- null under the same conditions")
    you_decide_league_reveal: bool = Field(
        default=False, description="true only for the room's host, while league_revealed "
                    "< league_total")
    league_next_result: RoomMatchResultOut | None = Field(
        default=None, description="the next group-stage fixture waiting to be revealed, "
                    "shaped exactly like an entry in `results` so the client's existing "
                    "reveal engine needs no changes to play it")
    you_are_out: bool = Field(
        description="true once the CALLING player's own tournament has ended -- win, "
                    "lose, or eliminated earlier -- even if the room as a whole is not "
                    "complete yet. This is what gates the journey fields below, not "
                    "`complete`: an eliminated player's own stats are already final.")
    # The journey card's own numbers -- null until you_are_out (or you_champion), not
    # until the whole room finishes. Yours alone: a room has no single "the human", so
    # this is always the CALLING player_id's own tournament, never a second seat's.
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
    # Orange/Purple Cap -- the WHOLE room's own leaders across every seat and every
    # filler, not the calling player's own twelve above. Progressive like `table`: any
    # match already in `results` counts, whether the room is complete or still mid-reveal.
    orange_cap: str | None = None
    orange_cap_runs: int | None = None
    purple_cap: str | None = None
    purple_cap_wickets: int | None = None
    # The squad-review screen's own overall number, carried through to the journey
    # card -- same field name and same meaning as solo's SeasonProgressOut.
    overall_rating: int | None = None
    squad: list[JourneySquadEntryOut] | None = None
    # Set only in the window between the draft finishing and the host starting the
    # actual matches -- every seat reviews their own squad+ratings here first, and
    # nothing in `replay_room_matches` resolves so much as a toss until the host does.
    awaiting_start: bool = False
    you_decide_start: bool = Field(
        default=False, description="true only for the room's host, while awaiting_start")


class RoomTossIn(BaseModel):
    player_id: str
    stage: str = Field(description="which currently-open fixture this toss call is for "
                                   "-- more than one can be open at once")
    elects: str = Field(description="'bat' | 'bowl' -- only honoured from the seat "
                                    "that actually won the toss")


# --- accounts / auth ---------------------------------------------------------------------
#
# Email+password only (confirmed with the user directly -- no OAuth, no magic link).
# Never a login wall: every route below is reached only when a player actively chose to
# sign in or register. Being logged in is purely additive elsewhere in this file -- a
# room seat's name pre-fills with the account's own username, and a completed game is
# saved to the account's history, both only if the player was signed in.

class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=24)
    email: str
    password: str = Field(min_length=8)


class LoginIn(BaseModel):
    identifier: str = Field(description="a username or an email -- the form doesn't ask which")
    password: str


class AccountOut(BaseModel):
    account_id: int
    username: str


class MeOut(BaseModel):
    account_id: int | None = Field(
        description="null for a missing/invalid/expired cookie -- this route never "
                    "401s, since every page calls it unconditionally on boot and a "
                    "no-login-wall app can't need special-case handling for that")
    username: str | None = None


class LeaderOut(BaseModel):
    person_id: str
    name: str
    total: int = Field(description="total runs (batters) or wickets (bowlers), summed "
                                   "across every game this account has saved")


class ProfileOut(BaseModel):
    username: str
    games_played: int
    titles_won: int
    top_batters: list[LeaderOut] = Field(description="up to 5, by total runs desc")
    top_bowlers: list[LeaderOut] = Field(description="up to 4, by total wickets desc")


class SaveResultIn(BaseModel):
    player_id: str = Field(description="which seat's own result to save -- room saves only")


class SaveResultOut(BaseModel):
    saved: bool = Field(description="true if this call newly saved the game")
    already_saved: bool = Field(description="true if this exact game was already saved "
                                            "(idempotent no-op, not an error)")


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
    rating = None
    if s.squad_complete:
        all_twelve = [c for c in s.order if c is not None] + (
            [s.impact] if s.impact is not None else [])
        rating = team_rating(all_twelve)
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
        overall_rating=rating.overall if rating else None,
        batting_rating=rating.batting if rating else None,
        bowling_rating=rating.bowling if rating else None,
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
    leaders = tournament_leaders(season.results + season.playoffs)
    rating = team_rating(all_twelve)
    return SeasonProgressOut(
        state=state, your_side=yours.name, table=table,
        your_results=your_results, playoffs=playoffs, pending=None, complete=True,
        champion=season.champion.name, you_champion=season.champion is yours,
        runs=stats.runs, wickets=stats.wickets,
        played=stats.played, won=stats.won, lost=stats.lost, tied=stats.tied,
        top_scorer=stats.top_scorer[0], top_scorer_runs=stats.top_scorer[1],
        top_wicket_taker=stats.top_wicket_taker[0],
        top_wicket_taker_wickets=stats.top_wicket_taker[1],
        orange_cap=leaders.top_scorer[0], orange_cap_runs=leaders.top_scorer[1],
        purple_cap=leaders.top_wicket_taker[0],
        purple_cap_wickets=leaders.top_wicket_taker[1],
        overall_rating=rating.overall,
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


# --- short-lived, per-request database access ----------------------------------------
#
# Everything below (rooms, and now accounts/auth) shares one connection helper. The
# solo draft/season routes never touch this at all -- their whole deck/model is loaded
# once at boot (`lifespan`, DIRECT_URL) and held in `STATE` for the process's life.

@contextmanager
def _db():
    """A short-lived connection per request, against the POOLED endpoint. Unlike the
    boot-time DIRECT_URL load (one connection, once, for the process's whole life), this
    is exactly the many-short-transactions pattern PgBouncer transaction-mode pooling
    exists for. Originally room-only (hence every caller's own variable name, `conn`,
    rather than anything room-specific) -- the accounts/auth routes reuse it unchanged
    now that they're the second thing in the app to need a per-request connection.

    Two bounds that didn't used to exist, both diagnosed from drafts freezing on a pick
    with no error and no way to tell why: `connect_timeout` bounds how long a request
    waits for Neon itself (TCP/TLS plus, on the free tier, waking a suspended compute)
    rather than hanging on a dead or very slow endpoint indefinitely; `lock_timeout`
    bounds how long a request waits for a row lock (`_load_room`'s, today) once
    connected, so a request that's genuinely stuck behind another (rather than just slow
    to reach the database) fails fast with a clean, retryable error instead of sitting
    until whatever the hosting platform's own request timeout eventually kills it. Both
    are caught by `_lock_not_available`/`_db_unavailable` below and turned into a 503
    rather than a raw 500."""
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10) as conn:
        conn.execute("set lock_timeout = '5s'")
        yield conn


def _current_account_id(request: Request) -> int | None:
    token = request.cookies.get(auth.COOKIE_NAME)
    if token is None:
        return None
    return auth.verify_session_cookie(token)


def _set_session_cookie(request: Request, response: Response, account_id: int) -> None:
    # `secure` from the REQUEST's own scheme, not hardcoded -- local `uvicorn` over
    # plain HTTP still gets a cookie the browser will send back; Vercel (always HTTPS)
    # gets a secure one automatically, no separate env flag needed.
    response.set_cookie(
        auth.COOKIE_NAME, auth.make_session_cookie(account_id),
        max_age=auth.SESSION_MAX_AGE_S, httponly=True, samesite="lax",
        secure=(request.url.scheme == "https"), path="/",
    )


@app.post("/api/auth/register", response_model=AccountOut)
def register(body: RegisterIn, request: Request, response: Response) -> AccountOut:
    with _db() as conn:
        try:
            account = accounts.create_account(conn, body.username, body.email, body.password)
        except accounts.AccountError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _set_session_cookie(request, response, account.account_id)
        return AccountOut(account_id=account.account_id, username=account.username)


@app.post("/api/auth/login", response_model=AccountOut)
def login(body: LoginIn, request: Request, response: Response) -> AccountOut:
    with _db() as conn:
        account = accounts.authenticate(conn, body.identifier, body.password)
        if account is None:
            raise HTTPException(status_code=401, detail="wrong username/email or password")
        _set_session_cookie(request, response, account.account_id)
        return AccountOut(account_id=account.account_id, username=account.username)


@app.post("/api/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/auth/me", response_model=MeOut)
def me(request: Request) -> MeOut:
    account_id = _current_account_id(request)
    if account_id is None:
        return MeOut(account_id=None, username=None)
    with _db() as conn:
        account = accounts.get_account(conn, account_id)
    if account is None:
        # A cookie that verifies (signature/expiry both fine) for an account that no
        # longer exists -- not possible today (nothing ever deletes an account), but
        # treated the same as no cookie at all rather than assumed unreachable.
        return MeOut(account_id=None, username=None)
    return MeOut(account_id=account.account_id, username=account.username)


def _room_player_out(player: rooms.RoomPlayer, seat: rooms.SeatProgress, *,
                      is_active: bool, caller_id: str | None,
                      pending_deal, pending_blocked) -> RoomPlayerOut:
    """`deal` is non-null only for the currently active seat, and even then carries
    `options`/`blocked` only for the caller whose own seat this is -- see
    `RoomPlayerOut.deal`'s own description. Everyone else sees just franchise/
    season_year, never the squad (this used to be sent to every caller for every seat;
    `GET /api/rooms/{code}` had no caller-identity parameter at all to redact by).

    `blocked` mirrors solo's own deal exactly (A64): the WHOLE squad, not just the
    part that can be taken, so a keeper already spoken for still reads as a keeper on
    the roster rather than the deal looking thin. Same `STATE["unrated"]` defensive
    net appended too, for parity with solo's own (currently empty, post-A65/A71) list.
    """
    deal = None
    if is_active and pending_deal is not None:
        fs_id, candidates = pending_deal
        franchise = candidates[0].franchise if candidates else None
        season_year = candidates[0].season_year if candidates else None
        show_options = player.player_id == caller_id
        deal = DealOut(
            fs_id=fs_id, franchise=franchise, season_year=season_year,
            options=[_card(c) for c in candidates] if show_options else [],
            blocked=([_card(c, why) for c, why in (pending_blocked or [])]
                     + STATE["unrated"].get(fs_id, [])) if show_options else [],
        )
    # A filler (`is_cpu`) seat's roster is complete the instant the room starts drafting
    # -- a real historical franchise-season's own eleven, never built turn by turn -- so
    # `picks_made`/`done` are reported as fully complete unconditionally rather than via
    # `seat.done`/`len(seat.picks)`, which would read "11/12, not done" for a filler
    # whose historical squad happens to have no Impact-eligible bench player at all
    # (`impact` can legitimately be `None`) even though nothing further will ever happen
    # for that seat. `name` prefers `seat.historical_name` for the same reason
    # `rooms.room_sides` does -- the stored `RoomPlayer.name` for a filler is just an
    # internal placeholder ("CPU 1"), never meant to be shown once a real squad exists.
    done = True if player.is_cpu else seat.done
    rating = None
    if done:
        all_twelve = [c for c in seat.order if c is not None] + (
            [seat.impact] if seat.impact is not None else [])
        rating = team_rating(all_twelve)
    return RoomPlayerOut(
        player_id=player.player_id, name=seat.historical_name or player.name,
        is_cpu=player.is_cpu,
        picks_made=TWELVE_SIZE if player.is_cpu else len(seat.picks),
        done=done,
        deal=deal,
        order=[None if c is None else _card(c) for c in seat.order],
        impact=None if seat.impact is None else _card(seat.impact),
        overall_rating=rating.overall if rating else None,
        batting_rating=rating.batting if rating else None,
        bowling_rating=rating.bowling if rating else None,
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
                pending_blocked=replay.pending_blocked,
            )
            for pid, p in room.players.items()
        ],
        failure_reason=room.failure_reason,
        draft_mode=room.draft_mode,
        is_open=room.is_open,
    )


@app.post("/api/rooms", response_model=CreatedRoomOut)
def create_room(body: CreateRoomIn) -> CreatedRoomOut:
    with _db() as conn:
        try:
            room, player_id = rooms.create_room(
                conn, body.format, body.timer_seconds, body.host_name, body.draft_mode,
                body.is_open)
        except rooms.RoomError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return CreatedRoomOut(
            player_id=player_id,
            room=_room_state_out(room, STATE["deck"], caller_id=player_id))


@app.post("/api/rooms/{code}/join", response_model=CreatedRoomOut)
def join_room(code: str, body: JoinRoomIn) -> CreatedRoomOut:
    with _db() as conn:
        try:
            room, player_id = rooms.join_room(conn, code, body.name, STATE["deck"])
        except rooms.RoomError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return CreatedRoomOut(
            player_id=player_id,
            room=_room_state_out(room, STATE["deck"], caller_id=player_id))


@app.post("/api/rooms/{code}/leave", response_model=RoomStateOut)
def leave_room(code: str, body: HostActionIn) -> RoomStateOut:
    """A non-host seat leaving the LOBBY frees it for real -- see `rooms.leave_room`'s
    own docstring for why this is a real seat removal, not just the caller going home.
    Refused once drafting has started, and refused for the host (nobody to hand the
    room off to)."""
    with _db() as conn:
        try:
            room = rooms.leave_room(conn, code, body.player_id)
        except rooms.RoomError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _room_state_out(room, STATE["deck"], caller_id=body.player_id)


@app.post("/api/rooms/{code}/kick", response_model=RoomStateOut)
def kick_room_player(code: str, body: KickPlayerIn) -> RoomStateOut:
    """The host's mirror of `/leave` -- see `rooms.kick_player`'s own docstring. Same
    scope: lobby only, and the host itself can never be the target."""
    with _db() as conn:
        try:
            room = rooms.kick_player(conn, code, body.player_id, body.target_id)
        except rooms.RoomError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _room_state_out(room, STATE["deck"], caller_id=body.player_id)


@app.post("/api/rooms/{code}/start", response_model=RoomStateOut)
def start_room(code: str, body: HostActionIn) -> RoomStateOut:
    """Host-only. Fills every seat still empty with a CPU seat and starts the first
    turn's clock -- a CPU's own twelve is no longer drafted up front (see web.rooms'
    own docstring): it depends on what humans take at their own turns, so it resolves
    turn by turn like everyone else, instantly whenever its turn comes up."""
    with _db() as conn:
        try:
            room = rooms.start_room(conn, code, body.player_id, STATE["deck"])
        except rooms.RoomError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _room_state_out(room, STATE["deck"], caller_id=body.player_id)


@app.post("/api/rooms/{code}/play-again", response_model=RoomStateOut)
def play_again_room(code: str, body: HostActionIn) -> RoomStateOut:
    """Any seated player, not the host only -- resets a finished ('complete' or
    'failed') room back to its own lobby: same code, same seats, same format/timer/
    draft_mode, but a fresh seed and both move logs cleared, ready for the host to
    start a new draft. Idempotent (see `rooms.play_again`'s own docstring), so it's
    safe for more than one player to click "play again" without coordinating."""
    with _db() as conn:
        try:
            room = rooms.play_again(conn, code, body.player_id)
        except rooms.RoomError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _room_state_out(room, STATE["deck"], caller_id=body.player_id)


@app.get("/api/rooms/open", response_model=list[OpenRoomOut])
def list_open_rooms() -> list[OpenRoomOut]:
    """The public browse list -- open, still-joinable rooms, newest first. Declared
    textually BEFORE `GET /api/rooms/{code}` below: FastAPI/Starlette matches routes in
    declaration order, so with the order reversed this path would be swallowed by
    `{code}` as a literal (nonexistent) room code lookup instead of ever reaching here."""
    with _db() as conn:
        return [
            OpenRoomOut(
                code=r.code, format=r.format, seats_filled=r.seats_filled,
                seats_total=rooms.ROOM_FORMATS[r.format], timer_seconds=r.timer_seconds,
                draft_mode=r.draft_mode, host_name=r.host_name)
            for r in rooms.list_open_rooms(conn)
        ]


@app.get("/api/rooms/{code}", response_model=RoomStateOut)
def get_room(code: str, player_id: str | None = None) -> RoomStateOut:
    """Poll target. Resolves any expired turn (auto-picking whoever timed out, or
    instantly resolving a CPU's turn) before returning, so a slow-polling client still
    sees an up-to-date room. `player_id` identifies the caller so only the currently
    active seat's own options are ever sent back to them -- everyone else sees that
    seat's franchise/season and every seat's team-so-far, never anyone's options."""
    with _db() as conn:
        try:
            room = rooms.room_state(conn, code, STATE["deck"])
        except rooms.RoomError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _room_state_out(room, STATE["deck"], caller_id=player_id)


@app.post("/api/rooms/{code}/pick", response_model=RoomStateOut)
def room_pick(code: str, body: RoomPickIn) -> RoomStateOut:
    with _db() as conn:
        try:
            room = rooms.submit_pick(
                conn, code, body.player_id, body.index, body.slot, STATE["deck"])
        except rooms.RoomError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _room_state_out(room, STATE["deck"], caller_id=body.player_id)


def _room_result_out(entry, player_id: str | None) -> RoomMatchResultOut:
    r = entry.result
    # `home_pid`/`away_pid` (not `a_pid`/`b_pid`) decide `you_home` -- the toss, not the
    # fixture list, is what actually assigns home/away, and `RoomResultEntry` keeps the
    # two pairs separate for exactly this reason (its own docstring has the why).
    you_home = None
    if player_id == entry.home_pid:
        you_home = True
    elif player_id == entry.away_pid:
        you_home = False
    return RoomMatchResultOut(
        stage=entry.stage,
        result=ResultOut(
            stage=r.stage, home=r.home.short, away=r.away.short,
            home_score=_score(r.home_runs, r.home_wickets),
            away_score=_score(r.away_runs, r.away_wickets),
            winner=None if r.winner is None else r.winner.short,
            margin=r.margin, yours=player_id in (entry.a_pid, entry.b_pid),
            you_home=you_home,
            home_innings=_innings_out(r.home_innings) if r.home_innings else None,
            away_innings=_innings_out(r.away_innings) if r.away_innings else None,
            # No single "the human" in a room -- see game.season.play_open's own
            # docstring for why a peer-to-peer match records neither of these at all.
            toss_won_by_you=None, toss_elected=None,
        ),
    )


def _room_current_match_out(fs, display_names: dict, player_id: str | None) -> RoomCurrentMatchOut:
    def name(pid: str) -> str:
        return display_names.get(pid, "")

    return RoomCurrentMatchOut(
        stage=fs.stage, a_name=name(fs.a_pid), b_name=name(fs.b_pid),
        a_pid=fs.a_pid, b_pid=fs.b_pid,
        pending_toss_winner_pid=fs.pending_toss_winner_pid,
        you_decide_toss=player_id is not None and player_id == fs.pending_toss_winner_pid,
        result=None if fs.result is None else ResultOut(
            stage=fs.stage, home=fs.result.home.short, away=fs.result.away.short,
            home_score=_score(fs.result.home_runs, fs.result.home_wickets),
            away_score=_score(fs.result.away_runs, fs.result.away_wickets),
            winner=None if fs.result.winner is None else fs.result.winner.short,
            margin=fs.result.margin,
            yours=player_id in (fs.a_pid, fs.b_pid),
            home_innings=_innings_out(fs.result.home_innings) if fs.result.home_innings else None,
            away_innings=_innings_out(fs.result.away_innings) if fs.result.away_innings else None,
            toss_won_by_you=None, toss_elected=None,
        ),
    )


def _room_match_out(room: rooms.Room, replay, player_id: str | None, deck) -> RoomMatchOut:
    """Maps `web.room_match.RoomMatchReplay` (pids throughout, since that module has no
    concept of display names) to the wire format (names, plus per-caller `you_decide_*`
    flags telling the frontend whether IT is the seat that can act -- the toss winner's
    own seat for a fixture's toss, the host's for advancing the round)."""

    # A filler seat's display name is its historical squad's own name (`room_sides`'s
    # own override, `RoomPlayer.name` for a REAL seat) -- read from ONE `room_sides`
    # call rather than separately in every place that names a seat below (the champion
    # banner, each current fixture's a_name/b_name, the journey squad), so none of them
    # can drift back to the raw stored placeholder ("CPU 1") the way
    # `_room_current_match_out` used to.
    sides = rooms.room_sides(room, deck)
    display_names = {pid: p.name for pid, p, _, _ in sides}

    def name(pid: str | None) -> str:
        return display_names.get(pid, "") if pid else ""

    current_matches = [_room_current_match_out(fs, display_names, player_id)
                        for fs in (replay.current_round or [])]

    table = None
    if replay.table is not None:
        table = [StandingOut(pos=i, name=row.standing.side.name, short=row.standing.side.short,
                             you=row.pid == player_id, played=row.standing.played,
                             won=row.standing.won, lost=row.standing.lost,
                             tied=row.standing.tied, points=row.standing.points,
                             nrr=round(row.standing.nrr, 3))
                 for i, row in enumerate(replay.table, 1)]

    league_revealed = league_total = None
    if replay.league_progress is not None:
        league_revealed, league_total = replay.league_progress

    you_are_out = player_id is not None and (
        player_id == replay.champion_pid or player_id in replay.eliminated_pids)
    leaders = (tournament_leaders([e.result for e in replay.results])
               if replay.results else None)
    journey = room_match_lib.room_journey(room, replay, player_id) if player_id else None
    squad = None
    rating = None
    if journey is not None:
        your_order, your_impact = next(
            ((order, impact) for pid, _, order, impact in sides if pid == player_id),
            ([], None))
        all_twelve = list(your_order) + ([your_impact] if your_impact is not None else [])
        squad = [_journey_entry(c, journey.acc) for c in all_twelve]
        rating = team_rating(all_twelve)

    return RoomMatchOut(
        format=room.format,
        results=[_room_result_out(e, player_id) for e in replay.results],
        table=table, complete=replay.complete,
        awaiting_start=replay.awaiting_start,
        you_decide_start=replay.awaiting_start and player_id == room.host_id,
        champion=name(replay.champion_pid) if replay.champion_pid else None,
        you_champion=(player_id is not None and replay.champion_pid == player_id)
                     if replay.complete else None,
        round_label=replay.round_label,
        current_matches=current_matches,
        advance_ready=replay.advance_ready,
        you_decide_advance=replay.advance_ready and player_id == room.host_id,
        league_revealed=league_revealed, league_total=league_total,
        you_decide_league_reveal=(replay.league_progress is not None
                                   and player_id == room.host_id),
        league_next_result=(_room_result_out(replay.league_next, player_id)
                             if replay.league_next is not None else None),
        you_are_out=you_are_out,
        runs=journey.runs if journey else None,
        wickets=journey.wickets if journey else None,
        played=journey.played if journey else None,
        won=journey.won if journey else None,
        lost=journey.lost if journey else None,
        tied=journey.tied if journey else None,
        top_scorer=journey.top_scorer[0] if journey else None,
        top_scorer_runs=journey.top_scorer[1] if journey else None,
        top_wicket_taker=journey.top_wicket_taker[0] if journey else None,
        top_wicket_taker_wickets=journey.top_wicket_taker[1] if journey else None,
        orange_cap=leaders.top_scorer[0] if leaders else None,
        orange_cap_runs=leaders.top_scorer[1] if leaders else None,
        purple_cap=leaders.top_wicket_taker[0] if leaders else None,
        purple_cap_wickets=leaders.top_wicket_taker[1] if leaders else None,
        overall_rating=rating.overall if rating else None,
        squad=squad,
    )


@app.get("/api/rooms/{code}/match", response_model=RoomMatchOut)
def room_match(code: str, player_id: str | None = None) -> RoomMatchOut:
    """Once every seat's twelve is drafted: a real toss (called live by whichever seat
    wins it), for a single match ('final'), a three-match knockout ('cup'), or a full
    league-plus-playoffs ('league') -- `game.season.play_open` via
    `web.room_match.replay_room_matches`, replayed from scratch on every poll exactly
    like the draft phase (A62). Independent fixtures within the same round (a cup's two
    semis, a league's Qualifier 1 + Eliminator) are all returned in `current_matches`
    at once, since they resolve in parallel rather than one at a time."""
    deck, model = STATE["deck"], STATE["model"]
    with _db() as conn:
        try:
            room, replay = room_match_lib.room_match_state(conn, code, deck, model)
        except rooms.RoomError as exc:
            status = 404 if "no room" in str(exc) else 409
            raise HTTPException(status_code=status, detail=str(exc)) from exc
    return _room_match_out(room, replay, player_id, deck)


@app.post("/api/rooms/{code}/match/toss", response_model=RoomMatchOut)
def room_match_toss(code: str, body: RoomTossIn) -> RoomMatchOut:
    """Only the seat that actually won THIS fixture's toss may call it -- `submit_toss`
    itself enforces that; a wrong-seat, wrong-fixture, or wrong-moment call is a 409,
    not a silent no-op."""
    deck, model = STATE["deck"], STATE["model"]
    with _db() as conn:
        try:
            room = room_match_lib.submit_toss(
                conn, code, body.player_id, body.stage, body.elects, deck, model)
            replay = room_match_lib.replay_room_matches(room, deck, model)
        except rooms.RoomError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _room_match_out(room, replay, body.player_id, deck)


@app.post("/api/rooms/{code}/match/advance", response_model=RoomMatchOut)
def room_match_advance(code: str, body: HostActionIn) -> RoomMatchOut:
    """Only the host paces the room from one ROUND of matches to the next -- gated on
    every fixture in the current round having resolved, not just one."""
    deck, model = STATE["deck"], STATE["model"]
    with _db() as conn:
        try:
            room = room_match_lib.advance_match(conn, code, body.player_id, deck, model)
            replay = room_match_lib.replay_room_matches(room, deck, model)
        except rooms.RoomError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _room_match_out(room, replay, body.player_id, deck)


@app.post("/api/rooms/{code}/match/start", response_model=RoomMatchOut)
def room_match_start(code: str, body: HostActionIn) -> RoomMatchOut:
    """Only the host unlocks the room's first match, once every seat has reviewed their
    own finished twelve on the squad-review screen -- mirrors `room_match_advance`
    exactly."""
    deck, model = STATE["deck"], STATE["model"]
    with _db() as conn:
        try:
            room = room_match_lib.start_matches(conn, code, body.player_id, deck, model)
            replay = room_match_lib.replay_room_matches(room, deck, model)
        except rooms.RoomError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _room_match_out(room, replay, body.player_id, deck)


class RoomLeagueRevealIn(BaseModel):
    player_id: str
    through: int = Field(ge=1, description="reveal the group stage through this many fixtures")


@app.post("/api/rooms/{code}/match/league-advance", response_model=RoomMatchOut)
def room_league_advance(code: str, body: RoomLeagueRevealIn) -> RoomMatchOut:
    """Only the host paces a league room's own round-robin, one fixture ('Continue',
    through = revealed + 1) or the whole rest of it ('Skip ahead', through = the group
    stage's own total) at a time -- the round-robin's OUTCOME is already fully decided
    (cached) the instant it's first simulated; this only controls how much of it has
    been shown."""
    deck, model = STATE["deck"], STATE["model"]
    with _db() as conn:
        try:
            room = room_match_lib.advance_league_reveal(
                conn, code, body.player_id, body.through, deck, model)
            replay = room_match_lib.replay_room_matches(room, deck, model)
        except rooms.RoomError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _room_match_out(room, replay, body.player_id, deck)
