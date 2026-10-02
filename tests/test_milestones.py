"""The live scoreboard's data: who is at the crease after each over, and the milestones
(fifty, hundred, hat-trick, five-for, three in an over) the reveal celebrates.

Both are side effects of `play_innings`, recorded from figures the loop already holds, so
none of them can change a match. What CAN go wrong is the bookkeeping, and every case
here is one where it would read as plausible while being wrong: a hat-trick that forgets
to span two overs, a dot ball that fails to break one, a striker taken before the change
of ends instead of after it, a fifth wicket celebrated again on the sixth.

Every outcome is scripted ball by ball, so each expected figure can be worked out by hand.
"""

from __future__ import annotations

import random

from etl.feasibility import BOWLERS_IN_TWELVE
from game.simulator import BALLS_PER_OVER, Player, play_innings

_VALUES = (-1.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
W, DOT, ONE, FOUR = 0, 1, 2, 5          # outcome indices, as `draw` returns them


class _Script:
    """A model that plays a fixed sequence of outcomes, one per legal delivery, then dots.
    `state()` is called exactly once per legal ball, which is what makes this work."""

    wide_rate, wide_runs, extras_rate = 0.0, 1.0, 0.0

    def __init__(self, outcomes):
        self._left = list(outcomes)

    def state(self, over, wickets):
        k = self._left.pop(0) if self._left else DOT
        return tuple(1.0 if i == k else 0.0 for i in range(8)), _VALUES


def _innings(outcomes, overs=20):
    bat = [Player(f"p{i}", 0.0, person_id=f"id{i}") for i in range(11)]
    # Equal bowlers: `choose_bowler` then takes them in list order, so w0 bowls overs 1
    # and 6, w1 overs 2 and 7, and so on -- which the hat-trick cases rely on.
    bowl = [Player(f"w{i}", 0.0, bowl=0.0, person_id=f"wid{i}")
            for i in range(BOWLERS_IN_TWELVE)]
    return play_innings(_Script(outcomes), bat, bowl, random.Random(0), overs=overs)


def _kinds(inn):
    return [(m.kind, m.name, m.balls) for m in inn.milestones]


def test_a_hat_trick_spans_the_bowlers_two_overs():
    """w0's last ball of over 1, then the first two of his next over (over 6)."""
    script = [DOT] * 5 + [W] + [DOT] * 24 + [W, W]
    inn = _innings(script)
    assert _kinds(inn) == [("hattrick", "w0", 32)]
    assert inn.milestones[0].wickets == 3


def test_a_dot_ball_between_wickets_breaks_the_hat_trick():
    """Three wickets in four balls of one over: three in the over, not a hat-trick."""
    inn = _innings([W, DOT, W, W])
    assert _kinds(inn) == [("three_in_over", "w0", 4)]


def test_another_bowlers_over_between_his_wickets_does_not_break_the_hat_trick():
    """A hat-trick is the BOWLER's consecutive deliveries, not the innings'. Overs 2-5
    belong to the other four bowlers, full of dots, and w0's streak survives them -- a streak kept per innings rather
    than per bowler would lose this one."""
    script = [DOT] * 4 + [W, W] + [DOT] * 24 + [W]
    inn = _innings(script)
    assert _kinds(inn) == [("hattrick", "w0", 31)]


def test_each_bowling_milestone_fires_once():
    """Every ball a wicket: w0 takes six in over 1 and w1 four in over 2 before the side
    is all out. A hat-trick at the third ball and a five-for at the fifth -- not again at
    the sixth, and the fourth wicket in a row is not a second hat-trick. w1 starts a fresh
    streak and gets his own."""
    inn = _innings([W] * 10)
    assert _kinds(inn) == [("hattrick", "w0", 3), ("five_for", "w0", 5),
                           ("hattrick", "w1", 9)]
    five = inn.milestones[1]
    assert (five.wickets, five.runs, five.faced) == (5, 0, 5)


def test_fifty_and_hundred_fire_on_the_ball_that_reaches_them():
    """Every ball a four. p0 faces the odd-numbered overs (the strike changes only at the
    end of an over): 48 off 12 after over 3, 52 off 13 on the first ball of over 5 --
    innings ball 25 -- and 100 off 25 on the first ball of over 9, innings ball 49."""
    inn = _innings([FOUR] * 120)
    p0 = [(m.kind, m.runs, m.faced, m.balls) for m in inn.milestones if m.name == "p0"]
    assert p0 == [("fifty", 52, 13, 25), ("hundred", 100, 25, 49)]
    assert all(m.wickets == 0 for m in inn.milestones)


def test_a_milestone_in_the_partial_final_over_is_still_recorded():
    """The over log never holds a partial over, so this is the case the milestone list
    exists for. Fours every ball, chasing 99: after four overs the openers have 48 each
    and the side 96, and the first ball of over 5 takes p0 to 52 and the side past the
    target -- a fifty in an over the log never records."""
    bat = [Player(f"p{i}", 0.0) for i in range(11)]
    bowl = [Player(f"w{i}", 0.0, bowl=0.0) for i in range(BOWLERS_IN_TWELVE)]
    inn = play_innings(_Script([FOUR] * 30), bat, bowl, random.Random(0), target=99)
    assert inn.chased and inn.balls == 25
    assert len(inn.over_log) == 4                       # the 25th ball is in no snapshot
    assert [(m.kind, m.name, m.balls) for m in inn.milestones] == [("fifty", "p0", 25)]


def test_the_striker_on_the_board_is_the_one_who_faces_the_next_over():
    """p0 faces all of over 1 (fours), so after the change of ends p1 is on strike for
    over 2 and p0, on 24 off 6, is at the other end."""
    inn = _innings([FOUR] * 6)
    o = inn.over_log[0]
    assert (o.striker.name, o.striker.runs, o.striker.balls) == ("p1", 0, 0)
    assert (o.non_striker.name, o.non_striker.runs, o.non_striker.balls) == ("p0", 24, 6)
    assert o.non_striker.fours == 6


def test_a_new_batter_appears_at_the_crease_after_a_wicket():
    """p0 is out on the first ball and p2 comes in on strike; five singles follow. p2 takes
    the last one and so finishes at the end the next over is bowled TO -- he faces it."""
    inn = _innings([W] + [ONE] * 5)
    o = inn.over_log[0]
    assert (o.striker.name, o.striker.runs, o.striker.balls) == ("p2", 3, 3)
    assert (o.non_striker.name, o.non_striker.runs, o.non_striker.balls) == ("p1", 2, 2)


def test_the_crease_figures_add_up_to_the_score_while_nobody_is_out():
    inn = _innings([ONE, FOUR, DOT, ONE, ONE, FOUR] * 20)
    for o in inn.over_log:
        assert o.striker.runs + o.non_striker.runs == o.runs
        assert o.striker.balls + o.non_striker.balls == o.balls


def test_the_bowlers_figures_are_his_cumulative_figures_after_the_over():
    """Singles every ball: w0 bowls overs 1 and 6, so after over 6 he has 12 balls, 12
    runs; one wicket in his first over is carried into the second."""
    inn = _innings([W] + [ONE] * 119)
    first, second = inn.over_log[0], inn.over_log[5]
    assert (first.bowler, first.bowler_balls, first.bowler_runs, first.bowler_wickets) == (
        "w0", 6, 5, 1)
    assert (second.bowler, second.bowler_balls, second.bowler_runs,
            second.bowler_wickets) == ("w0", 12, 11, 1)


# --- what reaches the page --------------------------------------------------------------

def test_the_api_carries_the_crease_the_bowler_and_the_milestones():
    from web.app import _innings_out
    out = _innings_out(_innings([W] * 10))
    first = out.over_log[0]
    assert first.striker is not None and first.non_striker is not None
    assert (first.bowler_balls, first.bowler_wickets) == (6, 6)
    assert [(m.kind, m.name, m.balls) for m in out.milestones] == [
        ("hattrick", "w0", 3), ("five_for", "w0", 5), ("hattrick", "w1", 9)]


def test_a_daily_innings_bowled_by_nobody_celebrates_no_bowler():
    """A legacy chase was bowled by "bowler 1".."bowler 5", who are not people. Their
    five-for must not reach the scoreboard any more than their card reaches the
    scorecard; the batting milestones are real and stay."""
    from web.app import _daily_innings_out
    script = [W] * 5 + [FOUR] * 115
    synthetic = _daily_innings_out(_innings(script), bowled_by_a_player=False)
    kinds = {m["kind"] for m in synthetic["milestones"]}
    assert "five_for" not in kinds and "hattrick" not in kinds
    assert "fifty" in kinds
    assert all(o["bowler_wickets"] == 0 for o in synthetic["over_log"])
    real = _daily_innings_out(_innings(script), bowled_by_a_player=True)
    assert {"five_for", "hattrick"} <= {m["kind"] for m in real["milestones"]}
