"""[A293] Phase 1 of making Build a Career harder: a career bowler stops at a real bowler's
best. Two rules, each measured against the archive -- a plan gives at most two death overs
an innings, and your wicket odds stop at WICKET_CEILING in every phase."""

from __future__ import annotations

import dataclasses
import math

import pytest

from game import career as C
from game import career_traits as ct
from game import simulator as S
from game.simulator import BowlerCard, Player


def test_the_declared_numbers_are_the_measured_ones():
    # stated, not read from the code (A207): two death overs, the real p99 multiplier
    assert S.PLAN_DEATH_OVERS == 2
    assert C.WICKET_CEILING == 1.51
    assert S.DEATH_FROM == 15 and S.phase_of_over(15) == "death" and S.phase_of_over(14) == "middle"


def _card(dismissal, lean=None):
    import tools.snapshot_deck as sd
    deck = sd.deck_from(sd.read_document())
    base = next(c for cs in deck.cards_by_fs.values() for c in cs if c.bowl_dismissal)
    return dataclasses.replace(base, bowl_dismissal=dismissal, phase_wkt=lean)


def test_odds_below_the_ceiling_are_untouched():
    c = _card(1.2, (-0.05, 0.0, 0.1))
    assert C.within_reach(c, 1.51) is c


def test_odds_above_the_ceiling_stop_at_it_in_every_phase():
    c = _card(1.7, (-0.1, 0.0, 0.2))
    out = C.within_reach(c, 1.51)
    assert out.bowl_dismissal == pytest.approx(1.51)
    for x in out.phase_wkt:                          # each phase's odds, multiplier x lean
        assert out.bowl_dismissal * math.exp(x) <= 1.51 + 1e-6
    assert out.phase_wkt[0] == pytest.approx(-0.1)   # a phase below the ceiling keeps its lean


def test_a_lean_alone_can_reach_the_ceiling():
    c = _card(1.4, (-0.1, 0.0, 0.2))                 # death: 1.4 x e^0.2 = 1.71
    out = C.within_reach(c, 1.51)
    assert out.bowl_dismissal == pytest.approx(1.4)
    assert 1.4 * math.exp(out.phase_wkt[2]) == pytest.approx(1.51, abs=1e-5)
    assert out.phase_wkt[:2] == (-0.1, 0.0)


def test_the_cap_is_your_own_ability_before_your_temper():
    base = _card(1.2)
    for temper in (-3.0, 0.0, 3.0):
        styled = ct.styled(base, {"temper": temper})
        assert C.wicket_cap(styled, {"temper": temper}) == pytest.approx(1.51)
    great = _card(1.6)                               # real seasons beyond the ceiling stay
    styled = ct.styled(great, {"temper": 3.0})
    assert C.wicket_cap(styled, {"temper": 3.0}) == pytest.approx(1.6)


def test_play_innings_counts_death_overs():
    from tools.snapshot_deck import model_from, read_document
    model = model_from(read_document())
    import random
    attack = [Player(f"b{i}", 0.0, 0.1 * i, person_id=f"p{i}") for i in range(5)]
    bats = [Player(f"x{i}", 0.0, None, person_id=f"x{i}") for i in range(11)]
    seen = []
    real = S.choose_bowler

    def spy(bowlers, previous, over=None, plan=None, rng=None):
        seen.append((over, {b.player.person_id: b.death_overs for b in bowlers}))
        return real(bowlers, previous, over, plan, rng)
    S.choose_bowler = spy
    try:
        S.play_innings(model, bats, attack, random.Random(1), plan={"p4": "death"})
    finally:
        S.choose_bowler = real
    last = dict(seen[-1][1])
    assert sum(last.values()) == len([o for o, _ in seen if o is not None and o >= 15]) - 1
    assert last.get("p4", 0) <= 2


def test_a_season_plays_every_match_of_yours_at_or_under_the_ceiling():
    """The wiring: an absurd multiplier (3.0, attacking temper on top) reaches the engine
    held to the ceiling in every match you play, and plays unheld with the rule off."""
    from tools.snapshot_deck import deck_from, model_from, read_document
    doc = read_document()
    deck, model = deck_from(doc), model_from(doc)
    start, ceiling = C.spin(deck, 3, "pace")
    fs = C.year_fs_ids(deck, 2014)[2]
    c0 = deck.cards_by_fs[fs][0]
    you = C.ability(start, ceiling, 0.6, setup=C.Setup("T", "pace", 2012), fs_id=fs,
                    year=2014, franchise=c0.franchise)
    you = dataclasses.replace(you, bowl_dismissal=3.0)
    traits = {"temper": 2.0}
    you = ct.styled(you, traits)
    played = []
    real = S.play_innings

    def spy(model, batting, bowling, *a, **kw):
        for p in bowling:
            if p.person_id == C.YOU_ID:
                played.append(p.bowl_dismissal)
        return real(model, batting, bowling, *a, **kw)

    import game.season as GS
    orig = GS.play_innings
    GS.play_innings = spy
    try:
        C.simulate_year(deck, model, 3, 2, you, traits=traits)
        held = list(played)
        played.clear()
        C.WICKET_CEILING_ON = False
        C.simulate_year(deck, model, 3, 2, you, traits=traits)
    finally:
        GS.play_innings = orig
        C.WICKET_CEILING_ON = True
    assert held and max(held) == pytest.approx(3.0)            # your own ability is the cap
    assert played and max(played) > 3.0 + 1e-9                 # temper unheld without it


def test_a_card_beyond_its_own_ability_is_held(monkeypatch):
    """Temper pushes a 1.3 bowler past 1.51; in a real season he plays at 1.51."""
    from tools.snapshot_deck import deck_from, model_from, read_document
    doc = read_document()
    deck, model = deck_from(doc), model_from(doc)
    start, ceiling = C.spin(deck, 3, "pace")
    fs = C.year_fs_ids(deck, 2014)[2]
    c0 = deck.cards_by_fs[fs][0]
    you = C.ability(start, ceiling, 0.6, setup=C.Setup("T", "pace", 2012), fs_id=fs,
                    year=2014, franchise=c0.franchise)
    traits = {"temper": 3.0}
    you = ct.styled(dataclasses.replace(you, bowl_dismissal=1.3), traits)
    assert you.bowl_dismissal > 1.51
    played = []
    import game.season as GS
    real = GS.play_innings

    def spy(model, batting, bowling, *a, **kw):
        played.extend(p.bowl_dismissal for p in bowling if p.person_id == C.YOU_ID)
        return real(model, batting, bowling, *a, **kw)
    monkeypatch.setattr(GS, "play_innings", spy)
    C.simulate_year(deck, model, 3, 2, you, traits=traits)
    assert played and all(d == pytest.approx(1.51) for d in played)


def test_a_death_bowler_with_overs_in_hand_stops_at_two_death_overs():
    """The cap on its own: he reaches over 18 having bowled two death overs and one before,
    so he still has an over in hand -- and is unplanned for it, so a fresher bowler bowls.
    (Without the cap the saving rule usually makes it redundant: he arrives with two left.)"""
    attack = [BowlerCard(Player(f"b{i}", 0.0, 0.1 * i, person_id=f"p{i}")) for i in range(5)]
    p4 = attack[4]
    p4.balls, p4.death_overs = 18, 2
    for b in attack[:4]:
        b.balls = 12                # fresher, and worse: unplanned, he would still win a tie
    chosen = S.choose_bowler(attack, attack[0], 17, {"p4": "death"})
    assert chosen is not p4
    p4.death_overs = 1                                   # one death over: still his
    assert S.choose_bowler(attack, attack[0], 17, {"p4": "death"}) is p4


PLAYOFF_SEED = 1     # (3 until A323) a 2014 season in which your side reaches the playoffs (asserted below)


def test_the_ceiling_holds_in_the_playoffs_too(monkeypatch):
    """The playoffs set up each match with their own hook, so the hold is wired in twice; this
    checks the second, which the league-stage test cannot see."""
    from tools.snapshot_deck import deck_from, model_from, read_document
    doc = read_document()
    deck, model = deck_from(doc), model_from(doc)
    start, ceiling = C.spin(deck, 3, "pace")
    fs = C.year_fs_ids(deck, 2014)[2]
    c0 = deck.cards_by_fs[fs][0]
    you = C.ability(start, ceiling, 0.6, setup=C.Setup("T", "pace", 2012), fs_id=fs,
                    year=2014, franchise=c0.franchise)
    traits = {"temper": 3.0}
    you = ct.styled(dataclasses.replace(you, bowl_dismissal=1.3), traits)
    import game.season as GS
    stage = {"now": "league"}
    played = {"league": [], "playoffs": []}
    real_innings, real_playoffs = GS.play_innings, GS.run_playoffs

    def spy(model, batting, bowling, *a, **kw):
        played[stage["now"]].extend(p.bowl_dismissal for p in bowling if p.person_id == C.YOU_ID)
        return real_innings(model, batting, bowling, *a, **kw)

    def playoffs(*a, **kw):
        stage["now"] = "playoffs"
        return real_playoffs(*a, **kw)
    monkeypatch.setattr(GS, "play_innings", spy)
    monkeypatch.setattr(GS, "run_playoffs", playoffs)
    monkeypatch.setattr(C, "run_playoffs", playoffs, raising=False)
    C.simulate_year(deck, model, PLAYOFF_SEED, 2, you, traits=traits)
    assert played["playoffs"], "the fixture's side must reach the playoffs"
    assert all(d == pytest.approx(1.51) for d in played["playoffs"])
