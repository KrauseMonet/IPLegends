"""web.flashback -- the pure parts: the eligibility threshold and candidate selection.

No database: both functions under test take plain rows/lists, matching this project's
usual split between DB-fetching (verified live) and pure logic (unit tested here). Every
test was verified to fail against the broken line it guards.
"""

from __future__ import annotations

import random

from web.flashback import CANDIDATE_COUNT, FLASHBACK_MIN_SEASONS, _group_eligible, _pick_candidates


def test_group_eligible_drops_a_franchise_below_the_season_floor():
    rows = [
        ("Kochi Tuskers Kerala", 2011, 1),          # 1 season -- below the floor
        ("Gujarat Lions", 2016, 2), ("Gujarat Lions", 2017, 3),   # 2 seasons -- below
        # Exactly one below the floor (3 of 4) -- the boundary case an off-by-one in
        # either direction would get wrong, unlike the 1- and 2-season rows above.
        ("Pune Warriors", 2011, 8), ("Pune Warriors", 2012, 9), ("Pune Warriors", 2013, 10),
        ("Mumbai Indians", 2008, 4), ("Mumbai Indians", 2009, 5),
        ("Mumbai Indians", 2010, 6), ("Mumbai Indians", 2011, 7),  # 4 seasons -- eligible
    ]
    pool = _group_eligible(rows, min_seasons=4)
    assert "Kochi Tuskers Kerala" not in pool
    assert "Gujarat Lions" not in pool
    assert "Pune Warriors" not in pool
    assert pool["Mumbai Indians"] == [(2008, 4), (2009, 5), (2010, 6), (2011, 7)]


def test_group_eligible_uses_the_declared_constant_by_default():
    assert FLASHBACK_MIN_SEASONS == 4


def test_pick_candidates_always_includes_the_correct_year():
    seasons = [(2008, 1), (2010, 2), (2013, 3), (2018, 4), (2021, 5)]
    rng = random.Random(7)
    candidates = _pick_candidates(rng, seasons, correct_year=2013)
    assert 2013 in candidates


def test_pick_candidates_returns_the_declared_count_with_no_duplicates():
    seasons = [(2008, 1), (2010, 2), (2013, 3), (2018, 4), (2021, 5), (2023, 6)]
    rng = random.Random(3)
    candidates = _pick_candidates(rng, seasons, correct_year=2018)
    assert len(candidates) == CANDIDATE_COUNT
    assert len(set(candidates)) == CANDIDATE_COUNT


def test_pick_candidates_never_invents_a_year_outside_the_franchises_own_seasons():
    seasons = [(2008, 1), (2010, 2), (2013, 3), (2018, 4)]
    rng = random.Random(11)
    candidates = _pick_candidates(rng, seasons, correct_year=2010)
    assert set(candidates) <= {y for y, _ in seasons}


def test_pick_candidates_shuffles_rather_than_always_placing_the_answer_last():
    """A weak implementation could append the correct year at a fixed position (e.g.
    always last) instead of shuffling -- this would still pass the three tests above
    while making the answer guessable by position alone."""
    seasons = [(2008, 1), (2010, 2), (2013, 3), (2018, 4), (2021, 5), (2023, 6)]
    positions = set()
    for seed in range(30):
        candidates = _pick_candidates(random.Random(seed), seasons, correct_year=2013)
        positions.add(candidates.index(2013))
    assert len(positions) > 1
