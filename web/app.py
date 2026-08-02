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
from contextlib import asynccontextmanager

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from etl.feasibility import REROLL_KINDS, TWELVE_SIZE, XI_SIZE, Card
from game.__main__ import overseas_status
from game.season import (
    MATCHES_EACH, TEAMS, JourneyAccumulator, Season, Side, historical_sides,
    journey_stats, play, run_cup, run_league, run_playoffs,
)
from game.simulator import load_model
from web import session as sess
from web import rooms

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
    rating: int | None = Field(description="A58/A60's integer 70-99 card rating")
    overseas: bool | None = Field(description="null means unknown, never domestic (A23)")
    positions: list[int] = Field(
        default_factory=list,
        description="batting numbers this player is eligible for (A72); "
                    "empty only for the defensive-net 'unrated' case")
    blocked: str | None = Field(default=None, description="why he cannot be taken")


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


class SeasonOut(BaseModel):
    state: str
    your_side: str
    table: list[StandingOut]
    your_results: list[ResultOut]
    playoffs: list[ResultOut]
    champion: str
    you_champion: bool
    matches_each: int = MATCHES_EACH
    teams: int = TEAMS


class JourneyStatsOut(BaseModel):
    state: str
    runs: int
    wickets: int
    played: int
    won: int
    lost: int
    tied: int
    champion: bool
    top_scorer: str
    top_scorer_runs: int
    top_wicket_taker: str
    top_wicket_taker_wickets: int
    squad: list[CardOut] = Field(description="the final twelve, in batting order then Impact")


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
        default=None, description="this round's dealt franchise-season, visible to everyone")
    order: list[CardOut | None]
    impact: CardOut | None


class RoomStateOut(BaseModel):
    code: str
    format: str
    seats: int
    timer_seconds: int
    host_id: str
    status: str = Field(description="lobby | drafting | complete | failed")
    round: int
    rounds_total: int = TWELVE_SIZE
    seconds_remaining: int
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
    """What the icon shows. Derived from the RATINGS rather than from `squad_members.role`,
    because the card is what a drafter is buying: a man rated in both disciplines is an
    all-rounder here even if A26's thresholds classified him as one or the other."""
    if card.role == "keeper":
        return "keeper"
    if card.has_bat and card.has_bowl:
        return "allrounder"
    if card.has_bowl:
        return "bowler"
    if card.has_bat:
        return "batter"
    return "unrated"


def _card(card: Card, blocked: str | None = None) -> CardOut:
    return CardOut(
        person_id=card.person_id, name=card.name, franchise=card.franchise,
        season_year=card.season_year, band=card.band, role=card.role,
        kind=_kind(card), rating=card.display, overseas=card.overseas,
        positions=sorted(card.positions), blocked=blocked,
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
    """
    from etl.feasibility import (
        BOWLERS_IN_TWELVE, IMPACT_SLOT, LOWER_ORDER_BAND, OVERSEAS_CAP, TWELVE_SIZE,
    )
    deck = STATE["deck"]
    seasons = {c.season_year for cards in deck.cards_by_fs.values() for c in cards}
    return {
        "xi_size": XI_SIZE,
        "twelve_size": TWELVE_SIZE,
        "positions": list(range(1, XI_SIZE + 1)),
        "impact_slot": IMPACT_SLOT,
        "bowlers_needed": BOWLERS_IN_TWELVE,
        "overseas_cap": OVERSEAS_CAP,
        "lower_order_default": list(LOWER_ORDER_BAND),
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
    bench and nothing to rearrange afterward, so a single request settles both."""
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


def _score(runs: int, wickets: int) -> str:
    return f"{runs}/{wickets}"


def _result_out(r, you: Side) -> ResultOut:
    return ResultOut(
        stage=r.stage, home=r.home.short, away=r.away.short,
        home_score=_score(r.home_runs, r.home_wickets),
        away_score=_score(r.away_runs, r.away_wickets),
        winner=None if r.winner is None else r.winner.short,
        margin=r.margin, yours=(r.home is you or r.away is you),
    )


@app.get("/api/season/{state}", response_model=SeasonOut)
def season(state: str) -> SeasonOut:
    """A full campaign: fourteen league matches, a table, then the playoffs.

    Gated on `playable`, not just `squad_complete` -- a completed draft whose own
    arrangement still fails a rule (should not happen once every pick is forward-checked,
    but is checked rather than assumed) is not a squad that can take the field.

    Around three seconds of simulation -- seventy league matches plus four playoff ties,
    each two innings scored ball by ball. Done inside the request rather than queued,
    because it is deterministic from the state and therefore cacheable by anything in
    front of it; a job queue would add a store to a design whose whole point (SPEC 11)
    is not having one.
    """
    player = _load(state)
    if not player.squad_complete:
        raise HTTPException(status_code=409,
                            detail=f"{len(player.picks)} of {TWELVE_SIZE} picks made")
    if not player.playable:
        raise HTTPException(status_code=409, detail="; ".join(player.errors))

    deck, model = STATE["deck"], STATE["model"]
    seed, moves = sess.decode(state)
    rng = random.Random(seed)
    sess.replay_stream(deck, seed, moves, rng)      # advance exactly as the draft did

    yours = Side(name="Your eleven", short="YOU",
                 xi=list(player.order), impact=player.impact, you=True)
    opposition = historical_sides(deck, rng, TEAMS - 1)
    if len(opposition) < TEAMS - 1:
        raise HTTPException(status_code=500, detail="could not field a full league")

    sides = [yours] + opposition
    result = run_playoffs(model, run_league(model, sides, rng), rng)

    return SeasonOut(
        state=state,
        your_side=yours.name,
        table=[StandingOut(pos=i, name=s.side.name, short=s.side.short, you=s.side.you,
                           played=s.played, won=s.won, lost=s.lost, tied=s.tied,
                           points=s.points, nrr=round(s.nrr, 3))
               for i, s in enumerate(result.table, 1)],
        your_results=[_result_out(r, yours) for r in result.results
                      if r.home is yours or r.away is yours],
        playoffs=[_result_out(r, yours) for r in result.playoffs],
        champion=result.champion.name,
        you_champion=result.champion is yours,
    )


@app.get("/api/card/{state}", response_model=JourneyStatsOut)
def card_stats(state: str) -> JourneyStatsOut:
    """The end-of-tournament journey card: runs, wickets, record, champion or not, the
    top scorer and top wicket-taker among the player's OWN twelve, and the squad itself --
    everything the shareable card needs.

    Replays the exact same season `/api/season` does -- same seed, same moves, so the same
    opposition and the same match sequence come out (SPEC 11.3's determinism) -- and
    additionally threads a `JourneyAccumulator` through so the player's own runs and
    wickets are folded in match by match rather than re-derived from a stored scorecard
    that does not exist.
    """
    player = _load(state)
    if not player.squad_complete:
        raise HTTPException(status_code=409,
                            detail=f"{len(player.picks)} of {TWELVE_SIZE} picks made")
    if not player.playable:
        raise HTTPException(status_code=409, detail="; ".join(player.errors))

    deck, model = STATE["deck"], STATE["model"]
    seed, moves = sess.decode(state)
    rng = random.Random(seed)
    sess.replay_stream(deck, seed, moves, rng)

    yours = Side(name="Your eleven", short="YOU",
                 xi=list(player.order), impact=player.impact, you=True)
    opposition = historical_sides(deck, rng, TEAMS - 1)
    if len(opposition) < TEAMS - 1:
        raise HTTPException(status_code=500, detail="could not field a full league")

    sides = [yours] + opposition
    acc = JourneyAccumulator()
    league = run_league(model, sides, rng, track=yours, stats=acc)
    season_result = run_playoffs(model, league, rng, track=yours, stats=acc)
    stats = journey_stats(season_result, yours, acc)

    all_twelve = [c for c in player.order if c is not None]
    if player.impact is not None:
        all_twelve.append(player.impact)

    return JourneyStatsOut(
        state=state, runs=stats.runs, wickets=stats.wickets,
        played=stats.played, won=stats.won, lost=stats.lost, tied=stats.tied,
        champion=stats.champion,
        top_scorer=stats.top_scorer[0], top_scorer_runs=stats.top_scorer[1],
        top_wicket_taker=stats.top_wicket_taker[0],
        top_wicket_taker_wickets=stats.top_wicket_taker[1],
        squad=[_card(c) for c in all_twelve],
    )


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
# Friends draft together, live, turn by turn (in-memory, v1 -- see `web.rooms`'s own
# docstring for why, and for the round-by-round mechanic itself). Nothing here decides
# anything about cricket either: `web.rooms` reuses `web.session.replay` per seat, which
# reuses `etl.feasibility.run_draft`, so a room's picks are validated exactly the way a
# solo draft's are.

def _room_player_out(player: rooms.RoomPlayer, deck) -> RoomPlayerOut:
    s = rooms.player_session(player, deck)
    if s is None:
        order = [None if c is None else _card(c) for c in (player.order or [])]
        impact = None if player.impact is None else _card(player.impact)
        deal = None
    else:
        order = [None if c is None else _card(c) for c in s.order]
        impact = None if s.impact is None else _card(s.impact)
        deal = None if s.deal is None else DealOut(
            fs_id=s.deal.fs_id, franchise=s.deal.franchise, season_year=s.deal.season_year,
            options=[_card(c) for c in s.deal.options],
        )
    return RoomPlayerOut(
        player_id=player.player_id, name=player.name, is_cpu=player.is_cpu,
        picks_made=TWELVE_SIZE if player.is_cpu else len(player.moves),
        done=player.done, deal=deal, order=order, impact=impact,
    )


def _room_state_out(room: rooms.Room, deck) -> RoomStateOut:
    remaining = 0
    if room.status == "drafting":
        remaining = max(0, round(room.timer_seconds - (time.monotonic() - room.round_started_at)))
    return RoomStateOut(
        code=room.code, format=room.format, seats=room.seats,
        timer_seconds=room.timer_seconds, host_id=room.host_id,
        status=room.status, round=room.round, seconds_remaining=remaining,
        players=[_room_player_out(p, deck) for p in room.players.values()],
        failure_reason=room.failure_reason,
    )


@app.post("/api/rooms", response_model=CreatedRoomOut)
def create_room(body: CreateRoomIn) -> CreatedRoomOut:
    try:
        room, player_id = rooms.create_room(body.format, body.timer_seconds, body.host_name)
    except rooms.RoomError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CreatedRoomOut(player_id=player_id, room=_room_state_out(room, STATE["deck"]))


@app.post("/api/rooms/{code}/join", response_model=CreatedRoomOut)
def join_room(code: str, body: JoinRoomIn) -> CreatedRoomOut:
    try:
        room, player_id = rooms.join_room(code, body.name)
    except rooms.RoomError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CreatedRoomOut(player_id=player_id, room=_room_state_out(room, STATE["deck"]))


@app.post("/api/rooms/{code}/start", response_model=RoomStateOut)
def start_room(code: str, body: HostActionIn) -> RoomStateOut:
    """Host-only. Fills every seat still empty with a CPU-drafted twelve (instantly, in
    full) and starts round 0's clock."""
    try:
        room = rooms.start_room(code, body.player_id, STATE["deck"])
    except rooms.RoomError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _room_state_out(room, STATE["deck"])


@app.get("/api/rooms/{code}", response_model=RoomStateOut)
def get_room(code: str) -> RoomStateOut:
    """Poll target. Resolves any expired round (auto-picking whoever is still waiting)
    before returning, so a slow-polling client still sees an up-to-date room."""
    try:
        room = rooms.room_state(code, STATE["deck"])
    except rooms.RoomError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _room_state_out(room, STATE["deck"])


@app.post("/api/rooms/{code}/pick", response_model=RoomStateOut)
def room_pick(code: str, body: RoomPickIn) -> RoomStateOut:
    try:
        room = rooms.submit_pick(code, body.player_id, body.index, body.slot, STATE["deck"])
    except rooms.RoomError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _room_state_out(room, STATE["deck"])


@app.get("/api/rooms/{code}/match", response_model=RoomMatchOut)
def room_match(code: str, player_id: str | None = None) -> RoomMatchOut:
    """Once every seat's twelve is drafted: a single match ('final'), a three-match
    knockout ('cup'), or a full league-plus-playoffs ('league') -- the same `play`/
    `run_cup`/`run_league`+`run_playoffs` the solo season already uses, just fed the
    room's own drafted sides instead of nine historical ones."""
    deck, model = STATE["deck"], STATE["model"]
    try:
        room = rooms.room_state(code, deck)
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
