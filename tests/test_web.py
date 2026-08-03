"""SPEC 11/1.2. The stateless session, tested without a database.

`web.app` needs the real deck at boot, so the HTTP layer itself is exercised elsewhere;
these tests are for the part that has to be right or the whole design collapses -- that a
session is reconstructible from a seed and a list of moves, and that a state the server did
not produce is refused rather than half-honoured.

[A73] A pick names both a candidate and the slot he bats at, in the same request, and
that slot is final the moment it lands -- there is no bench, no unplace. A completed
draft is therefore already arranged; what these tests exercise is that the atomic
pick+place is validated and replayed correctly.

Repositioning, added later, is NOT a reopening of A73 in spirit -- there is still no
bench, nothing sits unplaced. `from_slot` must always be occupied, but `to_slot` may be
open (freeing `from_slot` for a later pick) or occupied (a swap). Unlike the first cut of
this feature, a `Reposition` is now consumed by `run_draft` ITSELF, exactly like a
`Reroll` -- it costs an attempt, mutates `open_slots` directly, and so can only ever be
issued while the draft is still in progress (see `Reposition`'s and
`RepositionRequested`'s own docstrings in web/session.py and etl/feasibility.py).

The fixture deck is synthetic on purpose. A test that needed Neon to prove the state string
round-trips would be testing the network.
"""

from __future__ import annotations

import pytest

from etl.feasibility import IMPACT_SLOT, TWELVE_SIZE, XI_SIZE, Card, Deck
from web import session as sess


def card(n: int, positions: frozenset[int], *, fs: int = 1, role: str = "batter",
         bowl: float | None = None, overseas: bool = False,
         franchise: str | None = None, season_year: int | None = None) -> Card:
    return Card(fs_id=fs, person_id=f"c{n}", name=f"c{n}", bat=0.5, bowl=bowl,
                role=role, overseas=overseas, positions=positions,
                franchise=franchise, season_year=season_year)


# Four synthetic franchises, cycled across the fixture's franchise-seasons so each one
# has several distinct "seasons" -- needed to test that a "season" reroll (A73's second
# kind) actually narrows to the SAME franchise rather than dealing anywhere, which a
# fixture with every card's franchise left at the `Card` default of `None` could never
# tell apart from an unrestricted reroll (`None == None` for every fs).
FRANCHISES = ["Alpha", "Beta", "Gamma", "Delta"]


def deck_of(n_fs: int = 24, per_fs: int = 16) -> Deck:
    """Every franchise-season offers a keeper, several bowlers, and full position spread,
    so a draft can complete legally on its own -- these tests are about the session
    mechanics, not about whether a legal twelve is reachable at all.

    Each card is eligible for a WINDOW of four consecutive (wrapping) positions, not one
    exact position -- measured against the real archive, mean range width is 3.76 (A72),
    so a single-position card is not just a simplification, it is unrealistically rigid.
    A fixture that narrow measurably breaks the forward check: `run_draft` with the
    `rational` policy took 85 seconds and still failed to complete against a single-
    position version of this deck, against 0.35 seconds once every card had a real
    four-position window. The combinatorics were never the bug; the fixture was.
    """
    by_fs: dict[int, list[Card]] = {}
    n = 0
    for fs in range(1, n_fs + 1):
        franchise = FRANCHISES[(fs - 1) % len(FRANCHISES)]
        season_year = 2000 + (fs - 1) // len(FRANCHISES)
        cards = []
        for i in range(per_fs):
            start = i % XI_SIZE
            pos = frozenset(((start + k) % XI_SIZE) + 1 for k in range(4))
            role = "keeper" if i == 0 else "batter"
            bowl = 0.1 if i % 2 == 0 else None
            overseas = i % 6 == 0
            cards.append(card(n, pos, fs=fs, role=role, bowl=bowl, overseas=overseas,
                               franchise=franchise, season_year=season_year))
            n += 1
        by_fs[fs] = cards
    return Deck(by_fs, sorted(by_fs))


DECK = deck_of()


def _spread(candidates, made: int) -> int:
    """The default chooser: cycles through options rather than always taking index 0.

    The fixture squad's one keeper always sits at index 0 of the raw list (and, being
    restricted to position 1 alone, at index 0 of `eligible`'s filtered candidates too
    whenever he is still eligible) -- so "always pick 0" drafts nothing BUT keepers,
    twelve players all competing for the same single position. The forward check then
    correctly grinds through the deal-time guarantee's full redraw budget looking for a
    deal that is not entirely keepers, which is slow for a real reason rather than a bug.
    Spreading picks is what a real drafter does anyway.
    """
    return (made * 7) % len(candidates)


def _open_slots(s: sess.Session) -> frozenset[int]:
    open_slots = set(range(1, XI_SIZE + 1)) | {IMPACT_SLOT}
    for i, c in enumerate(s.order):
        if c is not None:
            open_slots.discard(i + 1)
    if s.impact is not None:
        open_slots.discard(IMPACT_SLOT)
    return frozenset(open_slots)


def walk(seed: int, chooser=_spread) -> sess.Session:
    """Play a whole draft through the public API, one pick at a time. [A73] Each pick
    already names its slot -- the lowest-numbered open slot the chosen candidate is
    eligible for -- so a completed walk is already a fully arranged, playable twelve;
    there is no separate arranging step left to do."""
    s = sess.replay(DECK, seed, ())
    while not s.squad_complete:
        i = chooser(s.deal.options, len(s.picks))
        chosen = s.deal.options[i]
        slot = min(chosen.slots & _open_slots(s))
        s = sess.replay(DECK, seed, s.moves + (sess.Pick(i, slot),))
    return s


# --- the state string -------------------------------------------------------------------

@pytest.mark.parametrize("moves", [
    (), (sess.Pick(0, 1),), (sess.Pick(0, 3), sess.Pick(1, 12)),
    tuple(sess.Pick(0, (i % XI_SIZE) + 1) for i in range(TWELVE_SIZE)),
])
def test_a_state_round_trips(moves):
    assert sess.decode(sess.encode(7, moves)) == (7, moves)


@pytest.mark.parametrize("bad", [
    "", "abc-1", "7-1.x", "-", "7-1:2..2:3.", "1e3-", "7-1:x", "7-3", "7-1:",
])
def test_a_malformed_state_is_rejected_not_guessed(bad):
    with pytest.raises(sess.InvalidState):
        sess.decode(bad)


def test_a_state_longer_than_the_move_cap_is_rejected():
    with pytest.raises(sess.InvalidState):
        sess.decode(sess.encode(7, tuple(sess.Pick(0, 1) for _ in range(sess.MOVE_CAP + 1))))


def test_a_negative_index_is_rejected():
    with pytest.raises(sess.InvalidState):
        sess.decode("7--1:1")


def test_a_slot_below_one_is_rejected():
    with pytest.raises(sess.InvalidState):
        sess.decode("7-0:0")


# --- replay, which is the whole design ---------------------------------------------------

def test_a_session_reconstructs_from_seed_and_moves_alone():
    """SPEC 11.2's load-bearing property. If this is false the API has to store sessions."""
    played = walk(11, chooser=lambda candidates, made: (made * 7) % len(candidates))
    rebuilt = sess.replay(DECK, played.seed, played.moves)
    assert [c.person_id for c in rebuilt.picks] == [c.person_id for c in played.picks]
    assert rebuilt.squad_complete and len(rebuilt.picks) == TWELVE_SIZE


def test_the_deal_is_one_franchise_season():
    """The mechanic: a drafter is served one squad at a time (A10), not a global pool."""
    s = sess.replay(DECK, 3, ())
    assert s.deal is not None
    assert {c.fs_id for c in s.deal.options} == {s.deal.fs_id}


def test_picks_accumulate_one_at_a_time():
    s = sess.replay(DECK, 5, ())
    moves: tuple = ()
    for expected in range(TWELVE_SIZE):
        assert len(s.picks) == expected
        assert not s.squad_complete
        i = _spread(s.deal.options, expected)
        chosen = s.deal.options[i]
        slot = min(chosen.slots & _open_slots(s))
        moves = moves + (sess.Pick(i, slot),)
        s = sess.replay(DECK, 5, moves)
    assert s.squad_complete and len(s.picks) == TWELVE_SIZE and s.deal is None


def test_a_choice_the_server_never_offered_is_refused():
    """The reason the state needs no signature: an index outside the deal cannot be
    honoured, so a forged state is rejected by replay rather than by a checksum."""
    s = sess.replay(DECK, 5, ())
    with pytest.raises(sess.InvalidState):
        sess.replay(DECK, 5, (sess.Pick(len(s.deal.options), 1),))


def test_the_picks_a_session_reports_matches_the_choices_made():
    s = sess.replay(DECK, 9, ())
    taken = []
    for _ in range(6):
        chosen = s.deal.options[1]
        slot = min(chosen.slots & _open_slots(s))
        taken.append(chosen.person_id)
        s = sess.replay(DECK, 9, s.moves + (sess.Pick(1, slot),))
    assert [c.person_id for c in s.picks] == taken


def test_the_overseas_cap_is_visible_through_the_session():
    """A61 has to bind on a human drafter too, not only on the policies check 12 runs."""
    played = walk(21)
    assert sum(1 for c in played.picks if c.overseas is True) <= 4


# --- atomic placement: final the moment a pick is made ------------------------------------

def test_a_completed_draft_is_already_playable():
    """[A73] There is no separate arranging phase -- every pick already committed to a
    slot, so a completed draft is a completed, legal twelve by construction."""
    s = walk(4)
    assert s.squad_complete
    assert s.playable
    assert s.errors == ()
    assert all(c is not None for c in s.order)
    assert s.impact is not None


def test_an_incomplete_draft_reports_what_is_still_missing():
    s = sess.replay(DECK, 4, ())
    assert not s.squad_complete
    assert any("batting positions are filled" in e for e in s.errors)
    assert any("Impact" in e for e in s.errors)


def test_a_pick_naming_an_ineligible_slot_is_refused():
    s = sess.replay(DECK, 4, ())
    chosen = s.deal.options[0]
    outside = next(i for i in range(1, XI_SIZE + 1) if i not in chosen.positions)
    with pytest.raises(sess.InvalidState):
        sess.replay(DECK, 4, (sess.Pick(0, outside),))


def test_a_pick_naming_an_already_filled_slot_is_refused():
    """[A73] No displacement any more -- a slot is either open or it is not, and a second
    pick cannot land on one already spoken for."""
    s = sess.replay(DECK, 4, ())
    chosen = s.deal.options[0]
    slot = min(chosen.positions)
    s2 = sess.replay(DECK, 4, (sess.Pick(0, slot),))
    assert s2.order[slot - 1] is not None
    with pytest.raises(sess.InvalidState):
        sess.replay(DECK, 4, s2.moves + (sess.Pick(0, slot),))


def test_the_impact_slot_accepts_any_drafted_player():
    s = sess.replay(DECK, 4, ())
    chosen = s.deal.options[0]
    s2 = sess.replay(DECK, 4, (sess.Pick(0, IMPACT_SLOT),))
    assert s2.impact is not None and s2.impact.person_id == chosen.person_id


# --- the opponent and the match stream ---------------------------------------------------

def test_the_opponent_is_drafted_after_the_player_on_the_same_stream():
    """One rng stream, in the order `game.__main__` uses it: player, then opponent, then
    the match. If the opponent were dealt from a fresh `Random(seed)` instead, the API and
    the CLI would quietly disagree about what seed 13 is, with nothing to notice it.
    """
    import random as _r

    from etl.feasibility import POLICIES, run_draft

    played = walk(13)
    house, rng = sess.after_draft(DECK, played.seed, played.moves)
    assert len(house) == TWELVE_SIZE
    assert sum(1 for c in house if c.overseas is True) <= 4

    fresh = run_draft(DECK, POLICIES["rational"], _r.Random(played.seed)).picks
    assert [c.person_id for c in house] != [c.person_id for c in fresh], (
        "the opponent was dealt from a fresh stream, not from behind the player's draft")


def test_two_sessions_with_the_same_state_produce_the_same_opponent():
    played = walk(13)
    a, _ = sess.after_draft(DECK, played.seed, played.moves)
    b, _ = sess.after_draft(DECK, played.seed, played.moves)
    assert [c.person_id for c in a] == [c.person_id for c in b]


# --- rerolls --------------------------------------------------------------------------
#
# Two kinds (both new): "team" deals any other franchise-season; "season" restricts the
# redeal to the SAME franchise, a different year. Both spend the same `REROLLS_ALLOWED`
# budget -- the kind only changes which pool the very next deal is drawn from.

def test_a_reroll_never_spends_a_pick():
    from etl.feasibility import REROLLS_ALLOWED

    s2 = sess.replay(DECK, 2, (sess.Reroll("team"),))
    assert len(s2.picks) == 0, "a reroll never counts as a pick"
    assert s2.rerolls_used == 1
    assert s2.rerolls_remaining == REROLLS_ALLOWED - 1


def test_a_team_reroll_never_repeats_the_rejected_franchise_season():
    """`run_draft` excludes the just-rejected fs from a "team" reroll's pool, so -- unlike
    the deal-time guarantee's own silent redraw, which can coincidentally repeat a fs --
    a single team reroll is guaranteed to land somewhere else."""
    s = sess.replay(DECK, 5, ())
    before_fs = s.deal.fs_id
    s = sess.replay(DECK, 5, (sess.Reroll("team"),))
    assert s.deal.fs_id != before_fs


def test_a_season_reroll_stays_within_the_same_franchise():
    """The whole point of the second kind: a different YEAR of the SAME team, never an
    unrelated one. `FRANCHISES` gives each franchise several fixture seasons, so this is
    actually exercising the restriction rather than passing by coincidence."""
    s = sess.replay(DECK, 5, ())
    before_fs, before_franchise = s.deal.fs_id, s.deal.franchise
    s = sess.replay(DECK, 5, (sess.Reroll("season"),))
    assert s.deal.fs_id != before_fs, "a season reroll must still deal something different"
    assert s.deal.franchise == before_franchise


def test_the_reroll_budget_is_enforced():
    from etl.feasibility import REROLLS_ALLOWED

    moves = tuple(sess.Reroll("team") for _ in range(REROLLS_ALLOWED))
    s = sess.replay(DECK, 2, moves)
    assert s.rerolls_used == REROLLS_ALLOWED
    assert s.rerolls_remaining == 0
    with pytest.raises(sess.InvalidState):
        sess.replay(DECK, 2, moves + (sess.Reroll("team"),))


def test_an_unknown_reroll_kind_is_rejected():
    with pytest.raises(sess.InvalidState):
        sess.replay(DECK, 2, (sess.Reroll("nation"),))


def test_a_reroll_can_be_interleaved_with_picks():
    s = sess.replay(DECK, 2, ())
    s = sess.replay(DECK, 2, s.moves + (sess.Reroll("team"),))
    chosen = s.deal.options[0]
    slot = min(chosen.slots)
    s = sess.replay(DECK, 2, s.moves + (sess.Pick(0, slot),))
    assert s.rerolls_used == 1
    assert len(s.picks) == 1
    assert s.picks[0].person_id == chosen.person_id


def test_reroll_state_round_trips():
    moves = (sess.Reroll("team"), sess.Pick(0, 7), sess.Reroll("season"))
    state = sess.encode(2, moves)
    assert sess.decode(state) == (2, moves)


# --- repositioning: moving or swapping already-placed players' slots ----------------------
#
# A reposition is now consumed by `run_draft` itself (`RepositionRequested`,
# etl/feasibility.py), the same way a `Reroll` is -- it costs an attempt, so it can only
# ever be issued while the draft is still in progress (there is no pick_no iteration left
# for a completed twelve to consume it in). Every fixture below uses `partial_walk`, never
# `walk`, for exactly that reason.

def partial_walk(seed: int, n: int, chooser=_spread) -> sess.Session:
    """Like `walk`, but stops after exactly `n` real picks, leaving the draft still in
    progress -- what every reposition test needs, since a completed twelve has no
    pick_no iteration left for `run_draft` to consume a `Reposition` move within."""
    s = sess.replay(DECK, seed, ())
    for _ in range(n):
        assert s.deal is not None, "ran out of picks before reaching n"
        i = chooser(s.deal.options, len(s.picks))
        chosen = s.deal.options[i]
        slot = min(chosen.slots & _open_slots(s))
        s = sess.replay(DECK, seed, s.moves + (sess.Pick(i, slot),))
    return s


def _find_pair(s: sess.Session, *, mutually_eligible: bool):
    """Two filled slots whose occupants either can or cannot legally trade places --
    searched for rather than assumed, since the fixture's four-wide windows make either
    outcome common but not guaranteed for any one specific pair."""
    filled = {i + 1: c for i, c in enumerate(s.order) if c is not None}
    if s.impact is not None:
        filled[IMPACT_SLOT] = s.impact
    for slot_a, card_a in filled.items():
        for slot_b, card_b in filled.items():
            if slot_a >= slot_b:
                continue
            ok = slot_b in card_a.slots and slot_a in card_b.slots
            if ok == mutually_eligible:
                return slot_a, slot_b
    return None


def _find_movable(s: sess.Session):
    """A filled slot and a DIFFERENT, currently-open slot its occupant is legally
    eligible for -- the "move" shape of a reposition, as opposed to `_find_pair`'s
    "swap" shape."""
    open_now = {i + 1 for i, c in enumerate(s.order) if c is None}
    for i, card in enumerate(s.order):
        if card is None:
            continue
        for target in card.positions:
            if target in open_now and target != i + 1:
                return i + 1, target
    return None


def test_reposition_swaps_two_already_placed_players():
    s = partial_walk(9, 8)
    pair = _find_pair(s, mutually_eligible=True)
    assert pair is not None, "fixture did not produce a swappable pair for this seed"
    slot_a, slot_b = pair

    def at(session, slot):
        return session.impact if slot == IMPACT_SLOT else session.order[slot - 1]

    card_a, card_b = at(s, slot_a), at(s, slot_b)
    s2 = sess.replay(DECK, 9, s.moves + (sess.Reposition(slot_a, slot_b),))
    assert at(s2, slot_a).person_id == card_b.person_id
    assert at(s2, slot_b).person_id == card_a.person_id
    # every other slot is untouched
    for slot in range(1, XI_SIZE + 1):
        if slot in (slot_a, slot_b):
            continue
        before, after = s.order[slot - 1], s2.order[slot - 1]
        assert (before.person_id if before else None) == (after.person_id if after else None)


def test_reposition_moves_into_an_open_slot_and_frees_the_original():
    """The new capability: `to_slot` need not be occupied. Moving into an open slot
    frees `from_slot` -- proven precisely (a later pick can then target it) at the
    run_draft level in tests/test_draft.py; this checks the same shape survives session
    replay end to end (encode/decode, `_NeedChoice`, the deal actually offered next)."""
    s = partial_walk(9, 6)
    pair = _find_movable(s)
    assert pair is not None, "fixture did not produce a movable slot for this seed"
    from_slot, to_slot = pair
    moved = s.order[from_slot - 1]

    s2 = sess.replay(DECK, 9, s.moves + (sess.Reposition(from_slot, to_slot),))
    assert s2.order[to_slot - 1].person_id == moved.person_id
    assert s2.order[from_slot - 1] is None, "the original slot must now be open"
    assert s2.deal is not None

    open_after = _open_slots(s2)
    assert from_slot in open_after, "the freed slot must be genuinely open, not cosmetic"
    eligible_here = [i for i, c in enumerate(s2.deal.options) if from_slot in c.slots]
    if eligible_here:   # only guaranteed by construction at the run_draft level above
        i = eligible_here[0]
        s3 = sess.replay(DECK, 9, s2.moves + (sess.Pick(i, from_slot),))
        assert s3.order[from_slot - 1].person_id == s2.deal.options[i].person_id


def test_reposition_refuses_when_the_from_slot_is_empty():
    """Only `to_slot` may be empty (the move case) -- `from_slot` must always hold
    someone, or there is nothing to reposition."""
    s = sess.replay(DECK, 4, ())
    chosen = s.deal.options[0]
    slot = min(chosen.positions)
    s2 = sess.replay(DECK, 4, (sess.Pick(0, slot),))
    empty_slot = next(sl for sl in range(1, XI_SIZE + 1) if sl != slot)
    with pytest.raises(sess.InvalidState):
        sess.replay(DECK, 4, s2.moves + (sess.Reposition(empty_slot, slot),))


def test_reposition_refuses_a_move_into_an_ineligible_open_slot():
    s = sess.replay(DECK, 4, ())
    chosen = s.deal.options[0]
    slot = min(chosen.positions)
    s2 = sess.replay(DECK, 4, (sess.Pick(0, slot),))
    bad_target = next(sl for sl in range(1, XI_SIZE + 1)
                       if sl != slot and sl not in chosen.positions)
    with pytest.raises(sess.InvalidState):
        sess.replay(DECK, 4, s2.moves + (sess.Reposition(slot, bad_target),))


def test_reposition_refuses_a_pair_that_is_not_mutually_eligible():
    s = partial_walk(9, 8)
    pair = _find_pair(s, mutually_eligible=False)
    assert pair is not None, "fixture did not produce an ineligible pair for this seed"
    with pytest.raises(sess.InvalidState):
        sess.replay(DECK, 9, s.moves + (sess.Reposition(*pair),))


def test_reposition_refuses_a_slot_swapping_with_itself():
    s = sess.replay(DECK, 4, ())
    chosen = s.deal.options[0]
    slot = min(chosen.positions)
    s2 = sess.replay(DECK, 4, (sess.Pick(0, slot),))
    with pytest.raises(sess.InvalidState):
        sess.replay(DECK, 4, s2.moves + (sess.Reposition(slot, slot),))


def test_reposition_can_swap_a_player_into_and_out_of_impact():
    """Slot 12 names Impact, same as a Pick -- swapping a batting slot with it must work
    exactly like swapping two XI slots, not need a separate code path."""
    s = partial_walk(9, 8)
    pair = None
    for slot in range(1, XI_SIZE + 1):
        if s.order[slot - 1] is not None and s.impact is not None \
                and IMPACT_SLOT in s.order[slot - 1].slots and slot in s.impact.slots:
            pair = (slot, IMPACT_SLOT)
            break
    assert pair is not None, "fixture did not produce an eligible impact swap for this seed"
    xi_slot, _ = pair
    xi_card, impact_card = s.order[xi_slot - 1], s.impact
    s2 = sess.replay(DECK, 9, s.moves + (sess.Reposition(xi_slot, IMPACT_SLOT),))
    assert s2.order[xi_slot - 1].person_id == impact_card.person_id
    assert s2.impact.person_id == xi_card.person_id


def test_a_reposition_costs_an_attempt_not_a_pick():
    """Mirrors `test_a_reroll_never_spends_a_pick`: a reposition must never advance the
    pick count, only occupy an attempt inside the SAME pick_no."""
    s = partial_walk(9, 8)
    pair = _find_pair(s, mutually_eligible=True)
    assert pair is not None
    s2 = sess.replay(DECK, 9, s.moves + (sess.Reposition(*pair),))
    assert len(s2.picks) == len(s.picks)


@pytest.mark.parametrize("bad", ["7-m3", "7-m3:", "7-mx:1", "7-m1:x", "7-m0:1"])
def test_a_malformed_reposition_is_rejected_not_guessed(bad):
    with pytest.raises(sess.InvalidState):
        sess.decode(bad)


def test_reposition_state_round_trips():
    moves = (sess.Pick(0, 3), sess.Reposition(3, 12), sess.Reposition(1, 2))
    state = sess.encode(2, moves)
    assert sess.decode(state) == (2, moves)
