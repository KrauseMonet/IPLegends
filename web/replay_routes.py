"""Match replays and what-ifs, for the Almanack [A319].

    GET  /api/almanack/matches?year=2016   every match of a season, for the picker
    GET  /api/almanack/match/{id}          one real match, ball by ball, as a `ResultOut`
    GET  /api/almanack/cards               every player-season card, for choosing a swap
    POST /api/almanack/whatif              the match again in the engine, with any swaps

A real match is rebuilt from `data/real_matches.json.gz` (`game.real_match`), so the replay
plays on the same scoreboard as every simulated match. A what-if plays the two real elevens in
the match engine: each player is the card of the season he played that match in, the side
that batted first bats first again, and a swap puts another card in the swapped player's
place in the order. One request plays the match once to watch and `ODDS_RUNS` times to say
how often each side wins, so a viewer sees whether a result was the engine's usual one.

No database, no account, nothing stored: a what-if is a pure function of the match, the swaps
and the seed, and the seed comes back so the same what-if can be played again.
"""

from __future__ import annotations

import random
from functools import cache, lru_cache

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from game import real_match as RM
from tools import snapshot_matches

router = APIRouter()

ODDS_RUNS = 100          # plays behind the odds: ~1.5 s locally, cached per match and swaps
CARDS_SHOWN = 30         # player-seasons a search returns
SWAPS_MAX = 6            # swaps in one what-if, either side
BOWLERS_NEEDED = 5       # twenty overs at four each (A50)
CACHE = "public, max-age=3600, s-maxage=86400"


@cache
def _doc() -> RM.Doc | None:
    raw = snapshot_matches.read()
    return RM.Doc.of(raw) if raw else None


def doc() -> RM.Doc:
    d = _doc()
    if d is None:
        raise HTTPException(503, "match replays are not available on this deployment")
    return d


def _state():
    from web.app import STATE
    return STATE["deck"], STATE["model"]


def _app():
    from web import app as A
    return A


def _cards_by(deck) -> dict[tuple[int, str], object]:
    return {(c.fs_id, c.person_id): c for cs in deck.cards_by_fs.values() for c in cs}


def _dnb_key(deck):
    cards = _cards_by(deck)

    def key(fs, person):
        c = cards.get((fs, person))
        return (min(c.positions, default=12) if c else 12, person)
    return key


# --------------------------------------------------------------------------- the real match

class SideOut(BaseModel):
    fs: int
    name: str
    short: str
    crest: str | None
    batted_first: bool


class PlayerOut(BaseModel):
    person: str
    name: str
    display: int | None = None
    role: str | None = None
    bowls: bool = False
    impact: bool = False


class EngineSideOut(SideOut):
    players: list[PlayerOut] = Field(description="the engine's eleven, in batting order, then any Impact Player")
    filled: list[str] = Field(default_factory=list, description="named players with no card, replaced")


class MatchOut(BaseModel):
    id: str
    day: str
    year: int
    stage: str
    ground: str | None
    city: str | None
    toss: str | None
    player_of_match: str | None
    sides: list[EngineSideOut]
    result: dict


class MatchRowOut(BaseModel):
    id: str
    day: str
    stage: str
    home: str
    away: str
    home_crest: str | None
    away_crest: str | None
    home_score: str
    away_score: str
    winner: str | None
    margin: str


def _score(inn) -> str:
    if inn is None:
        return "-"
    w = "" if inn.wickets == 10 else f"/{inn.wickets}"
    return f"{inn.runs}{w} ({inn.overs})"


@router.get("/api/almanack/matches")
def matches(year: int, response: Response) -> dict:
    d = doc()
    A = _app()
    rows = []
    for m in d.matches.values():
        if m["year"] != year:
            continue
        r = RM.result(d, m["id"])
        rows.append(MatchRowOut(id=m["id"], day=m["day"], stage=r.stage, home=r.home.name, away=r.away.name,
                                home_crest=A._side_crest(r.home), away_crest=A._side_crest(r.away),
                                home_score=_score(r.home_innings), away_score=_score(r.away_innings),
                                winner=r.winner.name if r.winner else None, margin=r.margin))
    rows.sort(key=lambda r: (r.day, r.id))
    response.headers["cache-control"] = CACHE
    return {"year": year, "years": sorted({m["year"] for m in d.matches.values()}),
            "matches": [r.model_dump() for r in rows]}


def engine_side(d: RM.Doc, m: dict, fs: int, deck) -> tuple[list, object | None, list[str]]:
    """The real eleven as cards, in the engine's batting order, and the Impact Player.

    Batting order is the real one for everyone who batted; the rest follow by the card's
    highest batting place (A76). Players named but holding no card (A27: never batted or
    bowled that season) are replaced by the squad's best card not already named, and listed,
    so the page can say so. From 2023 a side named twelve; the twelfth in this order is its
    Impact Player, an approximation the page states (the archive names who came on, not who
    the captain would have chosen before the toss)."""
    cards = _cards_by(deck)
    batted: list[str] = []
    for raw in m["inns"]:
        if raw["so"] or raw["fs"] != fs:
            continue
        for b in raw["b"]:
            for i in (b[RM.STRIKER], b[RM.NON_STRIKER]):
                p = raw["p"][i]
                if p not in batted:
                    batted.append(p)
    named = m["xi"].get(str(fs), [])
    rest = [p for p in named if p not in batted]
    have = [cards[(fs, p)] for p in batted if (fs, p) in cards]
    rest_cards = sorted((cards[(fs, p)] for p in rest if (fs, p) in cards),
                        key=lambda c: (min(c.positions, default=12), -(c.display or 0)))
    order = have + rest_cards
    missing = [p for p in named if (fs, p) not in cards]
    filled = []
    if missing:
        spare = sorted((c for c in deck.cards_by_fs.get(fs, ()) if c not in order),
                       key=lambda c: -(c.display or 0))
        for p in missing:
            if spare and len(order) < max(11, len(named)):
                order.append(spare.pop(0))
                filled.append(d.people.get(p, p))
    xi, impact = order[:11], (order[11] if len(order) > 11 else None)
    return xi, impact, filled


def _side_out(d, m, fs, deck, first_fs) -> EngineSideOut:
    A = _app()
    s = RM.side(d, fs)
    xi, impact, filled = engine_side(d, m, fs, deck)
    players = [PlayerOut(person=c.person_id, name=c.name, display=c.display, role=c.role, bowls=c.has_bowl)
               for c in xi]
    if impact is not None:
        players.append(PlayerOut(person=impact.person_id, name=impact.name, display=impact.display,
                                 role=impact.role, bowls=impact.has_bowl, impact=True))
    return EngineSideOut(fs=fs, name=s.name, short=s.short, crest=A._side_crest(s), batted_first=fs == first_fs,
                         players=players, filled=filled)


def _first_fs(m: dict) -> int:
    main = [i for i in m["inns"] if not i["so"]]
    return main[0]["fs"] if main else m["a"]


@router.get("/api/almanack/match/{match_id}")
def match(match_id: str, response: Response) -> MatchOut:
    d = doc()
    m = d.matches.get(match_id)
    if m is None:
        raise HTTPException(404, "no such match")
    deck, _model = _state()
    A = _app()
    r = RM.result(d, match_id, _dnb_key(deck))
    toss = None
    if m.get("toss"):
        tname = RM.side(d, m["toss"]).name
        toss = f"{tname} won the toss and chose to {'bat' if m['elect'] == 'bat' else 'field'}"
    first = _first_fs(m)
    other = m["b"] if first == m["a"] else m["a"]
    response.headers["cache-control"] = CACHE
    return MatchOut(id=match_id, day=m["day"], year=m["year"], stage=r.stage, ground=m.get("ground"),
                    city=m.get("city"), toss=toss, player_of_match=d.people.get(m["pom"]) if m.get("pom") else None,
                    sides=[_side_out(d, m, first, deck, first), _side_out(d, m, other, deck, first)],
                    result=A._result_out(r, None).model_dump())


@router.get("/api/almanack/cards")
def cards(q: str = "", response: Response = None) -> dict:
    """Player-seasons whose name matches `q` (accents and case folded), best card first, for
    choosing who comes in: [fs, person, name, year, team, display, role, bowls]."""
    from tools.build_almanack import player_slug
    want = player_slug(q).replace("-", " ").strip()
    if len(want) < 2:
        return {"cards": []}
    deck, _model = _state()
    rows = []
    for fs, cs in deck.cards_by_fs.items():
        for c in cs:
            if want in player_slug(c.name).replace("-", " "):
                rows.append([fs, c.person_id, c.name, c.season_year, c.franchise, c.display, c.role, c.has_bowl])
    rows.sort(key=lambda r: (-(r[5] or 0), r[2], r[3] or 0))
    if response is not None:
        response.headers["cache-control"] = CACHE
    return {"cards": rows[:CARDS_SHOWN]}


@router.get("/api/almanack/card/{fs}/{person}")
def card(fs: int, person: str, response: Response) -> dict:
    """One card's name, for a swap read back from a shared link."""
    deck, _model = _state()
    c = _cards_by(deck).get((fs, person))
    if c is None:
        raise HTTPException(404, "no such player-season")
    response.headers["cache-control"] = CACHE
    return {"name": c.name, "team": c.franchise, "year": c.season_year, "display": c.display}


# --------------------------------------------------------------------------- what if

class SwapIn(BaseModel):
    fs: int = Field(description="the side the swap is made in")
    out: str = Field(description="person id of the player taken out")
    in_fs: int = Field(description="the franchise-season of the card brought in")
    in_person: str


class WhatIfIn(BaseModel):
    id: str
    swaps: list[SwapIn] = Field(default_factory=list, max_length=SWAPS_MAX)
    seed: int | None = None


class OddsOut(BaseModel):
    runs: int
    home_wins: int
    away_wins: int
    ties: int


class WhatIfOut(BaseModel):
    seed: int
    result: dict
    swaps: list[str]


def what_if_sides(d: RM.Doc, m: dict, swaps: list[SwapIn], deck):
    """The two sides for the engine, swaps applied. Raises ValueError with a reason a reader
    can act on, for a swap that cannot be made."""
    from game.season import Side
    cards = _cards_by(deck)
    first = _first_fs(m)
    other = m["b"] if first == m["a"] else m["a"]
    built = {}
    for fs in (first, other):
        xi, impact, _filled = engine_side(d, m, fs, deck)
        built[fs] = [xi, impact]
    said = []
    for s in swaps:
        if s.fs not in built:
            raise ValueError("that side is not in this match")
        xi, impact = built[s.fs]
        new = cards.get((s.in_fs, s.in_person))
        if new is None:
            raise ValueError("no such player-season")
        if any(c.person_id == s.in_person for c in xi + ([impact] if impact else [])):
            raise ValueError(f"{new.name} is already in that side")
        if impact is not None and impact.person_id == s.out:
            old, built[s.fs][1] = impact, new
        else:
            at = next((i for i, c in enumerate(xi) if c.person_id == s.out), None)
            if at is None:
                raise ValueError("that player is not in that side")
            old, xi[at] = xi[at], new
        said.append(f"{new.name} ({new.franchise} {new.season_year}) for {old.name}")
    on = [{c.person_id for c in built[fs][0] + ([built[fs][1]] if built[fs][1] else [])} for fs in (first, other)]
    both = on[0] & on[1]
    if both:
        who = next(c.name for c in built[first][0] + ([built[first][1]] if built[first][1] else []) if c.person_id in both)
        raise ValueError(f"{who} cannot play for both sides")
    sides = []
    for fs in (first, other):
        xi, impact = built[fs]
        if sum(c.has_bowl for c in xi + ([impact] if impact else [])) < BOWLERS_NEEDED:
            name = RM.side(d, fs).name
            raise ValueError(f"{name} would have fewer than {BOWLERS_NEEDED} bowlers to get through twenty overs")
        base = RM.side(d, fs)
        sides.append(Side(name=base.name, short=base.short, xi=list(xi), impact=impact,
                          franchise=base.franchise, year=base.year))
    return sides[0], sides[1], said


@router.post("/api/almanack/whatif")
def what_if(body: WhatIfIn) -> WhatIfOut:
    """The match once, to watch. The seed decides it, and comes back."""
    from game.season import play
    d, m, (home, away, said) = _prepare(body)
    _deck, model = _state()
    seed = body.seed if body.seed is not None else random.SystemRandom().randrange(1, 10**9)
    r = play(model, home, away, random.Random(seed), stage=RM.stage_label(m))
    return WhatIfOut(seed=seed, result=_app()._result_out(r, None).model_dump(), swaps=said)


@router.post("/api/almanack/odds")
def odds(body: WhatIfIn, response: Response) -> OddsOut:
    """How often each side wins over `ODDS_RUNS` plays. The seeds are fixed, so the same match
    and the same swaps always give the same odds, whatever the seed of the one being watched."""
    key = (body.id, tuple((s.fs, s.out, s.in_fs, s.in_person) for s in body.swaps))
    response.headers["cache-control"] = "no-store"
    return _odds(key)


@lru_cache(maxsize=512)
def _odds(key) -> OddsOut:
    from game.season import play
    match_id, swaps = key
    body = WhatIfIn(id=match_id, swaps=[SwapIn(fs=a, out=b, in_fs=c, in_person=e) for a, b, c, e in swaps])
    _d, _m, (home, away, _said) = _prepare(body)
    _deck, model = _state()
    hw = aw = ties = 0
    for i in range(ODDS_RUNS):
        x = play(model, home, away, random.Random(f"odds:{match_id}:{i}"))
        if x.winner is home:
            hw += 1
        elif x.winner is away:
            aw += 1
        else:
            ties += 1
    return OddsOut(runs=ODDS_RUNS, home_wins=hw, away_wins=aw, ties=ties)


def _prepare(body: WhatIfIn):
    d = doc()
    m = d.matches.get(body.id)
    if m is None:
        raise HTTPException(404, "no such match")
    deck, _model = _state()
    try:
        return d, m, what_if_sides(d, m, body.swaps, deck)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
