"""Absences in Build a Career (A216): only a player new to his club misses matches by chance,
and then by passport; a star's is capped; an event card's replaces the random one; and every
absence has a reason."""

from __future__ import annotations

import pytest

from game import career as C
from tools.snapshot_deck import deck_from, model_from, read_document

DOC = read_document()


def test_availability_follows_seasons_at_the_club_and_passport():
    assert C.availability_shares(None, False) is C.AVAILABILITY_SHARES
    assert C.availability_shares(0, False) is C.AVAILABILITY_BY_TENURE[(0, False)]
    assert C.availability_shares(1, True) is C.AVAILABILITY_BY_TENURE[(1, True)]
    for tenure in (C.SETTLED_SEASONS, C.SETTLED_SEASONS + 5):
        for overseas in (False, True):
            assert C.availability_shares(tenure, overseas) == C.SETTLED_SHARES


def test_a_settled_player_misses_nothing_by_chance():
    for seed in range(400):
        for overseas in (False, True):
            block, reason = C.absence(seed, 3, 14, rating=78, overseas=overseas, role="pace",
                                      tenure=C.SETTLED_SEASONS)
            assert len(block) == 0 and reason is None
            # an injury card still keeps him out
            hurt, _ = C.absence(seed, 3, 14, extra=3, rating=78, overseas=overseas,
                                role="pace", tenure=C.SETTLED_SEASONS)
            assert len(hurt) == 3
    # a new arrival does miss matches, and an overseas one more often
    new = [len(C.absence(s, 3, 14, rating=78, tenure=0)[0]) > 0 for s in range(400)]
    new_os = [len(C.absence(s, 3, 14, rating=78, overseas=True, tenure=0)[0]) > 0
              for s in range(400)]
    assert 0.5 < sum(new) / 400 < sum(new_os) / 400
    # a squad season is not a settled regular's: he still plays a squad player's share
    sq = [len(C.absence(s, 3, 14, squad=True, rating=78, tenure=C.SETTLED_SEASONS)[0])
          for s in range(200)]
    assert sum(sq) / 200 > 5


def test_a_star_loses_at_most_a_quarter_of_the_league_to_chance():
    cap = int(C.STAR_MAX_ABSENCE * 14)
    for seed in range(300):
        block, _ = C.absence(seed, 3, 14, rating=95, overseas=True, role="pace", tenure=0)
        assert len(block) <= cap
    assert max(len(C.absence(s, 3, 14, rating=75, overseas=True, role="pace")[0])
               for s in range(300)) > cap


def test_an_event_cards_absence_replaces_the_random_one():
    for seed in range(200):
        block, reason = C.absence(seed, 2, 14, extra=4, rating=80, role="batter")
        assert len(block) == 4 and reason is None
        sq, _ = C.absence(seed, 2, 14, squad=True, extra=2, rating=80)
        base, _ = C.absence(seed, 2, 14, squad=True, rating=80)
        assert len(sq) == max(2, len(base))
    # without a Rating (older callers) the old rule still adds them
    assert len(C.missed_block(5, 2, 14, extra=4)) == min(14, len(C.missed_block(5, 2, 14)) + 4)


def test_every_random_absence_has_a_reason_and_duty_sits_at_an_end():
    seen = set()
    for seed in range(400):
        block, reason = C.absence(seed, 1, 14, rating=80, overseas=True, role="spin")
        assert (reason is not None) == bool(block)
        if reason:
            seen.add(reason)
        if reason == "international duty":
            assert block.start == 0 or block.stop == 14
    assert "international duty" in seen and seen & set(C.INJURIES["spin"])
    for seed in range(200):
        _, reason = C.absence(seed, 1, 14, rating=80, overseas=False, role="spin")
        assert reason != "international duty"


def _rec(franchise):
    return C.SeasonRecord(k=0, year=2010, franchise=franchise, short="", price=0, stayed=False,
                          event="", choice="", captain=False, ability=0.0, teams=0)


def test_seasons_at_a_club_count_back_from_the_last_and_one_club_keeps_its_count():
    seasons = [_rec("Mumbai Indians"), _rec("Delhi Daredevils"), _rec("Delhi Daredevils"),
               _rec("Delhi Capitals")]
    assert C.tenure_at(seasons, "Delhi Capitals") == 3          # a rename is one club
    assert C.tenure_at(seasons, "Mumbai Indians") == 0          # a move starts the count again
    assert C.tenure_at([], "Mumbai Indians") == 0


# --- wired into a real career -----------------------------------------------------------------


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


@pytest.mark.skipif(DOC is None, reason="no deck snapshot")
def test_a_career_at_one_club_stops_missing_matches_once_settled(deck, model):
    car, _ = C.auto_career(deck, model, 6, C.Setup("Tester", "middle", 2010, True), "loyal")
    seasons = car.seasons
    settled = [r for r in seasons
               if r.tenure >= C.SETTLED_SEASONS and not r.squad]
    assert len(settled) >= 3, "the fixture must stay settled at one club for a while"
    for i, r in enumerate(seasons):
        assert r.tenure == C.tenure_at(seasons[:i], r.franchise)
    for r in settled:
        assert r.missed - r.dropped == r.extra_missed and not (r.absence and not r.extra_missed)


@pytest.mark.skipif(DOC is None, reason="no deck snapshot")
def test_seasons_at_the_club_are_part_of_the_season_cache_key(deck, model, monkeypatch):
    seen = []
    real = C._cached_season

    def spy(deck, model, seed, k, you, record, key, *a, **kw):
        seen.append((record.tenure, key))
        return real(deck, model, seed, k, you, record, key, *a, **kw)

    monkeypatch.setattr(C, "_cached_season", spy)
    C.auto_career(deck, model, 6, C.Setup("Tester", "middle", 2010, True), "loyal")
    assert seen and all(key[-1] == tenure for tenure, key in seen)
    assert max(t for t, _ in seen) >= C.SETTLED_SEASONS
