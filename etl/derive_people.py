"""The three fields Cricsheet does not give us. SPEC 6.1, 6.2, 6.3.

    uv run python -m etl.derive_people --nationality
    uv run python -m etl.derive_people --keepers
    uv run python -m etl.derive_people --bowling-style
    uv run python -m etl.derive_people --all

Each override CSV under etl/overrides/ has exactly one column a human fills in and
several this code derives. The hand-filled one is the only copy of work that cannot be
recomputed, so it is **never written over**; the derived ones are refreshed on every run,
because a signal added later is useless if it can only reach rows that did not exist yet.
`merge_override` takes the decision column by name so the two cannot be confused. A30.

A third kind exists and is neither: a column a human added that this code does not
generate at all, such as `bowling_style.csv`'s `source`. `merge_override` cannot refresh
what it does not produce, so it carries such a column across untouched rather than
dropping it - it never destroys a column it did not generate. A112.

None of these generators decides anything a human is meant to decide. Where the archive
proves an answer they record it; where it merely suggests one they rank the candidates
and leave the column blank, and blank means undecided rather than no.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import NamedTuple

from etl.db import REPO_ROOT, connect, data_dir

OVERRIDES = REPO_ROOT / "etl" / "overrides"

INTERNATIONAL_ARCHIVES = (
    "tests_male_json.zip",
    "odis_male_json.zip",
    "t20s_male_json.zip",
)

# SPEC 6.3. Every person who has bowled a legal ball is asked about.
#
# This was 30, on the reasoning that below it a style would be guesswork about someone
# who barely bowled. That reasoning was wrong about what the question IS, and A113 is
# what showed it: a bowling action is a fact about the PLAYER, not about the workload,
# so a man who bowled four balls in nineteen years still has one and it is exactly as
# knowable from the public record as R Ashwin's. The threshold was not protecting
# against guesswork -- nothing here guesses, a blank stays NULL -- it was declining to
# ask a question that has an answer.
#
# It had a measurable cost. The threshold is career-wide while A65 rates anyone who
# bowled a single ball, so 97 people ended up in the deck with no style at all, and
# they bowled 109 of 2,826 overs -- 3.9% -- in a real simulated season, landing in
# Season Analysis's own `unknown` bucket. That bucket exists for A23's reason and stays
# (a revised archive can always introduce a bowler nobody has ruled on yet), but it
# should be empty in the ordinary case rather than permanently holding a twenty-fifth
# of the attack.
#
# A8 recorded the old boundary as permanent -- "NULL means never bowled enough to be
# asked about" -- and that sentence is now false. 1, not 0, because someone who never
# bowled at all is not a bowler and has nothing to be asked.
MIN_LEGAL_BALLS_FOR_STYLE = 1

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


# The IPL has capped a playing XI at four overseas players in every season since 2008.
# That cap is the only EXTERNAL test this file has: everything else in SPEC 6.1 checks our
# reading of the archives against the archives. An XI our data says fielded five overseas
# players is our data being wrong, and it is wrong about somebody in that XI specifically.
OVERSEAS_PER_XI = 4


def real_xis(conn) -> list[list[str]]:
    """Every team sheet in the archive, one row per team per match.

    `named_in_squad`, not `participated`, and the difference is the check's correctness.
    The cap applies to the eleven a team NAMES, and `participated` also counts a fielding
    substitute who took a catch -- someone never in the XI and never against the limit.
    Counting them read 99 XIs as illegal against a true 63, so a third of the alarm was
    the check misreading its own basis.

    The Impact Player is deliberately still counted. From 2023 a team names twelve here,
    and the cap covers the XI and the Impact Player together: four overseas in the XI
    obliges an Indian Impact Player, so four across the twelve is still the right bound.
    """
    return [row[0] for row in conn.execute("""
        select array_agg(person_id)
        from appearances where named_in_squad
        group by franchise_season_id, match_id
    """).fetchall()]


def team_sheet_evidence(
    conn, nationality: dict[str, str]
) -> tuple[dict[str, int], set[str]]:
    """What the four-overseas cap says about a nationality map. Evidence, never a verdict.

    Two different strengths come out of the same walk and they must not be confused.

    `contradicted` counts, per player, the illegal XIs they appear in. It is a SUSPICION:
    five overseas in an XI means at least one of the five is wrong, and it does not say
    which, so every genuine overseas player in that XI is named alongside the culprit.

    `must_be_domestic` is a PROOF. If four players *other than* this one are overseas, the
    cap leaves no room, so this player is domestic whatever any archive says. Two
    restrictions keep it from cascading, and both are load-bearing:

    - the four are counted from FULL MEMBER nations only, because an associate label is
      exactly the kind we are here to doubt and admitting one would let a wrong answer
      certify the next one;
    - the proof is drawn only from XIs our data already calls LEGAL. Inside a contradicted
      XI at least one nationality is known to be wrong, so 'four others are overseas' is
      the very claim under suspicion -- and reading it anyway proves every player in that
      XI domestic, Warner and Pollard included, which is how this was caught.
    """
    contradicted: dict[str, int] = {}
    must_be_domestic: set[str] = set()
    for xi in real_xis(conn):
        overseas = [p for p in xi
                    if nationality.get(p) not in (None, HOME_NATION)]
        if len(overseas) > OVERSEAS_PER_XI:
            for p in overseas:
                contradicted[p] = contradicted.get(p, 0) + 1
            continue
        trusted = {p for p in overseas if nationality.get(p) in FULL_MEMBERS}
        for p in xi:
            if len(trusted - {p}) >= OVERSEAS_PER_XI:
                must_be_domestic.add(p)
    return contradicted, must_be_domestic


def cricinfo_ids() -> dict[str, str]:
    """Cricsheet person_id -> Cricinfo player id, from the register we already fetch.

    Every row in these four CSVs is a question only a human can answer, and the slow
    part of answering is not the judgement but finding the right player: initials-only
    names are ambiguous and several are shared outright. The register resolves that
    for free, and it covers all four files completely, so a reviewer identifies the
    player rather than searching for them. Nothing here fills a value in.
    """
    path = data_dir() / "people.csv"
    if not path.exists():
        raise SystemExit(f"{path} not found. Run: uv run python -m etl.download")
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            row["identifier"]: row.get("key_cricinfo", "")
            for row in csv.DictReader(handle)
        }


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


class Fielding(NamedTuple):
    """Everything one pass over the IPL archive can say about who kept wicket."""

    career_stumpings: Counter
    stumpings: dict[tuple[str, str], Counter]
    catches: dict[tuple[str, str], Counter]
    bowled: dict[tuple[str, str], Counter]


def fielding_evidence() -> Fielding:
    """Stumpings, catches and balls bowled by person and (match_id, fielding team).

    The keeper is the *fielder* on a stumping, and fielders are not stored - see A19,
    they are derivable and nothing else in this phase needs them. So this reads the
    archive rather than the database. Stumpings are a narrow signal by design: they
    prove a keeper but cannot disprove one, which is why keepers.csv exists.

    Catches and balls bowled are here to rank the *candidates* for the squads no
    stumping reaches, and they are not evidence of the same strength. Measured against
    the 140 squad-seasons a stumping does settle, the top non-bowling catch-taker is
    the proven keeper only 51% of the time, though one of the top three is 86% of the
    time. So they order a review list and must never decide one - see A30.

    The per-match breakdown is what SPEC 6.6 needs: 'keeper if flagged as a keeper
    *and they kept for that squad*'. A career flag alone puts a keeper in every squad
    they ever appeared in, including the seasons they played purely as a batter.
    """
    path = data_dir() / "ipl_json.zip"
    if not path.exists():
        raise SystemExit(f"{path} not found. Run: uv run python -m etl.download")

    career: Counter = Counter()
    stumpings: dict[tuple[str, str], Counter] = defaultdict(Counter)
    catches: dict[tuple[str, str], Counter] = defaultdict(Counter)
    bowled: dict[tuple[str, str], Counter] = defaultdict(Counter)
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
                squad = (match_id, fielding[0]) if len(fielding) == 1 else None
                for over in innings.get("overs", []):
                    for delivery in over["deliveries"]:
                        bowler = registry.get(delivery.get("bowler", ""))
                        if bowler is not None and squad is not None:
                            bowled[squad][bowler] += 1
                        for wicket in delivery.get("wickets", []):
                            if wicket["kind"] not in ("stumped", "caught"):
                                continue
                            for fielder in wicket.get("fielders", []):
                                person_id = registry.get(fielder.get("name", ""))
                                if person_id is None:
                                    continue
                                if wicket["kind"] == "caught":
                                    # A fielding substitute is not the squad's keeper
                                    # and would only add noise to the ranking. The
                                    # stumping path deliberately keeps its original
                                    # behaviour, which A24 was ratified against.
                                    if not fielder.get("substitute") and squad:
                                        catches[squad][person_id] += 1
                                    continue
                                career[person_id] += 1
                                if squad is not None:
                                    stumpings[squad][person_id] += 1
    return Fielding(career, stumpings, catches, bowled)


def merge_override(
    path: Path,
    columns: list[str],
    rows: list[dict],
    key: tuple[str, ...] = ("person_id",),
    *,
    decided: str,
) -> tuple[int, int, int]:
    """Rewrite the file, refreshing evidence and destroying nothing it did not generate.

    Each override CSV mixes two kinds of column and they need opposite treatment.
    `decided` names the one a human fills in: it is the only copy of work that cannot
    be recomputed, so it is carried across untouched and never written over, not even
    with an equal value. Every other column in `columns` is evidence we derived, so it
    is refreshed from this run - otherwise adding a new signal could never reach a row
    that already existed, which is the whole point of the exercise.

    Rows come out in the order given, so a caller that sorts by likelihood gets a file
    whose top row is the one most worth reading. An existing row with no counterpart in
    `rows` is kept verbatim at the end rather than dropped: it may hold a decision, and
    a generator that can silently delete one is a generator that will.

    That last rule has a column-wise twin, and A112 added it after the row-wise one had
    stood alone for months. A column present in the file but absent from `columns` was
    not generated here, so this function knows nothing about it and cannot recompute it:
    it is appended untouched instead of being dropped. `bowling_style.csv` grew a
    `source` column recording HOW each of 476 hand-checked styles was decided, and the
    old code would have erased it on the next --bowling-style run while faithfully
    preserving the decisions themselves - leaving a file whose provenance was gone and
    whose decisions looked untouched, which is indistinguishable from provenance that
    was never recorded. Registering each new column by name would work and was rejected:
    it protects only the columns somebody remembered to register, and the next audit
    column added is exactly the one nobody will. Same reasoning as A53 naming the
    dependant in a truncate rather than reaching for CASCADE - refuse loudly by default,
    never empty something silently.
    """
    OVERRIDES.mkdir(parents=True, exist_ok=True)
    existing: list[dict] = []
    present: list[str] = []
    if path.exists():
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            present = list(reader.fieldnames or ())
            existing = list(reader)
    # Columns this run did not generate. Never refreshed, never dropped.
    carried = [c for c in present if c not in columns]
    fieldnames = list(columns) + carried
    by_key = {tuple(row.get(c, "") for c in key): row for row in existing}

    merged, added = [], 0
    for row in rows:
        out = {c: str(row.get(c, "")) for c in columns}
        was = by_key.pop(tuple(str(row.get(c, "")) for c in key), None)
        if was is None:
            added += 1
            out.update({c: "" for c in carried})
        else:
            out[decided] = was.get(decided, "")
            out.update({c: was.get(c, "") for c in carried})
        merged.append(out)
    orphans = [{c: row.get(c, "") for c in fieldnames} for row in by_key.values()]

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(merged)
        writer.writerows(orphans)
    return len(merged) - added, added, len(orphans)


def report_merge(path: Path, counts: tuple[int, int, int]) -> None:
    kept, added, orphans = counts
    print(f"\n  {path.relative_to(REPO_ROOT)}: {kept} existing rows kept, "
          f"{added} appended, evidence refreshed")
    if orphans:
        print(f"  {orphans} row(s) no longer derived, kept at the end of the file")


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
    # The archive vote answers 'which nation has this player ever represented', and the
    # draft asks 'was he overseas THAT season'. They come apart for a player who emigrated
    # after his IPL career: S Sohal batted for Punjab from 2008 and later played T20Is for
    # the United States, so the vote marks him overseas for seasons in which he was not.
    # The four-overseas cap catches this from outside, so the contradicted rows join the
    # unknown ones in the CSV and a human decides. Nothing here fills a value in - an
    # override already outranks the archive, so a filled row is the whole repair.
    contradicted, must_be_domestic = team_sheet_evidence(
        conn, {pid: nat for nat, _, _, pid in updates if nat})
    # Being named in an illegal XI is not enough to be offered: five overseas in an XI
    # names every genuine overseas player in it alongside the culprit, which is 152
    # players and mostly Pollard and Warner. A row is offered only where the archive is
    # positively contradicted -- the cap PROVES the player domestic and the archive calls
    # him overseas -- or where the archive's answer is an associate nation, which is the
    # shape a post-IPL emigration leaves and never the shape an IPL overseas slot has.
    disputed = sorted(
        ((played.get(pid, 0), people[pid], pid) for pid in resolved
         if pid not in overrides and resolved[pid] != HOME_NATION
         and (pid in must_be_domestic or resolved[pid] not in FULL_MEMBERS)),
        reverse=True,
    )
    print(f"\n  {len(contradicted)} player(s) sit in an XI our data says fielded more "
          f"than {OVERSEAS_PER_XI} overseas players.")
    print(f"  {len(must_be_domestic)} player(s) are PROVEN domestic by the cap. "
          f"{len(disputed)} archive-resolved row(s) now offered for review:")
    for m, name, pid in disputed:
        why = "cap proves domestic" if pid in must_be_domestic else "associate nation"
        print(f"    {m:>4}  {name:<24} archive says {resolved[pid]:<26} {why}")

    cricinfo = cricinfo_ids()
    rows = [
        {
            "person_id": pid,
            "name": name,
            "matches": m,
            "cricinfo_id": cricinfo.get(pid, ""),
            "archive_says": resolved.get(pid, ""),
            "illegal_xis": contradicted.get(pid, 0),
            "cap_proves_domestic": "yes" if pid in must_be_domestic else "",
            "nationality": "",
        }
        for m, name, pid in sorted(unknown, reverse=True) + disputed
    ]
    for row in rows[:25]:
        print(f"    {row['matches']:>4}  {row['name']}")
    if len(rows) > 25:
        print(f"    ... {len(rows) - 25} more, all written to the CSV")

    path = OVERRIDES / "nationality.csv"
    report_merge(path, merge_override(
        path,
        ["person_id", "name", "matches", "cricinfo_id", "archive_says",
         "illegal_xis", "cap_proves_domestic", "nationality"],
        rows,
        decided="nationality",
    ))


def do_keepers(conn) -> None:
    people = ipl_people(conn)
    played = matches_played(conn)
    print("Scanning the IPL archive for stumpings, catches and overs ...")
    fielding = fielding_evidence()
    stumpings = fielding.career_stumpings
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
    career_catches: Counter = Counter()
    for counts in fielding.catches.values():
        career_catches.update(counts)
    career_bowled: Counter = Counter()
    for counts in fielding.bowled.values():
        career_bowled.update(counts)
    cricinfo = cricinfo_ids()

    rows = [
        {
            "person_id": pid,
            "name": name,
            "matches": m,
            "stumpings": stumpings.get(pid, 0),
            "catches": career_catches.get(pid, 0),
            "balls_bowled": career_bowled.get(pid, 0),
            "cricinfo_id": cricinfo.get(pid, ""),
            "is_keeper": "y" if pid in derived else "",
        }
        for m, name, pid in candidates
    ]
    path = OVERRIDES / "keepers.csv"
    report_merge(path, merge_override(
        path,
        ["person_id", "name", "matches", "stumpings", "catches", "balls_bowled",
         "cricinfo_id", "is_keeper"],
        rows,
        decided="is_keeper",
    ))
    print("  Blank is_keeper means undecided, not 'no'. Fill it in by hand.")

    do_keepers_by_season(conn, people, keepers, fielding)


# How many of the leading catch-takers to put in front of a reviewer for a squad no
# stumping reaches. Three because that is where the measurement landed: one of the top
# three is the proven keeper 86% of the time, against 51% for the top one alone. A
# fourth adds rows faster than it adds answers.
CATCH_CANDIDATES = 3


def do_keepers_by_season(conn, people, keepers, fielding: Fielding) -> None:
    """SPEC 6.6's 'kept for that squad' clause, as a reviewable CSV.

    A stumping proves someone kept for a given squad in a given season. Nothing
    disproves it, so a squad with no stumping all season leaves its keeper unproved
    rather than unkeepered - those rows go out blank for a human to settle.

    The rest of the columns exist to make settling them quick. They are ranked, not
    ratified: `catch_rank` orders the non-bowlers by catches taken, and
    `keeper_elsewhere` marks someone a stumping proved in a *different* season. Both
    were tested against the 140 squads a stumping does settle and both are too weak to
    decide - the career cross-reference names a unique candidate for only 39 of them
    and is wrong for 5, picking de Villiers over KS Bharat for 2021 RCB. That is the
    A23 failure exactly: a famous keeper playing a season as a pure batter is
    indistinguishable from the man who actually kept. So `kept` still starts blank
    unless a stumping proved it, and blank still means undecided.
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

        cur.execute(
            """
            select franchise_season_id, person_id, count(*)
            from appearances where participated group by 1, 2
            """
        )
        appeared = {(fs_id, pid): n for fs_id, pid, n in cur}

    def by_squad(source: dict[tuple[str, str], Counter]) -> dict[int, Counter]:
        out: dict[int, Counter] = defaultdict(Counter)
        for (match_id, team), counts in source.items():
            fs_id = by_match.get(match_id, {}).get(team)
            if fs_id is not None:
                out[fs_id].update(counts)
        return out

    proved = by_squad(fielding.stumpings)
    caught = by_squad(fielding.catches)
    bowled = by_squad(fielding.bowled)

    # Proved in some *other* season. Scoped this way on purpose: within the season it
    # would simply restate `stumpings`, and a check that restates its own input is no
    # check at all.
    seasons_kept: dict[str, set[int]] = defaultdict(set)
    for fs_id, counts in proved.items():
        for person_id in counts:
            seasons_kept[person_id].add(fs_id)

    cricinfo = cricinfo_ids()
    rows = []
    unproved_seasons = 0
    for fs_id in sorted(squad, key=lambda f: label.get(f, "")):
        unproved_seasons += not proved.get(fs_id)
        ranked = sorted(
            (
                p for p in squad[fs_id]
                if not bowled[fs_id].get(p) and caught[fs_id][p]
            ),
            key=lambda p: (-caught[fs_id][p], people.get(p, "")),
        )
        rank = {p: i + 1 for i, p in enumerate(ranked)}
        candidates = (
            set(proved.get(fs_id, ()))
            | (squad[fs_id] & keepers)
            | set(ranked[:CATCH_CANDIDATES])
        )
        for person_id in sorted(
            candidates,
            key=lambda p: (
                -proved[fs_id][p],
                rank.get(p, len(ranked) + 1),
                -caught[fs_id][p],
                people.get(p, ""),
            ),
        ):
            elsewhere = seasons_kept.get(person_id, set()) - {fs_id}
            rows.append(
                {
                    "franchise_season_id": fs_id,
                    "squad": label.get(fs_id, ""),
                    "person_id": person_id,
                    "name": people.get(person_id, ""),
                    "matches": appeared.get((fs_id, person_id), 0),
                    "stumpings": proved[fs_id][person_id],
                    "catches": caught[fs_id][person_id],
                    "catch_rank": rank.get(person_id, ""),
                    "balls_bowled": bowled[fs_id].get(person_id, 0),
                    "keeper_elsewhere": "y" if elsewhere else "",
                    "cricinfo_id": cricinfo.get(person_id, ""),
                    "kept": "y" if proved[fs_id][person_id] else "",
                }
            )

    path = OVERRIDES / "keepers_by_season.csv"
    report_merge(path, merge_override(
        path,
        ["franchise_season_id", "squad", "person_id", "name", "matches", "stumpings",
         "catches", "catch_rank", "balls_bowled", "keeper_elsewhere", "cricinfo_id",
         "kept"],
        rows,
        key=("franchise_season_id", "person_id"),
        decided="kept",
    ))
    print(f"\n  franchise-seasons              {len(squad):>5}")
    print(f"  with a stumping to prove one   {len(squad) - unproved_seasons:>5}")
    print(f"  with none, keeper unproved     {unproved_seasons:>5}")


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

    cricinfo = cricinfo_ids()
    rows = [
        {
            "person_id": pid,
            "name": name,
            "legal_balls": legal,
            "cricinfo_id": cricinfo.get(pid, ""),
            "bowling_style": "",
        }
        for pid, name, legal in bowlers
    ]
    path = OVERRIDES / "bowling_style.csv"
    print(f"\n  bowlers with >= {MIN_LEGAL_BALLS_FOR_STYLE} legal balls {len(bowlers):>5}")
    print(f"  styles filled in                  {len(filled):>5}")
    print(f"  still blank                       {len(bowlers) - len(filled):>5}")
    report_merge(path, merge_override(
        path,
        ["person_id", "name", "legal_balls", "cricinfo_id", "bowling_style"],
        rows,
        decided="bowling_style",
    ))
    # Nothing in Cricsheet carries a bowling action, and no signal in the archive
    # implies one: economy and phase usage correlate with pace or spin without ever
    # determining it. This file is the one of the four that cannot be narrowed by
    # derivation at all, only filled.
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
