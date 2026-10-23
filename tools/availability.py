"""Measure Build a Career's availability tables (A216) from the archive.

    uv run python -m tools.availability            # print the measured tables
    uv run python -m tools.availability --check    # exit 1 while game/career.py disagrees

Needs the database (the local one is enough; never run it against Neon -- it reads
`deliveries`, A173). The population is A198's: players with ten or more IPL seasons, in
seasons they were REGULARS (A33's floors: 100 balls faced or 150 legal balls bowled), and
the share is the matches they took part in over their team's matches. A216 splits that by
the season's card rating (tiers 88 / 82 / 0) and by passport.

It first rebuilds the pooled table and compares it with `AVAILABILITY_SHARES`, so a
replication that does not reproduce A198's figure is visible before its split is trusted.

It also prints the evidence behind INTERNATIONAL_DUTY_SHARE, which is declared: how often a
regular's missed matches touch the start or the end of his team's season, overseas against
domestic. The excess for overseas players is the part international duty explains.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict

QUANTILES = 20
MIN_SEASONS = 10
MIN_BAT_BALLS = 100           # A33
MIN_BOWL_BALLS = 150          # A33
# Below this many seasons a (tier, passport) cell is too thin to stand alone: it takes the
# tier's table with both passports pooled, and the output says so.
MIN_CELL = 40

SQL = """
with seasons as (
    select a.person_id, count(distinct f.season_year) as n
      from appearances a
      join franchise_seasons f on f.franchise_season_id = a.franchise_season_id
     where a.participated
     group by 1
), team as (
    select fs_id, array_agg(match_id order by match_date, match_id) as matches
      from (select team_a_fs_id as fs_id, match_id, match_date from matches
            union all
            select team_b_fs_id, match_id, match_date from matches) t
     group by 1
), bat as (
    select batter_id as person_id, batting_fs_id as fs_id,
           count(*) filter (where extra_wides = 0) as balls
      from deliveries where not is_super_over group by 1, 2
), bowl as (
    select bowler_id as person_id, bowling_fs_id as fs_id,
           count(*) filter (where legal_ball) as balls
      from deliveries where not is_super_over group by 1, 2
), played as (
    select person_id, franchise_season_id as fs_id, array_agg(match_id) as matches
      from appearances where participated group by 1, 2
), rating as (
    select franchise_season_id as fs_id, person_id, max(display_rating) as display
      from player_season_rating group by 1, 2
)
select pl.person_id, pl.fs_id, pl.matches, t.matches, p.is_overseas, r.display
  from played pl
  left join rating r on r.fs_id = pl.fs_id and r.person_id = pl.person_id
  join seasons s on s.person_id = pl.person_id and s.n >= %(min_seasons)s
  join team t on t.fs_id = pl.fs_id
  join people p on p.person_id = pl.person_id
  left join bat b on b.person_id = pl.person_id and b.fs_id = pl.fs_id
  left join bowl w on w.person_id = pl.person_id and w.fs_id = pl.fs_id
 where coalesce(b.balls, 0) >= %(bat)s or coalesce(w.balls, 0) >= %(bowl)s
"""


def quantiles(values: list[float]) -> tuple[float, ...]:
    """Nearest-rank quantiles at 5%, 10%, ... 100%, to three places."""
    v = sorted(values)
    return tuple(round(v[max(0, math.ceil(q / QUANTILES * len(v)) - 1)], 3)
                 for q in range(1, QUANTILES + 1))


def tier_of(rating: int | None, tiers: tuple[int, ...]) -> int:
    return next(f for f in tiers if (rating or 0) >= f)


def measure(conn, tiers: tuple[int, ...]):
    rows = conn.execute(SQL, {"min_seasons": MIN_SEASONS, "bat": MIN_BAT_BALLS,
                              "bowl": MIN_BOWL_BALLS}).fetchall()
    pooled: list[float] = []
    cells: dict[tuple[int, bool], list[float]] = defaultdict(list)
    by_tier: dict[int, list[float]] = defaultdict(list)
    ends = {True: [0, 0], False: [0, 0]}          # passport -> [touching an end, any missed]
    unknown = 0
    for _person, _fs, mine, team, overseas, rating in rows:
        share = len(mine) / len(team)
        pooled.append(share)
        if overseas is None:
            unknown += 1                          # A23: never defaulted to a passport
            continue
        t = tier_of(rating, tiers)
        cells[(t, overseas)].append(share)
        by_tier[t].append(share)
        missed = [i for i, m in enumerate(team) if m not in set(mine)]
        if missed:
            ends[overseas][1] += 1
            if missed[0] == 0 or missed[-1] == len(team) - 1:
                ends[overseas][0] += 1
    return pooled, cells, by_tier, ends, unknown


def tables(cells, by_tier, tiers) -> tuple[dict, list[str]]:
    out, notes = {}, []
    for t in tiers:
        for overseas in (False, True):
            values = cells.get((t, overseas), [])
            if len(values) >= MIN_CELL:
                out[(t, overseas)] = quantiles(values)
            else:
                out[(t, overseas)] = quantiles(by_tier[t])
                notes.append(f"({t}, {overseas}): {len(values)} seasons, under {MIN_CELL}; "
                             f"both passports pooled ({len(by_tier[t])})")
    return out, notes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="exit 1 while game/career.py's tables disagree with the archive")
    args = ap.parse_args(argv)
    from etl.db import connect, database_url
    from game import career as C

    if "neon.tech" in database_url():
        print("refusing to run against Neon: this reads `deliveries` (A173); use the local database")
        return 2
    with connect() as conn:
        pooled, cells, by_tier, ends, unknown = measure(conn, C.AVAILABILITY_TIERS)
    if not pooled:
        print("no regular seasons found: is the archive loaded? (tools/localdb.sh rebuild)")
        return 2
    again = quantiles(pooled)
    print(f"pooled: {len(pooled)} regular seasons of players with {MIN_SEASONS}+ seasons"
          f" (A198 measured 838)")
    print(f"  AVAILABILITY_SHARES {'reproduced' if again == C.AVAILABILITY_SHARES else 'DIFFERS'}")
    if again != C.AVAILABILITY_SHARES:
        print(f"  measured {again}\n  stored   {C.AVAILABILITY_SHARES}")
    if unknown:
        print(f"  {unknown} seasons with no passport left out of the split")
    measured, notes = tables(cells, by_tier, C.AVAILABILITY_TIERS)
    for (t, o), values in sorted(cells.items(), reverse=True):
        mean = sum(values) / len(values)
        print(f"  tier {t:>2} {'overseas' if o else 'domestic'}: {len(values):4} seasons, "
              f"mean {mean:.3f}, every match {sum(v == 1 for v in values) / len(values):.0%}")
    for n in notes:
        print(f"  {n}")
    for o in (False, True):
        hit, total = ends[o]
        if total:
            print(f"  {'overseas' if o else 'domestic'}: {hit} of {total} absences touch the "
                  f"season's start or end ({hit / total:.0%})")
    print("\nAVAILABILITY_BY_TIER: dict[tuple[int, bool], tuple[float, ...]] = {")
    for key in sorted(measured, reverse=True):
        print(f"    {key}: {measured[key]},")
    print("}")
    if args.check:
        stale = [k for k in measured if measured[k] != C.AVAILABILITY_BY_TIER.get(k)]
        if stale:
            print(f"\nSTALE: game/career.py disagrees with the archive for {sorted(stale)}")
            return 1
        print("\ncurrent: game/career.py matches the archive")
    return 0


if __name__ == "__main__":
    sys.exit(main())
