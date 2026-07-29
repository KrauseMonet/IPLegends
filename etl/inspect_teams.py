"""Stage 3 reconnaissance and permanent archive audit.

Reports the team-name-by-year grid, the raw season-label mapping (SPEC 4.2), the exact
deck size in franchise-seasons, and per-season gaps in match numbering.

The audit is written back into data/manifest.json alongside the archive checksum it was
computed from, so a future discrepancy can be attributed to a revised Cricsheet archive
rather than mistaken for a download failure. Cricsheet is contributor-driven and recent
seasons do get revised after the fact.

    uv run python -m etl.inspect_teams
"""

from __future__ import annotations

import json
import zipfile
from collections import Counter, defaultdict
from datetime import date

from etl.db import data_dir
from etl.franchise_map import canonical

IPL_ARCHIVE = "ipl_json.zip"


def season_year_from_dates(dates: list[str]) -> int:
    """SPEC 4.2: derive the season from match dates, never from info.season."""
    return date.fromisoformat(dates[0]).year


def scan() -> dict:
    archive = data_dir() / IPL_ARCHIVE
    if not archive.exists():
        raise SystemExit(f"{archive} not found. Run: uv run python -m etl.download")

    team_years: dict[str, Counter] = defaultdict(Counter)
    franchise_years: dict[str, set[int]] = defaultdict(set)
    label_by_year: dict[int, Counter] = defaultdict(Counter)
    matches_per_year: Counter = Counter()
    numbered: dict[int, list[int]] = defaultdict(list)
    unnumbered: Counter = Counter()
    cross_year: list[tuple[str, list[str]]] = []
    total = 0

    with zipfile.ZipFile(archive) as zf:
        names = [n for n in zf.namelist() if n.endswith(".json")]
        for name in names:
            with zf.open(name) as handle:
                info = json.load(handle)["info"]

            dates = info.get("dates") or []
            if not dates:
                continue

            year = season_year_from_dates(dates)
            if len({date.fromisoformat(d).year for d in dates}) > 1:
                cross_year.append((name, dates))

            total += 1
            matches_per_year[year] += 1
            label_by_year[year][str(info.get("season"))] += 1

            number = (info.get("event") or {}).get("match_number")
            if isinstance(number, int):
                numbered[year].append(number)
            else:
                unnumbered[year] += 1

            for team in info.get("teams", []):
                team_years[team][year] += 1
                franchise_years[canonical(team)].add(year)

    gaps = {}
    for year, nums in numbered.items():
        highest = max(nums)
        missing = sorted(set(range(1, highest + 1)) - set(nums))
        if missing:
            gaps[year] = missing

    return {
        "team_years": team_years,
        "franchise_years": franchise_years,
        "label_by_year": label_by_year,
        "matches_per_year": matches_per_year,
        "numbered": numbered,
        "unnumbered": unnumbered,
        "gaps": gaps,
        "cross_year": cross_year,
        "total": total,
        "file_count": len(names),
    }


def deck_size(franchise_years: dict[str, set[int]]) -> int:
    return sum(len(years) for years in franchise_years.values())


def write_audit(result: dict) -> None:
    """Persist the audit into the manifest, keyed to the archive checksum."""
    manifest_path = data_dir() / "manifest.json"
    if not manifest_path.exists():
        print("\n(no manifest.json; skipping audit write)")
        return

    raw = json.loads(manifest_path.read_text())
    files = raw["files"] if isinstance(raw, dict) else raw
    ipl_sha = next(
        (f["sha256"] for f in files if f["filename"] == IPL_ARCHIVE), None
    )

    audit = {
        "ipl_archive_sha256": ipl_sha,
        "audited_at": date.today().isoformat(),
        "total_matches": result["total"],
        "seasons": sorted(result["matches_per_year"]),
        "franchise_seasons": deck_size(result["franchise_years"]),
        "matches_per_season": {
            str(y): n for y, n in sorted(result["matches_per_year"].items())
        },
        # Absent match numbers. These are matches abandoned without a ball bowled:
        # Cricsheet publishes ball-by-ball data, so a match with no deliveries produces
        # no file. Recorded so a future gap can be told apart from a failed download.
        "missing_match_numbers": {
            str(y): nums for y, nums in sorted(result["gaps"].items())
        },
        "raw_season_labels": {
            str(y): sorted(labels)
            for y, labels in sorted(result["label_by_year"].items())
        },
        "seasons_per_franchise": {
            f: sorted(years) for f, years in sorted(result["franchise_years"].items())
        },
    }

    manifest_path.write_text(
        json.dumps({"files": files, "ipl_archive_audit": audit}, indent=2) + "\n"
    )
    print(f"\nAudit written to {manifest_path}")


def report(result: dict) -> None:
    matches_per_year = result["matches_per_year"]
    franchise_years = result["franchise_years"]

    print("=" * 78)
    print("MATCHES PER SEASON")
    print("=" * 78)
    for year in sorted(matches_per_year):
        gap = result["gaps"].get(year)
        note = f"   missing match numbers: {gap}" if gap else ""
        print(f"  {year}   {matches_per_year[year]:>4} matches{note}")
    print(f"\n  {'TOTAL':<6} {result['total']:>4} matches "
          f"({result['file_count']} json files)")

    print()
    print("=" * 78)
    print("RAW info.season LABEL -> DERIVED SEASON YEAR   (SPEC 4.2 trap)")
    print("=" * 78)
    for year in sorted(result["label_by_year"]):
        labels = result["label_by_year"][year]
        rendered = ", ".join(f"{lab!r} x{n}" for lab, n in labels.most_common())
        flag = "  <-- split label" if any("/" in lab for lab in labels) else ""
        print(f"  {year}   {rendered}{flag}")

    print()
    print("=" * 78)
    print("DECK: FRANCHISE-SEASONS")
    print("=" * 78)
    for franchise, years in sorted(
        franchise_years.items(), key=lambda kv: (-len(kv[1]), kv[0])
    ):
        span = sorted(years)
        contiguous = span == list(range(span[0], span[-1] + 1))
        rendered = (
            f"{span[0]}-{span[-1]}" if contiguous and len(span) > 1
            else ", ".join(map(str, span))
        )
        # SPEC 1.1: the "same franchise, different year" reroll needs >= 2 seasons.
        reroll = len(span) - 1
        note = "  REROLL UNAVAILABLE" if reroll == 0 else f"  {reroll} alt season(s)"
        print(f"  {franchise:<30} {len(span):>2} seasons  {rendered:<24}{note}")

    print(f"\n  {len(franchise_years)} franchises, "
          f"{deck_size(franchise_years)} franchise-seasons in the deck")

    if result["cross_year"]:
        print("\nMATCHES SPANNING A YEAR BOUNDARY:")
        for name, dates in result["cross_year"]:
            print(f"  {name}: {dates}")


if __name__ == "__main__":
    outcome = scan()
    report(outcome)
    write_audit(outcome)
