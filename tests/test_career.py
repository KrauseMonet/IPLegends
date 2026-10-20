"""Build a Career (A197): the rules a career rests on, each checked against something
other than the code that applies it.

Runs against the committed deck snapshot (no database). Seasons are real simulations, so
only a handful are played; everything that can be checked without one is.
"""

from __future__ import annotations

import math
import random
from collections import Counter

import pytest

from etl.feasibility import BOWLERS_IN_TWELVE, Card
from etl.franchise_map import canonical
from game import career as C
from game.__main__ import OVERSEAS_LIMIT, attack, viable
from game.auction import value_curve
from game.season import fixtures, tournament_leaders
from tools.snapshot_deck import deck_from, model_from, read_document

DOC = read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="no deck snapshot")


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


# --- the schedule and the real years ------------------------------------------------------

# Franchises per year, written out from CLAUDE.md's franchise table rather than read from the
# deck: Deccan 2008-12, Pune Warriors 2011-13, Kochi 2011, Hyderabad from 2013, Chennai and
# Rajasthan out in 2016-17 for Gujarat Lions and Rising Pune, Gujarat Titans and Lucknow
# from 2022.
TEAMS_BY_YEAR = {2008: 8, 2009: 8, 2010: 8, 2011: 10, 2012: 9, 2013: 9, 2014: 8, 2015: 8,
                 2016: 8, 2017: 8, 2018: 8, 2019: 8, 2020: 8, 2021: 8, 2022: 10, 2023: 10,
                 2024: 10, 2025: 10, 2026: 10}


@pytest.mark.parametrize("n, each", [(8, 14), (9, 16), (10, 14)])
def test_a_real_years_schedule_has_the_real_league_length(n, each):
    games = fixtures(n, C.double_at_for(n))
    per_team = Counter(t for pair in games for t in pair)
    assert set(per_team.values()) == {each}
    if n < 10:     # eight and nine teams: everybody twice, home and away
        pairs = Counter(frozenset(p) for p in games)
        assert len(pairs) == n * (n - 1) // 2 and set(pairs.values()) == {2}


def test_every_year_plays_exactly_its_own_real_teams(deck):
    for year, n in TEAMS_BY_YEAR.items():
        sides, yours = C.year_sides(deck, year)
        assert len(sides) == n, year
        assert {s.year for s in sides} == {year}
        assert len({canonical(s.franchise) for s in sides}) == n
        assert yours is None


def test_2016_has_the_two_stand_ins_and_not_the_two_suspended_sides(deck):
    clubs = {canonical(s.franchise) for s in C.year_sides(deck, 2016)[0]}
    assert "Chennai Super Kings" not in clubs and "Rajasthan Royals" not in clubs
    assert {canonical("Gujarat Lions"), canonical("Rising Pune Supergiants")} <= clubs


# --- ability -----------------------------------------------------------------------------


def _setup(role="opener", overseas=False):
    return C.Setup("Tester", role, 2012, overseas)


def test_ability_is_the_start_at_zero_and_the_ceiling_at_one(deck):
    for role in C.ROLES:
        start, ceiling = C.spin(deck, 11, role)
        kw = dict(setup=_setup(role), fs_id=1, year=2012, franchise="X")
        a0, a1 = C.ability(start, ceiling, 0.0, **kw), C.ability(start, ceiling, 1.0, **kw)
        for half in ("bat_scoring", "bat_dismissal", "bowl_scoring", "bowl_dismissal"):
            s, c = getattr(start, half), getattr(ceiling, half)
            assert getattr(a0, half) == s
            if s is not None and c is not None:
                assert getattr(a1, half) == pytest.approx(c, rel=1e-12)
        assert a0.positions == start.positions and a0.band == start.band


def test_a_dismissal_multiplier_mixes_geometrically():
    a = Card(fs_id=1, person_id="a", name="a", bat=0.0, bat_scoring=0.0, bat_dismissal=0.5)
    b = Card(fs_id=1, person_id="b", name="b", bat=0.0, bat_scoring=0.0, bat_dismissal=2.0)
    half = C.ability(a, b, 0.5, setup=_setup(), fs_id=1, year=2012, franchise="X")
    assert half.bat_dismissal == pytest.approx(1.0)     # not 1.25


def test_nothing_lifts_ability_above_the_ceiling_or_below_the_floor():
    for k in range(C.MAX_SEASONS):
        assert C.arc_point(k, 5.0, 5.0) == C.ABILITY_CAP
        assert C.arc_point(k, -5.0, -5.0) == C.ABILITY_FLOOR


def test_the_ceiling_is_better_at_the_roles_job_and_the_spin_is_fixed_by_the_seed(deck):
    for role in C.ROLES:
        for seed in range(25):
            start, ceiling = C.spin(deck, seed, role)
            assert C.main_rating(ceiling) > C.main_rating(start)
            assert C._fits(start, role) and C._fits(ceiling, role)
            assert C.START_DISPLAY[0] <= start.display <= C.START_DISPLAY[1]
            assert ceiling.display >= C.CEILING_MIN_DISPLAY
            assert C.spin(deck, seed, role) == (start, ceiling)


# --- your place in the side ---------------------------------------------------------------


@pytest.mark.parametrize("role, overseas", [("pace", True), ("spin", False),
                                            ("allrounder", True), ("opener", True),
                                            ("keeper", False)])
def test_you_are_in_every_franchises_eleven_and_a_bowler_always_bowls(deck, model, role, overseas):
    start, ceiling = C.spin(deck, 4, role)
    for fs, squad in deck.cards_by_fs.items():
        c0 = squad[0]
        you = C.ability(start, ceiling, 0.0, setup=_setup(role, overseas), fs_id=fs,
                        year=c0.season_year, franchise=c0.franchise)
        xi, impact = C.xi_with(you, list(squad))
        mine = [c for c in xi if c.person_id == C.YOU_ID]
        assert len(mine) == 1 and len(xi) == 11, (role, fs)
        assert viable(xi)
        assert sum(c.overseas is True for c in xi) <= OVERSEAS_LIMIT
        me = mine[0]
        assert impact is None or impact.person_id != C.YOU_ID
        # your engine numbers are untouched, whatever the side does with its ranking
        for half in ("bat_scoring", "bat_dismissal", "bowl_scoring", "bowl_dismissal", "bat"):
            assert getattr(me, half) == getattr(you, half)
        if C.bowls_for_you(you):
            assert C.YOU_ID in [p.person_id for p in attack(xi, model)], (role, fs)
            # and with a bowling Impact Player on, replacing the weakest pure batter:
            # still you among the five
            if impact is not None and impact.has_bowl:
                assert impact.bowl <= me.bowl
                batters = [c for c in xi if not c.has_bowl and c.role != "keeper"]
                if batters:
                    out = min(batters, key=lambda c: c.bat if c.has_bat else -9.9)
                    swapped = [impact if c is out else c for c in xi]
                    assert C.YOU_ID in [p.person_id for p in attack(swapped, model)]


# --- a season's numbers -------------------------------------------------------------------


@pytest.fixture(scope="module")
def one_season(deck, model):
    start, ceiling = C.spin(deck, 3, "pace")
    fs = C.year_fs_ids(deck, 2014)[2]
    c0 = deck.cards_by_fs[fs][0]
    you = C.ability(start, ceiling, 0.6, setup=_setup("pace"), fs_id=fs, year=2014,
                    franchise=c0.franchise)
    season, yours, results = C.simulate_year(deck, model, 3, 2, you)
    return season, yours, results, C.season_lines(results)


def test_every_runs_and_wickets_line_adds_up_to_the_scorecards(one_season):
    """Batters' runs plus extras is each side's total, and bowlers' wickets are the other
    side's wickets lost (every dismissal here is the bowler's, A116), summed per side."""
    season, _, results, lines = one_season
    for side in season.sides:
        mine = [l for l in lines.values() if l.side is side]
        batted = [inn for r in results for s, inn in ((r.home, r.home_innings),
                                                       (r.away, r.away_innings)) if s is side]
        bowled = [inn for r in results for s, inn in ((r.home, r.away_innings),
                                                       (r.away, r.home_innings)) if s is side]
        assert sum(l.runs for l in mine) == sum(i.runs - i.extras for i in batted)
        assert sum(l.wickets for l in mine) == sum(i.wickets for i in bowled)
        assert sum(l.bowl_balls for l in mine) == sum(i.balls for i in bowled)
        assert max(l.matches for l in mine) == len(batted)


def test_the_caps_agree_with_the_tournaments_own_leaders(one_season):
    """The career marks a cap by the same ordering `tournament_leaders` uses (A175)."""
    _, _, results, lines = one_season
    leaders = tournament_leaders(results)
    top, best = C.season_caps(lines)
    assert (top.card.name, top.runs) == leaders.top_scorer
    assert top.side is leaders.top_scorer_side
    assert (best.card.name, best.wickets) == leaders.top_wicket_taker
    assert best.side is leaders.top_wicket_taker_side


# --- the season card ---------------------------------------------------------------------


def _line(role, runs=0, balls=0, outs=0, wickets=0, bowl_balls=0, bowl_runs=0,
          matches=14, innings=14, pid=None):
    card = Card(fs_id=1, person_id=pid or f"p{random.random()}", name="x", role=role)
    return C.Line(card=card, side=None, matches=matches, innings=innings, runs=runs,
                  balls=balls, outs=outs, wickets=wickets, bowl_balls=bowl_balls,
                  bowl_runs=bowl_runs)


def _peers(make, n=12):
    return {(f"q{i}", 0): make(i) for i in range(n)}


def test_the_best_batter_of_the_season_is_at_the_top_and_the_worst_at_the_bottom():
    peers = _peers(lambda i: _line("batter", runs=300 + 10 * i, balls=250, outs=10))
    best = {**peers, ("me", 0): _line("batter", runs=700, balls=400, outs=8)}
    worst = {**peers, ("me", 0): _line("batter", runs=90, balls=110, outs=13)}
    assert C.season_percentile(best, ("me", 0)) == 1.0
    assert C.season_percentile(worst, ("me", 0)) == 0.0


def test_a_bowler_is_ranked_on_wickets_and_economy_among_bowlers():
    peers = _peers(lambda i: _line("bowler", wickets=10 + i, bowl_balls=300, bowl_runs=400))
    best = {**peers, ("me", 0): _line("bowler", wickets=28, bowl_balls=330, bowl_runs=330)}
    worst = {**peers, ("me", 0): _line("bowler", wickets=4, bowl_balls=300, bowl_runs=520)}
    assert C.season_percentile(best, ("me", 0)) == 1.0
    assert C.season_percentile(worst, ("me", 0)) == 0.0


def test_a_batter_is_not_ranked_against_bowlers_runs():
    """Forty bowlers who made nothing must not make an ordinary batter the season's best."""
    peers = _peers(lambda i: _line("batter", runs=400 + 10 * i, balls=300, outs=10))
    tail = {(f"t{i}", 0): _line("bowler", runs=20, balls=20, outs=6, innings=8) for i in range(40)}
    me = {("me", 0): _line("batter", runs=300, balls=300, outs=12)}
    assert C.season_percentile({**peers, **tail, **me}, ("me", 0)) == 0.0


def test_a_percentile_reads_the_real_cards_from_lowest_to_highest():
    real = [70, 74, 75, 81, 99]
    assert C.quantile(real, 0.0) == 70 and C.quantile(real, 1.0) == 99
    assert C.quantile(real, 0.5) == 75
    assert [C.quantile(real, p / 20) for p in range(21)] == sorted(
        C.quantile(real, p / 20) for p in range(21))


def test_a_years_card_scale_is_that_years_real_regulars(deck):
    """p = 1 is the best real regular of the year in that kind, p = 0 the worst."""
    for year in (2008, 2016, 2025):
        cards = [c for fs in C.year_fs_ids(deck, year) for c in deck.cards_by_fs[fs]
                 if c.role in ("batter", "keeper") and (c.bat_balls or 0) >= 100]
        you = Card(fs_id=1, person_id="y", name="y", role="batter")
        assert C.season_display(deck, year, you, 1.0) == max(c.display for c in cards)
        assert C.season_display(deck, year, you, 0.0) == max(70, min(c.display for c in cards))


# --- offers and events ----------------------------------------------------------------------


def test_a_debutant_is_offered_the_base_price_and_no_stay(deck):
    offers = C.offers_for(deck, 1, 0, 2012, None, None)
    assert len(offers) == C.OFFERS and not any(o.stay for o in offers)
    assert {o.price for o in offers} == {C.DEBUT_PRICE}


def test_staying_survives_a_rename_and_not_a_suspension(deck):
    """A career carries its club as the CANONICAL franchise. Both directions are checked,
    since in the year of a rename the old and the new spelling can agree by accident."""
    delhi = canonical("Delhi Daredevils")
    assert any(o.stay and o.franchise == "Delhi Daredevils"
               for o in C.offers_for(deck, 1, 3, 2015, delhi, 85))
    assert any(o.stay and o.franchise == "Delhi Capitals"
               for o in C.offers_for(deck, 1, 3, 2019, delhi, 85))
    punjab = canonical("Kings XI Punjab")
    assert any(o.stay for o in C.offers_for(deck, 1, 3, 2014, punjab, 85))
    assert any(o.stay for o in C.offers_for(deck, 1, 3, 2024, punjab, 85))
    assert not any(o.stay for o in C.offers_for(deck, 1, 3, 2016,
                                                canonical("Chennai Super Kings"), 85))
    offers = C.offers_for(deck, 1, 3, 2019, delhi, 85)
    assert all(canonical(o.franchise) != delhi for o in offers if not o.stay)


def test_offers_follow_last_seasons_card_and_never_pass_the_record(deck):
    for display in (72, 80, 88, 95, 99):
        value = value_curve(display, mega=True)
        for o in C.offers_for(deck, 9, 4, 2020, None, display):
            assert o.price <= C.PRICE_CAP
            assert o.price == min(C.PRICE_CAP, max(C.DEBUT_PRICE, o.price))
            if value * (1 + C.PRICE_NOISE) < C.PRICE_CAP:
                assert value * (1 - C.PRICE_NOISE) - 5 <= o.price <= value * (1 + C.PRICE_NOISE) + 5


def test_event_cards_never_repeat_back_to_back_or_pass_their_limits():
    for seed in range(400):
        seq = C.event_sequence(seed)
        assert len(seq) == C.MAX_SEASONS
        for a, b in zip(seq, seq[1:]):
            assert a.key == "quiet" or a is not b
        counts = Counter(e.key for e in seq)
        for key, limit in C.EVENT_LIMIT.items():
            assert counts[key] <= limit


# --- the move protocol and replay -----------------------------------------------------------


def _event_seed(want_choice: bool) -> int:
    return next(s for s in range(100) if (len(C.draw_event(s, 0).options) > 1) == want_choice)


def test_a_fresh_career_asks_for_a_franchise_first(deck, model):
    c = C.play_career(deck, model, 5, _setup(), [])
    assert c.pending.kind == "offer" and c.pending.k == 0 and not c.seasons


def test_an_event_with_a_choice_waits_for_the_answer(deck, model):
    seed = _event_seed(True)
    c = C.play_career(deck, model, seed, _setup(), ["o0"])
    assert c.pending.kind == "event" and not c.seasons
    with pytest.raises(C.CareerError):
        C.play_career(deck, model, seed, _setup(), ["o0", "o1"])


@pytest.mark.parametrize("moves", [["r"], ["x1"], ["o9"], ["e0"], ["o-1"]])
def test_moves_that_do_not_fit_are_refused(deck, model, moves):
    with pytest.raises(C.CareerError):
        C.play_career(deck, model, _event_seed(False), _setup(), moves)


def test_retiring_ends_the_career_and_moves_after_it_are_refused(deck, model):
    seed = next(s for s in range(100) if all(len(C.draw_event(s, k).options) == 1
                                             for k in range(2)))
    done = C.play_career(deck, model, seed, _setup(), ["o0", "r"])
    assert done.complete and done.retired and len(done.seasons) == 1
    assert done.summary.runs == done.seasons[0].runs
    with pytest.raises(C.CareerError):
        C.play_career(deck, model, seed, _setup(), ["o0", "r", "o0"])


def test_a_warm_replay_is_the_same_career_as_a_cold_one(deck, model):
    """The season cache must never change a career: the first version read the next
    season's price from the blank record on a cache hit and priced you as a 70."""
    seed = next(s for s in range(100) if all(len(C.draw_event(s, k).options) == 1
                                             for k in range(3)))
    moves = ["o1", "o2"]
    C._SEASON_CACHE.clear()
    cold = C.play_career(deck, model, seed, _setup(), moves)
    warm = C.play_career(deck, model, seed, _setup(), moves)
    assert len(cold.seasons) == 2
    assert [vars(s) for s in cold.seasons] == [vars(s) for s in warm.seasons]
    assert cold.pending.offers == warm.pending.offers
    # and the offers really are priced from the season just played
    last = cold.seasons[-1].display
    value = value_curve(last, mega=True)
    for o in cold.pending.offers:
        factor = C.STAY_DISCOUNT if o.stay else None
        if factor:
            assert o.price == min(C.PRICE_CAP, max(C.DEBUT_PRICE, 5 * round(value * factor / 5)))
        else:
            assert value * (1 - C.PRICE_NOISE) - 5 <= o.price <= value * (1 + C.PRICE_NOISE) + 5


def test_a_season_is_a_pure_function_of_its_inputs(deck, model):
    """Each season draws from its own seeded stream (A93), so the same inputs give the same
    season, whatever else has drawn random numbers in between."""
    seed = next(s for s in range(100) if all(len(C.draw_event(s, k).options) == 1
                                             for k in range(2)))
    start, ceiling = C.spin(deck, seed, "opener")
    fs = C.year_fs_ids(deck, 2013)[0]
    c0 = deck.cards_by_fs[fs][0]
    you = C.ability(start, ceiling, 0.4, setup=_setup(), fs_id=fs, year=2013,
                    franchise=c0.franchise)
    a = C.play_season(deck, model, seed, 1, you, C.SeasonRecord(1, 2013, "X", "X", 0, False,
                                                                "quiet", "", False, 0.4, 0))
    random.random()          # a stray draw on the global stream must not matter either
    b = C.play_season(deck, model, seed, 1, you, C.SeasonRecord(1, 2013, "X", "X", 0, False,
                                                                "quiet", "", False, 0.4, 0))
    assert vars(a) == vars(b)
