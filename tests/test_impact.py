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
from etl.state_model import MIN_OBSERVATIONS


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


def _full_grid(runs_per_ball: float, out_rate: float, thin: tuple[int, str] | None = None):
    """Every over and bucket, uniform -- the smallest grid the remaining-runs chain accepts.
    `Cell(balls, runs, outs, outs_any)`: the chain reads `outs_any`, every wicket."""
    n = 10_000
    cells = {(o, b): Cell(n, round(runs_per_ball * n), 0, round(out_rate * n))
             for o in range(20) for b in ("0-1", "2-3", "4-5", "6+")}
    if thin:
        cells[thin] = Cell(1, 6, 0, 1)
    return Grid(cells)


def test_with_no_dismissals_only_the_last_wicket_costs_anything():
    """[A160] The chain's arithmetic, pinned where it has a closed form. If nobody can get
    out, losing a wicket changes nothing about what is still to come -- EXCEPT the tenth,
    which ends the innings and forfeits every remaining ball. Exactly the runs left."""
    r = 1.3
    costs = Costs(_full_grid(r, 0.0))
    for over in (0, 7, 19):
        for w in range(9):
            assert costs.of(over, w) == pytest.approx(0.0, abs=1e-12)
        # balls t+1 for the six balls of the over are 6o+1 .. 6o+6; runs left = r*(120-(t+1))
        left = sum(r * (120 - (6 * over + b + 1)) for b in range(6)) / 6
        assert costs.of(over, 9) == pytest.approx(left)


def test_a_wicket_costs_more_early_than_late_and_never_less_than_nothing():
    costs = Costs(_full_grid(1.3, 0.05))
    assert all(c >= 0 for c in costs.priced.values())
    assert costs.of(0, 0) > costs.of(10, 0) > costs.of(19, 0)
    assert len(costs.priced) == 200, "every (over, exact wickets) state is priced"


def test_the_price_reads_the_same_resolved_cell_the_runs_half_does_and_counts_nothing():
    """[A37, carried into A160] Both halves of a ball are priced against one state. The price
    is now BUILT from the grid, through the same walk, so a thin cell's dismissal rate is
    its trustworthy neighbour's -- and building the table is not balls landing in thin
    states, so it must leave the fallback count untouched."""
    g = _full_grid(1.3, 0.05, thin=(7, "2-3"))
    costs = Costs(g)
    assert not g.fallbacks, "building the price table counted itself as fallbacks"
    # The thin cell scores 6 a ball and is out every ball; if the chain read it raw, the
    # cost of a wicket at 1 down in over 7 (which moves INTO the thin bucket) would jump.
    uniform = Costs(_full_grid(1.3, 0.05))
    assert costs.of(7, 1) == pytest.approx(uniform.of(7, 1))


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


# --- [A162] the card's wicket price reads EXACT wicket counts, not the grid's pairs ------

from etl.impact import EXACT_SHRINK_BALLS, _rates  # noqa: E402


def test_without_exact_rates_the_price_is_the_grids_as_before():
    """The engine builds `Costs(grid)` and must get the pair-grained chain unchanged."""
    g = _full_grid(1.3, 0.05)
    assert Costs(g).priced == Costs(g, None).priced


def test_a_wicket_inside_a_pair_is_no_longer_free():
    """The A160 zig-zag: on the grid, 0 and 1 down are one state, so losing the first
    wicket changed nothing about the balls that follow. Exact data saying a side scores
    less at 1 down than at 0 must make that wicket cost something."""
    g = _full_grid(1.3, 0.05)
    n = 100_000
    exact = {(o, 0): (n, int(1.4 * n), int(0.05 * n)) for o in range(20)}
    exact.update({(o, 1): (n, int(1.0 * n), int(0.05 * n)) for o in range(20)})
    assert Costs(g, exact).of(5, 0) > Costs(g).of(5, 0) + 1.0


def test_a_thin_exact_state_stays_close_to_its_pair():
    """Five balls are not evidence about a state; the pair's rates carry it."""
    g = _full_grid(1.3, 0.05)
    runs, out = _rates(g, {(5, 3): (5, 60, 5)}, 5, 3)
    assert abs(runs - 1.3) < 0.3 and abs(out - 0.05) < 0.03
    assert EXACT_SHRINK_BALLS > 0
