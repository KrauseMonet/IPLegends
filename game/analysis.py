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

    bat: dict[str, list] = {}      # name -> [runs, balls]
    bowl: dict[str, list] = {}     # name -> [wickets, balls, runs]
    best_over = None
    highest = None

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
            if highest is None or inn.runs > highest["runs"]:
                highest = {"runs": inn.runs, "wickets": inn.wickets,
                           "overs": inn.overs, "side": side.short}

            for b in inn.batting:
                d = bat.setdefault(b.player.name, [0, 0])
                d[0] += b.runs
                d[1] += b.balls
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
    return a
