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
    bowler_id: str = ""


@dataclass
class FakePlayer:
    name: str


@dataclass
class FakeCard:
    player: FakePlayer
    runs: int = 0
    balls: int = 0
    wickets: int = 0
    out: bool = False
    faced_any: bool = True


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
    chased: bool = False


@dataclass
class FakeResult:
    home: FakeSide
    away: FakeSide
    home_innings: FakeInnings
    away_innings: FakeInnings
    winner: FakeSide | None = None


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


# --- [A110] per-bowler phase attribution, chases, positions, averages -----------------

def _bowl_log(overs, bowler, bid, runs=6, wkts=0):
    return [FakeSnap(over=o, over_runs=runs, over_wickets=wkts, bowler=bowler, bowler_id=bid)
            for o in overs]


def test_a_bowlers_death_overs_are_separated_from_his_powerplay_overs():
    """The point of the whole section. A season economy averages the two together and
    cannot tell a death specialist from a powerplay one; per-over attribution can."""
    death = _bowl_log(range(15, 20), "Death Man", "d1", runs=9)          # 5 overs @ 9
    power = _bowl_log(range(0, 6), "Power Man", "p1", runs=5)            # 6 overs @ 5
    # 12 overs of each, so both clear MIN_PHASE_OVERS.
    log = (death + power) * 3
    a = season_analysis([_result(log, [])])
    assert [b.name for b in a.death_bowlers] == ["Death Man"]
    assert [b.name for b in a.powerplay_bowlers] == ["Power Man"]
    assert a.death_bowlers[0].economy == 9.0
    assert a.powerplay_bowlers[0].economy == 5.0


def test_phase_bowling_is_keyed_on_person_not_name():
    """CLAUDE.md's standing rule. Two drafted seasons can share a name, and this
    aggregates across every side in the tournament."""
    a_log = _bowl_log(range(15, 20), "J Smith", "person-a", runs=6) * 3
    b_log = _bowl_log(range(15, 20), "J Smith", "person-b", runs=12) * 3
    a = season_analysis([_result(a_log, b_log)])
    assert len(a.death_bowlers) == 2, "same name, two people -- two rows"
    assert {b.person_id for b in a.death_bowlers} == {"person-a", "person-b"}
    assert [b.economy for b in a.death_bowlers] == [6.0, 12.0], "cheapest first"


def test_a_bowler_below_the_phase_floor_is_left_out():
    """Four death overs is one match's allocation; a leaderboard off that is a scoreline."""
    a = season_analysis([_result(_bowl_log(range(16, 20), "Cameo", "c1", runs=1), [])])
    assert a.death_bowlers == []


def test_batting_first_and_chasing_split_on_the_pair_position_not_the_chased_flag():
    """A FAILED chase is the case that matters, and the first version of this test did not
    contain one -- so it passed against both the right implementation and the wrong one.

    `Innings.chased` looks like "batted second" and is not: the engine sets it only when a
    target is actually overhauled, so it is false for every first innings AND every failed
    chase. Reading it as the split gave 108 bat-first against 40 chasing on a real season
    (which does not even divide 148) and a chasing win rate of 100%, because the flag and
    the win condition were the same fact. `home` bats first by construction."""
    home, away = FakeSide("HOM"), FakeSide("AWY")
    # Away batted second and FELL SHORT: chased is False even though they chased.
    failed = FakeResult(home, away,
                        FakeInnings(over_log=[FakeSnap(0, 6)], runs=180, chased=False),
                        FakeInnings(over_log=[FakeSnap(0, 6)], runs=150, chased=False),
                        winner=home)
    a = season_analysis([failed])
    assert a.chasing.innings == 1, "a failed chase is still a chase"
    assert a.chasing.runs == 150 and a.chasing.wins == 0
    assert a.bat_first.innings == 1 and a.bat_first.runs == 180 and a.bat_first.wins == 1

    # And a successful one, so both branches of the win count are covered.
    won = FakeResult(home, away,
                     FakeInnings(over_log=[FakeSnap(0, 6)], runs=180, chased=False),
                     FakeInnings(over_log=[FakeSnap(0, 6)], runs=181, chased=True),
                     winner=away)
    b = season_analysis([failed, won])
    assert b.bat_first.innings == 2 and b.chasing.innings == 2, "every match has one of each"
    assert b.bat_first.wins == 1 and b.chasing.wins == 1
    assert b.chasing.win_rate == 50.0


def test_batting_positions_use_the_list_order_and_skip_a_man_who_never_batted():
    """`Innings.batting` is in batting order, so the index is the position -- but a card
    that never faced a ball is not an innings at that position, and counting it would drag
    the average down with a phantom zero."""
    inn = FakeInnings(over_log=[FakeSnap(0, 6)], batting=[
        FakeCard(FakePlayer("Opener"), runs=60, balls=40, out=True),
        FakeCard(FakePlayer("Two"), runs=30, balls=20, out=False),
        FakeCard(FakePlayer("Did not bat"), runs=0, balls=0, faced_any=False),
    ])
    a = season_analysis([FakeResult(FakeSide("A"), FakeSide("B"), inn, FakeInnings())])
    assert (a.positions[0].runs, a.positions[0].outs, a.positions[0].innings) == (60, 1, 1)
    assert a.positions[0].average == 60.0 and a.positions[0].strike_rate == 150.0
    assert a.positions[1].outs == 0, "not out"
    assert a.positions[1].average == 0.0, "no dismissal means no average, not infinity"
    assert a.positions[2].innings == 0, "never faced a ball"
    assert len(a.positions) == 11


def test_batting_average_needs_dismissals_not_just_balls():
    """An average off one dismissal is a single score. Strike rate and average are
    different claims and this one needs its own floor."""
    inn = FakeInnings(over_log=[FakeSnap(0, 6)], batting=[
        FakeCard(FakePlayer("Anchor"), runs=600, balls=400, out=True),
        FakeCard(FakePlayer("OneOut"), runs=300, balls=200, out=True),
    ])
    # One innings -> one dismissal each, under MIN_DISMISSALS.
    a = season_analysis([FakeResult(FakeSide("A"), FakeSide("B"), inn, FakeInnings())])
    assert a.best_averages == [], "one dismissal is not an average"
    # Six innings -> six dismissals each, over the floor.
    many = [FakeResult(FakeSide("A"), FakeSide("B"), inn, FakeInnings()) for _ in range(6)]
    b = season_analysis(many)
    assert [l.name for l in b.best_averages] == ["Anchor", "OneOut"]
    # The same innings six times, so the RUNS multiply with the dismissals: 3,600 over 6.
    assert b.best_averages[0].value == 600.0
    assert b.best_averages[1].value == 300.0, "and the ordering is by average, not runs"
