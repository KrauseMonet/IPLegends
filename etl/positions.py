"""Batting position and bowling usage, derived. SPEC 6.4 and 6.5.

Pure functions over ordered delivery rows, so they can be tested against known
innings without a database.

The A4 special cases - retired hurt, retired out, concussion substitutes, Impact
Player entrants - need no special handling at all. The rule that covers them is
"first appearance wins", because a player who leaves and returns has already
appeared. Writing them as separate branches would be four ways to express one rule
and four things to get wrong.
"""

from __future__ import annotations

from collections import Counter

# SPEC 6.4. Positions are 1-11; the bands partition that range exactly.
BANDS = (
    ("opener", 1, 2),
    ("top_order", 3, 4),
    ("middle", 5, 6),
    ("finisher", 7, 8),
    ("tail", 9, 11),
)

# SPEC 6.5. A bowler whose busiest phase holds at least this share of their legal
# deliveries is described by that phase; below it they are 'mixed'.
PHASE_DOMINANCE = 0.50


def band_for(position: int | None) -> str | None:
    if position is None:
        return None
    for name, low, high in BANDS:
        if low <= position <= high:
            return name
    return None


def batting_order(deliveries) -> dict[str, int]:
    """person_id -> batting position, for a single innings.

    `deliveries` must be in delivery order and hold `.batter_id` and
    `.non_striker_id`. On the first delivery the striker is position 1 and the
    non-striker is position 2, which is the only place the pair's order matters.
    """
    position: dict[str, int] = {}
    for d in deliveries:
        for person_id in (d.batter_id, d.non_striker_id):
            if person_id not in position:
                position[person_id] = len(position) + 1
    return position


def modal_position(positions: list[int]) -> tuple[int | None, int | None, int | None, bool]:
    """(modal, min, max, was_tied) across a franchise-season.

    A tie is broken towards the lower position number: a player who batted 3 and 5
    equally often is described as a number 3, because that is the higher-value slot
    and the one a reader would recognise them by. The tie is returned rather than
    swallowed so the caller can report how often it happens.
    """
    if not positions:
        return None, None, None, False
    counts = Counter(positions)
    best = max(counts.values())
    tied = sorted(p for p, n in counts.items() if n == best)
    return tied[0], min(positions), max(positions), len(tied) > 1


def bowling_usage(phase_counts: dict[str, int]) -> str | None:
    """Which phase a bowler's legal deliveries actually came from.

    None when nothing is classifiable. A delivery has no phase when its innings'
    scheduled length is unknown or it belongs to a super over, and per the null rule
    an unknown must not be resolved into 'mixed' - 'mixed' is a finding about a
    bowler, not a place to put missing data.
    """
    total = sum(phase_counts.values())
    if total == 0:
        return None
    best = max(phase_counts.values())
    leaders = [p for p, n in phase_counts.items() if n == best]
    # Two phases level on the same count is a bowler who split their work, whichever
    # side of the threshold that count falls. Picking one would be a coin toss dressed
    # up as a finding, and with three phases a tie for the lead can only be a 50/50.
    if len(leaders) > 1:
        return "mixed"
    return leaders[0] if best / total >= PHASE_DOMINANCE else "mixed"
