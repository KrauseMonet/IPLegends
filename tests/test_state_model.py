"""SPEC 7.1. The state model's grain, and the guard on its outcome columns.

The seven outcome columns are a measurement of this archive, not a law of cricket: an 8
off an overthrow is legal and has nowhere to go. Cricsheet is contributor-driven and
recent seasons get revised, so the day that measurement stops holding is a download, not
a rewrite. These tests pin the two things that must survive it - the loader refuses to
drop an outcome it cannot store, and every one of the 80 states stays legible.
"""

from __future__ import annotations

import pytest

from etl.state_model import (
    BUCKETS,
    OFF_THE_BAT,
    Cell,
    bucket_of,
    every_cell,
    unexpected_outcomes,
    write,
)


def cell(over=0, bucket="0-1", faced=6, outcomes=None):
    dist = outcomes if outcomes is not None else {1: faced}
    return Cell(over, bucket, faced, sum(r * n for r, n in dist.items()), 0, dist)


class RefusesToBeUsed:
    """A connection that fails the test if the loader touches it."""

    def cursor(self, *a, **k):
        raise AssertionError("the guard let an unstorable outcome through to the database")


def test_an_outcome_with_no_column_stops_the_load():
    """The whole point. A 7 or 8 off the bat must not be silently dropped."""
    with pytest.raises(SystemExit) as raised:
        write(RefusesToBeUsed(), [cell(outcomes={1: 5, 8: 1})], {})
    assert "8 runs x1" in str(raised.value)


def test_the_refusal_names_every_unstorable_value_not_just_the_first():
    """A reviewer widening OFF_THE_BAT needs the whole list, or they fix it twice."""
    with pytest.raises(SystemExit) as raised:
        write(RefusesToBeUsed(), [cell(outcomes={7: 2}), cell(outcomes={8: 1})], {})
    message = str(raised.value)
    assert "7 runs x2" in message and "8 runs x1" in message


def test_unexpected_outcomes_pools_across_cells():
    assert unexpected_outcomes([cell(outcomes={8: 1}), cell(outcomes={8: 2, 4: 9})]) \
        == {8: 3}


def test_nothing_is_unexpected_when_every_outcome_has_a_column():
    assert unexpected_outcomes([cell(outcomes={r: 1 for r in OFF_THE_BAT})]) == {}


def test_all_eighty_states_are_present_even_when_the_archive_has_75():
    """An absent row and a zero row say different things to the simulator: 'not covered'
    and 'never seen'. Only one is true, so the table has to say which."""
    rows = every_cell([cell(over=5, bucket="2-3", faced=120)])
    assert len(rows) == 20 * len(BUCKETS) == 80
    assert len({(r.over_no, r.bucket) for r in rows}) == 80


def test_a_never_observed_state_arrives_as_zero_rather_than_absent():
    rows = {(r.over_no, r.bucket): r for r in every_cell([])}
    unseen = rows[(0, "6+")]          # nobody has been 6 down in the first over
    assert unseen.faced == 0 and unseen.dismissals == 0
    assert unseen.outcomes == {r: 0 for r in OFF_THE_BAT}


def test_filling_the_gaps_does_not_disturb_an_observed_cell():
    observed = cell(over=12, bucket="4-5", faced=999, outcomes={4: 999})
    rows = {(r.over_no, r.bucket): r for r in every_cell([observed])}
    assert rows[(12, "4-5")] is observed


def test_a_zero_ball_cell_reports_no_rate_rather_than_dividing_by_zero():
    empty = every_cell([])[0]
    assert empty.runs_per_ball == 0.0 and empty.dismissal_rate == 0.0


@pytest.mark.parametrize("wickets,expected", [
    (0, "0-1"), (1, "0-1"), (2, "2-3"), (3, "2-3"),
    (4, "4-5"), (5, "4-5"), (6, "6+"), (9, "6+"),
])
def test_wickets_land_in_the_bucket_a5_specifies(wickets, expected):
    assert bucket_of(wickets) == expected
