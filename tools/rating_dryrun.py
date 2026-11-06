"""Dry-run a change to the rating view: which cards it moves, why, and what it does to the
yardsticks the ratings are judged by. [A256]

    uv run python -m tools.rating_dryrun                          # today's view, yardsticks only
    uv run python -m tools.rating_dryrun migrations/052_x.sql      # a candidate, rolled back
    uv run python -m tools.rating_dryrun migrations/052_x.sql --list 40

The candidate migration is run INSIDE A TRANSACTION THAT IS ALWAYS ROLLED BACK, so nothing
is applied and the file stays a draft (CLAUDE.md: a migration is immutable the moment it
has been applied to any database, local included). LOCAL DATABASE ONLY: this reads
`deliveries` for the yardsticks, which is exactly the traffic that used up Neon's transfer
allowance (A173).

Every card that moves is put down to ONE cause, read off the view's own columns:

    own season  its `merit` changed            -- the change is about this season
    career      its `career_merit` changed     -- another season of the same player moved
    blend       merit and career unchanged, `blended_merit` changed -- the career rule
    scale       nothing of its own changed     -- only the shared anchors and league mean

A "scale" move is collateral: a card the change was not about, moved because the 70-99
anchors re-derive from everybody (A44/A74). The run FAILS (exit 1) if any such card moves
by more than `MAX_RIPPLE` points, which is the operational meaning of "fix the wrong
ratings without moving the right ones".

It also reports whether any ENGINE number moved (`scoring_per_ball`,
`dismissal_multiplier`, A160). A card correction should move none, and a run that moves
one says so on its first line rather than leaving it to be noticed.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field

import psycopg
from dotenv import load_dotenv

MAX_RIPPLE = 1          # the most a card the change was NOT about may move
EPS = 1e-9
ANCHOR_LO, ANCHOR_HI = 0.02, 0.992   # the view's own anchors (A118); checked, not trusted
SCALE_LO = 72.0      # [A267] the scale is 72-99 (70-99 until migration 054)

# A scorecard contradiction: one season clearly beat another in the same year, yet has
# the lower card. The definitions are the sweep's (2026-11-05), named so they cannot drift.
BAT_MIN_MATCHES, BAT_MIN_BALLS, BAT_RUNS_MARGIN = 7, 100, 30
BOWL_MIN_MATCHES, BOWL_MIN_BALLS, BOWL_WICKETS_MARGIN, BOWL_BALLS_SLACK = 7, 150, 3, 12
FULL_SEASON = 10        # matches, for the Player-of-the-Match parity table


@dataclass
class CardRow:
    """One player-season as the view rates it, with its raw season figures."""
    key: tuple
    name: str
    year: int
    role: str
    display: int
    merit: float | None
    career: float | None
    blended: float
    matches: int
    potm: float
    engine: dict = field(default_factory=dict)     # discipline -> (scoring, dismissal)
    cohort: dict = field(default_factory=dict)     # discipline -> cohort
    bpm: dict = field(default_factory=dict)        # discipline -> balls a match
    runs: int = 0
    faced: int = 0
    outs: int = 0
    bowled: int = 0
    conceded: int = 0
    wickets: int = 0
    played: int = 0

    @property
    def strike_rate(self) -> float:
        return 100 * self.runs / self.faced if self.faced else 0.0

    @property
    def economy(self) -> float:
        return 6 * self.conceded / self.bowled if self.bowled else 99.0


VIEW_SQL = """
    select franchise_season_id, person_id, discipline, season_year, cohort, balls, matches,
           merit, career_merit, blended_merit, display_rating, potm_awards,
           scoring_per_ball, dismissal_multiplier
    from player_season_rating
"""

STATS_SQL = """
    with bat as (
        select batting_fs_id fs, batter_id pid, sum(runs_batter) runs,
               count(*) filter (where extra_wides = 0) faced
        from deliveries where not is_super_over group by 1, 2),
    outs as (
        select batting_fs_id fs, player_out_id pid, count(*) outs
        from deliveries
        where not is_super_over and player_out_id is not null
          and wicket_kind not in ('retired hurt', 'retired not out')
        group by 1, 2),
    bowl as (
        select bowling_fs_id fs, bowler_id pid,
               count(*) filter (where legal_ball) bowled,
               sum(runs_batter + extra_wides + extra_noballs) conceded,
               count(*) filter (where credited_to_bowler and player_out_id is not null) wk
        from deliveries where not is_super_over group by 1, 2),
    app as (
        select franchise_season_id fs, person_id pid,
               count(*) filter (where participated) played
        from appearances group by 1, 2)
    select s.franchise_season_id, s.person_id, coalesce(p.display_name, p.primary_name),
           s.role, coalesce(bat.runs, 0), coalesce(bat.faced, 0), coalesce(outs.outs, 0),
           coalesce(bowl.bowled, 0), coalesce(bowl.conceded, 0), coalesce(bowl.wk, 0),
           coalesce(app.played, 0)
    from squad_members s
    join people p using (person_id)
    left join bat on bat.fs = s.franchise_season_id and bat.pid = s.person_id
    left join outs on outs.fs = s.franchise_season_id and outs.pid = s.person_id
    left join bowl on bowl.fs = s.franchise_season_id and bowl.pid = s.person_id
    left join app on app.fs = s.franchise_season_id and app.pid = s.person_id
"""


def _f(v):
    return None if v is None else float(v)


def read_cards(conn, stats: dict) -> dict:
    """The view's cards, one per player-season, joined to their raw season figures."""
    cards: dict[tuple, CardRow] = {}
    for (fs, pid, disc, year, cohort, balls, matches, merit, career, blended, display,
         potm, scoring, dismissal) in conn.execute(VIEW_SQL):
        key = (fs, pid)
        c = cards.get(key)
        if c is None:
            name, role, *figures = stats[key]
            c = cards[key] = CardRow(key, name, year, role, display, _f(merit), _f(career),
                                     float(blended), matches, float(potm or 0))
            (c.runs, c.faced, c.outs, c.bowled, c.conceded, c.wickets, c.played) = figures
        c.matches = max(c.matches, matches)
        c.cohort[disc] = cohort
        c.bpm[disc] = balls / matches if matches else 0.0
        c.engine[disc] = (_f(scoring), _f(dismissal))
    return cards


def read_stats(conn) -> dict:
    return {(fs, pid): rest for fs, pid, *rest in conn.execute(STATS_SQL)}


# --- pure: everything below is testable without a database ---------------------------

def percentile(values: list[float], q: float) -> float:
    """Postgres's `percentile_cont`: linear between the two nearest ranks."""
    v = sorted(values)
    k = (len(v) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def scale_of(cards: dict) -> tuple[float, float] | None:
    """The view's anchors, recomputed -- or None if they no longer reproduce every card,
    in which case anything that depends on them is skipped rather than reported wrong."""
    rated = [c for c in cards.values() if c.merit is not None]
    lo = percentile([c.blended for c in rated], ANCHOR_LO)
    hi = percentile([c.blended for c in rated], ANCHOR_HI)
    if any(to_display(c.blended, lo, hi) != c.display for c in rated):
        return None
    return lo, hi


def to_display(x: float, lo: float, hi: float) -> int:
    t = max(0.0, min(1.0, (x - lo) / (hi - lo)))
    return int(SCALE_LO + (99.0 - SCALE_LO) * t + 0.5)   # Postgres rounds half away from zero


def _differs(a, b) -> bool:
    if a is None or b is None:
        return (a is None) != (b is None)
    return abs(a - b) > EPS


def _shift(a, b) -> float:
    return 0.0 if a is None or b is None else abs(b - a)


def cause(old: CardRow, new: CardRow, player_changed: bool) -> str:
    """Why one card moved.

    `career` is NOT read off `career_merit` changing, because every career moves a little
    whenever the LEAGUE mean does (CAREER_N = 2 shrinks it toward the league), so any
    change to anybody's merit nudges every card in the deck. Reading that as "career"
    emptied the "scale" bucket and made the gate unable to fire -- found on the first
    candidate run, where all 100 collateral moves were filed as career. So: a career cause
    needs another season OF THE SAME PLAYER to have changed, and a blend cause needs the
    blend to have moved further than its career did (a career shift alone moves a convex
    blend by at most that shift). Everything else is the shared anchors and league mean.
    """
    if _differs(old.merit, new.merit):
        return "own season"
    if player_changed:
        return "career"
    if _shift(old.blended, new.blended) > _shift(old.career, new.career) + EPS:
        return "blend"
    return "scale"


def classify(base: dict, cand: dict) -> list[tuple[int, str, CardRow, CardRow]]:
    """Every card whose display moved: (points, cause, before, after)."""
    if base.keys() != cand.keys():
        raise SystemExit(f"the candidate rates a different set of player-seasons: "
                         f"{len(base.keys() - cand.keys())} gone, "
                         f"{len(cand.keys() - base.keys())} new")
    changed = defaultdict(set)          # person -> his seasons whose own merit moved
    for k in base:
        if _differs(base[k].merit, cand[k].merit):
            changed[k[1]].add(k)
    return [(cand[k].display - base[k].display,
             cause(base[k], cand[k], bool(changed[k[1]] - {k})), base[k], cand[k])
            for k in base if cand[k].display != base[k].display]


def engine_moved(base: dict, cand: dict) -> int:
    """How many player-season disciplines the ENGINE plays differently."""
    n = 0
    for k, b in base.items():
        for disc, (s, d) in b.engine.items():
            s2, d2 = cand[k].engine.get(disc, (None, None))
            n += _differs(s, s2) or _differs(d, d2)
    return n


def contradictions(cards: dict) -> dict[str, int]:
    """Same-season pairs where the card says the opposite of a clear scorecard."""
    by_year = defaultdict(list)
    for c in cards.values():
        by_year[c.year].append(c)
    out = {"batting pairs": 0, "batting contradicted": 0,
           "bowling pairs": 0, "bowling contradicted": 0}
    for season in by_year.values():
        bat = [c for c in season if c.role in ("batter", "keeper")
               and c.played >= BAT_MIN_MATCHES and c.faced >= BAT_MIN_BALLS]
        for a in bat:
            for b in bat:
                if (a is not b and a.runs >= b.runs + BAT_RUNS_MARGIN
                        and a.strike_rate > b.strike_rate and a.outs <= b.outs):
                    out["batting pairs"] += 1
                    out["batting contradicted"] += a.display < b.display
        bowl = [c for c in season if c.role == "bowler"
                and c.played >= BOWL_MIN_MATCHES and c.bowled >= BOWL_MIN_BALLS]
        for a in bowl:
            for b in bowl:
                if (a is not b and a.wickets >= b.wickets + BOWL_WICKETS_MARGIN
                        and a.economy < b.economy
                        and a.bowled >= b.bowled - BOWL_BALLS_SLACK):
                    out["bowling pairs"] += 1
                    out["bowling contradicted"] += a.display < b.display
    return out


def cap_holders(cards: dict) -> list[tuple[int, CardRow, CardRow]]:
    """Each season's leading run-scorer and wicket-taker (fewest runs conceded on a tie)."""
    by_year = defaultdict(list)
    for c in cards.values():
        by_year[c.year].append(c)
    return [(y, max(s, key=lambda c: c.runs), max(s, key=lambda c: (c.wickets, -c.conceded)))
            for y, s in sorted(by_year.items())]


def potm_band(rate: float) -> str:
    return "0" if rate == 0 else "<=.10" if rate <= .10 else "<=.20" if rate <= .20 else ">.20"


def potm_parity(cards: dict) -> dict[tuple, list[int]]:
    """Cards of full seasons by (main discipline, its cohort, Player-of-the-Match rate).
    Player of the Match is the one human judgement in the archive (A56): at the same award
    rate, every role should read about the same."""
    out = defaultdict(list)
    for c in cards.values():
        if c.matches < FULL_SEASON or not c.bpm:
            continue
        disc = max(c.bpm, key=lambda d: c.bpm[d] / (18.0 if d == "batting" else 24.0))
        out[(disc, c.cohort[disc], potm_band(c.potm / c.matches))].append(c.display)
    return out


# --- report ----------------------------------------------------------------------------

def _label(c: CardRow) -> str:
    return (f"{c.name} {c.year} [{c.role}] m{c.played} {c.runs}r sr{c.strike_rate:.0f} "
            f"{c.wickets}w eco{c.economy:.2f}")


def engine_table(cards: dict, scale) -> list[str]:
    """At the same card number, do career-lifted cards play worse? (the card-engine gap)"""
    from game.career import legend_value
    from tools.snapshot_deck import deck_from, read_document

    doc = read_document()
    if doc is None or scale is None:
        return ["  (skipped: no deck snapshot, or the anchors no longer reproduce)"]
    lo, hi = scale
    value = {(fs, c.person_id): legend_value(c)
             for fs, cs in deck_from(doc).cards_by_fs.items() for c in cs}
    groups = defaultdict(lambda: ([], []))
    for k, c in cards.items():
        if c.merit is None or k not in value:
            continue
        lift = c.display - to_display(c.merit, lo, hi)
        groups[c.display // 3 * 3][lift >= 5].append(value[k])
    lines = []
    for band in sorted(groups):
        flat, lifted = groups[band]
        if len(flat) >= 10 and len(lifted) >= 10:
            lines.append(f"  card {band}-{band + 2}: not lifted {statistics.fmean(flat):+.2f} "
                         f"(n{len(flat)})   lifted 5+ {statistics.fmean(lifted):+.2f} "
                         f"(n{len(lifted)}) runs a match")
    return lines or ["  (no band has ten of each)"]


def report(base: dict, cand: dict | None, top: int, show: list[str] = ()) -> int:
    failed = 0
    if cand is not None:
        moved = classify(base, cand)
        eng = engine_moved(base, cand)
        print(f"ENGINE numbers moved: {eng}" + ("" if eng == 0 else
              "   <-- this change reaches what the engine plays (A160)"))
        print(f"cards moved: {len(moved)} of {len(base)}")
        by_cause = defaultdict(list)
        for m in moved:
            by_cause[m[1]].append(m)
        for name in ("own season", "career", "blend", "scale"):
            ms = by_cause.get(name, [])
            sizes = defaultdict(int)
            for d, *_ in ms:
                sizes[min(abs(d), 5)] += 1
            dist = ", ".join(f"{'5+' if s == 5 else s}: {n}" for s, n in sorted(sizes.items()))
            print(f"  {name:10s} {len(ms):5d}   by points moved: {dist or '-'}")
        ripple = [m for m in by_cause.get("scale", []) if abs(m[0]) > MAX_RIPPLE]
        if ripple:
            failed = 1
            print(f"FAIL: {len(ripple)} cards the change was not about moved by more than "
                  f"{MAX_RIPPLE}:")
            for d, _, a, b in sorted(ripple, key=lambda m: -abs(m[0]))[:top]:
                print(f"    {a.display}->{b.display}  {_label(a)}")
        else:
            print(f"PASS: no card outside the change moved by more than {MAX_RIPPLE}")
        print(f"\nlargest moves (top {top}):")
        for d, why, a, b in sorted(moved, key=lambda m: -abs(m[0]))[:top]:
            print(f"  {a.display:2d}->{b.display:2d} {d:+d}  {why:10s} {_label(a)}")

    for title, cs in (("today", base), ("candidate", cand)):
        if cs is None:
            continue
        print(f"\n== yardsticks: {title} ==")
        k = contradictions(cs)
        print(f"  scorecard contradictions: batting {k['batting contradicted']} of "
              f"{k['batting pairs']}, bowling {k['bowling contradicted']} of "
              f"{k['bowling pairs']}")
        table = potm_parity(cs)
        bands = ["0", "<=.10", "<=.20", ">.20"]
        print("  mean card at the same Player-of-the-Match rate (full seasons):")
        print("  " + " " * 20 + "".join(f"{b:>13s}" for b in bands))
        for co in sorted({k2[:2] for k2 in table}):
            cells = []
            for b in bands:
                v = table.get((*co, b), [])
                cells.append(f"{statistics.fmean(v):7.1f} n{len(v):<4d}" if v else f"{'-':>13s}")
            print(f"  {co[0] + ' ' + co[1]:20s}" + "".join(cells))
        print("  card against what the engine plays:")
        print("\n".join(engine_table(cs, scale_of(cs))))

    for name in show:
        print(f"\n== {name} ==")
        for k in sorted((k for k, c in base.items() if c.name == name),
                        key=lambda k: base[k].year):
            after = "" if cand is None else f" -> {cand[k].display}"
            print(f"  {base[k].year}  {base[k].display}{after}   {_label(base[k])}")

    print("\n== cap holders ==")
    for (y, o, p) in cap_holders(base):
        ob = f"{o.display}" if cand is None else f"{o.display}->{cand[o.key].display}"
        pb = f"{p.display}" if cand is None else f"{p.display}->{cand[p.key].display}"
        print(f"  {y}  Orange {o.name} {o.runs} ({ob})   Purple {p.name} {p.wickets} ({pb})")
    return failed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("candidate", nargs="?", help="a draft view migration to dry-run")
    ap.add_argument("--list", type=int, default=25, help="how many moved cards to list")
    ap.add_argument("--show", action="append", default=[], metavar="NAME",
                    help="every season of this player, before and after (repeatable)")
    args = ap.parse_args()
    load_dotenv()
    url = os.environ["DATABASE_URL"]
    if not any(h in url for h in ("localhost", "127.0.0.1", "[::1]")):
        raise SystemExit("rating_dryrun runs against the LOCAL database only (A173)")
    with psycopg.connect(url) as conn:
        stats = read_stats(conn)
        base = read_cards(conn, stats)
        cand = None
        if args.candidate:
            try:
                conn.execute(open(args.candidate).read())
                cand = read_cards(conn, stats)
            finally:
                conn.rollback()
            left = conn.execute("select count(*) from player_season_rating").fetchone()[0]
            assert left == sum(len(c.engine) for c in base.values()), "rollback failed"
    sys.exit(report(base, cand, args.list, args.show))


if __name__ == "__main__":
    main()
