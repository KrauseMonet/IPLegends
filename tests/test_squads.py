"""SPEC 6.4, 6.5 and 6.6, pinned to published scorecards where one exists.

`batting_order` is checked against real innings rather than a fixture, for the same
reason the parser tests are: a fixture I write proves only that the code agrees with
me. The two innings below have public scorecards, so the order is falsifiable.

`role_for` is checked against the calibration table, which is the only part of the
role rule that is not a matter of taste. If a threshold moves far enough to misclassify
a player nobody would argue about, the table says so.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from etl.db import data_dir
from etl.parse_match import parse_match
from etl.positions import (
    BANDS,
    PHASE_DOMINANCE,
    band_for,
    batting_order,
    bowling_usage,
    modal_position,
)
from etl.roles import BOWL_MIN, CALIBRATION, role_for

ARCHIVE = data_dir() / "ipl_json.zip"

archive_only = pytest.mark.skipif(
    not ARCHIVE.exists(),
    reason="ipl_json.zip absent; run: uv run python -m etl.download",
)

# (match_id, published order of the first innings)
PUBLISHED_ORDERS = (
    (
        "335982",
        ["SC Ganguly", "BB McCullum", "RT Ponting", "DJ Hussey", "Mohammad Hafeez"],
    ),
    (
        "598027",
        ["CH Gayle", "TM Dilshan", "V Kohli", "AB de Villiers", "SS Tiwary", "R Rampaul"],
    ),
)


@archive_only
@pytest.mark.parametrize("match_id,published", PUBLISHED_ORDERS)
def test_batting_order_matches_the_published_scorecard(match_id, published):
    with zipfile.ZipFile(ARCHIVE) as zf:
        raw = json.loads(zf.read(f"{match_id}.json"))
    match = parse_match(raw, match_id)
    names = {v: k for k, v in raw["info"]["registry"]["people"].items()}

    innings = [d for d in match.deliveries if d.innings_no == 1 and not d.is_super_over]
    order = batting_order(innings)
    assert [names[pid] for pid, _ in sorted(order.items(), key=lambda kv: kv[1])] == (
        published
    )


@archive_only
def test_a_batter_arriving_as_non_striker_still_gets_a_position():
    """A4. McClenaghan batted in this innings without ever facing a delivery.

    Scanning `batter` alone drops him from the order entirely, so the last batting
    position is handed to the wrong player and the tail is one short.
    """
    with zipfile.ZipFile(ARCHIVE) as zf:
        raw = json.loads(zf.read("1082592.json"))
    match = parse_match(raw, "1082592")
    names = {v: k for k, v in raw["info"]["registry"]["people"].items()}

    innings = [d for d in match.deliveries if d.innings_no == 1]
    both = batting_order(innings)
    faced = {d.batter_id for d in innings}

    missed = {names[pid] for pid in set(both) - faced}
    assert missed == {"MJ McClenaghan"}
    assert both[raw["info"]["registry"]["people"]["MJ McClenaghan"]] == len(both)


def test_bands_partition_positions_one_to_eleven_exactly():
    covered = [p for _, low, high in BANDS for p in range(low, high + 1)]
    assert sorted(covered) == list(range(1, 12))
    assert len(covered) == len(set(covered)), "a position falls in two bands"
    assert band_for(None) is None
    assert band_for(12) is None


def test_modal_position_breaks_a_tie_towards_the_lower_number():
    modal, low, high, tied = modal_position([3, 3, 5, 5])
    assert (modal, low, high, tied) == (3, 3, 5, True)

    modal, low, high, tied = modal_position([4, 4, 4, 7])
    assert (modal, low, high, tied) == (4, 4, 7, False)

    assert modal_position([]) == (None, None, None, False)


def test_bowling_usage_needs_dominance_before_it_names_a_phase():
    assert bowling_usage({}) is None
    assert bowling_usage({"death": 30, "middle": 10}) == "death"
    # Exactly at the threshold counts as dominant, but only for a lone leader.
    assert bowling_usage({"death": 50, "middle": 50}) == "mixed"
    assert bowling_usage({"death": 49, "middle": 51, "powerplay": 0}) == "middle"
    assert bowling_usage({"death": 60, "middle": 40, "powerplay": 20}) == "death"
    assert bowling_usage({"death": 4, "middle": 3, "powerplay": 3}) == "mixed"
    assert PHASE_DOMINANCE == 0.50


@pytest.mark.parametrize("name,faced,bowled,expected", CALIBRATION)
def test_role_thresholds_classify_the_undisputed_players(name, faced, bowled, expected):
    matches = 100
    assert role_for(
        round(faced * matches), round(bowled * matches), matches
    ) == expected, name


def test_the_calibration_table_can_actually_disagree():
    """Twelve players who all came out the same way would prove nothing."""
    assert {expected for _, _, _, expected in CALIBRATION} == {
        "allrounder",
        "batter",
        "bowler",
    }


def test_no_role_when_the_player_neither_batted_nor_bowled():
    assert role_for(0, 0, 5) is None
    assert role_for(0, 0, 0) is None
    assert role_for(100, 0, 0) is None, "no matches means no per-match rate"


def test_keeper_overrides_the_workload_rule():
    assert role_for(0, 2400, 100, is_keeper=True) == "keeper"


def test_below_both_thresholds_the_larger_relative_workload_wins():
    matches = 100
    # Half of BAT_MIN, nothing bowled: a tail-end batter, not a bowler.
    assert role_for(450, 0, matches) == "batter"
    # Two overs across the season and never a bat: a bowler who did not bat.
    assert role_for(0, 12, matches) == "bowler"
    # Both at the same fraction of their own threshold falls to batter, and the
    # asymmetry is not smuggled back in by comparing raw ball counts.
    assert role_for(450, round(BOWL_MIN * matches / 2), matches) == "batter"
