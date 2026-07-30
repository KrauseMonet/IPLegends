"""SPEC 7.1 per-ball impact, computed in memory and printed. No schema, no writes.

    uv run python -m etl.impact                 the distributions
    uv run python -m etl.impact --player "V Kohli"

Deliberately writes nothing. The ratings grain (migration 009) is to be chosen against
these numbers rather than guessed in advance, and the shrinkage constant with it, so this
module exists to produce evidence and stops there. It has no `--write`.

The formula, from SPEC 7.1:

    impact = (runs_off_bat - E[runs | over, bucket])
             - (was_dismissed - P[out | over, bucket]) * wicket_cost(over, exact wickets)

**Scoring set is not the fitting set.** Second innings are scored but not fitted, and
miscounted overs are scored but not fitted. Reduced and unknown-length innings are in
neither.

**Whose dismissal.** `state_ball_outcomes.dismissals` counts any wicket, so its rate is
P(a wicket falls), which is not what a batter should be charged with: 555 non-striker run
outs are somebody else's failure. This module therefore fits a second dismissal grid
counting only `player_out_id = batter_id` and scores against that, and prints both so the
difference is a measured quantity rather than an argument. Nothing here presumes which one
migration 009 should store.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
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


class Baseline(NamedTuple):
    """One (over, bucket) cell of the scoring baseline, both dismissal readings."""

    faced: int
    runs: int
    outs_any: int
    outs_striker: int

    @property
    def runs_per_ball(self) -> float:
        return self.runs / self.faced

    def out_rate(self, striker_only: bool) -> float:
        return (self.outs_striker if striker_only else self.outs_any) / self.faced


def fit_baseline(conn) -> dict[tuple[int, str], Baseline]:
    """The per-ball grid over the fitting set, split by whose wicket it was."""
    grid: dict[tuple[int, str], list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    with conn.cursor(name="impact_baseline") as cur:
        cur.itersize = 20_000
        cur.execute(
            f"""
            with fit as (
                select match_id, innings_no, over_no, ball_no, runs_batter, runs_extras,
                       extra_wides, batter_id, player_out_id,
                       (wicket_kind is not null and wicket_kind <> all(%s)) as is_wicket
                from deliveries
                where {FITTING_SET}
            )
            select over_no, runs_batter, is_wicket,
                   (is_wicket and player_out_id = batter_id) as striker_out,
                   coalesce(sum(case when is_wicket then 1 else 0 end) over w, 0)
                       as wickets_down
            from fit
            where extra_wides = 0
            window w as (partition by match_id, innings_no order by over_no, ball_no
                         rows between unbounded preceding and 1 preceding)
            """,
            (list(NOT_A_WICKET),),
        )
        for over_no, runs, is_wicket, striker_out, wickets in cur:
            cell = grid[(over_no, bucket_of(wickets))]
            cell[0] += 1
            cell[1] += runs
            cell[2] += int(is_wicket)
            cell[3] += int(striker_out)
    return {key: Baseline(*vals) for key, vals in sorted(grid.items())}


class Costs:
    """Wicket cost per (over, exact wickets), with the gaps made visible.

    `wicket_cost` returns None wherever either side of the transition is under
    MIN_OBSERVATIONS, which is 17 of the 80 cells - early-over collapses, plus the over
    where nobody gets out. A ball landing there still has to be priced somehow. The
    fallback is the nearest priced wicket count in the *same over*, and every use is
    counted, so a fallback doing real work would show up as a large `fallback_balls`
    rather than as a silently plausible number.
    """

    def __init__(self, expected) -> None:
        self.priced = {
            (over, w): cost
            for over in range(20)
            for w in range(10)
            if (cost := wicket_cost(expected, over, w)) is not None
        }
        self.fallback_balls = 0
        self.unpriceable_balls = 0

    def of(self, over: int, wickets: int) -> float | None:
        exact = self.priced.get((over, wickets))
        if exact is not None:
            return exact
        near = sorted(
            (abs(w - wickets), w) for (o, w) in self.priced if o == over
        )
        if not near:
            self.unpriceable_balls += 1
            return None
        self.fallback_balls += 1
        return self.priced[(over, near[0][1])]


class Tally:
    __slots__ = ("faced", "runs", "outs", "impact_striker", "impact_any")

    def __init__(self) -> None:
        self.faced = 0
        self.runs = 0
        self.outs = 0
        self.impact_striker = 0.0
        self.impact_any = 0.0

    def add(self, runs: int, striker_out: bool, i_striker: float, i_any: float) -> None:
        self.faced += 1
        self.runs += runs
        self.outs += int(striker_out)
        self.impact_striker += i_striker
        self.impact_any += i_any

    def per_ball(self, striker_only: bool = True) -> float:
        total = self.impact_striker if striker_only else self.impact_any
        return total / self.faced if self.faced else 0.0


class Scored(NamedTuple):
    seasons: dict[tuple[int, int], Tally]      # (person_id, season) -> tally
    careers: dict[int, Tally]
    names: dict[int, str]
    bands: dict[tuple[int, int], str]          # (person_id, season) -> A6 batting band
    matches: dict[tuple[int, int], int]        # (person_id, season) -> matches played
    costs: Costs
    balls: int
    skipped: int          # balls in a cell too thin to score against


def score(conn, baseline, expected) -> Scored:
    costs = Costs(expected)
    seasons: dict[tuple[int, int], Tally] = defaultdict(Tally)
    careers: dict[int, Tally] = defaultdict(Tally)
    names: dict[int, str] = {}
    balls = skipped = 0

    with conn.cursor(name="impact_score") as cur:
        cur.itersize = 20_000
        cur.execute(
            f"""
            with scored as (
                select d.match_id, d.innings_no, d.over_no, d.ball_no, d.batter_id,
                       d.runs_batter, d.runs_extras, d.extra_wides, d.player_out_id,
                       m.season_year,
                       (d.wicket_kind is not null
                        and d.wicket_kind <> all(%s)) as is_wicket
                from deliveries d
                join matches m using (match_id)
                where {SCORING_SET}
            )
            select season_year, batter_id, over_no, runs_batter, is_wicket,
                   (is_wicket and player_out_id = batter_id) as striker_out,
                   coalesce(sum(case when is_wicket then 1 else 0 end) over w, 0)
                       as wickets_down
            from scored
            where extra_wides = 0
            window w as (partition by match_id, innings_no order by over_no, ball_no
                         rows between unbounded preceding and 1 preceding)
            """,
            (list(NOT_A_WICKET),),
        )
        for season, person, over, runs, is_wicket, striker_out, wickets in cur:
            cell = baseline.get((over, bucket_of(wickets)))
            if cell is None or cell.faced < MIN_OBSERVATIONS:
                # A thin cell has no baseline worth scoring against, so these balls are
                # dropped. They are counted, because a silent drop makes a player's scored
                # runs disagree with their scorecard for no visible reason - Kohli 2016
                # came out 838 off 583 against a real 860 off 590, and the 22 runs went
                # nowhere anyone could see.
                skipped += 1
                continue
            cost = costs.of(over, min(wickets, 9))
            if cost is None:
                skipped += 1
                continue
            runs_part = runs - cell.runs_per_ball
            i_striker = runs_part - (striker_out - cell.out_rate(True)) * cost
            i_any = runs_part - (is_wicket - cell.out_rate(False)) * cost
            seasons[(person, season)].add(runs, striker_out, i_striker, i_any)
            careers[person].add(runs, striker_out, i_striker, i_any)
            balls += 1

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
    return Scored(dict(seasons), dict(careers), names, bands, matches, costs,
                  balls, skipped)


# --- shrinkage, explored rather than set -------------------------------------------------

# [A7] The prior is the player's own career mean with this season's own contribution
# removed. A season shrunk toward a mean containing itself is barely shrunk at all, and
# exactly for the high-volume players where the prior is most informative.
#
# SPEC 7.2's clause "where they have meaningful career volume" is load-bearing and this
# module was first written without it, which is how the number below got measured rather
# than assumed. Without a floor, a player whose whole career is seven balls shrinks toward
# the other two balls of it, so shrinkage *raises* their rating and raises it further the
# stronger it gets. CAREER_FLOOR is a diagnostic value, not a ratified one.
CAREER_FLOOR = 300


def band_league_means(scored: Scored, striker_only: bool = True) \
        -> dict[tuple[int, str], float]:
    """[SPEC 7.2] League mean per season x A6 batting band, the fallback prior.

    Never the undifferentiated mean: shrinking a number 6 toward the all-batters average
    drags every finisher toward top-order norms.
    """
    per: dict[tuple[int, str], list] = defaultdict(lambda: [0.0, 0])
    for (person, season), t in scored.seasons.items():
        band = scored.bands.get((person, season))
        if band is None:
            continue
        acc = per[(season, band)]
        acc[0] += t.impact_striker if striker_only else t.impact_any
        acc[1] += t.faced
    return {key: total / n for key, (total, n) in per.items() if n}


class Prior(NamedTuple):
    value: float
    n: int              # career balls outside this season, 0 when the fallback was used
    source: str         # 'loso' | 'band' | 'season'


def loso_prior(scored: Scored, person: int, season: int, league, bands,
               striker_only: bool = True, floor: int = CAREER_FLOOR) -> Prior:
    career = scored.careers[person]
    mine = scored.seasons[(person, season)]
    total = (career.impact_striker if striker_only else career.impact_any) - \
            (mine.impact_striker if striker_only else mine.impact_any)
    n = career.faced - mine.faced
    if n and n >= floor:        # a single-season career leaves nothing to shrink toward
        return Prior(total / n, n, "loso")
    band = scored.bands.get((person, season))
    fallback = bands.get((season, band)) if band else None
    return (Prior(fallback, n, "band") if fallback is not None
            else Prior(league[season], n, "season"))


def shrink(raw: float, n: int, prior: float, k: float) -> float:
    return (n * raw + k * prior) / (n + k)


def league_means(scored: Scored, striker_only: bool = True) -> dict[int, float]:
    per: dict[int, list[float]] = defaultdict(lambda: [0.0, 0])
    for (_, season), t in scored.seasons.items():
        acc = per[season]
        acc[0] += t.impact_striker if striker_only else t.impact_any
        acc[1] += t.faced
    return {s: total / n for s, (total, n) in per.items()}


K_SWEEP = (50, 100, 200, 400, 800)


# --- reporting ---------------------------------------------------------------------------

def print_attribution(baseline) -> None:
    any_outs = sum(c.outs_any for c in baseline.values())
    striker = sum(c.outs_striker for c in baseline.values())
    print("\n=== whose dismissal the grid's rate describes ===")
    print(f"fitting set: {any_outs:,} dismissals, {striker:,} of them the striker's, "
          f"{any_outs - striker:,} ({(any_outs - striker) / any_outs:.2%}) somebody else's")
    print("essentially all of the difference is non-striker run outs. Charging them to the")
    print("striker is the only thing a single dismissals column can do.")

    print("\n    dismissal probability per ball faced, by wicket bucket")
    print(f"    {'over':<6}" + "".join(f"{lab + ' any':>10}{lab + ' str':>10}"
                                       for lab, _ in BUCKETS[:2]))
    for over in range(0, 20, 3):
        row = f"    {over:<6}"
        for label, _ in BUCKETS[:2]:
            c = baseline.get((over, label))
            ok = c and c.faced >= MIN_OBSERVATIONS
            row += (f"{c.out_rate(False):>10.4f}{c.out_rate(True):>10.4f}" if ok
                    else f"{'-':>10}{'-':>10}")
        print(row)


def print_raw(scored: Scored, floor: int) -> None:
    rows = sorted(scored.seasons.items(), key=lambda kv: kv[1].per_ball(), reverse=True)
    print(f"\n=== raw per-ball impact, no shrinkage: {len(rows):,} player-seasons, "
          f"{scored.balls:,} balls scored ===")

    def show(title, subset, n=15):
        print(f"\n    {title}")
        print(f"    {'player':<26}{'season':>7}{'balls':>7}{'runs':>6}{'out':>5}"
              f"{'impact/ball':>13}{'total':>9}")
        for (person, season), t in subset[:n]:
            print(f"    {scored.names.get(person, person)[:25]:<26}{season:>7}"
                  f"{t.faced:>7}{t.runs:>6}{t.outs:>5}{t.per_ball():>13.3f}"
                  f"{t.impact_striker:>9.1f}")

    show("TOP 15, no minimum balls - this is where the eight-ball wonders live", rows)
    show("BOTTOM 15, no minimum balls", rows[::-1])
    kept = [kv for kv in rows if kv[1].faced >= floor]
    show(f"TOP 15 with at least {floor} balls faced", kept)
    show(f"BOTTOM 15 with at least {floor} balls faced", kept[::-1])

    print("\n    distribution of raw impact/ball, by balls faced")
    print(f"    {'balls faced':<16}{'seasons':>9}{'p5':>8}{'p25':>8}{'median':>8}"
          f"{'p75':>8}{'p95':>8}{'min':>8}{'max':>8}")
    bands = ((1, 10), (11, 30), (31, 60), (61, 120), (121, 240), (241, 480), (481, 10_000))
    for lo, hi in bands:
        vals = sorted(t.per_ball() for _, t in rows if lo <= t.faced <= hi)
        if not vals:
            continue
        q = statistics.quantiles(vals, n=20) if len(vals) > 20 else None
        label = f"{lo}-{hi if hi < 10_000 else '+'}"
        print(f"    {label:<16}{len(vals):>9}"
              + (f"{q[0]:>8.2f}{q[4]:>8.2f}{statistics.median(vals):>8.2f}"
                 f"{q[14]:>8.2f}{q[18]:>8.2f}" if q else f"{'-':>8}" * 5)
              + f"{vals[0]:>8.2f}{vals[-1]:>8.2f}")


DRAFT_GATE_MATCHES = 4      # SPEC 7.3, provisional and unratified


def print_gate(scored: Scored) -> None:
    """What SPEC 7.3's provisional four-match gate does on its own, before any shrinkage.

    Worth measuring separately, because shrinkage and the gate are often assumed to solve
    the same problem and they do not: shrinkage toward an own-career prior protects against
    a thin *career*, the gate against a thin *season*.
    """
    rows = sorted(scored.seasons.items(), key=lambda kv: kv[1].per_ball(), reverse=True)
    kept = [(k, t) for k, t in rows
            if scored.matches.get(k, 0) >= DRAFT_GATE_MATCHES]
    print(f"\n=== SPEC 7.3's {DRAFT_GATE_MATCHES}-match gate, applied to the RAW list ===")
    print(f"    {len(kept):,} of {len(rows):,} player-seasons survive it")
    thin = [t for _, t in kept if t.faced < 30]
    print(f"    {len(thin)} survivors still under 30 balls faced "
          f"(min {min((t.faced for t in thin), default=0)})")
    print(f"    {'player':<26}{'season':>7}{'mts':>5}{'balls':>7}{'runs':>6}"
          f"{'impact/ball':>13}")
    for (person, season), t in kept[:12]:
        print(f"    {scored.names.get(person, person)[:25]:<26}{season:>7}"
              f"{scored.matches[(person, season)]:>5}{t.faced:>7}{t.runs:>6}"
              f"{t.per_ball():>13.3f}")


def print_attribution_effect(scored: Scored) -> None:
    rows = [(kv[0], kv[1]) for kv in scored.seasons.items() if kv[1].faced >= 100]
    diffs = [t.per_ball(True) - t.per_ball(False) for _, t in rows]
    print("\n=== how much the attribution choice moves a rating (>= 100 balls) ===")
    print(f"    striker-only minus any-wicket, per ball: mean {statistics.mean(diffs):+.4f}, "
          f"max {max(diffs):+.4f}, min {min(diffs):+.4f}")
    by_striker = [k for k, _ in sorted(rows, key=lambda kv: kv[1].per_ball(True),
                                       reverse=True)][:20]
    by_any = [k for k, _ in sorted(rows, key=lambda kv: kv[1].per_ball(False),
                                   reverse=True)][:20]
    moved = set(by_striker) ^ set(by_any)
    print(f"    top 20 membership differs by {len(moved) // 2} player-season(s)"
          + ("" if not moved else ": "
             + ", ".join(f"{scored.names.get(p, p)} {s}" for p, s in sorted(moved))))


def print_era_drift(scored: Scored) -> None:
    """The league mean per season. Not zero, and SPEC 7.4 is why it must be normalised.

    The baseline is fitted pooled across all 19 seasons, so a season that scored above the
    pooled environment leaves a positive league mean behind. This column *is* era drift,
    measured, and it is what 7.4's within-season normalisation removes.
    """
    league = league_means(scored)
    print("\n=== league mean impact/ball by season (the era drift 7.4 normalises out) ===")
    print(f"    {'season':<8}{'batters':>9}{'balls':>9}{'mean':>9}")
    for season in sorted(league):
        rows = [t for (_, s), t in scored.seasons.items() if s == season]
        print(f"    {season:<8}{len(rows):>9}{sum(t.faced for t in rows):>9}"
              f"{league[season]:>+9.3f}")


def print_player(scored: Scored, name: str, floor: int = CAREER_FLOOR) -> None:
    match = [p for p, n in scored.names.items() if n == name]
    if not match:
        print(f"\nno person named {name!r}")
        return
    person = match[0]
    league, bands = league_means(scored), band_league_means(scored)
    rows = sorted((s, t) for (p, s), t in scored.seasons.items() if p == person)
    career = scored.careers[person]

    print(f"\n=== {name}: leave-one-season-out shrinkage at five constants ===")
    print(f"career {career.runs:,} runs off {career.faced:,} balls scored, "
          f"raw impact/ball {career.per_ball():+.3f}")
    print(f"    {'season':<8}{'balls':>7}{'runs':>6}{'raw':>8}{'prior':>8}{'src':>7}"
          + "".join(f"{'k=' + str(k):>9}" for k in K_SWEEP))
    for season, t in rows:
        prior = loso_prior(scored, person, season, league, bands, floor=floor)
        raw = t.per_ball()
        print(f"    {season:<8}{t.faced:>7}{t.runs:>6}{raw:>+8.3f}"
              f"{prior.value:>+8.3f}{prior.source:>7}"
              + "".join(f"{shrink(raw, t.faced, prior.value, k):>+9.3f}"
                        for k in K_SWEEP))

    # How much of the season-to-season contrast survives each k. SPEC 7 opens by saying
    # that contrast "is the point of the game", so it is a quantity to watch, not a
    # side-effect: shrinkage strong enough to fix the cameos also flattens the career.
    def spread(vals):
        return max(vals) - min(vals)
    raws = [t.per_ball() for _, t in rows]
    print(f"\n    best-to-worst season spread: raw {spread(raws):.3f}, then")
    print("    " + "".join(f"{'k=' + str(k):>9}" for k in K_SWEEP))
    print("    " + "".join(
        f"{spread([shrink(t.per_ball(), t.faced, loso_prior(scored, person, s, league, bands, floor=floor).value, k) for s, t in rows]):>9.3f}"
        for k in K_SWEEP))
    print("    " + "".join(
        f"{spread([shrink(t.per_ball(), t.faced, loso_prior(scored, person, s, league, bands, floor=floor).value, k) for s, t in rows]) / spread(raws):>8.0%} "
        for k in K_SWEEP) + " of the raw spread retained")


def print_sweep_top(scored: Scored, floor: int, label: str) -> None:
    """What each candidate k does to the leaderboard. The calibration signal."""
    league, bands = league_means(scored), band_league_means(scored)
    print(f"\n=== top 8 player-seasons under each shrinkage constant, {label} ===")
    print("   (no draftability gate - SPEC 7.3's four-match minimum is not applied yet)")
    for k in K_SWEEP:
        ranked = sorted(
            (
                shrink(t.per_ball(), t.faced,
                       loso_prior(scored, p, s, league, bands, floor=floor).value, k),
                t.faced, p, s,
            )
            for (p, s), t in scored.seasons.items()
        )[::-1][:8]
        print(f"\n    k = {k}")
        for value, faced, p, s in ranked:
            print(f"        {scored.names.get(p, p)[:25]:<26}{s:>6}{faced:>7} balls"
                  f"{value:>+9.3f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--player", action="append", dest="players")
    parser.add_argument("--floor", type=int, default=100,
                        help="balls-faced cut for the second pair of raw lists")
    args = parser.parse_args(argv)

    with connect(direct=True) as conn:
        _, expected = fetch(conn)
        baseline = fit_baseline(conn)
        scored = score(conn, baseline, expected)

    print_attribution(baseline)
    total = scored.balls + scored.skipped
    print(f"\nwicket cost: {len(scored.costs.priced)} of 180 (over, wickets -> wickets+1) "
          f"transitions priced directly")
    print(f"    {scored.costs.fallback_balls:,} balls priced off the nearest wicket count "
          f"in the same over, {scored.costs.unpriceable_balls:,} unpriceable")
    print(f"    {scored.skipped:,} of {total:,} scoring-set balls faced "
          f"({scored.skipped / total:.2%}) fell in a cell under {MIN_OBSERVATIONS} balls "
          f"and are not scored at all")
    print_raw(scored, args.floor)
    print_gate(scored)
    print_attribution_effect(scored)
    print_era_drift(scored)
    # Both, because the second is only legible against the first: without a career floor
    # the prior for a tiny career is the rest of that tiny career, and shrinkage runs the
    # wrong way. SPEC 7.2 already says "meaningful career volume"; this is what it buys.
    print_sweep_top(scored, floor=0, label="NO career-volume floor on the prior")
    print_sweep_top(scored, floor=CAREER_FLOOR,
                    label=f"career floor {CAREER_FLOOR} balls, else the band-season mean")
    for name in args.players or ["V Kohli"]:
        print_player(scored, name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
