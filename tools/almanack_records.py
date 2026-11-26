"""The Almanack's second phase [A316]: the record book, the grounds and head to head.

Built from one pass over every delivery IN ORDER (match, innings, `delivery_id`), because the
records that matter most to a reader -- the fastest fifty, the biggest partnership, a hat-trick
-- are facts about a SEQUENCE, which no GROUP BY can give. The rules are `tools.build_history`'s:
super overs count for nothing; a ball faced is any ball not a wide (A22); a bowler is charged
runs off the bat, wides and no-balls (A1) and credited only his own wickets (A36); retired hurt
is not a dismissal.

Grounds come from `etl/overrides/venues.csv`: the archive writes one ground under up to four
names ("Feroz Shah Kotla", "Arun Jaitley Stadium, Delhi"), and the CSV decides which are one
place -- by Wikipedia's own redirects where they resolve (`source = wikipedia`) and by hand where
a title points at the wrong thing (`source = hand`, with the reason in `note`).
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from tools import build_history as H
from tools import almanack_viz as V
from tools.build_history import esc, overs, para, plural, rate, section, slug, table

VENUES = H.ROOT / "etl" / "overrides" / "venues.csv"
TOP = 10
H2H_FILE = H.STATIC / "almanack" / "h2h.json"
H2H_LIST_MIN = 6           # pairs in the searchable head-to-head file need this many balls
GROUND_CARDS = 12           # grounds shown as cards; every one is in the table below
RIVALRY_MIN_BALLS = 60     # a "great rivalry" needs this many balls between the two
SPELL_BALLS = 24           # an economical spell is a full four overs
SEASON_SR_MIN_BALLS = 200  # a season strike rate needs this many balls faced
GROUND_MIN_MATCHES = 10    # a ground page needs this many matches to be indexed


# --------------------------------------------------------------------------- one ordered pass

@dataclass
class Partnership:
    match_id: str
    fs: int
    wicket: int            # 1 = the opening stand
    a: str
    b: str
    runs: int = 0
    balls: int = 0


@dataclass
class Innings:
    match_id: str
    innings: int
    fs: int
    scheduled: int | None
    runs: int = 0
    wickets: int = 0
    balls: int = 0


@dataclass
class Pass:
    fifties: list = field(default_factory=list)       # (balls, runs, person, match, fs) reaching 50
    hundreds: list = field(default_factory=list)
    partnerships: list[Partnership] = field(default_factory=list)
    hat_tricks: list = field(default_factory=list)    # (person, match, fs)
    spells: list = field(default_factory=list)        # (runs, wickets, person, match, fs) for 24-ball spells
    innings: list[Innings] = field(default_factory=list)
    sixes_innings: list = field(default_factory=list)  # (sixes, runs, balls, person, match, fs)


def ordered_pass(conn) -> Pass:
    cur = conn.cursor()
    cur.execute("""select match_id, innings_no, batting_fs_id, bowling_fs_id, batter_id, non_striker_id, bowler_id,
                          runs_batter, runs_extras, extra_wides, legal_ball, credited_to_bowler,
                          player_out_id, wicket_kind, innings_scheduled_balls
                   from deliveries where not is_super_over order by match_id, innings_no, delivery_id""")
    out = Pass()
    key = None
    for row in cur:
        (mid, inn, fs, bfs, bat, ns, bowler, rb, rx, wides, legal, credited, pout, kind, sched) = row
        if (mid, inn) != key:
            if key is not None:
                _close(out, state)
            key = (mid, inn)
            state = dict(mid=mid, fs=fs, bfs=bfs, innings=Innings(mid, inn, fs, sched), score={}, faced={},
                         sixes={}, reached={}, part=None, dismissals=0, streak={})
        st = state
        ins: Innings = st["innings"]
        ins.runs += rb + rx
        ins.balls += 1 if legal else 0
        # the partnership: a maximal run of balls with the same two at the crease
        pair = frozenset((bat, ns))
        p = st["part"]
        if p is None or frozenset((p.a, p.b)) != pair:
            if p is not None:
                out.partnerships.append(p)
            a, b = sorted(pair)
            p = st["part"] = Partnership(mid, fs, st["dismissals"] + 1, a, b)
        p.runs += rb + rx
        p.balls += 1 if legal else 0
        # the batter's own innings, for fifties, hundreds and sixes
        if wides == 0:
            st["faced"][bat] = st["faced"].get(bat, 0) + 1
            st["score"][bat] = st["score"].get(bat, 0) + rb
            if rb == 6:
                st["sixes"][bat] = st["sixes"].get(bat, 0) + 1
            for mark, bucket in ((50, out.fifties), (100, out.hundreds)):
                if st["score"][bat] >= mark and (bat, mark) not in st["reached"]:
                    st["reached"][(bat, mark)] = True
                    bucket.append((st["faced"][bat], st["score"][bat], bat, mid, fs))
        # A hat-trick is three wickets credited to the bowler off three balls in a row from that
        # bowler, across overs too. EVERY ball bowled counts here, wides included: the public record
        # counts Tambe's 2014 hat-trick, whose first wicket was a stumping off a wide, and does not
        # count Rabada's 2020 Qualifier 2, where a wide fell between his second and third wickets.
        # (The game's own milestone, A167, lets a wide pass; that is the engine's rule, not the record's.)
        if credited:
            n = st["streak"].get(bowler, 0) + 1
            st["streak"][bowler] = n
            if n == 3:
                out.hat_tricks.append((bowler, mid, bfs))
        else:
            st["streak"][bowler] = 0
        if pout is not None and kind not in H.NOT_OUT:
            ins.wickets += 1
            st["dismissals"] += 1
    if key is not None:
        _close(out, state)
    return out


def _close(out: Pass, st: dict) -> None:
    if st["part"] is not None:
        out.partnerships.append(st["part"])
    out.innings.append(st["innings"])
    for bat, n in st["sixes"].items():
        out.sixes_innings.append((n, st["score"][bat], st["faced"][bat], bat, st["mid"], st["fs"]))


def toss_winners(conn) -> dict[str, int]:
    cur = conn.cursor()
    cur.execute("select match_id, toss_winner_fs_id from matches")
    return dict(cur.fetchall())


def ground_styles(conn) -> dict[str, dict[str, tuple]]:
    """Per match, per bowling style (A112/A114): legal balls, runs charged (A1), wickets (A36)."""
    cur = conn.cursor()
    cur.execute("""select d.match_id, coalesce(p.bowling_style, 'unknown'), count(*) filter (where d.legal_ball),
                          sum(d.runs_batter + d.extra_wides + d.extra_noballs), count(*) filter (where d.credited_to_bowler)
                   from deliveries d join people p on p.person_id = d.bowler_id
                   where not d.is_super_over group by 1, 2""")
    out: dict[str, dict[str, tuple]] = defaultdict(dict)
    for mid, style, b, r, w in cur.fetchall():
        out[mid][style] = (b, r, w)
    return out


def spells(conn) -> list:
    """Full four-over spells: legal balls, runs charged (A1) and wickets credited (A36)."""
    cur = conn.cursor()
    cur.execute("""select match_id, bowling_fs_id, bowler_id, count(*) filter (where legal_ball),
                          sum(runs_batter + extra_wides + extra_noballs), count(*) filter (where credited_to_bowler)
                   from deliveries where not is_super_over group by 1, 2, 3, innings_no""")
    return [(r[4], r[5], r[2], r[0], r[1]) for r in cur.fetchall() if r[3] == SPELL_BALLS]


# --------------------------------------------------------------------------- grounds

def read_venues(path=VENUES) -> dict[tuple[str, str], dict]:
    with open(path, newline="") as fh:
        return {(r["venue"], r["city"]): r for r in csv.DictReader(fh)}


@dataclass
class Ground:
    name: str
    city: str
    matches: list = field(default_factory=list)

    @property
    def slug(self) -> str:
        return slug(self.name)


def grounds(rec: H.Record, venues: dict) -> dict[str, Ground]:
    out: dict[str, Ground] = {}
    for m in rec.arch.matches.values():
        v = venues[(m.venue or "", m.city or "")]
        g = out.setdefault(v["ground"], Ground(v["ground"], v["ground_city"]))
        g.matches.append(m)
    for g in out.values():
        g.matches.sort(key=lambda m: (m.day, m.match_id))
    return out


# --------------------------------------------------------------------------- pages

def score_text(ins: Innings) -> str:
    w = "" if ins.wickets == 10 else f"/{ins.wickets}"
    return f"{ins.runs}{w} ({overs(ins.balls)} ov)"


class Records:
    def __init__(self, a, ps: Pass, spell_rows: list, venues: dict, toss: dict, styles: dict, h2h_all: list):
        self.a, self.rec, self.ps = a, a.rec, ps
        self.toss, self.styles, self.h2h_all = toss, styles, h2h_all
        self.h2h_url = ""   # set by build_almanack.build once the file's hash is known
        self.spells = spell_rows
        self.grounds = grounds(self.rec, venues)
        self.ground_of = {m.match_id: g for g in self.grounds.values() for m in g.matches}
        self.innings_by_match: dict[str, list[Innings]] = defaultdict(list)
        for i in ps.innings:
            self.innings_by_match[i.match_id].append(i)
        for v in self.innings_by_match.values():
            v.sort(key=lambda i: i.innings)

    # helpers
    def when(self, mid: str) -> str:
        m = self.rec.arch.matches[mid]
        return f'<a href="/seasons/{m.year}">{H.day(m.day)}</a>'

    def at(self, mid: str) -> str:
        g = self.ground_of[mid]
        return f'<a href="/almanack/grounds/{g.slug}">{esc(g.name)}</a>'

    def vs(self, mid: str, fs: int) -> str:
        return self.rec.team_link(self.rec.opponent(mid, fs))

    def full_length(self, i: Innings) -> bool:
        return i.scheduled == 120

    # ---------------------------------------------------------------- the record book
    def batting_records(self) -> str:
        a, rec = self.a, self.rec
        top = sorted(rec.arch.bats, key=lambda b: (-b.runs, b.balls, a.name(b.person)))[:TOP]
        rows = [[f"<b>{b.runs}{'' if b.out else '*'}</b> ({b.balls})", a.link(b.person), self.vs(b.match_id, b.fs),
                 self.when(b.match_id)] for b in top]
        out = "<h3>Highest scores</h3>" + table(["Runs (balls)", "Batter", "Against", "Date"], rows, num_from=4)
        for title, bucket in (("Fastest fifties", self.ps.fifties), ("Fastest hundreds", self.ps.hundreds)):
            best = sorted(bucket, key=lambda r: (r[0], a.name(r[2]), r[3]))[:TOP]
            rows = [[f"<b>{balls} balls</b>", a.link(p), self.vs(mid, fs), self.when(mid)] for balls, _r, p, mid, fs in best]
            out += f"<h3>{title}</h3>" + table(["Reached in", "Batter", "Against", "Date"], rows, num_from=4)
        sx = sorted(self.ps.sixes_innings, key=lambda r: (-r[0], r[2], a.name(r[3])))[:TOP]
        rows = [[f"<b>{n}</b>", f"{runs} ({balls})", a.link(p), self.vs(mid, fs), self.when(mid)] for n, runs, balls, p, mid, fs in sx]
        out += "<h3>Most sixes in an innings</h3>" + table(["Sixes", "Runs (balls)", "Batter", "Against", "Date"], rows, num_from=5)
        seasons = self._season_batting()
        rows = [[f"<b>{r['runs']:,}</b>", a.link(r["person"]), str(r["year"]), f"{rate(100 * r['runs'], r['balls'], 1)}"]
                for r in sorted(seasons, key=lambda r: (-r["runs"], r["balls"]))[:TOP]]
        out += "<h3>Most runs in a season</h3>" + table(["Runs", "Batter", "Season", "SR"], rows, num_from=4)
        rows = [[f"<b>{rate(100 * r['runs'], r['balls'], 1)}</b>", a.link(r["person"]), str(r["year"]), f"{r['runs']:,} off {r['balls']}"]
                for r in sorted((r for r in seasons if r["balls"] >= SEASON_SR_MIN_BALLS),
                                key=lambda r: (-r["runs"] / r["balls"], -r["balls"]))[:TOP]]
        out += (f"<h3>Fastest-scoring seasons</h3>" + table(["SR", "Batter", "Season", "Runs"], rows, num_from=4)
                + f'<p class="hx-note">Seasons of at least {SEASON_SR_MIN_BALLS} balls faced.</p>')
        return section("Batting", out)

    def _season_batting(self) -> list[dict]:
        acc: dict[tuple, dict] = {}
        for b in self.rec.arch.bats:
            y = self.rec.arch.matches[b.match_id].year
            r = acc.setdefault((b.person, y), dict(person=b.person, year=y, runs=0, balls=0))
            r["runs"] += b.runs
            r["balls"] += b.balls
        return list(acc.values())

    def bowling_records(self) -> str:
        a, rec = self.a, self.rec
        top = sorted((b for b in rec.arch.bowls if b.wickets), key=lambda b: (-b.wickets, b.runs, a.name(b.person)))[:TOP]
        rows = [[f"<b>{b.wickets}/{b.runs}</b> ({overs(b.balls)} ov)", a.link(b.person), self.vs(b.match_id, b.fs),
                 self.when(b.match_id)] for b in top]
        out = "<h3>Best figures</h3>" + table(["Figures", "Bowler", "Against", "Date"], rows, num_from=4)
        cheap = sorted(self.spells, key=lambda r: (r[0], -r[1], a.name(r[2])))[:TOP]
        rows = [[f"<b>{w}/{runs}</b>", a.link(p), self.vs(mid, fs), self.when(mid)] for runs, w, p, mid, fs in cheap]
        out += "<h3>Most economical four-over spells</h3>" + table(["Figures", "Bowler", "Against", "Date"], rows, num_from=4)
        acc: dict[tuple, list] = {}
        for b in rec.arch.bowls:
            y = rec.arch.matches[b.match_id].year
            r = acc.setdefault((b.person, y), [0, 0, 0])
            r[0] += b.wickets
            r[1] += b.runs
            r[2] += b.balls
        best = sorted(acc.items(), key=lambda kv: (-kv[1][0], kv[1][1]))[:TOP]
        rows = [[f"<b>{w}</b>", a.link(p), str(y), rate(6 * runs, balls, 2)] for (p, y), (w, runs, balls) in best]
        out += "<h3>Most wickets in a season</h3>" + table(["Wkts", "Bowler", "Season", "Econ"], rows, num_from=4)
        hats = sorted(self.ps.hat_tricks, key=lambda h: (rec.arch.matches[h[1]].day, h[1]))
        rows = [[a.link(p), self.vs(mid, fs), self.when(mid)] for p, mid, fs in hats]
        out += (f"<h3>Hat-tricks ({len(hats)})</h3>" + table(["Bowler", "Against", "Date"], rows, num_from=3)
                + '<p class="hx-note">Three wickets credited to the bowler off three balls in a row from that '
                  "bowler, across overs too. A wide in between breaks one, and a wicket off a wide counts.</p>")
        return section("Bowling", out)

    def partnership_records(self) -> str:
        a = self.a
        top = sorted(self.ps.partnerships, key=lambda p: (-p.runs, p.balls))[:TOP]
        rows = [[f"<b>{p.runs}</b> ({p.balls})", f"{H.ordinal(p.wicket)}", f"{a.link(p.a)} &amp; {a.link(p.b)}",
                 self.vs(p.match_id, p.fs), self.when(p.match_id)] for p in top]
        out = "<h3>Highest partnerships</h3>" + table(["Runs (balls)", "Wicket", "Batters", "Against", "Date"], rows, num_from=5)
        by_wkt = {}
        for p in self.ps.partnerships:
            if p.wicket <= 10 and (p.wicket not in by_wkt or (p.runs, -p.balls) > (by_wkt[p.wicket].runs, -by_wkt[p.wicket].balls)):
                by_wkt[p.wicket] = p
        rows = [[H.ordinal(w), f"<b>{p.runs}</b> ({p.balls})", f"{a.link(p.a)} &amp; {a.link(p.b)}", self.vs(p.match_id, p.fs),
                 self.when(p.match_id)] for w, p in sorted(by_wkt.items())]
        out += "<h3>The best stand for each wicket</h3>" + table(["Wicket", "Runs (balls)", "Batters", "Against", "Date"], rows, num_from=5)
        return section("Partnerships", out + '<p class="hx-note">A partnership is every ball the same two batters '
                       "were together, extras included; a retirement starts a new one at the same wicket.</p>")

    def team_records(self) -> str:
        rec = self.rec
        full = [i for i in self.ps.innings if self.full_length(i)]
        hi = sorted(full, key=lambda i: (-i.runs, i.balls))[:TOP]
        rows = [[f"<b>{score_text(i)}</b>", rec.team_link(i.fs), self.vs(i.match_id, i.fs), self.when(i.match_id)] for i in hi]
        out = "<h3>Highest totals</h3>" + table(["Total", "Team", "Against", "Date"], rows, num_from=4)
        done = [i for i in full if i.wickets == 10 or i.balls == 120]
        lo = sorted(done, key=lambda i: (i.runs, -i.balls))[:TOP]
        rows = [[f"<b>{score_text(i)}</b>", rec.team_link(i.fs), self.vs(i.match_id, i.fs), self.when(i.match_id)] for i in lo]
        out += ("<h3>Lowest totals</h3>" + table(["Total", "Team", "Against", "Date"], rows, num_from=4)
                + '<p class="hx-note">Innings scheduled for twenty overs that ended all out or at twenty overs.</p>')
        chases = []
        for mid, ins in self.innings_by_match.items():
            m = rec.arch.matches[mid]
            if len(ins) >= 2 and ins[1].innings == 2 and self.full_length(ins[1]) and m.winner == ins[1].fs and m.decided_by != "dls":
                chases.append(ins[1])
        ch = sorted(chases, key=lambda i: (-i.runs, i.balls))[:TOP]
        rows = [[f"<b>{score_text(i)}</b>", rec.team_link(i.fs), self.vs(i.match_id, i.fs), self.when(i.match_id)] for i in ch]
        out += "<h3>Highest successful chases</h3>" + table(["Total", "Team", "Against", "Date"], rows, num_from=4)
        by_runs = sorted((m for m in rec.arch.matches.values() if m.result_type == "runs" and m.decided_by != "dls"),
                         key=lambda m: (-(m.margin or 0), m.day))[:TOP]
        rows = [[f"<b>{m.margin} runs</b>", rec.team_link(m.winner), rec.team_link(m.b if m.winner == m.a else m.a),
                 self.when(m.match_id)] for m in by_runs]
        out += "<h3>Biggest wins by runs</h3>" + table(["Margin", "Winner", "Beaten", "Date"], rows, num_from=4)
        spare = []
        for m in rec.arch.matches.values():
            ins = self.innings_by_match.get(m.match_id, [])
            if m.result_type == "wickets" and len(ins) >= 2 and self.full_length(ins[1]) and m.decided_by != "dls":
                spare.append((120 - ins[1].balls, m))
        sp = sorted(spare, key=lambda t: (-t[0], t[1].day))[:TOP]
        rows = [[f"<b>{n} balls</b>", f"{m.margin} wkts", rec.team_link(m.winner), rec.team_link(m.b if m.winner == m.a else m.a),
                 self.when(m.match_id)] for n, m in sp]
        out += "<h3>Biggest wins by balls to spare</h3>" + table(["To spare", "Margin", "Winner", "Beaten", "Date"], rows, num_from=5)
        ties = sorted((m for m in rec.arch.matches.values() if m.result_type == "tie"), key=lambda m: m.day)
        rows = [[self.when(m.match_id), rec.team_link(m.a), rec.team_link(m.b), rec.team_link(m.winner) if m.winner else "-"] for m in ties]
        out += f"<h3>Super overs ({len(ties)})</h3>" + table(["Date", "Team", "Team", "Won the super over"], rows, num_from=4)
        return section("Teams and results", out)

    def headline_cards(self) -> list:
        """[A318] The first row of each list below, as cards in the colours of the side it was
        done for: the same sort as the list, so a card can never disagree with its table."""
        a, rec = self.a, self.rec
        out = []

        def card(label, value, person, fs, detail):
            out.append(V.RecordCard(label, value, a.name(person), detail, a.key(fs), A_path(a, person) or None,
                                    a.crest(fs)))

        b = sorted(rec.arch.bats, key=lambda b: (-b.runs, b.balls, a.name(b.person)))[0]
        card("Highest score", f"{b.runs}{'' if b.out else '*'}", b.person, b.fs,
             f"off {b.balls} balls, {rec.arch.matches[b.match_id].year}")
        for label, bucket in (("Fastest fifty", self.ps.fifties), ("Fastest hundred", self.ps.hundreds)):
            balls, _r, p, mid, fs = sorted(bucket, key=lambda r: (r[0], a.name(r[2]), r[3]))[0]
            card(label, f"{balls} balls", p, fs, f"v {rec.team(rec.opponent(mid, fs))}, {rec.arch.matches[mid].year}")
        w = sorted((b for b in rec.arch.bowls if b.wickets), key=lambda b: (-b.wickets, b.runs, a.name(b.person)))[0]
        card("Best figures", f"{w.wickets}/{w.runs}", w.person, w.fs,
             f"v {rec.team(rec.opponent(w.match_id, w.fs))}, {rec.arch.matches[w.match_id].year}")
        pt = sorted(self.ps.partnerships, key=lambda p: (-p.runs, p.balls))[0]
        out.append(V.RecordCard("Highest partnership", str(pt.runs), f"{a.name(pt.a)} & {a.name(pt.b)}",
                                f"off {pt.balls} balls, {rec.arch.matches[pt.match_id].year}", a.key(pt.fs), None,
                                a.crest(pt.fs)))
        full = [i for i in self.ps.innings if self.full_length(i)]
        hi = sorted(full, key=lambda i: (-i.runs, i.balls))[0]
        out.append(V.RecordCard("Highest total", score_text(hi).split(" (")[0], rec.team(hi.fs),
                                f"v {rec.team(rec.opponent(hi.match_id, hi.fs))}, {rec.arch.matches[hi.match_id].year}",
                                a.key(hi.fs), f"/teams/{slug(rec.franchise(hi.fs))}", a.crest(hi.fs)))
        lo = sorted((i for i in full if i.wickets == 10 or i.balls == 120), key=lambda i: (i.runs, -i.balls))[0]
        out.append(V.RecordCard("Lowest total", score_text(lo).split(" (")[0], rec.team(lo.fs),
                                f"v {rec.team(rec.opponent(lo.match_id, lo.fs))}, {rec.arch.matches[lo.match_id].year}",
                                a.key(lo.fs), f"/teams/{slug(rec.franchise(lo.fs))}", a.crest(lo.fs)))
        out.append(V.RecordCard("Hat-tricks", str(len(self.ps.hat_tricks)), "in the IPL",
                                f"since {rec.years[0]}", None, None))
        return out

    def records_page(self) -> str:
        intro = para("The IPL's record book from every ball since 2008: the highest scores and the fastest fifties, "
                     "the best spells, the biggest partnerships, the highest totals and chases, and every hat-trick "
                     "and super over.",
                     "Every figure is worked out from the ball-by-ball archive, in ball order, so a fastest fifty is "
                     "the ball the fiftieth run came off, not an estimate from a scorecard. Matches cut short by rain "
                     "are left out of the team totals.")
        body = (section("The record book", intro) + section("The headline records", V.record_wall(self.headline_cards()))
                + '<div class="al-tabbed">' + self.batting_records() + self.bowling_records()
                + self.partnership_records() + self.team_records() + "</div>" + _back())
        return _page(self.a, "/almanack/records", "IPL records: every ball since 2008",
                     "The IPL record book: highest scores, fastest fifties and hundreds, best figures, hat-tricks, "
                     "partnerships, highest totals and chases, and every super over.", "ALMANACK", "Records", body)

    # ---------------------------------------------------------------- grounds
    def ground_page(self, g: Ground) -> str:
        a, rec = self.a, self.rec
        ms = g.matches
        years = sorted({m.year for m in ms})
        home = Counter()
        for m in ms:
            home[rec.franchise(m.a)] += 1
            home[rec.franchise(m.b)] += 1
        firsts = [self.innings_by_match[m.match_id][0] for m in ms
                  if self.innings_by_match.get(m.match_id) and self.full_length(self.innings_by_match[m.match_id][0])]
        avg1 = sum(i.runs for i in firsts) / len(firsts) if firsts else None
        decided = [m for m in ms if m.winner is not None and m.result_type in ("runs", "wickets")]
        chased = sum(1 for m in decided if m.result_type == "wickets")
        toss = [m for m in ms if m.winner is not None and self.toss.get(m.match_id) is not None]
        toss_won = sum(1 for m in toss if self.toss[m.match_id] == m.winner)
        lead = [para(
            f"{esc(g.name)} in {esc(g.city)} has staged {plural(len(ms), 'IPL match', 'IPL matches')} in "
            f"{plural(len(years), 'season')} ({years[0] if len(years) == 1 else f'{years[0]} to {years[-1]}'}).",
            f"Most often it has been the home of {_teams(home)}." if home else "",
            (f"A full twenty-over first innings here has averaged {avg1:.0f} runs." if avg1 else ""),
            (f"Of {plural(len(decided), 'match', 'matches')} decided outright, the side batting second won "
             f"{chased} ({100 * chased / len(decided):.0f}%)." if decided else ""),
            (f"The side that won the toss went on to win {toss_won} of {len(toss)} ({100 * toss_won / len(toss):.0f}%)."
             if toss else ""))]
        body = section("The ground", "".join(lead))
        style = self._ground_styles(g)
        if style:
            rows = [[k.capitalize(), f"{100 * v[0] / max(1, sum(x[0] for x in style.values())):.0f}%", overs(v[0]),
                     rate(6 * v[1], v[0], 2), str(v[2]), rate(v[0], v[2], 1)] for k, v in style.items()]
            body += section("Pace and spin", table(["", "Share of overs", "Overs", "Econ", "Wkts", "Balls per wicket"], rows))
        inns = [i for m in ms for i in self.innings_by_match.get(m.match_id, []) if self.full_length(i)]
        if inns:
            hi = sorted(inns, key=lambda i: (-i.runs, i.balls))[:5]
            lo = sorted((i for i in inns if i.wickets == 10 or i.balls == 120), key=lambda i: (i.runs, -i.balls))[:5]
            rows = [[f"<b>{score_text(i)}</b>", rec.team_link(i.fs), self.vs(i.match_id, i.fs), self.when(i.match_id)] for i in hi]
            t = "<h3>Highest totals</h3>" + table(["Total", "Team", "Against", "Date"], rows, num_from=4)
            rows = [[f"<b>{score_text(i)}</b>", rec.team_link(i.fs), self.vs(i.match_id, i.fs), self.when(i.match_id)] for i in lo]
            t += "<h3>Lowest totals</h3>" + table(["Total", "Team", "Against", "Date"], rows, num_from=4)
            body += section("Totals", t)
        mids = {m.match_id for m in ms}
        runs, wkts = Counter(), Counter()
        for b in rec.arch.bats:
            if b.match_id in mids:
                runs[b.person] += b.runs
        for b in rec.arch.bowls:
            if b.match_id in mids:
                wkts[b.person] += b.wickets
        lead_bat = sorted(runs.items(), key=lambda kv: (-kv[1], a.name(kv[0])))[:5]
        lead_bowl = sorted((kv for kv in wkts.items() if kv[1]), key=lambda kv: (-kv[1], a.name(kv[0])))[:5]
        t = "<h3>Most runs here</h3>" + table(["Batter", "Runs"], [[a.link(p), f"<b>{n:,}</b>"] for p, n in lead_bat])
        if lead_bowl:
            t += "<h3>Most wickets here</h3>" + table(["Bowler", "Wkts"], [[a.link(p), f"<b>{n}</b>"] for p, n in lead_bowl])
        body += section("Leaders at the ground", t)
        top_inn = max((b for b in rec.arch.bats if b.match_id in mids), key=lambda b: (b.runs, -b.balls), default=None)
        top_sp = max((b for b in rec.arch.bowls if b.match_id in mids), key=lambda b: (b.wickets, -b.runs), default=None)
        best = []
        if top_inn:
            best.append(f"The highest score here is {a.link(top_inn.person)}'s {top_inn.runs}{'' if top_inn.out else '*'} "
                        f"off {top_inn.balls} balls against {esc(rec.team(rec.opponent(top_inn.match_id, top_inn.fs)))} "
                        f"on {H.day(rec.arch.matches[top_inn.match_id].day)}.")
        if top_sp and top_sp.wickets:
            best.append(f"The best bowling is {a.link(top_sp.person)}'s {top_sp.wickets}/{top_sp.runs} against "
                        f"{esc(rec.team(rec.opponent(top_sp.match_id, top_sp.fs)))} on "
                        f"{H.day(rec.arch.matches[top_sp.match_id].day)}.")
        if best:
            body += section("Best days", para(*best))
        body += _back(extra='<a class="go ghost" href="/almanack/grounds">All grounds</a>')
        return _page(self.a, f"/almanack/grounds/{g.slug}", f"{g.name}, {g.city}: IPL record",
                     f"{g.name} in IPL cricket: {len(ms)} matches, average first-innings score, chasing record, pace "
                     "and spin, highest and lowest totals, and the leading run-scorers and wicket-takers.",
                     "ALMANACK · GROUND", g.name, body, index=len(ms) >= GROUND_MIN_MATCHES)

    def _ground_styles(self, g: Ground) -> dict:
        st = self.styles
        out = {}
        for m in g.matches:
            for k, v in st.get(m.match_id, {}).items():
                acc = out.setdefault(k, [0, 0, 0])
                for i in range(3):
                    acc[i] += v[i]
        return {k: out[k] for k in ("pace", "spin") if k in out and out[k][0]}

    def grounds_index(self) -> str:
        rows = []
        for g in sorted(self.grounds.values(), key=lambda g: (-len(g.matches), g.name)):
            firsts = [self.innings_by_match[m.match_id][0] for m in g.matches
                      if self.innings_by_match.get(m.match_id) and self.full_length(self.innings_by_match[m.match_id][0])]
            decided = [m for m in g.matches if m.winner is not None and m.result_type in ("runs", "wickets")]
            chased = sum(1 for m in decided if m.result_type == "wickets")
            sty = self._ground_styles(g)
            tot = sum(v[0] for v in sty.values())
            rows.append([f'<a href="/almanack/grounds/{g.slug}">{esc(g.name)}</a>', esc(g.city), str(len(g.matches)),
                         f"{sum(i.runs for i in firsts) / len(firsts):.0f}" if firsts else "-",
                         f"{100 * chased / len(decided):.0f}%" if decided else "-",
                         f"{100 * sty['spin'][0] / tot:.0f}%" if tot and "spin" in sty else "-"])
        cards = []
        for g in sorted(self.grounds.values(), key=lambda g: (-len(g.matches), g.name))[:GROUND_CARDS]:
            cards.append(self._ground_card(g))
        body = section("Every ground", para(
            f"All {len(self.grounds)} grounds that have staged an IPL match, in India, South Africa and the "
            "United Arab Emirates.",
            "The average first innings counts only innings scheduled for twenty overs; the chasing figure is the "
            "share of matches decided outright that the side batting second won.")
            + f'<div class="al-grounds">{"".join(cards)}</div>'
            + "<h3>All the grounds</h3>"
            + table(["Ground", "City", "Matches", "Avg 1st inns", "Won chasing", "Spin share"], rows, num_from=2)
            + '<p class="hx-note">A ground is one place under every name it has had: the archive writes Delhi\'s '
              "ground as both Feroz Shah Kotla and Arun Jaitley Stadium, and Pune's as both Subrata Roy Sahara Stadium "
              "and the MCA Stadium.</p>") + _back()
        return _page(self.a, "/almanack/grounds", "Every IPL ground", "Every ground that has staged an IPL match, "
                     "with its average first-innings score, chasing record and how much spin it sees.",
                     "ALMANACK", "Grounds", body)

    def _ground_card(self, g: Ground) -> str:
        """[A318] A ground as a card in its home club's colours, with its three telling
        figures as bars: the first innings against a 130-210 range, chases won, spin's share."""
        from web.crests import franchise_crest
        home = Counter()
        for m in g.matches:
            home[self.rec.franchise(m.a)] += 1
            home[self.rec.franchise(m.b)] += 1
        club = home.most_common(1)[0][0] if home else None
        crest = franchise_crest(club) if club else None
        key = crest.rsplit("/", 1)[1].split("-", 1)[0] if crest else None
        firsts = [self.innings_by_match[m.match_id][0] for m in g.matches
                  if self.innings_by_match.get(m.match_id) and self.full_length(self.innings_by_match[m.match_id][0])]
        decided = [m for m in g.matches if m.winner is not None and m.result_type in ("runs", "wickets")]
        sty = self._ground_styles(g)
        tot = sum(v[0] for v in sty.values())
        meters = ""
        if firsts:
            avg = sum(i.runs for i in firsts) / len(firsts)
            meters += V.meter("Avg first innings", f"{avg:.0f}", (avg - 130) / 80)
        if decided:
            ch = sum(1 for m in decided if m.result_type == "wickets") / len(decided)
            meters += V.meter("Won chasing", f"{100 * ch:.0f}%", ch)
        if tot and "spin" in sty:
            sp = sty["spin"][0] / tot
            meters += V.meter("Spin share", f"{100 * sp:.0f}%", sp)
        img = f'<img src="{crest}" alt="" width="44" height="44" loading="lazy">' if crest else ""
        return (f'<a class="al-ground {V.crest_cls(key)}" href="/almanack/grounds/{g.slug}">{img}'
                f'<b>{esc(g.name)}</b><span>{esc(g.city)} · {plural(len(g.matches), "match", "matches")}</span>'
                f"{meters}</a>")

    # ---------------------------------------------------------------- head to head
    def h2h_document(self) -> str:
        a = self.a
        people, idx, pairs = [], {}, []
        for bat, bowl, balls, runs, outs in sorted(self.h2h_all, key=lambda r: (r[0], r[1])):
            for p in (bat, bowl):
                if p not in idx:
                    idx[p] = len(people)
                    people.append([a.name(p), A_path(a, p)])
            pairs.append([idx[bat], idx[bowl], balls, runs, outs])
        return json.dumps({"people": people, "pairs": pairs}, separators=(",", ":"), ensure_ascii=False) + "\n"

    def h2h_page(self) -> str:
        a = self.a
        big = [r for r in self.h2h_all if r[2] >= RIVALRY_MIN_BALLS]
        most = sorted(big, key=lambda r: (-r[2], a.name(r[0])))[:15]
        rows = [[a.link(bt), a.link(bw), f"<b>{b}</b>", str(r), rate(100 * r, b, 1), str(o)] for bt, bw, b, r, o in most]
        t = "<h3>The longest rivalries</h3>" + table(["Batter", "Bowler", "Balls", "Runs", "SR", "Out"], rows, num_from=2)
        owned = sorted(big, key=lambda r: (-r[4], r[3], a.name(r[1])))[:15]
        rows = [[a.link(bw), a.link(bt), f"<b>{o}</b>", str(b), str(r), rate(b, o, 1)] for bt, bw, b, r, o in owned]
        t += "<h3>Bowlers who had a batter's number</h3>" + table(["Bowler", "Batter", "Dismissals", "Balls", "Runs", "Balls per dismissal"], rows, num_from=2)
        tamed = sorted((r for r in big if r[4] == 0), key=lambda r: (-r[3] / r[2], -r[2]))[:15]
        rows = [[a.link(bt), a.link(bw), f"<b>{rate(100 * r, b, 1)}</b>", str(b), str(r)] for bt, bw, b, r, o in tamed]
        t += "<h3>Batters never dismissed by a bowler, fastest-scoring first</h3>" + table(["Batter", "Bowler", "SR", "Balls", "Runs"], rows, num_from=2)
        finder = (f'<div class="al-h2h" data-src="{self.h2h_url}"><input id="h2hBat" class="room-input" placeholder="Batter" aria-label="Batter" list="h2hBats" oninput="h2hFind()">'
                  '<input id="h2hBowl" class="room-input" placeholder="Bowler" aria-label="Bowler" list="h2hBowls" oninput="h2hFind()">'
                  '<datalist id="h2hBats"></datalist><datalist id="h2hBowls"></datalist></div>'
                  '<div id="h2hOut" class="al-h2h-out" aria-live="polite"></div>')
        body = (section("Head to head", para(
            "Every batter against every bowler they met, from the ball-by-ball archive. Type a batter, a bowler or "
            f"both to see the balls between them; pairs that met for at least {H2H_LIST_MIN} balls are listed.",
            "\"Out\" counts dismissals credited to the bowler, so a run out is not in it, and a ball faced is any ball "
            "that was not a wide.") + finder)
            + section("Rivalries", t + f'<p class="hx-note">Pairs that met for at least {RIVALRY_MIN_BALLS} balls.</p>')
            + _back() + '<script src="/static/almanack.js"></script>\n')
        return _page(self.a, "/almanack/head-to-head", "IPL head to head: every batter against every bowler",
                     "Every IPL batter against every bowler: balls, runs, strike rate and dismissals, with the longest "
                     "rivalries and the bowlers who had a batter's number.", "ALMANACK", "Head to head", body)


def A_path(a, person: str) -> str:
    from tools.build_almanack import player_path
    return player_path(a.name(person)) if person in a.careers else ""


def _teams(c: Counter) -> str:
    top = c.most_common(2)
    if len(top) == 2 and top[1][1] >= top[0][1] / 2:
        return f'<a href="/teams/{slug(top[0][0])}">{esc(top[0][0])}</a> and <a href="/teams/{slug(top[1][0])}">{esc(top[1][0])}</a>'
    return f'<a href="/teams/{slug(top[0][0])}">{esc(top[0][0])}</a>'


def _back(extra: str = "") -> str:
    return (f'    <p class="hx-pager actions">{extra}<a class="go ghost" href="/almanack">The Almanack</a></p>\n')


def _page(a, path, title, desc, over, heading, body, index=True):
    from tools.build_almanack import page
    return page(path, title, desc, over, heading, body, index=index)
