"""SPEC 7.1 per-ball impact, computed in memory and printed. No schema, no writes.

    uv run python -m etl.impact                        the distributions
    uv run python -m etl.impact --player "V Kohli"     one player's seasons, batting
    uv run python -m etl.impact --bowler "YS Chahal"   one player's seasons, bowling
    uv run python -m etl.impact --reconcile "V Kohli"  scored totals vs the scorecard

Deliberately writes nothing. The ratings grain is migration 009's to settle and the
shrinkage constant with it, so this module produces evidence and stops. It has no
`--write`.

The formula, from SPEC 7.1:

    impact = (runs - E[runs | state]) - (was_out - P[out | state]) * wicket_cost(state)

reversed in sign for the bowler. **Scoring set is not the fitting set**: second innings and
miscounted overs are scored but not fitted; reduced and unknown-length innings are neither.

**One state-resolution rule, both halves.** A ball's impact has a runs half and a wicket
half, and each needs a reference state. Where the exact state is too thin to trust, both
halves fall back the same way - to the nearest state in the *same over* along the wicket
axis - because two halves resolving differently would price one ball against two different
reference states. `Grid` does it at bucketed-wicket grain and `Costs` at exact-wicket
grain, which is the A31 split, but the walk is the same walk. Every use is counted.

**Whose dismissal.** [Ratified 2026-07-30] Batting is scored on `player_out_id = batter_id`
only. A non-striker run out is priced to the player who did not cause it, and while the
aggregate effect is 0.001 runs/ball the per-player effect reaches 0.193 and moves top-20
membership. The any-wicket reading is still fitted and printed **as a diagnostic only** -
nothing consumes it. Bowling is scored on `credited_to_bowler`, which excludes run outs on
the same principle.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter, defaultdict
from typing import NamedTuple

from etl.db import connect
from etl.state_model import (
    BUCKETS,
    FITTING_SET,
    MIN_OBSERVATIONS,
    NOT_A_WICKET,
    bucket_of,
    fetch,
    wicket_cost,
)

# Scoring set (SPEC 7.1's table). `not is_super_over` is redundant against the 120 - see
# validation check 6 - and is kept because a reader should not have to know that.
SCORING_SET = "not is_super_over and innings_scheduled_balls = 120"

BUCKET_ORDER = tuple(label for label, _ in BUCKETS)

# [Ratified 2026-07-30] The shrinkage constant, in balls. **Provisional.** Chosen from the
# spread-retained table: k >= 400 collapses the season-to-season contrast SPEC 7 is built
# on (38% retained, and k = 800 turns Kohli's genuinely poor 2008 positive), while k = 50
# and k = 100 both pass the within-player ordering test and k = 100 pulls harder on the
# noise. **Not settled**: it must be re-checked after SPEC 7.4's within-season
# normalisation, which may allow a lower value.
SHRINKAGE_K = 100

# [Ratified 2026-07-30] Below these a player-season is not rated at all - not shrunk, not
# floored, simply not draftable from that franchise-season. No value of the shrinkage
# constant can fix a thin season, because shrinkage pulls toward a prior and for a thin
# season the prior is mostly that same thin season. See `print_stability`, which is where
# both numbers come from.
BAT_FLOOR_BALLS = 100
BOWL_FLOOR_BALLS = 150          # 25 overs; not 100 - see print_stability

DRAFT_GATE_MATCHES = 4          # SPEC 7.3, provisional and unratified

K_SWEEP = (50, 100, 200, 400, 800)


class Cell(NamedTuple):
    """One (over, wicket bucket) reference state for one discipline.

    `balls` is the discipline's own denominator - balls faced for batting (A22:
    `extra_wides = 0`), legal balls for bowling - and `runs`/`outs` are summed over **every**
    delivery in the state, legal or not. The two differ for bowling: a no-ball is not a
    legal ball but the four hit off it is still charged. So the rate is runs-per-legal-ball
    including the runs that arrived on illegal ones, which is what an economy rate is.
    """

    balls: int
    runs: int
    outs: int
    outs_any: int       # diagnostic only, and only meaningful for batting

    @property
    def runs_per_ball(self) -> float:
        return self.runs / self.balls

    @property
    def out_rate(self) -> float:
        return self.outs / self.balls

    @property
    def out_rate_any(self) -> float:
        return self.outs_any / self.balls


class Grid:
    """Reference states at bucketed-wicket grain, with the thin ones resolved away.

    12 of the 75 observed cells sit under MIN_OBSERVATIONS and 5 have never happened, all
    in the early-over-collapse corner plus `ov19 0-1`. Balls do land there, and dropping
    them is not neutral: thin cells are collapse and death states, so dropping biases
    against exactly the players who batted in them. They are resolved to the nearest
    trustworthy bucket in the same over instead.
    """

    def __init__(self, cells: dict[tuple[int, str], Cell]) -> None:
        self.cells = cells
        self.fallbacks: Counter[tuple[int, str]] = Counter()
        self._resolved: dict[tuple[int, str], tuple[int, str]] = {}

    def _trustworthy(self, key) -> bool:
        cell = self.cells.get(key)
        return cell is not None and cell.balls >= MIN_OBSERVATIONS

    def resolve(self, over: int, bucket: str) -> tuple[int, str]:
        key = (over, bucket)
        if self._trustworthy(key):
            return key
        if key not in self._resolved:
            here = BUCKET_ORDER.index(bucket)
            near = sorted(
                (abs(BUCKET_ORDER.index(b) - here), b)
                for (o, b) in self.cells
                if o == over and self._trustworthy((o, b))
            )
            if not near:
                raise SystemExit(
                    f"over {over} has no state with {MIN_OBSERVATIONS}+ balls to fall back "
                    "to, so no ball in that over can be priced at all"
                )
            self._resolved[key] = (over, near[0][1])
        self.fallbacks[key] += 1
        return self._resolved[key]

    def of(self, over: int, wickets: int) -> Cell:
        return self.cells[self.resolve(over, bucket_of(wickets))]


class Costs:
    """Wicket cost at exact-wicket grain. Same fallback walk as `Grid`, finer grain."""

    def __init__(self, expected) -> None:
        self.priced = {
            (over, w): cost
            for over in range(20)
            for w in range(10)
            if (cost := wicket_cost(expected, over, w)) is not None
        }
        self.fallbacks: Counter[tuple[int, int]] = Counter()
        self._resolved: dict[tuple[int, int], tuple[int, int]] = {}

    def of(self, over: int, wickets: int) -> float:
        key = (over, wickets)
        if key in self.priced:
            return self.priced[key]
        if key not in self._resolved:
            near = sorted((abs(w - wickets), w) for (o, w) in self.priced if o == over)
            if not near:
                raise SystemExit(
                    f"over {over} has no priced wicket transition, so no dismissal in that "
                    "over can be costed"
                )
            self._resolved[key] = (over, near[0][1])
        self.fallbacks[key] += 1
        return self.priced[self._resolved[key]]


def fit_grids(conn) -> tuple[Grid, Grid]:
    """The batting and bowling reference grids, fitted over the SPEC 7.1 fitting set."""
    bat: dict[tuple[int, str], list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    bowl: dict[tuple[int, str], list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    with conn.cursor(name="impact_baseline") as cur:
        cur.itersize = 20_000
        cur.execute(
            f"""
            with fit as (
                select match_id, innings_no, over_no, ball_no, runs_batter,
                       runs_extras, extra_wides, extra_noballs, legal_ball,
                       batter_id, player_out_id, credited_to_bowler,
                       (wicket_kind is not null and wicket_kind <> all(%s)) as is_wicket
                from deliveries
                where {FITTING_SET}
            )
            select over_no, runs_batter, extra_wides, extra_noballs, legal_ball,
                   is_wicket,
                   (is_wicket and player_out_id = batter_id) as striker_out,
                   coalesce(credited_to_bowler, false) as bowler_out,
                   coalesce(sum(case when is_wicket then 1 else 0 end) over w, 0)
                       as wickets_down
            from fit
            window w as (partition by match_id, innings_no order by over_no, ball_no
                         rows between unbounded preceding and 1 preceding)
            """,
            (list(NOT_A_WICKET),),
        )
        for (over, runs, wides, noballs, legal, is_wicket,
             striker_out, bowler_out, wickets) in cur:
            key = (over, bucket_of(wickets))
            b = bowl[key]
            b[0] += int(legal)
            b[1] += runs + wides + noballs      # [A1] byes and legbyes are not the bowler's
            b[2] += int(bowler_out)
            if not wides:                       # [A22] a no-ball is a ball faced
                a = bat[key]
                a[0] += 1
                a[1] += runs
                a[2] += int(striker_out)
                a[3] += int(is_wicket)
    return (
        Grid({k: Cell(*v) for k, v in sorted(bat.items())}),
        Grid({k: Cell(*v) for k, v in sorted(bowl.items())}),
    )


class Tally:
    __slots__ = ("balls", "runs", "outs", "impact", "impact_any")

    def __init__(self) -> None:
        self.balls = 0
        self.runs = 0
        self.outs = 0
        self.impact = 0.0
        self.impact_any = 0.0

    @property
    def per_ball(self) -> float:
        return self.impact / self.balls if self.balls else 0.0

    @property
    def per_ball_any(self) -> float:
        return self.impact_any / self.balls if self.balls else 0.0


class Scored(NamedTuple):
    bat: dict[tuple[int, int], Tally]           # (person_id, season) -> tally
    bowl: dict[tuple[int, int], Tally]
    bat_careers: dict[int, Tally]
    bowl_careers: dict[int, Tally]
    names: dict[int, str]
    bands: dict[tuple[int, int], str]
    matches: dict[tuple[int, int], int]
    franchises: dict[tuple[int, int], set[int]]
    bat_grid: Grid
    bowl_grid: Grid
    costs: Costs


def score(conn, bat_grid: Grid, bowl_grid: Grid, expected) -> Scored:
    costs = Costs(expected)
    bat: dict[tuple[int, int], Tally] = defaultdict(Tally)
    bowl: dict[tuple[int, int], Tally] = defaultdict(Tally)
    bat_careers: dict[int, Tally] = defaultdict(Tally)
    bowl_careers: dict[int, Tally] = defaultdict(Tally)
    franchises: dict[tuple[int, int], set[int]] = defaultdict(set)

    with conn.cursor(name="impact_score") as cur:
        cur.itersize = 20_000
        cur.execute(
            f"""
            with scored as (
                select d.match_id, d.innings_no, d.over_no, d.ball_no,
                       d.batter_id, d.bowler_id, d.batting_fs_id, d.bowling_fs_id,
                       d.runs_batter, d.extra_wides, d.extra_noballs, d.legal_ball,
                       d.player_out_id, d.credited_to_bowler, m.season_year,
                       (d.wicket_kind is not null
                        and d.wicket_kind <> all(%s)) as is_wicket
                from deliveries d
                join matches m using (match_id)
                where {SCORING_SET}
            )
            select season_year, batter_id, bowler_id, batting_fs_id, bowling_fs_id,
                   over_no, runs_batter, extra_wides, extra_noballs, legal_ball,
                   is_wicket,
                   (is_wicket and player_out_id = batter_id) as striker_out,
                   coalesce(credited_to_bowler, false) as bowler_out,
                   coalesce(sum(case when is_wicket then 1 else 0 end) over w, 0)
                       as wickets_down
            from scored
            window w as (partition by match_id, innings_no order by over_no, ball_no
                         rows between unbounded preceding and 1 preceding)
            """,
            (list(NOT_A_WICKET),),
        )
        for (season, batter, bowler, bat_fs, bowl_fs, over, runs, wides, noballs,
             legal, is_wicket, striker_out, bowler_out, wickets) in cur:
            exact = min(wickets, 9)
            cost = costs.of(over, exact)

            charged = runs + wides + noballs
            cell = bowl_grid.of(over, wickets)
            # Expected terms count only on legal balls, actual terms on every delivery:
            # the denominator is legal balls but a no-ball's runs are still conceded.
            residual = charged - (cell.runs_per_ball if legal else 0.0)
            wickets_residual = bowler_out - (cell.out_rate if legal else 0.0)
            value = -(residual - wickets_residual * cost)
            for t in (bowl[(bowler, season)], bowl_careers[bowler]):
                t.balls += int(legal)
                t.runs += charged
                t.outs += int(bowler_out)
                t.impact += value
                t.impact_any += value
            franchises[(bowler, season)].add(bowl_fs)

            if wides:
                continue
            cell = bat_grid.of(over, wickets)
            runs_part = runs - cell.runs_per_ball
            value = runs_part - (striker_out - cell.out_rate) * cost
            diagnostic = runs_part - (is_wicket - cell.out_rate_any) * cost
            for t in (bat[(batter, season)], bat_careers[batter]):
                t.balls += 1
                t.runs += runs
                t.outs += int(striker_out)
                t.impact += value
                t.impact_any += diagnostic
            franchises[(batter, season)].add(bat_fs)

    with conn.cursor() as cur:
        cur.execute("select person_id, primary_name from people")
        names = dict(cur.fetchall())
        cur.execute(
            """
            select s.person_id, f.season_year, s.batting_band, s.matches_played
            from squad_members s
            join franchise_seasons f using (franchise_season_id)
            """
        )
        bands, matches = {}, {}
        for person, season, band, played in cur:
            if band is not None:
                bands[(person, season)] = band
            # A player can appear for two franchises in one season; take the larger.
            matches[(person, season)] = max(played, matches.get((person, season), 0))

    return Scored(dict(bat), dict(bowl), dict(bat_careers), dict(bowl_careers),
                  names, bands, matches, dict(franchises),
                  bat_grid, bowl_grid, costs)


# --- shrinkage ---------------------------------------------------------------------------

# [A7] The prior is the player's own career mean with this season's own contribution
# removed. A season shrunk toward a mean containing itself is barely shrunk at all, and
# exactly for the high-volume players where the prior is most informative.
#
# SPEC 7.2's clause "where they have meaningful career volume" is load-bearing, and this
# module was first written without it, which is how the number below got measured rather
# than assumed. Without a floor, a player whose whole career is seven balls shrinks toward
# the other two balls of it, so shrinkage *raises* their rating and raises it further the
# stronger it gets.
CAREER_FLOOR = 300


class Prior(NamedTuple):
    value: float
    n: int              # career balls outside this season, 0 when the fallback was used
    source: str         # 'loso' | 'band' | 'season'


def band_league_means(scored: Scored, seasons, striker_only=True) \
        -> dict[tuple[int, str], float]:
    """[SPEC 7.2] League mean per season x A6 batting band, the fallback prior.

    Never the undifferentiated mean: shrinking a number 6 toward the all-batters average
    drags every finisher toward top-order norms.
    """
    per: dict[tuple[int, str], list] = defaultdict(lambda: [0.0, 0])
    for (person, season), t in seasons.items():
        band = scored.bands.get((person, season))
        if band is None:
            continue
        acc = per[(season, band)]
        acc[0] += t.impact
        acc[1] += t.balls
    return {key: total / n for key, (total, n) in per.items() if n}


def league_means(seasons) -> dict[int, float]:
    per: dict[int, list] = defaultdict(lambda: [0.0, 0])
    for (_, season), t in seasons.items():
        acc = per[season]
        acc[0] += t.impact
        acc[1] += t.balls
    return {s: total / n for s, (total, n) in per.items() if n}


def loso_prior(scored, seasons, careers, person, season, league, bands,
               floor: int = CAREER_FLOOR) -> Prior:
    career, mine = careers[person], seasons[(person, season)]
    n = career.balls - mine.balls
    if n and n >= floor:
        return Prior((career.impact - mine.impact) / n, n, "loso")
    band = scored.bands.get((person, season))
    fallback = bands.get((season, band)) if band else None
    return (Prior(fallback, n, "band") if fallback is not None
            else Prior(league[season], n, "season"))


def shrink(raw: float, n: int, prior: float, k: float) -> float:
    return (n * raw + k * prior) / (n + k)


# --- reporting ---------------------------------------------------------------------------

def _row(scored, key, t, extra=""):
    person, season = key
    return (f"    {scored.names.get(person, person)[:25]:<26}{season:>7}"
            f"{t.balls:>7}{t.runs:>6}{t.outs:>5}{t.per_ball:>13.3f}"
            f"{t.impact:>9.1f}{extra}")


def print_resolution(scored: Scored, total_bat: int, total_bowl: int) -> None:
    print("\n=== state resolution: one rule, both halves of the impact ===")
    for name, grid, total in (("batting runs/out", scored.bat_grid, total_bat),
                              ("bowling runs/out", scored.bowl_grid, total_bowl)):
        used = sum(grid.fallbacks.values())
        print(f"    {name:<18} {used:>7,} of {total:,} balls ({used / total:.2%}) "
              f"resolved to a neighbouring bucket, across "
              f"{len(grid.fallbacks)} thin states")
    used = sum(scored.costs.fallbacks.values())
    print(f"    {'wicket cost':<18} {used:>7,} lookups resolved to a neighbouring exact "
          f"wicket count, {len(scored.costs.priced)} of 180 transitions priced directly")
    print("    nothing is dropped: every scoring-set ball is priced.")


def print_raw(scored: Scored, seasons, floor: int, what: str) -> None:
    rows = sorted(seasons.items(), key=lambda kv: kv[1].per_ball, reverse=True)
    total = sum(t.balls for _, t in rows)
    print(f"\n=== raw {what} impact, no shrinkage: {len(rows):,} player-seasons, "
          f"{total:,} balls ===")

    def show(title, subset, n=12):
        print(f"\n    {title}")
        print(f"    {'player':<26}{'season':>7}{'balls':>7}{'runs':>6}{'out':>5}"
              f"{'impact/ball':>13}{'total':>9}")
        for key, t in subset[:n]:
            print(_row(scored, key, t))

    show("TOP 12, no floor", rows)
    show("BOTTOM 12, no floor", rows[::-1])
    kept = [kv for kv in rows if kv[1].balls >= floor]
    show(f"TOP 12 at or above the {floor}-ball floor", kept)
    show(f"BOTTOM 12 at or above the {floor}-ball floor", kept[::-1])


def print_stability(seasons, what: str) -> None:
    """Where the raw list stops being noise. This is where the floors come from."""
    rows = sorted(seasons.items(), key=lambda kv: kv[1].per_ball, reverse=True)
    print(f"\n=== {what}: where does the raw list stabilise? ===")
    print(f"    {'balls':<14}{'seasons':>9}{'p5':>8}{'p25':>8}{'median':>8}{'p75':>8}"
          f"{'p95':>8}{'min':>8}{'max':>8}{'sd':>8}")
    bands = ((1, 10), (11, 30), (31, 60), (61, 100), (101, 150), (151, 250),
             (251, 400), (401, 10_000))
    for lo, hi in bands:
        vals = sorted(t.per_ball for _, t in rows if lo <= t.balls <= hi)
        if len(vals) < 2:
            continue
        q = statistics.quantiles(vals, n=20) if len(vals) > 20 else None
        label = f"{lo}-{hi if hi < 10_000 else '+'}"
        print(f"    {label:<14}{len(vals):>9}"
              + (f"{q[0]:>8.2f}{q[4]:>8.2f}{statistics.median(vals):>8.2f}"
                 f"{q[14]:>8.2f}{q[18]:>8.2f}" if q else f"{'-':>8}" * 5)
              + f"{vals[0]:>8.2f}{vals[-1]:>8.2f}{statistics.stdev(vals):>8.2f}")

    print(f"\n    top 5 at each candidate floor - read for the floor above which the names"
          f" stop\n    being one-innings artefacts")
    for cut in (0, 30, 50, 100, 150, 200):
        kept = [kv for kv in rows if kv[1].balls >= cut]
        head = ", ".join(f"{t.balls}b {t.per_ball:+.2f}" for _, t in kept[:5])
        print(f"    >= {cut:<4} {len(kept):>5} seasons   {head}")


def print_floor_effect(scored, seasons, floor, what, k=SHRINKAGE_K) -> None:
    """The floor and the ratified k together, which is what a rating will actually be."""
    gated = {key: t for key, t in seasons.items() if t.balls >= floor}
    league, bands = league_means(gated), band_league_means(scored, gated)
    careers = scored.bat_careers if what == "batting" else scored.bowl_careers
    ranked = sorted(
        (shrink(t.per_ball, t.balls,
                loso_prior(scored, gated, careers, p, s, league, bands).value, k),
         t, (p, s))
        for (p, s), t in gated.items()
    )[::-1]
    print(f"\n=== {what}: {floor}-ball floor + k = {k}, top 12 and bottom 6 ===")
    print(f"    {len(gated):,} of {len(seasons):,} player-seasons are rateable")
    print(f"    {'player':<26}{'season':>7}{'balls':>7}{'raw':>9}{'shrunk':>9}")
    for value, t, (p, s) in ranked[:12] + ranked[-6:]:
        print(f"    {scored.names.get(p, p)[:25]:<26}{s:>7}{t.balls:>7}"
              f"{t.per_ball:>+9.3f}{value:>+9.3f}")
    return {key: t for key, t in gated.items()}


def print_prior_breadth(scored, seasons, careers, floor, what, k=SHRINKAGE_K) -> None:
    """Does the LOSO prior rest on one other season, or on several?

    CAREER_FLOOR is a *volume* test and passes a two-season career, in which case the prior
    for either season is the other one - a single observation. That is the thin-season
    problem one level up, and it is the mechanism behind the only pathology that survived
    the balls floor, so it is measured rather than argued about.
    """
    gated = {key: t for key, t in seasons.items() if t.balls >= floor}
    league, bands = league_means(gated), band_league_means(scored, gated)
    others = Counter()
    for person, _ in gated:
        others[person] += 1
    rows = []
    for (person, season), t in gated.items():
        prior = loso_prior(scored, seasons, careers, person, season, league, bands)
        n_seasons = sum(1 for (p, s) in seasons if p == person and s != season
                        and seasons[(p, s)].balls)
        moved = shrink(t.per_ball, t.balls, prior.value, k) - t.per_ball
        rows.append((n_seasons, prior.source, moved, t, (person, season)))

    print(f"\n=== {what}: how broad is the prior behind each rateable season? ===")
    print(f"    {'other seasons in the prior':<30}{'seasons':>9}{'mean |move|':>13}"
          f"{'max up':>9}")
    for label, keep in (("0 (fallback to band/season)", lambda n: n == 0),
                        ("exactly 1", lambda n: n == 1),
                        ("2 to 4", lambda n: 2 <= n <= 4),
                        ("5 or more", lambda n: n >= 5)):
        subset = [r for r in rows if keep(r[0])]
        if not subset:
            continue
        moves = [r[2] for r in subset]
        print(f"    {label:<30}{len(subset):>9}"
              f"{statistics.mean(abs(m) for m in moves):>13.3f}{max(moves):>+9.3f}")

    single = sorted((r for r in rows if r[0] == 1), key=lambda r: -r[2])[:5]
    print(f"    biggest upward moves on a single-season prior:")
    for n, src, moved, t, (p, s) in single:
        print(f"        {scored.names.get(p, p)[:25]:<26}{s:>6}{t.balls:>6}b "
              f"raw {t.per_ball:+.3f} -> {t.per_ball + moved:+.3f} ({moved:+.3f})")


def print_gate_overlap(scored: Scored) -> None:
    """How the two floors and the match gate interact. Migration 009 has to store this."""
    keys = set(scored.bat) | set(scored.bowl)
    counts: Counter[str] = Counter()
    for key in keys:
        bat = scored.bat.get(key)
        bowl = scored.bowl.get(key)
        passes_bat = bat is not None and bat.balls >= BAT_FLOOR_BALLS
        passes_bowl = bowl is not None and bowl.balls >= BOWL_FLOOR_BALLS
        gate = scored.matches.get(key, 0) >= DRAFT_GATE_MATCHES
        counts["player-seasons"] += 1
        counts["passes 4-match gate"] += gate
        counts["batting rateable"] += passes_bat
        counts["bowling rateable"] += passes_bowl
        counts["both"] += passes_bat and passes_bowl
        counts["batting only"] += passes_bat and not passes_bowl
        counts["bowling only"] += passes_bowl and not passes_bat
        counts["neither"] += not passes_bat and not passes_bowl
        counts["neither, but past the match gate"] += (
            gate and not passes_bat and not passes_bowl)
    print(f"\n=== the two floors and the match gate, together "
          f"(bat {BAT_FLOOR_BALLS}, bowl {BOWL_FLOOR_BALLS}, "
          f"{DRAFT_GATE_MATCHES} matches) ===")
    for label in ("player-seasons", "passes 4-match gate", "batting rateable",
                  "bowling rateable", "both", "batting only", "bowling only",
                  "neither", "neither, but past the match gate"):
        print(f"    {label:<36}{counts[label]:>7,}")

    multi = [k for k, fs in scored.franchises.items() if len(fs) > 1]
    print(f"\n    player-seasons spanning two franchise-seasons: {len(multi)} "
          f"of {len(scored.franchises):,}")
    for key in sorted(multi)[:6]:
        print(f"        {scored.names.get(key[0], key[0])} {key[1]}: "
              f"{len(scored.franchises[key])} franchises")


def print_era_drift(seasons) -> None:
    league = league_means(seasons)
    print("\n=== league mean impact/ball by season (the era drift 7.4 normalises out) ===")
    print(f"    {'season':<8}{'mean':>9}", end="")
    for i, season in enumerate(sorted(league)):
        print(f"   {season} {league[season]:+.3f}", end="\n" if i % 4 == 3 else "")
    print()


def print_player(scored, seasons, careers, name, what, floor) -> None:
    match = [p for p, n in scored.names.items() if n == name]
    if not match:
        print(f"\nno person named {name!r}")
        return
    person = match[0]
    league, bands = league_means(seasons), band_league_means(scored, seasons)
    rows = sorted((s, t) for (p, s), t in seasons.items() if p == person)
    career = careers[person]
    if not rows:
        print(f"\n{name} has no {what} in the scoring set")
        return

    print(f"\n=== {name}, {what}: leave-one-season-out shrinkage at five constants ===")
    charged = "dismissals" if what == "batting" else "wickets"
    print(f"career {career.runs:,} runs, {career.outs} {charged}, off {career.balls:,} "
          f"scored balls, raw {career.per_ball:+.3f} per ball")
    print(f"    {'season':<8}{'balls':>7}{'runs':>6}{'raw':>8}{'prior':>8}{'src':>7}"
          + "".join(f"{'k=' + str(k):>9}" for k in K_SWEEP) + "   rateable")
    for season, t in rows:
        prior = loso_prior(scored, seasons, careers, person, season, league, bands)
        print(f"    {season:<8}{t.balls:>7}{t.runs:>6}{t.per_ball:>+8.3f}"
              f"{prior.value:>+8.3f}{prior.source:>7}"
              + "".join(f"{shrink(t.per_ball, t.balls, prior.value, k):>+9.3f}"
                        for k in K_SWEEP)
              + ("       yes" if t.balls >= floor else "        NO"))

    # How much season-to-season contrast survives each k. SPEC 7 opens by saying that
    # contrast "is the point of the game", so it is a quantity to watch, not a side-effect.
    def spread_at(k):
        vals = [shrink(t.per_ball, t.balls,
                       loso_prior(scored, seasons, careers, person, s, league,
                                  bands).value, k)
                for s, t in rows]
        return max(vals) - min(vals)
    raw_spread = max(t.per_ball for _, t in rows) - min(t.per_ball for _, t in rows)
    print(f"\n    best-to-worst season spread: raw {raw_spread:.3f}, then")
    print("    " + "".join(f"{'k=' + str(k):>9}" for k in K_SWEEP))
    print("    " + "".join(f"{spread_at(k):>9.3f}" for k in K_SWEEP))
    print("    " + "".join(f"{spread_at(k) / raw_spread:>8.0%} " for k in K_SWEEP)
          + " of the raw spread retained")


def print_reconcile(conn, scored: Scored, name: str) -> None:
    """Scored totals against the scorecard. A silent drop shows up here and nowhere else."""
    match = [p for p, n in scored.names.items() if n == name]
    if not match:
        print(f"\nno person named {name!r}")
        return
    person = match[0]
    with conn.cursor() as cur:
        cur.execute(
            f"""
            select m.season_year,
                   sum(d.runs_batter) filter (where {SCORING_SET}) as runs,
                   count(*) filter (where {SCORING_SET} and d.extra_wides = 0) as faced
            from deliveries d join matches m using (match_id)
            where d.batter_id = %s
            group by 1 order by 1
            """,
            (person,),
        )
        truth = {season: (runs or 0, faced) for season, runs, faced in cur}
    print(f"\n=== {name}: scored batting totals vs the scoring set, season by season ===")
    print(f"    {'season':<8}{'runs':>7}{'expected':>10}{'balls':>7}{'expected':>10}"
          f"{'ok':>5}")
    bad = 0
    for season, (runs, faced) in truth.items():
        t = scored.bat.get((person, season))
        got = (t.runs, t.balls) if t else (0, 0)
        ok = got == (runs, faced)
        bad += not ok
        print(f"    {season:<8}{got[0]:>7}{runs:>10}{got[1]:>7}{faced:>10}"
              f"{'yes' if ok else 'NO':>5}")
    print(f"    {len(truth) - bad} of {len(truth)} seasons reconcile exactly")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--player", action="append", dest="players")
    parser.add_argument("--bowler", action="append", dest="bowlers")
    parser.add_argument("--reconcile", action="append", dest="reconcile")
    args = parser.parse_args(argv)

    with connect(direct=True) as conn:
        _, expected = fetch(conn)
        bat_grid, bowl_grid = fit_grids(conn)
        scored = score(conn, bat_grid, bowl_grid, expected)

        total_bat = sum(t.balls for t in scored.bat.values())
        total_bowl = sum(t.balls for t in scored.bowl.values())
        print_resolution(scored, total_bat, total_bowl)
        print_stability(scored.bat, "batting")
        print_stability(scored.bowl, "bowling")
        print_raw(scored, scored.bat, BAT_FLOOR_BALLS, "batting")
        print_floor_effect(scored, scored.bat, BAT_FLOOR_BALLS, "batting")
        print_floor_effect(scored, scored.bowl, BOWL_FLOOR_BALLS, "bowling")
        print_prior_breadth(scored, scored.bat, scored.bat_careers,
                            BAT_FLOOR_BALLS, "batting")
        print_gate_overlap(scored)
        print_era_drift(scored.bat)
        for name in args.players or ["V Kohli"]:
            print_player(scored, scored.bat, scored.bat_careers, name, "batting",
                         BAT_FLOOR_BALLS)
        for name in args.bowlers or []:
            print_player(scored, scored.bowl, scored.bowl_careers, name, "bowling",
                         BOWL_FLOOR_BALLS)
        for name in args.reconcile or ["V Kohli"]:
            print_reconcile(conn, scored, name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
