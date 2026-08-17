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
    # [A115] Default 0 so every pre-existing fixture stays valid and keeps meaning what it
    # meant. A boundary-specific test sets them explicitly rather than relying on these.
    over_fours: int = 0
    over_sixes: int = 0


@dataclass
class FakePlayer:
    name: str
    person_id: str = ""      # empty falls back to the name, as the real code does
    # [A113] 'pace' | 'spin' | None. None is the default deliberately: a fake that says
    # nothing about style must land in the `unknown` bucket, never be assumed into a real
    # one, which is the property test_a_bowler_with_no_known_style_* pins.
    bowling_style: str | None = None


@dataclass
class FakeCard:
    player: FakePlayer
    runs: int = 0
    balls: int = 0
    wickets: int = 0
    out: bool = False
    faced_any: bool = True
    fours: int = 0
    sixes: int = 0


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
    fours: int = 0
    sixes: int = 0


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
    # Six innings -> six dismissals each, over the floor. ONE side object across all six,
    # as a real team is: [A111] a tally is keyed on (person, side), so six freshly built
    # FakeSides would be six different teams with one dismissal apiece.
    side_a, side_b = FakeSide("A"), FakeSide("B")
    many = [FakeResult(side_a, side_b, inn, FakeInnings()) for _ in range(6)]
    b = season_analysis(many)
    assert [l.name for l in b.best_averages] == ["Anchor", "OneOut"]
    # The same innings six times, so the RUNS multiply with the dismissals: 3,600 over 6.
    assert b.best_averages[0].value == 600.0
    assert b.best_averages[1].value == 300.0, "and the ordering is by average, not runs"


def test_a_player_on_two_sides_is_not_summed_into_one_inflated_row():
    """[A111] Measured on a real season: 16 of 100 people turned out for MORE THAN ONE
    side, because the nine historical opponents are independent franchise-season draws and
    a career spans franchises. Keying a tally on the NAME credited such a player with a
    combined total no single team produced -- the same fault A96 fixed for the Orange and
    Purple Caps, reproduced here.

    The rows stay separate AND carry the team, which is what makes two of them legible."""
    left, right = FakeSide("LEF"), FakeSide("RIG")
    dave = FakePlayer("D Warner", "person-dave")
    for_left = FakeInnings(over_log=[FakeSnap(0, 6)],
                           batting=[FakeCard(dave, runs=400, balls=200, out=True)])
    for_right = FakeInnings(over_log=[FakeSnap(0, 6)],
                            batting=[FakeCard(dave, runs=300, balls=150, out=True)])
    a = season_analysis([FakeResult(left, right, for_left, for_right)])

    rows = [l for l in a.top_scorers if l.name == "D Warner"]
    assert len(rows) == 2, "one row per (person, side), never one summed row"
    assert sorted(l.value for l in rows) == [300, 400]
    assert not any(l.value == 700 for l in a.top_scorers), "700 is a total nobody made"
    assert {l.team for l in rows} == {"LEF", "RIG"}, "the team is what tells them apart"


def test_the_same_side_across_many_matches_still_accumulates():
    """The fix must not go the other way: a player's own season for ONE team is still one
    row, summed across every match he played for it."""
    side, other = FakeSide("MI"), FakeSide("CSK")
    p = FakePlayer("R Sharma", "person-rohit")
    inn = FakeInnings(over_log=[FakeSnap(0, 6)],
                      batting=[FakeCard(p, runs=50, balls=30, out=True)])
    a = season_analysis([FakeResult(side, other, inn, FakeInnings()) for _ in range(4)])
    rows = [l for l in a.top_scorers if l.name == "R Sharma"]
    assert len(rows) == 1 and rows[0].value == 200, "four matches, one side, one row"
    assert rows[0].team == "MI"


# --- [A113] spin against pace ---------------------------------------------------------------

def _styled(log, bowlers):
    """One innings whose `bowling` list carries the styles the over log refers to.

    The style is deliberately NOT on the snapshot -- `style_of` looks it up from
    `Innings.bowling` (A19), so a test that set it on FakeSnap would be testing nothing the
    real code does.
    """
    return FakeInnings(over_log=log,
                       bowling=[FakeCard(FakePlayer(n, person_id=n, bowling_style=s))
                                for n, s in bowlers])


def _snaps(overs, bowler):
    return [FakeSnap(over=o, over_runs=6, over_wickets=1, bowler=bowler, bowler_id=bowler)
            for o in overs]


def test_spin_and_pace_overs_land_in_the_right_phase_buckets():
    """The whole point of the feature: a pace bowler's powerplay overs and a spinner's
    middle overs must not end up in the same cell, nor in each other's."""
    inn = _styled(_snaps([0, 1], "quick") + _snaps([7, 8], "tweaker"),
                  [("quick", "pace"), ("tweaker", "spin")])
    a = season_analysis([FakeResult(FakeSide("H"), FakeSide("A"), inn, FakeInnings())])
    assert a.style_phases["powerplay"]["pace"].overs == 2
    assert a.style_phases["powerplay"]["spin"].overs == 0
    assert a.style_phases["middle"]["spin"].overs == 2
    assert a.style_phases["middle"]["pace"].overs == 0
    # and the runs/wickets ride along with the overs, not just the count
    assert a.style_phases["powerplay"]["pace"].runs == 12
    assert a.style_phases["middle"]["spin"].wickets == 2


def test_a_bowler_with_no_known_style_is_counted_as_neither():
    """A23 at the point it actually bites. `bowling_style = None` is a real population --
    97 people bowled in the archive but never reached SPEC 6.3's 30-legal-ball threshold,
    so they have no style and never will. Folding them into the majority style is the
    error no internal check can catch, so it gets its own test."""
    inn = _styled(_snaps([0, 1, 2], "mystery"), [("mystery", None)])
    a = season_analysis([FakeResult(FakeSide("H"), FakeSide("A"), inn, FakeInnings())])
    assert a.style_phases["powerplay"]["unknown"].overs == 3
    assert a.style_phases["powerplay"]["pace"].overs == 0
    assert a.style_phases["powerplay"]["spin"].overs == 0


def test_an_unrecognised_style_string_is_unknown_rather_than_accepted():
    """`style_of` whitelists 'pace'/'spin' instead of trusting whatever it is given, so a
    value a future archive invents surfaces as unknown and gets noticed rather than being
    silently promoted to a third real bucket."""
    inn = _styled(_snaps([0], "odd"), [("odd", "left-arm wrist spin")])
    a = season_analysis([FakeResult(FakeSide("H"), FakeSide("A"), inn, FakeInnings())])
    assert a.style_phases["powerplay"]["unknown"].overs == 1
    assert a.style_phases["powerplay"]["spin"].overs == 0, "not matched on the word 'spin'"


def test_the_style_split_reconciles_with_the_phase_totals():
    """The test most likely to catch a real bug: every over bowled in a phase must appear
    in exactly one style bucket for that phase, so the three styles sum to the phase's own
    over count -- which `phases` computes independently, in `_accumulate`, from the same
    log. A dropped over, a double-count, or an over attributed to the wrong phase breaks
    this even when each individual bucket looks plausible.

    Deliberately mixes all three styles AND both innings, so a bug that only shows up on
    the second side (the shape that produced A101's swapped bowling figures) is in scope.
    """
    home = _styled(_snaps([0, 1, 2], "p1") + _snaps([7, 8], "s1") + _snaps([15, 16], "u1"),
                   [("p1", "pace"), ("s1", "spin"), ("u1", None)])
    away = _styled(_snaps([3], "p2") + _snaps([9, 10, 11], "s2") + _snaps([17], "u2"),
                   [("p2", "pace"), ("s2", "spin"), ("u2", None)])
    a = season_analysis([FakeResult(FakeSide("H"), FakeSide("A"), home, away)])
    for phase in PHASES:
        by_style = sum(a.style_phases[phase][s].overs
                       for s in ("pace", "spin", "unknown"))
        assert by_style == a.phases[phase].overs, (
            f"{phase}: {by_style} style-attributed overs against "
            f"{a.phases[phase].overs} in the phase total")
    # and nothing was lost overall
    assert sum(a.style_phases[p][s].overs for p in PHASES
               for s in ("pace", "spin", "unknown")) == a.overs_logged


def test_every_phase_carries_all_three_style_buckets_even_at_zero():
    """An absent bucket and a zero bucket say different things (A31's own argument for
    storing its five never-observed states as explicit zeroes). A consumer must never have
    to tell 'no unknown overs' from 'unknown was omitted'."""
    inn = _styled(_snaps([0], "p1"), [("p1", "pace")])
    a = season_analysis([FakeResult(FakeSide("H"), FakeSide("A"), inn, FakeInnings())])
    for phase in PHASES:
        assert set(a.style_phases[phase]) == {"pace", "spin", "unknown"}, phase


# --- [A115] boundaries ---------------------------------------------------------------------

def test_phase_boundaries_land_in_the_phase_the_over_belongs_to():
    """The same phase rule the runs already follow. A six in over 18 is a death six, and
    the fours/sixes must not pool into one tournament-wide number the way a naive
    accumulator would leave them."""
    log = [FakeSnap(over=0, over_runs=10, over_fours=2, over_sixes=0),    # powerplay
           FakeSnap(over=8, over_runs=8, over_fours=1, over_sixes=0),     # middle
           FakeSnap(over=18, over_runs=20, over_fours=1, over_sixes=2)]   # death
    a = season_analysis([_result(log, [])])
    assert (a.phases["powerplay"].fours, a.phases["powerplay"].sixes) == (2, 0)
    assert (a.phases["middle"].fours, a.phases["middle"].sixes) == (1, 0)
    assert (a.phases["death"].fours, a.phases["death"].sixes) == (1, 2)


def test_boundary_share_is_measured_against_its_own_phases_runs():
    """Both sides of the ratio come off the same snapshots. 2 fours and 2 sixes is 20 runs
    of a 40-run phase, so 50% -- and reading the denominator off the innings total instead
    would put a real boundary over a bigger number and understate every phase."""
    log = [FakeSnap(over=18, over_runs=40, over_fours=2, over_sixes=2)]
    a = season_analysis([_result(log, [])])
    assert a.phases["death"].boundary_runs == 20
    assert a.phases["death"].boundary_share == 50.0


def test_a_phase_with_runs_but_no_boundary_shares_zero_rather_than_dividing_by_nothing():
    a = season_analysis([_result([FakeSnap(over=0, over_runs=6)], [])])
    assert a.phases["powerplay"].boundary_share == 0.0
    assert a.phases["middle"].boundary_share == 0.0     # no runs at all, still not a crash


def test_tournament_totals_come_from_the_innings_not_the_over_log():
    """A partial final over is never logged (`OverSnapshot`'s own rule), so the six that
    wins a chase is invisible to the phase table and must NOT be missing from the
    headline. The innings here carries two more fours than its log does, which is exactly
    the shape of a chase ending mid-over."""
    inn = FakeInnings(over_log=[FakeSnap(over=0, over_runs=10, over_fours=1, over_sixes=1)],
                      runs=100, fours=3, sixes=1)
    a = season_analysis([FakeResult(FakeSide("H"), FakeSide("A"), inn, FakeInnings())])
    assert a.phases["powerplay"].fours == 1        # the log's view
    assert a.total_fours == 3                      # the innings' view
    assert a.total_sixes == 1
    assert a.boundary_runs == 3 * 4 + 1 * 6
    assert a.boundary_share == 18.0                # 18 of 100


def test_most_sixes_is_a_person_and_side_pair_like_every_other_board():
    """A111's rule, which this file has now had to apply four times: the same man on two
    drawn sides is two rows, never one summed total no team produced."""
    mumbai, chennai = FakeSide("MI"), FakeSide("CSK")
    hitter = FakePlayer("A Hitter", person_id="p1")
    for_mi = FakeInnings(batting=[FakeCard(hitter, runs=60, balls=30, sixes=5, fours=2)])
    for_csk = FakeInnings(batting=[FakeCard(hitter, runs=40, balls=25, sixes=4, fours=1)])
    a = season_analysis([
        FakeResult(mumbai, FakeSide("X"), for_mi, FakeInnings()),
        FakeResult(chennai, FakeSide("Y"), for_csk, FakeInnings()),
    ])
    rows = [(r.name, r.value, r.team) for r in a.most_sixes]
    assert rows == [("A Hitter", 5, "MI"), ("A Hitter", 4, "CSK")]
    assert 9 not in [r.value for r in a.most_sixes]      # never the summed total


def test_a_batter_who_hit_none_is_absent_from_the_boundary_boards():
    """Absent, not present with a zero. A leaderboard of noughts is not a leaderboard."""
    a = season_analysis([FakeResult(
        FakeSide("H"), FakeSide("A"),
        FakeInnings(batting=[FakeCard(FakePlayer("A Blocker", person_id="p9"),
                                      runs=20, balls=40)]),
        FakeInnings())])
    assert a.most_sixes == []
    assert a.most_fours == []


def test_boundaries_are_attributed_to_the_batting_position_the_man_came_in_at():
    a = season_analysis([FakeResult(
        FakeSide("H"), FakeSide("A"),
        FakeInnings(batting=[
            FakeCard(FakePlayer("Opener", person_id="o"), runs=50, balls=30, fours=6, sixes=1),
            FakeCard(FakePlayer("Three", person_id="t"), runs=20, balls=15, fours=1, sixes=2),
        ]),
        FakeInnings())])
    assert (a.positions[0].fours, a.positions[0].sixes) == (6, 1)
    assert (a.positions[1].fours, a.positions[1].sixes) == (1, 2)
    assert (a.positions[2].fours, a.positions[2].sixes) == (0, 0)
