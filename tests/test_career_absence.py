"""Absences in Build a Career (A216): only a player new to his club misses matches by chance,
and then by passport; a star's is capped; an event card's replaces the random one; and every
absence has a reason."""

from __future__ import annotations

import pytest

from game import career as C
from tools.snapshot_deck import deck_from, model_from, read_document

pytestmark = pytest.mark.usefixtures("no_incidents")   # A230: incidents are tested in test_career_story

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
    assert "international duty" in seen and seen & set(C.LEFT_OUT_OVERSEAS)
    for seed in range(200):
        _, reason = C.absence(seed, 1, 14, rating=80, overseas=False, role="spin")
        assert reason != "international duty"


# [A226] What a random absence may be called, by how long it is and what kind of season.
SELECTION = set(C.LEFT_OUT) | set(C.LEFT_OUT_OVERSEAS)
PERSONAL = {"illness", "a family matter"}


@pytest.mark.parametrize("overseas", [False, True])
def test_a_random_absence_is_never_an_injury_and_illness_is_always_short(overseas):
    """Injuries come from the injury cards alone. A long block is selection (or, overseas,
    international duty); illness, a family matter and a rest are only ever a short one."""
    seen = set()
    for seed in range(1500):
        for k in range(4):
            block, reason = C.absence(seed, k, 14, rating=80, overseas=overseas, role="pace")
            if not reason:
                continue
            seen.add(reason)
            allowed = SELECTION | ({"international duty"} if overseas else set())
            if len(block) <= C.SHORT_ABSENCE:
                allowed |= PERSONAL | {"rested"}
            assert reason in allowed, (len(block), reason)
    assert PERSONAL <= seen and "rested" in seen and seen & SELECTION
    # the overseas combination is an overseas player's reason, and only his
    assert ("the overseas combination" in seen) == overseas


def test_a_squad_seasons_absence_is_not_being_selected():
    reasons = {C.absence(seed, 3, 14, squad=True, rating=80, overseas=o)[1]
               for seed in range(300) for o in (False, True)}
    assert reasons - {None} == {C.NOT_SELECTED}


def test_every_label_reads_as_what_it_is():
    assert C.absence_words(C.NOT_SELECTED, 5, 3) == "Not selected for 5 matches (matches 3-7)."
    assert C.absence_words("rotation", 3, 4).startswith("Rotated out for 3 matches")
    assert C.absence_words("team balance", 4, 1).startswith("Left out for 4 matches for team balance")
    assert C.absence_words("illness", 1, 6) == "Illness kept you out of 1 match (match 6)."
    for reason in (C.NOT_SELECTED, "rotation", "team balance", "the overseas combination", "illness"):
        assert C.note_tone(C.absence_words(reason, 3, 2)) == ""          # information, not a cost


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


# ---- [A250] being left out waits for poor form -------------------------------------------

@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


def _newcomer(deck, fs, role="opener", overseas=False):
    start, ceiling = C.spin(deck, 4, role)
    c0 = deck.cards_by_fs[fs][0]
    return C.ability(start, ceiling, 0.5, setup=C.Setup("Tester", role, 2014, overseas),
                     fs_id=fs, year=c0.season_year, franchise=c0.franchise)


def _book_after(year, mine, n):
    """The form book straight after your n-th league match (1-based)."""
    book = C.FormBook(year.roster)
    book.add(year.results[:mine[n - 1] + 1])
    return book


def _poor(year, mine, n):
    """Recomputed here rather than read off `left_out_now`: last three in the worst third,
    the season so far in the worst quarter (a match on average against your kind), from
    your fifth match played."""
    book, key = _book_after(year, mine, n), (C.YOU_ID, id(year.yours))
    games = book.games.get(key, [])
    if len(games) < 5:
        return False
    kind = book.kind(key)
    peers = book._peers(key, kind, lambda gs: len(gs) >= 3)

    def rank(mine_, pool):
        return sum(p < mine_ for p in pool) / max(1, len(pool) - 1)
    last = rank(sum(book.score(kind, g) for g in games[-3:]),
                [sum(book.score(kind, g) for g in book.games[k][-3:]) for k in peers])
    avg = lambda gs: sum(book.score(kind, g) for g in gs) / len(gs)
    season = rank(avg(games), [avg(book.games[k]) for k in peers])
    return last < 1 / 3 and season < 1 / 4


def _seasons(deck, model, captain=False):
    """(year, mine, expected block, reason) for new arrivals across 2014 and 2018."""
    out = []
    for yr in (2014, 2018):
        for fs in C.year_fs_ids(deck, yr)[:4]:
            for overseas in (False, True):
                you = _newcomer(deck, fs, overseas=overseas)
                for seed in range(6):
                    year = C.simulate_year(deck, model, seed, 1, you, rating=78, tenure=0,
                                           captain=captain)
                    mine = [i for i, r in enumerate(year.results)
                            if r.stage == "league" and year.yours in (r.home, r.away)]
                    block, reason = C.absence(seed, 1, len(mine), False, 0,
                                              C.FORM_DROP_RATE["batter"], 78, overseas,
                                              "batter", 0)
                    out.append((year, mine, block, reason))
    return out


@pytest.fixture(scope="module")
def seasons(deck, model):
    return _seasons(deck, model)


def test_a_newcomer_is_left_out_only_straight_after_his_form_turns_poor(seasons):
    waited = never = 0
    for year, mine, block, reason in seasons:
        if reason not in C.FORM_LEFT_OUT:
            continue
        if not year.absent:
            assert year.absence_reason is None
            never += 1
            # never out of form at any reading he played behind
            for n in range(1, len(mine)):
                assert not _poor(year, mine, n)
            continue
        s = year.absent[0]                        # league index the block starts at
        assert year.absent == list(range(s, min(s + len(block), len(mine))))
        assert year.absence_reason == reason
        assert _poor(year, mine, s)
        # and not at any reading before it -- unless he was DROPPED from the next match
        # (A205): he cannot be left out of a match he is already out of, so the block waits
        # for his return, and a drop adds to it rather than standing in for it (A206).
        # Before A259 this sample never put a drop at that moment; A259 found one
        # (Sunrisers 2018, seed 3: poor after matches 9 and 10, dropped for 10 and 11).
        for n in range(1, s):
            assert not _poor(year, mine, n) or (n + 1) in year.dropped
        waited += 1
    assert waited >= 5 and never >= 5             # both happen in this sample


def test_other_absences_still_fall_where_they_were_drawn(seasons):
    seen = 0
    for year, mine, block, reason in seasons:
        if reason and reason not in C.FORM_LEFT_OUT:
            assert year.absent == list(block) and year.absence_reason == reason
            seen += 1
    assert seen >= 3


def test_a_captain_is_never_left_out_for_selection(deck, model):
    for year, mine, block, reason in _seasons(deck, model, captain=True):
        if reason in C.FORM_LEFT_OUT:
            assert year.absent == [] and year.absence_reason is None
