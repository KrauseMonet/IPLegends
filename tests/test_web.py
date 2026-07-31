"""SPEC 11. The stateless session, tested without a database.

`web.app` needs the real deck at boot, so the HTTP layer itself is exercised elsewhere;
these tests are for the part that has to be right or the whole design collapses -- that a
session is reconstructible from a seed and a list of choices, and that a state the server
did not produce is refused rather than half-honoured.

The fixture deck is synthetic on purpose. A test that needed Neon to prove the state string
round-trips would be testing the network.
"""

from __future__ import annotations

import pytest

from etl.feasibility import SQUAD_SIZE, Card, Deck, label
from web import session as sess

BANDS = ("opener", "top_order", "middle", "finisher")


def deck_of(n_fs: int = 24, per_fs: int = 10) -> Deck:
    by_fs: dict[int, list[Card]] = {}
    n = 0
    for fs in range(1, n_fs + 1):
        cards = []
        for i in range(per_fs):
            cards.append(Card(fs, f"c{n}", f"c{n}", frozenset(), 0.5, 0.5,
                              BANDS[i % len(BANDS)], "keeper",
                              overseas=(i % 5 == 0), display=80))
            n += 1
        by_fs[fs] = cards
    return label(Deck(by_fs, sorted(by_fs)))


DECK = deck_of()


def walk(seed: int, chooser=lambda pairs, made: 0) -> sess.Session:
    """Play a whole draft through the public API, one pick at a time."""
    s = sess.replay(DECK, seed, ())
    while not s.complete:
        i = chooser(s.deal.options, len(s.choices))
        s = sess.replay(DECK, seed, s.choices + (i,))
    return s


# --- the state string -------------------------------------------------------------------

@pytest.mark.parametrize("choices", [(), (0,), (0, 3, 14), tuple(range(SQUAD_SIZE))])
def test_a_state_round_trips(choices):
    assert sess.decode(sess.encode(7, choices)) == (7, choices)


@pytest.mark.parametrize("bad", ["", "abc-1", "7-1.x", "-", "7-1..2.", "1e3-"])
def test_a_malformed_state_is_rejected_not_guessed(bad):
    with pytest.raises(sess.InvalidState):
        sess.decode(bad)


def test_a_state_longer_than_the_squad_is_rejected():
    with pytest.raises(sess.InvalidState):
        sess.decode(sess.encode(7, tuple(range(SQUAD_SIZE + 1))))


# --- replay, which is the whole design ---------------------------------------------------

def test_a_session_reconstructs_from_seed_and_choices_alone():
    """SPEC 11.2's load-bearing property. If this is false the API has to store sessions."""
    played = walk(11, chooser=lambda pairs, made: (made * 7) % len(pairs))
    rebuilt = sess.replay(DECK, played.seed, played.choices)
    assert [c.person_id for c, _ in rebuilt.squad] == [c.person_id for c, _ in played.squad]
    assert [s for _, s in rebuilt.squad] == [s for _, s in played.squad]
    assert rebuilt.complete and len(rebuilt.squad) == SQUAD_SIZE


def test_the_deal_is_one_franchise_season():
    """The mechanic: a drafter is served one squad at a time (A10), not a global pool."""
    s = sess.replay(DECK, 3, ())
    assert s.deal is not None
    assert {c.fs_id for c, _ in s.deal.options} == {s.deal.fs_id}


def test_picks_accumulate_one_at_a_time():
    s = sess.replay(DECK, 5, ())
    for expected in range(SQUAD_SIZE):
        assert len(s.squad) == expected
        assert not s.complete
        s = sess.replay(DECK, 5, s.choices + (0,))
    assert s.complete and len(s.squad) == SQUAD_SIZE and s.deal is None


def test_a_choice_the_server_never_offered_is_refused():
    """The reason the state needs no signature: an index outside the deal cannot be
    honoured, so a forged state is rejected by replay rather than by a checksum."""
    s = sess.replay(DECK, 5, ())
    with pytest.raises(sess.InvalidState):
        sess.replay(DECK, 5, (len(s.deal.options),))


def test_the_squad_a_session_reports_matches_the_choices_made():
    """`_picks_so_far` replays a second time to rebuild the squad, so it is the piece most
    able to drift from the draft it is meant to mirror."""
    s = sess.replay(DECK, 9, ())
    taken = []
    for _ in range(6):
        taken.append(s.deal.options[1][0].person_id)
        s = sess.replay(DECK, 9, s.choices + (1,))
    assert [c.person_id for c, _ in s.squad] == taken


def test_the_overseas_cap_is_visible_through_the_session():
    """A61 has to bind on a human drafter too, not only on the policies check 12 runs."""
    played = walk(21, chooser=lambda pairs, made: 0)
    assert sum(1 for c, _ in played.squad if c.overseas is True) <= 4


# --- the opponent and the match stream ---------------------------------------------------

def test_the_opponent_is_drafted_after_the_player_on_the_same_stream():
    """One rng stream, in the order `game.__main__` uses it: player, then opponent, then
    the match. If the opponent were dealt from a fresh `Random(seed)` instead, the API and
    the CLI would quietly disagree about what seed 13 is, with nothing to notice it.

    Pinned by comparing against exactly that mistake: an opponent drafted without the
    player's draft in front of it is a DIFFERENT squad.
    """
    import random as _r

    from etl.feasibility import POLICIES, run_draft

    played = walk(13)
    house, rng = sess.after_draft(DECK, played.seed, played.choices)
    assert len(house) == SQUAD_SIZE
    assert sum(1 for c, _ in house if c.overseas is True) <= 4

    fresh = run_draft(DECK, POLICIES["rational"], _r.Random(played.seed)).picks
    assert [c.person_id for c, _ in house] != [c.person_id for c, _ in fresh], (
        "the opponent was dealt from a fresh stream, not from behind the player's draft")


def test_two_sessions_with_the_same_state_produce_the_same_opponent():
    played = walk(13)
    a, _ = sess.after_draft(DECK, played.seed, played.choices)
    b, _ = sess.after_draft(DECK, played.seed, played.choices)
    assert [c.person_id for c, _ in a] == [c.person_id for c, _ in b]
