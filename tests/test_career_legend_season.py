"""A legend is the best SEASON, carded like a real one, and the Hall of Fame is for careers
holding two of four honours (A225)."""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import math
import random

import pytest
from fastapi.testclient import TestClient

from game import career as C
from tools.snapshot_deck import deck_from, model_from, read_document
from web import career_results as CR, career_routes as R

DOC = read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="no deck snapshot")


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


# --- one discipline measured --------------------------------------------------------------------
# Against what the engine expected of the same balls: no pool, no peers.

OWN = (0.10, 0.80)


def test_a_season_exactly_as_expected_measures_at_your_own_level():
    s, d = C._measure(300, 12, 450.0, 450.0, 12.0, OWN, cheaper=False)
    assert (s, d) == pytest.approx(OWN)


def test_luck_is_what_happened_beyond_the_expectation_pulled_in_by_a_hundred_balls():
    s, d = C._measure(300, 6, 510.0, 450.0, 12.0, OWN, cheaper=False)
    w = 300 / (300 + C.MEASURE_BALLS)
    assert s == pytest.approx(0.10 + w * 60 / 300)            # 60 runs more than expected
    rate = 12 / 300
    assert d == pytest.approx(0.80 * (6 + 100 * rate) / (12 + 100 * rate))
    assert d < 0.80                                           # out half as often as expected


def test_bowling_counts_runs_saved_so_fewer_runs_is_more():
    cheap, _ = C._measure(300, 12, 400.0, 450.0, 12.0, OWN, cheaper=True)
    dear, _ = C._measure(300, 12, 500.0, 450.0, 12.0, OWN, cheaper=True)
    assert cheap > 0.10 > dear
    _, more = C._measure(300, 18, 450.0, 450.0, 12.0, OWN, cheaper=True)
    assert more > 0.80                                        # more wickets than expected


def test_nothing_is_measured_without_balls_or_numbers_of_your_own():
    assert C._measure(0, 0, 0, 0.0, 0.0, OWN, cheaper=False) == (None, None)
    assert C._measure(300, 12, 450.0, 450.0, 12.0, (None, None), cheaper=False) == (None, None)


# --- a season measured -------------------------------------------------------------------------


def _line(card, **kw):
    line = C.Line(card=card, side=None)
    for k, v in kw.items():
        setattr(line, k, v)
    return line


def _card(deck, role):
    return next(c for cs in deck.cards_by_fs.values() for c in cs
                if c.role == role and c.bat_scoring is not None and c.bat_dismissal
                and c.bowl_scoring is not None and c.bowl_dismissal)


FIGS = dict(balls=300, outs=10, runs=450, exp_runs=420.0, exp_outs=12.0, innings=12,
            bowl_balls=120, wickets=4, bowl_off_bat=150, bowl_exp_runs=160.0, bowl_exp_wickets=5.0)


@pytest.mark.parametrize("role,bat,bowl", [("batter", True, False), ("bowler", False, True),
                                           ("allrounder", True, True)])
def test_a_season_is_measured_on_the_disciplines_it_is_graded_on(deck, role, bat, bowl):
    card = _card(deck, role)
    got = C.measured_halves({("me", 0): _line(card, **FIGS)}, ("me", 0))
    assert (got[0] is not None) == bat and (got[2] is not None) == bowl
    if bat:
        assert got[:2] == pytest.approx(list(C._measure(300, 10, 450, 420.0, 12.0,
                                                        (card.bat_scoring, card.bat_dismissal), False)))
    if bowl:
        assert got[2:] == pytest.approx(list(C._measure(120, 4, 150, 160.0, 5.0,
                                                        (card.bowl_scoring, card.bowl_dismissal), True)))


def test_the_engine_records_what_it_expected_of_every_ball(deck, model):
    """Summed over a match, a batter's expected runs and outs and a bowler's must come from
    the same balls: every ball faced is one ball bowled, so the two sides' totals agree."""
    from game.season import historical_sides, play
    sides = historical_sides(deck, random.Random(4), 2)
    r = play(model, sides[0], sides[1], random.Random(9))
    for inn in (r.home_innings, r.away_innings):
        assert sum(b.exp_runs for b in inn.batting) == pytest.approx(sum(w.exp_runs for w in inn.bowling))
        assert sum(b.exp_outs for b in inn.batting) == pytest.approx(sum(w.exp_wickets for w in inn.bowling))
        assert sum(w.off_bat for w in inn.bowling) == sum(b.runs for b in inn.batting)
        assert all(0 < w.exp_wickets < w.balls for w in inn.bowling if w.balls)


def test_over_many_matches_the_expectation_matches_what_happened(deck, model):
    """The independent check on the sum itself: expected runs and wickets, added up over
    forty matches, land within a few percent of the runs and wickets that really happened."""
    from game.season import historical_sides, play
    runs = exp = outs = exp_outs = 0.0
    for s in range(40):
        sides = historical_sides(deck, random.Random(s), 2)
        r = play(model, sides[0], sides[1], random.Random(500 + s))
        for inn in (r.home_innings, r.away_innings):
            runs += sum(b.runs for b in inn.batting)
            exp += sum(b.exp_runs for b in inn.batting)
            outs += sum(b.out for b in inn.batting)
            exp_outs += sum(b.exp_outs for b in inn.batting)
    assert abs(exp - runs) / runs < 0.03
    assert abs(exp_outs - outs) / outs < 0.08


def test_a_season_line_adds_up_its_scorecards_expectations(deck, model):
    from game.season import historical_sides, play
    sides = historical_sides(deck, random.Random(7), 2)
    results = [play(model, sides[0], sides[1], random.Random(70 + s)) for s in range(3)]
    lines = C.season_lines(results)
    for (pid, side_id), ln in lines.items():
        side = next(sd for sd in sides if id(sd) == side_id)
        bat = [b for r in results for inn, who in ((r.home_innings, r.home), (r.away_innings, r.away))
               if who is side for b in inn.batting if b.player.person_id == pid and (b.faced_any or b.out)]
        bowl = [w for r in results for inn, who in ((r.home_innings, r.away), (r.away_innings, r.home))
                if who is side for w in inn.bowling if w.player.person_id == pid and w.balls]
        assert ln.exp_runs == pytest.approx(sum(b.exp_runs for b in bat))
        assert ln.exp_outs == pytest.approx(sum(b.exp_outs for b in bat))
        assert ln.bowl_off_bat == sum(w.off_bat for w in bowl)
        assert ln.bowl_exp_runs == pytest.approx(sum(w.exp_runs for w in bowl))
        assert ln.bowl_exp_wickets == pytest.approx(sum(w.exp_wickets for w in bowl))
    assert any(ln.exp_runs for ln in lines.values()) and any(ln.bowl_exp_wickets for ln in lines.values())


# --- the legend --------------------------------------------------------------------------------


def _career(deck, seasons):
    setup = C.Setup("X", "opener", 2012, False)
    start, ceiling = C.spin(deck, 3, "opener")
    return C.Career(setup=setup, seed=3, start=start, ceiling=ceiling, seasons=seasons,
                    legend=C.potential_legend(deck, "opener", ceiling))


def _rec(k, display, rating, measured):
    return C.SeasonRecord(k=k, year=2012 + k, franchise="Mumbai Indians", short="MI", price=100,
                          stayed=False, event="quiet", choice="", captain=False, ability=0.5,
                          teams=8, display=display, rating=rating, measured=measured)


M = [0.31, 0.62, None, None]


def test_the_legend_is_the_best_graded_season_playing_its_own_measured_numbers(deck):
    car = _career(deck, [_rec(0, 80, 90, [0.1, 1.0, None, None]), _rec(1, 94, 82, M),
                         _rec(2, 88, 88, [0.2, 0.9, None, None])])
    best, card = C.legend_card(car)
    assert best.k == 1 and card.display == 94                # the grade, not the Rating
    assert (card.bat_scoring, card.bat_dismissal) == (0.31, 0.62)
    played = C.ability(car.start, car.ceiling, 0.5, setup=car.setup, fs_id=0, year=2013, franchise=None)
    assert card.bowl_scoring == played.bowl_scoring          # an unmeasured half is the season's own


def test_a_tied_grade_goes_to_the_higher_rating_then_the_earlier_year(deck):
    car = _career(deck, [_rec(0, 90, 80, M), _rec(1, 90, 85, M), _rec(2, 90, 85, M)])
    assert C.legend_card(car)[0].k == 1


def test_a_career_with_no_measured_season_keeps_the_best_rating_rule(deck):
    car = _career(deck, [_rec(0, 95, 80, None), _rec(1, 85, 88, None)])
    best, card = C.legend_card(car)
    assert best.k == 1 and card.display == 88
    assert CR.legend(car)["basis"] == "rating"


def test_played_seasons_carry_a_measurement_of_the_disciplines_they_are_graded_on(deck, model):
    for role, bat, bowl in (("opener", True, False), ("pace", False, True)):
        car, _ = C.auto_career(deck, model, 5, C.Setup("X", role, 2012, False), "random")
        for r in car.seasons:
            assert r.measured is not None
            assert (r.measured[0] is not None) == bat and (r.measured[2] is not None) == bowl
        leg = CR.legend(car)
        best = max(car.seasons, key=lambda r: (r.display, r.rating, -r.year))
        assert leg["basis"] == "season" and leg["rating"] == best.display and leg["year"] == best.year


# --- the Hall of Fame --------------------------------------------------------------------------


@pytest.mark.parametrize("peak,titles,caps,rr,wr,n", [
    (90, 2, 0, None, None, 2), (89, 2, 0, None, None, 1), (90, 1, 0, None, None, 1),
    (80, 0, 1, 25, None, 2), (80, 0, 0, 26, 30, 0), (80, 0, 0, None, 25, 1),
    (95, 3, 2, 3, 1, 4),
])
def test_the_honours_are_the_four_and_two_put_a_career_in(peak, titles, caps, rr, wr, n):
    held = CR.honours(peak, titles, caps, rr, wr)
    assert len(held) == n
    assert CR.inducted(held) == (n >= 2)


@pytest.fixture
def client():
    from web.app import app
    return TestClient(app)


def test_the_hall_route_says_who_is_in_and_why(client, deck, model, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-only-career-secret")
    car, _ = C.auto_career(deck, model, 7, C.Setup("T", "opener", 2016, False), "strength")
    base = {"key": "k", "name": "T", "role": "opener", "debut": 2016, "overseas": False,
            "seasons": 10, "matches": 140, "runs": 100, "wickets": 0, "ending": "archive",
            "peak_year": 2020, "completed_at": datetime.datetime(2026, 10, 25)}
    card = CR.card(car)
    card.update(orange_caps=0, purple_caps=0)
    rows = [dict(base, key="in", titles=2, peak_card=91, card=card),
            dict(base, key="out", titles=2, peak_card=89, card=card)]
    monkeypatch.setattr(R.db, "connection", contextlib.nullcontext)
    monkeypatch.setattr(R.career_results, "hall", lambda conn, account_id: rows)
    from web import auth
    client.cookies.set(auth.COOKIE_NAME, auth.make_session_cookie(1))
    got = {h["key"]: h for h in client.get("/api/career/hall").json()}
    client.cookies.clear()
    assert got["in"]["inducted"] and got["in"]["honours"] == ["Peak Rating 91", "2 titles"]
    assert not got["out"]["inducted"] and got["out"]["honours"] == ["2 titles"]
