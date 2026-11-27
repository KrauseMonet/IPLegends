"""[A323] The first-innings Impact rescue, and the finisher band widened to 5-8.

The rescue has two halves and both are tested apart. `simulator._rescues` decides WHEN, on
a scripted innings where every number can be worked out by hand; `season.rescue_for`
decides WHETHER a side may, on hand-built cards; and the three ways a match is played are
checked to hand the second innings to the eleven the rescue left, with no second Impact
decision behind it.
"""

from __future__ import annotations

import datetime
import random

import pytest

from etl.feasibility import BATTING_ROLE_SLOTS, Card, Deck
import game.season as season
import game.simulator as sim
from game.season import Side, after_rescue, no_rescue, rescue_for
from game.simulator import (BALLS_PER_OVER, Innings, Player, Rescue, RescueAt,
                            play_innings)

_VALUES = (-1.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
W, DOT, ONE = 0, 1, 2


class _Costs:
    def __init__(self, remaining: float):
        self.remaining = [[remaining] * 11 for _ in range(121)]


class _Script:
    """One fixed outcome per legal ball, then dots. `costs` makes the projected total
    exactly runs so far + `remaining` (extras are off), so every trigger is by hand."""

    wide_rate, wide_runs, extras_rate = 0.0, 1.0, 0.0

    def __init__(self, outcomes, remaining=100.0):
        self._left = list(outcomes)
        self.costs = _Costs(remaining)

    def state(self, over, wickets):
        k = self._left.pop(0) if self._left else DOT
        return tuple(1.0 if i == k else 0.0 for i in range(8)), _VALUES


BAT = [Player(f"p{i}", 0.0, person_id=f"id{i}") for i in range(11)]
BOWL = [Player(f"w{i}", 0.0, bowl=0.0, person_id=f"wid{i}") for i in range(5)]
IMPACT = Player("imp", 0.0, person_id="imp", is_impact=True)
ALL_BATTERS = frozenset(f"id{i}" for i in range(11))


def _rescue(swappable=ALL_BATTERS, outbats=ALL_BATTERS):
    return Rescue(IMPACT, frozenset(swappable), frozenset(outbats))


def _play(script, rescue, remaining=100.0):
    return play_innings(_Script(script, remaining), list(BAT), BOWL, random.Random(0),
                        rescue=rescue)


def test_the_constants_are_the_ones_declared():
    """Stated, not read from the code (A207): a moved constant must fail here."""
    assert sim.RESCUE_BELOW == 135
    assert sim.RESCUE_MIN_WICKETS == 2
    assert sim.RESCUE_LAST_OVER == 17


def test_he_comes_in_next_for_a_man_already_out():
    """Two down for nothing, projected 0 + 100: under 135, so at the second wicket (id0,
    then id2 who came in for him) the Impact Player bats next, at four, ahead of id3, and
    the first man out is the one who goes off."""
    inn = _play([W, W], _rescue())
    assert inn.rescue == RescueAt("id0", "p0", "imp", "imp", 0, 2, 0)
    order = [c.player.person_id for c in inn.batting]
    assert order[:5] == ["id0", "id1", "id2", "imp", "id3"]
    assert inn.batting[3].faced_any                   # he batted
    assert len(inn.batting) == 12                     # the man off is still on the card


def test_not_before_the_second_wicket():
    """One down and projected under the line: too early."""
    inn = _play([W], _rescue())
    assert inn.rescue is None


def test_not_when_the_projection_is_at_the_line():
    """35 + 100 = 135, which is not BELOW 135."""
    script = [6 + 1] * 5 + [ONE] * 5 + [W, W]        # five sixes, five singles = 35
    inn = _play(script, _rescue())
    assert inn.runs == 35 and inn.wickets == 2 and inn.rescue is None
    inn = _play([7] * 5 + [ONE] * 4 + [W, W], _rescue())   # 34 + 100: under
    assert inn.rescue is not None and inn.rescue.runs == 34


def test_not_after_the_eighteenth_over():
    """Wickets in over 19 (0-based 18) are too late; in over 18 (17) they are not."""
    late = [DOT] * (18 * BALLS_PER_OVER) + [W, W]
    assert _play(late, _rescue()).rescue is None
    last = [DOT] * (17 * BALLS_PER_OVER) + [W, W]
    assert _play(last, _rescue()).rescue.over == 17


def test_only_if_he_outbats_the_man_due_in():
    """At two down the man due in is id3; the Impact Player does not outbat him, so not
    then -- but at three down id4 is due and he does."""
    inn = _play([W, W, W], _rescue(outbats=ALL_BATTERS - {"id3"}))
    assert inn.rescue is not None and inn.rescue.wickets == 3
    assert [c.player.person_id for c in inn.batting][4] == "imp"


def test_only_if_a_man_out_may_go_off():
    """At two down id0 and id2 are out and neither may go off (bowlers, say): no rescue.
    At three down id3 is out too, and he may."""
    inn = _play([W, W, W], _rescue(swappable={"id3"}))
    assert inn.rescue is not None and inn.rescue.wickets == 3
    assert inn.rescue.out_id == "id3"


def test_once_only():
    inn = _play([W] * 6, _rescue())
    assert sum(c.player.person_id == "imp" for c in inn.batting) == 1


def test_no_rescue_is_the_innings_it_always_was():
    with_terms = _play([W, W, ONE, W, 7, W], _rescue(outbats=frozenset()))
    without = _play([W, W, ONE, W, 7, W], None)
    assert with_terms.commentary == without.commentary
    assert [c.player.person_id for c in with_terms.batting] == \
        [c.player.person_id for c in without.batting]


# --- who may: rescue_for -------------------------------------------------------------------

def _card(name, bat=0.1, bowl=None, role="batter", positions=frozenset({1, 2, 3})):
    return Card(1, name, name, bat=bat, bowl=bowl, role=role, positions=positions)


class _M:
    """Enough of a Model for `to_player`."""
    unrated_bat = {}
    season_mean = {}


def _xi(bowlers=5):
    xi = [_card("k", role="keeper"), _card("b1"), _card("b2"), _card("b3", bat=0.05)]
    xi += [_card(f"a{i}", bat=0.02, bowl=0.1, role="bowler") for i in range(bowlers)]
    xi += [_card(f"t{i}", bat=0.01) for i in range(11 - len(xi))]
    return xi


@pytest.fixture
def model(monkeypatch):
    import game.__main__ as gm
    monkeypatch.setattr(gm, "to_player",
                        lambda c, m, is_impact=False: Player(c.name, 0.0,
                                                             person_id=c.person_id,
                                                             is_impact=is_impact))
    return _M()


def test_a_bowler_never_rescues(model):
    side = Side("S", "S", _xi(), impact=_card("imp", bowl=0.3, role="bowler"))
    assert rescue_for(side, model) is None


def test_five_bowling_options_must_remain(model):
    """Four bowlers in the eleven and a batter coming on: four. Refused. An all-rounder
    coming on makes five, which is allowed -- the user's own all-rounder clause."""
    assert rescue_for(Side("S", "S", _xi(4), impact=_card("imp", bat=0.3)), model) is None
    allr = _card("imp", bat=0.3, bowl=0.1, role="allrounder")
    assert rescue_for(Side("S", "S", _xi(4), impact=allr), model) is not None
    assert rescue_for(Side("S", "S", _xi(5), impact=_card("imp", bat=0.3)), model) is not None


def test_never_the_keeper_or_a_bowler_off_and_only_worse_bats_matter(model):
    side = Side("S", "S", _xi(5), impact=_card("imp", bat=0.06))
    r = rescue_for(side, model)
    assert "k" not in r.swappable
    assert not any(n.startswith("a") for n in r.swappable)
    assert {"b1", "b2", "b3"} <= r.swappable
    assert "b3" in r.outbats and "b1" not in r.outbats      # 0.06 > 0.05, < 0.1


def test_the_switch_turns_it_off(model):
    side = Side("S", "S", _xi(5), impact=_card("imp", bat=0.3))
    with no_rescue():
        assert rescue_for(side, model) is None
    with no_rescue(False):
        assert rescue_for(side, model) is not None


def test_after_a_rescue_he_takes_the_place_of_the_man_off():
    xi = _xi(5)
    imp = _card("imp", bat=0.3)
    side = Side("S", "S", xi, impact=imp)
    first = Innings([], [])
    assert after_rescue(side, first) is None
    first.rescue = RescueAt("b2", "b2", "imp", "imp", 5, 3, 20)
    eleven = after_rescue(side, first)
    assert eleven[2] is imp and eleven[:2] == xi[:2] and eleven[3:] == xi[3:]


# --- wiring: every way a match is played -----------------------------------------------------

class _Spy:
    """Stands in for `play_innings`: the first innings reports a rescue of b2, and every
    call's attack and rescue terms are kept."""

    def __init__(self, rescue_happens=True):
        self.calls = []
        self.rescue_happens = rescue_happens

    def __call__(self, model, batting, bowling, rng, target=None, **kw):
        self.calls.append(dict(batting=[p.person_id for p in batting],
                               bowling=[p.person_id for p in bowling],
                               rescue=kw.get("rescue"), target=target))
        inn = Innings([], [])
        inn.runs = 100 if target is None else 50
        if target is None and kw.get("rescue") is not None and self.rescue_happens:
            inn.rescue = RescueAt("b2", "b2", "imp", "imp", 6, 4, 60)
        return inn


@pytest.fixture
def wired(monkeypatch, model):
    import game.__main__ as gm
    spy = _Spy()
    monkeypatch.setattr(season, "play_innings", spy)
    monkeypatch.setattr(gm, "attack", lambda xi, m, impact=None: [
        Player(c.name, 0.0, person_id=c.person_id) for c in xi if c.has_bowl])
    monkeypatch.setattr(gm, "lineup", lambda xi, m, impact=None: [
        Player(c.name, 0.0, person_id=c.person_id) for c in xi])
    decided = []
    real = season.decide_impact
    monkeypatch.setattr(season, "decide_impact",
                        lambda side, *a: decided.append(side.name) or real(side, *a))
    imp = _card("imp", bat=0.3, bowl=0.2, role="allrounder")
    home = Side("HOME", "H", _xi(5), impact=imp)
    away = Side("AWAY", "A", [_card(f"x{i}", bowl=0.1 if i > 5 else None)
                              for i in range(11)])
    return spy, decided, home, away


def test_play_bowls_the_rescued_eleven_with_no_second_decision(wired):
    spy, decided, home, away = wired
    season.play(None, home, away, random.Random(0))
    assert spy.calls[0]["rescue"] is not None
    assert "imp" in spy.calls[1]["bowling"]             # he bowls: he is in the eleven
    assert "HOME" not in decided                        # no break-time Impact for HOME


def test_play_open_passes_the_rescue_the_same_way(wired):
    spy, decided, home, away = wired
    for seed in range(20):                  # a seed on which HOME bats first
        spy.calls.clear()
        decided.clear()
        season.play_open(None, home, away, random.Random(seed))
        if spy.calls[0]["batting"][0] == home.xi[0].person_id:
            break
    else:
        pytest.fail("HOME never batted first")
    assert spy.calls[0]["rescue"] is not None
    assert "imp" in spy.calls[1]["bowling"]
    assert "HOME" not in decided


def test_the_human_side_never_rescues_but_its_opponent_does(wired, monkeypatch):
    spy, decided, home, away = wired
    monkeypatch.setattr(season, "toss", lambda rng: True)
    # The human wins the toss and bats: no rescue terms for the human's own innings.
    moves = type("M", (), {})()
    monkeypatch.setattr(season, "_next_toss", lambda *a: season.TossElect("bat"))
    monkeypatch.setattr(season, "_next_impact", lambda *a: season.ImpactPick(None))
    season._play_human_match(None, home, away, random.Random(0), "league", 0, moves)
    assert spy.calls[0]["rescue"] is None
    # The human bowls first: the opponent (HOME here as the computer side) may rescue.
    spy.calls.clear()
    monkeypatch.setattr(season, "_next_toss", lambda *a: season.TossElect("bowl"))
    season._play_human_match(None, away, home, random.Random(0), "league", 0, moves)
    assert spy.calls[0]["rescue"] is not None
    assert "imp" in spy.calls[1]["bowling"]


# --- the bands, and the daily's pins ------------------------------------------------------

def test_the_finisher_band_is_five_to_eight_and_shares_eight_with_the_tail():
    assert BATTING_ROLE_SLOTS["finisher"] == frozenset({5, 6, 7, 8})
    assert BATTING_ROLE_SLOTS["tail"] == frozenset({8, 9, 10, 11})
    assert BATTING_ROLE_SLOTS["middle"] == frozenset({3, 4, 5})


def test_careers_and_auctions_read_the_one_table():
    import game.auction as au
    import game.career as C
    assert C.FINISHER_SLOTS == BATTING_ROLE_SLOTS["finisher"]
    assert au._BAND_SLOTS[2] - {au.IMPACT_SLOT} == BATTING_ROLE_SLOTS["finisher"]


def test_an_old_day_keeps_five_to_seven_and_no_rescue(monkeypatch):
    import web.daily as daily
    fin = _card("f", positions=BATTING_ROLE_SLOTS["finisher"])
    top = _card("t")
    deck = Deck(cards_by_fs={1: [fin, top]}, fs_ids=[1])
    old = daily.old_band_deck(deck)
    assert old.cards_by_fs[1][0].positions == frozenset({5, 6, 7})
    assert old.cards_by_fs[1][1] is top

    seen = []
    monkeypatch.setattr(daily.sess, "replay", lambda d, *a, **k: seen.append(d) or None)
    daily.replay_day(deck, daily.BANDS_FROM - datetime.timedelta(days=1), 1, [1], [])
    daily.replay_day(deck, daily.BANDS_FROM, 1, [1], [])
    assert seen[0].cards_by_fs[1][0].positions == frozenset({5, 6, 7})
    assert seen[1].cards_by_fs[1][0].positions == BATTING_ROLE_SLOTS["finisher"]


def test_an_old_day_plays_without_the_rescue(monkeypatch):
    import web.daily as daily
    states = []
    monkeypatch.setattr(daily, "_play_and_score",
                        lambda *a: states.append(season._RESCUE_OFF.get()))
    monkeypatch.setattr(daily, "nationality_deck", lambda full, d: full)
    deck = Deck(cards_by_fs={}, fs_ids=[])

    class Day:
        challenge_date = daily.RESCUE_FROM - datetime.timedelta(days=1)

    daily.play_and_score(deck, None, Day, 1, "")
    Day.challenge_date = daily.RESCUE_FROM
    daily.play_and_score(deck, None, Day, 1, "")
    assert states == [True, False]
