"""Build a Career (A197): the rules a career rests on, each checked against something
other than the code that applies it.

Runs against the committed deck snapshot (no database). Seasons are real simulations, so
only a handful are played; everything that can be checked without one is.
"""

from __future__ import annotations

import dataclasses
import math
import random
from collections import Counter

import pytest

from etl.feasibility import BOWLERS_IN_TWELVE, Card
from etl.franchise_map import canonical
from game import career as C
from game.__main__ import OVERSEAS_LIMIT, attack, bowling_order, viable
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



def _play(deck, model, seed, setup, moves, **kw):
    """`play_career` with every key moment answered "Balanced" (A204), every mega-auction year
    (A212) and bowling plan (A213) answered as an automatic player would, so a test written
    for offers and events keeps testing them. The given moves are fed one at a time."""
    moves, made = list(moves), []
    while True:
        c = C.play_career(deck, model, seed, setup, made, **kw)
        if c.pending is not None and c.pending.kind == "moment":
            made.append("m1")
            continue
        if c.pending is not None and c.pending.kind == "plan":
            made.append(C.policy_move(c, "strength", None))
            continue
        if c.pending is not None and c.pending.kind == "mega" and moves and moves[0][0] in "oc":
            moves.pop(0)                        # that season's offer move answers its mega year
            made.append(C.policy_move(c, "strength", None))
            continue
        if not moves:
            return c
        made.append(moves.pop(0))


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


def _setup(role="opener", overseas=False, debut=2012):
    return C.Setup("Tester", role, debut, overseas)


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
            assert C.main_rating(ceiling, role) > C.main_rating(start, role)
            assert C._fits(start, role) and C._fits(ceiling, role)
            if not C.ROLES[role].get("batting_only"):
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
                assert bowling_order(impact) >= bowling_order(me)      # never ahead of you
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
    year = C.simulate_year(deck, model, 3, 2, you)
    return year.season, year.yours, year.results, C.season_lines(year.results, year.roster)


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
          matches=14, innings=14, pid=None, band=None):
    card = Card(fs_id=1, person_id=pid or f"p{random.random()}", name="x", role=role,
                band=band)
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
    c = _play(deck, model, 5, _setup(), [])
    assert c.pending.kind == "offer" and c.pending.k == 0 and not c.seasons


def test_an_event_with_a_choice_waits_for_the_answer(deck, model):
    seed = _event_seed(True)
    c = _play(deck, model, seed, _setup(), ["o0"])
    assert c.pending.kind == "event" and not c.seasons
    with pytest.raises(C.CareerError):
        _play(deck, model, seed, _setup(), ["o0", "o1"])


@pytest.mark.parametrize("moves", [["r"], ["x1"], ["o9"], ["e0"], ["o-1"]])
def test_moves_that_do_not_fit_are_refused(deck, model, moves):
    with pytest.raises(C.CareerError):
        _play(deck, model, _event_seed(False), _setup(), moves)


def test_retiring_ends_the_career_and_moves_after_it_are_refused(deck, model):
    seed = next(s for s in range(100) if all(len(C.draw_event(s, k).options) == 1
                                             for k in range(2)))
    done = _play(deck, model, seed, _setup(), ["o0", "r"])
    assert done.complete and done.retired and len(done.seasons) == 1
    assert done.summary.runs == done.seasons[0].runs
    with pytest.raises(C.CareerError):
        _play(deck, model, seed, _setup(), ["o0", "r", "o0"])


def test_a_warm_replay_is_the_same_career_as_a_cold_one(deck, model):
    """The season cache must never change a career: the first version read the next
    season's price from the blank record on a cache hit and priced you as a 70."""
    seed = next(s for s in range(100) if all(len(C.draw_event(s, k).options) == 1
                                             for k in range(3)))
    moves = ["o1", "o2"]
    C._SEASON_CACHE.clear()
    cold = _play(deck, model, seed, _setup(), moves)
    warm = _play(deck, model, seed, _setup(), moves)
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


# --- availability, the reputation floor, all-rounder peers (A198) --------------------------


def _on_card(result, pid):
    return any(b.player.person_id == pid
               for inn in (result.home_innings, result.away_innings) if inn
               for b in inn.batting)


@pytest.fixture(scope="module")
def year_with_absence(deck, model):
    """A real season in which the drawn block of missed matches is not empty."""
    start, ceiling = C.spin(deck, 6, "opener")
    fs = C.year_fs_ids(deck, 2018)[1]
    c0 = deck.cards_by_fs[fs][0]
    you = C.ability(start, ceiling, 0.5, setup=_setup(), fs_id=fs, year=2018,
                    franchise=c0.franchise)
    # an opener can be dropped, so his block is drawn with the real drop rate taken off (A205)
    block = lambda k: C.missed_block(11, k, 14, form_drop=C.FORM_DROP_RATE["batter"])
    k = next(k for k in range(40) if len(block(k)) >= 2)
    year = C.simulate_year(deck, model, 11, k, you)
    assert year.dropped == [], "the fixture must hold no form drop, or it tests two things"
    return year, block(k)


def test_you_miss_exactly_the_drawn_block_and_never_a_playoff(year_with_absence):
    year, out = year_with_absence
    mine = [r for r in year.results if year.yours is r.home or year.yours is r.away]
    league = [r for r in mine if r.stage == "league"]
    assert len(league) == 14 and year.missed == len(out) >= 2
    for i, r in enumerate(league):
        assert _on_card(r, C.YOU_ID) == (i not in out), i
    assert all(_on_card(r, C.YOU_ID) for r in mine if r.stage != "league")
    # and the season's line counts only the matches you played
    lines = C.season_lines(year.results, year.roster)
    assert lines[(C.YOU_ID, id(year.yours))].matches == len(mine) - len(out)
    # the side is left holding you at the end, whatever the last league match was
    assert any(c.person_id == C.YOU_ID for c in year.yours.xi)


def test_a_missed_match_is_still_counted_for_the_side_that_played_it(year_with_absence):
    """Without the roster, the man who stood in for you would vanish from the season."""
    year, out = year_with_absence
    lines = C.season_lines(year.results, year.roster)
    runs = sum(l.runs for l in lines.values() if l.side is year.yours)
    total = sum(inn.runs - inn.extras for r in year.results
                for s, inn in ((r.home, r.home_innings), (r.away, r.away_innings))
                if s is year.yours)
    assert runs == total


def test_the_missed_block_comes_from_the_real_shares():
    lengths = Counter()
    for seed in range(2000):
        block = C.missed_block(seed, 0, 14)
        assert 0 <= block.start and block.stop <= 14
        lengths[len(block)] += 1
    allowed = {round((1 - s) * 14) for s in C.AVAILABILITY_SHARES}
    assert set(lengths) <= allowed
    full = sum(s == 1.0 for s in C.AVAILABILITY_SHARES) / len(C.AVAILABILITY_SHARES)
    assert abs(lengths[0] / 2000 - full) < 0.04
    assert sum(C.AVAILABILITY_SHARES) / len(C.AVAILABILITY_SHARES) == pytest.approx(0.86, abs=0.01)


@pytest.mark.parametrize("previous, form, want", [
    ([], 72, 72),               # a first season stands as it is
    ([90, 90], 70, 80),         # halfway back toward the career
    ([90, 90], 95, 95),         # a good season is never pulled down
    ([80], 60, 70),
    ([80, 70], 60, 68),         # the career is the mean of every season so far
    ([72, 74], 85, 85),
])
def test_the_reputation_floor_lifts_a_season_halfway_toward_the_career(previous, form, want):
    assert C.reputation_floor(previous, form) == want


def test_an_all_rounder_is_ranked_against_everybody_who_did_both():
    """Five listed all-rounders and twelve batters who bowl: the best of the five is not the
    season's best all-rounder if the batters who bowled did both better."""
    ars = {(f"a{i}", 0): _line("allrounder", runs=150 + i, balls=130, outs=8, wickets=6,
                               bowl_balls=150, bowl_runs=220) for i in range(5)}
    batters = {(f"b{i}", 0): _line("batter", runs=420 + i, balls=300, outs=8, wickets=12,
                                   bowl_balls=200, bowl_runs=240) for i in range(12)}
    me = {("me", 0): _line("allrounder", runs=200, balls=150, outs=8, wickets=8,
                           bowl_balls=180, bowl_runs=250)}
    assert C.season_percentile({**ars, **batters, **me}, ("me", 0)) == pytest.approx(5 / 17)


# --- form: squad seasons and release (A198) ------------------------------------------------


@pytest.mark.parametrize("squad, percentile, want", [
    (False, C.POOR_PERCENTILE, (False, False)),          # exactly the bar is not poor
    (False, C.POOR_PERCENTILE - 0.01, (True, False)),    # a poor season costs your place
    (True, C.POOR_PERCENTILE - 0.01, (True, True)),      # a second costs your career
    (True, C.POOR_PERCENTILE + 0.2, (False, False)),     # a good squad season wins it back
])
def test_form_decides_your_place(squad, percentile, want):
    assert C.form_after(squad, percentile) == want


def _quiet_seed(n):
    return next(s for s in range(300) if all(len(C.draw_event(s, k).options) == 1
                                             for k in range(n)))


def test_two_poor_seasons_end_a_career(deck, model, monkeypatch):
    """Every season poor: a regular season, a squad season, then nobody signs you."""
    monkeypatch.setattr(C, "POOR_PERCENTILE", 1.01)
    seed = _quiet_seed(3)
    first = _play(deck, model, seed, _setup(), ["o0"])
    assert first.pending.kind == "offer" and first.pending.squad
    done = _play(deck, model, seed, _setup(), ["o0", "o0"])
    assert done.complete and done.released and not done.retired
    assert [r.squad for r in done.seasons] == [False, True]
    assert done.summary.regular_seasons == 1
    with pytest.raises(C.CareerError):
        _play(deck, model, seed, _setup(), ["o0", "o0", "o0"])


def test_good_form_keeps_you_a_regular(deck, model, monkeypatch):
    monkeypatch.setattr(C, "POOR_PERCENTILE", 0.0)
    seed = _quiet_seed(3)
    c = _play(deck, model, seed, _setup(), ["o0", "o0", "o0"])
    assert [r.squad for r in c.seasons] == [False, False, False] and not c.released


def test_a_squad_season_is_part_of_the_league_and_none_of_the_playoffs(deck, model):
    start, ceiling = C.spin(deck, 6, "pace")
    fs = C.year_fs_ids(deck, 2019)[0]
    c0 = deck.cards_by_fs[fs][0]
    you = C.ability(start, ceiling, 0.5, setup=_setup("pace"), fs_id=fs, year=2019,
                    franchise=c0.franchise)
    # a season where the form-drop shortening WOULD fire, so a squad block shortened by
    # mistake shows (a squad player cannot be dropped for form, A205)
    q = C.FORM_DROP_RATE["bowler"]
    k = next(k for k in range(60) if len(C.missed_block(21, k, 14, squad=True, form_drop=q))
             != len(C.missed_block(21, k, 14, squad=True)))
    out = C.missed_block(21, k, 14, squad=True)
    year = C.simulate_year(deck, model, 21, k, you, squad=True)
    mine = [r for r in year.results if year.yours is r.home or year.yours is r.away]
    league = [r for r in mine if r.stage == "league"]
    assert [_on_card(r, C.YOU_ID) for r in league] == [i not in out for i in range(14)]
    assert not any(_on_card(r, C.YOU_ID) for r in mine if r.stage != "league")


def test_a_squad_season_plays_the_real_non_regular_share():
    lengths = Counter(len(C.missed_block(s, 1, 14, squad=True)) for s in range(2000))
    assert set(lengths) <= {round((1 - x) * 14) for x in C.SQUAD_SHARES}
    played = sum((14 - n) * c for n, c in lengths.items()) / 2000 / 14
    assert played == pytest.approx(sum(C.SQUAD_SHARES) / len(C.SQUAD_SHARES), abs=0.02)


def test_a_finisher_is_ranked_among_finishers_not_openers():
    """A163 compares a card within its role: the best finisher of the year is the best
    finisher, however many more runs the openers made."""
    openers = {(f"o{i}", 0): _line("batter", runs=450 + 10 * i, balls=330, outs=11,
                                   band="opener") for i in range(16)}
    finishers = {(f"f{i}", 0): _line("batter", runs=150 + 5 * i, balls=110, outs=7,
                                     band="finisher") for i in range(10)}
    me = {("me", 0): _line("batter", runs=260, balls=160, outs=6, band="finisher")}
    assert C.season_percentile({**openers, **finishers, **me}, ("me", 0)) == 1.0


def test_too_few_of_your_band_ranks_you_among_every_batter():
    openers = {(f"o{i}", 0): _line("batter", runs=450 + 10 * i, balls=330, outs=11,
                                   band="opener") for i in range(16)}
    finishers = {(f"f{i}", 0): _line("batter", runs=100, balls=100, outs=8,
                                     band="finisher") for i in range(C.MIN_BAND_PEERS - 1)}
    # bowlers who batted: in the season, never among the batters you are ranked against
    tail = {(f"t{i}", 0): _line("bowler", runs=40, balls=50, outs=7, band="tail")
            for i in range(10)}
    # better than every finisher and every bowler, worse than every opener on all three
    me = {("me", 0): _line("batter", runs=200, balls=150, outs=8, band="finisher")}
    p = C.season_percentile({**openers, **finishers, **tail, **me}, ("me", 0))
    assert p == pytest.approx((C.MIN_BAND_PEERS - 1) / (16 + C.MIN_BAND_PEERS - 1))


def test_the_finisher_band_counts_its_all_rounders_and_not_its_bowlers():
    """Three batters in the band is too few on their own; the all-rounders who bat there
    make it a peer group. The bowlers who bat at six or seven do not count: against them
    this finisher would be the best of the band, against the all-rounders he is not."""
    openers = {(f"o{i}", 0): _line("batter", runs=500, balls=330, outs=11, band="opener")
               for i in range(16)}
    finishers = {(f"f{i}", 0): _line("batter", runs=150, balls=110, outs=7, band="finisher")
                 for i in range(3)}
    ars = {(f"a{i}", 0): _line("allrounder", runs=300, balls=180, outs=6, band="finisher")
           for i in range(6)}
    lower = {(f"b{i}", 0): _line("bowler", runs=60, balls=55, outs=6, band="finisher")
             for i in range(8)}
    me = {("me", 0): _line("batter", runs=240, balls=150, outs=6, band="finisher")}
    assert C.season_percentile({**openers, **finishers, **ars, **lower, **me},
                               ("me", 0)) == pytest.approx(3 / 9)


def test_an_all_rounder_bowls_in_his_share_of_matches_and_never_leaves_four(deck, model):
    start, ceiling = C.spin(deck, 2, "allrounder")
    shares, rested_possible = [], 0
    for fs in C.year_fs_ids(deck, 2015)[:4]:
        c0 = deck.cards_by_fs[fs][0]
        you = C.ability(start, ceiling, 0.6, setup=_setup("allrounder"), fs_id=fs,
                        year=2015, franchise=c0.franchise)
        year = C.simulate_year(deck, model, 31, 4, you)
        mine = [r for r in year.results if year.yours is r.home or year.yours is r.away]
        bowled = 0
        for r in mine:
            inn = r.away_innings if year.yours is r.home else r.home_innings
            attack = [w for w in inn.bowling if w.balls]
            assert len({w.player.person_id for w in inn.bowling}) >= 5
            bowled += any(w.player.person_id == C.YOU_ID for w in attack)
        played = sum(_on_card(r, C.YOU_ID) for r in mine)
        rested = C._rested_lineup(year.yours, next(c for c in year.yours.xi
                                                   if c.person_id == C.YOU_ID))
        if rested is None:
            assert bowled == played          # no five others to rest behind: always bowls
        else:
            rested_possible += 1
            shares.append(bowled / played)
    assert rested_possible and all(0.2 < s < 0.95 for s in shares)


def test_a_finisher_bats_like_his_templates_and_never_bowls(deck):
    starts, ceilings = C.template_pools(deck, "finisher")
    assert any(c.role == "allrounder" for c in ceilings)     # real finishers are, mostly
    for seed in range(30):
        start, ceiling = C.spin(deck, seed, "finisher")
        you = C.ability(start, ceiling, 0.5, setup=_setup("finisher"), fs_id=1, year=2015,
                        franchise="X")
        assert you.role == "batter" and not you.has_bowl and you.bowl_scoring is None
        assert you.bat_scoring == pytest.approx(
            start.bat_scoring + (ceiling.bat_scoring - start.bat_scoring) * 0.5)
        assert not C.bowls_for_you(you)


def test_a_batting_only_roles_spins_are_drawn_on_batting(deck):
    """Middle third of the band's batting to start, top third as a ceiling."""
    starts, ceilings = C.template_pools(deck, "finisher")
    assert min(c.bat for c in ceilings) >= max(c.bat for c in starts)
    everyone = [c for cs in deck.cards_by_fs.values() for c in cs if C._fits(c, "finisher")]
    assert len(starts) + len(ceilings) == len(everyone) - len(everyone) // 3


def test_an_all_rounder_is_only_rested_when_five_others_can_bowl():
    """Resting him from a four-bowler eleven would leave the attack short of five, which
    cannot get through twenty overs (A133)."""
    from game.season import Side

    def card(i, bowl):
        return Card(fs_id=1, person_id=f"c{i}", name=f"c{i}", role="bowler" if bowl else "batter",
                    bowl=0.0 if bowl else None, bat=0.0)

    me = Card(fs_id=1, person_id=C.YOU_ID, name="me", role="allrounder", bat=0.1, bowl=0.1)
    four = Side(name="a", short="A", xi=[me] + [card(i, i < 4) for i in range(10)])
    five = Side(name="b", short="B", xi=[me] + [card(i, i < 5) for i in range(10)])
    assert C._rested_lineup(four, me) is None
    rested, _ = C._rested_lineup(five, me)
    assert sum(c.has_bowl and c.bowl > C._RESTED_BOWL for c in rested) == 5
    batter = Card(fs_id=1, person_id=C.YOU_ID, name="me", role="batter", bat=0.1, bowl=0.1)
    assert C._rested_lineup(Side(name="c", short="C", xi=[batter] + five.xi[1:]), batter) is None


def test_a_finisher_keeps_his_place_down_to_his_own_bar(deck, model, monkeypatch):
    """The finisher's declared bar, not the general one, decides his place -- checked in a
    real career by setting the general bar so high that only the exception can save him."""
    assert C.poor_bar("finisher") < C.POOR_PERCENTILE == C.poor_bar("opener")
    monkeypatch.setattr(C, "POOR_PERCENTILE", 1.01)
    monkeypatch.setitem(C.ROLES["finisher"], "poor_percentile", 0.0)
    seed = _quiet_seed(3)
    c = _play(deck, model, seed, _setup("finisher"), ["o0", "o0", "o0"])
    assert [r.squad for r in c.seasons] == [False, False, False] and not c.released
    opener = _play(deck, model, seed, _setup("opener"), ["o0", "o0"])
    assert opener.released


def test_a_squad_player_is_never_made_captain(deck, model, monkeypatch):
    """Every season poor, so the second is a squad season; with the captaincy card drawn for
    it, it must arrive as a quiet pre-season instead."""
    monkeypatch.setattr(C, "POOR_PERCENTILE", 1.01)
    seed = next(s for s in range(500) if C.draw_event(s, 1).key == "captain"
                and len(C.draw_event(s, 0).options) == 1)
    first = _play(deck, model, seed, _setup(), ["o0", "o0"])
    assert first.seasons[0].squad is False and first.complete      # released after two
    second = first.seasons[1]
    assert second.squad and not second.captain and second.event == "quiet"


# --- contracts, where you play, the new cards, real names (A200) ----------------------------


def _seed_quiet(n, overseas=False):
    return next(s for s in range(2000) if all(len(C.draw_event(s, k, overseas).options) == 1
                                              for k in range(n)))


def _lasting_offer(deck, model, seed, setup, years=3):
    """An offer from a club with a team in each of the next `years` seasons."""
    offers = _play(deck, model, seed, setup, []).pending.offers
    clubs = [{C.canonical(deck.cards_by_fs[fs][0].franchise)
              for fs in C.year_fs_ids(deck, setup.debut + y)} for y in range(years)]
    return next(i for i, o in enumerate(offers)
                if all(C.canonical(o.franchise) in c for c in clubs))


def test_a_three_year_deal_is_three_seasons_at_one_price_then_the_market(deck, model):
    # 2014-16, then an ordinary market in 2017: a deal from 2012 would reach the 2014 mega
    # auction, which ends every deal (A212), and one from 2015 would end into 2018's. A debut
    # is a signing even in a mega year.
    seed = _seed_quiet(4)
    i = _lasting_offer(deck, model, seed, _setup(debut=2014))
    c = _play(deck, model, seed, _setup(debut=2014), [f"c{i}"])
    assert _play(deck, model, seed, _setup(debut=2014), []).pending.offers[i].price3 == C.DEBUT_PRICE
    for year in (2, 3):
        o = c.pending.offers
        assert len(o) == 1 and o[0].stay and o[0].contract_year == year
        assert o[0].franchise == c.seasons[0].franchise and o[0].price == c.seasons[0].price
        c = _play(deck, model, seed, _setup(debut=2014), [f"c{i}"] + ["o0"] * (year - 1))
    assert [r.contract_year for r in c.seasons] == [1, 2, 3]
    assert len(c.pending.offers) > 1 and not any(o.contract_year for o in c.pending.offers)


def test_a_signed_year_cannot_be_signed_again(deck, model):
    seed = _seed_quiet(2)
    with pytest.raises(C.CareerError):
        _play(deck, model, seed, _setup(), ["c1", "c0"])


def test_a_contract_protects_you_from_release_and_then_does_not(deck, model, monkeypatch):
    """Every season poor: a one-year player is released after two, a three-year one plays
    out his deal and goes after it ends."""
    monkeypatch.setattr(C, "POOR_PERCENTILE", 1.01)
    seed = _seed_quiet(4)
    one = _play(deck, model, seed, _setup(debut=2015), ["o0", "o0"])
    assert one.released and len(one.seasons) == 2
    i = _lasting_offer(deck, model, seed, _setup(debut=2015))
    three = _play(deck, model, seed, _setup(debut=2015), [f"c{i}", "o0", "o0"])
    assert three.released and len(three.seasons) == 3
    assert [r.squad for r in three.seasons] == [False, True, True]


def test_a_contract_lapses_when_the_club_has_no_team(deck, model):
    """Chennai have no team in 2016: a deal signed there in 2015 cannot carry over."""
    setup = C.Setup("Tester", "opener", 2015)
    seed = next(s for s in range(2000) if len(C.draw_event(s, 0).options) == 1 and any(
        "Chennai" in o.franchise for o in C.offers_for(deck, s, 0, 2015, None, None)))
    i = next(n for n, o in enumerate(_play(deck, model, seed, setup, []).pending.offers)
             if "Chennai" in o.franchise)
    c = _play(deck, model, seed, setup, [f"c{i}"])
    assert len(c.pending.offers) > 1 and not any(o.contract_year for o in c.pending.offers)
    assert c.checkpoint.contract is None


def test_each_offer_says_where_you_would_play_in_that_eleven(deck, model):
    """The position on the offer is the one the season then uses, for a batter and a
    bowler alike (no event, so the season's ability is the offer's)."""
    for role in ("opener", "pace"):
        seed = _seed_quiet(1)
        c = _play(deck, model, seed, _setup(role), [])
        for o in c.pending.offers:
            assert 1 <= o.position <= 11
            assert (o.attack_rank is not None) == (role == "pace")
            if o.attack_rank is not None:
                assert 1 <= o.attack_rank <= 5
            you = C.ability(c.start, c.ceiling, C.arc_point(0, 0.0, 0.0), setup=_setup(role),
                            fs_id=o.fs_id, year=2012, franchise=o.franchise)
            _, yours = C.year_sides(deck, 2012, you)
            assert next(i for i, x in enumerate(yours.xi, 1) if x.person_id == C.YOU_ID) == o.position


def test_late_and_overseas_cards_only_reach_who_they_are_for():
    for seed in range(600):
        for overseas in (False, True):
            seq = C.event_sequence(seed, overseas)
            for k, e in enumerate(seq):
                if e.when == "late":
                    assert k >= C.LATE_FROM
                if e.when == "overseas":
                    assert overseas
    seen = {e.key for s in range(600) for e in C.event_sequence(s, True)}
    assert {"one_more", "home_league", "injury", "mentor", "rivalry", "slump"} <= seen


def test_retiring_on_a_high_ends_the_career_before_the_season(deck, model, monkeypatch):
    """Nobody is released here (POOR_PERCENTILE 0), so the career reaches the late card."""
    monkeypatch.setattr(C, "POOR_PERCENTILE", 0.0)
    setup = C.Setup("Tester", "opener", 2008)
    seed, k = next((s, k) for s in range(5000) for k in range(C.LATE_FROM, 14)
                   if C.draw_event(s, k).key == "one_more"
                   and all(len(C.draw_event(s, j).options) == 1 for j in range(k)))
    moves = ["o0"] * k
    before = _play(deck, model, seed, setup, moves + ["o0"])
    assert before.pending.kind == "event" and before.pending.event.key == "one_more"
    done = _play(deck, model, seed, setup, moves + ["o0", "e1"])
    assert done.retired and done.complete and len(done.seasons) == k


def test_an_event_that_costs_matches_adds_them_to_the_block():
    for seed in range(200):
        base = C.missed_block(seed, 2, 14)
        hurt = C.missed_block(seed, 2, 14, extra=4)
        assert len(hurt) == min(14, len(base) + 4) and hurt.start <= 14 - len(hurt)


def test_a_mentor_is_a_real_teammate_and_a_rival_a_real_opponent(deck):
    mentor = next(e for e in C.EVENTS if e.key == "mentor")
    rival = next(e for e in C.EVENTS if e.key == "rivalry")
    for fs in C.year_fs_ids(deck, 2018):
        squad = deck.cards_by_fs[fs]
        bowler = Card(fs_id=fs, person_id=C.YOU_ID, name="me", role="bowler")
        name = C.event_name(deck, mentor, fs, 2018, bowler)
        assert name in {c.name for c in squad if (c.bowl_balls or 0) >= 150}
        rname = C.event_name(deck, rival, fs, 2018, bowler)
        assert rname not in {c.name for c in squad}
        assert rname in {c.name for f in C.year_fs_ids(deck, 2018) for c in deck.cards_by_fs[f]}


def test_a_highlight_names_a_real_teammate_from_that_match(deck, model):
    start, ceiling = C.spin(deck, 8, "opener")
    for fs in C.year_fs_ids(deck, 2016):
        c0 = deck.cards_by_fs[fs][0]
        you = C.ability(start, ceiling, 1.0, setup=_setup(), fs_id=fs, year=2016,
                        franchise=c0.franchise)
        year = C.simulate_year(deck, model, 13, 1, you)
        hl = C._highlights(year.results, year.yours)
        squad_names = {c.name for c in deck.cards_by_fs[fs]}
        for h in hl:
            if " · " in h.text and "the final" not in h.text:
                named = h.text.split(" · ", 1)[1].rsplit(" ", 1)[0]
                assert named in squad_names, h.text
        if any(" · " in h.text for h in hl):
            return
    pytest.fail("no highlight with a teammate in eight seasons")


def test_a_spinners_mentor_is_a_spinner_who_bowled_that_season(deck):
    """Whoever bowled real overs of your style that season, by A33's floor, whatever the
    archive calls his role -- checked against an independent filter of every squad."""
    mentor = next(e for e in C.EVENTS if e.key == "mentor")
    checked = 0
    for fs, squad in deck.cards_by_fs.items():
        spinners = [c for c in squad if c.bowling_style == "spin" and c.display is not None
                    and (c.bowl_balls or 0) >= 150]
        if not spinners:
            continue
        you = Card(fs_id=fs, person_id=C.YOU_ID, name="me", role="bowler", bowling_style="spin")
        name = C.event_name(deck, mentor, fs, squad[0].season_year, you)
        assert name == max(spinners, key=lambda c: (c.display, c.person_id)).name
        checked += 1
    assert checked > 100
    gl = next(fs for fs, v in deck.cards_by_fs.items()
              if v[0].franchise == "Gujarat Lions" and v[0].season_year == 2017)
    you = Card(fs_id=gl, person_id=C.YOU_ID, name="me", role="bowler", bowling_style="spin")
    assert C.event_name(deck, mentor, gl, 2017, you) == "RA Jadeja"


def test_a_finals_line_names_its_real_top_scorer_you_included(deck, model):
    """Across real seasons, every final line names whoever made the most runs for your side
    in that final -- and says "you" when it was you."""
    start, ceiling = C.spin(deck, 8, "opener")
    finals = 0
    for year in (2010, 2016, 2019, 2023):
        for fs in C.year_fs_ids(deck, year):
            c0 = deck.cards_by_fs[fs][0]
            you = C.ability(start, ceiling, 1.0, setup=_setup(), fs_id=fs, year=year,
                            franchise=c0.franchise)
            y = C.simulate_year(deck, model, 17, 1, you)
            final = y.results[-1]
            if y.yours is not final.home and y.yours is not final.away:
                continue
            inn = final.home_innings if y.yours is final.home else final.away_innings
            top = max(inn.batting, key=lambda b: (b.runs, -b.balls))
            line = next(h.text for h in C._highlights(y.results, y.yours) if "the final" in h.text)
            if top.player.person_id == C.YOU_ID:
                assert f"you top-scored with {top.runs}" in line
            else:
                assert f"{top.player.name} {top.runs}" in line and "you top-scored" not in line
            finals += 1
    assert finals >= 3


# --- the season as it happened (A202) -------------------------------------------------------


@pytest.fixture(scope="module")
def detailed(deck, model):
    """A real season with you in it, and its record and detail."""
    start, ceiling = C.spin(deck, 3, "allrounder")
    fs = C.year_fs_ids(deck, 2019)[3]
    c0 = deck.cards_by_fs[fs][0]
    you = C.ability(start, ceiling, 0.8, setup=_setup("allrounder"), fs_id=fs, year=2019,
                    franchise=c0.franchise)
    record = C.SeasonRecord(0, 2019, c0.franchise, "X", 20, False, "quiet", "", False, 0.8, 0)
    year = C.simulate_year(deck, model, 23, 4, you)
    rec, detail = C.play_season_detail(deck, model, 23, 4, you, record)
    return year, rec, detail


def _told(year):
    """A career's league is PLAYED in matchday order (A204), so its results are the order
    to tell them in."""
    return list(year.results)


def test_the_feed_is_every_match_of_your_side_in_matchday_order(detailed):
    year, rec, detail = detailed
    mine = [r for r in _told(year) if year.yours is r.home or year.yours is r.away]
    assert len(mine) == sum(year.yours is r.home or year.yours is r.away for r in year.results)
    assert [l.stage for l in detail.feed] == [r.stage for r in mine]
    assert [l.result for l in detail.feed] == [
        "T" if r.winner is None else "W" if r.winner is year.yours else "L" for r in mine]
    assert detail.feed[-1].wins == sum(r.winner is year.yours for r in mine)


def test_matchday_order_keeps_every_side_within_a_game_of_the_rest(detailed):
    year, _, _ = detailed
    played = {id(s): 0 for s in year.season.sides}
    for r in [r for r in _told(year) if r.stage == "league"]:
        played[id(r.home)] += 1
        played[id(r.away)] += 1
        assert max(played.values()) - min(played.values()) <= 2


def test_the_running_table_ends_where_the_season_did(detailed):
    """The position on each line is the table at that moment -- after your last league
    match other sides still play, so the FINAL table is the one to compare."""
    year, rec, detail = detailed
    assert next(i for i, r in enumerate(detail.table, 1) if r.you) == rec.position
    assert [r.name for r in detail.table] == [s.side.name for s in year.season.table]
    assert [r.points for r in detail.table] == [s.points for s in year.season.table]


def test_your_matches_add_up_to_your_season(detailed):
    _, rec, detail = detailed
    assert sum(l.runs for l in detail.feed) == rec.runs == detail.feed[-1].season_runs
    assert sum(l.wickets for l in detail.feed) == rec.wickets == detail.feed[-1].season_wickets
    assert sum(l.played for l in detail.feed) == rec.matches


def test_the_cap_race_is_the_tournaments_own_leaders_after_every_match(detailed):
    """After each of your matches, compared with `tournament_leaders` over the matches told
    so far -- every line, not just the last, so a wrong ordering has many chances to show."""
    year, rec, detail = detailed
    told = _told(year)
    mine = [i for i, r in enumerate(told) if year.yours is r.home or year.yours is r.away]
    differed = 0
    for line, i in zip(detail.feed, mine):
        then = tournament_leaders(told[:i + 1])
        assert line.orange[:2] == then.top_scorer
        assert line.purple[:2] == then.top_wicket_taker
        differed += line.orange[0] != detail.feed[-1].orange[0]
    assert differed            # the lead changed hands at least once, so this is a real check


def test_the_matches_to_watch_are_your_playoffs_final_last(detailed):
    year, rec, detail = detailed
    po = [r for r in year.season.playoffs if year.yours is r.home or year.yours is r.away]
    assert [r.stage for r in detail.watch] == [r.stage for r in po]
    assert all(l.watch is not None for l in detail.feed if l.stage != "league")
    assert all(l.watch is None for l in detail.feed if l.stage == "league")
    if rec.finalist:
        assert detail.watch[-1].stage == "Final"


def test_a_season_played_in_a_call_carries_its_detail_and_a_resume_does_not(deck, model):
    seed = _seed_quiet(2)
    played = _play(deck, model, seed, _setup(), ["o0"])
    assert played.detail is not None and played.detail.feed
    resumed = _play(deck, model, seed, _setup(), [], resume=played.checkpoint)
    assert resumed.detail is None


def test_each_lines_position_is_the_table_at_that_moment(detailed):
    """Recomputed independently from the results up to that match, by points and wins."""
    year, rec, detail = detailed
    league = [r for r in _told(year) if r.stage == "league"]
    mine = [i for i, r in enumerate(league) if year.yours is r.home or year.yours is r.away]
    lines = [l for l in detail.feed if l.stage == "league"]
    for line, upto in zip(lines, mine):
        pts = {s.name: 0 for s in year.season.sides}           # a side yet to play is on 0
        for r in league[:upto + 1]:
            if r.winner is None:
                pts[r.home.name] += 1; pts[r.away.name] += 1
            else:
                pts[r.winner.name] += 2
        mine_pts = pts[year.yours.name]
        above = sum(p > mine_pts for p in pts.values())
        level = sum(p == mine_pts for p in pts.values())
        assert above + 1 <= line.position <= above + level        # NRR decides within a level



# --- key moments (A204) ----------------------------------------------------------------------


def test_an_approach_moves_both_numbers_at_the_measured_rate():
    me = Card(fs_id=1, person_id=C.YOU_ID, name="me", role="allrounder", bat=0.1, bowl=0.1,
              bat_scoring=0.02, bat_dismissal=1.0, bowl_scoring=0.01, bowl_dismissal=1.0)
    assert C.approach_card(me, 1) is me
    up, down = C.approach_card(me, 2), C.approach_card(me, 0)
    rate = math.exp(1.25 * 0.15)
    assert up.bat_scoring == pytest.approx(0.17) and up.bat_dismissal == pytest.approx(rate)
    assert up.bowl_scoring == pytest.approx(-0.14) and up.bowl_dismissal == pytest.approx(rate)
    assert down.bat_scoring == pytest.approx(-0.13) and down.bat_dismissal == pytest.approx(1 / rate)
    assert down.bowl_scoring == pytest.approx(0.16) and down.bowl_dismissal == pytest.approx(1 / rate)
    batter = Card(fs_id=1, person_id=C.YOU_ID, name="me", role="batter", bat=0.1,
                  bat_scoring=0.0, bat_dismissal=1.0)
    assert C.approach_card(batter, 2).bowl_scoring is None


# Delhi Capitals 2019, seed 29: found by search because it exercises every moment rule at
# once -- two league matches that would be must-win without MUST_WIN_MAX, playoff matches,
# and a called league match that is the SECOND meeting with its opponent. The first fixture
# these tests used (the first franchise that paused) had no league moment and no repeat
# meeting, so three rules could be broken without any test failing. These seasons are
# pinned to the engine, so an engine change can move them (A208 moved every one of them,
# Kings XI 2019 before this); re-find them by search rather than loosening a guard.
MOMENT_FS = 50
MOMENT_SEED = 29
EDGE = (26, 29)   # Chennai 2019: see test_every_league_stop_and_only_those_is_a_close_race


def _moment_year(deck, model, role="opener", seed=None, fs_only=None):
    """A real season with you in it, played with moments, pausing at the first."""
    seed = MOMENT_SEED if seed is None else seed
    fs_only = MOMENT_FS if fs_only is None else fs_only
    start, ceiling = C.spin(deck, 3, role)
    for fs in [fs_only] if fs_only is not None else C.year_fs_ids(deck, 2019):
        c0 = deck.cards_by_fs[fs][0]
        you = C.ability(start, ceiling, 0.9, setup=_setup(role), fs_id=fs, year=2019,
                        franchise=c0.franchise)
        try:
            C.simulate_year(deck, model, seed, 2, you, answers=())
        except C._NeedMoment as need:
            return you, need
    pytest.skip("no franchise reached a key moment")


def test_a_season_stops_only_where_something_is_at_stake(deck, model):
    """Recomputed independently, at EVERY stop of the season: a must-win has few matches left
    and your side within two points of the qualification line; a playoff carries its stage's
    stakes; a make-or-break (A205, tested on its own) names the drop."""
    you, need = _moment_year(deck, model)
    answers, kinds = (), []
    while True:
        try:
            C.simulate_year(deck, model, MOMENT_SEED, 2, you, answers=answers)
            break
        except C._NeedMoment as need:
            m, season = need.moment, need.season
            kinds.append(m.kind)
            if m.kind == "must_win":
                assert m.stage == "league"
                pts = {s.name: 0 for s in season.sides}
                for r in season.results:
                    if r.winner is None:
                        pts[r.home.name] += 1; pts[r.away.name] += 1
                    else:
                        pts[r.winner.name] += 2
                mine = pts[need.yours.name]
                ranked = sorted(pts.values(), reverse=True)
                assert min(abs(mine - ranked[3]), abs(mine - ranked[4])) <= C.MUST_WIN_EDGE
                played = sum(need.yours is r.home or need.yours is r.away for r in season.results)
                each = 16 if len(season.sides) == 9 else 14
                assert each - played <= C.MUST_WIN_WINDOW
            elif m.kind == "playoff":
                assert m.stage in C.PLAYOFF_STAKES and m.stakes == C.PLAYOFF_STAKES[m.stage]
            else:
                assert m.kind == "make_or_break" and m.stage == "league"
                assert f"next {C._WORDS[C.DROP_MATCHES]} matches" in m.stakes
            assert m.labels == C.APPROACH_LABELS["batter"]
            answers = answers + (1,)
    assert "must_win" in kinds and "playoff" in kinds, kinds


def test_your_call_is_played_in_that_match_and_only_that_one(deck, model):
    """Answer every moment "attack": in each moment match the player the engine used for you
    carries the attacking numbers, and in every other match your usual ones."""
    you, need = _moment_year(deck, model)
    answers = ()
    while True:
        try:
            year = C.simulate_year(deck, model, MOMENT_SEED, 2, you, answers=answers)
            break
        except C._NeedMoment:
            answers = answers + (2,)
    assert year.approaches and len(year.approaches) == len(answers)
    numbers = {n for n, *_ in year.approaches}
    n = 0
    for r in year.results:
        if year.yours is not r.home and year.yours is not r.away:
            continue
        n += 1
        mine = r.home_innings if year.yours is r.home else r.away_innings
        for b in mine.batting:
            if b.player.person_id != C.YOU_ID:
                continue
            want = you.bat_scoring + (C.ATTACK_SHIFT if n in numbers else 0.0)
            assert b.player.bat_scoring == pytest.approx(want), (n, r.stage)


def test_the_feed_tags_the_match_you_called_not_an_earlier_meeting(deck, model):
    """A league meets every side twice. The call belongs to the meeting it was made for:
    the feed's tagged line is the one whose match carried the changed numbers."""
    you, need = _moment_year(deck, model)
    answers = ()
    while True:
        try:
            year = C.simulate_year(deck, model, MOMENT_SEED, 2, you, answers=answers)
            break
        except C._NeedMoment:
            answers = answers + (2,)
    detail = C.season_detail(year)
    mine = [r for r in year.results if year.yours in (r.home, r.away)]
    opp = lambda r: (r.away if year.yours is r.home else r.home).short
    # The fixture must hold a league call on a REPEAT meeting, or this test cannot fail.
    assert any(st == "league" and any(opp(q) == opp(mine[num - 1]) for q in mine[:num - 1])
               for num, st, _, _, _ in year.approaches)
    for line, r in zip(detail.feed, mine):
        inn = r.home_innings if year.yours is r.home else r.away_innings
        shifted = any(b.player.person_id == C.YOU_ID
                      and b.player.bat_scoring == pytest.approx(you.bat_scoring + C.ATTACK_SHIFT)
                      for b in inn.batting)
        assert (line.approach is not None) == shifted, (r.stage, line.opp_short)


def test_every_league_stop_and_only_those_is_a_close_race(deck, model, monkeypatch):
    """With the cap lifted, the league stops at exactly the matches you play with few left and
    your side within MUST_WIN_EDGE points of the line, recomputed from an independent points
    table. Checked over EVERY league match, so a match the rule should have passed over counts
    as much as one it should have stopped at. EDGE, found by search, reaches the window with
    a match outside the edge as well as a close one; a season that is a close race in all of
    its last three would let the edge be deleted unnoticed."""
    you, need = _moment_year(deck, model, fs_only=EDGE[0], seed=EDGE[1])
    monkeypatch.setattr(C, "MUST_WIN_MAX", 99)
    answers = ()
    while True:
        try:
            year = C.simulate_year(deck, model, EDGE[1], 2, you, answers=answers)
            break
        except C._NeedMoment:
            answers = answers + (1,)                       # Balanced: no result changes
    pts = {s.name: 0 for s in year.season.sides}
    mine = [r for r in year.season.results if year.yours in (r.home, r.away)]
    breaks = {a[0] for a in year.approaches if a[4] == "make_or_break"}  # one call a match
    expected, outside_edge, k = set(), 0, 0
    for r in year.season.results:
        if year.yours in (r.home, r.away):
            k += 1
            left = len(mine) - k + 1
            ranked = sorted(pts.values(), reverse=True)
            m = pts[year.yours.name]
            gap = m - ranked[4] if m > ranked[4] else ranked[3] - m if m < ranked[3] else 0
            inn = r.home_innings if year.yours is r.home else r.away_innings
            played = any(b.player.person_id == C.YOU_ID for b in inn.batting)
            if played and left <= C.MUST_WIN_WINDOW and k not in breaks:
                if gap <= C.MUST_WIN_EDGE:
                    expected.add(k)
                else:
                    outside_edge += 1
        if r.winner is None:
            pts[r.home.name] += 1; pts[r.away.name] += 1
        else:
            pts[r.winner.name] += 2
    assert outside_edge, "the fixture must reach the window outside the edge, or the edge never acts"
    assert {a[0] for a in year.approaches if a[4] == "must_win"} == expected


def test_at_most_one_league_moment_and_every_playoff_you_play(deck, model, monkeypatch):
    you, need = _moment_year(deck, model)
    answers = ()
    while True:
        try:
            year = C.simulate_year(deck, model, MOMENT_SEED, 2, you, answers=answers)
            break
        except C._NeedMoment:
            answers = answers + (1,)
    league = [a for a in year.approaches if a[4] == "must_win"]
    assert len(league) <= C.MUST_WIN_MAX
    # Without the cap this season stops more than once in the league, so the cap is binding.
    monkeypatch.setattr(C, "MUST_WIN_MAX", 99)
    answers = ()
    while True:
        try:
            uncapped = C.simulate_year(deck, model, MOMENT_SEED, 2, you, answers=answers)
            break
        except C._NeedMoment:
            answers = answers + (1,)
    assert sum(a[4] == "must_win" for a in uncapped.approaches) > len(league)
    played_po = [r.stage for r in year.season.playoffs
                 if year.yours in (r.home, r.away)
                 and any(b.player.person_id == C.YOU_ID
                         for inn in (r.home_innings, r.away_innings) for b in inn.batting)]
    assert played_po, "the fixture must reach the playoffs or the rule is never asked"
    assert [a[1] for a in year.approaches if a[1] != "league"] == played_po


def test_an_answer_too_many_is_refused(deck, model):
    you, need = _moment_year(deck, model)
    answers = ()
    while True:
        try:
            C.simulate_year(deck, model, MOMENT_SEED, 2, you, answers=answers)
            break
        except C._NeedMoment:
            answers = answers + (1,)
    with pytest.raises(C.CareerError):
        C.simulate_year(deck, model, MOMENT_SEED, 2, you, answers=answers + (1,))


# --- form, make-or-break and drops (A205) ---------------------------------------------

from types import SimpleNamespace as _NS


def _book(specs: dict[str, tuple[str, str | None]]) -> "C.FormBook":
    """A form book over one side (id 1) of stub cards: {person: (role, band)}."""
    return C.FormBook({1: {p: _NS(role=r, band=b) for p, (r, b) in specs.items()}})


def _games(book, person: str, values: list) -> None:
    for v in values:
        book.games[(person, 1)].append(v if isinstance(v, tuple) else (v, 0, 0, 0, True))


def test_form_reads_the_last_three_matches_only():
    book = _book({"a": ("batter", "opener"), "b": ("batter", "opener"), "c": ("batter", "opener")})
    _games(book, "a", [100, 100, 0, 0, 0])            # all five: 200; last three: 0
    _games(book, "b", [15] * 6)                       # last three: 45; all six: 90
    _games(book, "c", [15] * 6)
    assert book.form_rank(("a", 1)) == 0.0
    assert C.form_word(book.form_rank(("a", 1))) == "Out of form"


def test_form_is_ranked_among_your_own_kind():
    book = _book({"a": ("batter", "opener"), "b": ("batter", "opener"), "c": ("batter", "opener"),
                  "x": ("bowler", "tail"), "y": ("bowler", "tail"), "z": ("bowler", "tail")})
    _games(book, "a", [3, 3, 4]); _games(book, "b", [10, 10, 10]); _games(book, "c", [12, 12, 12])
    for p in "xyz":
        _games(book, p, [(0, 1, 30, 24, None)] * 3)   # bowlers: no runs at all
    assert book.form_rank(("a", 1)) == 0.0            # with the bowlers it would sit mid-table


def test_a_batter_is_ranked_within_his_band_when_it_holds_enough():
    specs = {f"m{i}": ("batter", "middle") for i in range(C.MIN_BAND_PEERS + 1)}
    specs.update({f"o{i}": ("batter", "opener") for i in range(4)})
    book = _book(specs)
    for i in range(C.MIN_BAND_PEERS + 1):
        _games(book, f"m{i}", [10 + i] * 3)
    for i in range(4):
        _games(book, f"o{i}", [60] * 3)
    top = f"m{C.MIN_BAND_PEERS}"
    assert book.form_rank((top, 1)) == 1.0             # among openers it would be low
    assert C.form_word(book.form_rank((top, 1))) == "In form"


def test_a_form_score_is_runs_for_a_batter_and_runs_saved_for_a_bowler():
    game = (5, 2, 30, 24, True)
    assert C.FormBook.score("batter", game) == 5
    assert C.FormBook.score("bowler", game) == pytest.approx(2 * C.FORM_WICKET_RUNS - 30
                                                             + C.FORM_PAR_PER_BALL * 24)
    assert C.FormBook.score("allrounder", game) == pytest.approx(5 + 2 * C.FORM_WICKET_RUNS - 30
                                                                 + C.FORM_PAR_PER_BALL * 24)


def test_the_form_book_reads_a_bowler_from_the_other_innings_and_skips_who_was_not_needed():
    """A side's bowling is in the innings the OTHER side batted (A101's trap). A batter who
    did not bat, or a bowler who did not bowl, has no form reading from that match."""
    home, away = _NS(), _NS()
    bat = lambda p, runs, faced, out: _NS(player=_NS(person_id=p), runs=runs, faced_any=faced, out=out)
    bowl = lambda p, w, r, b: _NS(player=_NS(person_id=p), wickets=w, runs=r, balls=b)
    r = _NS(home=home, away=away,
            home_innings=_NS(batting=[bat("h_bat", 30, True, True), bat("h_dnb", 0, False, False),
                                      bat("h_bowl", 0, False, False), bat("h_part", 0, False, False)],
                             bowling=[bowl("a_bowl", 2, 25, 24)]),
            away_innings=_NS(batting=[bat("a_bowl", 4, True, True)],
                             bowling=[bowl("h_bowl", 3, 20, 24), bowl("h_part", 1, 12, 12)]))
    book = C.FormBook({id(home): {"h_bat": _NS(role="batter", band="opener"),
                                  "h_dnb": _NS(role="batter", band="middle"),
                                  "h_bowl": _NS(role="bowler", band="tail"),
                                  "h_part": _NS(role="batter", band="middle")},
                       id(away): {"a_bowl": _NS(role="bowler", band="tail")}})
    book.add([r])
    assert book.games[("h_bat", id(home))] == [(30, 0, 0, 0, True)]
    assert ("h_dnb", id(home)) not in book.games
    assert ("h_part", id(home)) not in book.games     # a batter who only bowled: not his job
    assert book.games[("h_bowl", id(home))] == [(0, 3, 20, 24, None)]
    assert book.games[("a_bowl", id(away))] == [(4, 2, 25, 24, True)]


MOB_FS = {"dropped": 140, "kept": 50}   # 2019: Rajasthan, Delhi (found by search)
MOB_SEED = {"dropped": 31, "kept": 1}
SIXTH = (77, 33)   # Kolkata 2019: see test_a_make_or_break_is_failed_only_in_the_worst_sixth


def _mob_you(deck, fs, role="opener"):
    start, ceiling = C.spin(deck, 3, role)
    return C.ability(start, ceiling, 0.0, setup=_setup(role), fs_id=fs, year=2019,
                     franchise=deck.cards_by_fs[fs][0].franchise)


def _in_xi(year, r) -> bool:
    inn = r.home_innings if year.yours is r.home else r.away_innings
    return any(b.player.person_id == C.YOU_ID for b in inn.batting)


def _your_league(year):
    return [r for r in year.season.results if year.yours in (r.home, r.away)]


def _poor_points(year, late: bool = False) -> list[int]:
    """Recomputed from the results: your league matches (1-based) you played having FORM
    poor as it stood straight after your previous match, with a match still to come.
    `late` reads it instead just before the match, other sides' matches included -- the
    wrong rule, kept so a fixture can show the two differ."""
    book, key = C.FormBook(year.roster), (C.YOU_ID, id(year.yours))
    results = year.season.results
    idx = [i for i, r in enumerate(results) if year.yours in (r.home, r.away)]
    out = []
    for k, i in enumerate(idx, 1):
        if late:
            book.add(results[:i])
        elif k > 1:
            book.add(results[:idx[k - 2] + 1])
        rank = book.form_rank(key)
        if _in_xi(year, results[i]) and rank is not None and rank < C.FORM_POOR and k < len(idx):
            out.append(k)
    return out


@pytest.mark.parametrize("fixture", ["dropped", "kept"])
def test_a_make_or_break_comes_the_first_time_your_form_turns_poor(deck, model, fixture):
    """Form as it stood straight after your own last match. "kept" is where it matters,
    found by search: reading it later, after other sides' matches, moves the first poor
    reading from match 11 to match 10."""
    year = C.simulate_year(deck, model, MOB_SEED[fixture], 2, _mob_you(deck, MOB_FS[fixture]), answers=None)
    poor = _poor_points(year)
    assert poor, "the fixture must reach poor form"
    assert list(year.verdicts) == [poor[0]]
    if fixture == "kept":
        assert _poor_points(year, late=True)[0] != poor[0], "the fixture must tell the readings apart"


def test_one_make_or_break_a_season(deck, model, monkeypatch):
    you = _mob_you(deck, MOB_FS["dropped"])
    assert len(C.simulate_year(deck, model, MOB_SEED["dropped"], 2, you, answers=None).verdicts) == C.MAKE_OR_BREAK_MAX
    monkeypatch.setattr(C, "MAKE_OR_BREAK_MAX", 99)
    assert len(C.simulate_year(deck, model, MOB_SEED["dropped"], 2, you, answers=None).verdicts) > 1   # it binds


def _single_rank(year, k: int) -> float:
    """Your k-th league match against every match of your kind up to and including it."""
    book, key = C.FormBook(year.roster), (C.YOU_ID, id(year.yours))
    results = year.season.results
    i = [j for j, r in enumerate(results) if year.yours in (r.home, r.away)][k - 1]
    book.add(results[:i + 1])
    return book.last_match_rank(key)


def test_failing_a_make_or_break_drops_you_for_the_next_two_matches(deck, model):
    year = C.simulate_year(deck, model, MOB_SEED["dropped"], 2, _mob_you(deck, MOB_FS["dropped"]), answers=None)
    (k, verdict), = year.verdicts.items()
    assert verdict == "dropped" and _single_rank(year, k) < C.FORM_FAIL
    mine = _your_league(year)
    assert _in_xi(year, mine[k - 1])
    assert not any(_in_xi(year, r) for r in mine[k:k + C.DROP_MATCHES])
    assert year.dropped == list(range(k + 1, k + 1 + C.DROP_MATCHES))


def test_coming_through_a_make_or_break_keeps_your_place(deck, model):
    year = C.simulate_year(deck, model, MOB_SEED["kept"], 2, _mob_you(deck, MOB_FS["kept"]), answers=None)
    (k, verdict), = year.verdicts.items()
    assert verdict == "kept" and _single_rank(year, k) >= C.FORM_FAIL
    assert _in_xi(year, _your_league(year)[k]) and year.dropped == []


def test_the_missed_block_leaves_room_for_real_form_drops():
    """The block's shares were measured from real seasons, so they already hold real form
    drops: for a droppable player the block is drawn DROP_MATCHES shorter at their real rate,
    and is otherwise the same block."""
    q, shortened, n = C.FORM_DROP_RATE["bowler"], 0, 4000
    for seed in range(n):
        plain = C.missed_block(seed, 3, 14)
        short = C.missed_block(seed, 3, 14, form_drop=q)
        if len(short) != len(plain):
            assert len(short) == max(0, len(plain) - C.DROP_MATCHES), seed
            shortened += 1
        else:
            assert short == plain, seed        # its own stream: the block is otherwise the same
        assert C.missed_block(seed, 3, 14, form_drop=0.0) == plain
    # a block of nought or one cannot be shortened by two: those seasons dilute the rate
    room = sum(len(C.missed_block(s, 3, 14)) > 0 for s in range(n)) / n
    se = (q * (1 - q) / n) ** 0.5
    assert abs(shortened / n - q * room) < 3 * se + 0.005


def test_a_drop_adds_to_the_season_block(deck, model):
    """Dropped matches are added to the drawn block; nothing is given back."""
    year = C.simulate_year(deck, model, MOB_SEED["dropped"], 2, _mob_you(deck, MOB_FS["dropped"]), answers=None)
    mine = _your_league(year)
    block = C.missed_block(MOB_SEED["dropped"], 2, len(mine), form_drop=C.FORM_DROP_RATE["batter"])
    assert block and year.dropped, "the fixture must hold both a block and a drop"
    assert year.missed == len(block) + len(year.dropped) == len(set(block) | {n - 1 for n in year.dropped})
    assert not any(_in_xi(year, mine[i]) for i in block)


def test_squad_seasons_and_allrounders_never_get_a_make_or_break(deck, model):
    squad = C.simulate_year(deck, model, MOB_SEED["dropped"], 2, _mob_you(deck, MOB_FS["dropped"]), squad=True,
                            answers=None)
    assert _poor_points(squad), "the fixture must reach poor form"
    assert squad.verdicts == {}
    ar = C.simulate_year(deck, model, MOB_SEED["dropped"], 2, _mob_you(deck, MOB_FS["dropped"], "allrounder"),
                         answers=None)
    assert _poor_points(ar), "the fixture must reach poor form"
    assert ar.verdicts == {}
    # nor is an all-rounder's block drawn short for drops he cannot have: a season where the
    # shortening would fire
    q = C.FORM_DROP_RATE["batter"]
    k = next(k for k in range(60) if len(C.missed_block(MOB_SEED["dropped"], k, 14, form_drop=q))
             != len(C.missed_block(MOB_SEED["dropped"], k, 14)))
    ar = C.simulate_year(deck, model, MOB_SEED["dropped"], k, _mob_you(deck, MOB_FS["dropped"], "allrounder"),
                         answers=None)
    assert ar.missed == len(C.missed_block(MOB_SEED["dropped"], k, 14))


def test_the_feed_shows_the_form_the_engine_read_and_the_drop(deck, model):
    you = _mob_you(deck, MOB_FS["dropped"])
    answers = ()
    while True:
        try:
            year = C.simulate_year(deck, model, MOB_SEED["dropped"], 2, you, answers=answers)
            break
        except C._NeedMoment:
            answers = answers + (1,)
    detail = C.season_detail(year)
    (k, _), = year.verdicts.items()
    league = [l for l in detail.feed if l.stage == "league"]
    assert league[k - 2].form_word == "Out of form"            # what the make-or-break read
    assert league[k - 1].stop == "make_or_break" and league[k - 1].verdict == "dropped"
    assert league[k - 1].approach == C.APPROACH_LABELS["batter"][1]
    assert [l.dropped for l in league[k:k + C.DROP_MATCHES]] == [True] * C.DROP_MATCHES
    assert not any(l.dropped for l in league[:k] + league[k + C.DROP_MATCHES:])
    book = C.FormBook(year.roster)
    book.add(year.results)
    assert detail.feed[-1].form == [C.form_figure("batter", g) for g in
                               book.games[(C.YOU_ID, id(year.yours))][-C.FORM_SHOWN:]]


def test_a_batter_bowls_only_when_his_side_lacks_five_real_bowlers(deck, model):
    """A batter's bowling is part-time (A26), so it is marked thin as a real part-timer's
    is (A203, A205): he never bowls ahead of five bowlers with real volume. The seasons are
    ones where, unmarked, he would have been ranked into the five."""
    from game.__main__ import BOWLERS_IN_TWELVE, bowling_order

    acted = 0
    for seed in range(1, 40):
        start, ceiling = C.spin(deck, seed, "middle")
        if start.bowl is None:
            continue
        fs = C.year_fs_ids(deck, 2016)[seed % 8]
        you = C.ability(start, ceiling, 0.5, setup=_setup("middle"), fs_id=fs, year=2016,
                        franchise=deck.cards_by_fs[fs][0].franchise)
        assert you.bowl_balls == 0                   # marked part-time
        year = C.simulate_year(deck, model, seed, 2, you, answers=None)
        real = [c for c in year.yours.xi if c.person_id != C.YOU_ID and c.has_bowl
                and not bowling_order(c)[0]]
        if len(real) < BOWLERS_IN_TWELVE:
            continue
        unmarked = dataclasses.replace(you, bowl_balls=None)
        fifth = sorted(real, key=bowling_order)[BOWLERS_IN_TWELVE - 1]
        if bowling_order(unmarked) < bowling_order(fifth):
            acted += 1                               # the old order would have bowled him
        for r in year.results:
            if year.yours not in (r.home, r.away):
                continue
            theirs = r.away_innings if year.yours is r.home else r.home_innings
            assert not any(w.player.person_id == C.YOU_ID and w.balls for w in theirs.bowling)
        if acted >= 2:
            break
    assert acted >= 2, "the rule must have had to act"


def test_a_make_or_break_is_failed_only_in_the_worst_sixth(deck, model):
    """A207: the fail bar is the worst sixth of your kind's matches, not the worst third
    that marks you out of form. SIXTH, found by search, has its make-or-break match between
    the two bars (ranked 0.299): kept now, dropped under the old bar."""
    year = C.simulate_year(deck, model, SIXTH[1], 2, _mob_you(deck, SIXTH[0]), answers=None)
    (k, verdict), = year.verdicts.items()
    rank = _single_rank(year, k)
    assert C.FORM_FAIL <= rank < C.FORM_POOR, "the fixture must sit between the two bars"
    assert verdict == "kept" and year.dropped == []
    assert _in_xi(year, _your_league(year)[k])
