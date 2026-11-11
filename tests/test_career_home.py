"""Your player's home batting number in Build a Career (A251): from his batting band, the
same at every club and every season, and an Impact Player never moves him."""

from __future__ import annotations

import dataclasses

import pytest

from game import career as C
from game.season import _insert_batting_impact
from tools.snapshot_deck import deck_from, model_from, read_document

DOC = read_document()
pytestmark = [pytest.mark.skipif(DOC is None, reason="no deck snapshot"),
              pytest.mark.usefixtures("no_incidents")]


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


def test_the_home_number_comes_from_the_batting_band():
    assert C.home_number(_plain("x", {1, 2, 3})) == 1
    assert C.home_number(_plain("x", {3, 4, 5})) == 4
    assert C.home_number(_plain("x", {5, 6, 7})) == 6
    assert C.home_number(_plain("x", {8, 9, 10, 11})) is None     # the tail is not pinned


def _plain(pid, positions, base=None):
    base = base or _BASE
    return dataclasses.replace(base, person_id=pid, name=pid, positions=frozenset(positions))


_BASE = None


@pytest.fixture(scope="module", autouse=True)
def _base(deck):
    global _BASE
    _BASE = deck.cards_by_fs[C.year_fs_ids(deck, 2015)[0]][0]


def _positions(year):
    out = []
    for r in year.results:
        if year.yours not in (r.home, r.away):
            continue
        inn = r.home_innings if r.home is year.yours else r.away_innings
        out += [i for i, b in enumerate(inn.batting, 1) if b.player.person_id == C.YOU_ID]
    return out


@pytest.mark.parametrize("role", ["opener", "middle", "finisher", "keeper", "allrounder"])
def test_you_bat_at_your_home_number_at_every_club_and_every_season(deck, model, role):
    start, ceiling = C.spin(deck, 4, role)
    expected = C.HOME_NUMBER[frozenset(start.positions)]   # every one of these has a band
    seen = set()
    for yr in (2012, 2015, 2018, 2022):
        for fs in C.year_fs_ids(deck, yr)[:3]:
            c0 = deck.cards_by_fs[fs][0]
            you = C.ability(start, ceiling, 0.5, setup=C.Setup("T", role, 2012, False),
                            fs_id=fs, year=c0.season_year, franchise=c0.franchise)
            year = C.simulate_year(deck, model, 1, 1, you, rating=80, tenure=3)
            seen.update(_positions(year))
            assert year.yours.pinned == {C.YOU_ID: expected}
            assert C.where_you_play(deck, you, fs)[0] == expected
    assert seen == {expected}


def test_an_impact_player_bats_around_a_pinned_player():
    """A top-order Impact Player comes in at 1 and would push everyone down a place; the
    pinned player stays at 4 and the order closes up around him."""
    xi = [_plain(f"p{i}", {1, 2, 3} if i < 3 else {8, 9, 10, 11}) for i in range(11)]
    xi[3] = _plain("you", {3, 4, 5})
    target, impact = xi[10], _plain("imp", {1, 2, 3})
    free = _insert_batting_impact(xi, target, impact)
    assert [c.person_id for c in free].index("you") == 4          # pushed down unpinned
    pinned = _insert_batting_impact(xi, target, impact, {"you": 4})
    ids = [c.person_id for c in pinned]
    assert ids.index("you") == 3 and ids[0] == "imp" and len(ids) == 11
    assert sorted(ids) == sorted(c.person_id for c in free)


def test_a_tail_ender_is_not_pinned(deck, model):
    seed = next(s for s in range(50) if min(C.spin(deck, s, "spin")[0].positions) >= 8)
    start, ceiling = C.spin(deck, seed, "spin")
    fs = C.year_fs_ids(deck, 2015)[0]
    c0 = deck.cards_by_fs[fs][0]
    you = C.ability(start, ceiling, 0.5, setup=C.Setup("T", "spin", 2012, False), fs_id=fs,
                    year=c0.season_year, franchise=c0.franchise)
    sides, yours = C.year_sides(deck, 2015, you)
    assert yours.pinned is None


# ---- the rise (A251) ----------------------------------------------------------------------

def test_a_batting_number_runs_from_the_foot_of_the_band_to_its_home():
    top, mid, fin = (_plain("x", b) for b in ({1, 2, 3}, {3, 4, 5}, {5, 6, 7}))
    assert [C.batting_number(top, p) for p in range(4)] == [3, 2, 1, 1]
    assert [C.batting_number(mid, p) for p in range(3)] == [5, 4, 4]
    assert [C.batting_number(fin, p) for p in range(3)] == [7, 6, 6]
    assert C.batting_number(_plain("x", {8, 9, 10, 11}), 2) is None


def test_promotion_is_one_place_for_a_mark_cleared_outside_a_poor_season():
    a, b = C.PROMOTE_AT
    assert C.next_promotions(0, a, a, poor=False, declining=False) == 1
    assert C.next_promotions(0, a - 1, a, poor=False, declining=False) == 0
    assert C.next_promotions(0, 99, 99, poor=False, declining=False) == 1    # one at a time
    assert C.next_promotions(1, b, b, poor=False, declining=False) == 2
    assert C.next_promotions(0, 99, 99, poor=True, declining=False) == 0     # not in a poor one
    assert C.next_promotions(2, 99, 99, poor=False, declining=False) == 2    # home is the top


def test_a_place_is_lost_only_on_a_sharp_decline():
    drop = C.DEMOTE_DROP
    assert C.next_promotions(2, 90 - drop, 90, poor=True, declining=True) == 1
    assert C.next_promotions(2, 90 - drop + 1, 90, poor=True, declining=True) == 2   # not sharp
    assert C.next_promotions(2, 60, 90, poor=False, declining=True) == 2     # not poor
    assert C.next_promotions(2, 60, 90, poor=True, declining=False) == 2     # not past the peak
    assert C.next_promotions(0, 60, 90, poor=True, declining=True) == 0      # nowhere to go


def test_a_season_is_played_at_the_number_it_is_given(deck, model):
    start, ceiling = C.spin(deck, 4, "middle")
    fs = C.year_fs_ids(deck, 2015)[0]
    c0 = deck.cards_by_fs[fs][0]
    you = C.ability(start, ceiling, 0.5, setup=C.Setup("T", "middle", 2012, False), fs_id=fs,
                    year=c0.season_year, franchise=c0.franchise)
    year = C.simulate_year(deck, model, 1, 1, you, rating=80, tenure=3, bat_at=5)
    assert set(_positions(year)) == {5} and year.yours.pinned == {C.YOU_ID: 5}
    assert C.where_you_play(deck, you, fs, 5)[0] == 5


# Seed 1 until A259; re-found after A259 re-estimated every card's engine numbers (seed 1's
# opener now stalls at No. 3 for five seasons). Seed 5 goes 3, 3, 3, 3, 2, then No. 1 for
# seven seasons and back to 2 in the last, with nine pre-season offers.
# 5 -> 0 re-found after A261 (seed 5 now ends its career at No. 1): seed 0 goes 3, 3, 2, 2, then
# No. 1 for eight seasons, then 2, 2, 3, 3, 3.
RISEN_SEED = 0


@pytest.fixture(scope="module")
def risen(no_incidents, deck, model):
    """An opener's career that climbs to No. 1 and falls back, found by search. Built here
    with the coach, phases and career leans off: a module fixture is set up before conftest's per-test
    switches, and the tests replay it with them off. Seed RISEN_SEED (re-found after A259,
    and again after A261)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(C, "COACH_ON", False)
        mp.setattr(C, "PHASE_ON", False)
        mp.setattr(C, "LEANS_ON", False)       # [A277] the career leans, likewise
        car, moves = C.auto_career(deck, model, RISEN_SEED, C.Setup("T", "opener", 2010, False),
                                   "strength")
    car.moves = moves
    return car


def test_offers_say_where_you_would_bat_at_the_number_you_have_earned(deck, model, risen):
    """At every pre-season offer of the career, each offer's batting place is the number
    the season is then played at."""
    setup = C.Setup("T", "opener", 2010, False)
    checked = 0
    for n in range(len(risen.moves)):
        c = C.play_career(deck, model, RISEN_SEED, setup, risen.moves[:n])
        if c.pending is not None and c.pending.kind == "offer":
            season = risen.seasons[len(c.seasons)]
            assert {o.position for o in c.pending.offers} == {season.bat_at}, season.year
            checked += 1
    assert checked >= 5


def test_the_number_is_part_of_the_seasons_cache_key(deck, model, monkeypatch):
    """Two careers can reach one season with different places earned, so the season cache
    must tell them apart."""
    keys = []
    real = C._cached_season

    def spy(deck_, model_, seed, k, you, record, key, *a, **kw):
        keys.append((record.bat_at, key))
        return real(deck_, model_, seed, k, you, record, key, *a, **kw)
    monkeypatch.setattr(C, "_cached_season", spy)
    C.auto_career(deck, model, RISEN_SEED, C.Setup("T", "opener", 2010, False), "strength")
    assert keys and all(bat in key for bat, key in keys)
    assert len({bat for bat, _ in keys}) > 1


def test_a_career_rises_by_the_rule_and_says_so(risen):
    """Each season's number recomputed from the seasons before it, by hand: the foot of the
    top band, one place up for each PROMOTE_AT mark cleared outside a poor season, one down
    on a poor season past the peak DEMOTE_DROP below the best Rating."""
    prom, peak, seen = 0, 0, set()
    for r in risen.seasons:
        assert r.bat_at == max(1, 3 - prom)
        seen.add(r.bat_at)
        peak = max(peak, r.rating)
        poor = r.percentile < C.poor_bar("opener")
        before = prom
        if not poor and prom < 2 and r.rating >= (80, 84)[prom]:
            prom += 1
        elif poor and risen.shape.declining(r.clock) and prom > 0 and r.rating <= peak - 10:
            prom -= 1
        said_up = any(n.startswith("Promoted to No.") for n in r.notes)
        said_down = any(n.startswith("Moved down to No.") for n in r.notes)
        assert (said_up, said_down) == (prom > before, prom < before), r.year
    assert seen == {1, 2, 3}                 # the fixture climbs and falls


def test_a_career_saved_before_the_rise_is_placed_by_its_rating(risen):
    cp = C.Checkpoint.from_dict({k: v for k, v in risen.checkpoint.to_dict().items()
                                 if k != "promotions"})
    assert cp.promotions is None
    assert C.earned(cp) == C.promotions_for(cp.seasons[-1].rating)
    assert C.promotions_for(C.PROMOTE_AT[0] - 1) == 0 and C.promotions_for(C.PROMOTE_AT[1]) == 2


def test_the_declared_marks():
    """The marks as declared (A251), so moving one is a decision rather than a slip."""
    assert C.next_promotions(0, 80, 80, poor=False, declining=False) == 1
    assert C.next_promotions(0, 79, 79, poor=False, declining=False) == 0
    assert C.next_promotions(1, 84, 84, poor=False, declining=False) == 2
    assert C.next_promotions(1, 83, 83, poor=False, declining=False) == 1
    assert C.next_promotions(2, 80, 90, poor=True, declining=True) == 1
    assert C.next_promotions(2, 81, 90, poor=True, declining=True) == 2


def test_the_last_season_was_played_at_its_number(risen):
    """Where he really batted in the career's last season, read off its scorecards."""
    last = risen.seasons[-1]
    seen = set()
    for r in risen.detail.cards:
        for side, inn in ((r.home, r.home_innings), (r.away, r.away_innings)):
            if side.you:
                seen.update(i for i, b in enumerate(inn.batting, 1)
                            if b.player.person_id == C.YOU_ID)
    assert seen == {last.bat_at} and last.bat_at != 1       # not the band's home number


def test_a_career_saved_before_the_rise_keeps_what_its_rating_earned(risen):
    peak = max(range(len(risen.seasons)), key=lambda i: risen.seasons[i].rating)
    d = risen.checkpoint.to_dict()
    d.pop("promotions")
    d["seasons"] = d["seasons"][:peak + 1]
    cp = C.Checkpoint.from_dict(d)
    assert cp.seasons[-1].rating >= C.PROMOTE_AT[-1] and C.earned(cp) == len(C.PROMOTE_AT)


def test_the_places_earned_survive_the_checkpoint(risen):
    cp = C.Checkpoint.from_dict(risen.checkpoint.to_dict())
    for n in (0, 1, 2):
        cp.promotions = n
        assert C.Checkpoint.from_dict(cp.to_dict()).promotions == n
