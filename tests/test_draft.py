"""A61. The four-overseas cap at deal time, now inside `eligible`'s forward check.

`eligible` and `run_draft` are behaviour, not schema, so by the standing rule in CLAUDE.md
this file is the only thing holding the cap in place. It is also the kind of rule that
fails silently: a draft with five overseas players completes perfectly happily and the
symptom arrives one layer later, as a final twelve that cannot be fielded legally.

[A72/A73] The cap now lives beside the position/keeper/bowler forward check rather than
beside slot scarcity, so every fixture here needs a deck that can ALSO field a legal
twelve -- otherwise a test meant to be about the overseas cap would actually be testing
the forward check for an unrelated reason. Placement is atomic (A73): a completed draft's
`order`/`impact` is already the final twelve, verified independently via `order_errors`
rather than a separate `best_twelve` solve.
"""

from __future__ import annotations

import random

from etl.feasibility import (
    ALL_SLOTS, BOWLERS_IN_TWELVE, OVERSEAS_CAP, TWELVE_SIZE, XI_SIZE,
    Card, Deck, DraftState, POLICIES, eligible, order_errors, run_draft,
)


def card(i: int, positions: frozenset[int], overseas: bool | None, *,
         role: str = "batter", bowl: float | None = None) -> Card:
    """One draftable player-season, positioned and roled so a fixture deck can field a
    legal twelve on its own -- these tests are about the overseas cap, not about whether
    a legal twelve is reachable at all."""
    return Card(fs_id=1, person_id=f"p{i}", name=f"p{i}", bat=0.5, bowl=bowl,
                role=role, overseas=overseas, positions=positions,
                keeper_eligible=(role == "keeper"))


def deck_of(overseas_per_fs: int, domestic_per_fs: int, n_fs: int = 20) -> Deck:
    """A deck where every franchise-season offers the same mix: one keeper, five bowlers,
    positions spread 1-11, in whatever nationality mix the test asks for."""
    by_fs: dict[int, list[Card]] = {}
    n = 0
    for fs in range(1, n_fs + 1):
        cards = []
        for overseas, count in ((True, overseas_per_fs), (False, domestic_per_fs)):
            for i in range(count):
                pos = frozenset({(n % XI_SIZE) + 1})
                role = "keeper" if n % 15 == 0 else "batter"
                bowl = 0.1 if n % 3 == 0 else None
                cards.append(card(n, pos, overseas, role=role, bowl=bowl))
                n += 1
        by_fs[fs] = cards
    return Deck(by_fs, sorted(by_fs))


# --- the filter -------------------------------------------------------------------------
#
# `eligible` is isolated from the forward check's other requirements throughout by
# passing keeper_have=True, bowl_have=BOWLERS_IN_TWELVE and a full remaining budget --
# these tests are about the overseas cap alone.

def test_an_overseas_card_is_ineligible_once_the_cap_is_reached():
    cards = [card(0, frozenset({1}), True), card(1, frozenset({1}), False)]
    eligible_ids = {
        c.person_id for c in eligible(
            cards, taken=set(), open_slots=ALL_SLOTS, keeper_have=True,
            bowl_have=BOWLERS_IN_TWELVE, overseas_taken=OVERSEAS_CAP, remaining=TWELVE_SIZE,
        )
    }
    assert eligible_ids == {"p1"}, "only the domestic card may still be drafted at the cap"


def test_an_overseas_card_is_eligible_below_the_cap():
    cards = [card(0, frozenset({1}), True), card(1, frozenset({1}), False)]
    eligible_ids = {
        c.person_id for c in eligible(
            cards, taken=set(), open_slots=ALL_SLOTS, keeper_have=True,
            bowl_have=BOWLERS_IN_TWELVE, overseas_taken=OVERSEAS_CAP - 1,
            remaining=TWELVE_SIZE,
        )
    }
    assert eligible_ids == {"p0", "p1"}


def test_an_unknown_nationality_is_not_spent_as_an_overseas_place():
    """A23/A49 at the third and last place the count is taken. `is_overseas` is NULL for
    nobody today, but a revised archive can reintroduce one, and an unknown must not have
    an overseas place quietly spent on it -- nor be blocked as if it were overseas."""
    cards = [card(0, frozenset({1}), None)]
    eligible_ids = {
        c.person_id for c in eligible(
            cards, taken=set(), open_slots=ALL_SLOTS, keeper_have=True,
            bowl_have=BOWLERS_IN_TWELVE, overseas_taken=OVERSEAS_CAP, remaining=TWELVE_SIZE,
        )
    }
    assert eligible_ids == {"p0"}, "an unknown is not a known overseas player"


def test_the_cap_can_be_switched_off_for_the_ablation():
    """`eligible(cap=None)` has to mean uncapped rather than zero."""
    cards = [card(0, frozenset({1}), True)]
    eligible_ids = {
        c.person_id for c in eligible(
            cards, taken=set(), open_slots=ALL_SLOTS, keeper_have=True,
            bowl_have=BOWLERS_IN_TWELVE, overseas_taken=99, remaining=TWELVE_SIZE, cap=None,
        )
    }
    assert eligible_ids == {"p0"}


# --- the draft ----------------------------------------------------------------------------

def test_no_completed_draft_exceeds_the_cap():
    """The property the game depends on: at most four overseas among a final twelve, with
    no selection path to trust -- placement is atomic, so a completed draft's own
    `order`/`impact` already IS the final twelve."""
    deck = deck_of(overseas_per_fs=6, domestic_per_fs=6)
    completed = 0
    for seed in range(100):
        result = run_draft(deck, POLICIES["rational"], random.Random(seed), guarantee=True)
        if not result.completed:
            continue
        completed += 1
        overseas = sum(1 for c in result.picks if c.overseas is True)
        assert overseas <= OVERSEAS_CAP, f"seed {seed} drafted {overseas} overseas players"
        assert len(result.picks) == TWELVE_SIZE
        assert not order_errors(result.order, result.impact, result.picks), (
            f"seed {seed} completed but order_errors calls the result illegal"
        )
    assert completed > 80, "the fixture deck should complete most of the time"


def test_a_deck_of_only_overseas_players_strands_rather_than_overfilling():
    """The cap must bind even when obeying it makes the draft impossible. Silently
    overfilling would be the A23 shape: a rule reported as satisfied while it is not."""
    deck = deck_of(overseas_per_fs=12, domestic_per_fs=0)
    result = run_draft(deck, POLICIES["rational"], random.Random(1), guarantee=False)
    assert not result.completed
    assert sum(1 for c in result.picks if c.overseas is True) <= OVERSEAS_CAP


def test_a_pick_that_would_strand_the_final_twelve_is_blocked():
    """The forward check's whole point: taking a card now must never make a legal twelve
    unreachable with the picks that remain. A deck offering only lower-order-eligible
    batters with no keeper anywhere cannot field a twelve at all, so every draft against it
    strands rather than completing illegally."""
    by_fs = {
        1: [card(i, frozenset({9, 10, 11}), False) for i in range(20)],
    }
    deck = Deck(by_fs, [1])
    result = run_draft(deck, POLICIES["rational"], random.Random(1), guarantee=False)
    assert not result.completed


def test_the_guarantee_redraws_rather_than_dealing_an_unservable_squad():
    """A deck where one franchise-season offers nothing eligible at all (empty) beside
    one that offers a full legal spread: the guarantee must re-draw past the empty deal
    every time rather than stranding on it."""
    alive = deck_of(overseas_per_fs=0, domestic_per_fs=20, n_fs=1).cards_by_fs[1]
    deck = Deck({1: [], 2: alive}, [1, 2])
    result = run_draft(deck, POLICIES["rational"], random.Random(3), guarantee=True)
    assert result.completed
    assert not order_errors(result.order, result.impact, result.picks)
    assert all(fs == 2 for fs in result.fs_served), "the empty deal is never actually served"


# --- rerolls --------------------------------------------------------------------------

def test_a_policy_can_reroll_a_servable_deal_without_it_counting_as_a_pick():
    """A reroll is a POLICY choice, distinct from the guarantee's own silent redraw past
    an empty deal: `RerollRequested` is raised even though the deal it rejects already has
    eligible candidates, and `run_draft` must retry with a new franchise-season for the
    SAME pick rather than treating the rejection as a completed pick or a stranding."""
    from etl.feasibility import RerollRequested, pick_rational

    deck = deck_of(overseas_per_fs=0, domestic_per_fs=20)
    rerolled = {"n": 0}

    def reroll_twice_then_pick(candidates, state, rng):
        if rerolled["n"] < 2 and state.remaining == TWELVE_SIZE:
            rerolled["n"] += 1
            raise RerollRequested
        return pick_rational(candidates, state, rng)

    result = run_draft(deck, reroll_twice_then_pick, random.Random(1), guarantee=True)
    assert result.completed
    assert result.player_rerolls == 2
    assert len(result.picks) == TWELVE_SIZE, "a reroll must never be counted as a pick"


# --- repositioning ----------------------------------------------------------------------

def test_a_reposition_frees_a_slot_for_a_pick_that_had_nowhere_else_to_go():
    """The scenario this exists for: a card eligible at exactly ONE position, already
    occupied by a teammate with somewhere else to go. A custom policy takes the flexible
    player into slot 2 first, then -- before its second pick -- repositions him out to
    slot 5 (also eligible), and only THEN takes the locked player into the now-open slot
    2. This is the one behaviour a client-side post-pass could never give: the reposition
    has to change what `run_draft`'s OWN `eligible()` call offers on the very next
    attempt, not just what the final arrangement looks like."""
    from etl.feasibility import RepositionRequested, pick_rational

    def card(i, positions, *, bowl=None, role="batter"):
        return Card(fs_id=1, person_id=f"p{i}", name=f"p{i}", bat=0.5, bowl=bowl,
                    role=role, positions=positions, keeper_eligible=(role == "keeper"))

    flex = card(0, frozenset({2, 5}))
    locked = card(1, frozenset({2}))
    keeper = card(2, frozenset({1}), role="keeper")
    bowlers = [card(10 + i, frozenset({pos}), bowl=0.2)
               for i, pos in enumerate((3, 4, 6, 7, 8))]
    extras = [card(20 + i, frozenset({9, 10, 11})) for i in range(6)]
    deck = Deck({1: [flex, locked, keeper] + bowlers + extras}, [1])

    done = {"repositioned": False}

    def policy(candidates, state, rng):
        by_id = {c.person_id: c for c in candidates}
        if state.remaining == TWELVE_SIZE:
            return by_id["p0"], 2
        if not done["repositioned"] and state.remaining == TWELVE_SIZE - 1:
            done["repositioned"] = True
            raise RepositionRequested(2, 5)
        if "p1" in by_id and 2 in state.open_slots:
            return by_id["p1"], 2
        return pick_rational(candidates, state, rng)

    result = run_draft(deck, policy, random.Random(1))
    assert result.completed
    assert result.order[1].person_id == "p1", "the locked player must land at slot 2"
    assert result.order[4].person_id == "p0", "the flexible player must have moved to slot 5"
    assert result.player_repositions == 1
    assert len(result.picks) == TWELVE_SIZE, "a reposition must never be counted as a pick"


def test_a_reposition_swap_never_changes_open_slots():
    """The other shape: both slots already occupied. Neither becomes open, so this must
    cost an attempt but never touch which slot a later pick can target."""
    from etl.feasibility import RepositionRequested, pick_rational

    def card(i, positions, *, bowl=None, role="batter"):
        return Card(fs_id=1, person_id=f"p{i}", name=f"p{i}", bat=0.5, bowl=bowl,
                    role=role, positions=positions, keeper_eligible=(role == "keeper"))

    a = card(0, frozenset({2, 5}))
    b = card(1, frozenset({2, 5}))
    keeper = card(2, frozenset({1}), role="keeper")
    bowlers = [card(10 + i, frozenset({pos}), bowl=0.2)
               for i, pos in enumerate((3, 4, 6, 7, 8))]
    extras = [card(20 + i, frozenset({9, 10, 11})) for i in range(6)]
    deck = Deck({1: [a, b, keeper] + bowlers + extras}, [1])

    done = {"swapped": False}

    def policy(candidates, state, rng):
        by_id = {c.person_id: c for c in candidates}
        if state.remaining == TWELVE_SIZE:
            return by_id["p0"], 2
        if state.remaining == TWELVE_SIZE - 1:
            return by_id["p1"], 5
        if not done["swapped"] and state.remaining == TWELVE_SIZE - 2:
            done["swapped"] = True
            raise RepositionRequested(2, 5)
        return pick_rational(candidates, state, rng)

    result = run_draft(deck, policy, random.Random(1))
    assert result.completed
    assert result.order[1].person_id == "p1" and result.order[4].person_id == "p0", (
        "a swap trades occupants, it does not touch which slots count as open"
    )
    assert result.player_repositions == 1
    assert len(result.picks) == TWELVE_SIZE


# --- the timeout fallback ----------------------------------------------------------------
# `pick_afk` had no coverage at all until the rule changed from "lowest-rated" to "random",
# which is exactly the shape this repo's own standing rule warns about: a rule that lives in
# behaviour rather than in a schema constraint is protected by nothing unless a test points
# at it. Each assertion below was checked to fail against the rule it guards being broken.

def _afk_deck() -> tuple[list[Card], DraftState]:
    """Ten candidates with distinct ratings, every one legal for the same open slots, so
    the only thing separating them is which one the policy chooses."""
    from etl.feasibility import DraftState
    cards = [Card(fs_id=1, person_id=f"a{i}", name=f"a{i}", bat=0.10 * (i + 1), bowl=None,
                  role="batter", overseas=False, positions=frozenset({2, 3, 4}),
                  keeper_eligible=False)
             for i in range(10)]
    state = DraftState(picks=(), order=(None,) * XI_SIZE, impact=None,
                       open_slots=frozenset({2, 3, 4}), keeper_have=False,
                       bowl_have=0, remaining=TWELVE_SIZE)
    return cards, state


def test_afk_does_not_always_take_the_lowest_rated():
    """The rule that changed. The old policy was `min(candidates, key=rating)`, so over
    many seeds it returned the same worst card every time; a random draw must not."""
    from etl.feasibility import pick_afk
    cards, state = _afk_deck()
    worst = min(cards, key=lambda c: c.rating).person_id
    chosen = {pick_afk(cards, state, random.Random(s))[0].person_id for s in range(60)}
    assert len(chosen) > 1, f"afk still deterministic, always picked {chosen}"
    assert chosen - {worst}, "afk never picked anything but the lowest-rated card"


def test_afk_spreads_across_the_candidates():
    """Uniform-ish rather than merely non-constant -- a draw biased onto two or three
    cards would pass the test above while still not being a random eligible player."""
    from etl.feasibility import pick_afk
    cards, state = _afk_deck()
    seen = {pick_afk(cards, state, random.Random(s))[0].person_id for s in range(400)}
    assert len(seen) == len(cards), f"only {len(seen)} of {len(cards)} cards ever chosen"


def test_afk_is_reproducible_for_a_given_seed():
    """A62's determinism guarantee: the same rng state must yield the same pick, which is
    what lets `web.rooms._resolve` seed from room state and get a reproducible timeout."""
    from etl.feasibility import pick_afk
    cards, state = _afk_deck()
    first = pick_afk(cards, state, random.Random("room-7:afk:3"))
    again = pick_afk(cards, state, random.Random("room-7:afk:3"))
    assert first[0].person_id == again[0].person_id and first[1] == again[1]


def test_afk_only_ever_returns_a_slot_the_card_is_eligible_for():
    """The player is drawn, the slot is not -- `choose_slot` still returns the lowest open
    position, so a random pick can never be placed somewhere illegal."""
    from etl.feasibility import pick_afk
    cards, state = _afk_deck()
    for s in range(50):
        chosen, slot = pick_afk(cards, state, random.Random(s))
        assert slot in (chosen.slots & state.open_slots)
        assert slot == min(chosen.positions & state.open_slots)
