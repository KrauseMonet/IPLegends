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
from etl.franchise_map import canonical
from game.auction import crore
from game.site import SITE_NAME, SITE_ORIGIN
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
    card: int | None = None       # his real card that season: where your Rating starts or can peak (A214)


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
    contract_year: int            # 1-3 on a three-year deal, 0 on a one-year
    event: str
    choice: str
    card: int                     # the Season grade, after the reputation floor
    form: int                     # the season's own grade
    rating: int | None = None     # your Rating that season (A214); None on an older token
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
    mega: str = ""                # how a mega-auction year went (A212)
    plan: list[str] = []          # "Name: Death", the plan the season began with (A213)


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
    hint: str                     # what it does, worded from the option's own numbers


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


class MilestoneOut(BaseModel):
    year: int
    text: str
    key: str = ""                 # "title", "orange_cap", ... for the timeline's colour (A219)


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
    milestones: list[str] = []    # career milestones reached in this match (A209)


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
    season_play: SeasonPlayOut | None = None
    auction: AuctionOut | None = None
    rating: int = 0               # your Rating now: going into the season pending, or the last (A214)
    potential: int = 0            # the ceiling's card: the most your Rating can reach
    age: int = 0
    injuries: list[str] = []      # "2014 · Side strain (5 matches)"


def _template(c) -> TemplateOut:
    return TemplateOut(name=c.name, year=c.season_year, franchise=c.franchise,
                       crest=crests.crest_url(c.franchise, c.season_year), card=c.display)


def _season(r: C.SeasonRecord) -> SeasonOut:
    return SeasonOut(
        year=r.year, franchise=r.franchise, short=r.short,
        crest=crests.crest_url(r.franchise, r.year), price=crore(r.price), stayed=r.stayed,
        squad=r.squad, captain=r.captain, contract_year=r.contract_year, event=r.event,
        choice=r.choice, card=r.display, rating=r.rating or None, age=C.DEBUT_AGE + r.k,
        notes=list(r.notes), note_tones=[C.note_tone(t) for t in r.notes],
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
        plan=[f"{n}: {PHASE_WORDS[ph]}" for n, ph in r.plan])


def option_hint(o: C.EventOption) -> str:
    """What an answer does, in words, derived from the numbers it applies -- never written
    beside them, so the hint cannot say one thing while the option does another. In Rating
    points (A214), with the chances of each way it can turn out."""
    bits = []
    if o.season:
        bits.append(f"{o.season:+d} Rating this season")
    if o.permanent:
        bits.append(f"{o.permanent:+d} Rating for good")
    if o.captain:
        bits.append("you captain the side")
    if o.missed:
        bits.append(f"miss {o.missed} matches")
    if o.retire:
        bits.append("your career ends here")
    for out in o.outcomes:
        what = []
        if out.permanent:
            what.append(f"{out.permanent:+d} Rating for good")
        if out.missed:
            what.append(f"{out.missed} more matches out")
        if what:
            when = "next season" if out.next_season else "this season"
            bits.append(f"{round(100 * out.chance)}% chance {when}: {', '.join(what)}")
    return "; ".join(bits)[:1].upper() + "; ".join(bits)[1:] if bits else "No change"


def _club_gone(career: C.Career) -> str | None:
    p = career.pending
    if p is None or p.kind != "offer" or not career.seasons:
        return None
    last = career.seasons[-1].franchise
    deck, _ = _state()
    playing = {canonical(deck.cards_by_fs[fs][0].franchise) for fs in C.year_fs_ids(deck, p.year)}
    return last if canonical(last) not in playing else None


PHASE_WORDS = {"powerplay": "Powerplay", "middle": "Middle overs", "death": "Death"}

APPROACH_HINTS = ("Fewer runs, safer: out less often", "Your usual game",
                  "More runs, riskier: out more often")
BOWLING_HINTS = ("Cheaper overs, fewer wickets", "Your usual game",
                 "More wickets, more runs conceded")


def _moment(m: C.Moment, role: str) -> MomentOut:
    hints = BOWLING_HINTS if role in ("pace", "spin") else APPROACH_HINTS
    return MomentOut(stage=m.stage, opponent=m.opponent, opp_short=m.opp_short,
                     opp_crest=crests.crest_url(m.opp_franchise, m.opp_year), stakes=m.stakes,
                     options=[MomentOptionOut(label=l, hint=h) for l, h in zip(m.labels, hints)],
                     kind=m.kind)


def _pending(p: C.Pending, role: str = "") -> PendingOut:
    event = None
    if p.kind == "event":
        event = EventOut(key=p.event.key, title=p.event.title, text=p.event_text or p.event.text,
                         options=[EventOptionOut(label=o.label, hint=option_hint(o))
                                  for o in p.event.options])
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
        mega=mega, plan=plan)


def _offer(o: C.Offer, year: int) -> OfferOut:
    return OfferOut(franchise=o.franchise, short=o.short,
                    crest=crests.crest_url(o.franchise, year), price=crore(o.price),
                    price3=(crore(o.price3) if o.price3 is not None and not o.contract_year
                            else None),
                    contract_year=o.contract_year, strength=o.strength, stay=o.stay,
                    position=o.position, attack_rank=o.attack_rank)


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


def _summary(c: C.Career) -> SummaryOut:
    s = c.summary
    ending = "retired" if c.retired else "released" if c.released else "the end of the archive"
    return SummaryOut(
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
                    for r in c.seasons for m in r.milestones])


def _season_play(detail: C.SeasonDetail,
                 milestones: list[C.CareerMilestone] = ()) -> SeasonPlayOut:
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
        form_word=l.form_word, milestones=[m.text for m in milestones if m.match == i],
        card=l.card, absence=l.absence)
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
    bits.append(f"peak Rating {s.peak_rating}" if s.peak_rating else f"peak card {s.peak_display}")
    return (f"{SITE_NAME} · Build a Career\n{role}, debut {c.setup.debut}: "
            f"{', '.join(bits)}\n{marks}\n{SITE_ORIGIN}/career")


def _out(seed: int, career: C.Career, partial: list[str]) -> CareerOut:
    st = career.setup
    return CareerOut(
        token=make_token(seed, st, career, partial), name=st.name, role=st.role,
        role_label=C.ROLES[st.role]["label"], debut=st.debut, overseas=st.overseas,
        start=_template(career.start), ceiling=_template(career.ceiling),
        seasons=[_season(r) for r in career.seasons],
        pending=(_pending(career.pending, st.role).model_copy(update={"club_gone": _club_gone(career)})
                 if career.pending else None),
        summary=_summary(career) if career.summary else None,
        share=share_text(career) if career.summary else None,
        season_play=(_season_play(career.detail, career.live_milestones)
                     if career.detail else None),
        auction=_auction(career.auction, _auction_year(career)) if career.auction else None,
        rating=_rating_now(career), potential=career.ceiling.display,
        age=C.DEBUT_AGE + (career.pending.k if career.pending else len(career.seasons) - 1),
        injuries=[f"{y} · {t}" for y, t in (career.checkpoint.injuries if career.checkpoint else [])])


def _rating_now(career: C.Career) -> int:
    """Your Rating going into the season pending (before its event), or the last season's."""
    if career.pending is None or career.checkpoint is None:
        return career.seasons[-1].rating if career.seasons else 0
    k = career.pending.k
    return C.rating_at(career.start, career.ceiling,
                       C.arc_point(k, career.checkpoint.permanent, 0.0))


def _auction_year(career: C.Career) -> int:
    """The year the auction just decided was for: the season pending, or the one just played."""
    return career.pending.year if career.pending else career.seasons[-1].year


def _moves_since_checkpoint(career: C.Career, moves: list[str]) -> list[str]:
    """The moves to keep beside the checkpoint: those that answered the season now pending
    (an offer awaiting its event, a season paused at a key moment), none once a season is
    played or the career is over."""
    return (moves if career.pending is not None and career.pending.kind in ("event", "plan", "moment")
            else [])


# --- routes -----------------------------------------------------------------------------------


class RoleOut(BaseModel):
    key: str
    label: str
    blurb: str = ""


class OptionsOut(BaseModel):
    roles: list[RoleOut]
    first_debut: int
    last_debut: int


@router.get("/api/career/options")
def career_options() -> OptionsOut:
    return OptionsOut(roles=[RoleOut(key=k, label=v["label"], blurb=v["blurb"]) for k, v in C.ROLES.items()],
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
        clubs = []
        for t in r["card"].get("timeline", []):
            if all(canonical(t["franchise"]) != canonical(c[0]) for c in clubs):
                clubs.append((t["franchise"], t["year"]))
        out.append(HallOut(
            key=r["key"], name=r["name"], role=r["role"],
            role_label=C.ROLES.get(r["role"], {}).get("label", r["role"]), debut=r["debut"],
            overseas=r["overseas"], seasons=r["seasons"], matches=r["matches"],
            runs=r["runs"], wickets=r["wickets"], titles=r["titles"],
            peak_card=r["peak_card"], peak_year=r["peak_year"], ending=r["ending"],
            peak_rating=r["card"].get("peak_rating"), peak_rating_year=r["card"].get("peak_rating_year"),
            legend=bool(r["card"].get("legend")),
            runs_rank=C.all_time_rank(r["runs"], real_runs) if real_runs is not None else None,
            wickets_rank=(C.all_time_rank(r["wickets"], real_wkts)
                          if real_wkts is not None else None),
            crests=[crests.crest_url(f, y) for f, y in clubs], card=r["card"],
            completed_at=r["completed_at"].isoformat()))
    return out
