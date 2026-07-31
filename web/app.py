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
import random
from contextlib import asynccontextmanager

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from etl.feasibility import SQUAD_SIZE, Card
from game.__main__ import (
    BAND_ORDER, attack, batting_order, choose_xi, overseas_status,
)
from game.simulator import Innings, load_model, play_innings
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
    yield
    STATE.clear()


app = FastAPI(title="IPLegends", version="0.1.0", lifespan=lifespan)


# --- wire format ----------------------------------------------------------------------

class CardOut(BaseModel):
    person_id: str
    name: str
    franchise: str | None
    season_year: int | None
    band: str | None
    role: str | None
    rating: int | None = Field(description="A58/A60's integer 70-99 card rating")
    overseas: bool | None = Field(description="null means unknown, never domestic (A23)")
    slot: str | None = Field(default=None, description="the slot this option would fill")


class DealOut(BaseModel):
    fs_id: int
    franchise: str | None
    season_year: int | None
    options: list[CardOut]


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


class InningsOut(BaseModel):
    side: str
    runs: int
    wickets: int
    overs: str
    extras: int
    batting: list[dict]
    bowling: list[dict]


class MatchOut(BaseModel):
    state: str
    first: InningsOut
    second: InningsOut
    result: str


# --- mapping ---------------------------------------------------------------------------

def _card(card: Card, slot: str | None = None) -> CardOut:
    return CardOut(
        person_id=card.person_id, name=card.name, franchise=card.franchise,
        season_year=card.season_year, band=card.band, role=card.role,
        rating=card.display, overseas=card.overseas, slot=slot,
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


@app.get("/api/match/{state}", response_model=MatchOut)
def match(state: str) -> MatchOut:
    player = _load(state)
    if not player.complete:
        raise HTTPException(
            status_code=409,
            detail=f"{len(player.squad)} of {SQUAD_SIZE} picks made")

    deck, model = STATE["deck"], STATE["model"]
    seed, choices = sess.decode(state)
    try:
        # Returns the LIVE rng, already advanced past both drafts, so the match continues
        # one stream exactly as `python -m game --seed N` does.
        house, rng = sess.after_draft(deck, seed, choices)
    except sess.InvalidState as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    xi_you = choose_xi([c for c, _ in player.squad])
    xi_them = choose_xi([c for c, _ in house])

    first = play_innings(model, batting_order(xi_you, model), attack(xi_them, model), rng)
    second = play_innings(model, batting_order(xi_them, model), attack(xi_you, model),
                          rng, target=first.runs)

    if second.runs > first.runs:
        result = f"OPPONENT win by {10 - second.wickets} wickets"
    elif first.runs > second.runs:
        result = f"YOU win by {first.runs - second.runs} runs"
    else:
        result = "tied"

    return MatchOut(
        state=state,
        first=_innings_out(first, "YOU"),
        second=_innings_out(second, "OPPONENT"),
        result=result,
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
