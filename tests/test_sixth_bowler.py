"""[A239] The sixth bowler: a change bowler past the attack's frontline five, capped at
CHANGE_MAX_OVERS, who comes on only to take an over off a frontline bowler being hit."""

from __future__ import annotations

import datetime
import random
from types import SimpleNamespace

import pytest

from etl.feasibility import Card
from game import simulator as S
from game.__main__ import attack, bowling_order, five_man_attack, opposition_twelve
from game.simulator import (
    BALLS_PER_OVER, CHANGE_MAX_OVERS, BowlerCard, Player, change_bowler, choose_bowler,
    play_innings,
)
from tools.snapshot_deck import deck_from, model_from, read_document

DOC = read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="no deck snapshot")


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


def _bat(scoring: float) -> list[Player]:
    return [Player(f"b{i}", 0.0, bat_scoring=scoring, person_id=f"b{i}") for i in range(11)]


def _attack(n: int = 6, quality: float = 0.0) -> list[Player]:
    return [Player(f"w{i}", 0.0, -i / 100, bowl_scoring=quality, person_id=f"w{i}")
            for i in range(n)]


def _overs_by(inn) -> dict[str, int]:
    out: dict[str, int] = {}
    for o in inn.over_log:
        out[o.bowler_id] = out.get(o.bowler_id, 0) + 1
    return out


# -- the rule itself ------------------------------------------------------------------

def _card(pid: str, balls: int = 0, runs: int = 0, change: bool = False) -> BowlerCard:
    c = BowlerCard(Player(pid, 0.0, 0.0, person_id=pid), balls=balls, runs=runs, change=change,
                   max_overs=CHANGE_MAX_OVERS if change else 4)
    return c


def test_a_change_needs_a_full_over_and_an_economy_at_the_bar():
    sixth = _card("six", change=True)
    bar = S.CHANGE_ECONOMY
    under_an_over = _card("a", balls=5, runs=30)
    cheap = _card("b", balls=12, runs=int(bar * 2) - 1)
    at_bar = _card("c", balls=12, runs=int(bar * 2))
    for chosen, want in ((under_an_over, None), (cheap, None), (at_bar, sixth)):
        assert change_bowler([chosen, sixth], chosen, None) is want, chosen.player.name


def test_a_change_bowler_never_bowls_two_running_or_past_his_cap():
    hit = _card("a", balls=12, runs=40)
    sixth = _card("six", change=True)
    assert change_bowler([hit, sixth], hit, previous=sixth) is None
    done = _card("six", balls=CHANGE_MAX_OVERS * BALLS_PER_OVER, change=True)
    assert change_bowler([hit, done], hit, None) is None


def test_rotation_never_picks_a_change_bowler():
    """Even the best bowler in the attack, unbowled, is never the rotation's choice if he is
    the change bowler."""
    front = [_card(f"f{i}") for i in range(5)]
    sixth = _card("six", change=True)
    sixth.player = Player("six", 0.0, 0.9, person_id="six")
    for over in range(20):
        assert not choose_bowler(front + [sixth], None, over).change


def test_the_over_taken_is_the_expensive_bowlers():
    """After a change, the bowler it was taken from waits his turn: of five frontline
    bowlers at two overs each, the one who had an over taken off him is not next."""
    front = [_card(f"f{i}", balls=12) for i in range(5)]
    front[0].player = Player("best", 0.0, 0.9, person_id="best")   # rotation's first choice
    front[0].taken_off = 1
    assert choose_bowler(front, None, 10).player.name != "best"


# -- whole innings --------------------------------------------------------------------

def test_strong_batting_brings_on_the_sixth_and_he_bowls_at_most_two(model):
    used = 0
    for seed in range(20):
        inn = play_innings(model, _bat(0.6), _attack(6), random.Random(seed))
        sixth = inn.bowling[5]
        assert sixth.change and sixth.balls <= CHANGE_MAX_OVERS * BALLS_PER_OVER
        used += sixth.balls > 0
    assert used >= 15


def test_the_sixth_comes_on_only_when_a_frontline_bowler_is_at_the_bar(model):
    """Replayed from the over log: before every change over some frontline bowler had
    bowled an over and was going at CHANGE_ECONOMY or more; and an innings in which no
    frontline bowler ever reached the bar never sees the sixth."""
    bar = S.CHANGE_ECONOMY
    quiet = changed = 0
    for seed in range(60):
        inn = play_innings(model, _bat(-0.2), _attack(6), random.Random(seed))
        figures: dict[str, tuple[int, int]] = {}
        ever = False
        for o in inn.over_log:
            hit = any(b >= BALLS_PER_OVER and r * BALLS_PER_OVER >= bar * b
                      for pid, (b, r) in figures.items() if pid != "w5")
            if o.bowler_id == "w5":
                assert hit, (seed, o.over)
                changed += 1
            ever |= hit
            figures[o.bowler_id] = (o.bowler_balls, o.bowler_runs)
        if not ever:
            assert inn.bowling[5].balls == 0
            quiet += 1
    assert quiet and changed


def test_the_expensive_bowler_bowls_one_fewer(model):
    """In a twenty-over innings every frontline bowler bowls four overs less those taken
    off him -- the change bowler's overs come out of the expensive bowler's share."""
    checked = 0
    for seed in range(30):
        inn = play_innings(model, _bat(0.4), _attack(6), random.Random(seed))
        if inn.balls < 120 or not inn.bowling[5].balls or len(inn.over_log) < 20:
            continue
        overs = _overs_by(inn)
        for b in inn.bowling[:5]:
            assert overs.get(b.player.person_id, 0) == 4 - b.taken_off
        assert sum(b.taken_off for b in inn.bowling[:5]) == overs["w5"]
        checked += 1
    assert checked >= 5


def test_twenty_overs_are_always_bowled_and_never_two_running(model):
    rng = random.Random(1)
    for seed in range(200):
        bowl = [Player(f"w{i}", 0.0, -i / 100, bowl_scoring=rng.uniform(-0.4, 0.3),
                       person_id=f"w{i}") for i in range(6)]
        inn = play_innings(model, _bat(rng.uniform(-0.3, 0.8)), bowl, random.Random(seed))
        ids = [o.bowler_id for o in inn.over_log]
        assert all(a != b for a, b in zip(ids, ids[1:]))
        for b in inn.bowling:
            assert b.balls <= b.max_overs * BALLS_PER_OVER


def test_an_unused_sixth_changes_nothing(model, monkeypatch):
    """A sixth bowler who never comes on draws nothing and moves nothing: with the bar out
    of reach, six bowlers play exactly the innings five do."""
    monkeypatch.setattr(S, "CHANGE_ECONOMY", 1e9)
    for seed in range(10):
        five = play_innings(model, _bat(0.3), _attack(5), random.Random(seed))
        six = play_innings(model, _bat(0.3), _attack(6), random.Random(seed))
        assert [(o.bowler_id, o.runs, o.wickets) for o in five.over_log] == \
               [(o.bowler_id, o.runs, o.wickets) for o in six.over_log]


# -- who the sixth is -------------------------------------------------------------------

@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


def test_attack_adds_the_next_bowler_in_the_order_and_the_pin_removes_him(deck, model):
    checked = 0
    for fs, squad in deck.cards_by_fs.items():
        xi, _ = opposition_twelve(list(squad))
        ranked = sorted((c for c in xi if c.has_bowl), key=bowling_order)
        got = [p.person_id for p in attack(xi, model)]
        assert got == [c.person_id for c in ranked[:6]]
        with five_man_attack():
            assert [p.person_id for p in attack(xi, model)] == got[:5]
        checked += len(ranked) >= 6
    assert checked > 100


def test_a_daily_keeps_the_five_man_attack_it_was_scored_under(monkeypatch):
    from web import daily
    xi = [Card(fs_id=1, person_id=f"p{i}", name=f"p{i}", bat=0.0, season_year=2020,
               bowl=0.1 - i / 100, bowl_balls=300)
          for i in range(7)]
    monkeypatch.setattr(daily, "_play_and_score",
                        lambda full, model, day, player, state: len(attack(xi, model_from(DOC))))
    before = SimpleNamespace(challenge_date=daily.SIXTH_BOWLER_FROM - datetime.timedelta(days=1))
    after = SimpleNamespace(challenge_date=daily.SIXTH_BOWLER_FROM)
    assert daily.play_and_score(None, None, before, 1, "") == 5
    assert daily.play_and_score(None, None, after, 1, "") == 6
    assert len(attack(xi, model_from(DOC))) == 6            # the switch does not leak out
