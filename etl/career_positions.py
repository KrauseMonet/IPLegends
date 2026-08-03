"""Batting-position experience, career-wide AND per-season. SPEC 1.2 / A72/A76.

Migration 004 gave every franchise-season a `batting_position_min/max` envelope.
Career MIN/MAX across those envelopes needs no new derivation and was tried first --
it does not hold up, because a single outlier innings widens a MIN/MAX envelope
forever (measured: SV Samson 1-8, RK Singh 3-8, both far looser than reputation).

So this counts instead of enveloping: for every person, how many innings have they
batted at each exact position. `etl.derive_squads.batting_positions()` already does
the per-innings first-appearance-wins scan (A4), keyed by (franchise_season_id,
person_id) -> one position per innings that season. This module writes that dict TWICE
from the same single scan, at two different grains, rather than deriving it twice:

- `person_batting_positions` (migration 017, A72): re-aggregated by person alone,
  dropping the season key -- the CAREER record.
- `person_season_batting_positions` (migration 021, A76): the season key kept -- the
  SEASON record, needed because A76's four-role classification tries a player's
  current-season evidence first and only falls back to his career when that season
  alone is too thin, the same season-then-career cascade A57/A67/A71 already use for
  ratings.

No threshold and no fallback are applied to either table -- see migrations 017 and 021.
Those are game rules and live in `etl.batting_roles` alone.

Run with: uv run python -m etl.career_positions
          uv run python -m etl.career_positions --write
"""

from __future__ import annotations

import argparse
from collections import Counter

from etl.db import connect
from etl.derive_squads import batting_positions


def career_counts(by_season: dict[tuple[int, str], list[int]]) -> Counter[tuple[str, int]]:
    """Counter[(person_id, position)] -> innings, across the whole archive."""
    counts: Counter[tuple[str, int]] = Counter()
    for (_fs_id, person_id), positions in by_season.items():
        for position in positions:
            counts[(person_id, position)] += 1
    return counts


def season_counts(
    by_season: dict[tuple[int, str], list[int]]
) -> Counter[tuple[int, str, int]]:
    """Counter[(franchise_season_id, person_id, position)] -> innings, that season."""
    counts: Counter[tuple[int, str, int]] = Counter()
    for (fs_id, person_id), positions in by_season.items():
        for position in positions:
            counts[(fs_id, person_id, position)] += 1
    return counts


def write_career(conn, counts: Counter[tuple[str, int]]) -> int:
    with conn.cursor() as cur:
        cur.execute("truncate person_batting_positions")
        with cur.copy(
            "copy person_batting_positions (person_id, position, innings) from stdin"
        ) as copy:
            for (person_id, position), innings in sorted(counts.items()):
                copy.write_row((person_id, position, innings))
    conn.commit()
    return len(counts)


def write_season(conn, counts: Counter[tuple[int, str, int]]) -> int:
    with conn.cursor() as cur:
        cur.execute("truncate person_season_batting_positions")
        with cur.copy(
            "copy person_season_batting_positions "
            "(franchise_season_id, person_id, position, innings) from stdin"
        ) as copy:
            for (fs_id, person_id, position), innings in sorted(counts.items()):
                copy.write_row((fs_id, person_id, position, innings))
    conn.commit()
    return len(counts)


def report(counts: Counter[tuple[str, int]]) -> None:
    people = {person_id for person_id, _ in counts}
    by_position: Counter[int] = Counter()
    for (_person_id, position), innings in counts.items():
        by_position[position] += 1

    print(f"\n  people with at least one recorded batting innings  {len(people):>6}")
    print(f"  (person, position) rows                            {len(counts):>6}")
    print("\n  distinct people who have EVER batted at each position:")
    for position in range(1, 12):
        print(f"    {position:>2}: {by_position.get(position, 0):>4}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true",
        help="load migration 017's person_batting_positions AND migration 021's "
             "person_season_batting_positions (truncates both first)")
    args = parser.parse_args(argv)
    with connect(direct=True) as conn:
        by_season = batting_positions(conn)
        career = career_counts(by_season)
        season = season_counts(by_season)
        report(career)
        print(f"\n  (franchise_season, person, position) rows  {len(season):>6}")
        if args.write:
            n_career = write_career(conn, career)
            n_season = write_season(conn, season)
            print(f"\nwrote {n_career:,} rows to person_batting_positions")
            print(f"wrote {n_season:,} rows to person_season_batting_positions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
