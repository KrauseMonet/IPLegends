"""The three fields Cricsheet does not give us. SPEC 6.1, 6.2, 6.3.

    uv run python -m etl.derive_people --nationality
    uv run python -m etl.derive_people --keepers
    uv run python -m etl.derive_people --bowling-style
    uv run python -m etl.derive_people --all

Each override CSV under etl/overrides/ is hand-maintained and authoritative. These
generators therefore **never overwrite an existing row** - they append people who are
missing and leave everything already in the file exactly as it is. A generator that
clobbered a hand-filled column would destroy the only copy of work that cannot be
recomputed.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from etl.db import REPO_ROOT, connect, data_dir

OVERRIDES = REPO_ROOT / "etl" / "overrides"

INTERNATIONAL_ARCHIVES = (
    "tests_male_json.zip",
    "odis_male_json.zip",
    "t20s_male_json.zip",
)

# SPEC 6.3. Below this a style would be guesswork about someone who barely bowled.
MIN_LEGAL_BALLS_FOR_STYLE = 30

# The IPL's home nation, so `is_overseas` is a fact about a known nationality rather
# than a guess. It is never used as a fallback: see the note on A3 below.
HOME_NATION = "India"

# Invitational sides that field players from several countries. A cap for one says
# nothing about nationality, and for a player with no other cap in these archives it
# is the difference between 'unknown' and a confidently wrong answer.
COMPOSITE_TEAMS = frozenset({"ICC World XI", "World XI", "Africa XI", "Asia XI"})

# The twelve ICC full members, checked against the archives on every run. Afghanistan
# is absent from all three, which is what made 'default to India' silently produce
# eight false records - see A23. A hardcoded list is safe here in a way an expected
# schedule is not: full membership has changed twice in thirty years, and the check
# reports rather than corrects, so staleness shows up as a stale line of output.
FULL_MEMBERS = (
    "Afghanistan", "Australia", "Bangladesh", "England", "India", "Ireland",
    "New Zealand", "Pakistan", "South Africa", "Sri Lanka", "West Indies", "Zimbabwe",
)


def ipl_people(conn) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("select person_id, primary_name from people")
        return dict(cur.fetchall())


def matches_played(conn) -> dict[str, int]:
    """Participated matches per person, for sorting review lists by what matters."""
    with conn.cursor() as cur:
        cur.execute(
            "select person_id, count(*) from appearances where participated group by 1"
        )
        return dict(cur.fetchall())


def report_coverage(all_teams: set[str]) -> None:
    """Which cricketing nations the archives can speak for at all. A23.

    A nation missing here cannot be derived for anybody, so every one of its players
    falls to the override. Afghanistan is the known case; this runs every time so a
    second one cannot arrive unnoticed in a later Cricsheet revision.
    """
    missing = [nation for nation in FULL_MEMBERS if nation not in all_teams]
    print(f"  archives cover {len(all_teams)} team names")
    if missing:
        print(f"  ICC FULL MEMBERS ABSENT FROM ALL THREE ARCHIVES: {', '.join(missing)}")
        print("  Every player of theirs is unknown and must come from the override.")
    else:
        print("  all 12 ICC full members present")


def national_teams(wanted: set[str]) -> tuple[dict[str, str], list[str]]:
    """person_id -> national team, from the Test, ODI and T20I archives combined.

    T20I alone would miss anyone whose caps predate the format or came only in the
    longer forms, which is the whole reason A3 pooled all three.
    """
    seen: dict[str, Counter] = defaultdict(Counter)
    composite_only: list[str] = []
    all_teams: set[str] = set()
    for archive in INTERNATIONAL_ARCHIVES:
        path = data_dir() / archive
        if not path.exists():
            raise SystemExit(f"{path} not found. Run: uv run python -m etl.download")
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if not name.endswith(".json"):
                    continue
                info = json.loads(zf.read(name))["info"]
                registry = info.get("registry", {}).get("people", {})
                all_teams.update(info.get("teams") or ())
                for team, players in (info.get("players") or {}).items():
                    for player in players:
                        person_id = registry.get(player)
                        if person_id in wanted:
                            seen[person_id][team] += 1

    report_coverage(all_teams)

    resolved: dict[str, str] = {}
    conflicts: list[str] = []
    for person_id, counts in seen.items():
        ranked = [(t, n) for t, n in counts.most_common() if t not in COMPOSITE_TEAMS]
        if not ranked:
            composite_only.append(f"{person_id}: {dict(counts)}")
            continue
        resolved[person_id] = ranked[0][0]
        if len(ranked) > 1:
            conflicts.append(
                f"{person_id}: {', '.join(f'{t} x{n}' for t, n in ranked)}"
            )
    if composite_only:
        print(f"  {len(composite_only)} capped only by a composite side, left unknown:")
        for line in composite_only:
            print(f"    {line}")
    return resolved, conflicts


def keeper_stumpings() -> tuple[Counter, dict[tuple[str, str], Counter]]:
    """Stumpings by person, and by (match_id, fielding team name).

    The keeper is the *fielder* on a stumping, and fielders are not stored - see A19,
    they are derivable and nothing else in this phase needs them. So this reads the
    archive rather than the database. It is a narrow signal by design: it proves a
    keeper but cannot disprove one, which is why keepers.csv exists.

    The per-match breakdown is what SPEC 6.6 needs: 'keeper if flagged as a keeper
    *and they kept for that squad*'. A career flag alone puts a keeper in every squad
    they ever appeared in, including the seasons they played purely as a batter.
    """
    path = data_dir() / "ipl_json.zip"
    if not path.exists():
        raise SystemExit(f"{path} not found. Run: uv run python -m etl.download")

    career: Counter = Counter()
    per_match: dict[tuple[str, str], Counter] = defaultdict(Counter)
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if not name.endswith(".json"):
                continue
            raw = json.loads(zf.read(name))
            match_id = name[: -len(".json")]
            teams = raw["info"]["teams"]
            registry = raw["info"].get("registry", {}).get("people", {})
            for innings in raw.get("innings", []):
                fielding = [t for t in teams if t != innings.get("team")]
                for over in innings.get("overs", []):
                    for delivery in over["deliveries"]:
                        for wicket in delivery.get("wickets", []):
                            if wicket["kind"] != "stumped":
                                continue
                            for fielder in wicket.get("fielders", []):
                                person_id = registry.get(fielder.get("name", ""))
                                if person_id is None:
                                    continue
                                career[person_id] += 1
                                if len(fielding) == 1:
                                    per_match[(match_id, fielding[0])][person_id] += 1
    return career, per_match


def merge_override(
    path: Path, columns: list[str], rows: list[dict], key: tuple[str, ...] = ("person_id",)
) -> tuple[int, int]:
    """Append rows whose key is absent. Never touch a row already present."""
    OVERRIDES.mkdir(parents=True, exist_ok=True)
    existing: list[dict] = []
    if path.exists():
        with path.open(newline="", encoding="utf-8") as handle:
            existing = list(csv.DictReader(handle))
    known = {tuple(row[c] for c in key) for row in existing}
    added = [row for row in rows if tuple(str(row[c]) for c in key) not in known]

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in existing:
            writer.writerow({c: row.get(c, "") for c in columns})
        for row in added:
            writer.writerow({c: row.get(c, "") for c in columns})
    return len(existing), len(added)


def read_override(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def do_nationality(conn) -> None:
    people = ipl_people(conn)
    played = matches_played(conn)
    print(f"Scanning {len(INTERNATIONAL_ARCHIVES)} international archives ...")
    resolved, conflicts = national_teams(set(people))

    overrides = {
        row["person_id"]: row["nationality"].strip()
        for row in read_override(OVERRIDES / "nationality.csv")
        if row.get("nationality", "").strip()
    }

    updates = []
    unknown = []
    for person_id, name in people.items():
        if person_id in overrides:
            nationality, source = overrides[person_id], "override"
        elif person_id in resolved:
            nationality, source = resolved[person_id], "international"
        else:
            # A3 originally defaulted these to India. It cannot: the three archives
            # contain no Afghanistan team at all, so every uncapped-elsewhere Afghan
            # was being written down as Indian and not overseas. Unknown stays null.
            nationality, source = None, None
            unknown.append((played.get(person_id, 0), name, person_id))
        overseas = None if nationality is None else nationality != HOME_NATION
        updates.append((nationality, source, overseas, person_id))

    with conn.cursor() as cur:
        cur.executemany(
            """
            update people set nationality = %s, nationality_source = %s,
                              is_overseas = %s
            where person_id = %s
            """,
            updates,
        )
    conn.commit()

    by_source = Counter(source for _, source, _, _ in updates)
    print(f"\n  people                {len(people):>5}")
    for source in ("international", "override"):
        print(f"  {source:<21} {by_source[source]:>5}")
    print(f"  {'unknown (null)':<21} {by_source[None]:>5}")
    print(f"  overseas              {sum(1 for _, _, o, _ in updates if o):>5}")
    print(f"  home nation           "
          f"{sum(1 for _, _, o, _ in updates if o is False):>5}")

    if conflicts:
        print(f"\n  {len(conflicts)} player(s) capped by more than one team "
              f"(most frequent wins):")
        for line in conflicts[:10]:
            print(f"    {line}")

    # A3/A23: this list needs full review, not a spot check. Until a row is filled in
    # the player has no nationality and cannot be counted against the overseas rule.
    # Banded by IPL footprint because that, not alphabetical order, is what decides
    # whether an unknown can quietly break a legal XI.
    print(f"\n  {len(unknown)} with no international cap in the archives, "
          f"sorted by matches played - REVIEW ALL OF THESE:")
    bands = (
        ("50+ matches", 50, 10**9),
        ("20-49", 20, 49),
        ("5-19", 5, 19),
        ("1-4", 1, 4),
        ("never played", 0, 0),
    )
    for label, low, high in bands:
        n = sum(1 for m, _, _ in unknown if low <= m <= high)
        print(f"    {label:<14} {n:>4}")
    rows = [
        {"person_id": pid, "name": name, "matches": m, "nationality": ""}
        for m, name, pid in sorted(unknown, reverse=True)
    ]
    for row in rows[:25]:
        print(f"    {row['matches']:>4}  {row['name']}")
    if len(rows) > 25:
        print(f"    ... {len(rows) - 25} more, all written to the CSV")

    path = OVERRIDES / "nationality.csv"
    before, added = merge_override(
        path, ["person_id", "name", "matches", "nationality"], rows
    )
    print(f"\n  {path.relative_to(REPO_ROOT)}: {before} existing rows kept, "
          f"{added} appended")


def do_keepers(conn) -> None:
    people = ipl_people(conn)
    played = matches_played(conn)
    print("Scanning the IPL archive for stumpings ...")
    stumpings, per_match = keeper_stumpings()
    derived = {pid for pid in stumpings if pid in people}

    overrides = read_override(OVERRIDES / "keepers.csv")
    marked = {
        row["person_id"]
        for row in overrides
        if row.get("is_keeper", "").strip().lower() in ("y", "yes", "true", "1")
    }
    keepers = derived | marked

    with conn.cursor() as cur:
        cur.execute("update people set is_keeper = false")
        cur.executemany(
            "update people set is_keeper = true where person_id = %s",
            [(pid,) for pid in sorted(keepers)],
        )
    conn.commit()

    print(f"\n  stumpings found            {sum(stumpings.values()):>5}")
    print(f"  keepers proved by stumping {len(derived):>5}")
    print(f"  keepers added by hand      {len(marked - derived):>5}")
    print(f"  is_keeper set              {len(keepers):>5}")

    # Everyone who kept is in here as a yes; everyone plausible is in as a blank for
    # a human to decide. Sorted so the ones that matter are reviewed first.
    candidates = sorted(
        (
            (played.get(pid, 0), people[pid], pid)
            for pid in people
            if pid in derived or played.get(pid, 0) >= 10
        ),
        reverse=True,
    )
    rows = [
        {
            "person_id": pid,
            "name": name,
            "matches": m,
            "stumpings": stumpings.get(pid, 0),
            "is_keeper": "y" if pid in derived else "",
        }
        for m, name, pid in candidates
    ]
    path = OVERRIDES / "keepers.csv"
    before, added = merge_override(
        path, ["person_id", "name", "matches", "stumpings", "is_keeper"], rows
    )
    print(f"\n  {path.relative_to(REPO_ROOT)}: {before} existing rows kept, "
          f"{added} appended")
    print("  Blank is_keeper means undecided, not 'no'. Fill it in by hand.")

    do_keepers_by_season(conn, people, keepers, per_match)


def do_keepers_by_season(conn, people, keepers, per_match) -> None:
    """SPEC 6.6's 'kept for that squad' clause, as a reviewable CSV.

    A stumping proves someone kept for a given squad in a given season. Nothing
    disproves it, so a squad with no stumping all season leaves its keeper unproved
    rather than unkeepered - those rows go out blank for a human to settle.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            select m.match_id, fs.franchise_season_id, fs.display_name, fs.season_year
            from matches m
            join franchise_seasons fs
              on fs.franchise_season_id in (m.team_a_fs_id, m.team_b_fs_id)
            """
        )
        by_match: dict[str, dict[str, int]] = defaultdict(dict)
        label: dict[int, str] = {}
        for match_id, fs_id, display_name, season_year in cur:
            by_match[match_id][display_name] = fs_id
            label[fs_id] = f"{season_year} {display_name}"

        cur.execute("select franchise_season_id, person_id from squad_members")
        squad: dict[int, set[str]] = defaultdict(set)
        for fs_id, person_id in cur:
            squad[fs_id].add(person_id)

    proved: dict[int, Counter] = defaultdict(Counter)
    for (match_id, team), counts in per_match.items():
        fs_id = by_match.get(match_id, {}).get(team)
        if fs_id is not None:
            proved[fs_id].update(counts)

    rows = []
    unproved_seasons = 0
    for fs_id in sorted(squad, key=lambda f: label.get(f, "")):
        candidates = set(proved.get(fs_id, ())) | (squad[fs_id] & keepers)
        unproved_seasons += not proved.get(fs_id)
        for person_id in sorted(candidates, key=lambda p: -proved[fs_id][p]):
            rows.append(
                {
                    "franchise_season_id": fs_id,
                    "squad": label.get(fs_id, ""),
                    "person_id": person_id,
                    "name": people.get(person_id, ""),
                    "stumpings": proved[fs_id][person_id],
                    "kept": "y" if proved[fs_id][person_id] else "",
                }
            )

    path = OVERRIDES / "keepers_by_season.csv"
    before, added = merge_override(
        path,
        ["franchise_season_id", "squad", "person_id", "name", "stumpings", "kept"],
        rows,
        key=("franchise_season_id", "person_id"),
    )
    print(f"\n  franchise-seasons              {len(squad):>5}")
    print(f"  with a stumping to prove one   {len(squad) - unproved_seasons:>5}")
    print(f"  with none, keeper unproved     {unproved_seasons:>5}")
    print(f"  {path.relative_to(REPO_ROOT)}: {before} existing rows kept, "
          f"{added} appended")


def do_bowling_style(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            select d.bowler_id, p.primary_name, count(*) legal
            from deliveries d join people p on p.person_id = d.bowler_id
            where d.legal_ball
            group by 1, 2 having count(*) >= %s
            order by legal desc
            """,
            (MIN_LEGAL_BALLS_FOR_STYLE,),
        )
        bowlers = cur.fetchall()

    overrides = {
        row["person_id"]: row.get("bowling_style", "").strip().lower()
        for row in read_override(OVERRIDES / "bowling_style.csv")
    }
    filled = {pid: style for pid, style in overrides.items() if style in ("pace", "spin")}

    with conn.cursor() as cur:
        cur.execute("update people set bowling_style = null")
        cur.executemany(
            "update people set bowling_style = %s where person_id = %s",
            [(style, pid) for pid, style in sorted(filled.items())],
        )
    conn.commit()

    rows = [
        {
            "person_id": pid,
            "name": name,
            "legal_balls": legal,
            "bowling_style": overrides.get(pid, ""),
        }
        for pid, name, legal in bowlers
    ]
    path = OVERRIDES / "bowling_style.csv"
    before, added = merge_override(
        path, ["person_id", "name", "legal_balls", "bowling_style"], rows
    )
    print(f"\n  bowlers with >= {MIN_LEGAL_BALLS_FOR_STYLE} legal balls {len(bowlers):>5}")
    print(f"  styles filled in                  {len(filled):>5}")
    print(f"  still blank                       {len(bowlers) - len(filled):>5}")
    print(f"\n  {path.relative_to(REPO_ROOT)}: {before} existing rows kept, "
          f"{added} appended")
    if len(filled) < len(bowlers):
        print("  A8: pace/spin slots collapse to a generic bowler slot until this "
              "is filled.\n  Blank stays NULL in the database - never guessed.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nationality", action="store_true")
    parser.add_argument("--keepers", action="store_true")
    parser.add_argument("--bowling-style", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)

    steps = []
    if args.all or args.nationality:
        steps.append(("SPEC 6.1 nationality", do_nationality))
    if args.all or args.keepers:
        steps.append(("SPEC 6.2 wicketkeepers", do_keepers))
    if args.all or args.bowling_style:
        steps.append(("SPEC 6.3 pace versus spin", do_bowling_style))
    if not steps:
        raise SystemExit("Nothing to do. Pass --nationality, --keepers, "
                         "--bowling-style or --all.")

    with connect(direct=True) as conn:
        for title, step in steps:
            print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")
            step(conn)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
