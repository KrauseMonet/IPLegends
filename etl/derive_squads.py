"""Reconstruct squads per franchise-season. SPEC 6.4, 6.5, 6.6.

Every column written here is derived from deliveries and appearances that are already
loaded; nothing is read from the archive again. The derivation itself lives in
`etl.positions` and `etl.roles`, which are pure and tested, so this module is only
plumbing: pull the counts, apply the rules, COPY the result.

Super overs are excluded throughout. A super over is a tie-break, not part of the
innings, so a player's batting position and bowling workload in one describe nothing
about their role in the season.

Run with: uv run python -m etl.derive_squads
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import NamedTuple

from etl.db import connect
from etl.derive_people import OVERRIDES, read_override
from etl.positions import band_for, batting_order, bowling_usage, modal_position
from etl.roles import role_for


class Pair(NamedTuple):
    """A batter and their partner on one delivery, which is all 6.4 needs."""

    batter_id: str
    non_striker_id: str


def batting_positions(conn) -> dict[tuple[int, str], list[int]]:
    """(franchise_season_id, person_id) -> the position held in each innings."""
    positions: dict[tuple[int, str], list[int]] = defaultdict(list)
    with conn.cursor(name="batting_order") as cur:
        cur.itersize = 20_000
        cur.execute(
            """
            select match_id, innings_no, batting_fs_id, batter_id, non_striker_id
            from deliveries
            where not is_super_over
            order by match_id, innings_no, over_no, ball_no
            """
        )
        innings_key = None
        innings_fs = None
        buffer: list[Pair] = []

        def flush() -> None:
            if not buffer:
                return
            for person_id, position in batting_order(buffer).items():
                positions[(innings_fs, person_id)].append(position)

        for match_id, innings_no, fs_id, batter_id, non_striker_id in cur:
            key = (match_id, innings_no)
            if key != innings_key:
                flush()
                innings_key, innings_fs, buffer = key, fs_id, []
            buffer.append(Pair(batter_id, non_striker_id))
        flush()
    return positions


def _pairs(conn, sql: str) -> dict[tuple[int, str], int]:
    with conn.cursor() as cur:
        cur.execute(sql)
        return {(fs_id, person_id): n for fs_id, person_id, n in cur}


def balls_faced(conn) -> dict[tuple[int, str], int]:
    """SPEC A22: a no-ball is a ball faced, a wide is not. Not `legal_ball`."""
    return _pairs(
        conn,
        """
        select batting_fs_id, batter_id, count(*)
        from deliveries
        where not is_super_over and extra_wides = 0
        group by 1, 2
        """,
    )


def balls_bowled(conn) -> dict[tuple[int, str], int]:
    return _pairs(
        conn,
        """
        select bowling_fs_id, bowler_id, count(*)
        from deliveries
        where not is_super_over and legal_ball
        group by 1, 2
        """,
    )


def phase_counts(conn) -> dict[tuple[int, str], dict[str, int]]:
    """Legal deliveries by phase. A null phase is unknown and is left out entirely."""
    counts: dict[tuple[int, str], dict[str, int]] = defaultdict(dict)
    with conn.cursor() as cur:
        cur.execute(
            """
            select bowling_fs_id, bowler_id, phase, count(*)
            from deliveries
            where not is_super_over and legal_ball and phase is not null
            group by 1, 2, 3
            """
        )
        for fs_id, person_id, phase, n in cur:
            counts[(fs_id, person_id)][phase] = n
    return counts


def matches_played(conn) -> dict[tuple[int, str], int]:
    return _pairs(
        conn,
        """
        select franchise_season_id, person_id, count(*)
        from appearances
        where participated
        group by 1, 2
        """,
    )


def kept_for_squad() -> set[tuple[int, str]]:
    """SPEC 6.6's second clause, from etl/overrides/keepers_by_season.csv.

    `people.is_keeper` is a career fact and cannot answer this: it would make a
    keeper of someone in every season they appeared, including the ones they spent
    as a specialist batter. A blank `kept` is undecided, and undecided is not a yes.
    """
    return {
        (int(row["franchise_season_id"]), row["person_id"])
        for row in read_override(OVERRIDES / "keepers_by_season.csv")
        if row.get("kept", "").strip().lower() in ("y", "yes", "true", "1")
    }


def build(conn) -> tuple[list[tuple], dict]:
    played = matches_played(conn)
    faced = balls_faced(conn)
    bowled = balls_bowled(conn)
    phases = phase_counts(conn)
    positions = batting_positions(conn)
    kept = kept_for_squad()

    rows: list[tuple] = []
    stats = {
        "pairs": len(played),
        "roles": Counter(),
        "no_role": [],
        "modal_ties": 0,
        "no_position": 0,
        "no_usage": 0,
    }

    for (fs_id, person_id), matches in sorted(played.items()):
        key = (fs_id, person_id)
        role = role_for(
            faced.get(key, 0),
            bowled.get(key, 0),
            matches,
            is_keeper=key in kept,
        )
        if role is None:
            # Fielded a dismissal and nothing else. `role` is NOT NULL, and there is
            # no honest answer, so the pair is left out rather than given one.
            stats["no_role"].append(key)
            continue
        stats["roles"][role] += 1

        modal, low, high, tied = modal_position(positions.get(key, []))
        stats["modal_ties"] += tied
        stats["no_position"] += modal is None

        usage = bowling_usage(phases.get(key, {})) if bowled.get(key) else None
        stats["no_usage"] += bowled.get(key, 0) > 0 and usage is None

        rows.append(
            (fs_id, person_id, matches, role, modal, low, high, band_for(modal), usage)
        )
    return rows, stats


def write(conn, rows: list[tuple]) -> None:
    with conn.cursor() as cur:
        cur.execute("truncate squad_members")
        with cur.copy(
            """
            copy squad_members (
                franchise_season_id, person_id, matches_played, role,
                batting_position_modal, batting_position_min, batting_position_max,
                batting_band, bowling_usage
            ) from stdin
            """
        ) as copy:
            for row in rows:
                copy.write_row(row)
    conn.commit()


def keeper_coverage(conn) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select n, count(*) from (
                select fs.franchise_season_id,
                       count(*) filter (where sm.role = 'keeper') as n
                from franchise_seasons fs
                left join squad_members sm using (franchise_season_id)
                group by 1
            ) t group by 1 order by 1
            """
        )
        return cur.fetchall()


def main() -> int:
    with connect(direct=True) as conn:
        rows, stats = build(conn)
        write(conn, rows)

        total = len(rows)
        print(f"\n  (franchise-season, person) pairs   {stats['pairs']:>6}")
        print(f"  squad_members written              {total:>6}")
        print(f"  no role derivable, excluded        {len(stats['no_role']):>6}")
        print("\n  roles:")
        for role, n in stats["roles"].most_common():
            print(f"    {role:<12} {n:>6}  {n / total:6.1%}")
        print(f"\n  modal position tied, broken low    {stats['modal_ties']:>6}")
        print(f"  no batting position (never batted) {stats['no_position']:>6}")
        print(f"  bowled but no classifiable phase   {stats['no_usage']:>6}")

        print("\n  keepers per franchise-season:")
        for n, seasons in keeper_coverage(conn):
            flag = "" if n == 1 else "   <-- not exactly one"
            print(f"    {n} keeper(s): {seasons:>4} season(s){flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
