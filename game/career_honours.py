"""Honours in Build a Career: what a career DID, never a claim about how it plays (A286).

Skills say how a player plays and must match the engine (A267/A278). Honours sit beside
them and are true by definition, because each one describes what happened: feat titles
earned from the scorecards (big matches, finals, one club you tormented, chases you saw
home, and the captaincy), a nickname the papers coin from the shape of the career, the real
players who taught you, the career's best match, its record against the rival the papers
built up, and a card finish set by everything won. None of it reaches the engine, a rating
or a legend's value in a room.

Pure: every honour is worked out from the season records when a page asks, never stored
apart from them (A19). Each season keeps one small `feats` record taken from its own match
feed (`season_feats`), because the feed itself is not kept. Every threshold here is declared
game design.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from etl.franchise_map import canonical

PLAYOFF_STAGES = ("Qualifier 1", "Eliminator", "Qualifier 2", "Final")


def _impact(runs: int, wickets: int) -> int:
    return runs + 20 * wickets            # A183's prominence: a batter and a bowler on one scale


def season_feats(feed, rival: list | None, captain: bool) -> dict:
    """One season's facts the honours need, off its match feed. Small, so it rides in the
    token: playoff and final figures, runs and wickets against each club, not-outs in won
    chases, the season's best match, and the record against the season's rival."""
    po = [0, 0, 0]                        # playoff matches played, runs, wickets
    finals = []                           # [runs, wickets, won, pom] per final played
    opp: dict[str, list[int]] = {}        # canonical club: [matches, runs, wickets]
    chases = 0                            # not out, 20+, in a chase your side won
    won = played = 0
    best = None
    vs_rival = [0, 0, 0, 0]               # matches, won, runs, wickets against his side
    rival_club = canonical(rival[0]) if rival else None
    for l in feed or []:
        if not l.played:
            continue
        played += 1
        won += l.result == "W"
        club = canonical(l.opp_franchise) if l.opp_franchise else l.opp_short
        o = opp.setdefault(club, [0, 0, 0])
        o[0] += 1
        o[1] += l.runs
        o[2] += l.wickets
        if l.stage in PLAYOFF_STAGES:
            po[0] += 1
            po[1] += l.runs
            po[2] += l.wickets
        if l.stage == "Final":
            finals.append([l.runs, l.wickets, int(l.result == "W"), int(l.pom)])
        if l.batted and not l.out and l.runs >= 20 and l.chasing and l.result == "W":
            chases += 1
        if rival_club and club == rival_club:
            vs_rival[0] += 1
            vs_rival[1] += l.result == "W"
            vs_rival[2] += l.runs
            vs_rival[3] += l.wickets
        score = _impact(l.runs, l.wickets) + (40 if l.stage == "Final" else
                                              20 if l.stage in PLAYOFF_STAGES else 0)
        score += 10 if l.result == "W" else 0
        if best is None or score > best["score"]:
            best = {"score": score, "stage": l.stage, "opp": l.opponent, "runs": l.runs,
                    "balls": l.balls, "out": l.out, "batted": l.batted, "wkts": l.wickets,
                    "bowl_runs": l.bowl_runs, "bowl_balls": l.bowl_balls,
                    "result": l.result, "margin": l.margin, "pom": bool(l.pom)}
    out = {"po": po, "finals": finals, "opp": opp, "chases": chases, "won": won,
           "played": played, "best": best, "captain": bool(captain)}
    if rival:
        out["rival"] = [rival[1], rival[0], *vs_rival]
    return out


# --- feat titles ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Title:
    key: str
    label: str
    text: str                           # why, in words
    kind: str = "feat"                  # "feat" | "captain"


BIG_MATCH_MIN = 4                       # playoff matches played...
BIG_MATCH_LIFT = 1.25                   # ...at this much your usual runs + 20 x wickets a match
BIG_MATCH_FLOOR = 30                    # ...and never under this a match
FINAL_GOOD_RUNS = 30                    # a final counts as yours at 30+ runs...
FINAL_GOOD_WKTS = 2                     # ...or two wickets
FINALS_MIN = 2                          # good finals for Finals specialist
SCOURGE_MIN_MATCHES = 8
SCOURGE_LIFT = 1.5                      # your usual runs + 20 x wickets a match, against them
SCOURGE_FLOOR = 25
CHASE_MIN = 10                          # not-outs (20+) in won chases
CAPTAIN_TITLES_DYNASTY = 3
CAPTAIN_SEASONS_LONG = 6
CAPTAIN_WIN_SHARE = 0.6                 # matches won as captain, of those played...
CAPTAIN_WIN_MIN = 40                    # ...over at least this many


def _feats(seasons) -> list[dict]:
    return [r.feats for r in seasons if getattr(r, "feats", None)]


def titles(seasons) -> list[Title]:
    """Every feat title the career has earned, in a fixed order. Read off the season records
    (`feats`, plus the captaincy and titles every record has always carried), so a career
    saved before A286 earns the captaincy titles its records support and no feat it cannot."""
    out: list[Title] = []
    fs = _feats(seasons)
    played = sum(f["played"] for f in fs)
    usual = sum(_impact(c[1], c[2]) for f in fs for c in f["opp"].values()) / max(played, 1)
    po_m = sum(f["po"][0] for f in fs)
    po_i = sum(_impact(f["po"][1], f["po"][2]) for f in fs)
    if (po_m >= BIG_MATCH_MIN and po_i >= BIG_MATCH_FLOOR * po_m
            and po_i >= BIG_MATCH_LIFT * usual * po_m):
        out.append(Title("big_match", "Big-match player",
                         f"{po_m} playoff matches, at their best when it mattered most"))
    finals = [x for f in fs for x in f["finals"]]
    good = [x for x in finals if x[0] >= FINAL_GOOD_RUNS or x[1] >= FINAL_GOOD_WKTS]
    if len(good) >= FINALS_MIN and 2 * len(good) >= len(finals):
        out.append(Title("finals", "Finals specialist", f"{len(good)} finals decided by you"))
    clubs: dict[str, list[int]] = {}
    for f in fs:
        for club, (m, r, w) in f["opp"].items():
            c = clubs.setdefault(club, [0, 0, 0])
            c[0] += m
            c[1] += r
            c[2] += w
    regular = {c: v for c, v in clubs.items() if v[0] >= SCOURGE_MIN_MATCHES}
    if regular:
        club, (m, r, w) = max(regular.items(),
                              key=lambda kv: (_impact(kv[1][1], kv[1][2]) / kv[1][0], kv[0]))
        rate = _impact(r, w) / m
        if rate >= SCOURGE_FLOOR and rate >= SCOURGE_LIFT * usual:
            what = " and ".join(x for x in (f"{r} runs" if r else "",
                                           f"{w} wickets" if w else "") if x)
            out.append(Title("scourge", f"Scourge of {club}", f"{what} in {m} matches against them"))
    chases = sum(f["chases"] for f in fs)
    if chases >= CHASE_MIN:
        out.append(Title("chase", "Chase master", f"Not out {chases} times seeing a chase home"))
    out += captain_titles(seasons)
    return out


def captain_titles(seasons) -> list[Title]:
    """Titles for the captaincy, off `captain` and `champion`, which every season record has
    carried since A197/A213, so they reach careers saved before A286 too."""
    led = [r for r in seasons if r.captain]
    out: list[Title] = []
    if not led:
        return out
    cups = sum(r.champion for r in led)
    if cups >= CAPTAIN_TITLES_DYNASTY:
        out.append(Title("dynasty", "Dynasty builder", f"{cups} titles as captain", "captain"))
    elif cups:
        out.append(Title("winning_captain", "Title-winning captain",
                         "Lifted the trophy as captain", "captain"))
    if len(led) >= CAPTAIN_SEASONS_LONG:
        out.append(Title("leader", "Leader of men", f"Captain for {len(led)} seasons", "captain"))
    clubs = {canonical(r.franchise) for r in led}
    if len(led) >= 4 and len(clubs) == 1:
        out.append(Title("one_club_captain", f"{next(iter(clubs))}'s captain",
                         f"Led one club for {len(led)} seasons", "captain"))
    fs = [r.feats for r in led if getattr(r, "feats", None)]
    p, w = sum(f["played"] for f in fs), sum(f["won"] for f in fs)
    if p >= CAPTAIN_WIN_MIN and w >= CAPTAIN_WIN_SHARE * p:
        out.append(Title("winning_record", "Born winner",
                         f"Won {w} of {p} matches as captain", "captain"))
    if any(x[0] >= 50 and x[2] for f in fs for x in f["finals"]):
        out.append(Title("captains_knock", "Captain's knock",
                         "A fifty in a final won as captain", "captain"))
    return out


# --- the nickname --------------------------------------------------------------------------
# The papers coin one once a career has a shape, and it sticks: it is decided on the first
# season the career fits a type (`NICK_FROM` seasons in at the earliest) and never changes
# after, however the career goes on. Avoided on purpose: nicknames tied to real players.

NICK_FROM = 4                           # seasons before the papers coin one from skills shown
NICK_WORK_FROM = 7                      # ...and from the work chosen, if nothing has shown by then

# Each type: (key, test on the career so far, names to choose from). Checked in this order;
# the first that fits decides the nickname.
NICK_POOLS = {
    "swing": ("Swing King", "The Swing Merchant", "Mr New Ball"),
    "yorker": ("The Yorker Man", "The Toe-Crusher", "Mr Last Over"),
    "spin_strike": ("The Spin Wizard", "The Magician", "The Mystery Man"),
    "spin_miser": ("The Squeeze", "Mr Miserly", "The Strangler"),
    "pace": ("The Express", "The Spearhead", "The Workhorse"),
    "spin": ("The Twirler", "The Spin Doctor", "The Wily One"),
    "general": ("The General", "The Skipper", "The Gaffer"),
    "finisher": ("The Finisher", "Ice in the Veins", "The Closer"),
    "anchor": ("The Rock", "Old Reliable", "The Anchor"),
    "launch": ("The Launchpad", "Powerplay Pirate", "The Opener's Opener"),
    "spin_killer": ("The Spin Killer", "Twirl Tamer", "The Dancing Feet"),
    "pace_tamer": ("The Pace Tamer", "Fearless", "The Hook Merchant"),
    "destroyer": ("The Destroyer", "The Demolisher", "The Wrecking Ball"),
    "allrounder": ("Mr Everything", "The Two-in-One", "The Utility Man"),
    "runs": ("The Run Machine", "The Accumulator", "Mr Dependable"),
}


def _worked(r) -> set[str]:
    """What a season was PLAYED with, as the nickname reads it: the drills, phase work and
    temper on its record before a ball was bowled (so a season's own focus verdict, recorded
    after its milestones, never changes which season coined the name)."""
    out: set[str] = set()
    d = getattr(r, "drills_built", None) or {}
    ph = getattr(r, "phase_built", None) or {}
    temper = (getattr(r, "traits", None) or {}).get("temper", 0.0)
    bowl, bat = ph.get("bowl") or (0, 0, 0), ph.get("bat") or (0, 0, 0)
    if d.get("spin", 0) >= 1:
        out.add("spin")
    if d.get("pace", 0) >= 1:
        out.add("pace")
    if d.get("death_wkts", 0) >= 1 or bowl[2] >= WORKED_PHASE:
        out.add("yorker")
    if bowl[0] >= WORKED_PHASE:
        out.add("new_ball")
    if bat[0] >= WORKED_PHASE:
        out.add("pp_bat")
    if bat[2] >= WORKED_PHASE:
        out.add("death_bat")
    if temper >= 1:
        out.add("attack")
    if temper <= -1:
        out.add("patience")
    return out


WORKED_PHASE = 0.03                     # one step of phase work (A248's PHASE_STEP)


def nick_type(seasons, role: str, style: str | None, work_too: bool = True) -> str | None:
    """The type the career so far fits, or None. `role` is the created role, `style` the
    bowling style (pace / spin / None). A skill counts once shown in two seasons, a piece of
    work once two seasons were played with it."""
    held: dict[str, int] = {}
    work: dict[str, int] = {}
    for r in seasons:
        for sk in getattr(r, "skills", None) or []:
            held[sk[0]] = held.get(sk[0], 0) + 1
        for w in _worked(r):
            work[w] = work.get(w, 0) + 1
    bowler = role in ("pace", "spin") or (role == "allrounder" and style)
    captained = sum(r.captain and r.champion for r in seasons) >= 2
    # What the career SHOWED decides first; what it worked on only where it showed nothing.
    # So a batter who drilled against spin but became an anchor is named for the anchoring.
    # Skills can only show from a Rating of 80, so what was worked on always comes first; the
    # papers wait for what shows, and name the work only when nothing has (`work_too`).
    for count, need in ((held, 2), (work, 2)) if work_too else ((held, 2),):
        def has(*keys):
            return any(count.get(k, 0) >= need for k in keys)
        skills = count is held
        if bowler and style == "pace" and has("new_ball"):
            return "swing"
        if bowler and style == "pace" and has(*(("death_wickets", "death_bowler") if skills
                                                else ("yorker",))):
            return "yorker"
        if bowler and style == "spin" and role == "spin" and has("strike_bowler" if skills else "attack"):
            return "spin_strike"
        if bowler and style == "spin" and role == "spin" and has("miser" if skills else "patience"):
            return "spin_miser"
        if not skills and captained:
            return "general"
        if role in ("pace", "spin"):
            continue
        if has("death_hitter" if skills else "death_bat"):
            return "finisher"
        if has("anchor" if skills else "patience"):
            return "anchor"
        if has("pp_specialist" if skills else "pp_bat"):
            return "launch"
        if has("spin_basher" if skills else "spin"):
            return "spin_killer"
        if has("pace_basher" if skills else "pace"):
            return "pace_tamer"
        if has(*(("aggressor", "big_hitter", "boundary_hitter") if skills else ("attack",))):
            return "destroyer"
    if not work_too:
        return None
    if captained:
        return "general"
    if role in ("pace", "spin"):
        return role
    if role == "allrounder":
        return "allrounder"
    if sum(r.runs for r in seasons) >= 2500:
        return "runs"
    return None


def nickname(seasons, role: str, style: str | None, name: str) -> tuple[str, int] | None:
    """(the nickname, the year the papers coined it), or None if no type fits yet. Sticky:
    decided on the first season from `NICK_FROM` that fits a type by the skills it showed
    (from `NICK_WORK_FROM`, by the work it chose too), chosen within the type
    by a hash of the player's name and that year, so it never moves afterwards."""
    for i in range(NICK_FROM, len(seasons) + 1):
        t = nick_type(seasons[:i], role, style, work_too=i >= NICK_WORK_FROM)
        if t:
            year = seasons[i - 1].year
            pool = NICK_POOLS[t]
            h = int(hashlib.sha256(f"{name}:{year}:{t}".encode()).hexdigest(), 16)
            return pool[h % len(pool)], year
    return None


# --- lineage, the best match, the rival, the card finish -----------------------------------

def lineage(seasons) -> list[str]:
    """The real players who taught you, in the order they did (`SeasonRecord.mentors`)."""
    out: list[str] = []
    for r in seasons:
        for m in getattr(r, "mentors", None) or []:
            if m not in out:
                out.append(m)
    return out


def signature(seasons) -> dict | None:
    """The career's best match: the season's own best, scored by runs + 20 x wickets, more
    in a final or playoff and for a win. The earlier one on a tie."""
    best = None
    for r in seasons:
        b = (getattr(r, "feats", None) or {}).get("best")
        if b and (best is None or b["score"] > best["score"]):
            best = dict(b, year=r.year, club=r.franchise)
    return best


def signature_words(b: dict | None) -> str | None:
    if not b:
        return None
    bat = f"{b['runs']}{'' if b['out'] else '*'} off {b['balls']}" if b["batted"] else ""
    bowl = f"{b['wkts']}/{b['bowl_runs']}" if b["bowl_balls"] else ""
    # The half that made the match the best one: a few runs beside a five-for are noise.
    if bat and bowl:
        if b["runs"] < 20 and b["wkts"]:
            bat = ""
        elif not b["wkts"] and b["runs"] >= 20:
            bowl = ""
    parts = [x for x in (bat, bowl) if x]
    if not parts:
        return None
    opp = b["opp"].rsplit(" ", 1)[0] if b["opp"][-4:].isdigit() else b["opp"]
    stage = "" if b["stage"] == "league" else f" {b['stage']}"
    res = {"W": "won", "L": "lost", "T": "tied"}.get(b["result"], "")
    tail = f" · {res}" if res else ""      # the margin names the winner, so it is left out
    return f"{' and '.join(parts)} v {opp}, {b['year']}{stage}{tail}"


def rivalries(seasons) -> list[dict]:
    """Head to head against each rival the papers built up: matches against his side, won,
    lost, and your runs and wickets in them."""
    out: dict[str, dict] = {}
    for r in seasons:
        rv = (getattr(r, "feats", None) or {}).get("rival")
        if not rv:
            continue
        name, club, m, w, runs, wk = rv
        d = out.setdefault(name, {"name": name, "club": canonical(club), "matches": 0,
                                  "won": 0, "runs": 0, "wickets": 0, "seasons": 0})
        d["matches"] += m
        d["won"] += w
        d["runs"] += runs
        d["wickets"] += wk
        d["seasons"] += 1
    return [d for d in out.values() if d["matches"]]


# Measured on 84 careers (seven roles, careless and strength-first policies, 17 seasons at the
# median): points at the 20th/40th/60th/80th/95th percentile read 7 / 13 / 20 / 31 / 40, so
# each band holds about a fifth of careers; a careless career reaches Platinum about 1 in 20.
FINISHES = ((0, "Standard"), (8, "Bronze"), (14, "Silver"), (22, "Gold"), (34, "Platinum"))


def finish_points(seasons, mastered: int, titles_: list[Title]) -> int:
    """Points toward the card finish: 3 a title, 2 a cap, 2 a mastered skill, 1 a feat or
    captaincy title, 1 a season rated 90+ (declared)."""
    return (3 * sum(r.champion for r in seasons)
            + 2 * sum(r.orange_cap + r.purple_cap for r in seasons)
            + 2 * mastered + len(titles_)
            + sum((r.rating or 0) >= 90 for r in seasons))


def finish(points: int) -> str:
    out = FINISHES[0][1]
    for at, name in FINISHES:
        if points >= at:
            out = name
    return out


@dataclass
class Honours:
    nickname: str | None = None
    nickname_year: int | None = None
    titles: list[Title] = field(default_factory=list)
    lineage: list[str] = field(default_factory=list)
    signature: dict | None = None
    signature_words: str | None = None
    rivalries: list[dict] = field(default_factory=list)
    finish: str = "Standard"
    points: int = 0


def honours(seasons, role: str, style: str | None, name: str, mastered: int) -> Honours:
    ts = titles(seasons)
    nick = nickname(seasons, role, style, name)
    sig = signature(seasons)
    pts = finish_points(seasons, mastered, ts)
    return Honours(nickname=nick[0] if nick else None, nickname_year=nick[1] if nick else None,
                   titles=ts, lineage=lineage(seasons), signature=sig,
                   signature_words=signature_words(sig), rivalries=rivalries(seasons),
                   finish=finish(pts), points=pts)
