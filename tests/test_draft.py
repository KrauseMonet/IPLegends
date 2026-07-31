"""A61. The four-overseas cap at deal time.

`eligible_pairs` and `run_draft` are behaviour, not schema, so by the standing rule in
CLAUDE.md this file is the only thing holding the cap in place. It is also the kind of rule
that fails silently: a draft with five overseas players completes perfectly happily and the
symptom arrives one layer later, as an XI that cannot be fielded.

The cap replaced a measured 87-of-400 failure rate, so a regression here does not look like
a crash. It looks like one drafter in five reaching a dead end.
"""

from __future__ import annotations

import random

from etl.feasibility import (
    OVERSEAS_CAP, Card, Deck, POLICIES, TEMPLATE, eligible_pairs, label, run_draft,
)


def card(i: int, overseas: bool | None) -> Card:
    """One draftable player-season, already labelled with every slot, so these tests are
    about the cap rather than about slot supply."""
    return Card(
        fs_id=1, person_id=f"p{i}", name=f"p{i}",
        slots=frozenset({"open", "keeper", "opener", "bowler"}),
        bat=0.5, bowl=0.5, band="opener", role="keeper", overseas=overseas,
    )


# Every band the template draws on, so slot supply can never be what strands a drafter
# here -- only the cap can.
BANDS = ("opener", "top_order", "middle", "finisher")


def deck_of(overseas_per_fs: int, domestic_per_fs: int, n_fs: int = 20) -> Deck:
    """A deck where every franchise-season offers the same mix of bands and nationalities."""
    by_fs: dict[int, list[Card]] = {}
    n = 0
    for fs in range(1, n_fs + 1):
        cards = []
        for overseas, count in ((True, overseas_per_fs), (False, domestic_per_fs)):
            for i in range(count):
                cards.append(Card(fs, f"c{n}", f"c{n}", frozenset(), 0.5, 0.5,
                                  BANDS[i % len(BANDS)], "keeper", overseas=overseas))
                n += 1
        by_fs[fs] = cards
    return label(Deck(by_fs, sorted(by_fs)))


# --- the filter -----------------------------------------------------------------------

def test_an_overseas_card_is_ineligible_once_the_cap_is_reached():
    cards = [card(0, True), card(1, False)]
    unfilled = {"open": 5}
    at_cap = {c.person_id for c, _ in
              eligible_pairs(cards, unfilled, set(), overseas_taken=OVERSEAS_CAP)}
    assert at_cap == {"p1"}, "only the domestic card may still be drafted at the cap"


def test_an_overseas_card_is_eligible_below_the_cap():
    cards = [card(0, True), card(1, False)]
    below = {c.person_id for c, _ in
             eligible_pairs(cards, {"open": 5}, set(), overseas_taken=OVERSEAS_CAP - 1)}
    assert below == {"p0", "p1"}


def test_an_unknown_nationality_is_not_spent_as_an_overseas_place():
    """A23/A49 at the third and last place the count is taken. `is_overseas` is NULL for
    nobody today, but a revised archive can reintroduce one, and an unknown must not have
    an overseas place quietly spent on it -- nor be blocked as if it were overseas."""
    cards = [card(0, None)]
    at_cap = {c.person_id for c, _ in
              eligible_pairs(cards, {"open": 5}, set(), overseas_taken=OVERSEAS_CAP)}
    assert at_cap == {"p0"}, "an unknown is not a known overseas player"


def test_the_cap_can_be_switched_off_for_the_ablation():
    """`etl.feasibility` compares capped against uncapped, so `cap=None` has to mean
    uncapped rather than zero."""
    cards = [card(0, True)]
    uncapped = {c.person_id for c, _ in
                eligible_pairs(cards, {"open": 5}, set(), overseas_taken=99, cap=None)}
    assert uncapped == {"p0"}


# --- the draft ------------------------------------------------------------------------

def test_no_completed_draft_exceeds_the_cap():
    """The property the game depends on: at most four overseas in a squad of fifteen means
    every eleven drawn from it is legal, with no XI-selection path to trust."""
    deck = deck_of(overseas_per_fs=6, domestic_per_fs=6)
    completed = 0
    for seed in range(200):
        result = run_draft(deck, POLICIES["rational"], random.Random(seed), guarantee=True)
        if not result.completed:
            continue
        completed += 1
        overseas = sum(1 for c, _ in result.picks if c.overseas is True)
        assert overseas <= OVERSEAS_CAP, f"seed {seed} drafted {overseas} overseas players"
        assert len(result.picks) == sum(TEMPLATE.values())
    assert completed > 150, "the fixture deck should complete most of the time"


def test_a_deck_of_only_overseas_players_strands_rather_than_overfilling():
    """The cap must bind even when obeying it makes the draft impossible. Silently
    overfilling would be the A23 shape: a rule reported as satisfied while it is not."""
    deck = deck_of(overseas_per_fs=12, domestic_per_fs=0)
    result = run_draft(deck, POLICIES["rational"], random.Random(1), guarantee=False)
    assert not result.completed
    assert sum(1 for c, _ in result.picks if c.overseas is True) <= OVERSEAS_CAP
