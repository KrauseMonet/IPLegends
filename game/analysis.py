"""Season Analysis: the charts a broadcast puts up after a tournament.

Everything here is computed from what the engine ALREADY produced -- `Result`s carrying two
`Innings`, each with an `over_log` built for the reveal animation (`OverSnapshot`: over
index, bowler, and that over's own runs and wickets). No schema change, no new storage, no
second simulation: a season is reproducible from its state string (A62), so the same replay
that renders the season page feeds this. Measured on a real season: the replay is ~2.7s and
already paid, and this whole aggregation adds ~1.3ms on top of it.

WHAT THE ENGINE CANNOT ANSWER, so that nobody looks for it here:

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
alone cannot), and [A113] **spin against pace, per phase**.

Spin-vs-pace was listed here as something the engine COULD NOT answer until 2026-08-17, and
it is worth saying what changed, because the reason moved twice. First it was a data gap:
`people.bowling_style` was NULL for everybody. A112 filled it (479 of 479 bowlers, 313 pace
and 166 spin). That left a PLUMBING gap -- the style was in the database and the analysis
never touches the database -- so A113 threaded it: `bowling_style` onto `Card` (selected in
`load_deck`), onto `Player` (via `to_player`), and it is then looked up per over from
`Innings.bowling` rather than copied onto every `OverSnapshot` (A19). An UNKNOWN style is
its own bucket in every phase and is never folded into either real one (A23) -- and it is
NOT empty, since 97 people bowled without ever reaching SPEC 6.3's 30-ball threshold.

**Read `StyleSplit` before displaying this: the per-phase ECONOMIES are meaningful and the
per-phase over SHARES are not**, because `choose_bowler` assigns overs by workload and
models no phase policy. That is measured, not assumed.

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
    # [A111] Which SIDE this figure was earned for. Not decoration: a person can turn out
    # for more than one drawn side in the same tournament, so a leaderboard row is a
    # (person, side) pair and the team is what tells two of them apart.
    team: str = ""


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
    team: str = ""
    overs: int = 0
    runs: int = 0
    wickets: int = 0

    @property
    def economy(self) -> float:
        return round(self.runs / self.overs, 2) if self.overs else 0.0


@dataclass
class StyleSplit:
    """Spin against pace in ONE phase, attributed over by over.

    Per-phase economy and wickets for each style. The ECONOMIES are the real output here.

    **The over SHARES are not a cricket finding and must not be presented as one, which was
    measured rather than assumed.** Real T20 puts pace on the new ball and at the death and
    spin through the middle; this engine cannot reproduce that, because `choose_bowler`
    hands the next over to whoever has bowled least and knows nothing about phase or style
    -- a deliberate omission its own docstring already records ("a real captain saves their
    best for the death; this one does not"). Measured on a real 74-fixture season: pace took
    51.0% of powerplay overs, 58.5% of middle and 53.5% of death, every phase within four
    points of its 55.1% overall share, and individual bowlers turned up evenly in both the
    powerplay and the death (Maharoof 14 overs in each). So a phase share here measures the
    STYLE MIX OF THE FIVE-MAN ATTACK, not a captain's policy. Recorded here because the
    obvious reading of this table is the wrong one, and the first draft of the UI copy for
    it asserted exactly the pattern the engine does not produce.

    `unknown` is its own bucket and is NEVER folded into either style (A23). It is NOT
    empty: 97 people bowled in the archive but never reached SPEC 6.3's 30-legal-ball
    threshold, so `people.bowling_style` does not know them and never will -- 109 of 2,826
    overs in the measured season, about 3.9%.

    The bucket is reported even when empty, for the same reason A31 stores its five
    never-observed states as explicit zeroes: an absent row and a zero row say different
    things, and silently counting an unknown as the majority style is precisely the error
    internal checks cannot catch.
    """

    overs: int = 0
    runs: int = 0
    wickets: int = 0

    @property
    def economy(self) -> float:
        return round(self.runs / self.overs, 2) if self.overs else 0.0

    @property
    def balls_per_wicket(self) -> float:
        return round(self.overs * BALLS_PER_OVER / self.wickets, 1) if self.wickets else 0.0


# The bucket names. `unknown` is a first-class member rather than an afterthought, so
# every consumer that iterates STYLES gets it for free and cannot quietly omit it.
STYLES = ("pace", "spin", "unknown")
STYLE_LABEL = {"pace": "Pace", "spin": "Spin", "unknown": "Unknown"}


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
    # [A113] phase -> style -> StyleSplit. Every phase carries all three of STYLES, so a
    # reader never has to distinguish "no unknown overs" from "the unknown bucket was
    # omitted" -- the same reason A31 stores its five never-observed states as explicit
    # zeroes rather than absent rows.
    style_phases: dict = field(default_factory=dict)


def _blank_phases() -> dict:
    return {p: PhaseSplit() for p in PHASES}


def _blank_style_phases() -> dict:
    return {p: {s: StyleSplit() for s in STYLES} for p in PHASES}


def style_of(innings, snap) -> str:
    """Which STYLES bucket this over belongs to.

    Looked up from `Innings.bowling` rather than read off a field on `OverSnapshot`: the
    innings already holds every `Player` who bowled, so copying the style onto each of the
    twenty snapshots would be a second copy of one fact with nothing keeping them in step
    (A19). Keyed on `person_id`, falling back to the name only when a snapshot carries no
    id -- the same precedence the per-bowler phase attribution above already uses, and for
    the same reason: never key a player on a name string when an id is available.

    Anything not exactly 'pace' or 'spin' -- None, or a value a future archive invents --
    lands in 'unknown'. Deliberately a whitelist, not `or 'unknown'`: a new style string
    should show up as unknown and be noticed, not be silently accepted as a third real
    bucket nor coerced into one of the two.
    """
    by_id, by_name = {}, {}
    for bc in innings.bowling:
        by_id[bc.player.person_id] = bc.player.bowling_style
        by_name[bc.player.name] = bc.player.bowling_style
    style = by_id.get(snap.bowler_id) if snap.bowler_id else None
    if style is None:
        style = by_name.get(snap.bowler)
    return style if style in ("pace", "spin") else "unknown"


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
                       manhattan=_blank_manhattan(), your_manhattan=_blank_manhattan(),
                       style_phases=_blank_style_phases())

    # [A111] Keyed on (person_id, side identity), never on the name and never on the
    # person alone. Measured on a real season: **16 of 100 people turned out for MORE THAN
    # ONE side**, because the nine historical opponents are independent franchise-season
    # draws (A63) and a career spans franchises -- so summing across sides credits a player
    # with a total no single team produced. Exactly the fault A96 fixed for the Orange and
    # Purple Caps; this file reproduced it because it keyed on `b.player.name`.
    #
    # `id(side)` because `Side` is a plain dataclass with no `eq=False` and is not safely
    # hashable by value -- the same identity care A79/A80/A96 each needed.
    bat: dict[tuple, list] = {}    # (person, side) -> [runs, balls, outs, name, team]
    bowl: dict[tuple, list] = {}   # (person, side) -> [wickets, balls, runs, name, team]
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
        # (innings, the side BATTING, the side BOWLING). The third element matters: a
        # bowler in `inn.bowling` and every `OverSnapshot` in `inn.over_log` belongs to
        # the side that did NOT bat this innings. Keying or labelling them by the batting
        # side attributes a bowler to his OPPONENT -- which changes every match, so his
        # season splits into per-match fragments. Caught by measuring: the maximum death
        # overs for any (bowler, side) pair came out at exactly 4, one match's allocation.
        pairs = ((r.home_innings, r.home, r.away), (r.away_innings, r.away, r.home))
        if not all(i for i, _, _ in pairs):
            continue
        a.fixtures += 1
        for inn, side, fielding in pairs:
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
                key = (snap.bowler_id or snap.bowler, id(fielding), phase_of(snap.over))
                pb = phase_bowl.get(key)
                if pb is None:
                    pb = phase_bowl[key] = PhaseBowler(name=snap.bowler,
                                                       person_id=snap.bowler_id,
                                                       team=fielding.short)
                pb.overs += 1
                pb.runs += snap.over_runs
                pb.wickets += snap.over_wickets

                # [A113] Spin against pace, in the same pass and off the same snapshot, so
                # the style totals cannot drift from the per-bowler ones above -- both are
                # driven by one iteration over one over_log.
                ss = a.style_phases[phase_of(snap.over)][style_of(inn, snap)]
                ss.overs += 1
                ss.runs += snap.over_runs
                ss.wickets += snap.over_wickets
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
                d = bat.setdefault((b.player.person_id or b.player.name, id(side)),
                                   [0, 0, 0, b.player.name, side.short])
                d[0] += b.runs
                d[1] += b.balls
                d[2] += 1 if b.out else 0
            for b in inn.bowling:
                d = bowl.setdefault((b.player.person_id or b.player.name, id(fielding)),
                                    [0, 0, 0, b.player.name, fielding.short])
                d[0] += b.wickets
                d[1] += b.balls
                d[2] += b.runs

    a.best_over, a.highest_innings = best_over, highest

    a.top_scorers = [Leader(v[3], v[0], f"{v[1]} balls", v[4])
                     for _, v in sorted(bat.items(), key=lambda kv: -kv[1][0])[:10]]
    a.top_wickets = [Leader(v[3], v[0], f"{v[1] // BALLS_PER_OVER} overs", v[4])
                     for _, v in sorted(bowl.items(), key=lambda kv: -kv[1][0])[:10]]

    # Rate leaders need a volume floor or they are won by whoever bowled two overs -- A33's
    # reasoning at one-tournament scale. The numbers are not picked round: they are half a
    # league campaign (7 of 14 matches) at A59's own declared reference exposures, 18 balls
    # faced and 24 bowled per match. An earlier pass used 90/120 and it showed -- the best
    # strike rate came back off 94 balls, under seven a match, which is a cameo rather than
    # a season and reads as one.
    MIN_BALLS_BOWLED = 24 * 7      # 28 overs
    MIN_BALLS_FACED = 18 * 7
    econ = [(v[3], round(v[2] / (v[1] / BALLS_PER_OVER), 2), v[1], v[4])
            for v in bowl.values() if v[1] >= MIN_BALLS_BOWLED]
    a.best_economy = [Leader(n, e, f"{b // BALLS_PER_OVER} overs", tm)
                      for n, e, b, tm in sorted(econ, key=lambda t: t[1])[:10]]
    sr = [(v[3], round(100 * v[0] / v[1], 1), v[1], v[4])
          for v in bat.values() if v[1] >= MIN_BALLS_FACED]
    a.best_strike = [Leader(n, s, f"{b} balls", tm)
                     for n, s, b, tm in sorted(sr, key=lambda t: -t[1])[:10]]

    # [A110] Batting AVERAGE -- runs per dismissal, which is a different claim from strike
    # rate and the one that separates an anchor from a slogger. Same volume floor, plus a
    # dismissal floor: an average off one dismissal is a single score, not an average.
    MIN_DISMISSALS = 5
    avg = [(v[3], round(v[0] / v[2], 1), v[2], v[4])
           for v in bat.values() if v[1] >= MIN_BALLS_FACED and v[2] >= MIN_DISMISSALS]
    a.best_averages = [Leader(n, x, f"{o} outs", tm)
                       for n, x, o, tm in sorted(avg, key=lambda t: -t[1])[:10]]

    a.positions = positions

    # A phase economy needs enough overs IN THAT PHASE to mean anything. Four overs at the
    # death is one match's full allocation; the floor is a season's worth of the job, so a
    # bowler who bowled two good death overs all tournament does not top the list.
    MIN_PHASE_OVERS = 12
    def _phase_list(phase: str) -> list:
        rows = [pb for (_, _, ph), pb in phase_bowl.items()
                if ph == phase and pb.overs >= MIN_PHASE_OVERS]
        return sorted(rows, key=lambda pb: (pb.economy, -pb.wickets))[:10]

    a.death_bowlers = _phase_list("death")
    a.powerplay_bowlers = _phase_list("powerplay")
    return a
