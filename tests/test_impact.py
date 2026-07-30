"""SPEC 7.2-7.3. Pure tests for the rules that live in loader behaviour.

A rule enforced by a CHECK is protected by the database. A rule enforced by the loader is
protected by nothing unless a test points at it - which is the whole reason A37 needs one:
the shared state-resolution rule is behaviour, has no schema representation, and would
become invisible the day somebody changed the walk.
"""

from __future__ import annotations

import pytest

from etl.impact import (
    BAT_FLOOR_BALLS,
    BOWL_FLOOR_BALLS,
    DRAFT_GATE_MATCHES,
    Cell,
    Costs,
    Grid,
    gate_reason,
    shrink,
)
from etl.state_model import MIN_OBSERVATIONS, Remaining


def grid(**cells) -> Grid:
    """`over_bucket` keyword to ball count, e.g. o4_01=5000."""
    built = {}
    for key, balls in cells.items():
        over, bucket = key.split("_")
        label = {"01": "0-1", "23": "2-3", "45": "4-5", "6": "6+"}[bucket]
        built[(int(over[1:]), label)] = Cell(balls, balls, 0, 0)
    return Grid(built)


# --- A37: one resolution rule, and it never drops a ball -----------------------------

def test_a_thin_cell_resolves_to_the_nearest_trustworthy_bucket_in_the_same_over():
    g = grid(o4_01=MIN_OBSERVATIONS, o4_23=MIN_OBSERVATIONS - 1, o4_45=MIN_OBSERVATIONS)
    # '2-3' is thin and sits equidistant from two healthy neighbours; the walk is
    # deterministic and picks the lower-indexed one rather than an arbitrary dict order.
    assert g.resolve(4, "2-3") == (4, "0-1")


def test_resolution_never_leaves_the_over():
    """A fat cell in a neighbouring over must not attract the walk.

    Asserted through `of()` rather than `resolve()` on purpose. `resolve` rebuilds its
    answer as `(over, bucket)` and so reports the right over even if the walk searched the
    wrong ones - the assertion has to be that the cell actually CAME BACK, which only
    holds if the candidate list was confined to this over.
    """
    g = grid(o4_01=MIN_OBSERVATIONS, o5_23=MIN_OBSERVATIONS * 10)
    assert g.of(4, 8).balls == MIN_OBSERVATIONS


def test_a_trustworthy_cell_is_never_resolved_away():
    g = grid(o4_01=MIN_OBSERVATIONS, o4_23=MIN_OBSERVATIONS)
    assert g.resolve(4, "2-3") == (4, "2-3")
    assert not g.fallbacks, "resolving a healthy cell must not be counted as a fallback"


def test_every_fallback_is_counted():
    g = grid(o4_01=MIN_OBSERVATIONS, o4_23=1)
    for _ in range(3):
        g.of(4, 2)
    assert sum(g.fallbacks.values()) == 3


def test_an_over_with_nothing_to_fall_back_to_fails_loudly_rather_than_dropping_the_ball():
    """The A37 bug was a silent `continue`. Anything unpriceable must stop the run."""
    g = grid(o4_01=1)
    with pytest.raises(SystemExit, match="no state"):
        g.of(4, 0)


def test_both_halves_of_a_ball_resolve_within_the_same_over():
    """The A37 invariant: runs and wicket cost may not be priced against different overs.

    Grids walk at bucketed grain and costs at exact grain (A31), so they can land on
    different wicket counts - but never on a different over, or the two halves of one
    ball are describing two different moments of the innings.
    """
    g = grid(o7_01=MIN_OBSERVATIONS, o7_23=1, o7_45=MIN_OBSERVATIONS)
    expected = {
        (7, 0): Remaining(500, 40, 120),
        (7, 1): Remaining(500, 45, 100),
        (7, 8): Remaining(500, 60, 30),
        (7, 9): Remaining(500, 65, 20),
    }
    costs = Costs(expected)
    for wickets in range(10):
        assert g.resolve(7, "2-3" if 2 <= wickets <= 3 else "0-1")[0] == 7
        costs.of(7, wickets)  # must not raise: every wicket count is priceable in over 7


# --- A33: the gate the loader computes must be the gate the CHECK enforces -----------

@pytest.mark.parametrize(
    "balls, matches, expected",
    [
        (100, 4, None),
        (999, 9, None),
        (99, 4, "balls"),
        (100, 3, "matches"),
        (99, 3, "both"),
        (0, 0, "both"),
    ],
)
def test_gate_reason_mirrors_migration_009s_check(balls, matches, expected):
    """If these two ever disagree the constraint cannot catch it - it only sees the result."""
    assert gate_reason(balls, matches, 100) == expected


def test_the_gate_reason_is_null_exactly_when_both_gates_pass():
    for balls in (0, 99, 100, 500):
        for matches in (0, 3, 4, 20):
            passes = balls >= 100 and matches >= DRAFT_GATE_MATCHES
            assert (gate_reason(balls, matches, 100) is None) == passes


def test_the_two_floors_are_not_the_same_number():
    """A33 measured each discipline separately rather than assuming 100 transfers."""
    assert BAT_FLOOR_BALLS == 100
    assert BOWL_FLOOR_BALLS == 150


# --- A19/A35: the stored inputs must reconstruct the view's rating --------------------

def test_shrinkage_is_recoverable_from_the_columns_migration_009_stores():
    """`player_season_rating` computes (impact_total + k*prior) / (balls + k).

    That is only the same number as `shrink` because impact_total is the SUM. Storing a
    mean instead would silently change the formula's meaning, and the view - being the one
    place k lives - has no way to notice.
    """
    balls, impact_total, prior, k = 313, 304.9, 0.107, 100
    raw = impact_total / balls
    assert shrink(raw, balls, prior, k) == pytest.approx(
        (impact_total + k * prior) / (balls + k)
    )
