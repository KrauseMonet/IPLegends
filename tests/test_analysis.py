"""Season Analysis (`game/analysis.py`). Pure aggregation over engine output -- no DB, no
simulation -- so these build tiny fake results rather than playing a season."""

from __future__ import annotations

from dataclasses import dataclass, field

from game.analysis import PHASES, OverBar, phase_of, season_analysis


@dataclass
class FakeSnap:
    over: int
    over_runs: int
    over_wickets: int = 0
    bowler: str = "A Bowler"


@dataclass
class FakePlayer:
    name: str


@dataclass
class FakeCard:
    player: FakePlayer
    runs: int = 0
    balls: int = 0
    wickets: int = 0


@dataclass
class FakeSide:
    short: str


@dataclass
class FakeInnings:
    over_log: list = field(default_factory=list)
    batting: list = field(default_factory=list)
    bowling: list = field(default_factory=list)
    runs: int = 0
    wickets: int = 0
    overs: str = "20.0"


@dataclass
class FakeResult:
    home: FakeSide
    away: FakeSide
    home_innings: FakeInnings
    away_innings: FakeInnings


def _result(home_log, away_log, home=None, away=None):
    h, a = home or FakeSide("HOM"), away or FakeSide("AWY")
    return FakeResult(h, a, FakeInnings(over_log=home_log), FakeInnings(over_log=away_log))


# --- the phase rule ------------------------------------------------------------------------

def test_phase_boundaries_match_the_projects_own_rule():
    """SPEC 4.8/A5: death is the final 25% of scheduled overs -- (120*3//4)//6 = over index
    15 -- and powerplay is the first six. An analysis screen using a different split from
    the one the ratings use would quietly contradict them."""
    assert [phase_of(o) for o in (0, 5)] == ["powerplay", "powerplay"]
    assert [phase_of(o) for o in (6, 14)] == ["middle", "middle"]
    assert [phase_of(o) for o in (15, 19)] == ["death", "death"]


def test_a_shortened_innings_moves_the_death_boundary():
    """The rule is proportional, not the literal over 16 -- a ten-over innings dies from
    over 8 (index 7), which is what `(60*3//4)//6` gives."""
    assert phase_of(6, scheduled_balls=60) == "middle"
    assert phase_of(7, scheduled_balls=60) == "death"


# --- aggregation ---------------------------------------------------------------------------

def test_phase_totals_split_runs_and_wickets_by_over():
    log = [FakeSnap(over=0, over_runs=10, over_wickets=1),    # powerplay
           FakeSnap(over=8, over_runs=7),                      # middle
           FakeSnap(over=17, over_runs=20, over_wickets=2)]    # death
    a = season_analysis([_result(log, [])])
    assert (a.phases["powerplay"].runs, a.phases["powerplay"].wickets) == (10, 1)
    assert (a.phases["middle"].runs, a.phases["middle"].wickets) == (7, 0)
    assert (a.phases["death"].runs, a.phases["death"].wickets) == (20, 2)
    assert a.phases["death"].run_rate == 20.0        # one over, twenty runs


def test_manhattan_counts_how_many_innings_reached_each_over():
    """Without this an over only half the innings survive to looks like a collapse. Two
    innings reach over 1; only one reaches over 20."""
    a = season_analysis([_result([FakeSnap(0, 6), FakeSnap(19, 12)], [FakeSnap(0, 4)])])
    first, last = a.manhattan[0], a.manhattan[19]
    assert (first.innings, first.runs) == (2, 10)
    assert first.average_runs == 5.0                  # 10 over TWO innings, not one
    assert (last.innings, last.runs, last.average_runs) == (1, 12, 12.0)


def test_manhattan_is_one_based_for_display():
    """The engine indexes overs from 0; a scorecard never does."""
    a = season_analysis([_result([FakeSnap(0, 6)], [])])
    assert a.manhattan[0].over == 1 and a.manhattan[19].over == 20


def test_your_side_is_matched_by_identity_not_by_name():
    """Two drawn franchise-seasons can share a name, and `Side` has no `eq=False` -- the
    same trap A79/A80/A96 each hit. Two sides named alike, only one tracked."""
    mine, theirs = FakeSide("MI"), FakeSide("MI")
    r = FakeResult(mine, theirs,
                   FakeInnings(over_log=[FakeSnap(0, 30)]),
                   FakeInnings(over_log=[FakeSnap(0, 5)]))
    a = season_analysis([r], track=mine)
    assert a.your_phases["powerplay"].runs == 30, "tracked side's own batting only"
    assert a.phases["powerplay"].runs == 35, "league-wide still counts both"


def test_rate_leaders_need_a_volume_floor():
    """Otherwise the best economy in a tournament belongs to whoever bowled two overs --
    A33's reasoning at one-tournament scale."""
    inn = FakeInnings(
        over_log=[FakeSnap(0, 6)],
        bowling=[FakeCard(FakePlayer("Cameo"), wickets=1, balls=12),       # 2 overs
                 FakeCard(FakePlayer("Workhorse"), wickets=9, balls=300)],  # 50 overs
        batting=[FakeCard(FakePlayer("Slogger"), runs=40, balls=10),
                 FakeCard(FakePlayer("Anchor"), runs=300, balls=200)])
    a = season_analysis([FakeResult(FakeSide("A"), FakeSide("B"), inn, FakeInnings())])
    assert [l.name for l in a.best_economy] == ["Workhorse"], "a 2-over cameo must not qualify"
    assert [l.name for l in a.best_strike] == ["Anchor"], "a 10-ball cameo must not qualify"


def test_best_over_and_highest_innings_are_found():
    r = FakeResult(FakeSide("A"), FakeSide("B"),
                   FakeInnings(over_log=[FakeSnap(3, 9), FakeSnap(18, 27, bowler="X")], runs=200),
                   FakeInnings(over_log=[FakeSnap(1, 11)], runs=150))
    a = season_analysis([r])
    assert a.best_over["runs"] == 27 and a.best_over["over"] == 19 and a.best_over["bowler"] == "X"
    assert a.highest_innings["runs"] == 200


def test_an_unplayed_fixture_is_skipped_not_counted():
    """A room's round can hold a fixture nobody has resolved yet -- and it arrives in two
    shapes: an entry whose `result` is None at all, and a `Result` with null innings."""
    unplayed = FakeResult(FakeSide("A"), FakeSide("B"), None, None)
    a = season_analysis([None, unplayed, _result([FakeSnap(0, 6)], [])])
    assert a.fixtures == 1 and a.innings == 2
