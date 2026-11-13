"""The contract cycle in Build a Career (A288): trials and an uncapped auction at a debut, a
deal that runs to the next mega auction, and a release into the mini auction."""

from __future__ import annotations

import random

import pytest
from fastapi.testclient import TestClient

from etl.franchise_map import canonical
from game import career as C
from tools.snapshot_deck import deck_from, model_from, read_document

CYCLE_TESTS = True          # conftest leaves the contract cycle on for this module
DOC = read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="no deck snapshot")

# Found by search (A288): an opener whose debut club is Deccan Chargers 2012, which has no team
# in 2013, so his second season is a mini auction he did nothing to deserve.
FOLDED = (1, C.Setup("X", "opener", 2012), "loyal")
# ...and a fast bowler released after 2018 -- a deal signed at the 2018 mega auction, so
# contract years remained -- who goes into the 2019 mini auction. [A289] Re-found (was seed 1,
# released after 2025) once the trade window gave the careless player more to answer.
RELEASED = (5, C.Setup("X", "pace", 2015), "random")
LOST_AT = 1                 # ...whose second season is neither a star's nor one ending in a release


@pytest.fixture(scope="module", autouse=True)
def cycle_on():
    """On for the module before any module-scoped career below is built (conftest has it off
    for the session; A251's trap)."""
    names = {"CYCLE_ON": True, "COACH_ON": False, "PHASE_ON": False, "LEANS_ON": False,
             "DRILLS_ON": False, "KEEPER_ASK_CHANCE": 0.0}   # ...and the rest as conftest has them
    was = {n: getattr(C, n) for n in names}
    for n, v in names.items():
        setattr(C, n, v)
    yield
    for n, v in was.items():
        setattr(C, n, v)


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


def walk(deck, model, seed, setup, policy="random", stop=None):
    """An automatic career played the way the API plays one: each request resumes from the last
    checkpoint with only the moves since it. Returns the career where it stopped, every move,
    and every pending decision on the way."""
    rng = random.Random(f"{seed}:t")
    cp, since, every, seen = None, [], [], []
    while True:
        car = C.play_career(deck, model, seed, setup, since, resume=cp)
        if car.complete or (stop is not None and stop(car)):
            return car, every, seen
        seen.append(car.pending)
        # each request keeps only its own season's moves: the checkpoint is where it began
        if car.pending.kind != "epilogue":
            assert car.checkpoint.k == car.pending.k, (car.pending.kind, car.pending.year)
        move = C.policy_move(car, policy, rng)
        cp, since = car.checkpoint, since[car.ck_at:] + [move]
        every.append(move)


@pytest.fixture(scope="module")
def released(deck, model):
    seed, setup, policy = RELEASED
    return walk(deck, model, seed, setup, policy)


def test_a_deal_runs_to_the_next_mega_auction():
    assert [C.next_mega(y) for y in (2008, 2011, 2012, 2015, 2017, 2018, 2025)] == \
        [2011, 2014, 2014, 2018, 2018, 2022, 2027]
    c = C.cycle_contract("Mumbai Indians", 95, 2012)
    assert (c.left, c.years, c.price) == (2, 2, 95)
    assert C.cycle_contract("x", 20, 2015).left == 3
    assert C.cycle_contract("x", 20, 2017).left == 1


def test_a_debut_starts_with_four_trials_and_cannot_be_skipped(deck, model):
    seed, setup, _ = FOLDED
    car = C.play_career(deck, model, seed, setup, [])
    p = car.pending
    assert p.kind == "trials"
    clubs = [o.fs_id for o in p.offers]
    assert len(clubs) == 4 and len(set(clubs)) == 4
    assert set(clubs) <= set(C.year_fs_ids(deck, setup.debut))
    for bad in ("r", "v00", "v0", "v04", "v012", "o0"):
        with pytest.raises(C.CareerError):
            C.play_career(deck, model, seed, setup, [bad])


def _first_notes(deck, model, seed, setup, pick):
    car, _, _ = walk_from(deck, model, seed, setup, [pick])
    return car.seasons[0]


def walk_from(deck, model, seed, setup, first):
    """Play one season after the given first moves, answering the rest with the careless player."""
    rng = random.Random(seed)
    moves = list(first)
    while True:
        car = C.play_career(deck, model, seed, setup, moves)
        if car.seasons or car.complete:
            return car, moves, None
        moves.append(C.policy_move(car, "random", rng))


def test_how_a_trial_goes_comes_from_the_seed_not_from_which_trials_you_choose(deck, model):
    seed, setup, _ = FOLDED
    invites = C.play_career(deck, model, seed, setup, []).pending.offers
    a = _first_notes(deck, model, seed, setup, "v01")
    b = _first_notes(deck, model, seed, setup, "v02")
    first = invites[0].franchise
    line_a = [n for n in a.notes if first in n and "trial" in n]
    line_b = [n for n in b.notes if first in n and "trial" in n]
    assert len(line_a) == len(line_b) == 1 and line_a == line_b
    # ...and the notes name exactly the two trials attended
    named = {o.franchise for o in invites if any(o.franchise in n and "trial" in n for n in a.notes)}
    assert named == {invites[0].franchise, invites[1].franchise}


def test_only_the_clubs_that_saw_you_bid_when_none_scouts(deck, model, monkeypatch):
    monkeypatch.setattr(C, "SCOUTED", 0.0)
    year = 2014
    fss = C.year_fs_ids(deck, year)
    for seed in range(12):
        attended = random.Random(seed).sample(fss, 2)
        a, reports = C.debut_auction(deck, seed, year, 150.0, attended, {})
        bidders = {f for f, _s, _p in a.bids}
        names = {deck.cards_by_fs[fs][0].franchise for fs in attended}
        assert bidders <= names
        assert {r.fs_id for r in reports} == set(attended)
        assert a.price >= C.DEBUT_PRICE and a.kind == "debut"
        assert a.franchise in names


def test_a_club_short_in_your_band_bids_more(deck):
    year, fss = 2014, C.year_fs_ids(deck, 2014)
    higher = 0
    for seed in range(10):
        attended = fss[:2]
        plain, _ = C.debut_auction(deck, seed, year, 150.0, attended, {})
        keen, _ = C.debut_auction(deck, seed, year, 150.0, attended, {fs: "top" for fs in fss})
        assert keen.price >= plain.price
        higher += keen.price > plain.price
    assert higher > 0


def test_band_need_counts_regulars_against_the_stated_shortage(deck):
    short = {"top": 4, "middle": 5, "finisher": 2, "pace": 2, "spin": 1}
    slots = {"top": {1, 2, 3}, "middle": {3, 4, 5}, "finisher": {5, 6, 7}}
    opener = C.Card(fs_id=0, person_id=C.YOU_ID, name="you", role="batter",
                    positions=frozenset({1, 2, 3}), bat=0.0, display=75)
    quick = C.Card(fs_id=0, person_id=C.YOU_ID, name="you", role="bowler", bowl=0.0,
                   bowling_style="pace", positions=frozenset({8, 9, 10, 11}), display=75)
    saw_short = {"top": 0, "pace": 0}
    for fs, cards in deck.cards_by_fs.items():
        tops = sum((c.bat_balls or 0) >= 100 and c.role != "bowler"
                   and bool(set(c.positions or ()) & slots["top"]) for c in cards)
        paces = sum((c.bowl_balls or 0) >= 150 and c.bowling_style == "pace" for c in cards)
        assert (C.band_need(deck, opener, fs) == "top") == (tops <= short["top"])
        assert (C.band_need(deck, quick, fs) == "pace") == (paces <= short["pace"])
        saw_short["top"] += tops <= short["top"]
        saw_short["pace"] += paces <= short["pace"]
    assert all(0 < n < len(deck.cards_by_fs) for n in saw_short.values())


def test_a_contract_year_asks_nothing_and_every_move_has_a_reason(deck, released):
    car, _, seen = released
    assert all(p.kind != "offer" for p in seen)
    seasons = car.seasons
    for prev, now in zip(seasons, seasons[1:]):
        if canonical(prev.franchise) == canonical(now.franchise):
            if now.year not in C.MEGA_YEARS:
                assert now.price == prev.price        # the deal's price, every year of it
            continue
        folded = C._club_fs(deck, now.year, canonical(prev.franchise)) is None
        let_go = any("release" in n for n in prev.notes)
        assert now.year in C.MEGA_YEARS or folded or let_go or now.event == "trade", \
            (prev.year, prev.franchise, now.year, now.franchise)


def test_a_release_goes_to_the_mini_auction_even_with_a_contract_year_left(deck, model):
    seed, setup, policy = RELEASED
    car, _, _ = walk(deck, model, seed, setup, policy, stop=lambda c: c.pending is not None
                     and c.pending.kind == "mega" and c.pending.mega.kind == "mini")
    p, cp = car.pending, car.checkpoint
    assert p.mega.why == "released" and p.mega.retain is None
    last = car.seasons[-1]
    assert p.year not in C.MEGA_YEARS and C.next_mega(last.year) > p.year   # a year remained
    assert cp.released_by == canonical(last.franchise) and cp.contract is None
    assert any("release" in n for n in last.notes)


def test_the_club_that_released_you_does_not_bid(deck):
    year = 2016
    fss = C.year_fs_ids(deck, year)
    gone = canonical(deck.cards_by_fs[fss[0]][0].franchise)
    for seed in range(15):
        a = C.mini_auction(deck, seed, 3, year, gone, 86, 30, capped=True)
        assert a.kind == "mini"
        assert all(canonical(f) != gone for f, _s, _p in a.bids)
        assert canonical(a.franchise) != gone


def test_released_and_unsold_ends_the_career_but_a_folded_club_is_signed_late(deck):
    year, fss = 2016, C.year_fs_ids(deck, 2016)
    gone = canonical(deck.cards_by_fs[fss[0]][0].franchise)
    ceilings = C.mega_ceilings(deck, 7, 3, year, None, 72, capped=False)
    assert max(ceilings.values()) < 200          # nobody will meet a ₹2 cr base
    out = C.mini_auction(deck, 7, 3, year, gone, 72, 200, capped=False)
    assert out.fs_id < 0 and not out.sold and not out.bids
    late = C.mini_auction(deck, 7, 3, year, None, 72, 200, capped=False)
    assert late.fs_id in fss and not late.sold and late.price == 200


def test_a_club_that_folds_sends_its_player_to_the_mini_auction(deck, model):
    seed, setup, policy = FOLDED
    car, _, _ = walk(deck, model, seed, setup, policy, stop=lambda c: len(c.seasons) >= 1)
    assert car.pending.kind == "mega" and car.pending.mega.kind == "mini"
    assert car.pending.mega.why == "folded"
    assert car.checkpoint.released_by is None


def test_resuming_from_each_checkpoint_plays_the_career_a_full_replay_plays(deck, model, released):
    seed, setup, _ = RELEASED
    car, every, _ = released
    full = C.play_career(deck, model, seed, setup, every)
    key = lambda r: (r.year, r.franchise, r.price, r.runs, r.wickets, r.matches, r.event)
    assert [key(r) for r in full.seasons] == [key(r) for r in car.seasons]
    assert full.checkpoint.to_dict() == car.checkpoint.to_dict()


def _next_season(deck, model, seed, setup, policy, cp):
    rng, moves = random.Random(3), []
    while True:
        out = C.play_career(deck, model, seed, setup, moves, resume=cp)
        if len(out.seasons) > len(cp.seasons) or out.complete:
            return out
        moves.append(C.policy_move(out, policy, rng))


def test_a_club_that_has_lost_patience_releases_you(deck, model):
    seed, setup, policy = RELEASED
    car, _, _ = walk(deck, model, seed, setup, policy, stop=lambda c: len(c.seasons) >= LOST_AT
                     and c.pending is not None)
    base = C.Checkpoint.from_dict(car.checkpoint.to_dict())
    warm = _next_season(deck, model, seed, setup, policy, base)
    played = warm.seasons[len(base.seasons)]
    # the fixture: a season that would not have ended in a release on form, at the same club
    assert warm.checkpoint.released_by is None and played.rating < C.STAR_RATING
    assert canonical(played.franchise) == base.current
    cold = C.Checkpoint.from_dict(base.to_dict())
    cold.standing = -3
    out = _next_season(deck, model, seed, setup, policy, cold)
    assert out.checkpoint.released_by == canonical(out.seasons[-1].franchise)
    assert any("lost patience" in n for n in out.seasons[-1].notes)


def test_the_api_starts_a_career_at_its_trials(deck, model):
    from web.app import app
    seed, setup = 1, C.Setup("X", "spin", 2012)     # found by search: two clubs short of spinners
    want = [o.need for o in C.play_career(deck, model, seed, setup, []).pending.offers]
    assert any(want) and not all(want)       # the fixture shows both kinds of club
    with TestClient(app) as client:
        d = client.post("/api/career/start", json={"name": "X", "role": "spin",
                                                    "debut": 2012, "seed": seed}).json()
        p = d["pending"]
        assert p["kind"] == "trials" and len(p["offers"]) == 4 and not p["can_retire"]
        assert [o["need"] for o in p["offers"]] == want
        r = client.post("/api/career/move", json={"token": d["token"], "move": "v01"}).json()
        assert r["auction"]["kind"] == "debut"
        assert r["pending"]["kind"] != "trials"
