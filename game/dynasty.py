"""Club dynasty, stage one [A307]: several Build a Career careers told as one club's story.

Pure: no database, no deck. A pass holder picks ONE club; a career started while that club
is their dynasty is a dynasty career, and when it finishes it joins the dynasty's history
(a compact record of its seasons, `record_of`). Everything the dynasty card shows is
worked out from those records each time it is read (`card`), never stored apart from them
(A19), so a rule changed here re-tells every dynasty at once.

What a dynasty changes in play is one thing only: a dynasty career starts ONE step warmer
at the club (`start_bonus`, A230's club standing) -- unless the dynasty's last career left
the club by being released while there, which costs that step for the next one. Nothing
here touches a rating or a match.

Every threshold below is declared game design, like REPUTATION: the archive has nothing to
measure a dynasty against.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FIRST_YEAR, LAST_YEAR = 2008, 2026     # the seasons a career can play (debuts 2008-2017)
START_BONUS = 1                        # club standing a dynasty career starts with at the club
ERA_GAP = 3                            # titles this close (in years) belong to one era...
ERA_TITLES = 2                         # ...and an era needs at least this many
RIVAL_MIN = 2                          # knockout meetings before a club is named the rival
SCORE = {"title": 10, "final": 4, "cap": 3, "season": 1}


# --- what a finished dynasty career keeps -------------------------------------------------

def record_of(setup, checkpoint, canon) -> dict:
    """A finished career's seasons as the dynasty keeps them: small, and only what the card
    reads. `canon` maps a franchise display name to its canonical club (etl.franchise_map)."""
    seasons = []
    for r in checkpoint.seasons:
        feats = r.feats or {}
        seasons.append({
            "year": r.year, "club": canon(r.franchise), "franchise": r.franchise,
            "short": r.short, "runs": r.runs, "wickets": r.wickets, "matches": r.matches,
            "captain": bool(r.captain), "champion": bool(r.champion), "finalist": bool(r.finalist),
            "orange": bool(r.orange_cap), "purple": bool(r.purple_cap),
            "rating": r.rating, "grade": r.display, "ko": list(feats.get("ko") or [])})
    last = checkpoint.seasons[-1] if checkpoint.seasons else None
    return {"name": setup.name, "role": setup.role, "debut": setup.debut,
            "ended": checkpoint.ended, "epilogue": checkpoint.epilogue or "",
            "last_club": canon(last.franchise) if last else None, "seasons": seasons}


def at_club(rec: dict, club: str) -> list[dict]:
    return [s for s in rec["seasons"] if s["club"] == club]


def joins(rec: dict, club: str) -> bool:
    """A finished career joins the dynasty only if it played a season for the club."""
    return rec["ended"] is not None and bool(at_club(rec, club))


def left_badly(rec: dict, club: str) -> bool:
    """Released while at the club: the one exit a season record can see (A288 releases)."""
    return rec["ended"] == "released" and rec["last_club"] == club


def start_bonus(history: list[dict], club: str) -> int:
    """Standing a new dynasty career starts with at the club: one step, unless the dynasty's
    most recent career left it by being released there."""
    if history and left_badly(history[-1], club):
        return 0
    return START_BONUS


# --- the card -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Era:
    first: int
    last: int
    titles: int
    name: str                          # the player with most seasons at the club in it

    @property
    def label(self) -> str:
        span = str(self.first) if self.first == self.last else f"{self.first}–{self.last}"
        return f"The {surname(self.name)} years, {span}"


@dataclass(frozen=True)
class Leader:
    name: str
    value: int


@dataclass
class Card:
    club: str
    careers: int = 0
    seasons: int = 0
    titles: list[int] = field(default_factory=list)          # years
    finals_lost: list[int] = field(default_factory=list)
    caps: list[tuple[int, str, str]] = field(default_factory=list)   # year, "orange"/"purple", name
    captains: list[tuple[str, int]] = field(default_factory=list)    # name, seasons as captain
    runs: list[Leader] = field(default_factory=list)
    wickets: list[Leader] = field(default_factory=list)
    best: list[dict] = field(default_factory=list)            # best seasons, by grade
    eras: list[Era] = field(default_factory=list)
    covered: list[int] = field(default_factory=list)          # years one of yours was at the club
    gaps: list[int] = field(default_factory=list)
    rival: tuple[str, int, int] | None = None                 # club, won, lost in knockouts
    timeline: list[dict] = field(default_factory=list)        # per career: name, years at the club
    score: int = 0


def surname(name: str) -> str:
    parts = name.split()
    return parts[-1] if parts else name


def eras(titles: list[int], seasons_by_year: dict[int, list[str]]) -> list[Era]:
    """Runs of titles no more than ERA_GAP years apart, of at least ERA_TITLES; each named
    after whoever of yours played most seasons for the club across it (ties: first named)."""
    out: list[Era] = []
    run: list[int] = []
    for y in sorted(set(titles)) + [None]:
        if run and (y is None or y - run[-1] > ERA_GAP):
            if len(run) >= ERA_TITLES:
                count: dict[str, int] = {}
                for yr in range(run[0], run[-1] + 1):
                    for n in seasons_by_year.get(yr, []):
                        count[n] = count.get(n, 0) + 1
                name = max(count, key=lambda n: (count[n], -list(count).index(n))) if count else ""
                out.append(Era(run[0], run[-1], len(run), name))
            run = []
        if y is not None:
            run.append(y)
    return out


def card(history: list[dict], club: str) -> Card:
    c = Card(club=club)
    runs: dict[str, int] = {}
    wkts: dict[str, int] = {}
    caps_as: dict[str, int] = {}
    by_year: dict[int, list[str]] = {}
    titles: set[int] = set()
    ko: dict[str, list[int]] = {}
    best = []
    for rec in history:
        mine = at_club(rec, club)
        if not mine:
            continue
        c.careers += 1
        name = rec["name"]
        c.timeline.append({"name": name, "role": rec["role"], "years": [s["year"] for s in mine]})
        for s in mine:
            c.seasons += 1
            by_year.setdefault(s["year"], []).append(name)
            runs[name] = runs.get(name, 0) + s["runs"]
            wkts[name] = wkts.get(name, 0) + s["wickets"]
            if s["captain"]:
                caps_as[name] = caps_as.get(name, 0) + 1
            if s["champion"]:
                titles.add(s["year"])
            elif s["finalist"]:
                c.finals_lost.append(s["year"])
            if s["orange"]:
                c.caps.append((s["year"], "orange", name))
            if s["purple"]:
                c.caps.append((s["year"], "purple", name))
            for _stage, opp, won in s["ko"]:
                if opp != club:
                    k = ko.setdefault(opp, [0, 0])
                    k[0 if won else 1] += 1
            best.append({"name": name, "year": s["year"], "grade": s["grade"],
                         "runs": s["runs"], "wickets": s["wickets"]})
    c.titles = sorted(titles)
    c.finals_lost = sorted(set(c.finals_lost) - titles)
    c.caps.sort()
    c.captains = sorted(caps_as.items(), key=lambda kv: (-kv[1], kv[0]))
    c.runs = [Leader(n, v) for n, v in sorted(runs.items(), key=lambda kv: (-kv[1], kv[0])) if v][:5]
    c.wickets = [Leader(n, v) for n, v in sorted(wkts.items(), key=lambda kv: (-kv[1], kv[0])) if v][:5]
    c.best = sorted(best, key=lambda b: (-b["grade"], b["year"], b["name"]))[:5]
    c.eras = eras(c.titles, by_year)
    c.covered = sorted(by_year)
    c.gaps = [y for y in range(FIRST_YEAR, LAST_YEAR + 1) if y not in by_year]
    met = {k: v for k, v in ko.items() if sum(v) >= RIVAL_MIN}
    if met:
        opp = max(met, key=lambda k: (sum(met[k]), met[k][1], -sorted(met).index(k)))
        c.rival = (opp, met[opp][0], met[opp][1])
    c.score = (SCORE["title"] * len(c.titles) + SCORE["final"] * len(c.finals_lost)
               + SCORE["cap"] * len(c.caps) + SCORE["season"] * c.seasons)
    return c


def share_text(c: Card, display: str) -> str:
    """No player is named, A129's rule for every share line here."""
    years = f"{len(c.covered)} of {LAST_YEAR - FIRST_YEAR + 1} seasons"
    titles = f"{len(c.titles)} title{'' if len(c.titles) == 1 else 's'}"
    line = [f"My {display} dynasty: {c.careers} career{'' if c.careers == 1 else 's'}, "
            f"{years}, {titles}, {len(c.caps)} cap{'' if len(c.caps) == 1 else 's'}.",
            f"Dynasty score {c.score}."]
    if c.eras:
        line.append(f"{len(c.eras)} era{'' if len(c.eras) == 1 else 's'} of titles.")
    return " ".join(line)
