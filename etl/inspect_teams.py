"""Stage 3 reconnaissance: what team names exist, and in which seasons.

Reads the IPL archive without unpacking it and prints the distinct team-name-by-year
grid. SPEC 4.3 forbids guessing franchise mappings, so this output is reviewed by hand
before etl/overrides/franchises.csv is written.

Also reports the season-label trap (SPEC 4.2) by showing which raw info.season strings
map to which derived season year.

    uv run python -m etl.inspect_teams
"""

from __future__ import annotations

import json
import zipfile
from collections import Counter, defaultdict
from datetime import date

from etl.db import data_dir

IPL_ARCHIVE = "ipl_json.zip"


def season_year_from_dates(dates: list[str]) -> int:
    """SPEC 4.2: derive the season from match dates, never from info.season."""
    return date.fromisoformat(dates[0]).year


def scan() -> dict:
    archive = data_dir() / IPL_ARCHIVE
    if not archive.exists():
        raise SystemExit(f"{archive} not found. Run: uv run python -m etl.download")

    team_years: dict[str, Counter] = defaultdict(Counter)
    label_by_year: dict[int, Counter] = defaultdict(Counter)
    matches_per_year: Counter = Counter()
    cross_year_matches: list[tuple[str, list[str]]] = []
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
            years_spanned = {date.fromisoformat(d).year for d in dates}
            if len(years_spanned) > 1:
                cross_year_matches.append((name, dates))

            total += 1
            matches_per_year[year] += 1
            label_by_year[year][str(info.get("season"))] += 1
            for team in info.get("teams", []):
                team_years[team][year] += 1

    return {
        "team_years": team_years,
        "label_by_year": label_by_year,
        "matches_per_year": matches_per_year,
        "cross_year_matches": cross_year_matches,
        "total": total,
        "file_count": len(names),
    }


def report(result: dict) -> None:
    team_years = result["team_years"]
    matches_per_year = result["matches_per_year"]

    print("=" * 78)
    print("MATCHES PER SEASON")
    print("=" * 78)
    for year in sorted(matches_per_year):
        print(f"  {year}   {matches_per_year[year]:>4} matches")
    print(f"\n  {'TOTAL':<6} {result['total']:>4} matches "
          f"({result['file_count']} json files in archive)")

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
    print("DISTINCT TEAM NAMES BY SEASON   (SPEC 4.3 - confirm mappings by hand)")
    print("=" * 78)
    print(f"  {len(team_years)} distinct team name strings\n")

    ordered = sorted(team_years.items(), key=lambda kv: (min(kv[1]), kv[0]))
    for team, years in ordered:
        span = sorted(years)
        total_matches = sum(years.values())
        if len(span) == 1:
            season_str = str(span[0])
        else:
            contiguous = span == list(range(span[0], span[-1] + 1))
            season_str = (
                f"{span[0]}-{span[-1]}" if contiguous else ", ".join(map(str, span))
            )
        print(f"  {team:<34} {season_str:<22} {total_matches:>4} matches")

    if result["cross_year_matches"]:
        print()
        print("=" * 78)
        print("MATCHES SPANNING A YEAR BOUNDARY   (season_year uses the first date)")
        print("=" * 78)
        for name, dates in result["cross_year_matches"]:
            print(f"  {name}: {dates}")

    print()
    print("Next: confirm the franchise mapping by hand, then write "
          "etl/overrides/franchises.csv")


if __name__ == "__main__":
    report(scan())
