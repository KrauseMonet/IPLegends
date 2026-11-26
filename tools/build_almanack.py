"""Write the Almanack: a front page, an index of every player and a page per player, from the archive.

    uv run python -m tools.build_almanack           # (re)write web/static/almanack*, almanack/players/
    uv run python -m tools.build_almanack --check   # exit 1 if a committed page is stale

WHY [A315]. The site's record pages (`tools.build_history`) cover seasons and franchises; this
covers the PLAYERS, which is where a cricket reader actually looks something up. It is plain
HTML for the same reason those pages are (AdSense judged a site drawn by JavaScript to be
"low-value content"), and it leads with what other stats sites do not print: a player's
figures in each phase and against pace and spin measured against the LEAGUE IN THE SAME
SEASONS (runs above par), how he gets out, where he batted, the bowlers and batters he met
most, and the card this game rates each season at.

A page is indexed only for a player with `INDEX_MATCHES` or more matches; the rest carry
`noindex`, because several hundred near-empty pages built from one template is exactly what
Google calls scaled, low-value content, and would undo the point of the section.

THE RULES are `tools.build_history`'s, so a figure here agrees with the season pages:
super overs count for nothing; a ball faced is any ball that was not a wide (A22); a bowler is
charged runs off the bat, wides and no-balls, never byes or leg-byes (A1), and credited only
his own wickets (A36); retired hurt is not a dismissal. Splits by phase and by bowling style
count the STRIKER's dismissals only (A36), so a non-striker run out is in the dismissal table
but not in a phase. A match is one the player was NAMED in or took part in (`named_in_squad or
participated`), which is how a scorecard counts: Kohli reads 266 to 2025 against a published
267, where batting-or-bowling alone gave 262. Named lists from 2023 carry twelve, and in all 551
such sides the twelfth did come on as the Impact Player, so nobody named sat the match out.

No sentence on these pages gives a real player a pronoun: the name, or a noun, does the work.
"""

from __future__ import annotations

import hashlib
import pathlib
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from tools import almanack_auctions as AU
from tools import almanack_records as R2
from tools import build_history as H
from tools.build_history import esc, overs, para, plural, rate, section, slug, table

STATIC = H.STATIC
ALMANACK = STATIC / "almanack.html"
PLAYERS_INDEX = STATIC / "almanack" / "players.html"
PLAYER_DIR = STATIC / "almanack" / "players"
GROUND_DIR = STATIC / "almanack" / "grounds"
AUCTION_DIR = STATIC / "almanack" / "auctions"

INDEX_MATCHES = 20      # a player page is indexed from this many matches (Phase 0: 343 players)
H2H_MIN_BALLS = 12      # a head-to-head row on a PLAYER page needs this many balls between the two
H2H_LOAD_BALLS = 6      # pairs loaded at all: the searchable file lists down to this (A316)
H2H_ROWS = 6            # rows in each head-to-head table
BEST_ROWS = 5           # best innings / best spells listed
TOP_ALL_TIME = 10       # rows in the front page's all-time tables
PHASES = ("powerplay", "middle", "death")
PHASE_LABEL = {"powerplay": "Powerplay (overs 1-6)", "middle": "Middle (7-15)", "death": "Death (16-20)"}
STYLES = ("pace", "spin")
OUT_KINDS = {"retired hurt"}   # every other kind is a dismissal, as in build_history


def player_slug(name: str) -> str:
    """'Morné Morkel' -> 'morne-morkel': accents folded first, so no letter is lost."""
    return slug(unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode())


def player_path(name: str) -> str:
    return f"/almanack/players/{player_slug(name)}"


# --------------------------------------------------------------------------- the extra record

@dataclass
class Split:
    """One player's balls in some slice, with what the league did in that slice in the same
    seasons, weighted by his own balls in each (so par is HIS mix of seasons, not the archive's)."""
    runs: int = 0
    balls: int = 0
    outs: int = 0
    par_runs: float = 0.0
    par_outs: float = 0.0

    def above_par(self) -> float | None:   # runs above par per 100 balls
        return 100 * (self.runs - self.par_runs) / self.balls if self.balls else None


@dataclass
class People:
    meta: dict[str, dict]                                   # person -> nationality, style, keeper
    role: dict[str, str]                                    # person -> commonest squad role
    matches: dict[tuple[str, int], int]                     # (person, fs) -> matches played
    pom: Counter                                            # person -> Player of the Match awards
    bat_phase: dict[str, dict[str, Split]]
    bowl_phase: dict[str, dict[str, Split]]
    bat_style: dict[str, dict[str, Split]]
    out_kinds: dict[str, Counter] = field(default_factory=dict)       # how he was out
    wicket_kinds: dict[str, Counter] = field(default_factory=dict)    # how his wickets fell
    h2h: list[tuple[str, str, int, int, int]] = field(default_factory=list)  # batter, bowler, balls, runs, outs
    positions: dict[str, dict[int, int]] = field(default_factory=dict)
    cards: dict[tuple[str, int], object] = field(default_factory=dict)       # (person, fs) -> Card
    standing: dict[str, int] = field(default_factory=dict)


def _splits(rows, par) -> dict[str, dict[str, Split]]:
    """rows: (person, season, key, runs, balls, outs); par: (season, key) -> (runs, balls, outs)."""
    out: dict[str, dict[str, Split]] = defaultdict(dict)
    for person, season, key, runs, balls, outs in rows:
        s = out[person].setdefault(key, Split())
        s.runs += runs
        s.balls += balls
        s.outs += outs
        pr, pb, po = par.get((season, key), (0, 0, 0))
        if pb:
            s.par_runs += balls * pr / pb
            s.par_outs += balls * po / pb
    return out


def load_people(conn) -> People:
    cur = conn.cursor()
    cur.execute("select person_id, nationality, bowling_style, is_keeper from people")
    meta = {p: dict(nationality=n, style=s, keeper=k) for p, n, s, k in cur.fetchall()}
    cur.execute("""select person_id, role from squad_members
                   group by person_id, role order by person_id, count(*) desc, role""")
    role: dict[str, str] = {}
    for p, r in cur.fetchall():
        role.setdefault(p, r)
    cur.execute("""select person_id, franchise_season_id, count(distinct match_id) from appearances
                   where participated or named_in_squad group by 1, 2""")
    matches = {(p, fs): n for p, fs, n in cur.fetchall()}
    cur.execute("select player_of_match_id, count(*) from matches where player_of_match_id is not null group by 1")
    pom = Counter(dict(cur.fetchall()))

    dismissal = "d.player_out_id = d.batter_id and d.wicket_kind <> all(%(nk)s)"
    nk = {"nk": list(OUT_KINDS)}
    # Batting by phase, the striker's own balls (A22) and dismissals (A36).
    cur.execute(f"""select d.batter_id, m.season_year, d.phase, sum(d.runs_batter),
                           count(*) filter (where d.extra_wides = 0), count(*) filter (where {dismissal})
                    from deliveries d join matches m using (match_id)
                    where not d.is_super_over group by 1, 2, 3""", nk)
    rows = cur.fetchall()
    par: dict = defaultdict(lambda: [0, 0, 0])
    for _p, season, ph, r, b, o in rows:
        acc = par[(season, ph)]
        acc[0] += r; acc[1] += b; acc[2] += o
    bat_phase = _splits(rows, {k: tuple(v) for k, v in par.items()})

    # Bowling by phase: legal balls, runs charged (A1), wickets credited (A36).
    cur.execute("""select d.bowler_id, m.season_year, d.phase, sum(d.runs_batter + d.extra_wides + d.extra_noballs),
                          count(*) filter (where d.legal_ball), count(*) filter (where d.credited_to_bowler)
                   from deliveries d join matches m using (match_id)
                   where not d.is_super_over group by 1, 2, 3""")
    rows = cur.fetchall()
    par = defaultdict(lambda: [0, 0, 0])
    for _p, season, ph, r, b, o in rows:
        acc = par[(season, ph)]
        acc[0] += r; acc[1] += b; acc[2] += o
    bowl_phase = _splits(rows, {k: tuple(v) for k, v in par.items()})

    # Batting against each bowling style (people.bowling_style, A112/A114).
    cur.execute(f"""select d.batter_id, m.season_year, coalesce(p.bowling_style, 'unknown'), sum(d.runs_batter),
                           count(*) filter (where d.extra_wides = 0), count(*) filter (where {dismissal})
                    from deliveries d join matches m using (match_id) join people p on p.person_id = d.bowler_id
                    where not d.is_super_over group by 1, 2, 3""", nk)
    rows = cur.fetchall()
    par = defaultdict(lambda: [0, 0, 0])
    for _p, season, st, r, b, o in rows:
        acc = par[(season, st)]
        acc[0] += r; acc[1] += b; acc[2] += o
    bat_style = _splits(rows, {k: tuple(v) for k, v in par.items()})

    cur.execute("""select player_out_id, wicket_kind, count(*) from deliveries
                   where not is_super_over and player_out_id is not null and wicket_kind <> all(%(nk)s)
                   group by 1, 2""", nk)
    out_kinds: dict[str, Counter] = defaultdict(Counter)
    for p, k, n in cur.fetchall():
        out_kinds[p][k] += n
    cur.execute("""select bowler_id, wicket_kind, count(*) from deliveries
                   where not is_super_over and credited_to_bowler group by 1, 2""")
    wicket_kinds: dict[str, Counter] = defaultdict(Counter)
    for p, k, n in cur.fetchall():
        wicket_kinds[p][k] += n

    cur.execute(f"""select batter_id, bowler_id, count(*) filter (where extra_wides = 0), sum(runs_batter),
                           count(*) filter (where d.credited_to_bowler and {dismissal})
                    from deliveries d where not is_super_over group by 1, 2
                    having count(*) filter (where extra_wides = 0) >= %(mb)s""", {**nk, "mb": H2H_LOAD_BALLS})
    h2h = [tuple(r) for r in cur.fetchall()]

    cur.execute("select person_id, position, innings from person_batting_positions")
    positions: dict[str, dict[int, int]] = defaultdict(dict)
    for p, pos, n in cur.fetchall():
        positions[p][pos] = n

    return People(meta, role, matches, pom, bat_phase, bowl_phase, bat_style, out_kinds=out_kinds,
                  wicket_kinds=wicket_kinds, h2h=h2h, positions=positions)


def attach_cards(people: People) -> None:
    """The card each season is rated at, read from the committed deck snapshot -- the same
    numbers the game deals -- never from a second derivation of them."""
    from game import skills as skill_book
    from tools.snapshot_deck import deck_from, read_document
    doc = read_document()
    if doc is None:
        raise SystemExit("no readable deck snapshot (run tools.snapshot_deck)")
    deck = deck_from(doc)
    cards = [c for cs in deck.cards_by_fs.values() for c in cs]
    people.cards = {(c.person_id, c.fs_id): c for c in cards}
    people.standing = skill_book.standing(cards)


# --------------------------------------------------------------------------- one player

ROLE_WORDS = {"batter": "batter", "bowler": "bowler", "allrounder": "all-rounder", "keeper": "wicketkeeper-batter"}
KIND_WORDS = {"caught": "Caught", "bowled": "Bowled", "lbw": "LBW", "run out": "Run out", "stumped": "Stumped",
              "caught and bowled": "Caught and bowled", "hit wicket": "Hit wicket", "retired out": "Retired out",
              "obstructing the field": "Obstructing the field"}


@dataclass
class Career:
    person: str
    name: str
    seasons: list[dict]          # one per (year, franchise season), in order
    bat: dict                    # career batting totals
    bowl: dict                   # career bowling totals
    matches: int
    innings: list               # every Bat
    spells: list                # every Bowl
    franchises: list[str]       # canonical, in the order first played for


class Almanack:
    records = None   # the phase-two pages (tools.almanack_records), attached by load_all
    auctions = None  # the phase-three pages (tools.almanack_auctions), attached by load_all

    def __init__(self, rec: H.Record, people: People):
        self.rec, self.p = rec, people
        bats: dict[str, list] = defaultdict(list)
        bowls: dict[str, list] = defaultdict(list)
        for b in rec.arch.bats:
            bats[b.person].append(b)
        for b in rec.arch.bowls:
            bowls[b.person].append(b)
        self.careers: dict[str, Career] = {}
        persons = {p for p, _ in people.matches} | set(bats) | set(bowls)
        for person in persons:
            c = self._career(person, bats.get(person, []), bowls.get(person, []))
            if c.matches:
                self.careers[person] = c
        self.by_runs = sorted(self.careers.values(), key=lambda c: (-c.bat["runs"], c.bat["balls"], c.name))
        self.by_wkts = sorted(self.careers.values(), key=lambda c: (-c.bowl["wickets"], c.bowl["runs"], c.name))
        self.by_pom = sorted(self.careers.values(), key=lambda c: (-self.p.pom.get(c.person, 0), c.name))
        self.run_rank = {c.person: i for i, c in enumerate(self.by_runs, 1) if c.bat["runs"]}
        self.wkt_rank = {c.person: i for i, c in enumerate(self.by_wkts, 1) if c.bowl["wickets"]}

    def name(self, person: str) -> str:
        return self.rec.person(person)

    def link(self, person: str) -> str:
        if person in self.careers:
            return f'<a href="{player_path(self.name(person))}">{esc(self.name(person))}</a>'
        return esc(self.name(person))

    def _career(self, person: str, bats: list, bowls: list) -> Career:
        rec = self.rec
        fss = sorted({fs for (p, fs) in self.p.matches if p == person} | {b.fs for b in bats} | {b.fs for b in bowls},
                     key=lambda fs: (rec.arch.fs[fs][1], rec.team(fs)))
        seasons = []
        for fs in fss:
            bb = [b for b in bats if b.fs == fs]
            ww = [b for b in bowls if b.fs == fs]
            seasons.append(dict(fs=fs, year=rec.arch.fs[fs][1], matches=self.p.matches.get((person, fs), 0),
                                bat=_bat_totals(bb), bowl=_bowl_totals(ww)))
        franchises: list[str] = []
        for s in seasons:
            f = rec.franchise(s["fs"])
            if f not in franchises:
                franchises.append(f)
        return Career(person, rec.person(person), seasons, _bat_totals(bats), _bowl_totals(bowls),
                      sum(s["matches"] for s in seasons), bats, bowls, franchises)


def _bat_totals(bats: list) -> dict:
    hs = max(((b.runs, not b.out) for b in bats), default=(0, False))
    return dict(inns=len(bats), outs=sum(b.out for b in bats), runs=sum(b.runs for b in bats),
                balls=sum(b.balls for b in bats), fours=sum(b.fours for b in bats),
                sixes=sum(b.sixes for b in bats), hs=hs, hundreds=sum(b.runs >= 100 for b in bats),
                fifties=sum(50 <= b.runs < 100 for b in bats), ducks=sum(b.runs == 0 and b.out for b in bats))


def _bowl_totals(bowls: list) -> dict:
    best = max(((b.wickets, -b.runs) for b in bowls if b.balls), default=(0, 0))
    return dict(balls=sum(b.balls for b in bowls), runs=sum(b.runs for b in bowls),
                wickets=sum(b.wickets for b in bowls), best=(best[0], -best[1]),
                fours=sum(b.wickets >= 4 for b in bowls), inns=sum(1 for b in bowls if b.balls))


def _hs(t: dict) -> str:
    runs, notout = t["hs"]
    return f"{runs}{'*' if notout else ''}"


def _sr(t: dict) -> str:
    return rate(100 * t["runs"], t["balls"], 1)


def _econ(t: dict) -> str:
    return rate(6 * t["runs"], t["balls"], 2)


def _signed(x: float | None, places: int = 1) -> str:
    if x is None:
        return "-"
    return f"{'+' if x >= 0 else '−'}{abs(x):.{places}f}"


def _rank_words(n: int) -> str:
    return "the most" if n == 1 else f"the {H.ordinal(n)}-most"


# --------------------------------------------------------------------------- page parts

def lede(a: Almanack, c: Career) -> str:
    rec, meta = a.rec, a.p.meta.get(c.person, {})
    years = sorted({s["year"] for s in c.seasons})
    span = f"{years[0]}" if len(years) == 1 else f"{years[0]} to {years[-1]}"
    role = ROLE_WORDS.get(a.p.role.get(c.person, ""), "player")
    nat = meta.get("nationality")
    who = f"{esc(c.name)} is {'an' if role[0] in 'aeiou' else 'a'} {role}{' from ' + esc(nat) if nat else ''}"
    clubs = [f'<a href="/teams/{slug(f)}">{esc(f)}</a>' for f in c.franchises]
    first = (f"{who} who played {plural(c.matches, 'IPL match', 'IPL matches')} in "
             f"{plural(len(years), 'season')} ({span}), for {H.listing(clubs)}.")
    b, w = c.bat, c.bowl
    said = []
    if b["runs"] >= 100:
        s = f"{esc(c.name)} scored {b['runs']:,} runs at a strike rate of {_sr(b)}"
        if b["outs"]:
            s += f" and an average of {rate(b['runs'], b['outs'], 2)}"
        rk = a.run_rank.get(c.person)
        if rk and rk <= 50:
            s += f", {_rank_words(rk)} runs in the IPL's history"
        said.append(("bat", s + "."))
    if w["wickets"] >= 10:
        s = f"{esc(c.name)} took {plural(w['wickets'], 'wicket')} at an economy of {_econ(w)}"
        rk = a.wkt_rank.get(c.person)
        if rk and rk <= 50:
            s += f", {_rank_words(rk)} wickets in the IPL's history"
        said.append(("bowl", s + "."))
    if bowls_first(a, c):
        said.reverse()
    parts = [first] + [t for _, t in said]
    pom = a.p.pom.get(c.person, 0)
    if pom:
        parts.append(f"Named Player of the Match {H.times(pom)}.")
    return para(*parts)


def bowls_first(a: Almanack, c: Career) -> bool:
    """A bowler's page leads with the wickets: by his commonest squad role (A26)."""
    return a.p.role.get(c.person) == "bowler"


def glance(a: Almanack, c: Career) -> str:
    runs, wkts = ("Runs", f"{c.bat['runs']:,}"), ("Wickets", f"{c.bowl['wickets']}")
    tiles = [("Matches", f"{c.matches}"), *((wkts, runs) if bowls_first(a, c) else (runs, wkts)),
             ("Player of the Match", f"{a.p.pom.get(c.person, 0)}")]
    best = max((a.p.cards[(c.person, s['fs'])].display for s in c.seasons
                if (c.person, s["fs"]) in a.p.cards and a.p.cards[(c.person, s["fs"])].display), default=None)
    if best:
        tiles.append(("Best card", f"{best}"))
    inner = "".join(f'<div class="al-tile"><b>{v}</b><span>{k}</span></div>' for k, v in tiles)
    return f'    <div class="al-tiles">{inner}</div>\n'


def seasons_table(a: Almanack, c: Career) -> str:
    from game import skills as skill_book
    rows = []
    for s in c.seasons:
        card = a.p.cards.get((c.person, s["fs"]))
        rating, chips = "-", ""
        if card is not None and card.display:
            rating = f"<b>{card.display}</b>"
            shown = skill_book.card_skills(c.person, card.season_year, card.role, card.display,
                                           a.p.standing.get(c.person, 0))
            chips = '<span class="skills">' + "".join(f'<span class="sk t{x.tier}" title="{esc(x.blurb)}">{esc(x.label)}</span>'
                             for x in shown) + "</span>"
        b, w = s["bat"], s["bowl"]
        rows.append([f'<a href="/seasons/{s["year"]}">{s["year"]}</a>', a.rec.team_link(s["fs"]), str(s["matches"]),
                     f"{b['runs']:,}" if b["inns"] else "-", _sr(b) if b["balls"] else "-",
                     str(w["wickets"]) if w["balls"] else "-", _econ(w) if w["balls"] else "-", rating, chips])
    body = table(["Season", "Team", "M", "Runs", "SR", "Wkts", "Econ", "Card", "Skills"], rows, num_from=2,
                 cls="al-seasons")
    note = ('<p class="hx-note">The card is the rating this game deals that season at, on a 72-99 scale: '
            'see <a href="/guides/ratings">how the ratings work</a>. Skills show only where the card '
            'is high enough to open them. A match abandoned without a ball bowled is not in the archive, '
            'so a match count can sit one or two below the official one.</p>')
    return section("Season by season", body + note)


def batting_table(c: Career) -> str:
    if not c.bat["inns"]:
        return ""
    rows = []
    for s in c.seasons:
        b = s["bat"]
        if not b["inns"]:
            continue
        rows.append([str(s["year"]), str(b["inns"]), str(b["inns"] - b["outs"]), f"<b>{b['runs']:,}</b>", str(b["balls"]),
                     rate(b["runs"], b["outs"], 2), _sr(b), _hs(b), str(b["hundreds"]), str(b["fifties"]),
                     str(b["fours"]), str(b["sixes"])])
    t = c.bat
    rows.append(["<b>Career</b>", str(t["inns"]), str(t["inns"] - t["outs"]), f"<b>{t['runs']:,}</b>", str(t["balls"]),
                 rate(t["runs"], t["outs"], 2), _sr(t), _hs(t), str(t["hundreds"]), str(t["fifties"]),
                 str(t["fours"]), str(t["sixes"])])
    return section("Batting", table(["Season", "Inns", "NO", "Runs", "Balls", "Avg", "SR", "HS", "100", "50", "4s", "6s"],
                                     rows))


def bowling_table(c: Career) -> str:
    if not c.bowl["balls"]:
        return ""
    rows = []
    for s in c.seasons:
        w = s["bowl"]
        if not w["balls"]:
            continue
        rows.append([str(s["year"]), str(w["inns"]), overs(w["balls"]), str(w["runs"]), f"<b>{w['wickets']}</b>",
                     rate(w["runs"], w["wickets"], 2), _econ(w), rate(w["balls"], w["wickets"], 1),
                     f"{w['best'][0]}/{w['best'][1]}", str(w["fours"])])
    t = c.bowl
    rows.append(["<b>Career</b>", str(t["inns"]), overs(t["balls"]), str(t["runs"]), f"<b>{t['wickets']}</b>",
                 rate(t["runs"], t["wickets"], 2), _econ(t), rate(t["balls"], t["wickets"], 1),
                 f"{t['best'][0]}/{t['best'][1]}", str(t["fours"])])
    return section("Bowling", table(["Season", "Inns", "Overs", "Runs", "Wkts", "Avg", "Econ", "SR", "Best", "4w+"], rows))


MIN_SPLIT_BALLS = 30   # a phase or style row needs this many balls to be printed


def against_the_field(a: Almanack, c: Career) -> str:
    out = []
    bp = a.p.bat_phase.get(c.person, {})
    if sum(s.balls for s in bp.values()) >= MIN_SPLIT_BALLS:
        rows = []
        for ph in PHASES:
            s = bp.get(ph)
            if not s or s.balls < MIN_SPLIT_BALLS:
                continue
            rows.append([PHASE_LABEL[ph], f"{s.runs:,}", str(s.balls), rate(100 * s.runs, s.balls, 1),
                         rate(100 * s.par_runs, s.balls, 1), f"<b>{_signed(s.above_par())}</b>",
                         rate(s.balls, s.outs, 1), rate(s.balls, s.par_outs, 1)])
        st = a.p.bat_style.get(c.person, {})
        for style in STYLES:
            s = st.get(style)
            if not s or s.balls < MIN_SPLIT_BALLS:
                continue
            rows.append([f"Against {style}", f"{s.runs:,}", str(s.balls), rate(100 * s.runs, s.balls, 1),
                         rate(100 * s.par_runs, s.balls, 1), f"<b>{_signed(s.above_par())}</b>",
                         rate(s.balls, s.outs, 1), rate(s.balls, s.par_outs, 1)])
        if rows:
            out.append("<h3>Batting</h3>" + table(["", "Runs", "Balls", "SR", "League SR", "Runs above par /100",
                                                    "Balls per dismissal", "League"], rows))
    wp = a.p.bowl_phase.get(c.person, {})
    if sum(s.balls for s in wp.values()) >= MIN_SPLIT_BALLS:
        rows = []
        for ph in PHASES:
            s = wp.get(ph)
            if not s or s.balls < MIN_SPLIT_BALLS:
                continue
            saved = None if not s.balls else 100 * (s.par_runs - s.runs) / s.balls
            rows.append([PHASE_LABEL[ph], overs(s.balls), rate(6 * s.runs, s.balls, 2), rate(6 * s.par_runs, s.balls, 2),
                         f"<b>{_signed(saved)}</b>", str(s.outs), rate(s.balls, s.outs, 1), rate(s.balls, s.par_outs, 1)])
        if rows:
            out.append("<h3>Bowling</h3>" + table(["", "Overs", "Econ", "League econ", "Runs saved /100",
                                                    "Wkts", "Balls per wicket", "League"], rows))
    if not out:
        return ""
    # The six innings whose scheduled length is unknown (A17) have no phase, so their balls
    # sit outside every phase row; say so rather than let the rows look short.
    loose = []
    lb, lw = bp.get(None), wp.get(None)
    if lb and lb.balls:
        loose.append(f"{plural(lb.runs, 'run')} off {plural(lb.balls, 'ball')} batting")
    if lw and lw.balls:
        loose.append(f"{plural(lw.balls, 'ball')} bowled")
    if loose:
        out.append(f'<p class="hx-note al-loose">{H.listing(loose).capitalize()} came in innings whose '
                   "scheduled length the archive does not record, so they belong to no phase.</p>")
    note = ('<p class="hx-note">"League" is what every batter or bowler in the IPL did in the same phase, '
            'or against the same kind of bowling, in the seasons this player played, weighted by this '
            "player's own balls in each season. So a figure above par is measured against the same eras, "
            "not against the whole archive. Splits count the striker's own dismissals.</p>")
    return section("Against the field", "".join(out) + note)


def dismissals(a: Almanack, c: Career) -> str:
    parts = []
    out = a.p.out_kinds.get(c.person)
    if out and sum(out.values()) >= 5:
        n = sum(out.values())
        rows = [[KIND_WORDS.get(k, k.capitalize()), str(v), f"{100 * v / n:.0f}%"] for k, v in out.most_common()]
        parts.append("<h3>How the dismissals came</h3>" + table(["", "Times", "Share"], rows))
    wk = a.p.wicket_kinds.get(c.person)
    if wk and sum(wk.values()) >= 5:
        n = sum(wk.values())
        rows = [[KIND_WORDS.get(k, k.capitalize()), str(v), f"{100 * v / n:.0f}%"] for k, v in wk.most_common()]
        parts.append("<h3>How the wickets fell</h3>" + table(["", "Wickets", "Share"], rows))
    return section("Dismissals", "".join(parts)) if parts else ""


def batting_positions(a: Almanack, c: Career) -> str:
    pos = a.p.positions.get(c.person)
    if not pos or sum(pos.values()) < 5:
        return ""
    n = sum(pos.values())
    top = max(pos.values())
    bars = "".join(
        f'<div class="al-bar"><span class="al-bar-k">No. {p}</span>'
        f'<span class="al-bar-t"><i style="width:{100 * v / top:.0f}%"></i></span>'
        f'<span class="al-bar-v">{v}</span></div>' for p, v in sorted(pos.items()))
    modal = max(pos, key=lambda p: (pos[p], -p))
    return section("Where in the order",
                   para(f"{plural(n, 'innings', 'innings')} in all, most often at number {modal} "
                        f"({pos[modal]} of them).") + f'<div class="al-bars">{bars}</div>')


def best_of(a: Almanack, c: Career) -> str:
    rec = a.rec
    parts = []
    inns = sorted(c.innings, key=lambda b: (-b.runs, b.balls))[:BEST_ROWS]
    if inns and inns[0].runs >= 30:
        rows = []
        for b in inns:
            m = rec.arch.matches[b.match_id]
            rows.append([f"<b>{b.runs}{'' if b.out else '*'}</b> ({b.balls})", rec.team_link(rec.opponent(b.match_id, b.fs)),
                         f'<a href="/seasons/{m.year}">{H.day(m.day)}</a>'])
        # Figures first and nothing in the mono number face, so on a phone the score is what shows.
        parts.append("<h3>Best innings</h3>" + table(["Runs (balls)", "Against", "Date"], rows, num_from=3))
    spells = sorted((b for b in c.spells if b.wickets), key=lambda b: (-b.wickets, b.runs))[:BEST_ROWS]
    if spells and spells[0].wickets >= 2:
        rows = []
        for b in spells:
            m = rec.arch.matches[b.match_id]
            rows.append([f"<b>{b.wickets}/{b.runs}</b> ({overs(b.balls)} ov)", rec.team_link(rec.opponent(b.match_id, b.fs)),
                         f'<a href="/seasons/{m.year}">{H.day(m.day)}</a>'])
        parts.append("<h3>Best bowling</h3>" + table(["Figures", "Against", "Date"], rows, num_from=3))
    return section("Best days", "".join(parts)) if parts else ""


def head_to_head(a: Almanack, c: Career, faced: list, bowled: list) -> str:
    parts = []
    if faced:
        rows = [[a.link(bw), str(b), str(r), rate(100 * r, b, 1), str(o)] for _bt, bw, b, r, o in faced]
        parts.append("<h3>Bowlers faced most</h3>" + table(["Bowler", "Balls", "Runs", "SR", "Out"], rows))
    if bowled:
        rows = [[a.link(bt), str(b), str(r), rate(100 * r, b, 1), str(o)] for bt, _bw, b, r, o in bowled]
        parts.append("<h3>Batters bowled to most</h3>" + table(["Batter", "Balls", "Runs", "SR", "Dismissed"], rows))
    if not parts:
        return ""
    return section("Head to head", "".join(parts) + f'<p class="hx-note">Pairs that met for at least '
                   f"{H2H_MIN_BALLS} balls. \"Out\" counts dismissals credited to the bowler, so a run out is not "
                   "in it.</p>")


ROBOTS = '<meta name="robots" content="noindex,follow">\n'


def page(path: str, title: str, description: str, over: str, heading: str, body: str,
         index: bool = True) -> str:
    text = H.PAGE.format(title=esc(title), description=esc(description), origin=H.ORIGIN, path=path,
                         over=esc(over), heading=esc(heading), body=body)
    if not index:
        text = text.replace('<meta name="description"', ROBOTS + '<meta name="description"', 1)
    return H.finish_page(text)


def player_page(a: Almanack, c: Career, faced: list, bowled: list) -> str:
    auction = a.auctions.player_section(c.person) if a.auctions is not None else ""
    body = (glance(a, c) + section("The career", lede(a, c)) + seasons_table(a, c) + auction + batting_table(c)
            + bowling_table(c) + against_the_field(a, c) + best_of(a, c) + batting_positions(a, c)
            + dismissals(a, c) + head_to_head(a, c, faced, bowled)
            + '    <p class="hx-pager actions"><a class="go ghost" href="/almanack/players">All players</a>'
              '<a class="go ghost" href="/almanack">The Almanack</a></p>\n')
    years = sorted({s["year"] for s in c.seasons})
    desc = (f"{c.name}'s IPL career {years[0]}-{years[-1]}: {c.bat['runs']:,} runs, {c.bowl['wickets']} wickets, "
            f"season by season, by phase, against pace and spin, and head to head.")
    return page(player_path(c.name), f"{c.name}: IPL career and stats", desc, "ALMANACK · PLAYER", c.name, body,
                index=c.matches >= INDEX_MATCHES)


# --------------------------------------------------------------------------- the front page and index

def all_time(a: Almanack) -> str:
    bat = [[f"{i}. {a.link(c.person)}", f"<b>{c.bat['runs']:,}</b>", str(c.matches), _sr(c.bat),
            rate(c.bat['runs'], c.bat['outs'], 2)] for i, c in enumerate(a.by_runs[:TOP_ALL_TIME], 1)]
    bowl = [[f"{i}. {a.link(c.person)}", f"<b>{c.bowl['wickets']}</b>", str(c.matches), _econ(c.bowl),
             rate(c.bowl['runs'], c.bowl['wickets'], 2)] for i, c in enumerate(a.by_wkts[:TOP_ALL_TIME], 1)]
    return (section("Most runs", table(["Player", "Runs", "M", "SR", "Avg"], bat))
            + section("Most wickets", table(["Player", "Wkts", "M", "Econ", "Avg"], bowl)))


def facts(a: Almanack) -> list[str]:
    """Fun facts, each a figure worked out here from the archive -- never typed in."""
    cs = list(a.careers.values())
    out = []
    pom = a.by_pom[0]
    out.append(f"{a.link(pom.person)} has been named Player of the Match "
               f"{a.p.pom[pom.person]} times, more than anyone else.")
    most_clubs = max(cs, key=lambda c: (len(c.franchises), -c.matches))
    n = len(most_clubs.franchises)
    tied = [c for c in cs if len(c.franchises) == n]
    out.append(f"{H.listing([a.link(c.person) for c in sorted(tied, key=lambda c: c.name)])} "
               f"{'has' if len(tied) == 1 else 'have each'} played for {H.words(n)} different franchises, the most of anyone.")
    seasons = {c.person: len({s['year'] for s in c.seasons}) for c in cs}
    most = max(seasons.values())
    everyone = sorted((c for c in cs if seasons[c.person] == most), key=lambda c: c.name)
    out.append(f"{H.words(len(everyone)).capitalize()} {'player has' if len(everyone) == 1 else 'players have'} played in all {most} seasons: "
               f"{H.listing([a.link(c.person) for c in everyone])}." if most == len(a.rec.years)
               else f"The most seasons anyone has played is {most}.")
    ducks = max(cs, key=lambda c: (c.bat["ducks"], -c.bat["inns"]))
    out.append(f"{a.link(ducks.person)} has been out for a duck {ducks.bat['ducks']} times, the most of anyone.")
    sixes = max(cs, key=lambda c: c.bat["sixes"])
    out.append(f"{a.link(sixes.person)} has hit {sixes.bat['sixes']:,} sixes, the most in the IPL.")
    top = max(a.rec.arch.bats, key=lambda b: (b.runs, -b.balls))
    m = a.rec.arch.matches[top.match_id]
    out.append(f"The highest score is {a.link(top.person)}'s {top.runs}{'' if top.out else '*'} off {top.balls} balls "
               f"against {esc(a.rec.team(a.rec.opponent(top.match_id, top.fs)))} on {H.day(m.day)}.")
    best = max(a.rec.arch.bowls, key=lambda b: (b.wickets, -b.runs))
    m = a.rec.arch.matches[best.match_id]
    out.append(f"The best bowling figures are {a.link(best.person)}'s {best.wickets}/{best.runs} against "
               f"{esc(a.rec.team(a.rec.opponent(best.match_id, best.fs)))} on {H.day(m.day)}.")
    return out


def almanack_page(a: Almanack) -> str:
    cards = [("/almanack/players", "Players", f"All {len(a.careers):,} who played in the IPL, each with a full career page."),
             ("/seasons", "Seasons", f"Every season from {a.rec.years[0]} to {a.rec.years[-1]}: the champion, the table and the leaders."),
             ("/teams", "Franchises", f"All {len(a.rec.franchises)} franchises, every name each played under, and its record."),
             ("/almanack/records", "Records", "Highest scores, fastest fifties, best figures, hat-tricks, partnerships and the biggest chases."),
             ("/almanack/head-to-head", "Head to head", "Every batter against every bowler, and the longest rivalries in the IPL."),
             ("/almanack/auctions", "Auctions", "Every IPL auction since 2008: what each buy cost and what the player then did."),
             ("/almanack/grounds", "Grounds", "Every ground: average scores, chasing records, pace and spin, and who did best there."),
             ("/records", "Rated seasons", "The highest-rated seasons on this game's 72-99 card, and the leaders across every season."),
             ("/articles", "Articles", "Long reads from the ball-by-ball record: the best seasons, the death overs, chasing."),
             ("/guides/ratings", "How we rate", "What the 72-99 card means and how each season is measured.")]
    grid = "".join(f'<a class="hx-card" href="{u}"><b>{esc(t)}</b><span>{esc(d)}</span></a>' for u, t, d in cards)
    intro = para(f"The Almanack is the IPL's record from every ball bowled since {a.rec.years[0]}: "
                 f"{len(a.rec.arch.matches):,} matches and {len(a.careers):,} players. Every figure on these pages "
                 "is worked out from the ball-by-ball archive the game itself is built on.",
                 "Each player's page measures phase and pace-or-spin figures against the league in the same "
                 "seasons, so a strike rate from 2009 and one from 2025 can be read side by side.")
    fact_list = "<ul>" + "".join(f"<li>{f}</li>" for f in facts(a)) + "</ul>"
    body = (section("The record, ball by ball", intro + f'<div class="hx-cards">{grid}</div>')
            + section("Did you know", fact_list) + all_time(a))
    return page("/almanack", "The Almanack: every IPL player, season and record",
                "The IPL's record from every ball since 2008: every player's career by season, by phase and "
                "against pace and spin, every season and franchise, records and articles.",
                "THE ALMANACK", "The Almanack", body)


def players_index(a: Almanack) -> str:
    rows = []
    for c in sorted(a.careers.values(), key=lambda c: (-(c.bat["runs"] + 20 * c.bowl["wickets"]), c.name)):
        years = sorted({s["year"] for s in c.seasons})
        span = str(years[0]) if len(years) == 1 else f"{years[0]}-{years[-1]}"
        rows.append([a.link(c.person), span, str(c.matches), f"{c.bat['runs']:,}", str(c.bowl["wickets"])])
    filt = ('<p id="alCount" class="hx-note" aria-live="polite"></p>'
            '<input id="alFilter" class="room-input al-filter" placeholder="Find a player" '
            'aria-label="Find a player" oninput="alFilter(this.value)">')
    body = section("Every player",
                   para(f"All {len(a.careers):,} players who played in the IPL, ordered by runs plus twenty "
                        "for each wicket, so batters and bowlers stand on one list.")
                   + filt + table(["Player", "Seasons", "M", "Runs", "Wkts"], rows, num_from=2, cls="al-index"))
    body += '<script src="/static/almanack.js"></script>\n'
    return page("/almanack/players", "Every IPL player", "Every player who played in the IPL since 2008, "
                "with a career page for each.", "ALMANACK", "Every player", body)


# --------------------------------------------------------------------------- build

def h2h_by_player(a: Almanack) -> tuple[dict, dict]:
    faced: dict[str, list] = defaultdict(list)
    bowled: dict[str, list] = defaultdict(list)
    for row in a.p.h2h:
        if row[2] < H2H_MIN_BALLS:
            continue
        faced[row[0]].append(row)
        bowled[row[1]].append(row)
    key = lambda r: (-r[2], -r[4], a.name(r[1]))  # noqa: E731
    for d in (faced, bowled):
        for p in d:
            d[p].sort(key=key)
            d[p] = d[p][:H2H_ROWS]
    return faced, bowled


def build(a: Almanack) -> dict[pathlib.Path, str]:
    faced, bowled = h2h_by_player(a)
    out = {ALMANACK: almanack_page(a), PLAYERS_INDEX: players_index(a)}
    if a.records is not None:
        R = a.records
        doc = R.h2h_document()
        R.h2h_url = "/static/almanack/h2h.json?v=" + hashlib.sha256(doc.encode()).hexdigest()[:10]
        out[R2.H2H_FILE] = doc
        out[STATIC / "almanack" / "records.html"] = R.records_page()
        out[STATIC / "almanack" / "grounds.html"] = R.grounds_index()
        out[STATIC / "almanack" / "head-to-head.html"] = R.h2h_page()
        for g in R.grounds.values():
            out[GROUND_DIR / f"{g.slug}.html"] = R.ground_page(g)
    if a.auctions is not None:
        out[STATIC / "almanack" / "auctions.html"] = a.auctions.index_page()
        for y in a.auctions.by_year:
            out[AUCTION_DIR / f"{y}.html"] = a.auctions.year_page(y)
    for c in a.careers.values():
        out[PLAYER_DIR / f"{player_slug(c.name)}.html"] = player_page(a, c, faced.get(c.person, []), bowled.get(c.person, []))
    return out


def load_all(conn) -> Almanack:
    rec = H.Record(H.load(conn))
    people = load_people(conn)
    attach_cards(people)
    a = Almanack(rec, people)
    a.auctions = AU.Auctions(a, AU.read_sales())
    a.records = R2.Records(a, R2.ordered_pass(conn), R2.spells(conn), R2.read_venues(), R2.toss_winners(conn),
                           R2.ground_styles(conn), people.h2h)
    return a


def main(check: bool = False) -> int:
    with H._connect() as conn:
        a = load_all(conn)
    pages = build(a)
    existing = (set(PLAYER_DIR.glob("*.html")) | set(GROUND_DIR.glob("*.html")) | set(AUCTION_DIR.glob("*.html"))
                | {p for p in (ALMANACK, PLAYERS_INDEX) if p.exists()})
    stale = sorted(str(p.relative_to(STATIC)) for p, t in pages.items() if not p.exists() or p.read_text() != t)
    extra = sorted(str(p.relative_to(STATIC)) for p in existing - set(pages))
    if check:
        for s in stale[:20]:
            print(f"stale: {s}")
        if len(stale) > 20:
            print(f"... and {len(stale) - 20} more")
        for s in extra:
            print(f"not generated (delete it): {s}")
        if not stale and not extra:
            print(f"almanack pages current ({len(pages)} pages)")
        return 1 if stale or extra else 0
    PLAYER_DIR.mkdir(parents=True, exist_ok=True)
    GROUND_DIR.mkdir(parents=True, exist_ok=True)
    AUCTION_DIR.mkdir(parents=True, exist_ok=True)
    for p, t in pages.items():
        if not p.exists() or p.read_text() != t:
            p.write_text(t)
    for s in extra:
        (STATIC / s).unlink()
    indexed = sum(c.matches >= INDEX_MATCHES for c in a.careers.values())
    print(f"wrote {len(stale)} of {len(pages)} almanack pages ({indexed} players indexed)"
          + (f", removed {len(extra)}" if extra else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(check="--check" in sys.argv))
