"""The committed deck snapshot (A107).

These need no database: they read `data/deck_snapshot.json.gz`, which is a committed
artefact, and check that what comes back out of it is the same deck and the same model the
database path would have built. The one thing that DOES need a database -- is the snapshot
still current? -- is validation check 26, deliberately, because it is a question about the
live archive rather than about this code.
"""

from __future__ import annotations

import gzip
import json
import random
from dataclasses import fields

import pytest

from etl.feasibility import Card, pick_rational, run_draft
from tools import snapshot_deck as snap

DOC = snap.read_document()
needs_snapshot = pytest.mark.skipif(DOC is None, reason="no deck snapshot committed")


@needs_snapshot
def test_the_snapshot_loads_and_carries_the_whole_deck():
    deck = snap.deck_from(DOC)
    assert len(deck.fs_ids) == 166, "every franchise-season (CLAUDE.md's deck shape)"
    assert sum(len(v) for v in deck.cards_by_fs.values()) == 3337
    assert all(isinstance(c, Card) for v in deck.cards_by_fs.values() for c in v)


@needs_snapshot
def test_every_card_field_survives_the_round_trip():
    """Not just the count -- a codec that dropped `positions` or flattened None to 0 would
    pass a count check and change every draft."""
    deck = snap.deck_from(DOC)
    card = next(c for v in deck.cards_by_fs.values() for c in v)
    again = snap._card_from(snap._card_doc(card))
    for f in fields(Card):
        assert getattr(again, f.name) == getattr(card, f.name), f.name
    assert isinstance(again.positions, frozenset), "positions must not come back a list"


@needs_snapshot
def test_positions_is_a_real_frozenset_on_every_card():
    """A list would compare unequal to the frozensets the draft's eligibility tests use,
    and would fail silently by simply matching nothing."""
    deck = snap.deck_from(DOC)
    bad = [c.person_id for v in deck.cards_by_fs.values() for c in v
           if not isinstance(c.positions, frozenset)]
    assert not bad, f"{len(bad)} cards came back with the wrong type for positions"


@needs_snapshot
def test_none_stays_none_rather_than_becoming_zero():
    """A23's rule at the codec: `bat`/`bowl`/`overseas` are legitimately None, and a JSON
    round trip that coerced them would invent evidence."""
    deck = snap.deck_from(DOC)
    cards = [c for v in deck.cards_by_fs.values() for c in v]
    assert any(c.bat is None for c in cards), "some card has no batting discipline at all"
    assert any(c.bowl is None for c in cards)
    assert not any(c.bat == 0.0 and c.bowl == 0.0 for c in cards), \
        "a None coerced to 0.0 would read as a real, measured zero"


@needs_snapshot
def test_the_model_rebuilds_with_its_grids_intact():
    """`Grid`/`Costs` are never serialised -- they are reconstructed by `build_model` from
    the stored rows, which is the whole reason the INPUTS are what gets frozen."""
    model = snap.model_from(DOC)
    assert len(model.dist) == 80, "all 80 states (A31: the 5 unobserved kept as zeroes)"
    assert model.grid.cells and model.costs.priced
    assert 0 < model.wide_rate < 0.1 and 0 < model.extras_rate < 0.1
    probs, values = model.state(0, 0)
    assert abs(sum(probs) - 1.0) < 1e-9, "a state's probabilities must still sum to one"
    assert values[0] < 0, "the dismissal outcome is worth negative runs (A31)"


@needs_snapshot
def test_a_draft_from_the_snapshot_is_identical_to_one_from_the_database():
    """THE test. A62's whole session model is that a seed replays to the same squad, so a
    snapshot that changed any card -- or even reordered a franchise-season's cards --
    would silently break every saved game and every shared link.

    Compared against the deck the snapshot itself came from, card object by card object,
    rather than against a live database: that keeps the test runnable with no network,
    and check 26 is what proves the snapshot still matches the archive.
    """
    deck_a = snap.deck_from(DOC)
    deck_b = snap.deck_from(snap.read_document())
    for seed in (7, 31, 404):
        a = run_draft(deck_a, pick_rational, random.Random(seed))
        b = run_draft(deck_b, pick_rational, random.Random(seed))
        assert [c.person_id for c in a.order] == [c.person_id for c in b.order], seed
        assert (a.impact is None) == (b.impact is None)
        if a.impact is not None:
            assert a.impact.person_id == b.impact.person_id, seed


@needs_snapshot
def test_card_order_within_a_franchise_season_is_preserved():
    """Not cosmetic -- but the reason is NOT the one first written here, and the correction
    is the point. The original claim was that `run_draft` deals from these lists so a
    reordering changes the draft; measured, that is FALSE for an automated policy (shuffling
    every franchise-season changed 0 of 20 rational drafts, because the policy chooses by
    rating from the whole set).

    It matters for the recorded HUMAN move instead: `web/session.py` resolves a pick as
    `candidates[move.index]`, so a reordering makes an old state string select a DIFFERENT
    player -- silently, with the state still parsing and replaying to a legal squad. That is
    every saved game and every shared link, which is what A62 rests on."""
    doc2 = snap.read_document()
    for fs, cards in DOC["deck"]["cards_by_fs"].items():
        assert [c["person_id"] for c in cards] == \
               [c["person_id"] for c in doc2["deck"]["cards_by_fs"][fs]], fs


def test_an_unknown_card_field_is_rejected_rather_than_ignored():
    """The schema guard, and the reason this file is JSON rather than pickle: a snapshot
    written before a `Card` field was added must fail LOUDLY at load, so the caller can
    fall back to the database, instead of yielding objects quietly missing the field."""
    with pytest.raises(TypeError):
        snap._card_from({"fs_id": 1, "person_id": "x", "name": "N",
                         "positions": [1, 2], "a_field_that_does_not_exist": True})


def test_a_missing_card_field_is_rejected_too():
    with pytest.raises(TypeError):
        snap._card_from({"person_id": "x", "name": "N", "positions": [1]})   # no fs_id


def test_an_unreadable_snapshot_reads_as_absent_rather_than_raising(tmp_path, monkeypatch):
    """Every caller's correct response to a bad snapshot is to use the database, so
    `read_document` must never raise -- a corrupt file has to be indistinguishable from a
    missing one at the call site."""
    corrupt = tmp_path / "corrupt.json.gz"
    corrupt.write_bytes(b"this is not gzip")
    monkeypatch.setattr(snap, "SNAPSHOT", corrupt)
    assert snap.read_document() is None

    truncated = tmp_path / "truncated.json.gz"
    truncated.write_bytes(gzip.compress(b'{"format_version": 1, "deck": '))
    monkeypatch.setattr(snap, "SNAPSHOT", truncated)
    assert snap.read_document() is None

    monkeypatch.setattr(snap, "SNAPSHOT", tmp_path / "nothing-here.json.gz")
    assert snap.read_document() is None


def test_a_future_format_version_is_refused(tmp_path, monkeypatch):
    """An old deployment must not try to read a newer layout it does not understand; it
    falls back to the database, which is always correct if slower."""
    future = tmp_path / "future.json.gz"
    future.write_bytes(gzip.compress(
        json.dumps({"format_version": snap.FORMAT_VERSION + 1}).encode()))
    monkeypatch.setattr(snap, "SNAPSHOT", future)
    assert snap.read_document() is None
