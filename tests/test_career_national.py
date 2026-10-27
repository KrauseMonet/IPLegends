"""Playing for your country in Build a Career (A229): the measured selection rates, the value
of a cap, and the wiring that carries both through a career.

The rates are checked by sampling `select` against the tables it claims to follow; the wiring
by recomputing every season's verdict from the season before it, independently of the loop."""

from __future__ import annotations

import base64
import json
import random

import pytest

from game import career as C
from game import national as N
from game import press as P
from tools.snapshot_deck import deck_from, model_from, read_document

pytestmark = pytest.mark.usefixtures("no_incidents")   # A230: incidents are tested in test_career_story

DOC = read_document()
needs_deck = pytest.mark.skipif(DOC is None, reason="no deck snapshot")


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


# --- the selectors --------------------------------------------------------------------------


@pytest.mark.parametrize("grade,b", [(70, 0), (79, 0), (80, 1), (84, 1), (85, 2), (99, 2)])
def test_the_bands_are_the_measured_ones(grade, b):
    assert N.band(grade) == b


@pytest.mark.parametrize("overseas", [False, True])
@pytest.mark.parametrize("state,table", [({}, "FIRST_CALL"),
                                         ({"capped": True, "in_side": True, "caps": 10}, "KEEP"),
                                         ({"capped": True, "in_side": False, "caps": 10}, "RECALL")])
@pytest.mark.parametrize("grade", [75, 82, 90])
def test_each_verdict_happens_at_its_measured_rate(overseas, state, table, grade):
    kind = "overseas" if overseas else "indian"
    want = getattr(N, table)[kind][N.band(grade)]
    n = 4000
    picked = sum(N.select(state, grade, s, 3, overseas).in_side for s in range(n))
    assert abs(picked / n - want) < 0.03, (table, kind, grade, picked / n, want)


def test_a_verdict_follows_from_the_state():
    for s in range(300):
        sel = N.select({}, 90, s, 1, False)
        assert sel.event in ("debut", "") and sel.capped == (sel.event == "debut")
        sel = N.select({"capped": True, "in_side": True, "caps": 7}, 90, s, 1, False)
        assert sel.event in ("kept", "dropped") and sel.capped
        assert sel.total == 7 + sel.caps and (sel.caps > 0) == (sel.event == "kept")
        sel = N.select({"capped": True, "in_side": False, "caps": 7}, 90, s, 1, False)
        assert sel.event in ("recalled", "") and sel.capped


def test_internationals_come_from_the_measured_distribution():
    debut = [N.select({}, 99, s, 2, False) for s in range(3000)]
    caps = [x.caps for x in debut if x.event == "debut"]
    assert caps and min(caps) >= 1 and max(caps) <= 2 * N.DEBUT_MEDIAN["indian"] - 1
    kept = [N.select({"capped": True, "in_side": True}, 99, s, 2, True) for s in range(500)]
    assert {x.caps for x in kept if x.event == "kept"} <= set(N.CAPS_DECILES["overseas"])


def test_the_verdict_is_seeded_by_season_and_draws_nothing_else():
    a = N.select({}, 86, 5, 4, False)
    random.seed(1)
    random.random()
    assert N.select({}, 86, 5, 4, False) == a           # its own stream, not the global one
    assert len({N.select({}, 86, 5, k, False).event for k in range(60)}) == 2


# --- the value of a cap ------------------------------------------------------------------------


@needs_deck
def test_an_uncapped_player_is_offered_less_than_a_capped_one(deck):
    for year, card in ((2015, 85), (2020, 90), (2012, 78)):
        cap = C.offers_for(deck, 3, 4, year, None, card, capped=True)
        unc = C.offers_for(deck, 3, 4, year, None, card, capped=False)
        assert [o.franchise for o in cap] == [o.franchise for o in unc]
        for a, b in zip(cap, unc):
            assert b.price < a.price and abs(b.price - a.price * C.UNCAPPED_VALUE) <= 5
    # a debutant is a debutant either way
    assert [o.price for o in C.offers_for(deck, 3, 0, 2012, None, None, capped=False)] == \
        [o.price for o in C.offers_for(deck, 3, 0, 2012, None, None, capped=True)]


def test_an_uncapped_player_is_retained_at_the_uncapped_price():
    assert C.retention_slab(95, capped=False) == C.UNCAPPED_RETAIN == 400
    assert C.retention_slab(84, capped=False) is None      # below the card a club retains
    assert C.retention_slab(95, capped=True) == 1800


@needs_deck
def test_a_mega_auction_values_an_uncapped_player_lower(deck):
    cap = C.mega_ceilings(deck, 5, 3, 2018, None, 88, capped=True)
    unc = C.mega_ceilings(deck, 5, 3, 2018, None, 88, capped=False)
    for fs in cap:
        assert unc[fs] < cap[fs] and abs(unc[fs] - cap[fs] * C.UNCAPPED_VALUE) <= 2


@needs_deck
def test_a_cap_changes_what_you_are_paid_and_never_how_you_play(deck, model, monkeypatch):
    """The same loyal career with the selectors switched off plays every match identically
    until a mega auction, where the cap can change which club buys you, or (A230) a season
    whose card is the national board's workload call, offered only to a player in his
    national side; before that only the money moves."""
    setup = C.Setup("Ann Example", "opener", 2010)
    real, _ = C.auto_career(deck, model, 3, setup, "loyal")
    assert any(r.national == "debut" for r in real.seasons), "the fixture must be capped"
    C._SEASON_CACHE.clear()
    monkeypatch.setattr(N, "select", lambda state, grade, seed, k, overseas:
                        N.Selection("", 0, 0, False, False))
    never, _ = C.auto_career(deck, model, 3, setup, "loyal")
    C._SEASON_CACHE.clear()
    play = lambda c: [(r.year, r.franchise, r.matches, r.runs, r.balls, r.wickets, r.display)
                      for r in c.seasons]
    a, b = play(real), play(never)
    split = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), None)
    if split is not None:
        r, n = real.seasons[split], never.seasons[split]
        assert (r.year in C.MEGA_YEARS and r.franchise != n.franchise) or (
            r.event == "workload" != n.event), "a cap changed a match, not a price"
    capped_from = next(i for i, r in enumerate(real.seasons) if r.national == "debut")
    later = list(zip(real.seasons, never.seasons))[capped_from + 1:split]
    assert later and all(a.price >= b.price for a, b in later)
    # ordinary offers specifically: a mega year prices you by another path (retention slabs
    # and auction ceilings), so it cannot stand in for the offers' own rule
    ordinary = [(a, b) for a, b in later if a.year not in C.MEGA_YEARS]
    assert ordinary and any(a.price > b.price for a, b in ordinary)


# --- through a career --------------------------------------------------------------------------


@needs_deck
@pytest.mark.parametrize("seed,role,debut,overseas,country", [
    (3, "opener", 2010, False, ""), (11, "pace", 2009, False, ""),
    (5, "allrounder", 2012, True, "Australia"), (8, "spin", 2015, True, "Sri Lanka")])
def test_every_season_s_verdict_follows_from_the_season_before(deck, model, seed, role, debut,
                                                                overseas, country):
    setup = C.Setup("Ann Example", role, debut, overseas, country=country,
                    ambitions=("international",))
    car, _ = C.auto_career(deck, model, seed, setup, "strength")
    nation = country or "India"
    state: dict = {}
    events = []
    for r in car.seasons:
        sel = N.select(state, r.display, seed, r.k, overseas)
        assert (r.national, r.caps, r.caps_total) == (sel.event, sel.caps, sel.total), r.year
        state = {"capped": sel.capped, "in_side": sel.in_side, "caps": sel.total}
        texts = [m.text for m in r.milestones]
        assert (f"{nation} debut" in texts) == (sel.event == "debut")
        marks = [m for m in range(N.CAP_MARK, sel.total + 1, N.CAP_MARK) if m > sel.total - sel.caps]
        assert [t for t in texts if t.endswith(f"internationals for {nation}")] == \
            [f"{m} internationals for {nation}" for m in marks]
        kinds = [f["k"] for f in r.press]
        for ev in ("debut", "recalled", "dropped"):
            assert (f"nt_{ev}" in kinds) == (sel.event == ev)
        assert kinds.count("nt_caps") == len(marks)
        events.append(sel.event)
    assert car.checkpoint.national == state
    assert events.count("debut") <= 1
    debut_year = next((r.year for r, ev in zip(car.seasons, events) if ev == "debut"), None)
    assert C.ambition_status("international", car.seasons) == (
        ("done", debut_year) if debut_year else ("open", None))


@needs_deck
def test_an_overseas_career_with_no_country_is_never_selected(deck, model):
    car, _ = C.auto_career(deck, model, 5, C.Setup("Ann Example", "allrounder", 2012, True), "strength")
    assert all(r.national == "" and r.caps_total == 0 for r in car.seasons)
    assert car.checkpoint.national == {}


@needs_deck
def test_a_checkpoint_from_before_the_selectors_starts_uncapped(deck, model):
    setup = C.Setup("Ann Example", "opener", 2010)
    car, _ = C.auto_career(deck, model, 3, setup, "strength")
    old = car.checkpoint.to_dict()
    del old["national"]
    assert C.Checkpoint.from_dict(old).national == {}


@pytest.mark.parametrize("kw", [dict(overseas=False, country="Australia"),
                                dict(overseas=True, country="Narnia"),
                                dict(overseas=True, ambitions=("international",))])
def test_a_country_is_checked(kw):
    with pytest.raises(C.CareerError):
        C.Setup("Ann Example", "opener", 2012, **kw).check()


def test_a_country_and_its_ambition_are_accepted():
    C.Setup("Ann Example", "opener", 2012, True, country="England", ambitions=("international",)).check()
    C.Setup("Ann Example", "opener", 2012, ambitions=("international",)).check()     # India


def test_the_selectors_make_the_papers():
    v = P.Voice("Ann Example", "pace", 4)
    base = {"y": 2016, "country": "England", "caps": 6, "total": 30, "grade": 88}
    for kind in ("debut", "recalled", "dropped"):
        c = P.render({**base, "k": f"nt_{kind}"}, v)
        assert "England" in c.headline + " ".join(c.body)
    debut = P.render({**base, "k": "nt_debut"}, v)
    assert debut.section == "FRONT PAGE" and "88" in " ".join(debut.body)
    s = P.SeasonFacts(k=3, year=2016, club="Mumbai Indians", price=100, age=22,
                      national={**base, "event": "kept"}, caps_marks=[50])
    assert [f["k"] for f in P.season_facts(s, [])] == ["nt_caps"]


# --- through the API ------------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-only-career-secret")
    from fastapi.testclient import TestClient
    from web.app import app
    with TestClient(app) as c:
        yield c


@needs_deck
def test_the_country_rides_in_the_token_and_an_older_token_has_none(client):
    from web import career_routes as R
    d = client.post("/api/career/start", json={"name": "Ann Example", "role": "pace", "debut": 2012,
                                               "seed": 4, "overseas": True, "country": "England"}).json()
    assert d["country"] == "England" and d["caps"] == 0 and not d["in_side"]
    seed, setup, cp, partial = R.read_token(d["token"])
    assert setup.country == "England"
    body = json.dumps({"v": 1, "seed": seed,
                       "setup": [setup.name, setup.role, setup.debut, True, None, "", "", []],
                       "cp": cp.to_dict(), "partial": partial}, separators=(",", ":"))
    payload = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    old = client.post("/api/career/view", json={"token": f"{payload}.{R._sign(payload)}"})
    assert old.status_code == 200 and old.json()["country"] == ""
    indian = client.post("/api/career/start", json={"name": "Ann Example", "role": "pace",
                                                    "debut": 2012, "seed": 4}).json()
    assert indian["country"] == "India"
    bad = client.post("/api/career/start", json={"name": "Ann Example", "role": "pace", "debut": 2012,
                                                 "country": "England"})
    assert bad.status_code == 400
