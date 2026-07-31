"""SPEC 11. The HTTP surface: a thin, stateless layer over the draft and the engine.

Thin is the requirement, not an aspiration. Every rule this API appears to enforce -- the
overseas cap, the deal-time guarantee, five bowlers, the four-overseas XI -- is enforced in
`etl.feasibility` or `game`, and is reached from here by calling that code rather than by
restating it. Nothing in this module decides anything about cricket.

It holds no session state. The deck and the state model are read once at boot and never
re-queried, so a request touches the database exactly never (SPEC 11.3).
"""

from __future__ import annotations

import os
import pathlib
import random
from contextlib import asynccontextmanager

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from etl.feasibility import SQUAD_SIZE, Card
from game.__main__ import (
    BAND_ORDER, attack, batting_order, choose_xi, overseas_status,
)
from game.season import (
    MATCHES_EACH, TEAMS, Season, Side, historical_sides, run_league, run_playoffs,
)
from game.simulator import load_model
from web import session as sess

# Populated at boot. The deck is 1,689 cards and takes ~3.8 s to build; the state model is
# two stored grids. Both are immutable for the life of the process.
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

    A squad is about twenty men and roughly half clear A33's floors, so a deal that showed
    only the rated ones would misrepresent the squad -- Chennai 2010 would look like ten
    players. These are returned as plain dicts and never as `Card`s, deliberately: a Card
    is the draft's currency, and one of these entering `cards_by_fs` would make an unrated
    player draftable through the `open` slot.
    """
    # The reason carries the NUMBERS, not just the word. McCullum made 158* in the first
    # match ever played and is unrated for 2008 -- because he appeared four times and faced
    # 92 balls, under A33's hundred-ball floor. "Not rated" alone reads as a bug to anyone
    # who remembers the innings; "4 matches, 92 balls" reads as the rule it is.
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
            "overseas": overseas, "slot": None,
            "blocked": ", ".join(bits) + " · below the rating floor",
        })
    return out


app = FastAPI(title="IPLegends", version="0.1.0", lifespan=lifespan)

STATIC = pathlib.Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """One page, served from disk. The client is a static file with no build step: it
    reads `/api/meta` for the template rather than embedding a copy of it (A19)."""
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
    slot: str | None = Field(default=None, description="the slot this option would fill")
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
    picks_total: int = SQUAD_SIZE
    overseas_taken: int
    overseas_cap: int
    squad: list[CardOut]
    deal: DealOut | None
    complete: bool


class PickIn(BaseModel):
    index: int = Field(ge=0, description="an index into the deal's options")


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


def _card(card: Card, slot: str | None = None, blocked: str | None = None) -> CardOut:
    return CardOut(
        person_id=card.person_id, name=card.name, franchise=card.franchise,
        season_year=card.season_year, band=card.band, role=card.role,
        kind=_kind(card), rating=card.display, overseas=card.overseas,
        slot=slot, blocked=blocked,
    )


def _session_out(s: sess.Session) -> SessionOut:
    from etl.feasibility import OVERSEAS_CAP
    return SessionOut(
        state=s.state,
        picks_made=len(s.squad),
        overseas_taken=sum(1 for c, _ in s.squad if c.overseas is True),
        overseas_cap=OVERSEAS_CAP,
        squad=[_card(c, slot) for c, slot in s.squad],
        deal=None if s.deal is None else DealOut(
            fs_id=s.deal.fs_id, franchise=s.deal.franchise,
            season_year=s.deal.season_year,
            options=[_card(c, slot) for c, slot in s.deal.options],
            blocked=[_card(c, None, why) for c, why in (s.deal.blocked or [])]
                    + STATE["unrated"].get(s.deal.fs_id, []),
        ),
        complete=s.complete,
    )


def _load(state: str) -> sess.Session:
    try:
        seed, choices = sess.decode(state)
        return sess.replay(STATE["deck"], seed, choices)
    except sess.InvalidState as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _innings_out(innings: Innings, side: str) -> InningsOut:
    return InningsOut(
        side=side, runs=innings.runs, wickets=innings.wickets,
        overs=innings.overs, extras=innings.extras,
        batting=[{"name": b.player.name, "runs": b.runs, "balls": b.balls,
                  "out": b.out, "rated": b.player.rated_bat}
                 for b in innings.batting if b.balls or b.out],
        bowling=[{"name": b.player.name, "overs": f"{b.balls // 6}.{b.balls % 6}",
                  "runs": b.runs, "wickets": b.wickets}
                 for b in innings.bowling if b.balls],
    )


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
    template baked into JavaScript is a second copy of `TEMPLATE`, and the day A40 is
    retuned again the board would show the old shape with nothing to catch it.
    """
    from etl.feasibility import OVERSEAS_CAP, TEMPLATE
    deck = STATE["deck"]
    seasons = {c.season_year for cards in deck.cards_by_fs.values() for c in cards}
    return {
        "template": TEMPLATE,
        "squad_size": SQUAD_SIZE,
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
    current = _load(state)
    if current.complete:
        raise HTTPException(status_code=409, detail="this squad is already full")
    if not 0 <= body.index < len(current.deal.options):
        raise HTTPException(
            status_code=400,
            detail=f"index {body.index} is not among the "
                   f"{len(current.deal.options)} options dealt")
    seed, choices = sess.decode(state)
    return _session_out(_load(sess.encode(seed, choices + (body.index,))))


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

    Around three seconds of simulation -- seventy league matches plus four playoff ties,
    each two innings scored ball by ball. Done inside the request rather than queued,
    because it is deterministic from the state and therefore cacheable by anything in
    front of it; a job queue would add a store to a design whose whole point (SPEC 11)
    is not having one.
    """
    player = _load(state)
    if not player.complete:
        raise HTTPException(status_code=409,
                            detail=f"{len(player.squad)} of {SQUAD_SIZE} picks made")

    deck, model = STATE["deck"], STATE["model"]
    seed, choices = sess.decode(state)
    rng = random.Random(seed)
    sess.replay_stream(deck, seed, choices, rng)      # advance exactly as the draft did

    yours = Side(name="Your eleven", short="YOU",
                 xi=choose_xi([c for c, _ in player.squad]), you=True)
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


@app.get("/api/xi/{state}")
def playing_xi(state: str) -> dict:
    """The eleven the engine would field, and whether it is legal on the overseas rule."""
    s = _load(state)
    if not s.complete:
        raise HTTPException(status_code=409, detail="squad is not full")
    xi = choose_xi([c for c, _ in s.squad])
    ordered = sorted(xi, key=lambda c: (BAND_ORDER.get(c.band, 9),
                                        -(c.bat if c.has_bat else -9.9)))
    return {
        "xi": [_card(c).model_dump() for c in ordered],
        "overseas": overseas_status(xi),
    }
