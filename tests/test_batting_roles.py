"""etl/batting_roles.py -- A76's career-based batting role for draft eligibility.

`dominant_band` is checked against `CALIBRATION`, the same shape `tests/test_squads.py`
uses for `etl.roles.CALIBRATION`. `batting_role`'s four-tier cascade gets one targeted
case per tier, each verified to fail if the tier it exercises is skipped or short-
circuited wrong -- the project's standing rule that a rule living in application code,
not a table CHECK, is unprotected without a test that actually exercises it.
"""

from __future__ import annotations

import pytest

from etl.batting_roles import (
    BAND_ORDER, CALIBRATION, MIN_INNINGS_FOR_ROLE, band_totals, batting_role,
    dominant_band,
)


@pytest.mark.parametrize("name,counts,expected", CALIBRATION)
def test_calibration_table_classifies_correctly(name, counts, expected):
    assert dominant_band(counts) == expected, name


def test_calibration_spans_all_four_bands():
    assert {expected for _, _, expected in CALIBRATION} == set(BAND_ORDER)


def test_no_evidence_at_all_has_no_dominant_band():
    assert dominant_band({}) is None


# --- the overlap positions (3 and 5), and the bug a naive implementation actually had ----

def test_position_3_alone_resolves_to_top_not_middle():
    """No other evidence at all -- both neighbours are at zero, so the tie breaks
    toward the earlier band. Not a special case bolted on for this: it falls straight
    out of the same tie-break `band_totals` uses everywhere."""
    assert dominant_band({3: 10}) == "top"


def test_position_5_alone_resolves_to_middle_not_finisher():
    assert dominant_band({5: 10}) == "middle"


def test_position_3_follows_the_stronger_unambiguous_neighbour():
    # Rayudu-shaped: heavier middle (position 4) evidence pulls position 3 with it.
    assert dominant_band({1: 2, 4: 10, 3: 3}) == "middle"
    # Warner-shaped: heavier top (1/2) evidence keeps position 3 with the top order.
    assert dominant_band({1: 20, 4: 2, 3: 3}) == "top"


def test_the_two_overlap_positions_are_resolved_independently():
    """KD Karthik-shaped -- the actual bug found while building the calibration table.
    Allocating position 3 to `middle` must not inflate `middle` enough to also win
    position 5's comparison against `finisher`. Both decisions must use the frozen,
    unambiguous baseline, never a total already carrying the other boundary position's
    vote, or a heavy top/middle career reads as heavier than it is at the middle/
    finisher boundary too."""
    counts = {3: 30, 4: 66, 5: 68, 6: 45, 7: 23}
    # unambiguous: middle=66, finisher=68 -- finisher already leads before either
    # boundary position is allocated, and must still lead after both are.
    assert dominant_band(counts) == "finisher"
    totals = band_totals(counts)
    assert totals["finisher"] > totals["middle"]


def test_a_genuine_tie_in_the_final_totals_breaks_toward_the_earlier_band():
    assert dominant_band({1: 5, 4: 5}) == "top"
    assert dominant_band({4: 5, 6: 5}) == "middle"
    assert dominant_band({6: 5, 9: 5}) == "finisher"


# --- the four-tier cascade ----------------------------------------------------------------

def test_tier_1_a_season_with_enough_of_its_own_evidence_uses_it_even_against_career():
    season = {1: MIN_INNINGS_FOR_ROLE, 2: MIN_INNINGS_FOR_ROLE}   # top, this season
    career = {9: 50, 10: 50, 11: 50}                              # tail, overall
    assert batting_role(season, career, "allrounder") == "top"


def test_tier_2_a_thin_season_falls_back_to_a_clear_career_identity():
    """The domestic-batter case this tier exists for: a quiet/rested/short season must
    not demote a consistently middle-order career batter to tailender."""
    season = {4: 2}                              # below the floor alone
    career = {3: 20, 4: 40, 5: 20}                # clearly middle, at large volume
    assert sum(season.values()) < MIN_INNINGS_FOR_ROLE
    assert batting_role(season, career, "batter") == "middle"


def test_tier_3_a_short_career_batter_uses_his_thin_signal_rather_than_tailender():
    """Measured against the archive: role=batter players below the innings floor still
    have real evidence, and most of it is outside the tail (85-92% in the measured
    population). A short career must not be read as no career."""
    season = career = {1: 2, 2: 1}                # 3 innings total, all top-order
    assert sum(career.values()) < MIN_INNINGS_FOR_ROLE
    assert batting_role(season, career, "batter") == "top"


def test_tier_3_never_rescues_a_bowler():
    """The one population this tier must NOT rescue: a bowler with a token handful of
    tail innings is not promoted past tail just because he faced a couple of balls --
    A26's `role_for` already told us his batting workload doesn't clear the bar."""
    season = career = {8: 2}
    assert batting_role(season, career, "bowler") == "tail"


def test_tier_4_a_bowler_with_no_batting_evidence_at_all_is_tail():
    assert batting_role({}, {}, "bowler") == "tail"


def test_tier_4_resolves_deterministically_even_for_an_unreachable_role():
    """A non-bowler role with truly zero evidence anywhere cannot occur via
    `etl.feasibility.load_deck` -- A26's `role_for` requires having batted at least once
    to be tagged batter/allrounder/keeper -- but `batting_role` itself must still
    resolve rather than crash if it is ever asked."""
    assert batting_role({}, {}, "batter") == "tail"
    assert batting_role({}, {}, None) == "tail"


# --- SP Narine: the case the whole cascade exists for -------------------------------------

def test_narines_actual_season_sequence_resolves_the_way_the_archive_says_he_played():
    """Real counts, measured against the archive on 2026-08-03 (`person_batting_positions`
    / `person_season_batting_positions`). Career-wide he reads `top` (68 top-band innings
    against 36 tail), which would be the wrong answer for every one of his genuine tail
    seasons below if the cascade did not try each season's own evidence first."""
    career = {1: 15, 2: 52, 3: 1, 4: 8, 5: 7, 6: 1, 7: 6, 8: 14, 9: 11, 10: 8, 11: 3}
    assert dominant_band(career) == "top"

    tail_seasons = {
        2013: {8: 1, 10: 4, 11: 1},
        2014: {9: 3, 10: 1, 11: 1},
        2022: {2: 2, 4: 1, 5: 2, 7: 1, 8: 3, 9: 1},
        2023: {2: 1, 4: 1, 7: 3, 8: 3, 9: 2},
    }
    for year, season in tail_seasons.items():
        assert sum(season.values()) >= MIN_INNINGS_FOR_ROLE, year
        assert batting_role(season, career, "bowler") == "tail", year

    opener_seasons = {
        2017: {1: 10, 2: 2, 8: 2},
        2024: {2: 14},
        2025: {2: 12},
    }
    for year, season in opener_seasons.items():
        assert sum(season.values()) >= MIN_INNINGS_FOR_ROLE, year
        assert batting_role(season, career, "allrounder") == "top", year

    # His three thinnest, earliest seasons (2012: 2 innings, 2015: 1, 2016: 4) never
    # clear the season floor and fall through to the career answer -- the cascade's
    # one named, disclosed limitation (see the module docstring), not glossed over here.
    thinnest_seasons = ({9: 1, 10: 1}, {11: 1}, {9: 2, 10: 2})
    for season in thinnest_seasons:
        assert sum(season.values()) < MIN_INNINGS_FOR_ROLE
        assert batting_role(season, career, "bowler") == "top"
