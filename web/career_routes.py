"""Build a Career's API [A199]. No database and no account, like the draft and the auction.

**The state is a signed checkpoint, not a replay.** Every other game here replays its moves
from scratch on each request (A62), and that is what makes an unsigned state trustworthy: a
forged move simply fails to replay. A career cannot afford it -- a season costs up to ~2 s on a
cold production server, and a career runs to nineteen of them -- so each response carries the
career as it stands after its last completed season (`game.career.Checkpoint`), signed with the
site's own secret, and a request resumes from it and plays at most one season. The signature
puts back what replay gave for free: the page cannot hand the server a season it never played.
`"career:"` prefixes the signed payload, so a career token can never pass as a session cookie
(whose payload is an account id).
"""

from __future__ import annotations

import base64
import hmac
import json
import random

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from game import career as C
from etl.franchise_map import canonical
from game.auction import crore
from game.site import SITE_HOST, SITE_NAME
from web import auth, bingo_routes, crests

router = APIRouter()

TOKEN_VERSION = 1
NAME_MAX = 30


def _state():
    from web.app import STATE      # app imports this module; read the deck at call time
    return STATE["deck"], STATE["model"]


def _facts():
    f = bingo_routes._facts()
    return list(f.players.values()) if f is not None else None


# --- the token ------------------------------------------------------------------------------


def _sign(payload: str) -> str:
    return auth._sign("career:" + payload)


def make_token(seed: int, setup: C.Setup, career: C.Career, partial: list[str]) -> str:
    body = json.dumps({"v": TOKEN_VERSION, "seed": seed,
                       "setup": [setup.name, setup.role, setup.debut, setup.overseas],
                       "cp": career.checkpoint.to_dict(), "partial": partial},
                      separators=(",", ":"))
    payload = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return f"{payload}.{_sign(payload)}"


def read_token(token: str) -> tuple[int, C.Setup, C.Checkpoint, list[str]]:
    """The career a token describes, or a 400 if it was not signed here or is malformed."""
    try:
        payload, sig = token.rsplit(".", 1)
    except ValueError:
        raise HTTPException(400, "That career could not be read.") from None
    if not hmac.compare_digest(_sign(payload), sig):
        raise HTTPException(400, "That career could not be read.")
    try:
        d = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if d.get("v") != TOKEN_VERSION:
            raise ValueError("old token")
        name, role, debut, overseas = d["setup"]
        return (int(d["seed"]), C.Setup(name, role, int(debut), bool(overseas)),
                C.Checkpoint.from_dict(d["cp"]), [str(m) for m in d["partial"]])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "That career could not be read.") from None


# --- what the page sees ---------------------------------------------------------------------


class TemplateOut(BaseModel):
    name: str
    year: int
    franchise: str
    crest: str | None


class HighlightOut(BaseModel):
    stage: str
    opponent: str
    text: str


class SeasonOut(BaseModel):
    year: int
    franchise: str
    short: str
    crest: str | None
    price: str
    stayed: bool
    squad: bool
    captain: bool
    event: str
    choice: str
    card: int                     # the season's card, after the reputation floor
    form: int                     # the season's own card
    matches: int
    missed: int
    runs: int
    balls: int
    outs: int
    highest: int
    fifties: int
    hundreds: int
    wickets: int
    bowl_balls: int
    bowl_runs: int
    best: str | None              # "4/21"
    position: int
    teams: int
    playoffs: bool
    finalist: bool
    champion: bool
    orange_cap: bool
    purple_cap: bool
    highlights: list[HighlightOut]


class OfferOut(BaseModel):
    franchise: str
    short: str
    crest: str | None
    price: str
    strength: int | None
    stay: bool


class EventOptionOut(BaseModel):
    label: str
    hint: str                     # what it does, worded from the option's own numbers


class EventOut(BaseModel):
    key: str
    title: str
    text: str
    options: list[EventOptionOut]


class PendingOut(BaseModel):
    kind: str                     # "offer" | "event"
    year: int
    season_no: int                # 1-based
    squad: bool
    offers: list[OfferOut]
    event: EventOut | None = None
    can_retire: bool
    # Your last club, when it has no team this year (Chennai and Rajasthan in 2016-17, Deccan
    # after 2012): why there is no offer to stay.
    club_gone: str | None = None


class SummaryOut(BaseModel):
    seasons: int
    regular_seasons: int
    matches: int
    runs: int
    strike_rate: float | None
    average: float | None
    highest: int
    fifties: int
    hundreds: int
    wickets: int
    economy: float | None
    titles: int
    finals: int
    orange_caps: int
    purple_caps: int
    captaincies: int
    franchises: list[str]
    earnings: str
    peak_card: int
    peak_year: int
    runs_rank: int | None
    wickets_rank: int | None
    ending: str                   # "retired" | "released" | "the end of the archive"


class CareerOut(BaseModel):
    token: str
    name: str
    role: str
    role_label: str
    debut: int
    overseas: bool
    start: TemplateOut
    ceiling: TemplateOut
    seasons: list[SeasonOut]
    pending: PendingOut | None
    summary: SummaryOut | None
    share: str | None


def _template(c) -> TemplateOut:
    return TemplateOut(name=c.name, year=c.season_year, franchise=c.franchise,
                       crest=crests.crest_url(c.franchise, c.season_year))


def _season(r: C.SeasonRecord) -> SeasonOut:
    return SeasonOut(
        year=r.year, franchise=r.franchise, short=r.short,
        crest=crests.crest_url(r.franchise, r.year), price=crore(r.price), stayed=r.stayed,
        squad=r.squad, captain=r.captain, event=r.event, choice=r.choice, card=r.display,
        form=r.form, matches=r.matches, missed=r.missed, runs=r.runs, balls=r.balls,
        outs=r.outs, highest=r.highest, fifties=r.fifties, hundreds=r.hundreds,
        wickets=r.wickets, bowl_balls=r.bowl_balls, bowl_runs=r.bowl_runs,
        best=f"{r.best[0]}/{r.best[1]}" if r.bowl_balls else None, position=r.position,
        teams=r.teams, playoffs=r.playoffs, finalist=r.finalist, champion=r.champion,
        orange_cap=r.orange_cap, purple_cap=r.purple_cap,
        highlights=[HighlightOut(stage=h.stage, opponent=h.opponent, text=h.text)
                    for h in r.highlights])


def option_hint(o: C.EventOption) -> str:
    """What an answer does, in words, derived from the numbers it applies -- never written
    beside them, so the hint cannot say one thing while the option does another."""
    bits = []
    if o.season:
        bits.append(("a lift this season" if o.season > 0 else "a dip this season"))
    if o.permanent:
        bits.append("better from here on" if o.permanent > 0 else "a lasting cost")
    if o.captain:
        bits.append("you captain the side")
    return ", ".join(bits).capitalize() if bits else "No change"


def _club_gone(career: C.Career) -> str | None:
    p = career.pending
    if p is None or p.kind != "offer" or not career.seasons:
        return None
    last = career.seasons[-1].franchise
    deck, _ = _state()
    playing = {canonical(deck.cards_by_fs[fs][0].franchise) for fs in C.year_fs_ids(deck, p.year)}
    return last if canonical(last) not in playing else None


def _pending(p: C.Pending) -> PendingOut:
    event = None
    if p.kind == "event":
        event = EventOut(key=p.event.key, title=p.event.title, text=p.event.text,
                         options=[EventOptionOut(label=o.label, hint=option_hint(o))
                                  for o in p.event.options])
    return PendingOut(
        kind=p.kind, year=p.year, season_no=p.k + 1, squad=p.squad,
        offers=[OfferOut(franchise=o.franchise, short=o.short,
                         crest=crests.crest_url(o.franchise, p.year), price=crore(o.price),
                         strength=o.strength, stay=o.stay) for o in p.offers],
        event=event, can_retire=p.kind == "offer" and p.k > 0)


def _summary(c: C.Career) -> SummaryOut:
    s = c.summary
    ending = "retired" if c.retired else "released" if c.released else "the end of the archive"
    return SummaryOut(
        seasons=s.seasons, regular_seasons=s.regular_seasons, matches=s.matches, runs=s.runs,
        strike_rate=round(100 * s.runs / s.balls, 1) if s.balls else None,
        average=round(s.runs / s.outs, 1) if s.outs else None, highest=s.highest,
        fifties=s.fifties, hundreds=s.hundreds, wickets=s.wickets,
        economy=round(6 * s.bowl_runs / s.bowl_balls, 2) if s.bowl_balls else None,
        titles=s.titles, finals=s.finals, orange_caps=s.orange_caps,
        purple_caps=s.purple_caps, captaincies=s.captaincies, franchises=s.franchises,
        earnings=crore(s.earnings), peak_card=s.peak_display, peak_year=s.peak_year,
        runs_rank=s.runs_rank, wickets_rank=s.wickets_rank, ending=ending)


def share_text(c: C.Career) -> str:
    """One line per career, no player names: what happened, not who it was built from
    (the spin is the surprise for whoever plays next, as A129 keeps a daily's answer back)."""
    s = c.summary
    role = C.ROLES[c.setup.role]["label"]
    marks = "".join("🏆" if r.champion else "🟧" if r.orange_cap else "🟪" if r.purple_cap
                    else "⬜" if r.squad else "🟩" for r in c.seasons)
    bits = [f"{s.seasons} seasons"]
    if s.runs:
        bits.append(f"{s.runs:,} runs")
    if s.wickets:
        bits.append(f"{s.wickets} wickets")
    if s.titles:
        bits.append(f"{s.titles} title{'s' if s.titles != 1 else ''}")
    bits.append(f"peak card {s.peak_display}")
    return (f"{SITE_NAME} · Build a Career\n{role}, debut {c.setup.debut}: "
            f"{', '.join(bits)}\n{marks}\n{SITE_HOST}/career")


def _out(seed: int, career: C.Career, partial: list[str]) -> CareerOut:
    st = career.setup
    return CareerOut(
        token=make_token(seed, st, career, partial), name=st.name, role=st.role,
        role_label=C.ROLES[st.role]["label"], debut=st.debut, overseas=st.overseas,
        start=_template(career.start), ceiling=_template(career.ceiling),
        seasons=[_season(r) for r in career.seasons],
        pending=(_pending(career.pending).model_copy(update={"club_gone": _club_gone(career)})
                 if career.pending else None),
        summary=_summary(career) if career.summary else None,
        share=share_text(career) if career.summary else None)


def _moves_since_checkpoint(career: C.Career, moves: list[str]) -> list[str]:
    """The moves to keep beside the checkpoint: those that answered the season now pending
    (an offer awaiting its event), none once a season is played or the career is over."""
    return moves if career.pending is not None and career.pending.kind == "event" else []


# --- routes -----------------------------------------------------------------------------------


class RoleOut(BaseModel):
    key: str
    label: str


class OptionsOut(BaseModel):
    roles: list[RoleOut]
    first_debut: int
    last_debut: int


@router.get("/api/career/options")
def career_options() -> OptionsOut:
    return OptionsOut(roles=[RoleOut(key=k, label=v["label"]) for k, v in C.ROLES.items()],
                      first_debut=C.FIRST_DEBUT, last_debut=C.LAST_DEBUT)


class StartIn(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX)
    role: str
    debut: int
    overseas: bool = False
    seed: int | None = Field(default=None, ge=1, le=2 ** 31)


@router.post("/api/career/start")
def career_start(body: StartIn) -> CareerOut:
    name = " ".join(body.name.split())
    if not name or any(ch in name for ch in "<>") or any(ord(ch) < 32 for ch in name):
        raise HTTPException(400, "Choose a plain name.")
    setup = C.Setup(name, body.role, body.debut, body.overseas)
    try:
        setup.check()
    except C.CareerError as e:
        raise HTTPException(400, str(e)) from None
    seed = body.seed or random.SystemRandom().randint(1, 2 ** 31)
    deck, model = _state()
    career = C.play_career(deck, model, seed, setup, [], _facts())
    return _out(seed, career, [])


class MoveIn(BaseModel):
    token: str = Field(max_length=200_000)
    move: str = Field(max_length=8)


@router.post("/api/career/move")
def career_move(body: MoveIn) -> CareerOut:
    seed, setup, checkpoint, partial = read_token(body.token)
    moves = partial + [body.move]
    deck, model = _state()
    try:
        career = C.play_career(deck, model, seed, setup, moves, _facts(), resume=checkpoint)
    except C.CareerError as e:
        raise HTTPException(409, str(e)) from None
    return _out(seed, career, _moves_since_checkpoint(career, moves))


class ViewIn(BaseModel):
    token: str = Field(max_length=200_000)


@router.post("/api/career/view")
def career_view(body: ViewIn) -> CareerOut:
    """The career a token holds, unchanged -- how the page picks a career back up after a
    reload. Resumes with no moves, so it plays nothing."""
    seed, setup, checkpoint, partial = read_token(body.token)
    deck, model = _state()
    try:
        career = C.play_career(deck, model, seed, setup, partial, _facts(), resume=checkpoint)
    except C.CareerError as e:
        raise HTTPException(409, str(e)) from None
    return _out(seed, career, partial)
