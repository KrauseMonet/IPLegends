"""Career-based batting role, for DRAFT ELIGIBILITY. A76.

Supersedes A72/A73's per-exact-position eligibility (`MIN_INNINGS_AT_POSITION`,
`LOWER_ORDER_BAND`, `Card.career_positions`'s widening rule) with a coarser, single
role per player: `top` (1-3), `middle` (3-5), `finisher` (5-7), or `tail` (8-11).
Deliberately a DIFFERENT classification from SPEC 6.4's five rating-cohort bands in
`etl/positions.py` (opener/top_order/middle/finisher/tail, no overlap, per franchise-
season, used for rating cohort offsets) -- that one answers "how do we normalise this
SEASON's rating", this one answers "which slots may this PLAYER be drafted into", and
the two questions have never had to agree with each other.

The four bands overlap at their shared boundary on purpose: position 3 belongs to both
`top` and `middle`, position 5 to both `middle` and `finisher`, matching ordinary
cricket usage where a #3 is as often grouped with the top three as with the middle
order, and a #5 sits on the finisher/middle line. A player still gets exactly ONE role.

THE ALGORITHM WAS NOT "find the single most-frequent exact position, then check a
secondary position only at the boundary" -- that was tried first and is wrong. Rohit
Sharma's single most common EXACT position across his career is 4 (86 innings) even
though he is a career-defining opener, because his top-order innings are split across
two positions (1: 74, 2: 52) while his middle-order innings land on one. Comparing
AGGREGATE BAND totals directly avoids the trap: 167 top-band innings against 149
middle-band innings decide him correctly, and no exact-position modal step is needed at
all. `band_totals` implements the aggregate comparison; `dominant_band` picks the
largest total.

THE FOUR-TIER CASCADE (`batting_role`) answers two questions raised against the first
draft of this idea, together rather than separately:

  - "A domestic batter who was rested/injured one season, and defaults to tailender for
    it" -- fixed by trying the season's own evidence first, then the player's CAREER
    evidence, before ever reaching a fixed default.
  - "A domestic player in the squad who never played enough to know his role at all" --
    split into two genuinely different populations. If `squad_role == 'bowler'`, he
    doesn't get a batting rating at all (A26: `role_for` already means "doesn't clear
    the batting-workload bar"), and tailender is corroborated evidence, not a guess --
    measured, 5.7% of bowlers never bat at all. If `squad_role` is `batter`/`allrounder`/
    `keeper`, he can never have zero career batting innings (clearing A26's ball-volume
    bar requires having batted at least once), so there is always SOME signal below the
    innings floor -- measured, of players tagged `batter` with under 5 career innings,
    85-92% of their actual innings are OUTSIDE the tail band. Discarding that in favour
    of a flat default would misclassify most of them.

No hand-maintained list of "outlier" names (e.g. Narine) is needed for the season-vs-
career behaviour either -- every player is tried per-season first, by default, and it is
simply a fact about the archive that most players' seasons agree with their career and
Narine's do not (he opened for entire seasons, 2017-2020 and 2024-2025, and batted 9-11
for entire OTHER seasons, 2012-2016) -- see `tests/test_batting_roles.py` for his season
sequence worked through in full, including the three earliest seasons too thin even for
him, where the cascade's own honest limit is what shows up instead of a wrong answer.
"""

from __future__ import annotations

# Positions with no boundary ambiguity map to exactly one band.
UNAMBIGUOUS_BAND = {
    1: "top", 2: "top",
    4: "middle",
    6: "finisher", 7: "finisher",
    8: "tail", 9: "tail", 10: "tail", 11: "tail",
}
# 3 is the top/middle boundary; 5 is the middle/finisher boundary. Resolved in
# `band_totals`, each independently of the other's evidence.
OVERLAP_POSITIONS = (3, 5)

# Ties -- both in resolving an overlap position and in picking the final dominant band
# -- favour the earlier band here, one consistent direction throughout.
BAND_ORDER = ("top", "middle", "finisher", "tail")

# Minimum total batting innings, at a season OR a career, before that grain's own
# aggregate is trusted rather than treated as too thin to call. Same value A72 used for
# a single exact position; a band aggregates up to four positions, so this many innings
# spread across a band is at least as much evidence as this many at one position was.
MIN_INNINGS_FOR_ROLE = 5


def band_totals(counts: dict[int, int]) -> dict[str, int]:
    """Final innings total for each of the four bands, the two overlap positions
    resolved by comparing the UNAMBIGUOUS weight of their two neighbouring bands.

    Both comparisons are made against the frozen UNAMBIGUOUS baseline, never against a
    total already carrying the other overlap position's allocation -- position 3's
    allocation must not change what position 5 sees, or the two boundaries stop being
    resolved independently. (Caught by `CALIBRATION`: KD Karthik's 30 innings at 3 would
    otherwise inflate `middle` enough to also win position 5's comparison, reading him
    as `middle` instead of `finisher`.)

    Ties (including "no other evidence at all", both baselines zero) favour the earlier
    band: position 3 alone resolves to `top`, position 5 alone resolves to `middle`.
    That is not a separate rule bolted on for the boundary -- it falls straight out of
    BAND_ORDER's single tie-break direction applied consistently.
    """
    unambiguous = {band: 0 for band in BAND_ORDER}
    for position, innings in counts.items():
        band = UNAMBIGUOUS_BAND.get(position)
        if band is not None:
            unambiguous[band] += innings

    totals = dict(unambiguous)

    pos3 = counts.get(3, 0)
    if pos3:
        totals["top" if unambiguous["top"] >= unambiguous["middle"] else "middle"] += pos3

    pos5 = counts.get(5, 0)
    if pos5:
        totals["middle" if unambiguous["middle"] >= unambiguous["finisher"] else "finisher"] += pos5

    return totals


def dominant_band(counts: dict[int, int]) -> str | None:
    """The band with the most innings once positions 3 and 5 are resolved.

    None only when `counts` is empty -- no batting evidence at all -- which is the one
    case `band_totals` cannot itself distinguish from "every band happens to total
    zero" (impossible in practice, but this keeps the two cases explicit rather than
    relying on that impossibility).
    """
    if not counts:
        return None
    totals = band_totals(counts)
    best = max(totals.values())
    return next(band for band in BAND_ORDER if totals[band] == best)


def batting_role(
    season_counts: dict[int, int],
    career_counts: dict[int, int],
    squad_role: str | None,
    *,
    min_innings: int = MIN_INNINGS_FOR_ROLE,
) -> str:
    """The four-tier cascade. Always returns one of the four bands, never None -- see
    the module docstring for what each tier is protecting against and why."""
    if sum(season_counts.values()) >= min_innings:
        return dominant_band(season_counts)
    if sum(career_counts.values()) >= min_innings:
        return dominant_band(career_counts)
    if squad_role != "bowler":
        band = dominant_band(career_counts)
        if band is not None:
            return band
    return "tail"


# (name, career position -> innings, expected role). Real counts measured against the
# archive on 2026-08-03, chosen for careers with no serious public dispute about where
# they bat. Spans all four bands; `tests/test_batting_roles.py` parametrizes over this
# exactly as `tests/test_squads.py` does for `etl.roles.CALIBRATION`.
CALIBRATION = (
    ("RG Sharma", {1: 74, 2: 52, 3: 41, 4: 86, 5: 22, 6: 1}, "top"),
    ("V Sehwag", {1: 17, 2: 81, 3: 3, 4: 1, 5: 2}, "top"),
    ("S Dhawan", {1: 37, 2: 165, 3: 12, 4: 3, 5: 2, 7: 2}, "top"),
    ("DA Warner", {1: 115, 2: 48, 3: 6, 4: 14, 5: 1}, "top"),
    ("Q de Kock", {1: 72, 2: 46}, "top"),
    ("SR Tendulkar", {1: 4, 2: 70, 4: 3, 5: 1}, "top"),
    ("SV Samson", {1: 27, 2: 18, 3: 94, 4: 36, 5: 3, 6: 6, 7: 1, 8: 1}, "top"),
    ("AT Rayudu", {1: 2, 2: 13, 3: 43, 4: 47, 5: 47, 6: 31, 7: 3, 8: 1}, "middle"),
    ("MK Pandey", {1: 16, 2: 12, 3: 74, 4: 37, 5: 14, 6: 2, 7: 7, 8: 2}, "middle"),
    ("MS Dhoni", {3: 8, 4: 66, 5: 74, 6: 52, 7: 25, 8: 14, 9: 3}, "finisher"),
    ("HH Pandya", {3: 16, 4: 29, 5: 34, 6: 41, 7: 26, 8: 4}, "finisher"),
    ("KA Pollard", {3: 3, 4: 19, 5: 66, 6: 61, 7: 20, 8: 2}, "finisher"),
    ("AD Russell", {3: 5, 4: 13, 5: 21, 6: 37, 7: 31, 8: 8}, "finisher"),
    ("DJ Bravo", {1: 1, 2: 2, 3: 1, 4: 10, 5: 25, 6: 31, 7: 29, 8: 11, 9: 3}, "finisher"),
    ("KD Karthik", {3: 30, 4: 66, 5: 68, 6: 45, 7: 23, 8: 2}, "finisher"),
    ("RK Singh", {3: 1, 4: 3, 5: 18, 6: 28, 7: 9, 8: 3}, "finisher"),
    ("JJ Bumrah", {9: 3, 10: 18, 11: 12}, "tail"),
    ("SL Malinga", {9: 14, 10: 6, 11: 5}, "tail"),
    ("Rashid Khan", {5: 2, 6: 2, 7: 17, 8: 52, 9: 1}, "tail"),
    ("R Ashwin", {2: 2, 3: 4, 4: 3, 5: 7, 6: 8, 7: 19, 8: 40, 9: 11, 10: 4}, "tail"),
    ("YS Chahal", {9: 1, 10: 5, 11: 14}, "tail"),
    ("Mohammed Shami", {8: 2, 9: 20, 10: 11, 11: 3}, "tail"),
)
