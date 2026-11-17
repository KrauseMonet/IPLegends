"""[A294] Phase 2 of making Build a Career harder: your place is earned, and a career can end.

Four rules. The club picks its eleven by its own rule, so a player it would leave out has a
squad season, and a bowler outside its five bowls as the change bowler; a star is kept only
in his prime; clubs pay less for a player past his peak; a fast bowler past his peak is
injured oftener. The numbers are stated here, not read from the code (A207)."""

from __future__ import annotations

import dataclasses
import random

import pytest

from game import career as C
from game import career_incidents as incidents

HARDER_TESTS = True
CYCLE_TESTS = True                       # the production flow (A288)


@pytest.fixture(scope="module")
def deck_model():
    import tools.snapshot_deck as sd

    doc = sd.read_document()
    return sd.deck_from(doc), sd.model_from(doc)


def test_the_declared_numbers():
    assert C.DECLINE_VALUE == 0.8
    assert C.DECLINE_VALUE_FLOOR == 0.25
    assert C.LATE_PACE_RISK == 1.6


# --- clubs pay less past the peak -------------------------------------------------------

def test_no_discount_while_growing_or_at_the_peak():
    std = C.Shape()                               # rise 5: at the peak through clock 6
    for clock in (0, 3, 5, 6):
        assert C.decline_value(std, clock) == 1.0


def test_a_fifth_off_for_each_season_of_decline_down_to_a_floor():
    std = C.Shape()
    assert C.decline_value(std, 7) == pytest.approx(0.8)
    assert C.decline_value(std, 8) == pytest.approx(0.64)
    assert C.decline_value(std, 9.5) == pytest.approx(0.8 ** 3.5)
    assert C.decline_value(std, 30) == pytest.approx(0.25)
    early = C.Shape(peak="early")                 # rise 3: past the peak from clock 4
    assert C.decline_value(early, 5) == pytest.approx(0.8)


def test_the_age_discount_scales_what_every_club_bids(deck_model):
    deck, _ = deck_model
    full = C.mega_ceilings(deck, 5, 3, 2016, None, 80, capped=True)
    half = C.mega_ceilings(deck, 5, 3, 2016, None, 80, capped=True, age=0.5)
    assert full.keys() == half.keys()
    for fs in full:
        assert abs(half[fs] - full[fs] / 2) <= 1


def test_an_old_player_can_go_unsold_where_a_young_one_sells(deck_model):
    deck, _ = deck_model
    both = 0
    for seed in range(40):
        young = C.mini_auction(deck, seed, 4, 2016, "Chennai Super Kings", 72, 50, capped=True)
        old = C.mini_auction(deck, seed, 4, 2016, "Chennai Super Kings", 72, 50, capped=True,
                             age=0.3)
        if not young.sold:
            assert not old.sold                   # paying less never sells a player who was not
        if young.sold and not old.sold:
            both += 1
            assert old.fs_id < 0                  # released and unsold: the career is over
    assert both > 0


def test_a_career_passes_its_decline_to_every_auction(deck_model, monkeypatch):
    deck, model = deck_model
    seen = []
    real_mega, real_mini = C.mega_auction, C.mini_auction

    def mega(*a, **kw):
        seen.append(("mega", a[3], kw.get("age", 1.0)))                 # year, age
        return real_mega(*a, **kw)

    def mini(*a, **kw):
        seen.append(("mini", a[3], kw.get("age", 1.0)))
        return real_mini(*a, **kw)
    monkeypatch.setattr(C, "mega_auction", mega)
    monkeypatch.setattr(C, "mini_auction", mini)
    checked = 0
    for seed in range(12):
        seen.clear()
        setup = C.Setup("Test", "pace", 2009)
        career, _ = C.auto_career(deck, model, seed, setup, "random")
        shape = C.draw_shape(seed)
        clocks = {r.year: r.clock for r in career.seasons}
        for _kind, year, age in seen:
            if year in clocks:
                assert age == pytest.approx(C.decline_value(shape, clocks[year]))
                checked += age < 1.0
        if checked:
            break
    assert checked, "no career reached an auction past its peak"


# --- a star is kept only in his prime ---------------------------------------------------

@pytest.mark.parametrize("released,rating,prime,standing,out", [
    (True, 88, True, 0, (False, "star")),        # a star in his prime is kept
    (True, 88, False, 0, (True, None)),          # ...past it, two poor seasons cost him
    (True, 80, True, 0, (True, None)),           # not a star: released
    (False, 80, True, -2, (True, "cold")),       # a club that has gone cold lets you go
    (False, 88, True, -2, (False, None)),        # ...but not a star in his prime
    (False, 88, False, -2, (True, "cold")),      # ...and a declining star, yes
    (False, 80, True, -1, (False, None)),
])
def test_release_decision(released, rating, prime, standing, out):
    assert C.release_decision(released, rating, prime, standing) == out


def test_the_star_rule_reads_the_career_s_own_clock(deck_model, monkeypatch):
    deck, model = deck_model
    calls = []
    real = C.release_decision

    def spy(released, rating, prime, standing):
        calls.append(prime)
        return real(released, rating, prime, standing)
    monkeypatch.setattr(C, "release_decision", spy)
    seed = 3
    setup = C.Setup("Test", "opener", 2010)
    _, moves = C.auto_career(deck, model, seed, setup, "loyal")
    calls.clear()                                 # a career replays on every move: read one
    career = C.play_career(deck, model, seed, setup, moves)
    shape = C.draw_shape(seed)
    assert calls == [not shape.declining(r.clock) for r in career.seasons]
    assert True in calls and False in calls


# --- earning your place -----------------------------------------------------------------

def _you(card, **kw):
    return dataclasses.replace(card, person_id=C.YOU_ID, name="You", **kw)


def _squad(deck, franchise="Mumbai", year=2013):
    fs = next(fs for fs, cs in deck.cards_by_fs.items() if cs[0].franchise.startswith(franchise)
              and cs[0].season_year == year)
    return list(deck.cards_by_fs[fs])


def _batter(squad, overseas=False):
    """A career batter at this club, built from one of its own batters."""
    import game.__main__ as M

    xi = M.opposition_xi(squad)
    base = next(c for c in xi if c.role == "batter")
    return _you(base, overseas=overseas, bat_balls=None)


def _legal_outs(you, squad):
    """Independently of `merit_rival`: every player in the club's own eleven you could
    replace and leave it with a keeper, five bowlers and at most four overseas."""
    import game.__main__ as M

    xi = M.opposition_xi(squad)
    out = []
    for c in xi:
        new = [x for x in xi if x is not c] + [you]
        keepers = sum(x.role == "keeper" for x in new)
        bowlers = sum(x.has_bowl for x in new)
        overseas = sum(x.overseas is True for x in new)
        if keepers >= 1 and bowlers >= 5 and overseas <= 4:
            out.append(c)
    return out


def test_the_rival_is_the_lowest_rated_legal_swap(deck_model):
    deck, _ = deck_model
    for franchise, year in (("Mumbai", 2013), ("Chennai", 2018), ("Kolkata", 2024)):
        squad = _squad(deck, franchise, year)
        you = _batter(squad)
        want = min(_legal_outs(you, squad), key=lambda c: c.display)
        assert C.merit_rival(you, squad).display == want.display


def test_an_overseas_player_competes_with_the_overseas_players(deck_model):
    import game.__main__ as M

    deck, _ = deck_model
    for fs, cs in sorted(deck.cards_by_fs.items()):
        squad = list(cs)
        xi = M.opposition_xi(squad)
        if sum(c.overseas is True for c in xi) == 4:
            break
    you = _batter(squad, overseas=True)
    assert C.merit_rival(you, squad).overseas is True
    home = _batter(squad, overseas=False)
    assert C.merit_rival(home, squad).display <= C.merit_rival(you, squad).display


def test_the_club_picks_on_merit(deck_model):
    deck, _ = deck_model
    squad = _squad(deck)
    you = _batter(squad)
    bar = C.merit_rival(you, squad).display
    assert C.picked_on_merit(you, squad, bar)               # level: you are in
    assert not C.picked_on_merit(you, squad, bar - 1)
    assert C.left_out_on_merit(you, squad, False, bar - 1)
    assert not C.left_out_on_merit(you, squad, False, bar)


def test_a_captain_picks_himself(deck_model):
    deck, _ = deck_model
    squad = _squad(deck)
    you = _batter(squad)
    bar = C.merit_rival(you, squad).display
    assert not C.left_out_on_merit(you, squad, True, bar - 1)


def test_nobody_is_left_out_with_the_rule_off(deck_model, monkeypatch):
    deck, _ = deck_model
    squad = _squad(deck)
    you = _batter(squad)
    monkeypatch.setattr(C, "EARN_ON", False)
    assert not C.left_out_on_merit(you, squad, False, 60)


def _sixth_bowler(deck):
    """A real squad with five real bowlers, and a career bowler who ranks just behind them."""
    import game.__main__ as M

    for fs, cs in sorted(deck.cards_by_fs.items()):
        squad = list(cs)
        bowlers = sorted((c for c in squad if c.has_bowl and c.bowl_balls
                          and c.bowl_balls >= 150), key=M.bowling_order)
        if len(bowlers) < 6:
            continue
        fifth = bowlers[4]
        you = _you(fifth, bowl=fifth.bowl - 0.02, bowl_balls=None, bat=-0.6,
                   role="bowler", positions=frozenset({9, 10, 11}))
        earned, _ = C.xi_with(you, squad, attack=False)
        if _attack_rank(earned) == 6:
            return fs, squad, you
    raise AssertionError("no squad found")


def _attack_rank(xi, you_id=C.YOU_ID):
    import game.__main__ as M

    attack = sorted((c for c in xi if c.has_bowl), key=M.bowling_order)
    return next(i for i, c in enumerate(attack, 1) if c.person_id == you_id)


def test_a_sixth_bowler_bowls_as_the_change_bowler(deck_model):
    deck, _ = deck_model
    _fs, squad, you = _sixth_bowler(deck)
    forced, _ = C.xi_with(you, squad, attack=True)
    earned, _ = C.xi_with(you, squad, attack=False)
    assert _attack_rank(forced) <= 5
    assert _attack_rank(earned) == 6


def test_the_season_ranks_only_a_captain_into_the_attack(deck_model, monkeypatch):
    deck, model = deck_model
    _fs, squad, you = _sixth_bowler(deck)
    seen = []
    real = C.year_sides

    def spy(deck_, year, you_=None, number=None, attack=True):
        seen.append(attack)
        return real(deck_, year, you_, number, attack)
    monkeypatch.setattr(C, "year_sides", spy)
    for captain in (False, True):
        C.simulate_year(deck, model, 1, 2, you, captain=captain)
    assert seen == [False, True]
    monkeypatch.setattr(C, "EARN_ON", False)
    C.simulate_year(deck, model, 1, 2, you, captain=False)
    assert seen[-1] is True


def test_offers_report_the_attack_the_season_plays(deck_model):
    deck, _ = deck_model
    fs, squad, you = _sixth_bowler(deck)
    _pos, rank = C.where_you_play(deck, you, fs)
    assert rank == 6


def _season_of(deck, model, seed, setup, upto, monkeypatch, out_in=None):
    """Play a career until `upto` seasons are done, the club leaving you out in the year
    `out_in` and picking you in every other. Keyed on the year, not on a count of calls: a
    career replays from scratch on every move."""
    monkeypatch.setattr(C, "picked_on_merit",
                        lambda you, squad, rating: you.season_year != out_in)
    C._SEASON_CACHE.clear()
    rng = random.Random(f"{seed}:policy")
    moves = []
    while True:
        career = C.play_career(deck, model, seed, setup, moves)
        if career.complete or len(career.seasons) >= upto:
            return career
        moves.append(C.policy_move(career, "loyal", rng))


def test_a_player_left_out_has_a_squad_season(deck_model, monkeypatch):
    deck, model = deck_model
    setup = C.Setup("Test", "opener", 2010)
    career = _season_of(deck, model, 4, setup, 4, monkeypatch, 2012)
    r = career.seasons[2]
    assert r.left_out and r.squad
    assert any("not in it" in n for n in r.notes)
    assert not career.seasons[1].left_out and not career.seasons[3].left_out


def test_being_left_out_is_not_itself_a_poor_season(deck_model, monkeypatch):
    deck, model = deck_model
    found = 0
    for seed in range(8):
        setup = C.Setup("Test", "opener", 2010)
        career = _season_of(deck, model, seed, setup, 4, monkeypatch, 2012)
        if len(career.seasons) < 4:
            continue
        r, nxt = career.seasons[2], career.seasons[3]
        assert r.left_out
        if r.percentile >= C.poor_bar("opener"):
            found += 1
            assert not nxt.squad              # a good season out of the side keeps your place
    assert found


def test_a_left_out_season_is_played_as_one(deck_model, monkeypatch):
    deck, model = deck_model
    setup = C.Setup("Test", "opener", 2010)
    full = _season_of(deck, model, 4, setup, 3, monkeypatch, None)
    out = _season_of(deck, model, 4, setup, 3, monkeypatch, 2012)
    assert out.seasons[2].matches < full.seasons[2].matches


# --- a fast bowler past his peak --------------------------------------------------------

def test_a_higher_risk_only_ever_adds_an_injury():
    more = 0
    for seed in range(400):
        base = incidents.candidate(seed, 3, "pace", 14)
        late = incidents.candidate(seed, 3, "pace", 14, 1.6)
        injured = {i.key for i in incidents.INCIDENTS.values() if i.injury}
        if base[0] in injured:
            assert late == base                  # same draw, same injury, same match
        more += late[0] in injured and base[0] not in injured
    assert more > 0


def test_late_marks_a_fast_bowler_past_his_peak_only(deck_model):
    deck, model = deck_model
    for role, style in (("pace", "pace"), ("spin", "spin")):
        seed = 2
        career, _ = C.auto_career(deck, model, seed, C.Setup("Test", role, 2009), "loyal")
        shape = C.draw_shape(seed)
        late = [r.late for r in career.seasons]
        want = [style == "pace" and shape.declining(r.clock) for r in career.seasons]
        assert late == want
        if role == "pace":
            assert any(late)


def test_a_late_season_raises_the_injury_risk(deck_model, monkeypatch):
    deck, model = deck_model
    seen = []
    real = incidents.candidate

    def spy(seed, k, kind, matches, risk_scale=1.0):
        seen.append(risk_scale)
        return real(seed, k, kind, matches, risk_scale)
    monkeypatch.setattr(C.incidents, "candidate", spy)
    _fs, _squad, you = _sixth_bowler(deck)
    you = dataclasses.replace(you, bowling_style="pace")
    for late in (True, False):
        try:
            C.simulate_year(deck, model, 1, 2, you, answers=(), late=late)
        except C._NeedMoment:
            pass                                 # paused at a key moment: the draw came first
    assert seen == [1.6, 1.0]


def test_the_season_plays_the_record_s_late_flag(deck_model, monkeypatch):
    deck, model = deck_model
    seen = {}
    real = C.simulate_year

    def spy(deck_, model_, seed, k, you, *a, **kw):
        seen[you.season_year] = kw.get("late", False)
        return real(deck_, model_, seed, k, you, *a, **kw)
    monkeypatch.setattr(C, "simulate_year", spy)
    C._SEASON_CACHE.clear()
    seed = 2
    career, _ = C.auto_career(deck, model, seed, C.Setup("Test", "pace", 2009), "loyal")
    late = {r.year: r.late for r in career.seasons}
    assert any(late.values())
    assert {y: seen[y] for y in late} == late


def test_a_pace_bowling_all_rounder_is_not_marked_late(deck_model):
    """The late risk is a fast bowler's: an all-rounder who bowls seam keeps his own."""
    deck, model = deck_model
    for seed in range(20):
        start, _ceiling = C.spin(deck, seed, "allrounder")
        if start.bowling_style == "pace":
            break
    else:
        raise AssertionError("no pace-bowling all-rounder found")
    career, _ = C.auto_career(deck, model, seed, C.Setup("Test", "allrounder", 2009), "loyal")
    shape = C.draw_shape(seed)
    assert any(shape.declining(r.clock) for r in career.seasons)
    assert not any(r.late for r in career.seasons)


# --- unsold past the peak ends it -------------------------------------------------------

def test_unsold_ends_a_career_only_past_the_peak():
    std = C.Shape()                                # declining from clock 6 on
    assert C.unsold_ends(False, std, 6.5)
    assert not C.unsold_ends(False, std, 5.0)      # before the peak a club signs you late
    assert not C.unsold_ends(True, std, 9.0)       # sold: carry on
    early = C.Shape(peak="early")                  # declining from clock 4
    assert C.unsold_ends(False, early, 4.5)


def test_unsold_never_ends_a_career_with_the_decline_rules_off(monkeypatch):
    monkeypatch.setattr(C, "DECLINE_ON", False)
    assert not C.unsold_ends(False, C.Shape(), 12.0)


def test_unsold_at_a_mega_auction_past_the_peak_ends_the_career(deck_model, monkeypatch):
    """Every mega auction goes unsold and nobody retains you: the career runs, signed late
    as a squad player, until the first mega auction past the peak, and ends there."""
    deck, model = deck_model
    real_offer = C.mega_offer

    def no_retain(*a, **kw):
        return dataclasses.replace(real_offer(*a, **kw), retain=None)

    years = []

    def unsold(deck_, seed, k, year, current, last, base, *, capped, age=1.0):
        years.append(year)
        fs = C.year_fs_ids(deck_, year)[0]
        c = deck_.cards_by_fs[fs][0]
        return C.MegaResult(base, False, False, fs, c.franchise, "XX", base)
    calls = []
    real_ends = C.unsold_ends

    def spy(sold, shape, clock):
        out = real_ends(sold, shape, clock)
        calls.append(out)
        return out
    monkeypatch.setattr(C, "mega_offer", no_retain)
    monkeypatch.setattr(C, "mega_auction", unsold)
    monkeypatch.setattr(C, "unsold_ends", spy)
    C._SEASON_CACHE.clear()
    seed, setup = 3, C.Setup("Test", "opener", 2009)
    _, moves = C.auto_career(deck, model, seed, setup, "loyal")
    calls.clear()                                  # read one replay of the finished career
    years.clear()
    career = C.play_career(deck, model, seed, setup, moves)
    played = {r.year: r for r in career.seasons}
    assert career.released
    assert calls and calls[-1] is True and not any(calls[:-1])
    ended = years[-1]                              # the mega year that ended it
    assert ended not in played and ended - 1 in played
    assert len(years) >= 2                         # the fixture must have one before the peak
    for y in years[:-1]:
        assert played[y].squad                     # before the peak: signed late, a squad player


def test_unsold_at_a_mini_auction_past_the_peak_ends_the_career(deck_model, monkeypatch):
    """Released every season, and every mini auction leaves you unsold but signed late (as
    one does for a club that folded): before the peak you play on, past it the career ends.
    Mega auctions always sell, so only the mini auction's check can end it."""
    deck, model = deck_model

    def late(deck_, seed, k, year, gone, last, base, *, capped, age=1.0):
        fs = C.year_fs_ids(deck_, year)[0]
        c = deck_.cards_by_fs[fs][0]
        return C.MegaResult(base, False, False, fs, c.franchise, "XX", base, kind="mini")

    def sold(deck_, seed, k, year, current, last, base, *, capped, age=1.0):
        fs = C.year_fs_ids(deck_, year)[0]
        c = deck_.cards_by_fs[fs][0]
        return C.MegaResult(base, True, False, fs, c.franchise, "XX", base)
    monkeypatch.setattr(C, "mini_auction", late)
    monkeypatch.setattr(C, "mega_auction", sold)
    monkeypatch.setattr(C, "release_decision", lambda released, rating, prime, standing: (True, None))
    calls = []
    real_ends = C.unsold_ends

    def spy(sold_, shape, clock):
        out = real_ends(sold_, shape, clock)
        calls.append(out)
        return out
    monkeypatch.setattr(C, "unsold_ends", spy)
    C._SEASON_CACHE.clear()
    seed, setup = 3, C.Setup("Test", "opener", 2009)
    _, moves = C.auto_career(deck, model, seed, setup, "loyal")
    calls.clear()                                  # read one replay of the finished career
    career = C.play_career(deck, model, seed, setup, moves)
    assert career.released and career.seasons[-1].year < C.LAST_YEAR
    assert calls[-1] is True and not any(calls[:-1])
    assert len(calls) >= 3                         # it ran on through mini auctions first
