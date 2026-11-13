"""The trade window in Build a Career (A289): an approach from a club that needs you, your
own club shopping you, and a trade you ask for yourself."""

from __future__ import annotations

import random

import pytest
from fastapi.testclient import TestClient

from etl.franchise_map import canonical
from game import career as C
from tools.snapshot_deck import deck_from, model_from, read_document

CYCLE_TESTS = True
DOC = read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="no deck snapshot")

SETUPS = [C.Setup("X", role, 2009 + i) for i, role in
          enumerate(["opener", "middle", "finisher", "keeper", "pace", "spin", "allrounder"])]


@pytest.fixture(scope="module", autouse=True)
def cycle_on():
    names = {"CYCLE_ON": True, "COACH_ON": False, "PHASE_ON": False, "LEANS_ON": False,
             "DRILLS_ON": False, "KEEPER_ASK_CHANCE": 0.0}
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


def until_window(deck, model, seed, setup, after=0, policy="loyal"):
    """Play on (the careful player) to the first trade window after `after` seasons."""
    rng = random.Random(seed)
    cp, since = None, []
    while True:
        car = C.play_career(deck, model, seed, setup, since, resume=cp)
        if car.complete:
            return None, cp, since
        if car.pending.kind == "window" and car.pending.k >= after:
            return car, car.checkpoint, since[car.ck_at:]
        move = C.policy_move(car, policy, rng)
        cp, since = car.checkpoint, since[car.ck_at:] + [move]


def answer(deck, model, seed, setup, cp, since, move, policy="loyal"):
    """Answer the window with `move`, then play the season out."""
    rng = random.Random(seed + 1)
    moves = since + [move]
    while True:
        car = C.play_career(deck, model, seed, setup, moves, resume=cp)
        if len(car.seasons) > len(cp.seasons) or car.complete:
            return car
        moves.append(C.policy_move(car, policy, rng))


def test_a_window_opens_only_in_an_ordinary_contract_year(deck, model):
    for setup in SETUPS[:4]:
        seen = []
        rng, cp, since = random.Random(7), None, []
        while True:
            car = C.play_career(deck, model, 7, setup, since, resume=cp)
            if car.complete:
                break
            seen.append(car.pending)
            move = C.policy_move(car, "random", rng)
            cp, since = car.checkpoint, since[car.ck_at:] + [move]
        windows = [p for p in seen if p.kind == "window"]
        assert windows
        for p in windows:
            assert p.k >= C.WINDOW_FROM and p.year not in C.MEGA_YEARS
        mini = {p.year for p in seen if p.kind == "mega"}
        assert not mini & {p.year for p in windows}
        # ...and the old trade card never comes up under the cycle (the window shops you)
        assert all(r.event != "trade" for r in car.seasons)


def test_the_old_trade_card_never_fits_under_the_cycle():
    card = next(e for e in C.EVENTS if e.key == "trade")
    assert not C.event_fits(card, C.EventContext(staying=True, k=5))


def test_every_approach_comes_from_a_club_that_needs_you(deck, model, monkeypatch):
    monkeypatch.setattr(C, "APPROACH_CHANCE", 1.0)
    monkeypatch.setattr(C, "SHOP_CHANCE", 1.0)
    kinds = set()
    for setup in SETUPS:
        car, cp, _ = until_window(deck, model, 3, setup)
        if car is None:
            continue
        here_club = canonical(car.pending.offers[0].franchise)
        rating = C.rating_at(car.start, car.top, C.career_t(cp, car.start, car.top, car.shape))
        for e in car.pending.window["events"]:
            kinds.add(e["kind"])
            o = e["offer"]
            assert canonical(o.franchise) != here_club
            if e["kind"] in ("higher", "bowl"):
                assert o.need, e        # the club is short of what it wants you for
            if e["kind"] == "captain":
                assert rating >= C.STAR_RATING and cp.k >= C.CAPTAIN_FROM
            if e["kind"] == "shopped":
                assert cp.standing <= 0 and rating < C.STAR_RATING
    assert {"higher", "shopped"} <= kinds


def test_only_a_club_gone_cold_on_you_shops_you(deck, monkeypatch):
    monkeypatch.setattr(C, "SHOP_CHANCE", 1.0)
    monkeypatch.setattr(C, "APPROACH_CHANCE", 0.0)
    you = C.Card(fs_id=0, person_id=C.YOU_ID, name="you", role="batter", bat=0.0,
                 positions=C.MIDDLE_SLOTS, display=80)
    year = 2016
    club = canonical(deck.cards_by_fs[C.year_fs_ids(deck, year)[0]][0].franchise)
    shops = lambda cp, rating: any(a.kind == "shopped" for a in C.window_events(
        deck, 11, 3, year, you, club, SETUPS[1], cp, rating))
    assert shops(C.Checkpoint(k=3, standing=0), 80)
    assert not shops(C.Checkpoint(k=3, standing=1), 80)          # still in favour
    assert not shops(C.Checkpoint(k=3, standing=0), 85)          # a star
    assert not shops(C.Checkpoint(k=3, standing=0, captain=club), 80)   # its captain


def test_an_approach_never_takes_the_place_of_your_club_shopping_you(deck, monkeypatch):
    monkeypatch.setattr(C, "SHOP_CHANCE", 1.0)
    monkeypatch.setattr(C, "APPROACH_CHANCE", 1.0)
    you = C.Card(fs_id=0, person_id=C.YOU_ID, name="you", role="batter", bat=0.0,
                 positions=C.MIDDLE_SLOTS, display=80)
    year = 2016
    club = canonical(deck.cards_by_fs[C.year_fs_ids(deck, year)[0]][0].franchise)
    for seed in range(20):
        got = C.window_events(deck, seed, 3, year, you, club, SETUPS[1], C.Checkpoint(k=3), 80)
        assert [a.kind for a in got][-1:] == ["shopped"]
        assert any(a.kind == "higher" for a in got)       # the fixture: a club short of top order


def test_window_events_are_drawn_from_the_seed_alone(deck):
    you = C.Card(fs_id=0, person_id=C.YOU_ID, name="you", role="batter", bat=0.0,
                 positions=C.MIDDLE_SLOTS, display=80)
    cp = C.Checkpoint(k=3)
    year, club = 2016, canonical(deck.cards_by_fs[C.year_fs_ids(deck, 2016)[0]][0].franchise)
    a = C.window_events(deck, 11, 3, year, you, club, SETUPS[1], cp, 80)
    b = C.window_events(deck, 11, 3, year, you, club, SETUPS[1], cp, 80)
    assert a == b
    # the shop roll is drawn whatever the approach roll did, so one never moves the other
    rng = random.Random("11:window:3")
    first, second = rng.random(), rng.random()
    assert any(x.kind == "shopped" for x in a) == (second < C.SHOP_CHANCE)


def test_a_request_is_refused_by_a_club_that_trusts_you_and_unwanted_without_need(deck):
    fss = C.year_fs_ids(deck, 2016)
    you = C.Card(fs_id=0, person_id=C.YOU_ID, name="you", role="batter", bat=0.0,
                 positions=C.TOP_SLOTS, display=80)
    short = [fs for fs in fss if C.band_need(deck, you, fs) == "top"]
    full = [fs for fs in fss if C.band_need(deck, you, fs) is None]
    assert short and full
    trusted, cold = C.Checkpoint(standing=2), C.Checkpoint(standing=0)
    assert C.request_answer(deck, you, short[0], trusted, 80, False) == "refused"
    assert C.request_answer(deck, you, short[0], cold, 80, True) == "refused"   # the armband
    assert C.request_answer(deck, you, short[0], cold, 80, False) == "agreed"
    assert C.request_answer(deck, you, full[0], cold, 80, False) == "unwanted"
    assert C.request_answer(deck, you, full[0], cold, 85, False) == "agreed"    # a star
    assert C.WANTED_RATING == 85


def _request(deck, model):
    """A window where a request can go either way: not a star, not trusted by the club, and
    some clubs short where you play and some not."""
    for setup in SETUPS[1:4]:
        car, cp, since = until_window(deck, model, 5, setup, after=1)
        if car is None:
            continue
        rating = C.rating_at(car.start, car.top, C.career_t(cp, car.start, car.top, car.shape))
        clubs = car.pending.window["clubs"]
        if (rating < C.WANTED_RATING and cp.standing < C.STANDING_BACKED
                and any(o.need for o in clubs) and not all(o.need for o in clubs)):
            return setup, car, cp, since, clubs
    pytest.fail("no window where a request can go either way")


def test_asking_costs_goodwill_and_a_granted_request_carries_the_contract(deck, model):
    setup, car, cp, since, clubs = _request(deck, model)
    contract = cp.contract or car.checkpoint.contract
    here = car.pending.offers[0]
    for i, o in enumerate(clubs):
        out = answer(deck, model, 5, setup, cp, since, f"a{i}")
        played = out.seasons[len(cp.seasons)]
        moved = canonical(played.franchise) != canonical(here.franchise)
        notes = " ".join(played.notes)
        assert ("got it" in notes) == moved
        if moved:
            assert canonical(played.franchise) == canonical(o.franchise)
            assert played.price == here.price           # the fee goes with you
            assert played.contract_year == here.contract_year
            break
    else:
        pytest.fail("no club took the request")

    # a request that does not come off still costs goodwill, against staying put that season
    stay = answer(deck, model, 5, setup, cp, since, "x0")
    for i in range(len(clubs)):
        asked = answer(deck, model, 5, setup, cp, since, f"a{i}")
        if canonical(asked.seasons[-1].franchise) == canonical(here.franchise):
            assert asked.checkpoint.standing == stay.checkpoint.standing + C.REQUEST_STANDING
            assert C.REQUEST_STANDING == -1
            break
    else:
        pytest.fail("every request came off")


def _approach(deck, model, kind, monkeypatch):
    monkeypatch.setattr(C, "APPROACH_CHANCE", 1.0)
    monkeypatch.setattr(C, "APPROACH_WEIGHTS", {"higher": 1.0, "captain": 1.0, "bowl": 1.0})
    for setup in SETUPS:
        for seed in range(1, 25):
            car, cp, since = until_window(deck, model, seed, setup, after=1)
            if car is None:
                continue
            for i, e in enumerate(car.pending.window["events"]):
                if e["kind"] == kind:
                    return setup, seed, car, cp, since, i, e
    pytest.fail(f"no {kind} approach found")


def test_taking_an_approach_to_bat_higher_moves_you_up_a_band(deck, model, monkeypatch):
    setup, seed, car, cp, since, i, e = _approach(deck, model, "higher", monkeypatch)
    before = C.your_band(C.with_band(car.start, cp.up_band))
    out = answer(deck, model, seed, setup, cp, since, f"x{i + 1}")
    after = out.checkpoint.up_band
    assert after == C.UP_BAND[before]
    played = out.seasons[len(cp.seasons)]
    assert canonical(played.franchise) == canonical(e["offer"].franchise)
    assert played.bat_at in C.BAND_SLOTS[after]
    assert out.checkpoint.standing >= 0
    # ...and it lasts: the season after is played in the new band too
    nxt, rng, moves = out.checkpoint, random.Random(2), []
    while True:
        car2 = C.play_career(deck, model, seed, setup, moves, resume=nxt)
        if len(car2.seasons) > len(nxt.seasons) or car2.complete:
            break
        moves.append(C.policy_move(car2, "loyal", rng))
    later = car2.seasons[len(nxt.seasons)]
    assert later.bat_at in C.BAND_SLOTS[after], (later.year, later.bat_at)


def test_taking_an_approach_to_bowl_makes_you_an_allrounder(deck, model, monkeypatch):
    setup, seed, car, cp, since, i, e = _approach(deck, model, "bowl", monkeypatch)
    out = answer(deck, model, seed, setup, cp, since, f"x{i + 1}")
    assert out.checkpoint.roles[-1] == "allrounder"
    played = out.seasons[len(cp.seasons)]
    assert played.bowl_balls > 0 and played.role["role"] == "allrounder"


def test_turning_down_your_clubs_trade_costs_you_standing(deck, model, monkeypatch):
    monkeypatch.setattr(C, "SHOP_CHANCE", 1.0)
    monkeypatch.setattr(C, "APPROACH_CHANCE", 0.0)
    for setup in SETUPS:
        car, cp, since = until_window(deck, model, 9, setup, after=2)
        if car is None or not any(e["kind"] == "shopped" for e in car.pending.window["events"]):
            continue
        out = answer(deck, model, 9, setup, cp, since, "x0")
        played = out.seasons[len(cp.seasons)]
        assert any("ready to let you go" in n for n in played.notes)
        monkeypatch.setattr(C, "SHOP_CHANCE", 0.0)
        plain = answer(deck, model, 9, setup, cp, since, "x0")
        assert out.checkpoint.standing == plain.checkpoint.standing - 1
        return
    pytest.fail("no club shopped its player")


def test_the_api_shows_the_window(deck, model, monkeypatch):
    from web.app import app
    monkeypatch.setattr(C, "SHOP_CHANCE", 1.0)
    monkeypatch.setattr(C, "APPROACH_CHANCE", 1.0)
    car, cp, since = until_window(deck, model, 9, SETUPS[1], after=2)
    assert car.pending.window["events"]          # the fixture: something on the table
    from web import career_routes as R
    with TestClient(app):
        out = R._pending(car.pending, "middle", cp)
    assert out.kind == "window" and out.window is not None
    assert len(out.window.clubs) == len(C.year_fs_ids(deck, car.pending.year)) - 1
    assert [e.kind for e in out.window.events] == [e["kind"] for e in car.pending.window["events"]]
