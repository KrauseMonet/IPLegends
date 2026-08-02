"""A72/A73. The legality algebra for a final twelve: eleven numbered batting positions
plus one Impact Player, drafted straight into place one pick at a time.

A73 replaced the old "draft fifteen, then find some legal arrangement" model with an
atomic one: every pick commits to exactly one open slot the moment it is made, so squad
size and slot count are always the same number. That is what makes `could_still_complete`
a pair of integer inequalities rather than a bipartite-matching search -- with as many
future picks as future open slots, and a hypothetical future pick assumed eligible
everywhere, position-matching can never be the failure mode; only the keeper and bowler
counts can still be unreachable. This file pins that reasoning directly rather than
cross-checking it against a slower solver, since there is no longer a second solver to
cross-check against.
"""

from __future__ import annotations

import pytest

from etl.feasibility import (
    BOWLERS_IN_TWELVE, LOWER_ORDER_BAND, OVERSEAS_CAP, TWELVE_SIZE, XI_SIZE,
    Card, could_still_complete, eligible, order_errors,
)
from game.simulator import MAX_OVERS_PER_BOWLER, OVERS


def card(i: int, positions: frozenset[int], *, role: str = "batter",
         bowl: float | None = None, overseas: bool | None = False) -> Card:
    return Card(
        fs_id=1, person_id=f"c{i}", name=f"c{i}", bat=0.5, bowl=bowl,
        role=role, overseas=overseas, career_positions=positions,
    )


# --- the shared constant ----------------------------------------------------------------

def test_bowlers_in_twelve_matches_the_five_bowler_cap():
    """A50: twenty overs at a maximum of four each is exactly five. Re-declared in
    etl.feasibility because etl must not import game; this is what keeps them from
    drifting apart silently."""
    assert BOWLERS_IN_TWELVE == OVERS // MAX_OVERS_PER_BOWLER


# --- Card.positions: the widening rule, applied in exactly one place --------------------

def test_genuine_top_or_middle_evidence_is_kept_exactly():
    """KL Rahul-shaped: real evidence above the lower-order band stays untouched -- the
    widening rule never loosens where the evidence itself says otherwise."""
    c = card(0, frozenset({1, 2, 3, 4}))
    assert c.positions == frozenset({1, 2, 3, 4})


def test_evidence_confined_to_the_lower_order_widens_to_the_whole_band():
    """SP Narine-shaped: a finisher whose only real innings are at 7/8 is, in practice,
    fungible across the whole lower order -- not pinned to exactly 7 and 8."""
    c = card(0, frozenset({7, 8}))
    lo, hi = LOWER_ORDER_BAND
    assert c.positions == frozenset(range(lo, hi + 1))


def test_no_qualifying_position_at_all_also_widens_to_the_whole_band():
    """Whether from zero batting evidence or evidence too thin to concentrate anywhere --
    both read the same to a drafter, so both get the same widened band as a specialist
    bowler who has batted just enough to qualify down at 8."""
    c = card(0, frozenset())
    lo, hi = LOWER_ORDER_BAND
    assert c.positions == frozenset(range(lo, hi + 1))


def test_a_single_qualifying_position_at_the_edge_of_the_band_is_not_widened():
    """A player who qualifies at position 6 alone has genuine evidence just above the
    band (LOWER_ORDER_BAND starts at 7) -- his exact single position is kept, not widened."""
    c = card(0, frozenset({6}))
    assert c.positions == frozenset({6})


def test_slots_always_include_the_impact_pseudo_position():
    c = card(0, frozenset({1}))
    assert (XI_SIZE + 1) in c.slots
    assert c.slots == c.positions | {XI_SIZE + 1}


# --- order_errors: every way a drafter's own arrangement can be illegal ------------------

def _legal_squad() -> list[Card]:
    """Twelve cards that field an obviously legal twelve: a keeper, five distinct
    bowlers, and enough spread across positions 1-11."""
    squad = [card(0, frozenset({1}), role="keeper", bowl=None)]
    for pos in range(2, 12):
        squad.append(card(pos, frozenset({pos}), bowl=0.1 if pos <= 6 else None))
    squad.append(card(20, frozenset({11}), bowl=0.1))  # the Impact Player
    return squad


def test_a_legal_order_has_no_errors():
    """`squad[i]` is built to correspond to position `i + 1` by construction (see
    `_legal_squad`) -- direct index assignment, not `min(c.positions)`, since a
    lower-order card's widened positions no longer pin down which exact slot he was
    built for."""
    squad = _legal_squad()
    order = list(squad[:XI_SIZE])
    impact = squad[XI_SIZE]
    assert order_errors(order, impact, squad) == []


def test_placing_a_player_outside_his_range_is_named():
    squad = _legal_squad()
    order = [None] * XI_SIZE
    lower_order_only = card(99, frozenset({9}))  # widens to the whole band, never to 1
    order[0] = lower_order_only
    errors = order_errors(order, squad[0], squad + [lower_order_only])
    assert any("cannot bat at 1" in e for e in errors)


def test_no_keeper_is_named():
    squad = [card(i, frozenset({i + 1}), bowl=0.1) for i in range(TWELVE_SIZE)]
    order = squad[:XI_SIZE]
    impact = squad[XI_SIZE]
    errors = order_errors(order, impact, squad)
    assert any("wicketkeeper" in e for e in errors)


def test_fewer_than_five_bowling_options_is_named():
    squad = [card(i, frozenset({i + 1}), role="keeper" if i == 0 else "batter")
             for i in range(TWELVE_SIZE)]
    order = squad[:XI_SIZE]
    impact = squad[XI_SIZE]
    errors = order_errors(order, impact, squad)
    assert any("bowling option" in e for e in errors)


def test_too_many_overseas_is_named():
    squad = [card(i, frozenset({i + 1}), bowl=0.1,
                  role="keeper" if i == 0 else "batter",
                  overseas=(i < OVERSEAS_CAP + 1))
             for i in range(TWELVE_SIZE)]
    order = squad[:XI_SIZE]
    impact = squad[XI_SIZE]
    errors = order_errors(order, impact, squad)
    assert any("overseas" in e for e in errors)


# --- could_still_complete: the forward check collapses to two count checks --------------

def test_zero_remaining_needs_the_keeper_and_bowlers_already_in_hand():
    assert could_still_complete(frozenset(), keeper_have=True,
                                 bowl_have=BOWLERS_IN_TWELVE, remaining_after=0)
    assert not could_still_complete(frozenset(), keeper_have=False,
                                     bowl_have=BOWLERS_IN_TWELVE, remaining_after=0)
    assert not could_still_complete(frozenset(), keeper_have=True,
                                     bowl_have=BOWLERS_IN_TWELVE - 1, remaining_after=0)


def test_a_future_pick_can_always_still_supply_the_keeper():
    """With at least one pick left, a hypothetical future wildcard can be the keeper --
    so a missing keeper alone never fails the check while picks remain."""
    assert could_still_complete(frozenset({1}), keeper_have=False,
                                 bowl_have=BOWLERS_IN_TWELVE, remaining_after=1)


def test_bowling_depth_is_the_one_count_that_can_still_run_out():
    """Two picks left, only two more bowling options could possibly arrive: reachable
    exactly on the margin, not reachable one short of it."""
    assert could_still_complete(frozenset({1, 2}), keeper_have=True,
                                 bowl_have=BOWLERS_IN_TWELVE - 2, remaining_after=2)
    assert not could_still_complete(frozenset({1, 2}), keeper_have=True,
                                     bowl_have=BOWLERS_IN_TWELVE - 3, remaining_after=2)


def test_the_invariant_between_remaining_picks_and_open_slots_is_enforced():
    """[A73] Every pick fills exactly one open slot; remaining picks and open slots must
    never drift apart. A caller that breaks this is a bug, not a legality question, so
    it is asserted rather than silently tolerated."""
    with pytest.raises(AssertionError):
        could_still_complete(frozenset({1, 2}), keeper_have=True,
                              bowl_have=BOWLERS_IN_TWELVE, remaining_after=1)


# --- eligible(): the direct analogue of "a scalar keeper weight is wrong" ---------------

def test_a_non_keeper_is_excluded_on_the_last_pick_with_no_keeper_yet():
    """The old brute-force-vs-scalar-weight counter-example's point, made directly: a
    high-rated non-keeper cannot be the answer to the very last pick if no keeper has
    been taken, no matter how good his rating -- there is no future pick left to supply
    one instead."""
    open_slots = frozenset({11})
    cards = [card(0, frozenset({11}), role="batter", bowl=0.1)]
    offered = list(eligible(cards, taken=set(), open_slots=open_slots,
                             keeper_have=False, bowl_have=BOWLERS_IN_TWELVE,
                             overseas_taken=0, remaining=1))
    assert offered == []


def test_a_keeper_is_offered_for_the_same_last_pick():
    open_slots = frozenset({11})
    cards = [card(0, frozenset({11}), role="keeper", bowl=None)]
    offered = list(eligible(cards, taken=set(), open_slots=open_slots,
                             keeper_have=False, bowl_have=BOWLERS_IN_TWELVE,
                             overseas_taken=0, remaining=1))
    assert len(offered) == 1


def test_a_card_with_nowhere_open_to_bat_is_never_offered():
    open_slots = frozenset({1, 2})
    cards = [card(0, frozenset({9, 10, 11}), bowl=0.1)]  # eligible nowhere still open
    offered = list(eligible(cards, taken=set(), open_slots=open_slots,
                             keeper_have=True, bowl_have=BOWLERS_IN_TWELVE,
                             overseas_taken=0, remaining=2))
    assert offered == []
