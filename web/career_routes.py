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

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from game import career as C
from game import career_focus as CF, career_traits as ct, national, press
from etl.franchise_map import canonical
from game.auction import crore
from game.site import SITE_NAME, SITE_ORIGIN
from web import admin as admin_lib
from web import auth, bingo_routes, career_results, crests, db

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
                       "setup": [setup.name, setup.role, setup.debut, setup.overseas,
                                 setup.number, setup.home, setup.celebration,
                                 list(setup.ambitions), setup.country],
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
    return decode_payload(payload)


def decode_payload(payload: str) -> tuple[int, C.Setup, C.Checkpoint, list[str]]:
    """The career in a token's payload, with no signature check: for `read_token`, and for
    a tool that has already matched the payload to a saved career by its hash (A272)."""
    try:
        d = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if d.get("v") != TOKEN_VERSION:
            raise ValueError("old token")
        # Four fields before A228 (no identity, no ambitions), eight before A229 (no country).
        name, role, debut, overseas, *rest = d["setup"]
        number, home, celebration, ambitions, country = (rest + [None, "", "", [], ""][len(rest):])[:5]
        return (int(d["seed"]), C.Setup(name, role, int(debut), bool(overseas),
                                         None if number is None else int(number), str(home or ""),
                                         str(celebration or ""), tuple(str(a) for a in ambitions or ()),
                                         str(country or "")),
                C.Checkpoint.from_dict(d["cp"]), [str(m) for m in d["partial"]])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "That career could not be read.") from None


# --- what the page sees ---------------------------------------------------------------------


class TemplateOut(BaseModel):
    name: str
    year: int
    franchise: str
    crest: str | None
    card: int | None = None       # his real card that season: where your Rating starts or can peak (A214)


class HighlightOut(BaseModel):
    stage: str
    opponent: str
    text: str


class ClippingOut(BaseModel):
    """[A228] A newspaper clipping, as the page draws it. Plain text: the page escapes it."""
    key: str
    year: int
    section: str
    headline: str
    dek: str
    dateline: str
    body: list[str]
    quote: str = ""
    match: int | None = None
    crest: str | None = None
    tone: str = ""


def _clip(c: press.Clipping) -> ClippingOut:
    return ClippingOut(key=c.key, year=c.year, section=c.section, headline=c.headline, dek=c.dek,
                       dateline=c.dateline, body=c.body, quote=c.quote, match=c.match,
                       crest=crests.crest_url(c.franchise, c.year) if c.franchise else None,
                       tone=c.tone)


class SkillOut(BaseModel):
    """[A278] A skill a season earned, the way a real card shows one."""
    key: str
    label: str
    tier: int                     # 1 style, 2 special, 3 elite
    blurb: str
    seasons: int = 1              # seasons it was shown, on a career's list
    new: bool = False             # [A282] shown for the first time in this career


class MasteredOut(BaseModel):
    """[A285] A skill the career has mastered: shown in MASTER_SEASONS seasons, kept for good."""
    key: str
    label: str
    tier: int
    year: int | None = None       # the season it was mastered (None on a saved card)


def _mastered_out(rows) -> list[MasteredOut]:
    from etl.skills import SKILL_BY_KEY
    return [MasteredOut(key=k, label=SKILL_BY_KEY[k].label, tier=t, year=y)
            for k, t, y in rows if k in SKILL_BY_KEY]


class SlotOut(BaseModel):
    """[A282] One of the player card's three skill slots."""
    tier: int
    name: str                     # Style / Special / Elite
    opens_at: int                 # the Rating that opens it
    open: bool
    skill: SkillOut | None = None


def _skills_out(rows) -> list[SkillOut]:
    from etl.skills import SKILL_BY_KEY
    out = []
    for row in rows or []:
        sk = SKILL_BY_KEY.get(row[0])
        if sk is not None:
            out.append(SkillOut(key=sk.key, label=sk.label, tier=sk.tier, blurb=C.career_skills.blurb(sk.key),
                                seasons=row[2] if len(row) > 2 and isinstance(row[2], int) else 1))
    return out


class SeasonOut(BaseModel):
    year: int
    franchise: str
    short: str
    crest: str | None
    price: str
    stayed: bool
    squad: bool
    captain: bool
    contract_year: int            # 1-3 on a three-year deal, 0 on a one-year
    event: str
    choice: str
    card: int                     # the Season grade, after the career's pull (A267)
    form: int                     # the season's own grade
    rating: int | None = None     # your Rating that season (A214); None on an older token
    potential: int = 0            # your potential that season, as it stood going in (A224)
    age: int = 0                  # A214, shown only
    notes: list[str] = []         # what happened, in words (A214)
    note_tones: list[str] = []    # 'up' | 'down' | '' per note, for the page's marks (A218)
    matches: int
    missed: int
    dropped: int = 0              # of those, dropped for form (A205)
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
    five_fors: int = 0
    hat_tricks: int = 0
    milestones: list[str] = []    # A209
    skills: list[SkillOut] = []   # what the season card shows (A278)
    combos: list[str] = []        # [A282] combinations those skills make
    mega: str = ""                # how a mega-auction year went (A212)
    plan: list[str] = []          # "Name: Death", the plan the season began with (A213)
    poms: int = 0                 # Player of the Match awards (A228)
    national: str = ""            # what the selectors made of the season (A229)
    caps: int = 0                 # internationals in the window after it
    caps_total: int = 0
    press: list[ClippingOut] = [] # the season in the papers, the biggest story first (A228)
    league: list[str] = []        # real news from around the league that year (A228)
    role: str = ""                # the role this season was played in, when changed (A238)
    role_label: str = ""
    trial: bool = False           # a trial season the coach offered
    focus: FocusResultOut | None = None   # [A285] the season's focus and its challenge


class OfferOut(BaseModel):
    franchise: str
    short: str
    crest: str | None
    price: str                    # a one-year deal
    price3: str | None            # a year, on a three-year deal; None when not offered
    contract_year: int            # 2 or 3 when this is a deal already signed, else 0
    strength: int | None
    stay: bool
    position: int | None          # where you would bat in that eleven
    attack_rank: int | None       # where you would come in its attack, if you bowl
    strength_words: str = ""      # the squad in words (A230): the page shows no number


class MegaOut(BaseModel):
    """A mega-auction year's choice (A212): your club's retention offer, if it made one, or
    the auction at a base price of your choosing."""
    retain: OfferOut | None
    bases: list[str]              # the base prices, in crore/lakh words
    suggested: int                # index: the auction's own base for your card
    retain_card: int = C.RETAIN_CARD   # the card a club wants before it retains you


class AuctionBidOut(BaseModel):
    short: str
    crest: str | None
    price: str
    lakh: int = 0                 # the same price, for the auctioneer's voice (A218)


class AuctionOut(BaseModel):
    """The mega auction decided by THIS move, for the page to play (A212)."""
    retained: bool
    sold: bool
    base: str
    franchise: str
    short: str
    crest: str | None
    price: str
    bids: list[AuctionBidOut]
    price_lakh: int = 0           # A218, as `lakh` above


class PlanBowlerOut(BaseModel):
    """One of your side's bowlers, for a captain's plan (A213), with his real season's
    figures to plan by. `phase` is where the plan has him now: powerplay, middle, death or None."""
    name: str
    you: bool
    style: str | None
    wickets: int | None
    economy: float | None
    phase: str | None


class EventOptionOut(BaseModel):
    label: str
    hint: str                     # what it means, in a voice at the club -- never numbers (A230)


class EventOut(BaseModel):
    key: str
    title: str
    text: str
    options: list[EventOptionOut]


class MomentOptionOut(BaseModel):
    label: str
    hint: str


class MomentOut(BaseModel):
    """A key moment the season is paused at (A204): who, what is at stake, and the three
    approaches with what each does to your game."""
    stage: str
    opponent: str
    opp_short: str
    opp_crest: str | None
    stakes: str
    options: list[MomentOptionOut]
    kind: str = "must_win"        # "must_win" | "playoff" | "make_or_break" (A205)


class FocusOptionOut(BaseModel):
    """[A285] A focus you can choose: the skill it works toward, the lever it builds and the
    coach's challenge for the season. `key` "" is no focus."""
    key: str
    label: str
    skill: str = ""
    skill_label: str = ""
    tier: int = 0
    challenge: str = ""
    target: int = 0
    blurb: str = ""
    built: float = 0.0            # how far the lever is built already, 0-1
    current: bool = False         # the focus you worked on last season


class FocusResultOut(BaseModel):
    """[A285] How a season's focus went."""
    key: str
    label: str
    skill_label: str
    challenge: str
    met: int
    target: int
    done: bool
    switched: bool
    hits: list[bool] = []         # per match of yours, in the feed's order


class PendingOut(BaseModel):
    kind: str                     # "offer" | "event" | "moment"
    year: int
    season_no: int                # 1-based
    squad: bool
    offers: list[OfferOut]
    event: EventOut | None = None
    can_retire: bool
    # Your last club, when it has no team this year (Chennai and Rajasthan in 2016-17, Deccan
    # after 2012): why there is no offer to stay.
    club_gone: str | None = None
    moment: MomentOut | None = None
    mega: MegaOut | None = None
    plan: list[PlanBowlerOut] | None = None    # a captain's side: set it, or change it here (A213)
    epilogue: list[str] = []                    # a finished career: where next? `w{i}` (A228)
    incident: EventOut | None = None            # something happened mid-season: `i{n}` (A230)
    cold: bool = False                          # your club has not asked you to stay (A230)
    coach: EventOut | None = None               # the coach's offer: `t{i}` (A238)
    focus: list[FocusOptionOut] | None = None   # what to work on this season: `f{i}` (A285)


class MilestoneOut(BaseModel):
    year: int
    text: str
    key: str = ""                 # "title", "orange_cap", ... for the timeline's colour (A219)


class SummaryOut(BaseModel):
    mastered: list[MasteredOut] = []   # [A285] skills the career has mastered
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
    bowling_average: float | None = None      # runs a wicket (A220)
    bowling_strike_rate: float | None = None  # balls a wicket (A220)
    best_bowling: str | None = None           # the career's best figures, "4/18" (A220)
    titles: int
    finals: int
    orange_caps: int
    purple_caps: int
    captaincies: int
    franchises: list[str]
    earnings: str
    peak_card: int
    peak_year: int
    peak_rating: int | None = None    # A214
    peak_rating_year: int | None = None
    runs_rank: int | None
    wickets_rank: int | None
    ending: str                   # "retired" | "released" | "the end of the archive"
    five_fors: int = 0
    hat_tricks: int = 0
    milestones: list[MilestoneOut] = []       # A209, the career's in order
    skills: list[SkillOut] = []               # A278, every skill a season showed
    poms: int = 0                             # A228
    farewell: ClippingOut | None = None       # the papers on the career's end (A228)
    epilogue: ClippingOut | None = None       # where the player went after the game (A228)


class MatchLineOut(BaseModel):
    stage: str
    opponent: str
    opp_short: str
    opp_crest: str | None
    result: str                   # "W" | "L" | "T"
    score_for: str
    score_against: str
    margin: str
    played: bool
    batted: bool
    runs: int
    balls: int
    out: bool
    wickets: int
    bowl_runs: int
    bowl_balls: int
    wins: int
    losses: int
    position: int | None
    season_runs: int
    season_wickets: int
    orange: str                   # leader's name
    orange_n: int
    orange_you: bool
    purple: str
    purple_n: int
    purple_you: bool
    watch: int | None             # index into `watch`
    approach: str | None = None   # your call, when this was a key moment (A204)
    stop: str | None = None       # what kind of key moment it was (A205)
    verdict: str | None = None    # a make-or-break's outcome: "kept" | "dropped"
    dropped: bool = False         # left out for form
    form: list[str] = []          # your last five matches, oldest first
    form_word: str | None = None  # In form / Steady / Out of form
    card: int | None = None       # index into `cards`: this match's scorecard (A214)
    absence: str | None = None    # why you missed this match (A216)
    incident: str | None = None   # what happened before this match, and your answer (A230)
    cover: str | None = None      # a teammate broke down and you bowled his overs (A247)
    milestones: list[str] = []    # career milestones reached in this match (A209)
    pom: bool = False             # Player of the Match (A228)
    challenge: bool = False       # [A285] this match met your focus's challenge


class TableRowOut(BaseModel):
    """The solo season's points-table row (A153), so the page draws both with one table."""
    pos: int
    short: str
    name: str
    crest: str | None
    played: int
    won: int
    lost: int
    tied: int = 0
    points: int
    nrr: float
    runs_for: int = 0
    overs_for: str = "0.0"
    runs_against: int = 0
    overs_against: str = "0.0"
    form: list[str] = []
    you: bool


class SeasonPlayOut(BaseModel):
    """The season just played, as it happened (A202). Only in the response that played it --
    or, when it is paused at a key moment (A204), the season so far (`complete` false)."""
    feed: list[MatchLineOut]
    table: list[TableRowOut]
    watch: list[dict]             # ResultOut, the solo season's own match shape, for reveal.js
    cards: list[dict] = []        # every match of yours as a scorecard: ResultOut without the over log (A214)
    your_short: str
    your_name: str
    your_crest: str | None
    complete: bool = True
    analysis: dict | None = None  # AnalysisOut, the solo season's own shape, for analysis.js (A211)
    press: list[ClippingOut] = [] # the clippings this season's matches made, each on its match (A228)


class AmbitionOut(BaseModel):
    key: str
    label: str
    state: str                    # "done" | "open" | "broken"
    year: int | None = None


class CareerOut(BaseModel):
    token: str
    name: str
    role: str                     # the role you were created in
    role_label: str
    role_now: str = ""            # the role the coach has you in now (A238)
    role_now_label: str = ""
    season_role: str = ""         # the role the season pending is played in (a trial's)
    role_path: list[str] = []     # "Opening batter", "All-rounder from 2014"
    debut: int
    overseas: bool
    start: TemplateOut
    ceiling: TemplateOut
    seasons: list[SeasonOut]
    pending: PendingOut | None
    summary: SummaryOut | None
    share: str | None
    season_play: SeasonPlayOut | None = None
    auction: AuctionOut | None = None
    rating: int = 0               # your Rating now: going into the season pending, or the last (A214)
    potential: int = 0            # the most your Rating can reach now: the ceiling, plus what you earned (A224)
    potential_start: int = 0      # the ceiling's own card, where the potential began
    age: int = 0
    injuries: list[str] = []      # "2014 · Side strain (5 matches)"
    number: int | None = None     # A228: who you are, for the papers
    home: str = ""
    celebration: str = ""
    ambitions: list[AmbitionOut] = []
    country: str = ""             # who you can play for: India, the chosen country, or "" (A229)
    caps: int = 0
    in_side: bool = False
    standing: str = ""            # how your club sees you, in words; "" when neutral (A230)
    traits: list[str] = []        # who your player has become, in words, never numbers (A245)
    skill_slots: list[SlotOut] = []   # [A282] the last season's skills, by tier
    combo_hint: str = ""          # [A282] the nearest combination, one skill away
    combos_now: list[str] = []    # [A282] combinations the last season's skills make
    mastered: list[MasteredOut] = []  # [A285] skills mastered so far
    focus_now: str = ""           # [A285] what you are working on, in words
    master_seasons: int = 4       # [A285] seasons a skill must be shown in to be mastered


def _template(c) -> TemplateOut:
    return TemplateOut(name=c.name, year=c.season_year, franchise=c.franchise,
                       crest=crests.crest_url(c.franchise, c.season_year), card=c.display)


def _season(r: C.SeasonRecord, ceiling: int = 0, voice: press.Voice | None = None) -> SeasonOut:
    deck, _ = _state()
    return SeasonOut(
        poms=r.poms, national=r.national, caps=r.caps, caps_total=r.caps_total,
        press=[_clip(c) for c in press.edition(r.press, voice)] if voice else [],
        league=press.league_news(r.year, C.clubs_by_year(deck), C.MEGA_YEARS),
        potential=round(ceiling + r.potential),
        year=r.year, franchise=r.franchise, short=r.short,
        crest=crests.crest_url(r.franchise, r.year), price=crore(r.price), stayed=r.stayed,
        squad=r.squad, captain=r.captain, contract_year=r.contract_year, event=r.event,
        choice=r.choice, card=r.display, rating=r.rating or None, age=C.DEBUT_AGE + r.k,
        notes=list(r.notes),
        note_tones=(list(r.tones) if len(r.tones) == len(r.notes) else [C.note_tone(t) for t in r.notes]),
        form=r.form, matches=r.matches, missed=r.missed, dropped=r.dropped, runs=r.runs, balls=r.balls,
        outs=r.outs, highest=r.highest, fifties=r.fifties, hundreds=r.hundreds,
        wickets=r.wickets, bowl_balls=r.bowl_balls, bowl_runs=r.bowl_runs,
        best=f"{r.best[0]}/{r.best[1]}" if r.bowl_balls else None, position=r.position,
        teams=r.teams, playoffs=r.playoffs, finalist=r.finalist, champion=r.champion,
        orange_cap=r.orange_cap, purple_cap=r.purple_cap,
        highlights=[HighlightOut(stage=h.stage, opponent=h.opponent, text=h.text)
                    for h in r.highlights],
        five_fors=r.five_fors, hat_tricks=r.hat_tricks,
        milestones=[m.text for m in r.milestones], mega=r.mega,
        skills=_skills_out([[k, t, 1] for k, t, _st in r.skills]),
        combos=C.career_skills.combos(k for k, *_ in r.skills),
        plan=[f"{n}: {PHASE_WORDS[ph]}" for n, ph in r.plan],
        role=r.role["role"] if r.role else "",
        role_label=C.role_label(r.role["role"]) if r.role else "",
        trial=bool(r.role and r.role["trial"]),
        focus=_focus_result(r.focus))


def _focus_hits(career: C.Career) -> list[bool] | None:
    """[A285] The season just played's challenge, match by match -- only once the season is
    complete, since a paused season's record does not exist yet."""
    d = career.detail
    if d is None or not d.complete or not career.seasons:
        return None
    f = career.seasons[-1].focus
    return list(f["hits"]) if f else None


def _focus_result(f: dict | None) -> FocusResultOut | None:
    if not f:
        return None
    fo = CF.FOCUS_BY_KEY[f["focus"]]
    return FocusResultOut(key=fo.key, label=fo.label, skill_label=C.career_skills.label(fo.skill),
                          challenge=fo.challenge, met=f["met"], target=f["target"],
                          done=f["done"], switched=f["switched"], hits=list(f.get("hits") or []))


def _focus_options(p: C.Pending, cp) -> list[FocusOptionOut]:
    out = []
    for key in p.focus["options"]:
        if not key:
            out.append(FocusOptionOut(key="", label="No particular focus",
                                      blurb="Play your game. Nothing is built this season."))
            continue
        fo = CF.FOCUS_BY_KEY[key]
        sk = C.career_skills.S.SKILL_BY_KEY[fo.skill]
        out.append(FocusOptionOut(
            key=key, label=fo.label, skill=fo.skill, skill_label=sk.label, tier=sk.tier,
            challenge=fo.challenge, target=fo.target, blurb=fo.blurb,
            built=round(C.focus_built(key, cp), 3) if cp is not None else 0.0,
            current=key == p.focus.get("current")))
    return out


def role_path(c: C.Career) -> list[str]:
    """The jobs a career has had, in order [A238]: "Opening batter", "All-rounder from 2014".
    Trial seasons are not a change of job."""
    out, now = [C.ROLES[c.setup.role]["label"]], c.setup.role
    for r in c.seasons:
        role = r.role["role"] if r.role and not r.role["trial"] else c.setup.role
        if role != now:
            out.append(f"{C.role_label(role)} from {r.year}")
            now = role
    return out


def strength_words(strength: int | None) -> str:
    """A squad's strength in words (A230): the mean card of its best twelve, without you.
    Measured over all 166 real squads: 77 to 84, median 80, top tenth 82."""
    if strength is None:
        return ""
    return ("Title contenders" if strength >= 82 else "A strong side" if strength >= 81
            else "A mid-table side" if strength >= 80 else "A side rebuilding")


def _club_gone(career: C.Career) -> str | None:
    p = career.pending
    if p is None or p.kind != "offer" or not career.seasons:
        return None
    last = career.seasons[-1].franchise
    deck, _ = _state()
    playing = {canonical(deck.cards_by_fs[fs][0].franchise) for fs in C.year_fs_ids(deck, p.year)}
    return last if canonical(last) not in playing else None


PHASE_WORDS = {"powerplay": "Powerplay", "middle": "Middle overs", "death": "Death"}

# [A230] What each call means, said the way a teammate would say it: never in numbers.
APPROACH_HINTS = ("Bat time. Let the others take the risks.", "Play your natural game.",
                  "Take the bowlers on from ball one.")
BOWLING_HINTS = ("Hit your lengths and dry up the runs.", "Bowl your usual stuff.",
                 "Attack the stumps. Wickets win this.")
ALLROUNDER_HINTS = ("Keep it tight, with bat and ball.", "Play your natural game.",
                    "Go hard at everything.")


def _moment(m: C.Moment, role: str) -> MomentOut:
    hints = (BOWLING_HINTS if role in ("pace", "spin") else
             ALLROUNDER_HINTS if C.role_kind(role) == "allrounder" else APPROACH_HINTS)
    return MomentOut(stage=m.stage, opponent=m.opponent, opp_short=m.opp_short,
                     opp_crest=crests.crest_url(m.opp_franchise, m.opp_year), stakes=m.stakes,
                     options=[MomentOptionOut(label=l, hint=h) for l, h in zip(m.labels, hints)],
                     kind=m.kind)


def _pending(p: C.Pending, role: str = "", cp=None) -> PendingOut:
    event = incident = None
    if p.kind == "event":
        event = EventOut(key=p.event.key, title=p.event.title, text=p.event_text or p.event.text,
                         options=[EventOptionOut(label=C.fill(o.label, *p.event_fill),
                                                 hint=C.fill(o.hint, *p.event_fill))
                                  for o in p.event.options])
    coach = None
    if p.kind == "coach" and p.coach:
        coach = EventOut(key=p.coach["key"], title=p.coach["title"], text=p.coach["text"],
                         options=[EventOptionOut(label=o["label"], hint=o["hint"])
                                  for o in p.coach["options"]])
    if p.kind == "incident" and p.incident:
        incident = EventOut(key=p.incident["key"], title=p.incident["title"],
                            text=p.incident["text"],
                            options=[EventOptionOut(label=o.label, hint=o.hint)
                                     for o in p.incident["options"]])
    plan = [PlanBowlerOut(name=c.name, you=c.person_id == C.YOU_ID, style=c.bowling_style,
                          wickets=c.bowl_wickets, economy=c.economy,
                          phase=p.plan.get(c.person_id)) for c in p.plan_bowlers] or None
    mega = None
    if p.kind == "mega" and p.mega is not None:
        mega = MegaOut(retain=_offer(p.mega.retain, p.year) if p.mega.retain else None,
                       bases=[crore(b) for b in p.mega.bases], suggested=p.mega.suggested)
    return PendingOut(
        kind=p.kind, year=p.year, season_no=p.k + 1, squad=p.squad,
        offers=[_offer(o, p.year) for o in p.offers],
        event=event, can_retire=p.kind in ("offer", "mega") and p.k > 0,
        moment=_moment(p.moment, role) if p.kind == "moment" and p.moment else None,
        mega=mega, plan=plan, incident=incident, cold=p.cold, coach=coach,
        focus=_focus_options(p, cp) if p.kind == "focus" and p.focus else None,
        epilogue=list(press.EPILOGUES.values()) if p.kind == "epilogue" else [])


def _offer(o: C.Offer, year: int) -> OfferOut:
    return OfferOut(franchise=o.franchise, short=o.short,
                    crest=crests.crest_url(o.franchise, year), price=crore(o.price),
                    price3=(crore(o.price3) if o.price3 is not None and not o.contract_year
                            else None),
                    contract_year=o.contract_year, strength=o.strength, stay=o.stay,
                    position=o.position, attack_rank=o.attack_rank,
                    strength_words=strength_words(o.strength))


def _auction(a: C.MegaResult, year: int) -> AuctionOut:
    return AuctionOut(retained=a.retained, sold=a.sold, base=crore(a.base) if a.base else "",
                      franchise=a.franchise, short=a.short,
                      crest=crests.crest_url(a.franchise, year), price=crore(a.price),
                      price_lakh=a.price,
                      bids=[AuctionBidOut(short=sh, crest=crests.crest_url(f, year), price=crore(pr),
                                          lakh=pr)
                            for f, sh, pr in a.bids])


def _best_bowling(seasons) -> str | None:
    """The career's best figures: the most wickets in a match, the fewest runs breaking a tie
    -- the scorecard convention, read off each season's own best."""
    bests = [r.best for r in seasons if r.bowl_balls]
    if not bests:
        return None
    w, runs = max(bests, key=lambda b: (b[0], -b[1]))
    return f"{w}/{runs}"


def voice(seed: int, st: C.Setup) -> press.Voice:
    return press.Voice(st.name, st.role, seed, st.number, st.home, st.celebration)


def main_club(seasons) -> str | None:
    """The club a career is remembered at: the most seasons, the latest name it played under."""
    if not seasons:
        return None
    n: dict[str, int] = {}
    for r in seasons:
        n[canonical(r.franchise)] = n.get(canonical(r.franchise), 0) + 1
    best = max(n, key=lambda k: (n[k], max(r.year for r in seasons if canonical(r.franchise) == k)))
    return next(r.franchise for r in reversed(seasons) if canonical(r.franchise) == best)


def _ending_clips(c: C.Career) -> tuple[press.Clipping, press.Clipping | None]:
    s, v = c.summary, voice(c.seed, c.setup)
    ending = "retired" if c.retired else "released" if c.released else "archive"
    club = main_club(c.seasons)
    last = c.seasons[-1].year
    fare = press.farewell(v, ending, s.seasons, s.runs, s.wickets, s.titles, club, last,
                          s.runs_rank, s.wickets_rank)
    epi = press.epilogue(s.epilogue, v, club, last) if s.epilogue in press.EPILOGUES else None
    return fare, epi


def _summary(c: C.Career) -> SummaryOut:
    s = c.summary
    ending = "retired" if c.retired else "released" if c.released else "the end of the archive"
    fare, epi = _ending_clips(c)
    return SummaryOut(
        poms=s.poms, farewell=_clip(fare), epilogue=_clip(epi) if epi else None,
        seasons=s.seasons, regular_seasons=s.regular_seasons, matches=s.matches, runs=s.runs,
        strike_rate=round(100 * s.runs / s.balls, 1) if s.balls else None,
        average=round(s.runs / s.outs, 1) if s.outs else None, highest=s.highest,
        fifties=s.fifties, hundreds=s.hundreds, wickets=s.wickets,
        economy=round(6 * s.bowl_runs / s.bowl_balls, 2) if s.bowl_balls else None,
        bowling_average=round(s.bowl_runs / s.wickets, 1) if s.wickets else None,
        bowling_strike_rate=round(s.bowl_balls / s.wickets, 1) if s.wickets else None,
        best_bowling=_best_bowling(c.seasons),
        titles=s.titles, finals=s.finals, orange_caps=s.orange_caps,
        purple_caps=s.purple_caps, captaincies=s.captaincies, franchises=s.franchises,
        earnings=crore(s.earnings), peak_card=s.peak_display, peak_year=s.peak_year,
        peak_rating=s.peak_rating or None, peak_rating_year=s.peak_rating_year or None,
        runs_rank=s.runs_rank, wickets_rank=s.wickets_rank, ending=ending,
        five_fors=s.five_fors, hat_tricks=s.hat_tricks,
        # From the seasons rather than `s.milestones`, which keeps (year, text) pairs for the
        # saved card; the same walk in the same order, with each milestone's kind kept.
        milestones=[MilestoneOut(year=r.year, text=m.text, key=m.key)
                    for r in c.seasons for m in r.milestones],
        skills=_skills_out(C.career_skills.signature(c.seasons)),
        mastered=_mastered_out(C.career_skills.mastered(c.seasons)))


def _season_play(detail: C.SeasonDetail,
                 milestones: list[C.CareerMilestone] = (),
                 clips: list[press.Clipping] = (),
                 hits: list[bool] | None = None) -> SeasonPlayOut:
    from web.app import _analysis_out, _result_out      # the solo season's own shapes

    yours = next((side for r in detail.watch for side in (r.home, r.away) if side.you), None)
    you_row = next(t for t in detail.table if t.you)
    feed = [MatchLineOut(
        stage=l.stage, opponent=l.opponent, opp_short=l.opp_short,
        opp_crest=crests.crest_url(l.opp_franchise, l.opp_year), result=l.result,
        score_for=l.score_for, score_against=l.score_against, margin=l.margin, played=l.played,
        batted=l.batted, runs=l.runs, balls=l.balls, out=l.out, wickets=l.wickets,
        bowl_runs=l.bowl_runs, bowl_balls=l.bowl_balls, wins=l.wins, losses=l.losses,
        position=l.position, season_runs=l.season_runs, season_wickets=l.season_wickets,
        orange=l.orange[0], orange_n=l.orange[1], orange_you=l.orange[2],
        purple=l.purple[0], purple_n=l.purple[1], purple_you=l.purple[2], watch=l.watch,
        approach=l.approach, stop=l.stop, verdict=l.verdict, dropped=l.dropped, form=l.form,
        form_word=l.form_word, milestones=[m.text for m in milestones if m.match == i], pom=l.pom,
        card=l.card, absence=l.absence, incident=l.incident,
        cover=l.cover, challenge=bool(hits and len(hits) == len(detail.feed) and hits[i]))
        for i, l in enumerate(detail.feed)]
    table = [TableRowOut(pos=i, short=t.short, name=t.name,
                         crest=crests.crest_url(t.franchise, t.year), played=t.played,
                         won=t.won, lost=t.lost, tied=t.tied, points=t.points, nrr=t.nrr,
                         runs_for=t.runs_for, overs_for=_overs(t.balls_for),
                         runs_against=t.runs_against, overs_against=_overs(t.balls_against),
                         form=t.form, you=t.you) for i, t in enumerate(detail.table, 1)]
    watch = [_mark_you(r, _result_out(r, yours).model_dump()) for r in detail.watch]
    cards = [_scorecard(_mark_you(r, _result_out(r, yours).model_dump())) for r in detail.cards]
    return SeasonPlayOut(feed=feed, table=table, watch=watch, cards=cards, your_short=you_row.short,
                         your_name=you_row.name,
                         your_crest=next(x.crest for x in table if x.you),
                         complete=detail.complete,
                         press=[_clip(c) for c in clips if c.match is not None],
                         analysis=(_analysis_out(detail.analysis, detail.results).model_dump()
                                   if detail.analysis is not None else None))


def _mark_you(result, r: dict) -> dict:
    """Mark your own player in a serialised match: `you` on his batting and bowling rows
    and on his crease entries, `bowler_you` on the overs he bowled -- so a scorecard and the
    ball-by-ball board can pick him out. Matched on person id (never the name, which is the
    player's own free text and could equal a real one), and done here rather than in the
    shared serialisers so no other mode's payload grows a field that is always false. The
    lists line up one to one with `web.app._innings_out`'s, including its rule that a
    bowler who never got a ball has no row."""
    for key, inn in (("home_innings", result.home_innings), ("away_innings", result.away_innings)):
        out = r.get(key)
        if not out or inn is None:
            continue
        for row, b in zip(out["batting"], inn.batting):
            row["you"] = b.player.person_id == C.YOU_ID
        for row, bo in zip(out["bowling"], [bo for bo in inn.bowling if bo.balls > 0]):
            row["you"] = bo.player.person_id == C.YOU_ID
        for row, o in zip(out["over_log"], inn.over_log):
            row["bowler_you"] = o.bowler_id == C.YOU_ID
            for end in ("striker", "non_striker"):
                snap = getattr(o, end)
                if row.get(end) and snap is not None:
                    row[end]["you"] = snap.person_id == C.YOU_ID
    return r


def _overs(balls: int) -> str:
    return f"{balls // 6}.{balls % 6}"


def _scorecard(r: dict) -> dict:
    """A result as a scorecard alone: the over log is the reveal's, and is most of the size."""
    for key in ("home_innings", "away_innings"):
        if r.get(key):
            r[key] = {**r[key], "over_log": [], "milestones": []}
    for so in r.get("super_overs") or []:
        for key in ("first_innings", "second_innings"):
            if so.get(key):
                so[key] = {**so[key], "over_log": [], "milestones": []}
    return r


def share_text(c: C.Career) -> str:
    """One line per career, no player names: what happened, not who it was built from
    (the spin is the surprise for whoever plays next, as A129 keeps a daily's answer back)."""
    s = c.summary
    role = " → ".join(x.split(" from ")[0] for x in role_path(c))
    marks = "".join("🏆" if r.champion else "🟧" if r.orange_cap else "🟪" if r.purple_cap
                    else "⬜" if r.squad else "🟩" for r in c.seasons)
    bits = [f"{s.seasons} seasons"]
    if s.runs:
        bits.append(f"{s.runs:,} runs")
    if s.wickets:
        bits.append(f"{s.wickets} wickets")
    if s.titles:
        bits.append(f"{s.titles} title{'s' if s.titles != 1 else ''}")
    bits.append(f"peak Rating {s.peak_rating}" if s.peak_rating else f"peak card {s.peak_display}")
    return (f"{SITE_NAME} · Build a Career\n{role}, debut {c.setup.debut}: "
            f"{', '.join(bits)}\n{marks}\n{SITE_ORIGIN}/career")


def _live_clips(seed: int, career: C.Career) -> list[press.Clipping]:
    """The clippings of the season told in this response, each on its match: the played
    season's own edition, or, for a season paused at a key moment, its matches so far."""
    v, d = voice(seed, career.setup), career.detail
    if d is None:
        return []
    if d.complete and career.seasons:
        return press.edition(career.seasons[-1].press, v)
    club = career.pending.offers[0].franchise if career.pending and career.pending.offers else ""
    year = career.pending.year if career.pending else 0
    return press.edition(press.match_facts(d.feed, year, club), v)


def _ambitions(career: C.Career) -> list[AmbitionOut]:
    out = []
    for key in career.setup.ambitions:
        state, year = C.ambition_status(key, career.seasons)
        out.append(AmbitionOut(key=key, label=C.AMBITIONS[key][0], state=state, year=year))
    return out


def _skill_view(career) -> dict:
    """[A282] The player card's slots and combination hint, from the last season played."""
    CS = C.career_skills
    last = career.seasons[-1] if career.seasons else None
    shown = list(last.skills) if last else []
    by_key = {s.key: s for s in _skills_out(shown)}
    slots = [SlotOut(tier=x["tier"], name=x["name"], opens_at=x["opens_at"], open=x["open"],
                     skill=by_key.get(x["key"])) for x in CS.slots(shown, _rating_now(career))]
    role = career.role_now or career.setup.role
    card_role = C.ROLES.get(role, {}).get("role", ("allrounder",))[0]   # a full-time bowler bowls
    cp = career.checkpoint
    fk = cp.focus if cp is not None else ""
    return {"mastered": _mastered_out(CS.mastered(career.seasons)),
            "master_seasons": CS.MASTER_SEASONS,
            "focus_now": CF.FOCUS_BY_KEY[fk].label if fk in CF.FOCUS_BY_KEY else "",
            "skill_slots": slots, "combos_now": CS.combos(k for k, *_ in shown),
            "combo_hint": CS.combo_hint([k for k, *_ in shown], card_role) if last else ""}


def _mark_new(seasons: list[SeasonOut]) -> list[SeasonOut]:
    """[A282] A skill is new the first season it is shown."""
    seen: set[str] = set()
    for s in seasons:
        for k in s.skills:
            k.new = k.key not in seen
            seen.add(k.key)
    return seasons


def _out(seed: int, career: C.Career, partial: list[str]) -> CareerOut:
    st = career.setup
    v = voice(seed, st)
    return CareerOut(
        number=st.number, home=st.home, celebration=st.celebration, ambitions=_ambitions(career),
        country=st.nation,
        caps=int((career.checkpoint.national if career.checkpoint else {}).get("caps", 0)),
        in_side=bool((career.checkpoint.national if career.checkpoint else {}).get("in_side")),
        token=make_token(seed, st, career, partial), name=st.name, role=st.role,
        role_label=C.ROLES[st.role]["label"], debut=st.debut, overseas=st.overseas,
        role_now=career.role_now or st.role,
        role_now_label=C.role_label(career.role_now or st.role),
        season_role=career.season_role or career.role_now or st.role, role_path=role_path(career),
        start=_template(career.start), ceiling=_template(career.ceiling),
        seasons=_mark_new([_season(r, career.ceiling.display, v) for r in career.seasons]),
        pending=(_pending(career.pending, career.season_role or st.role,
                          career.checkpoint).model_copy(update={"club_gone": _club_gone(career)})
                 if career.pending else None),
        summary=_summary(career) if career.summary else None,
        share=share_text(career) if career.summary else None,
        season_play=(_season_play(career.detail, career.live_milestones, _live_clips(seed, career),
                                  _focus_hits(career))
                     if career.detail else None),
        auction=_auction(career.auction, _auction_year(career)) if career.auction else None,
        rating=_rating_now(career), potential=round(career.top.display),
        potential_start=career.ceiling.display,
        age=C.DEBUT_AGE + (career.pending.k if career.pending else len(career.seasons) - 1),
        injuries=[f"{y} · {t}" for y, t in (career.checkpoint.injuries if career.checkpoint else [])],
        standing=(C.STANDING_WORDS.get(career.checkpoint.standing, "")
                  if career.checkpoint and not career.summary else ""),
        traits=_trait_words(career), **_skill_view(career))


def _trait_words(career: C.Career) -> list[str]:
    """Who your player has become (A245): the checkpoint's traits, in words."""
    if career.checkpoint is None:
        return []
    role = career.role_now or career.setup.role
    return (ct.words(career.checkpoint.traits, bowler=C.role_kind(role) == "bowler")
            + career.phase_words)                                   # [A248]


def _rating_now(career: C.Career) -> int:
    """Your Rating going into the season pending (before its event), or the last season's."""
    if (career.pending is None or career.checkpoint is None
            or career.pending.kind == "epilogue"):
        return career.seasons[-1].rating if career.seasons else 0
    cp = career.checkpoint
    return C.rating_at(career.start, career.top,
                       C.career_t(cp, career.start, career.top, career.shape))


def _auction_year(career: C.Career) -> int:
    """The year the auction just decided was for: the season pending, or the one just played."""
    return career.pending.year if career.pending else career.seasons[-1].year


def _moves_since_checkpoint(career: C.Career, moves: list[str]) -> list[str]:
    """The moves to keep beside the checkpoint: those that answered the season now pending
    (an offer awaiting its event, a season paused at a key moment), none once a season is
    played or the career is over."""
    return (moves if career.pending is not None
            and career.pending.kind in ("event", "plan", "moment", "incident", "coach",
                                        "focus")
            else [])


# --- routes -----------------------------------------------------------------------------------


class RoleOut(BaseModel):
    key: str
    label: str
    blurb: str = ""


class ChoiceOut(BaseModel):
    key: str
    label: str


class OptionsOut(BaseModel):
    roles: list[RoleOut]
    first_debut: int
    last_debut: int
    celebrations: list[ChoiceOut] = []       # A228
    ambitions: list[ChoiceOut] = []
    ambitions_max: int = C.AMBITIONS_MAX
    nations: list[str] = list(national.OVERSEAS_NATIONS)     # A229: an overseas player's choices
    home_max: int = C.HOME_MAX
    paper: str = press.PAPER


@router.get("/api/career/options")
def career_options() -> OptionsOut:
    return OptionsOut(roles=[RoleOut(key=k, label=v["label"], blurb=v["blurb"]) for k, v in C.ROLES.items()],
                      first_debut=C.FIRST_DEBUT, last_debut=C.LAST_DEBUT,
                      celebrations=[ChoiceOut(key=k, label=v[0]) for k, v in press.CELEBRATIONS.items()],
                      ambitions=[ChoiceOut(key=k, label=v[0]) for k, v in C.AMBITIONS.items()])


class StartIn(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX)
    role: str
    debut: int
    overseas: bool = False
    seed: int | None = Field(default=None, ge=1, le=2 ** 31)
    number: int | None = Field(default=None, ge=0, le=99)      # A228
    home: str = Field(default="", max_length=C.HOME_MAX)
    celebration: str = ""
    ambitions: list[str] = Field(default=[], max_length=C.AMBITIONS_MAX)
    country: str = ""                                          # A229, overseas only


def seed_allowed(request: Request) -> bool:
    """[A235] Choosing a career's seed is for admins: a seed fixes the two spins and the
    career's shape, so picking one picks a known-great career -- and a saved career's best
    season can be brought into a friends' auction (A215). Everybody else gets a random one."""
    token = request.cookies.get(auth.COOKIE_NAME)
    return admin_lib.is_admin(auth.verify_session_cookie(token) if token is not None else None)


@router.post("/api/career/start")
def career_start(body: StartIn, request: Request) -> CareerOut:
    name = " ".join(body.name.split())
    if not name or any(ch in name for ch in "<>") or any(ord(ch) < 32 for ch in name):
        raise HTTPException(400, "Choose a plain name.")
    home = " ".join(body.home.split())
    if any(ch in home for ch in "<>") or any(ord(ch) < 32 for ch in home):
        raise HTTPException(400, "Choose a plain home town.")
    setup = C.Setup(name, body.role, body.debut, body.overseas, body.number, home,
                    body.celebration, tuple(body.ambitions), body.country)
    try:
        setup.check()
    except C.CareerError as e:
        raise HTTPException(400, str(e)) from None
    if body.seed is not None and not seed_allowed(request):
        raise HTTPException(403, "Choosing a seed is for admins.")
    seed = body.seed or random.SystemRandom().randint(1, 2 ** 31)
    deck, model = _state()
    career = C.play_career(deck, model, seed, setup, [], _facts())
    return _out(seed, career, [])


class RerollIn(BaseModel):
    token: str = Field(max_length=200_000)


@router.post("/api/career/reroll")
def career_reroll(body: RerollIn, request: Request, response: Response) -> CareerOut:
    """[A227] An admin redraws both spins of a career that has not begun: the same player
    (name, role, debut, passport) on a new seed, so a new start, a new ceiling and a new
    career shape. Only before the first offer is answered -- nothing played can change.

    404 to anybody who is not an admin, as every admin power answers (A154). A new seed is
    no more than starting the same career again, which anybody can do; the route is a
    shortcut, not a privilege over the game, and a non-admin is simply not offered it."""
    response.headers["Cache-Control"] = "no-store"
    account_id = _account(request)
    if not admin_lib.is_admin(account_id):
        raise HTTPException(status_code=404, detail="not found")
    seed, setup, checkpoint, partial = read_token(body.token)
    if checkpoint.k or checkpoint.seasons or partial:
        raise HTTPException(409, "Only a career that has not begun can be rerolled.")
    rng = random.SystemRandom()
    new = rng.randint(1, 2 ** 31)
    while new == seed:
        new = rng.randint(1, 2 ** 31)
    return career_start(StartIn(name=setup.name, role=setup.role, debut=setup.debut,
                                overseas=setup.overseas, seed=new, number=setup.number,
                                home=setup.home, celebration=setup.celebration,
                                ambitions=list(setup.ambitions), country=setup.country), request)


class MoveIn(BaseModel):
    token: str = Field(max_length=200_000)
    move: str = Field(max_length=16)          # "m1p012303": a call with a new plan (A213)


def _stale(deck, model, seed, setup, checkpoint, partial) -> C.Career | None:
    """[A214] A season's answers so far that no longer replay -- a token from before an
    engine change, mid-season (an event that asked a question may now ask none). The
    checkpoint is a completed season and always replays, so the career resumes from it
    and only this season's choices are asked again. None when the answers still replay.
    Seen on production minutes after A214 went live: three such moves refused with 409,
    and the page took a refused reload for an unreadable career and dropped it."""
    if not partial:
        return None
    try:
        C.play_career(deck, model, seed, setup, partial, _facts(), resume=checkpoint)
        return None
    except C.CareerError:
        return C.play_career(deck, model, seed, setup, [], _facts(), resume=checkpoint)


@router.post("/api/career/move")
def career_move(body: MoveIn) -> CareerOut:
    seed, setup, checkpoint, partial = read_token(body.token)
    moves = partial + [body.move]
    deck, model = _state()
    try:
        career = C.play_career(deck, model, seed, setup, moves, _facts(), resume=checkpoint)
    except C.CareerError as e:
        restarted = _stale(deck, model, seed, setup, checkpoint, partial)
        if restarted is not None:
            return _out(seed, restarted, [])
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
        restarted = _stale(deck, model, seed, setup, checkpoint, partial)
        if restarted is not None:
            return _out(seed, restarted, [])
        raise HTTPException(409, str(e)) from None
    return _out(seed, career, partial)


# --- saving, and the Hall of Fame [A210] ------------------------------------------------------


def _account(request: Request) -> int:
    token = request.cookies.get(auth.COOKIE_NAME)
    account_id = auth.verify_session_cookie(token) if token is not None else None
    if account_id is None:
        raise HTTPException(401, "Sign in to keep your careers.")
    return account_id


class SaveIn(BaseModel):
    token: str = Field(max_length=200_000)


class SaveOut(BaseModel):
    saved: bool                   # newly saved by this call
    careers: int                  # how many this account now holds


@router.post("/api/career/save")
def career_save(body: SaveIn, request: Request, response: Response) -> SaveOut:
    """Keep a finished career. The token is signed (A199), so it needs no replay to be
    trusted; a career still going is refused, and saving one twice is a no-op."""
    response.headers["Cache-Control"] = "no-store"
    account_id = _account(request)
    seed, setup, checkpoint, partial = read_token(body.token)
    deck, model = _state()
    try:
        career = C.play_career(deck, model, seed, setup, partial, resume=checkpoint)
    except C.CareerError as e:
        raise HTTPException(409, str(e)) from None
    if career.summary is None:
        raise HTTPException(409, "That career is still going.")
    with db.connection() as conn:
        new = career_results.save(conn, account_id, career_results.natural_key(body.token), career)
        n = career_results.count(conn, account_id)
    return SaveOut(saved=new, careers=n)


class HallOut(BaseModel):
    key: str
    name: str
    role: str
    role_label: str
    debut: int
    overseas: bool
    seasons: int
    matches: int
    runs: int
    wickets: int
    titles: int
    peak_card: int
    peak_year: int
    peak_rating: int | None = None    # A214
    peak_rating_year: int | None = None
    ending: str
    runs_rank: int | None         # place on the real all-time list, derived when read
    wickets_rank: int | None
    crests: list[str | None]      # each club, in the order you joined it
    card: dict
    completed_at: str
    legend: bool = False          # can be brought into a friends' auction (A215)
    legend_value: float | None = None   # runs a match he adds to a twelve (A234)
    legend_bat: float | None = None     # [A269] the two halves of it: batting
    legend_bowl: float | None = None    # and bowling, None when his bowling is not counted
    legend_group: str | None = None     # [A269] the picker's role group, and its place
    legend_group_order: int | None = None
    honours: list[str] = []       # which induction honours it holds (A225)
    inducted: bool = False        # in the Hall of Fame proper; the rest fold away (A225)
    extras: list["ExtraLegendOut"] = []   # [A272] more seasons kept as legends, by request
    mastered: list[MasteredOut] = []   # [A285] from the card; read off its signature if older
    skills: list[SkillOut] = []   # [A278] the career's skills; none on a career saved before


class ExtraLegendOut(BaseModel):
    """[A272] One extra legend season of a saved career, shaped like a Hall of Fame entry's
    legend half so the lobby picker lists it the same way. `key` is what to bring it by."""
    key: str
    name: str
    card: dict                    # {"legend": ...}, as a hall entry's card holds it
    legend_value: float | None = None
    legend_bat: float | None = None
    legend_bowl: float | None = None
    legend_group: str | None = None
    legend_group_order: int | None = None


def _legend_fields(card: dict) -> dict:
    """[A234] Worked out when read from the stored legend, so every legend, old or new, is
    valued by the same rule. [A269] With its batting and bowling apart and its role group."""
    from tools.snapshot_deck import _card_from

    leg = card.get("legend")
    if not leg:
        return {}
    bat, bowl = C.legend_values(_card_from(leg["card"]))
    order, group = C.legend_group(leg.get("role", ""))
    return {"legend_value": round(bat + (bowl or 0.0), 2), "legend_bat": round(bat, 2),
            "legend_bowl": None if bowl is None else round(bowl, 2),
            "legend_group": group, "legend_group_order": order}


@router.get("/api/career/hall")
def career_hall(request: Request, response: Response) -> list[HallOut]:
    response.headers["Cache-Control"] = "no-store"
    account_id = _account(request)
    with db.connection() as conn:
        rows = career_results.hall(conn, account_id)
    facts = _facts()
    real_runs = [p.runs for p in facts] if facts is not None else None
    real_wkts = [p.wickets for p in facts] if facts is not None else None
    out = []
    for r in rows:
        bring = career_results.has_skills(r["card"])     # [A284] only a career with skills
        clubs = []
        for t in r["card"].get("timeline", []):
            if all(canonical(t["franchise"]) != canonical(c[0]) for c in clubs):
                clubs.append((t["franchise"], t["year"]))
        runs_rank = C.all_time_rank(r["runs"], real_runs) if real_runs is not None else None
        wickets_rank = C.all_time_rank(r["wickets"], real_wkts) if real_wkts is not None else None
        held = career_results.honours(r["peak_card"], r["titles"],
                                      r["card"].get("orange_caps", 0) + r["card"].get("purple_caps", 0),
                                      runs_rank, wickets_rank)
        out.append(HallOut(
            honours=held, inducted=career_results.inducted(held),
            key=r["key"], name=r["name"], role=r["role"],
            role_label=C.ROLES.get(r["role"], {}).get("label", r["role"]), debut=r["debut"],
            overseas=r["overseas"], seasons=r["seasons"], matches=r["matches"],
            runs=r["runs"], wickets=r["wickets"], titles=r["titles"],
            peak_card=r["peak_card"], peak_year=r["peak_year"], ending=r["ending"],
            peak_rating=r["card"].get("peak_rating"), peak_rating_year=r["card"].get("peak_rating_year"),
            legend=bring and bool(r["card"].get("legend")),
            **(_legend_fields(r["card"]) if bring else {}),
            runs_rank=runs_rank, wickets_rank=wickets_rank,
            crests=[crests.crest_url(f, y) for f, y in clubs], card=r["card"],
            skills=_skills_out(r["card"].get("skills")),
            mastered=_mastered_out(r["card"].get("mastered")
                                   or C.career_skills.mastered_from_signature(r["card"].get("skills"))),
            completed_at=r["completed_at"].isoformat(),
            extras=[ExtraLegendOut(key=career_results.extra_key(r["key"], x["year"]),
                                   name=r["name"], card={"legend": x},
                                   **_legend_fields({"legend": x}))
                    for x in (r["card"].get("extra_legends") or []) if bring]))
    return out
