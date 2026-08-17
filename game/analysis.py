"""Season Analysis: the charts a broadcast puts up after a tournament.

Everything here is computed from what the engine ALREADY produced -- `Result`s carrying two
`Innings`, each with an `over_log` built for the reveal animation (`OverSnapshot`: over
index, bowler, and that over's own runs and wickets). No schema change, no new storage, no
second simulation: a season is reproducible from its state string (A62), so the same replay
that renders the season page feeds this. Measured on a real season: the replay is ~2.7s and
already paid, and this whole aggregation adds ~1.3ms on top of it.

WHAT THE ENGINE CANNOT ANSWER, so that nobody looks for it here:

- **Spin against pace.** `people.bowling_style` is NULL for all 816 people -- the column
  exists and was never filled (the override CSV still awaits a human, per CLAUDE.md's own
  table). This is a data-entry gap, not a modelling one, and no amount of work in this file
  produces it. Parked deliberately rather than approximated.
- **Dismissal kinds.** The simulator's outcome space is runs 0-6 plus "wicket" (A47), so
  there is no caught/bowled/lbw to split by.
- **Boundaries and dot balls.** A `BatterCard` tallies runs and balls, not fours, sixes or
  dots, and `over_log` is per over rather than per ball.
- **Wagon wheels.** Nothing models shot direction.

WHAT IT DOES COVER, beyond the phase totals [A110]: per-bowler phase economy (who actually
bowls the death, attributed over by over off `OverSnapshot.bowler_id`), batting first
against chasing (`Innings.chased` is the engine's own flag, so no inference), batting by
POSITION (`Innings.batting` is in batting order, so the index is the position), and batting
averages (`BatterCard.out` distinguishes a dismissal from a not-out, which strike rate
alone cannot).

PHASES follow SPEC 4.8/A5 exactly, rather than a definition invented for this screen: death
is the final 25% of the innings' scheduled overs -- `(scheduled_balls * 3 // 4) // 6`, which
is over index 15 in a full twenty -- powerplay is the first six, middle is the rest. Using a
different split here would put the analysis screen quietly at odds with the ratings.
"""

from __future__ import annotations

from dataclasses import dataclass, field

BALLS_PER_OVER = 6
FULL_INNINGS_BALLS = 120
POWERPLAY_OVERS = 6


def phase_of(over_no: int, scheduled_balls: int = FULL_INNINGS_BALLS) -> str:
    """SPEC 4.8's own rule, over index 0-based (the same basis `OverSnapshot.over` uses)."""
    if over_no < POWERPLAY_OVERS:
        return "powerplay"
    death_from = (scheduled_balls * 3 // 4) // BALLS_PER_OVER
    return "death" if over_no >= death_from else "middle"


PHASES = ("powerplay", "middle", "death")
PHASE_LABEL = {"powerplay": "Powerplay", "middle": "Middle", "death": "Death"}
PHASE_OVERS = {"powerplay": "1-6", "middle": "7-15", "death": "16-20"}


@dataclass
class PhaseSplit:
    runs: int = 0
    wickets: int = 0
    overs: int = 0

    @property
    def run_rate(self) -> float:
        return round(self.runs / self.overs, 2) if self.overs else 0.0

    @property
    def balls_per_wicket(self) -> float:
        return round(self.overs * BALLS_PER_OVER / self.wickets, 1) if self.wickets else 0.0


@dataclass
class OverBar:
    """One column of a Manhattan. Aggregated across the tournament, so `innings` is how many
    innings actually reached this over -- without it, over 20 looks like a collapse when it
    is really that half the innings ended before it."""

    over: int          # 1-based for display; the engine's index + 1
    runs: int = 0
    wickets: int = 0
    innings: int = 0

    @property
    def average_runs(self) -> float:
        return round(self.runs / self.innings, 2) if self.innings else 0.0


@dataclass
class Leader:
    name: str
    value: float
    detail: str


@dataclass
class PhaseBowler:
    """One bowler's work in ONE phase, attributed over by over.

    This is the section the whole feature exists for: `OverSnapshot` records who bowled
    each over, so a death-overs economy is a real measurement here rather than an
    inference from a season total. In T20 that is the difference between a good bowler and
    a valuable one -- an economy of 7 across twenty overs at the death is a different
    player from the same 7 in the powerplay, and no season aggregate can tell them apart.
    """

    name: str
    person_id: str
    overs: int = 0
    runs: int = 0
    wickets: int = 0

    @property
    def economy(self) -> float:
        return round(self.runs / self.overs, 2) if self.overs else 0.0


@dataclass
class InningsSplit:
    """Batting first against chasing. `Innings.chased` is set by the engine, so this needs
    no inference about which innings came second."""

    innings: int = 0
    runs: int = 0
    wins: int = 0

    @property
    def average(self) -> float:
        return round(self.runs / self.innings, 1) if self.innings else 0.0

    @property
    def win_rate(self) -> float:
        return round(100 * self.wins / self.innings, 1) if self.innings else 0.0


@dataclass
class PositionRow:
    """Batting position 1-11. `Innings.batting` is built in batting order
    (`cards[0]` is the striker at ball one), so the list index IS the position."""

    position: int
    runs: int = 0
    balls: int = 0
    outs: int = 0
    innings: int = 0

    @property
    def average(self) -> float:
        # Runs per DISMISSAL, cricket's own convention -- a not-out is not a completed
        # innings. Zero dismissals means no average exists, which is a dash rather than
        # an infinity or a zero.
        return round(self.runs / self.outs, 1) if self.outs else 0.0

    @property
    def strike_rate(self) -> float:
        return round(100 * self.runs / self.balls, 1) if self.balls else 0.0


@dataclass
class SeasonAnalysis:
    fixtures: int = 0
    innings: int = 0
    overs_logged: int = 0
    phases: dict = field(default_factory=dict)          # league-wide
    your_phases: dict = field(default_factory=dict)     # the tracked side batting
    manhattan: list = field(default_factory=list)       # league-wide, 20 bars
    your_manhattan: list = field(default_factory=list)
    top_scorers: list = field(default_factory=list)
    top_wickets: list = field(default_factory=list)
    best_economy: list = field(default_factory=list)
    best_strike: list = field(default_factory=list)
    best_over: dict | None = None
    highest_innings: dict | None = None
    # [A110]
    death_bowlers: list = field(default_factory=list)      # best economy, overs 16-20
    powerplay_bowlers: list = field(default_factory=list)  # best economy, overs 1-6
    bat_first: InningsSplit = field(default_factory=InningsSplit)
    chasing: InningsSplit = field(default_factory=InningsSplit)
    positions: list = field(default_factory=list)          # 11 rows
    best_averages: list = field(default_factory=list)       # runs per dismissal


def _blank_phases() -> dict:
    return {p: PhaseSplit() for p in PHASES}


def _blank_manhattan() -> list:
    return [OverBar(over=i + 1) for i in range(FULL_INNINGS_BALLS // BALLS_PER_OVER)]


def _accumulate(innings, phases: dict, bars: list) -> None:
    for snap in innings.over_log:
        if snap.over >= len(bars):
            continue                     # a miscounted/extended over cannot land in a bar
        split = phases[phase_of(snap.over)]
        split.runs += snap.over_runs
        split.wickets += snap.over_wickets
        split.overs += 1
        bar = bars[snap.over]
        bar.runs += snap.over_runs
        bar.wickets += snap.over_wickets
        bar.innings += 1


def season_analysis(results, track=None) -> SeasonAnalysis:
    """`results` is any iterable of `Result`; `track` is the side whose own batting gets its
    own split (the human's in solo, a seat's in a room), or None for league-wide only.

    Sides are compared by IDENTITY, never by name or value -- `Side` is a plain dataclass
    without `eq=False`, and two drawn franchise-seasons can legitimately share a name. This
    file is the fourth place in the codebase to need that care (A79/A80/A96), so it is
    stated rather than assumed.
    """
    a = SeasonAnalysis(phases=_blank_phases(), your_phases=_blank_phases(),
                       manhattan=_blank_manhattan(), your_manhattan=_blank_manhattan())

    bat: dict[str, list] = {}      # name -> [runs, balls, outs]
    bowl: dict[str, list] = {}     # name -> [wickets, balls, runs]
    best_over = None
    highest = None
    # [A110] Keyed by person_id, never the name string -- two drafted seasons can share
    # one, and this aggregates across every side in the tournament. The name rides along
    # for display only.
    phase_bowl: dict[tuple[str, str], PhaseBowler] = {}
    positions = [PositionRow(position=i + 1) for i in range(11)]

    for r in results:
        if r is None:
            continue           # a room's round can hold a fixture nobody has resolved yet
        pairs = ((r.home_innings, r.home), (r.away_innings, r.away))
        if not all(i for i, _ in pairs):
            continue
        a.fixtures += 1
        for inn, side in pairs:
            a.innings += 1
            a.overs_logged += len(inn.over_log)
            _accumulate(inn, a.phases, a.manhattan)
            if track is not None and side is track:
                _accumulate(inn, a.your_phases, a.your_manhattan)

            for snap in inn.over_log:
                if best_over is None or snap.over_runs > best_over["runs"]:
                    best_over = {"runs": snap.over_runs, "over": snap.over + 1,
                                 "bowler": snap.bowler, "side": side.short}
                # Per-over bowling attribution. `bowler_id` is empty only for a snapshot
                # built by an older caller or a test; falling back to the name keeps the
                # over counted rather than dropping it, which would understate a workload.
                key = (snap.bowler_id or snap.bowler, phase_of(snap.over))
                pb = phase_bowl.get(key)
                if pb is None:
                    pb = phase_bowl[key] = PhaseBowler(name=snap.bowler,
                                                       person_id=snap.bowler_id)
                pb.overs += 1
                pb.runs += snap.over_runs
                pb.wickets += snap.over_wickets
            if highest is None or inn.runs > highest["runs"]:
                highest = {"runs": inn.runs, "wickets": inn.wickets,
                           "overs": inn.overs, "side": side.short}

            # Batting first against chasing, from the PAIR POSITION, not `Innings.chased`.
            #
            # `chased` was the obvious-looking field and it is a different fact: the engine
            # sets it only when a target is actually overhauled (`innings.runs > target`),
            # so it marks a SUCCESSFUL chase and is false for every first innings AND every
            # failed one. Reading it as "batted second" gave 108 bat-first innings against
            # 40 chasing -- which does not even split 148 evenly -- and a chasing win rate
            # of 100%, because the flag and the win condition were the same thing.
            #
            # `home` bats first here by construction (`play()` calls `play_innings` for
            # home with no target, then for away with one), which is what `pairs` orders
            # by, so the first element of the pair is the first innings.
            split = a.bat_first if inn is r.home_innings else a.chasing
            split.innings += 1
            split.runs += inn.runs
            if r.winner is side:
                split.wins += 1

            for pos, b in enumerate(inn.batting):
                # `Innings.batting` is in batting order, so the index IS the position --
                # but only for someone who actually came to the crease. A card that never
                # faced a ball is not a completed innings at that position and would drag
                # every average down with a phantom zero.
                if pos < len(positions) and b.faced_any:
                    row = positions[pos]
                    row.innings += 1
                    row.runs += b.runs
                    row.balls += b.balls
                    row.outs += 1 if b.out else 0

            for b in inn.batting:
                d = bat.setdefault(b.player.name, [0, 0, 0])
                d[0] += b.runs
                d[1] += b.balls
                d[2] += 1 if b.out else 0
            for b in inn.bowling:
                d = bowl.setdefault(b.player.name, [0, 0, 0])
                d[0] += b.wickets
                d[1] += b.balls
                d[2] += b.runs

    a.best_over, a.highest_innings = best_over, highest

    a.top_scorers = [Leader(n, v[0], f"{v[1]} balls")
                     for n, v in sorted(bat.items(), key=lambda kv: -kv[1][0])[:10]]
    a.top_wickets = [Leader(n, v[0], f"{v[1] // BALLS_PER_OVER} overs")
                     for n, v in sorted(bowl.items(), key=lambda kv: -kv[1][0])[:10]]

    # Rate leaders need a volume floor or they are won by whoever bowled two overs -- A33's
    # reasoning at one-tournament scale. The numbers are not picked round: they are half a
    # league campaign (7 of 14 matches) at A59's own declared reference exposures, 18 balls
    # faced and 24 bowled per match. An earlier pass used 90/120 and it showed -- the best
    # strike rate came back off 94 balls, under seven a match, which is a cameo rather than
    # a season and reads as one.
    MIN_BALLS_BOWLED = 24 * 7      # 28 overs
    MIN_BALLS_FACED = 18 * 7
    econ = [(n, round(v[2] / (v[1] / BALLS_PER_OVER), 2), v[1])
            for n, v in bowl.items() if v[1] >= MIN_BALLS_BOWLED]
    a.best_economy = [Leader(n, e, f"{b // BALLS_PER_OVER} overs")
                      for n, e, b in sorted(econ, key=lambda t: t[1])[:10]]
    sr = [(n, round(100 * v[0] / v[1], 1), v[1])
          for n, v in bat.items() if v[1] >= MIN_BALLS_FACED]
    a.best_strike = [Leader(n, s, f"{b} balls")
                     for n, s, b in sorted(sr, key=lambda t: -t[1])[:10]]

    # [A110] Batting AVERAGE -- runs per dismissal, which is a different claim from strike
    # rate and the one that separates an anchor from a slogger. Same volume floor, plus a
    # dismissal floor: an average off one dismissal is a single score, not an average.
    MIN_DISMISSALS = 5
    avg = [(n, round(v[0] / v[2], 1), v[2])
           for n, v in bat.items() if v[1] >= MIN_BALLS_FACED and v[2] >= MIN_DISMISSALS]
    a.best_averages = [Leader(n, x, f"{o} outs")
                       for n, x, o in sorted(avg, key=lambda t: -t[1])[:10]]

    a.positions = positions

    # A phase economy needs enough overs IN THAT PHASE to mean anything. Four overs at the
    # death is one match's full allocation; the floor is a season's worth of the job, so a
    # bowler who bowled two good death overs all tournament does not top the list.
    MIN_PHASE_OVERS = 12
    def _phase_list(phase: str) -> list:
        rows = [pb for (_, ph), pb in phase_bowl.items()
                if ph == phase and pb.overs >= MIN_PHASE_OVERS]
        return sorted(rows, key=lambda pb: (pb.economy, -pb.wickets))[:10]

    a.death_bowlers = _phase_list("death")
    a.powerplay_bowlers = _phase_list("powerplay")
    return a
