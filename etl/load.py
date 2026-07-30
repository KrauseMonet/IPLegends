"""Load parsed matches into Postgres with COPY FROM STDIN.

    uv run python -m etl.load                  season 2016 only (the default)
    uv run python -m etl.load --season 2008 --season 2016
    uv run python -m etl.load --all            the whole archive

Row-by-row inserts are not an option: `deliveries` is ~296k rows against a remote
database, and COPY is the only reason that is viable.

Reloading a season is safe and expected during development. Matches for the target
seasons are deleted first and `deliveries` and `appearances` cascade from them, so
a reload replaces rather than duplicates. `people` is upserted instead, because a
person spans seasons and is not owned by any one of them.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import zipfile
from collections import Counter

from etl.db import connect, data_dir
from etl.parse_match import ParsedMatch, parse_match

IPL_ARCHIVE = "ipl_json.zip"
REGISTER = "people.csv"
DEFAULT_SEASON = 2016

DELIVERY_COLUMNS = (
    "match_id", "innings_no", "over_no", "ball_no",
    "batting_fs_id", "bowling_fs_id",
    "batter_id", "bowler_id", "non_striker_id",
    "runs_batter", "runs_extras",
    "extra_wides", "extra_noballs", "extra_byes", "extra_legbyes", "extra_penalty",
    "innings_scheduled_balls", "is_super_over", "legal_ball", "credited_to_bowler",
    "over_miscounted", "wicket_kind", "player_out_id", "phase",
)

MATCH_COLUMNS = (
    "match_id", "match_date", "season_year", "raw_season_label", "venue", "city",
    "team_a_fs_id", "team_b_fs_id", "toss_winner_fs_id", "toss_decision",
    "winner_fs_id", "result_type", "result_margin", "decided_by",
    "scheduled_overs", "had_super_over", "was_reduced", "player_of_match_id",
)

APPEARANCE_COLUMNS = (
    "match_id", "person_id", "franchise_season_id", "named_in_squad", "participated",
)


def parse_archive(seasons: set[int] | None) -> list[ParsedMatch]:
    archive = data_dir() / IPL_ARCHIVE
    if not archive.exists():
        raise SystemExit(f"{archive} not found. Run: uv run python -m etl.download")

    parsed: list[ParsedMatch] = []
    with zipfile.ZipFile(archive) as zf:
        for name in sorted(n for n in zf.namelist() if n.endswith(".json")):
            raw = json.loads(zf.read(name))
            # Cheap pre-filter on the season before doing the full parse. Derived
            # from dates, never info.season - see SPEC 4.2.
            if seasons is not None:
                year = int(raw["info"]["dates"][0][:4])
                if year not in seasons:
                    continue
            parsed.append(parse_match(raw, name[:-5]))
    return parsed


def register_names() -> dict[str, str]:
    """person_id -> unique_name from the Cricsheet register.

    `people.primary_name` is specified as the register's `unique_name`, which is
    stabler than the per-match spelling. Absent ids fall back to the match name.
    """
    path = data_dir() / REGISTER
    if not path.exists():
        print(f"warning: {path} absent; falling back to per-match names")
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            row["identifier"]: row["unique_name"]
            for row in csv.DictReader(handle)
            if row.get("identifier")
        }


def upsert_franchises(cur, matches: list[ParsedMatch]) -> dict[str, int]:
    names = sorted({m.team_a for m in matches} | {m.team_b for m in matches})
    cur.executemany(
        "insert into franchises (canonical_name) values (%s) on conflict do nothing",
        [(n,) for n in names],
    )
    cur.execute("select canonical_name, franchise_id from franchises")
    return dict(cur.fetchall())


def upsert_franchise_seasons(
    cur, matches: list[ParsedMatch], franchise_ids: dict[str, int]
) -> dict[tuple[str, int], int]:
    """One row per franchise per season, carrying that season's own name."""
    wanted: dict[tuple[str, int], str] = {}
    for match in matches:
        for canonical_name, display in match.display_names.items():
            wanted[(canonical_name, match.season_year)] = display

    cur.executemany(
        """
        insert into franchise_seasons (franchise_id, season_year, display_name)
        values (%s, %s, %s)
        on conflict (franchise_id, season_year)
            do update set display_name = excluded.display_name
        """,
        [
            (franchise_ids[name], year, display)
            for (name, year), display in sorted(wanted.items())
        ],
    )
    cur.execute(
        """
        select f.canonical_name, fs.season_year, fs.franchise_season_id
        from franchise_seasons fs join franchises f using (franchise_id)
        """
    )
    return {(name, year): fs_id for name, year, fs_id in cur.fetchall()}


def upsert_people(cur, matches: list[ParsedMatch]) -> int:
    canonical_names = register_names()
    people: dict[str, str] = {}
    for match in matches:
        for person in match.people:
            people[person.person_id] = canonical_names.get(
                person.person_id, person.name
            )

    cur.executemany(
        """
        insert into people (person_id, primary_name) values (%s, %s)
        on conflict (person_id) do update set primary_name = excluded.primary_name
        """,
        sorted(people.items()),
    )
    return len(people)


def copy_matches(cur, matches: list[ParsedMatch], fs: dict[tuple[str, int], int]) -> None:
    columns = ", ".join(MATCH_COLUMNS)
    with cur.copy(f"copy matches ({columns}) from stdin") as copy:
        for m in matches:
            year = m.season_year
            copy.write_row(
                (
                    m.match_id, m.match_date, year, m.raw_season_label, m.venue, m.city,
                    fs[(m.team_a, year)], fs[(m.team_b, year)],
                    fs[(m.toss_winner, year)] if m.toss_winner else None,
                    m.toss_decision,
                    fs[(m.winner, year)] if m.winner else None,
                    m.result_type, m.result_margin, m.decided_by,
                    m.scheduled_overs, m.had_super_over, m.was_reduced,
                    m.player_of_match_id,
                )
            )


def copy_appearances(
    cur, matches: list[ParsedMatch], fs: dict[tuple[str, int], int]
) -> int:
    columns = ", ".join(APPEARANCE_COLUMNS)
    total = 0
    with cur.copy(f"copy appearances ({columns}) from stdin") as copy:
        for m in matches:
            for a in m.appearances:
                copy.write_row(
                    (
                        m.match_id, a.person_id, fs[(a.franchise, m.season_year)],
                        a.named_in_squad, a.participated,
                    )
                )
                total += 1
    return total


def copy_deliveries(
    cur, matches: list[ParsedMatch], fs: dict[tuple[str, int], int]
) -> int:
    columns = ", ".join(DELIVERY_COLUMNS)
    total = 0
    with cur.copy(f"copy deliveries ({columns}) from stdin") as copy:
        for m in matches:
            year = m.season_year
            for d in m.deliveries:
                copy.write_row(
                    (
                        m.match_id, d.innings_no, d.over_no, d.ball_no,
                        fs[(d.batting_franchise, year)],
                        fs[(d.bowling_franchise, year)],
                        d.batter_id, d.bowler_id, d.non_striker_id,
                        d.runs_batter, d.runs_extras,
                        d.extra_wides, d.extra_noballs, d.extra_byes,
                        d.extra_legbyes, d.extra_penalty,
                        d.innings_scheduled_balls, d.is_super_over, d.legal_ball,
                        d.credited_to_bowler, d.over_miscounted,
                        d.wicket_kind, d.player_out_id, d.phase,
                    )
                )
                total += 1
    return total


def load(seasons: set[int] | None) -> None:
    label = "the full archive" if seasons is None else f"season(s) {sorted(seasons)}"
    print(f"Parsing {label} ...")
    matches = parse_archive(seasons)
    if not matches:
        raise SystemExit(f"No matches found for {label}.")

    warnings = [w for m in matches for w in m.warnings]
    print(f"  {len(matches)} matches parsed, "
          f"{sum(len(m.deliveries) for m in matches)} deliveries")

    # Unpooled endpoint: PgBouncer transaction mode makes COPY unreliable.
    with connect(direct=True) as conn:
        with conn.cursor() as cur:
            franchise_ids = upsert_franchises(cur, matches)
            fs = upsert_franchise_seasons(cur, matches, franchise_ids)
            n_people = upsert_people(cur, matches)

            cur.execute(
                "delete from matches where match_id = any(%s)",
                ([m.match_id for m in matches],),
            )
            replaced = cur.rowcount

            copy_matches(cur, matches, fs)
            n_appearances = copy_appearances(cur, matches, fs)
            n_deliveries = copy_deliveries(cur, matches, fs)
        conn.commit()

    print(f"\n  franchises        {len(franchise_ids):>7}")
    print(f"  franchise_seasons {len(fs):>7}")
    print(f"  people            {n_people:>7}  (upserted)")
    print(f"  matches           {len(matches):>7}  ({replaced} replaced)")
    print(f"  appearances       {n_appearances:>7}")
    print(f"  deliveries        {n_deliveries:>7}")

    if warnings:
        print(f"\n{len(warnings)} parser warning(s) - values left NULL, never guessed:")
        for w in warnings:
            print(f"  {w}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, action="append", dest="seasons")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)

    if args.all and args.seasons:
        raise SystemExit("--all and --season are mutually exclusive.")
    if args.all:
        load(None)
    else:
        load(set(args.seasons or [DEFAULT_SEASON]))


if __name__ == "__main__":
    main(sys.argv[1:])
