"""Build a Career's Rating, a youngster's start and events that say what they did (A214)."""

from __future__ import annotations

import random
from collections import Counter

import pytest

from game import career as C
from tools.snapshot_deck import deck_from, model_from, read_document

DOC = read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="no deck snapshot")


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


def _setup(role="opener", debut=2012):
    return C.Setup("Tester", role, debut, False)


def _play(deck, model, seed, setup, moves):
    from tests.test_career import _play as play
    return play(deck, model, seed, setup, moves)


# --- the Rating -----------------------------------------------------------------------------


def test_the_rating_runs_from_the_starts_card_to_the_ceilings(deck):
    for role in C.ROLES:
        start, ceiling = C.spin(deck, 4, role)
        assert C.rating_at(start, ceiling, 0.0) == start.display
        assert C.rating_at(start, ceiling, 1.0) == ceiling.display
        assert C.rating_at(start, ceiling, C.ABILITY_FLOOR) >= C.RATING_FLOOR
        mids = [C.rating_at(start, ceiling, t / 10) for t in range(11)]
        assert mids == sorted(mids)


def test_a_point_on_a_card_is_a_point_on_the_rating(deck):
    start, ceiling = C.spin(deck, 4, "pace")
    for t in (0.0, 0.3, 0.55):
        for pts in (-3, 2, 5):
            moved = C.rating_at(start, ceiling, t + C.points_t(pts, start, ceiling))
            assert abs(moved - (C.rating_at(start, ceiling, t) + pts)) <= 1     # rounding only


def test_a_youngster_starts_low_and_every_batting_template_batted_where_his_role_does(deck):
    for role in ("opener", "middle", "finisher"):
        starts, ceilings = C.template_pools(deck, role)
        assert all(c.positions == C.ROLES[role]["slots"] for c in starts + ceilings)
    for role in ("opener", "middle", "pace", "spin", "keeper", "allrounder"):
        starts, _ = C.template_pools(deck, role)
        assert starts and all(C.START_DISPLAY[0] <= c.display <= C.START_DISPLAY[1] for c in starts)


def test_each_season_records_the_rating_it_was_played_at(deck, model):
    car, _ = C.auto_career(deck, model, 3, _setup("pace", 2010), "strength")
    for r in car.seasons:
        assert r.rating == C.rating_at(car.start, C.top_card(car.ceiling, car.legend, r.potential),
                                       r.ability)
    assert car.summary.peak_rating == max(r.rating for r in car.seasons)


def test_form_feeds_development_only_while_you_are_still_growing(deck, model):
    car, _ = C.auto_career(deck, model, 3, _setup("opener", 2008), "strength")
    # [A224] "still growing" is the career's own shape, read at the clock it was played at
    growing = [car.shape.growing(r.clock) for r in car.seasons]
    assert True in growing and False in growing, "the fixture must live past its peak"
    for r, g in zip(car.seasons, growing):
        want = round(2 * C.DEV_POINTS * (r.percentile - 0.5), 1) if g else 0.0
        assert r.development == want
    assert any(r.development for r, g in zip(car.seasons, growing) if g)


def test_clubs_price_you_on_your_rating_and_your_last_season(deck, model):
    assert C.market_value(80, None) is None                  # a debutant is uncapped
    assert C.market_value(90, 70) == round(0.6 * 90 + 0.4 * 70)
    car = _play(deck, model, 1, _setup(), ["o0"])
    cp = car.checkpoint
    want = C.market_value(C.rating_at(car.start, car.top,
                                      C.arc_point(cp.clock, cp.permanent, 0.0, car.shape)),
                          cp.last_display)
    assert C.market_for(car.start, car.ceiling, cp, car.shape, car.legend) == want


# --- a youngster gets time; a star always finds a club ----------------------------------------


def _quiet(n, role="opener"):
    return next(s for s in range(300) if all(len(C.draw_event(s, k, role=role).options) == 1
                                             for k in range(n)))


def test_a_youngsters_first_seasons_cannot_cost_him_his_place(deck, model, monkeypatch):
    monkeypatch.setattr(C, "POOR_PERCENTILE", 1.01)             # every season poor
    monkeypatch.setattr(C, "STAR_RATING", 100)
    seed = _quiet(4)
    c = _play(deck, model, seed, _setup(), ["o0"] * 4)
    squads = [r.squad for r in c.seasons]
    assert squads[:C.YOUTH_SEASONS + 1] == [False] * (C.YOUTH_SEASONS + 1)
    assert squads[C.YOUTH_SEASONS + 1] is True                  # then form decides again
    assert any("giving you time" in n for n in c.seasons[0].notes)


def test_a_star_is_never_released_for_form(deck, model, monkeypatch):
    monkeypatch.setattr(C, "POOR_PERCENTILE", 1.01)
    monkeypatch.setattr(C, "YOUTH_SEASONS", 0)
    seed = _quiet(4)
    monkeypatch.setattr(C, "STAR_RATING", 100)
    out = _play(deck, model, seed, _setup(), ["o0", "o0"])
    assert out.released
    monkeypatch.setattr(C, "STAR_RATING", 0)
    kept = _play(deck, model, seed, _setup(), ["o0", "o0", "o0"])
    assert not kept.released and [r.squad for r in kept.seasons] == [False, True, True]
    assert any("somebody always signs you" in n for n in kept.seasons[1].notes)


# --- events -----------------------------------------------------------------------------------


def test_injuries_come_mostly_to_fast_bowlers_and_a_stress_fracture_only_to_them():
    def injuries(role):
        return Counter(e.key for s in range(500) for e in C.event_sequence(s, False, role)
                       if e.injury)
    pace, opener, spin = injuries("pace"), injuries("opener"), injuries("spin")
    assert sum(pace.values()) > 2 * sum(spin.values()) > 2 * sum(opener.values()) * 0.9
    assert pace["fracture"] > 0 and opener["fracture"] == spin["fracture"] == 0


def test_every_option_states_its_outcomes_and_they_add_up():
    for e in C.EVENTS:
        for o in e.options:
            if o.outcomes:
                assert sum(x.chance for x in o.outcomes) == pytest.approx(1.0)


def _event_seed(key, role="opener", k=0):
    return next(s for s in range(5000) if C.draw_event(s, k, role=role).key == key
                and all(len(C.draw_event(s, j, role=role).options) == 1 for j in range(k)))


def test_a_rebuild_costs_this_season_and_lands_the_next(deck, model):
    # a quiet second season, so nothing else moves its ability
    # [A224] and a standard shape, so the second season's ability is the ARC's
    seed = next(s for s in range(5000) if C.draw_event(s, 0, role="opener").key == "coach"
                and C.draw_event(s, 1, role="opener").key == "quiet"
                and C.draw_shape(s) == C.STANDARD)
    coach = next(e for e in C.EVENTS if e.key == "coach")
    rebuild = coach.options[0]
    turned = C.draw_outcome(seed, 0, rebuild)
    c = _play(deck, model, seed, _setup(), ["o0", "e0", "o0"])
    first, second = c.seasons[0], c.seasons[1]
    assert first.rating == C.rating_at(c.start, c.ceiling, C.arc_point(0, 0.0, 0.0)) + rebuild.season
    assert any(f"{rebuild.season:+d} Rating this season" in n for n in first.notes)
    assert any(turned.text in n and f"{turned.permanent:+d} Rating for good" in n
               for n in second.notes)
    # and the permanent change reached the ability the second season was played at
    assert second.potential == 0, "the fixture must not raise its potential after season 1"
    dev = C.points_t(first.development, c.start, c.ceiling)
    assert second.ability == pytest.approx(C.arc_point(1, dev + C.points_t(turned.permanent, c.start, c.ceiling), 0.0))


def test_a_lasting_change_is_put_in_a_fans_numbers(deck, model):
    seed = _event_seed("mentor")
    c = _play(deck, model, seed, _setup(), ["o0", "e0"])
    notes = " ".join(c.seasons[0].notes)
    assert "+3 Rating for good" in notes and "per 100 balls" in notes
    assert "{name}" not in notes


def test_injuries_are_kept_on_the_career(deck, model):
    seed = _event_seed("fracture", role="pace")
    c = _play(deck, model, seed, _setup("pace"), ["o0", "e1"])
    assert c.checkpoint.injuries and "stress fracture" in c.checkpoint.injuries[0][1].lower()
    assert any("-3 Rating for good" in n for n in c.seasons[0].notes)
    again = C.Checkpoint.from_dict(c.checkpoint.to_dict())
    assert again.injuries == c.checkpoint.injuries and again.later == c.checkpoint.later


def test_a_skill_line_names_only_what_changed():
    from etl.feasibility import Card
    a = Card(fs_id=1, person_id="a", name="a", role="batter", bat_scoring=0.0, bat_dismissal=1.0)
    b = Card(fs_id=1, person_id="a", name="a", role="batter", bat_scoring=0.05, bat_dismissal=0.9)
    assert C.skill_words(a, b) == "You now score 5 more runs per 100 balls and get out 10% less often."
    assert C.skill_words(a, a) is None
