"""Write the IPL history pages -- a page per season and a page per franchise -- from the archive.

    uv run python -m tools.build_history           # (re)write web/static/seasons/, teams/
    uv run python -m tools.build_history --check   # exit 1 if a committed page is stale

WHY. Almost everything on this site is drawn by JavaScript after the page loads, so a crawler
reading the pages finds buttons and labels and very little else, which is what Google AdSense
rejected as "low-value content". These pages are the opposite: plain HTML, written out once,
holding the record the game is built from -- every season's champion, final, table and
leaders, every franchise's history -- in sentences as well as tables.

THE RULES the numbers follow are the project's own (CLAUDE.md), so a figure here agrees with
the scorecards and the ratings rather than with some other convention:

  - super overs count for nothing but the result;
  - a ball faced is any ball that was not a wide (A22), so a no-ball is a ball faced;
  - a bowler is charged runs off the bat, wides and no-balls, never byes or leg-byes (A1),
    and credited only the wickets that are his (A36);
  - "retired hurt" is not a dismissal; every other way out is;
  - a season is the year its matches were played in, never the archive's label;
  - a franchise is its CANONICAL name, so Delhi Daredevils and Delhi Capitals are one club.

The league table needs the stage of each match, which the loader does not keep, so it is read
from `data/ipl_json.zip`. Matches abandoned without a ball are not in the archive at all, so a
table here can be a point or two short of the official one, and each page says how many such
matches it knows to be missing (interior gaps in the match numbers; a gap at the very end of a
schedule cannot be seen, which is also said).

Same treatment as `tools.snapshot_deck`: a committed build artefact, written by a command, with
`--check` as the gate. Pages are written with the shared shell already in place and every asset
stamped, so `tools.shell --check` and `tools.stamp_assets --check` pass on them too.
"""

from __future__ import annotations

import html
import json
import os
import pathlib
import re
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "web" / "static"
PARTIALS = ROOT / "web" / "partials"
ARCHIVE = ROOT / "data" / "ipl_json.zip"
SEASON_DIR = STATIC / "seasons"
TEAM_DIR = STATIC / "teams"
ORIGIN = "https://fineleg.app"

TOP = 10                    # rows in each leaderboard
NOT_OUT = {"retired hurt"}  # every other wicket kind is a dismissal


# --------------------------------------------------------------------------- the archive

@dataclass
class Match:
    match_id: str
    day: date
    year: int
    venue: str | None
    city: str | None
    a: int
    b: int
    winner: int | None
    result_type: str | None
    margin: int | None
    decided_by: str | None
    pom: str | None
    stage: str | None = None          # None = league stage
    number: int | None = None


@dataclass
class Bat:
    match_id: str
    fs: int
    person: str
    runs: int = 0
    balls: int = 0
    fours: int = 0
    sixes: int = 0
    out: bool = False


@dataclass
class Bowl:
    match_id: str
    fs: int
    person: str
    balls: int = 0
    runs: int = 0
    wickets: int = 0


@dataclass
class Total:
    match_id: str
    innings: int
    fs: int
    runs: int = 0
    wickets: int = 0
    balls: int = 0


@dataclass
class Archive:
    fs: dict[int, tuple[str, int, str]]          # fs id -> (franchise, year, display name)
    names: dict[str, str]                        # person -> the name a fan knows (A252)
    matches: dict[str, Match]
    bats: list[Bat]
    bowls: list[Bowl]
    totals: list[Total]
    gaps: dict[int, list[int]] = field(default_factory=dict)


def _connect():
    import psycopg
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    return psycopg.connect(os.environ["DIRECT_URL"])


def load(conn, archive: pathlib.Path = ARCHIVE) -> Archive:
    cur = conn.cursor()
    cur.execute("""select fs.franchise_season_id, f.canonical_name, fs.season_year, fs.display_name
                   from franchise_seasons fs join franchises f using (franchise_id)""")
    fs = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}
    cur.execute("select person_id, coalesce(display_name, primary_name) from people")
    names = dict(cur.fetchall())
    cur.execute("""select match_id, match_date, season_year, venue, city, team_a_fs_id, team_b_fs_id,
                          winner_fs_id, result_type, result_margin, decided_by, player_of_match_id
                   from matches""")
    matches = {r[0]: Match(*r) for r in cur.fetchall()}

    # Batting: an innings is any appearance at the crease, as striker or non-striker (A4).
    cur.execute("""
        with crease as (
            select match_id, innings_no, batting_fs_id fs, batter_id person from deliveries where not is_super_over
            union
            select match_id, innings_no, batting_fs_id, non_striker_id from deliveries where not is_super_over),
        faced as (
            select match_id, innings_no, batter_id person,
                   sum(runs_batter) runs, count(*) filter (where extra_wides = 0) balls,
                   count(*) filter (where runs_batter = 4) fours, count(*) filter (where runs_batter = 6) sixes
            from deliveries where not is_super_over group by 1, 2, 3),
        outs as (
            select distinct match_id, innings_no, player_out_id person from deliveries
            where not is_super_over and player_out_id is not null and wicket_kind <> all(%s))
        select c.match_id, c.fs, c.person, coalesce(f.runs, 0), coalesce(f.balls, 0),
               coalesce(f.fours, 0), coalesce(f.sixes, 0), o.person is not null
        from crease c
        left join faced f using (match_id, innings_no, person)
        left join outs o using (match_id, innings_no, person)""", (list(NOT_OUT),))
    bats = [Bat(*r) for r in cur.fetchall()]

    cur.execute("""
        select match_id, bowling_fs_id, bowler_id,
               count(*) filter (where legal_ball),
               sum(runs_batter + extra_wides + extra_noballs),
               count(*) filter (where credited_to_bowler)
        from deliveries where not is_super_over group by 1, 2, 3, innings_no""")
    bowls = [Bowl(*r) for r in cur.fetchall()]

    cur.execute("""
        select match_id, innings_no, batting_fs_id, sum(runs_batter + runs_extras),
               count(*) filter (where player_out_id is not null and wicket_kind <> all(%s)),
               count(*) filter (where legal_ball)
        from deliveries where not is_super_over group by 1, 2, 3""", (list(NOT_OUT),))
    totals = [Total(*r) for r in cur.fetchall()]

    arch = Archive(fs, names, matches, bats, bowls, totals)
    _read_stages(arch, archive)
    return arch


def _read_stages(arch: Archive, archive: pathlib.Path) -> None:
    numbers: dict[int, set[int]] = defaultdict(set)
    with zipfile.ZipFile(archive) as z:
        for n in z.namelist():
            if not n.endswith(".json"):
                continue
            m = arch.matches.get(n[:-5])
            if m is None:
                continue
            event = json.loads(z.read(n))["info"].get("event", {})
            m.stage = event.get("stage")
            m.number = event.get("match_number")
            if m.stage is None and isinstance(m.number, int):
                numbers[m.year].add(m.number)
    for year, seen in numbers.items():
        missing = sorted(set(range(1, max(seen) + 1)) - seen)
        if missing:
            arch.gaps[year] = missing


# --------------------------------------------------------------------------- formatting

def esc(s) -> str:
    return html.escape(str(s), quote=True)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


COUNT_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
TIMES = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth"}


def words(n: int) -> str:
    return COUNT_WORDS[n] if 0 <= n < len(COUNT_WORDS) else f"{n:,}"


def overs(balls: int) -> str:
    return f"{balls // 6}.{balls % 6}" if balls % 6 else str(balls // 6)


def day(d: date) -> str:
    return f"{d.day} {d:%B %Y}"


def rate(num: float, den: float, places: int) -> str:
    return f"{num / den:.{places}f}" if den else "-"


def listing(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def times(n: int) -> str:
    return {1: "once", 2: "twice"}.get(n, f"{words(n)} times")


def poss(name: str) -> str:
    """'Mumbai Indians' 92', 'Virat Kohli's 113'."""
    return esc(name) + ("'" if name.endswith("s") else "'s")


def year_links(years: list[int]) -> str:
    return listing([f'<a href="/seasons/{y}">{y}</a>' for y in years])


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


# --------------------------------------------------------------------------- the record

class Record:
    """Everything the pages say, worked out once from the archive."""

    def __init__(self, arch: Archive):
        from web.crests import crest_url
        self.arch = arch
        self.crest_url = crest_url
        self.years = sorted({y for _, y, _ in arch.fs.values()})
        self.by_year: dict[int, list[Match]] = defaultdict(list)
        for m in sorted(arch.matches.values(), key=lambda m: (m.day, m.match_id)):
            self.by_year[m.year].append(m)
        self.franchises = sorted({f for f, _, _ in arch.fs.values()})
        self.fs_of: dict[tuple[str, int], int] = {(f, y): i for i, (f, y, _) in arch.fs.items()}
        self.totals_by_match: dict[str, list[Total]] = defaultdict(list)
        for t in arch.totals:
            self.totals_by_match[t.match_id].append(t)
        for ts in self.totals_by_match.values():
            ts.sort(key=lambda t: t.innings)

    # names
    def team(self, fs: int) -> str:
        return self.arch.fs[fs][2]

    def franchise(self, fs: int) -> str:
        return self.arch.fs[fs][0]

    def person(self, pid: str) -> str:
        return self.arch.names.get(pid, pid)

    def crest(self, fs: int, size: int = 22) -> str:
        name, year = self.arch.fs[fs][2], self.arch.fs[fs][1]
        url = self.crest_url(name, year)
        return (f'<img class="hx-crest" src="{url}" alt="" width="{size}" height="{size}" loading="lazy">'
                if url else "")

    def team_link(self, fs: int, crest: bool = True) -> str:
        f = self.franchise(fs)
        return (f'<a class="hx-team" href="/teams/{slug(f)}">{self.crest(fs) if crest else ""}'
                f'<span>{esc(self.team(fs))}</span></a>')

    # results
    def final(self, year: int) -> Match:
        return self.by_year[year][-1]

    def runner_up(self, m: Match) -> int | None:
        if m.winner is None:
            return None
        return m.b if m.winner == m.a else m.a

    def margin(self, m: Match) -> str:
        if m.result_type == "no result" or m.winner is None:
            return "no result"
        if m.result_type == "tie":
            return "in a super over after a tie"
        unit = "run" if m.result_type == "runs" else "wicket"
        text = f"by {plural(m.margin or 0, unit)}"
        return text + " (DLS method)" if m.decided_by == "dls" else text

    def score(self, t: Total) -> str:
        w = "" if t.wickets == 10 else f"/{t.wickets}"
        return f"{t.runs}{w} ({overs(t.balls)} ov)"

    def scoreline(self, m: Match) -> str:
        parts = [f"{esc(self.team(t.fs))} {self.score(t)}" for t in self.totals_by_match[m.match_id][:2]]
        return " &middot; ".join(parts)

    def titles_before(self, franchise: str, year: int) -> int:
        n = 0
        for y in self.years:
            if y > year:
                break
            w = self.final(y).winner
            if w is not None and self.franchise(w) == franchise:
                n += 1
        return n

    # season aggregates
    def batting(self, keep) -> list[dict]:
        acc: dict[tuple, dict] = {}
        for b in self.arch.bats:
            m = self.arch.matches[b.match_id]
            if not keep(m, b.fs):
                continue
            key = (b.person, self.franchise(b.fs))
            r = acc.setdefault(key, dict(person=b.person, fs=b.fs, inns=0, outs=0, runs=0, balls=0,
                                         fours=0, sixes=0, hs=(-1, False), fifties=0, hundreds=0,
                                         years=set()))
            r["inns"] += 1
            r["outs"] += b.out
            r["runs"] += b.runs
            r["balls"] += b.balls
            r["fours"] += b.fours
            r["sixes"] += b.sixes
            r["years"].add(m.year)
            r["fs"] = b.fs
            if (b.runs, not b.out) > r["hs"]:
                r["hs"] = (b.runs, not b.out)
            if b.runs >= 100:
                r["hundreds"] += 1
            elif b.runs >= 50:
                r["fifties"] += 1
        return sorted(acc.values(), key=lambda r: (-r["runs"], r["balls"], self.person(r["person"])))

    def bowling(self, keep) -> list[dict]:
        acc: dict[tuple, dict] = {}
        for b in self.arch.bowls:
            m = self.arch.matches[b.match_id]
            if not keep(m, b.fs):
                continue
            key = (b.person, self.franchise(b.fs))
            r = acc.setdefault(key, dict(person=b.person, fs=b.fs, balls=0, runs=0, wickets=0,
                                         best=(-1, 0), fours=0, years=set()))
            r["balls"] += b.balls
            r["runs"] += b.runs
            r["wickets"] += b.wickets
            r["years"].add(m.year)
            r["fs"] = b.fs
            if (b.wickets, -b.runs) > (r["best"][0], -r["best"][1]):
                r["best"] = (b.wickets, b.runs)
            if b.wickets >= 4:
                r["fours"] += 1
        rows = [r for r in acc.values() if r["balls"]]
        return sorted(rows, key=lambda r: (-r["wickets"], r["runs"] / r["balls"], self.person(r["person"])))

    def top_innings(self, keep, n: int = 5) -> list[Bat]:
        rows = [b for b in self.arch.bats if keep(self.arch.matches[b.match_id], b.fs)]
        return sorted(rows, key=lambda b: (-b.runs, b.balls, self.person(b.person)))[:n]

    def top_spells(self, keep, n: int = 5) -> list[Bowl]:
        rows = [b for b in self.arch.bowls if keep(self.arch.matches[b.match_id], b.fs) and b.wickets]
        return sorted(rows, key=lambda b: (-b.wickets, b.runs, self.person(b.person)))[:n]

    def opponent(self, match_id: str, fs: int) -> int:
        m = self.arch.matches[match_id]
        return m.b if m.a == fs else m.a

    def table(self, year: int) -> list[dict]:
        rows: dict[int, dict] = {}
        for fs, (_, y, _) in self.arch.fs.items():
            if y == year:
                rows[fs] = dict(fs=fs, p=0, w=0, l=0, nr=0, pts=0, playoffs=False)
        for m in self.by_year[year]:
            if m.stage is not None:
                for side in (m.a, m.b):
                    rows[side]["playoffs"] = True
                continue
            for side in (m.a, m.b):
                r = rows[side]
                r["p"] += 1
                if m.winner is None:
                    r["nr"] += 1
                    r["pts"] += 1
                elif m.winner == side:
                    r["w"] += 1
                    r["pts"] += 2
                else:
                    r["l"] += 1
        # Level on points, a side that went through was ahead on net run rate, which is the
        # one thing this table cannot compute; ordering it first keeps the table honest.
        return sorted(rows.values(), key=lambda r: (-r["pts"], not r["playoffs"], -r["w"], self.team(r["fs"])))

    def finish(self, fs: int) -> str:
        year = self.arch.fs[fs][1]
        f = self.final(year)
        if f.winner == fs:
            return "Champions"
        if self.runner_up(f) == fs:
            return "Runners-up"
        if any(m.stage and fs in (m.a, m.b) for m in self.by_year[year]):
            return "Playoffs"
        return "League stage"


# --------------------------------------------------------------------------- page shell

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="google-adsense-account" content="ca-pub-2624074910535553">
<title>{title} · Fine Leg XI</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{origin}{path}">
<link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32.png">
<link rel="icon" type="image/png" sizes="512x512" href="/static/favicon-512.png">
<link rel="apple-touch-icon" sizes="180x180" href="/static/favicon-180.png">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#050b1a">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Fine Leg XI">
<meta property="og:title" content="Fine Leg XI">
<meta property="og:description" content="{description}">
<meta property="og:image" content="{origin}/static/og-image.png">
<meta property="og:type" content="article">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{origin}/static/og-image.png">
<link rel="preload" href="/static/fonts/Archivo.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/static/fonts/Anton.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="/static/fonts.css">
<link rel="stylesheet" href="/static/style.css">
<link rel="stylesheet" href="/static/crests.css">
</head>
<body>
<!-- shell:nav -->
<!-- /shell:nav -->
<div class="sheet">
  <article class="prose-page hx">
    <div class="report">
      <div class="over-line">{over}</div>
      <h1 class="call">{heading}</h1>
    </div>
{body}
  </article>

  <!-- shell:auth -->
<!-- /shell:auth -->

  <!-- shell:footer -->
<!-- /shell:footer -->
</div>

<script src="/static/common.js"></script>
<script src="/static/auth.js"></script>
<script src="/static/info.js"></script>
</body>
</html>
"""


def finish_page(text: str) -> str:
    """Fill the shared shell and stamp every asset, exactly as `tools.shell` and
    `tools.stamp_assets` would, so both gates pass on a page straight out of here."""
    from tools.shell import _block
    from tools.stamp_assets import stamp_text
    for p in PARTIALS.glob("*.html"):
        body = p.read_text()
        text = _block(p.stem).sub(lambda m: m.group(1) + body + m.group(3), text)
    return stamp_text(text)


def page(path: str, title: str, description: str, over: str, heading: str, body: str) -> str:
    return finish_page(PAGE.format(title=esc(title), description=esc(description), origin=ORIGIN,
                                   path=path, over=esc(over), heading=esc(heading), body=body))


def table(head: list[str], rows: list[list[str]], num_from: int = 1, cls: str = "") -> str:
    """`num_from`: columns from this index on are numbers, right-aligned."""
    th = "".join(f'<th{" class=n" if i >= num_from else ""}>{h}</th>' for i, h in enumerate(head))
    body = "".join("<tr>" + "".join(f'<td{" class=n" if i >= num_from else ""}>{c}</td>'
                                    for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="hx-scroll"><table class="hx-table {cls}"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def section(heading: str, inner: str) -> str:
    return f'    <section class="hx-sec">\n      <h2>{heading}</h2>\n      {inner}\n    </section>\n'


def para(*sentences: str) -> str:
    return "<p>" + " ".join(s for s in sentences if s) + "</p>"


# --------------------------------------------------------------------------- season pages

def bat_rows(rec: Record, rows: list[dict], team: bool = True) -> list[list[str]]:
    out = []
    for i, r in enumerate(rows, 1):
        hs, notout = r["hs"]
        cells = [f"{i}. {esc(rec.person(r['person']))}"]
        if team:
            cells.append(rec.team_link(r["fs"]))
        cells += [f"<b>{r['runs']:,}</b>", str(r["inns"]), rate(r["runs"], r["outs"], 2),
                  rate(100 * r["runs"], r["balls"], 1), f"{hs}{'*' if notout else ''}",
                  f"{r['hundreds']}/{r['fifties']}", str(r["sixes"])]
        out.append(cells)
    return out


def bowl_rows(rec: Record, rows: list[dict], team: bool = True) -> list[list[str]]:
    out = []
    for i, r in enumerate(rows, 1):
        cells = [f"{i}. {esc(rec.person(r['person']))}"]
        if team:
            cells.append(rec.team_link(r["fs"]))
        cells += [f"<b>{r['wickets']}</b>", overs(r["balls"]), rate(6 * r["runs"], r["balls"], 2),
                  rate(r["runs"], r["wickets"], 2), f"{r['best'][0]}/{r['best'][1]}"]
        out.append(cells)
    return out


BAT_HEAD = ["Player", "Team", "Runs", "Inns", "Avg", "SR", "HS", "100/50", "6s"]
BOWL_HEAD = ["Player", "Team", "Wkts", "Overs", "Econ", "Avg", "Best"]


def leader_sentence(rec: Record, rows: list[dict], what: str) -> str:
    """'X scored the most runs' only when nobody matched him; a share is said as one."""
    if not rows:
        return ""
    key = "runs" if what == "runs" else "wickets"
    top = [r for r in rows if r[key] == rows[0][key]]
    names = [f"{esc(rec.person(r['person']))} ({esc(rec.team(r['fs']))})" for r in top]
    r = rows[0]
    if what == "runs":
        extra = (", including a hundred" if r["hundreds"] == 1 else
                 f", including {r['hundreds']} hundreds" if r["hundreds"] else "")
        if len(top) == 1:
            return (f"{names[0]} scored the most runs, {r['runs']:,} at a strike rate of "
                    f"{rate(100 * r['runs'], r['balls'], 1)}{extra}.")
        return f"{listing(names)} shared the most runs, {r['runs']:,} each."
    if len(top) == 1:
        return (f"{names[0]} took the most wickets, {r['wickets']} at an economy rate of "
                f"{rate(6 * r['runs'], r['balls'], 2)}.")
    return (f"{listing(names)} took {r['wickets']} wickets each, the most of the season; the "
            f"official Purple Cap goes to the more economical bowler on a tie.")


def season_page(rec: Record, year: int) -> str:
    ms = rec.by_year[year]
    teams = sorted((fs for fs, (_, y, _) in rec.arch.fs.items() if y == year), key=rec.team)
    keep = lambda m, fs: m.year == year
    bats, bowls = rec.batting(keep), rec.bowling(keep)
    f = rec.final(year)
    champ, runner = f.winner, rec.runner_up(f)
    edition = year - 2007

    # Who was new, who was missing, who had a new name.
    prev = {rec.franchise(fs): rec.team(fs) for fs, (_, y, _) in rec.arch.fs.items() if y == year - 1}
    now = {rec.franchise(fs): rec.team(fs) for fs in teams}
    ever_before = {fr for fr, y, _ in rec.arch.fs.values() if y < year}
    debut = sorted(n for fr, n in now.items() if fr not in ever_before)
    back = sorted(n for fr, n in now.items() if fr in ever_before and fr not in prev)
    gone = sorted(n for fr, n in prev.items() if fr not in now)
    renamed = sorted(f"{prev[fr]} became {n}" for fr, n in now.items() if fr in prev and prev[fr] != n)

    cities = Counter(m.city for m in ms if m.city)
    grounds = Counter(m.venue for m in ms if m.venue)
    ground, ground_n = grounds.most_common(1)[0]
    totals = [t for m in ms for t in rec.totals_by_match[m.match_id]]
    firsts = [rec.totals_by_match[m.match_id][0] for m in ms if rec.totals_by_match[m.match_id]]
    high = max(totals, key=lambda t: (t.runs, -t.wickets))
    allout = [t for t in totals if t.wickets == 10]
    low = min(allout, key=lambda t: t.runs) if allout else None
    sixes = sum(b.sixes for b in rec.arch.bats if rec.arch.matches[b.match_id].year == year)
    fours = sum(b.fours for b in rec.arch.bats if rec.arch.matches[b.match_id].year == year)
    hundreds = sum(1 for b in rec.arch.bats if rec.arch.matches[b.match_id].year == year and b.runs >= 100)
    inn = rec.top_innings(keep)
    spells = rec.top_spells(keep)
    table_rows = rec.table(year)

    # ---- the story
    span = f"between {day(ms[0].day)} and {day(ms[-1].day)}"
    intro = para(
        f"The {year} Indian Premier League was the {ordinal(edition)} season of the competition.",
        f"{words(len(teams)).capitalize()} teams played the {len(ms)} matches in the ball-by-ball "
        f"record {span}, at {plural(len(grounds), 'ground')} in {plural(len(cities), 'city', 'cities')}; "
        f"{esc(ground)} staged the most, {ground_n}.")
    story = []
    if champ is not None:
        n = rec.titles_before(rec.franchise(champ), year)
        nth = "their first title" if n == 1 else f"their {TIMES.get(n, ordinal(n))} title"
        venue = f" at {esc(f.venue)}" if f.venue else ""
        story.append(para(
            f"<strong>{esc(rec.team(champ))}</strong> won {nth}, beating {esc(rec.team(runner))} "
            f"{rec.margin(f)} in the final{venue} on {day(f.day)}.",
            f"The final finished {rec.scoreline(f)}.",
            f"{esc(rec.person(f.pom))} was named Player of the Match." if f.pom else ""))
    story.append(para(leader_sentence(rec, bats, "runs"), leader_sentence(rec, bowls, "wickets")))
    league = [r for r in table_rows if r["p"]]
    top = [r for r in league if r["pts"] == league[0]["pts"]]
    tops = listing([esc(rec.team(r["fs"])) for r in top])
    lead = (f"{tops} topped the league stage with {league[0]['pts']} points" if len(top) == 1 else
            f"{tops} finished level at the top of the league stage on {league[0]['pts']} points")
    qual = [esc(rec.team(r["fs"])) for r in table_rows if r["playoffs"]]
    story.append(para(
        f"{lead}; {listing(qual)} were the {words(len(qual))} sides in the knockouts." if qual else f"{lead}.",
        f"The highest total of the season was {poss(rec.team(high.fs))} {high.runs}"
        f"{'' if high.wickets == 10 else '/' + str(high.wickets)} against "
        f"{esc(rec.team(rec.opponent(high.match_id, high.fs)))}"
        + (f", and the lowest all-out total {poss(rec.team(low.fs))} {low.runs}." if low else "."),
        f"Batters hit {sixes:,} sixes and {fours:,} fours, and there were {plural(hundreds, 'hundred')}",
        f"in all; the average first-innings score was "
        f"{sum(t.runs for t in firsts) / len(firsts):.0f}."))
    changes = []
    if edition == 1:
        changes.append(f"The eight founding sides were {listing([esc(n) for n in sorted(now.values())])}.")
    else:
        if debut:
            changes.append(f"{listing([esc(n) for n in debut])} played {'its' if len(debut) == 1 else 'their'} first season.")
        if back:
            changes.append(f"{listing([esc(n) for n in back])} returned after time away.")
        if gone:
            changes.append(f"{listing([esc(n) for n in gone])} did not take part.")
        if renamed:
            changes.append(f"{listing([esc(r) for r in renamed])}.")
    if changes:
        story.append(para(*changes))

    body = section("The season", intro + "".join(story))

    # ---- the table
    note = ("Ordered by points, then wins. The official table also used net run rate, which is "
            "not shown here, so two sides level on points may appear in either order.")
    if year in rec.arch.gaps:
        miss = rec.arch.gaps[year]
        note += (f" {plural(len(miss), 'league match', 'league matches')} (number "
                 f"{listing([str(n) for n in miss])}) {'was' if len(miss) == 1 else 'were'} abandoned "
                 f"without a ball and {'is' if len(miss) == 1 else 'are'} not in the archive; each gave "
                 f"both sides a point in the official table, which is not counted here.")
    rows = [[f"{i}. " + rec.team_link(r["fs"]), str(r["p"]), str(r["w"]), str(r["l"]), str(r["nr"]),
             f"<b>{r['pts']}</b>", "Q" if r["playoffs"] else ""] for i, r in enumerate(table_rows, 1)]
    body += section("League stage",
                    table(["Team", "P", "W", "L", "NR", "Pts", ""], rows) + f'<p class="hx-note">{note}</p>')

    # ---- playoffs
    ko = [m for m in ms if m.stage is not None]
    if ko:
        rows = []
        for m in ko:
            w = esc(rec.team(m.winner)) if m.winner else "No result"
            rows.append([esc(m.stage), day(m.day), rec.scoreline(m),
                         f"{w} won {rec.margin(m)}" if m.winner else w])
        body += section("Knockouts", table(["Stage", "Date", "Scores", "Result"], rows, num_from=9))

    # ---- leaders
    body += section(f"Most runs in {year}", table(BAT_HEAD, bat_rows(rec, bats[:TOP]), num_from=2))
    body += section(f"Most wickets in {year}", table(BOWL_HEAD, bowl_rows(rec, bowls[:TOP]), num_from=2))
    rows = [[f"{esc(rec.person(b.person))}", rec.team_link(b.fs),
             f"<b>{b.runs}{'' if b.out else '*'}</b>", str(b.balls),
             esc(rec.team(rec.opponent(b.match_id, b.fs)))] for b in inn]
    body += section("Highest innings", table(["Player", "Team", "Runs", "Balls", "Against"], rows, num_from=2))
    rows = [[f"{esc(rec.person(b.person))}", rec.team_link(b.fs), f"<b>{b.wickets}/{b.runs}</b>",
             overs(b.balls), esc(rec.team(rec.opponent(b.match_id, b.fs)))] for b in spells]
    body += section("Best bowling", table(["Player", "Team", "Figures", "Overs", "Against"], rows, num_from=2))

    # ---- squads in the game
    links = "".join(f"<li>{rec.team_link(fs)} &mdash; {rec.finish(fs).lower()}</li>" for fs in teams)
    body += section("The teams", f'<ul class="hx-list">{links}</ul>' + para(
        f"Every squad from {year} is in the deck of <a href=\"/draft\">the draft</a>, where you pick a "
        f"twelve from real franchise-seasons and play a season of your own with it, and in "
        f"<a href=\"/auction\">the auction</a>. How each player's card is rated is explained in "
        f"<a href=\"/guides/ratings\">how the ratings work</a>."))

    nav = []
    if year - 1 in rec.by_year:
        nav.append(f'<a class="act minor" href="/seasons/{year - 1}">&larr; {year - 1}</a>')
    nav.append('<a class="act minor" href="/seasons">All seasons</a>')
    if year + 1 in rec.by_year:
        nav.append(f'<a class="act minor" href="/seasons/{year + 1}">{year + 1} &rarr;</a>')
    body += f'    <div class="foot-actions hx-pager">{"".join(nav)}</div>\n'

    champ_txt = f"{rec.team(champ)} won the title. " if champ else ""
    desc = (f"IPL {year}: {champ_txt}The final, the league table, the knockouts and the season's "
            f"leading run-scorers and wicket-takers, from the ball-by-ball record.")
    return page(f"/seasons/{year}", f"IPL {year} season", desc, "IPL HISTORY", f"IPL {year}", body)


def seasons_index(rec: Record) -> str:
    rows = []
    for y in rec.years:
        f = rec.final(y)
        keep = lambda m, fs, y=y: m.year == y
        bat, bowl = rec.batting(keep)[0], rec.bowling(keep)[0]
        rows.append([f'<a href="/seasons/{y}"><b>{y}</b></a>',
                     rec.team_link(f.winner) if f.winner else "No result",
                     rec.team_link(rec.runner_up(f)) if f.winner else "",
                     f"{esc(rec.person(bat['person']))} {bat['runs']}",
                     f"{esc(rec.person(bowl['person']))} {bowl['wickets']}"])
    champs = Counter(rec.franchise(rec.final(y).winner) for y in rec.years if rec.final(y).winner)
    most = champs.most_common()
    best = [fr for fr, n in most if n == most[0][1]]
    intro = para(
        f"The Indian Premier League has run for {len(rec.years)} seasons, from {rec.years[0]} to "
        f"{rec.years[-1]}, and {words(len(champs))} different franchises have won it.",
        f"{listing([esc(b) for b in best])} {'has' if len(best) == 1 else 'have'} won the most titles, "
        f"{most[0][1]} {'each' if len(best) > 1 else ''}.".replace(" .", "."),
        "Each season below has its own page: the final and how it was won, the league table, every "
        "knockout match, and the leading run-scorers and wicket-takers, all counted from the same "
        "ball-by-ball record the game is built on.")
    body = section("Nineteen seasons", intro)
    body += section("Every final", table(["Season", "Champions", "Runners-up", "Most runs", "Most wickets"],
                                          rows, num_from=9))
    body += section("Counting rules", para(
        "Runs, balls and wickets are counted from Cricsheet's ball-by-ball data. Super overs count for "
        "the result only. A ball faced is any ball that was not a wide, so a no-ball counts. A bowler is "
        "charged runs off the bat, wides and no-balls, never byes or leg-byes, and is credited only the "
        "wickets that are his, so a run out is not. Matches abandoned without a ball being bowled are not "
        "in the record. Figures can therefore differ slightly from other sources that count differently."))
    return page("/seasons", "Every IPL season", "Every IPL season from 2008 to 2026: champions, runners-up "
                "and the leading run-scorer and wicket-taker of each, with a page for every season.",
                "IPL HISTORY", "Every season", body)


# --------------------------------------------------------------------------- team pages

def team_page(rec: Record, franchise: str) -> str:
    seasons = sorted((rec.arch.fs[fs][1], fs) for fs in rec.arch.fs if rec.franchise(fs) == franchise)
    years = [y for y, _ in seasons]
    fs_ids = {fs for _, fs in seasons}
    names: list[tuple[str, int, int]] = []
    for y, fs in seasons:
        n = rec.team(fs)
        if names and names[-1][0] == n:
            names[-1] = (n, names[-1][1], y)
        else:
            names.append((n, y, y))
    keep = lambda m, fs: fs in fs_ids
    played = [m for m in rec.arch.matches.values() if m.a in fs_ids or m.b in fs_ids]
    won = sum(1 for m in played if m.winner in fs_ids)
    nr = sum(1 for m in played if m.winner is None)
    lost = len(played) - won - nr
    ties = sum(1 for m in played if m.result_type == "tie")
    titles = [y for y, fs in seasons if rec.final(y).winner == fs]
    finals = [y for y, fs in seasons if rec.runner_up(rec.final(y)) == fs]
    playoffs = [y for y, fs in seasons if rec.finish(fs) != "League stage"]
    bats, bowls = rec.batting(keep), rec.bowling(keep)
    inn, spells = rec.top_innings(keep, 5), rec.top_spells(keep, 5)
    totals = [t for t in rec.arch.totals if t.fs in fs_ids]
    high = max(totals, key=lambda t: (t.runs, -t.wickets))
    cities = Counter(m.city for m in played if m.city)
    city, city_n = cities.most_common(1)[0]
    latest = seasons[-1][1]

    # ---- the story
    span_years = [y for y in range(years[0], years[-1] + 1)]
    missed = [y for y in span_years if y not in years and y in rec.by_year]
    name_txt = listing([f"{esc(n)} ({a}{'' if a == b else '&ndash;' + str(b)})" for n, a, b in names])
    current = years[-1] == rec.years[-1]
    have = "have " if current else ""
    intro = [f"{esc(franchise)} {have}played a single season of the IPL, in {years[0]}." if len(years) == 1 else
             f"{esc(franchise)} {have}played {plural(len(years), 'season')} of the IPL, from {years[0]} "
             f"to {years[-1]}."]
    if len(names) > 1:
        intro.append(f"The franchise has played as {name_txt}.")
    if missed:
        intro.append(f"They did not take part in {listing([str(y) for y in missed])}.")
    if not current:
        intro.append(f"The franchise no longer plays.")
    intro.append(f"Across {plural(len(played), 'match', 'matches')} in the ball-by-ball record they won "
                 f"{won} and lost {lost}" + (f", with {plural(nr, 'no result')}" if nr else "") +
                 f", winning {100 * won / max(1, won + lost):.0f}% of the matches that had a result" +
                 (f"; {words(ties)} of those wins and losses came in a super over after a tie." if ties else "."))
    honours = []
    if titles:
        honours.append(f"They {have}won the title {times(len(titles))} "
                       f"({year_links(titles)})")
        if finals:
            honours[-1] += f" and lost {words(len(finals))} {'final' if len(finals) == 1 else 'finals'} " \
                           f"({year_links(finals)})"
        honours[-1] += "."
    elif finals:
        honours.append(f"They {'have never won the title, and have been' if current else 'never won the title, and were'} runners-up "
                       f"{times(len(finals))} "
                       f"({year_links(finals)})).")
    else:
        honours.append("They never reached a final." if not current else "They have never reached a final.")
    honours.append(f"They reached the knockouts in {plural(len(playoffs), 'season')} of {len(years)}." if len(years) > 1
                   else ("They reached the knockouts." if playoffs else "They did not reach the knockouts."))
    best = max(seasons, key=lambda s: (sum(1 for m in rec.by_year[s[0]] if m.winner == s[1]), -s[0]))
    bw = sum(1 for m in rec.by_year[best[0]] if m.winner == best[1])
    bp = sum(1 for m in rec.by_year[best[0]] if best[1] in (m.a, m.b))
    if len(years) > 1:
        honours.append(f"Their most wins in a season came in <a href=\"/seasons/{best[0]}\">{best[0]}</a>, "
                       f"{bw} from {bp} matches.")
    people = []
    if bats:
        b = bats[0]
        across = f" in {plural(len(b['years']), 'season')}" if len(years) > 1 else ""
        people.append(f"Their leading run-scorer is {esc(rec.person(b['person']))}, with {b['runs']:,} runs"
                      f"{across} at a strike rate of "
                      f"{rate(100 * b['runs'], b['balls'], 1)}.")
    if bowls:
        b = bowls[0]
        people.append(f"{esc(rec.person(b['person']))} {'has taken' if current else 'took'} the most wickets for them, {b['wickets']}.")
    if inn:
        i = inn[0]
        people.append(f"The highest score made for the franchise is {poss(rec.person(i.person))} "
                      f"{i.runs}{'' if i.out else '*'} against "
                      f"{esc(rec.team(rec.opponent(i.match_id, i.fs)))} in {rec.arch.matches[i.match_id].year},"
                      f" and their highest total {high.runs}{'' if high.wickets == 10 else '/' + str(high.wickets)}"
                      f" against {esc(rec.team(rec.opponent(high.match_id, high.fs)))} in "
                      f"{rec.arch.matches[high.match_id].year}.")
    people.append(f"They {have}played more matches in {esc(city)} than anywhere else, {city_n}.")
    body = section("The franchise", para(*intro) + para(*honours) + para(*people))

    # ---- season by season
    rows = []
    for y, fs in seasons:
        mine = [m for m in rec.by_year[y] if fs in (m.a, m.b)]
        w = sum(1 for m in mine if m.winner == fs)
        n = sum(1 for m in mine if m.winner is None)
        keep_y = lambda m, f2, y=y, fs=fs: m.year == y and f2 == fs
        top_bat = rec.batting(keep_y)
        top_bowl = rec.bowling(keep_y)
        rows.append([f'<a href="/seasons/{y}"><b>{y}</b></a>', rec.crest(fs) + esc(rec.team(fs)),
                     f"{w}&ndash;{len(mine) - w - n}" + (f"&ndash;{n}" if n else ""), rec.finish(fs),
                     f"{esc(rec.person(top_bat[0]['person']))} {top_bat[0]['runs']}" if top_bat else "",
                     f"{esc(rec.person(top_bowl[0]['person']))} {top_bowl[0]['wickets']}" if top_bowl else ""])
    body += section("Season by season", table(["Season", "Played as", "W&ndash;L", "Finish", "Most runs",
                                                 "Most wickets"], rows, num_from=9))
    head = BAT_HEAD[:1] + ["Seasons"] + BAT_HEAD[2:]
    rows = [[r[0], str(len(b["years"]))] + r[1:] for r, b in zip(bat_rows(rec, bats[:TOP], team=False), bats)]
    body += section("Most runs for the franchise", table(head, rows, num_from=1))
    head = BOWL_HEAD[:1] + ["Seasons"] + BOWL_HEAD[2:]
    rows = [[r[0], str(len(b["years"]))] + r[1:] for r, b in zip(bowl_rows(rec, bowls[:TOP], team=False), bowls)]
    body += section("Most wickets for the franchise", table(head, rows, num_from=1))
    rows = [[esc(rec.person(b.person)), f"<b>{b.runs}{'' if b.out else '*'}</b>", str(b.balls),
             esc(rec.team(rec.opponent(b.match_id, b.fs))), str(rec.arch.matches[b.match_id].year)] for b in inn]
    body += section("Highest innings", table(["Player", "Runs", "Balls", "Against", "Season"], rows))
    rows = [[esc(rec.person(b.person)), f"<b>{b.wickets}/{b.runs}</b>", overs(b.balls),
             esc(rec.team(rec.opponent(b.match_id, b.fs))), str(rec.arch.matches[b.match_id].year)] for b in spells]
    body += section("Best bowling", table(["Player", "Figures", "Overs", "Against", "Season"], rows))
    body += section("In the game", para(
        f"Every {esc(franchise)} squad, all {plural(len(years), 'season')} of them, is in the deck of "
        f"<a href=\"/draft\">the draft</a> and <a href=\"/auction\">the auction</a>, rated by "
        f"<a href=\"/guides/ratings\">the method explained here</a>."))
    body += '    <div class="foot-actions hx-pager"><a class="act minor" href="/teams">All franchises</a>' \
            '<a class="act minor" href="/seasons">All seasons</a></div>\n'
    t_txt = f", {len(titles)} {'title' if len(titles) == 1 else 'titles'}" if titles else ""
    desc = (f"{franchise} in the IPL: {len(years)} seasons{t_txt}, every season's record, and the "
            f"franchise's leading run-scorers and wicket-takers from the ball-by-ball record.")
    over = f'IPL FRANCHISE'
    return page(f"/teams/{slug(franchise)}", f"{franchise} IPL history", desc, over, rec.team(latest), body)


def teams_index(rec: Record) -> str:
    rows = []
    for fr in sorted(rec.franchises, key=lambda f: (-sum(1 for fs in rec.arch.fs if rec.franchise(fs) == f), f)):
        seasons = sorted((rec.arch.fs[fs][1], fs) for fs in rec.arch.fs if rec.franchise(fs) == fr)
        titles = [y for y, fs in seasons if rec.final(y).winner == fs]
        latest = seasons[-1][1]
        rows.append([f'<a class="hx-team" href="/teams/{slug(fr)}">{rec.crest(latest)}<span>{esc(fr)}</span></a>',
                     str(seasons[0][0]) if len(seasons) == 1 else f"{seasons[0][0]}&ndash;{seasons[-1][0]}",
                     str(len(seasons)), str(len(titles)),
                     ", ".join(str(y) for y in titles)])
    body = section("Fifteen franchises", para(
        f"{len(rec.franchises)} franchises have played in the IPL since 2008. Some have changed their "
        "names, some have changed their crests, and several no longer play; each is counted here as one "
        "club across every name it has used, so Delhi Daredevils and Delhi Capitals are the same "
        "franchise, as are Kings XI Punjab and Punjab Kings.",
        "Each franchise page has its full season-by-season record, its titles and finals, and its "
        "leading run-scorers and wicket-takers."))
    body += section("Every franchise", table(["Franchise", "Years", "Seasons", "Titles", "Won in"], rows, num_from=2))
    renames, defunct = [], []
    for fr in rec.franchises:
        seasons = sorted((rec.arch.fs[fs][1], rec.team(fs)) for fs in rec.arch.fs if rec.franchise(fs) == fr)
        for (y0, n0), (y1, n1) in zip(seasons, seasons[1:]):
            if n0 != n1:
                renames.append(f"{esc(n0)} became {esc(n1)} in {y1}")
        if seasons[-1][0] != rec.years[-1]:
            last = seasons[-1][0]
            span = str(last) if seasons[0][0] == last else f"{seasons[0][0]} to {last}"
            defunct.append(f"<a href=\"/teams/{slug(fr)}\">{esc(fr)}</a> ({span})")
    current = sorted({rec.franchise(fs) for fs, (_, y, _) in rec.arch.fs.items() if y == rec.years[-1]})
    stats = []
    for fr in rec.franchises:
        fs_ids = {fs for fs in rec.arch.fs if rec.franchise(fs) == fr}
        ms = [m for m in rec.arch.matches.values() if m.a in fs_ids or m.b in fs_ids]
        won = sum(1 for m in ms if m.winner in fs_ids)
        decided = sum(1 for m in ms if m.winner is not None)
        finals = sum(1 for y in rec.years if {rec.final(y).a, rec.final(y).b} & fs_ids)
        stats.append((fr, len(ms), won, decided, finals))
    regular = [x for x in stats if x[1] >= 100]
    best = max(regular, key=lambda x: x[2] / x[3])
    worst = min(regular, key=lambda x: x[2] / x[3])
    most_finals = max(stats, key=lambda x: x[4])
    most_played = max(stats, key=lambda x: x[1])
    body += section("Who has done best", para(
        f"{esc(most_played[0])} have played the most matches in the ball-by-ball record, {most_played[1]}.",
        f"Among franchises with at least a hundred matches, {esc(best[0])} have the best record, winning "
        f"{100 * best[2] / best[3]:.0f}% of the matches that had a result, and {esc(worst[0])} the weakest, "
        f"{100 * worst[2] / worst[3]:.0f}%.",
        f"{esc(most_finals[0])} have played in the most finals, {most_finals[4]}.",
        "A title is not the whole story: several sides have topped the league stage and then lost in the "
        "knockouts, which is why each franchise page lists every season's finish as well as its trophies."))
    body += section("Names and changes", para(
        f"{words(len(current)).capitalize()} franchises play today: {listing([esc(c) for c in current])}.",
        f"Over the years {listing(renames)}." if renames else "",
        f"{words(len(defunct)).capitalize()} franchises no longer play: {listing(defunct)}." if defunct else "",
        "A franchise's crest on these pages is the one it wore in that season, so the old Delhi "
        "Daredevils and Kings XI Punjab crests appear in the years they were used."))
    return page("/teams", "Every IPL franchise", "Every IPL franchise since 2008, with seasons played, "
                "titles won and a full history page for each.", "IPL HISTORY", "Every franchise", body)


# --------------------------------------------------------------------------- build

def build(rec: Record) -> dict[pathlib.Path, str]:
    out = {STATIC / "seasons.html": seasons_index(rec), STATIC / "teams.html": teams_index(rec)}
    for y in rec.years:
        out[SEASON_DIR / f"{y}.html"] = season_page(rec, y)
    for f in rec.franchises:
        out[TEAM_DIR / f"{slug(f)}.html"] = team_page(rec, f)
    return out


def main(check: bool = False) -> int:
    with _connect() as conn:
        rec = Record(load(conn))
    pages = build(rec)
    existing = set(SEASON_DIR.glob("*.html")) | set(TEAM_DIR.glob("*.html"))
    stale = sorted(str(p.relative_to(STATIC)) for p, text in pages.items()
                   if not p.exists() or p.read_text() != text)
    extra = sorted(str(p.relative_to(STATIC)) for p in existing - set(pages))
    if check:
        for s in stale:
            print(f"stale: {s}")
        for s in extra:
            print(f"not generated (delete it): {s}")
        if not stale and not extra:
            print(f"history pages current ({len(pages)} pages)")
        return 1 if stale or extra else 0
    SEASON_DIR.mkdir(exist_ok=True)
    TEAM_DIR.mkdir(exist_ok=True)
    for p, text in pages.items():
        if not p.exists() or p.read_text() != text:
            p.write_text(text)
    for s in extra:
        (STATIC / s).unlink()
    print(f"wrote {len(stale)} of {len(pages)} history pages" + (f", removed {len(extra)}" if extra else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(check="--check" in sys.argv))
