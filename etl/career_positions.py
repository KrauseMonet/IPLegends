"""Career-wide batting-position experience. SPEC 1.2 / A72.

Migration 004 gave every franchise-season a `batting_position_min/max` envelope.
Career MIN/MAX across those envelopes needs no new derivation and was tried first --
it does not hold up, because a single outlier innings widens a MIN/MAX envelope
forever (measured: SV Samson 1-8, RK Singh 3-8, both far looser than reputation).

So this counts instead of enveloping: for every person, how many innings have they
batted at each exact position, across every match in the archive. `etl.derive_squads.
batting_positions()` already does the per-innings first-appearance-wins scan (A4) to
build the per-franchise-season envelope; this module reuses that same scan and
re-aggregates it by person alone, dropping the franchise-season key, rather than
re-deriving the scan a second way (A19).

No threshold and no fallback are applied here -- see migration 017's comment. Those are
game rules and live in `etl.feasibility.Card.positions` alone.

Run with: uv run python -m etl.career_positions
          uv run python -m etl.career_positions --write
"""

from __future__ import annotations

import argparse
from collections import Counter

from etl.db import connect
from etl.derive_squads import batting_positions


def career_counts(conn) -> Counter[tuple[str, int]]:
    """Counter[(person_id, position)] -> innings, across the whole archive."""
    counts: Counter[tuple[str, int]] = Counter()
    for (_fs_id, person_id), positions in batting_positions(conn).items():
        for position in positions:
            counts[(person_id, position)] += 1
    return counts


def write(conn, counts: Counter[tuple[str, int]]) -> int:
    with conn.cursor() as cur:
        cur.execute("truncate person_batting_positions")
        with cur.copy(
            "copy person_batting_positions (person_id, position, innings) from stdin"
        ) as copy:
            for (person_id, position), innings in sorted(counts.items()):
                copy.write_row((person_id, position, innings))
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
    parser.add_argument("--write", action="store_true",
                         help="load migration 017's person_batting_positions (truncates first)")
    args = parser.parse_args(argv)
    with connect(direct=True) as conn:
        counts = career_counts(conn)
        report(counts)
        if args.write:
            n = write(conn, counts)
            print(f"\nwrote {n:,} rows to person_batting_positions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
