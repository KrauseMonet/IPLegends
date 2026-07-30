"""SPEC 7.1. The state model the ratings and the simulator both stand on.

    uv run python -m etl.state_model            fit and print the grid
    uv run python -m etl.state_model --cells    every cell, not the summary
    uv run python -m etl.state_model --write    fit, then load migration 007's tables

[A5] State is exact `over_no` crossed with wickets bucketed 0-1 / 2-3 / 4-5 / 6+.
[A28] Fitting set is `innings_scheduled_balls = 120` and nothing else, first innings
only, super overs and miscounted overs out.

`--write` truncates and rewrites both tables, so it is re-runnable. It stores counts and
never rates: per A19 every rate is derivable and two copies of one fact drift.

**The outcome guard.** Seven columns hold the whole distribution because 0-6 covers 100%
of the current archive. That is a measurement, not a law of cricket - an 8 off an
overthrow is legal and would have nowhere to go. `unexpected_outcomes` finds any such
value and `--write` refuses to run, naming it. Migration 007's sum constraint would also
catch it, and deliberately so: the constraint protects the table, and this protects
against the constraint being quietly wrong about what belongs in the table. A refreshed
archive is exactly when both matter.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from typing import NamedTuple

from etl.db import connect

# [A5] Wicket buckets, as (label, lowest wickets down). Read in order.
BUCKETS = (("0-1", 0), ("2-3", 2), ("4-5", 4), ("6+", 6))

# `retired hurt` carries a player_out_id but is not a dismissal: the batter may come
# back and the fielding side has not taken a wicket. `retired out` is a dismissal and
# does count. 19 deliveries and 6 respectively - small, and wrong in a way no internal
# check would ever surface, because every table would agree with every other.
NOT_A_WICKET = ("retired hurt",)

# Every scoring outcome off the bat, measured: these seven cover 100% of the fitting set.
# 5s happen (32 of them, off overthrows), 7s do not. The simulator needs the distribution,
# not the mean, so these are counted rather than averaged away. Anything outside this tuple
# is counted under its own real value and blocks `--write` - see the module docstring.
OFF_THE_BAT = (0, 1, 2, 3, 4, 5, 6)

FITTING_SET = """
    innings_no = 1 and not is_super_over and not over_miscounted
    and innings_scheduled_balls = 120
"""


class Cell(NamedTuple):
    over_no: int
    bucket: str
    faced: int              # deliveries a batter faced here (A22: extra_wides = 0)
    runs_off_bat: int
    dismissals: int
    outcomes: dict[int, int]

    @property
    def runs_per_ball(self) -> float:
        return self.runs_off_bat / self.faced if self.faced else 0.0

    @property
    def dismissal_rate(self) -> float:
        return self.dismissals / self.faced if self.faced else 0.0

    @property
    def unexpected(self) -> dict[int, int]:
        """Outcomes with no column to sit in. Empty on the archive as it stands."""
        return {r: n for r, n in self.outcomes.items() if r not in OFF_THE_BAT}


class Remaining(NamedTuple):
    """One (over, exact wickets) state, in counts. Migration 008's three columns."""

    observations: int
    runs_so_far: int        # summed over observations, not per innings
    runs_remaining: int

    @property
    def mean_remaining(self) -> float:
        return self.runs_remaining / self.observations

    @property
    def mean_final(self) -> float:
        """What the innings ends on from here. **This** is what prices a wicket."""
        return (self.runs_so_far + self.runs_remaining) / self.observations


def unexpected_outcomes(cells: list[Cell]) -> dict[int, int]:
    total: Counter[int] = Counter()
    for cell in cells:
        total.update(cell.unexpected)
    return dict(sorted(total.items()))


def bucket_of(wickets: int) -> str:
    label = BUCKETS[0][0]
    for name, low in BUCKETS:
        if wickets >= low:
            label = name
    return label


def fetch(conn) -> tuple[list[Cell], dict[tuple[int, int], Remaining]]:
    """Fit the per-ball grid and the expected-runs-remaining grid in one pass.

    The two grids deliberately use different wicket grains. Per-ball rates are noisy
    and get the bucketed grid A5 specifies. Expected runs remaining is an aggregate
    over the rest of the innings, so it is far smoother per observation and can afford
    **exact** wickets - which it has to, because the cost of a wicket is the difference
    one wicket makes, and inside a two-wide bucket one wicket usually makes none. On
    the bucketed grid half the states would price a wicket at zero.
    """
    per_ball: dict[tuple[int, str], list] = defaultdict(
        lambda: [0, 0, 0, defaultdict(int)]
    )
    remaining: dict[tuple[int, int], list] = defaultdict(lambda: [0, 0, 0])

    with conn.cursor(name="state_fit") as cur:
        cur.itersize = 20_000
        cur.execute(
            f"""
            with fit as (
                select match_id, over_no, ball_no, runs_batter, runs_extras,
                       extra_wides,
                       (wicket_kind is not null
                        and wicket_kind <> all(%s)) as is_wicket
                from deliveries
                where {FITTING_SET}
            )
            select over_no, runs_batter, extra_wides, is_wicket,
                   coalesce(sum(case when is_wicket then 1 else 0 end) over w, 0)
                       as wickets_down,
                   coalesce(sum(runs_batter + runs_extras) over w, 0) as runs_so_far,
                   sum(runs_batter + runs_extras) over r as runs_remaining
            from fit
            window
                w as (partition by match_id order by over_no, ball_no
                      rows between unbounded preceding and 1 preceding),
                r as (partition by match_id order by over_no, ball_no
                      rows between current row and unbounded following)
            """,
            (list(NOT_A_WICKET),),
        )
        for over_no, runs, wides, is_wicket, wickets, runs_before, runs_left in cur:
            exact = remaining[(over_no, min(wickets, 9))]
            exact[0] += 1
            exact[1] += runs_before
            exact[2] += runs_left

            if wides:
                continue  # not a ball faced (A22), so not a state observation either
            cell = per_ball[(over_no, bucket_of(wickets))]
            cell[0] += 1
            cell[1] += runs
            cell[2] += int(is_wicket)
            cell[3][runs] += 1  # keyed by the real value, so a surprise names itself

    cells = [
        Cell(over_no, bucket, faced, runs, outs, dict(dist))
        for (over_no, bucket), (faced, runs, outs, dist) in sorted(per_ball.items())
    ]
    # Counts, not means - the rule migrations 007 and 008 store under. Every mean is one
    # division away wherever it is wanted, and there is only ever one copy of the fact.
    expected = {
        key: Remaining(*totals) for key, totals in sorted(remaining.items()) if totals[0]
    }
    return cells, expected


MIN_OBSERVATIONS = 100   # below this a cell is printed as '-' rather than as a number


def _seen(expected, over: int, wickets: int) -> Remaining | None:
    got = expected.get((over, wickets))
    return got if got and got.observations >= MIN_OBSERVATIONS else None


def mean_remaining(expected, over: int, wickets: int) -> float | None:
    got = _seen(expected, over, wickets)
    return got.mean_remaining if got else None


def wicket_cost(expected, over: int, wickets: int) -> float | None:
    """**The** cost of a wicket: the drop in expected final total. SPEC 7.1, A31.

    Verified strictly positive on all 99 transitions with at least MIN_OBSERVATIONS either
    side - range 2.7 to 24.8 runs, mean 12.3 - and monotone in wickets at every over.
    """
    here, worse = _seen(expected, over, wickets), _seen(expected, over, wickets + 1)
    return None if here is None or worse is None else here.mean_final - worse.mean_final


def confounded_wicket_cost(expected, over: int, wickets: int) -> float | None:
    """The wrong way to price a wicket, kept because the printout is how we found it out.

    A team 4 down in over 12 is not a team that was 3 down and lost one: it is
    disproportionately a team being bowled at well, or one taking risks. Differencing runs
    remaining measures that selection alongside the wicket, strongly enough to flip the
    sign - over 12 at 0 down comes out at -2.0 runs, and three cells land indistinguishable
    from zero. Nothing may price a dismissal with this.
    """
    here, worse = mean_remaining(expected, over, wickets), \
        mean_remaining(expected, over, wickets + 1)
    return None if here is None or worse is None else here - worse


def every_cell(cells: list[Cell]) -> list[Cell]:
    """All 80 states, with the never-observed ones present as explicit zeroes.

    The archive supplies 75. The other five - 4 down in over 0 and its neighbours - have
    never happened, and that is a finding about cricket rather than a hole in the table.
    A missing key and a zero-observation row say different things to whatever reads this
    ("not covered" versus "never seen"), and only one of them is true, so the table says
    which. The simulator can then treat n = 0 the same way it treats n = 6.
    """
    have = {(c.over_no, c.bucket): c for c in cells}
    return [
        have.get((over, label))
        or Cell(over, label, 0, 0, 0, {r: 0 for r in OFF_THE_BAT})
        for over in range(20)
        for label, _ in BUCKETS
    ]


def write(conn, cells: list[Cell], expected) -> tuple[int, int]:
    """Load both tables. Truncate-and-rewrite, so re-running is safe."""
    surprises = unexpected_outcomes(cells)
    if surprises:
        raise SystemExit(
            "refusing to write: outcomes off the bat with no column to hold them: "
            + ", ".join(f"{runs} runs x{n}" for runs, n in surprises.items())
            + f"\nOFF_THE_BAT is {OFF_THE_BAT}. Widen it and migration 007 together, "
            "or the distribution silently stops summing to balls faced."
        )

    rows = every_cell(cells)
    with conn.cursor() as cur:
        cur.execute("truncate state_ball_outcomes")
        with cur.copy(
            """
            copy state_ball_outcomes (
                over_no, wicket_bucket, faced, runs_off_bat, dismissals,
                runs_0, runs_1, runs_2, runs_3, runs_4, runs_5, runs_6
            ) from stdin
            """
        ) as copy:
            for c in rows:
                copy.write_row(
                    (c.over_no, c.bucket, c.faced, c.runs_off_bat, c.dismissals)
                    + tuple(c.outcomes.get(r, 0) for r in OFF_THE_BAT)
                )

        cur.execute("truncate state_runs_remaining")
        with cur.copy(
            """
            copy state_runs_remaining (
                over_no, wickets, observations, runs_so_far_total, runs_remaining_total
            ) from stdin
            """
        ) as copy:
            for (over_no, wickets), r in sorted(expected.items()):
                copy.write_row((over_no, wickets, r.observations,
                                r.runs_so_far, r.runs_remaining))
    conn.commit()
    return len(rows), len(expected)


def report(cells: list[Cell], expected, show_cells: bool) -> None:
    total = sum(c.faced for c in cells)
    print(f"\nfitted on {total:,} balls faced across {len(cells)} cells "
          f"(20 overs x {len(BUCKETS)} wicket buckets)")

    unseen = 20 * len(BUCKETS) - len(cells)
    thin = [c for c in cells if c.faced < MIN_OBSERVATIONS]
    print(f"{unseen} states never observed, {len(thin)} observed but under "
          f"{MIN_OBSERVATIONS} balls, {len(cells) - len(thin)} of 80 trustworthy")
    for c in sorted(thin, key=lambda c: c.faced)[:8]:
        print(f"    ov{c.over_no:<3} {c.bucket:<4} {c.faced:>5} balls")

    surprises = unexpected_outcomes(cells)
    print("outcomes off the bat with no column: "
          + (", ".join(f"{r} runs x{n}" for r, n in surprises.items()) if surprises
             else "none, so the 7 columns are the whole distribution"))

    print("\nexpected runs off the bat per ball, by over and wickets down")
    print(f"    {'over':<6}" + "".join(f"{label:>10}" for label, _ in BUCKETS))
    grid = {(c.over_no, c.bucket): c for c in cells}
    for over in range(20):
        row = f"    {over:<6}"
        for label, _ in BUCKETS:
            c = grid.get((over, label))
            ok = c and c.faced >= MIN_OBSERVATIONS
            row += f"{c.runs_per_ball:>10.3f}" if ok else f"{'-':>10}"
        print(row)

    print("\ndismissal probability per ball faced")
    print(f"    {'over':<6}" + "".join(f"{label:>10}" for label, _ in BUCKETS))
    for over in range(20):
        row = f"    {over:<6}"
        for label, _ in BUCKETS:
            c = grid.get((over, label))
            ok = c and c.faced >= MIN_OBSERVATIONS
            row += f"{c.dismissal_rate:>10.4f}" if ok else f"{'-':>10}"
        print(row)

    print("\nexpected FINAL total at exact wickets, and the cost of a wicket (A31)")
    print(f"    {'over':<6}{'0 down':>10}{'2 down':>10}{'4 down':>10}{'6 down':>10}"
          f"{'cost@2':>10}{'confounded':>12}")
    for over in range(0, 20, 2):
        row = f"    {over:<6}"
        for w in (0, 2, 4, 6):
            got = _seen(expected, over, w)
            row += f"{got.mean_final:>10.1f}" if got else f"{'-':>10}"
        for fn, width in ((wicket_cost, 10), (confounded_wicket_cost, 12)):
            cost = fn(expected, over, 2)
            row += f"{cost:>{width}.1f}" if cost is not None else f"{'-':>{width}}"
        print(row)

    costs = [c for over in range(20) for w in range(9)
             if (c := wicket_cost(expected, over, w)) is not None]
    negative = [c for c in costs if c <= 0]
    print(f"    {len(costs)} priced transitions, {min(costs):.1f} to {max(costs):.1f} runs, "
          f"mean {sum(costs) / len(costs):.1f}, {len(negative)} non-positive")

    if show_cells:
        print("\nall 80 cells, with the outcome distribution the simulator needs")
        header = "".join(f"{r:>7}" for r in OFF_THE_BAT)
        print(f"    {'over':<5}{'wkts':<6}{'balls':>7}{header}{'other':>7}{'W':>7}")
        for c in every_cell(cells):
            dist = "".join(f"{c.outcomes.get(r, 0):>7}" for r in OFF_THE_BAT)
            print(f"    {c.over_no:<5}{c.bucket:<6}{c.faced:>7}{dist}"
                  f"{sum(c.unexpected.values()):>7}{c.dismissals:>7}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cells", action="store_true")
    parser.add_argument("--write", action="store_true",
                        help="load migration 007's two tables (truncates first)")
    args = parser.parse_args(argv)
    with connect(direct=True) as conn:
        cells, expected = fetch(conn)
        report(cells, expected, args.cells)
        if args.write:
            outcomes, remaining = write(conn, cells, expected)
            print(f"\nwrote {outcomes} rows to state_ball_outcomes "
                  f"and {remaining} to state_runs_remaining")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
