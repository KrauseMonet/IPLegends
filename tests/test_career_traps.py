"""[A295] Phase 3 of making Build a Career harder: traps -- choices that look good and can
cost you, each flagged in its hint. Workload builds fatigue that raises injury risk;
rebuilding your technique can fail for good; ego answers cost standing at the club; and an
offer says when its club would not pick you. Numbers stated, not read from the code (A207)."""

from __future__ import annotations

import random

import pytest

from game import career as C
from game import career_incidents as incidents

HARDER_TESTS = True
CYCLE_TESTS = True


@pytest.fixture(scope="module")
def deck_model():
    import tools.snapshot_deck as sd

    doc = sd.read_document()
    return sd.deck_from(doc), sd.model_from(doc)


def _event(key):
    return next(e for e in C.EVENTS if e.key == key)


def _option(event_key, label):
    return next(o for o in _event(event_key).options if o.label == label)


def _incident_option(key, label):
    return next(o for o in incidents.INCIDENTS[key].options if o.label == label)


# --- workload ---------------------------------------------------------------------------

def test_the_declared_workload_numbers():
    assert C.FATIGUE_RISK == 0.5
    assert C.FATIGUE_MAX == 3.0
    assert C.FATIGUE_DECAY == 1.0


def test_fatigue_wears_off_a_point_a_season_and_stays_in_range():
    assert C.fatigue_after(2.0, 0.0) == 1.0
    assert C.fatigue_after(2.0, 1.0) == 2.0
    assert C.fatigue_after(0.0, 0.0) == 0.0
    assert C.fatigue_after(0.0, 1.0) == 1.0          # a fresh player's niggle carries over
    assert C.fatigue_after(3.0, 2.0) == 3.0


def test_playing_through_adds_workload_and_resting_takes_it_away():
    assert _option("workload", "Tell them you want to play").fatigue == 1.0
    assert _option("workload", "Agree").fatigue == -3.0
    assert _option("home_league", "Play there first").fatigue == 1.0
    assert _option("home_league", "Skip it").fatigue == 0.0
    assert _option("fitness", "Commit to it").fatigue == -1.0
    assert _incident_option("hamstring", "Strap it and play on").fatigue == 1.0
    assert _incident_option("hamstring", "Sit out and let it heal").fatigue == 0.0
    assert _incident_option("side_strain", "Rush back").fatigue == 1.0
    assert _incident_option("back", "Manage it and keep bowling").fatigue == 1.0


def test_every_workload_option_says_so_in_its_hint():
    for o in (_option("workload", "Tell them you want to play"),
              _option("home_league", "Play there first"),
              _incident_option("hamstring", "Strap it and play on"),
              _incident_option("side_strain", "Rush back")):
        assert any(w in o.hint.lower() for w in ("body", "stays with you", "no break"))


def test_a_tired_season_raises_the_injury_risk(deck_model, monkeypatch):
    deck, model = deck_model
    seen = []
    real = incidents.candidate

    def spy(seed, k, kind, matches, risk_scale=1.0):
        seen.append(risk_scale)
        return real(seed, k, kind, matches, risk_scale)
    monkeypatch.setattr(C.incidents, "candidate", spy)
    you = next(c for cs in deck.cards_by_fs.values() for c in cs
               if c.role == "bowler" and c.bowling_style == "pace")
    you = C.dataclasses.replace(you, person_id=C.YOU_ID, name="You", bowl_balls=None)
    for late, fatigue in ((False, 2.0), (True, 2.0), (False, 0.0)):
        try:
            C.simulate_year(deck, model, 1, 2, you, answers=(), late=late, fatigue=fatigue)
        except C._NeedMoment:
            pass
    assert seen == [pytest.approx(2.0), pytest.approx(1.6 * 2.0), pytest.approx(1.0)]


def _play_with_card(deck, model, monkeypatch, event_key, answer, seasons=4, seed=2):
    """A career whose every pre-season card is `event_key`, always answered `answer`."""
    card = _event(event_key)
    monkeypatch.setattr(C, "season_event", lambda *a, **kw: card)
    C._SEASON_CACHE.clear()
    setup = C.Setup("Test", "pace", 2010)
    rng = random.Random("traps")
    moves = []
    while True:
        career = C.play_career(deck, model, seed, setup, moves)
        if career.complete or len(career.seasons) >= seasons:
            return career
        p = career.pending
        moves.append(f"e{answer}" if p.kind == "event" else C.policy_move(career, "loyal", rng))


def test_refusing_rest_every_season_carries_workload_into_it(deck_model, monkeypatch):
    deck, model = deck_model
    played = _play_with_card(deck, model, monkeypatch, "workload", 1)
    assert all(r.fatigue >= 1.0 for r in played.seasons)
    rested = _play_with_card(deck, model, monkeypatch, "workload", 0)
    assert all(r.fatigue == 0.0 for r in rested.seasons)


def test_the_season_plays_the_record_s_fatigue(deck_model, monkeypatch):
    deck, model = deck_model
    seen = {}
    real = C.simulate_year

    def spy(deck_, model_, seed, k, you, *a, **kw):
        seen[you.season_year] = kw.get("fatigue", 0.0)
        return real(deck_, model_, seed, k, you, *a, **kw)
    monkeypatch.setattr(C, "simulate_year", spy)
    career = _play_with_card(deck, model, monkeypatch, "workload", 1)
    assert {r.year: r.fatigue for r in career.seasons} == {r.year: seen[r.year]
                                                          for r in career.seasons}


def test_no_workload_with_the_traps_off(deck_model, monkeypatch):
    deck, model = deck_model
    monkeypatch.setattr(C, "TRAPS_ON", False)
    career = _play_with_card(deck, model, monkeypatch, "workload", 1)
    assert all(r.fatigue == 0.0 for r in career.seasons)


# --- remodelling your action ------------------------------------------------------------

def test_a_rebuild_can_fail_for_good():
    rebuild = _option("coach", "Rebuild it")
    assert sum(o.chance for o in rebuild.trap_outcomes) == pytest.approx(1.0)
    assert sorted((o.chance, o.permanent) for o in rebuild.trap_outcomes) == [
        (0.2, -3), (0.3, 1), (0.5, 5)]
    drawn = [C.draw_outcome(seed, 3, rebuild).permanent for seed in range(2000)]
    assert 0.17 < drawn.count(-3) / len(drawn) < 0.23
    assert "never find" in rebuild.hint


def test_with_the_traps_off_a_rebuild_never_fails(monkeypatch):
    monkeypatch.setattr(C, "TRAPS_ON", False)
    rebuild = _option("coach", "Rebuild it")
    assert all(C.draw_outcome(seed, 3, rebuild).permanent > 0 for seed in range(500))


# --- ego --------------------------------------------------------------------------------

def test_ego_costs_standing():
    hit_back = _incident_option("press", "Hit back at the press conference")
    have_it = _incident_option("row", "Have it out with him")
    own_it = _incident_option("press", "Own it")
    assert C.incident_standing(hit_back, None) == -1
    assert C.incident_standing(have_it, have_it.turns[0]) == -1
    assert C.incident_standing(have_it, have_it.turns[1]) == -2
    assert C.incident_standing(own_it, None) == 1
    for o in (hit_back, have_it):
        assert any(w in o.hint for w in ("club", "management"))


def test_ego_is_free_with_the_traps_off(monkeypatch):
    monkeypatch.setattr(C, "TRAPS_ON", False)
    hit_back = _incident_option("press", "Hit back at the press conference")
    assert C.incident_standing(hit_back, None) == 0


# --- the stacked club -------------------------------------------------------------------

def _squad_and_you(deck):
    import dataclasses
    import game.__main__ as M

    fs = next(fs for fs, cs in deck.cards_by_fs.items()
              if cs[0].franchise.startswith("Mumbai") and cs[0].season_year == 2013)
    squad = list(deck.cards_by_fs[fs])
    base = next(c for c in M.opposition_xi(squad) if c.role == "batter")
    return fs, squad, dataclasses.replace(base, person_id=C.YOU_ID, name="You", bat_balls=None)


def test_an_offer_says_when_the_club_would_not_pick_you(deck_model):
    deck, _ = deck_model
    fs, squad, you = _squad_and_you(deck)
    rival = C.merit_rival(you, squad)
    out = C.merit_fields(deck, you, fs, rival.display - 1)
    assert out == {"starts": False, "rival": rival.name, "rival_rating": rival.display}
    assert C.merit_fields(deck, you, fs, rival.display) == {"starts": True}
    assert C.merit_fields(deck, you, fs, rival.display - 1, captain=True) == {"starts": True}


def test_trial_offers_carry_the_verdict(deck_model):
    deck, model = deck_model
    found = 0
    for seed in range(30):
        setup = C.Setup("Test", "middle", 2013)
        career = C.play_career(deck, model, seed, setup, [])
        assert career.pending.kind == "trials"
        for o in career.pending.offers:
            if not o.starts:
                found += 1
                assert o.rival and o.rival_rating is not None
        if found:
            break
    assert found, "no trial club left a debutant out"


def test_the_page_receives_the_verdict():
    from web import career_routes as R

    o = C.Offer(fs_id=1, franchise="Mumbai Indians", short="MI", price=100, strength=82,
                starts=False, rival="R Sharma", rival_rating=88)
    out = R._offer(o, 2013)
    assert (out.starts, out.rival, out.rival_rating) == (False, "R Sharma", 88)


def _with_incident(deck, model, monkeypatch, key, answer, seasons=3, seed=3):
    """A career in which every season brings incident `key`, always answered `answer`."""
    monkeypatch.setattr(C.incidents, "candidate",
                        lambda seed_, k, kind, matches, risk_scale=1.0: (key, 1))
    monkeypatch.setattr(C, "incidents_spent", lambda seasons_: ())
    C._SEASON_CACHE.clear()
    setup = C.Setup("Test", "opener", 2010)
    rng = random.Random("niggle")
    moves = []
    while True:
        career = C.play_career(deck, model, seed, setup, moves)
        if career.complete or len(career.seasons) >= seasons:
            return career
        p = career.pending
        moves.append(f"i{answer}" if p.kind == "incident" else C.policy_move(career, "loyal", rng))


def test_a_niggle_adds_no_workload_with_the_traps_off(deck_model, monkeypatch):
    deck, model = deck_model
    monkeypatch.setattr(C, "TRAPS_ON", False)
    career = _with_incident(deck, model, monkeypatch, "hamstring", 1)
    assert any(r.incident for r in career.seasons)
    assert all(r.fatigue == 0.0 for r in career.seasons)


def test_an_ego_answer_costs_standing_in_the_season(deck_model, monkeypatch):
    deck, model = deck_model
    career = _with_incident(deck, model, monkeypatch, "press", 2)
    hit = [r.incident for r in career.seasons if r.incident
           and r.incident["choice"] == "Hit back at the press conference"]
    assert hit and all(i["standing"] == -1 for i in hit)


def test_playing_through_a_niggle_carries_into_next_season(deck_model, monkeypatch):
    """Every season brings a hamstring, always strapped: the next season starts tired."""
    deck, model = deck_model
    career = _with_incident(deck, model, monkeypatch, "hamstring", 1)
    strapped = [r for r in career.seasons if r.incident and r.incident["key"] == "hamstring"
                and r.incident["choice"] == "Strap it and play on"]
    assert strapped and strapped[0].k + 1 < len(career.seasons)
    assert career.seasons[strapped[0].k + 1].fatigue >= 1.0
