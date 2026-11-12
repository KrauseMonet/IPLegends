"""What you work on in Build a Career: a FOCUS chosen each pre-season, and the coach's
CHALLENGE that goes with it (A285).

A focus names one skill you are working toward and the lever that builds it -- a drill
(A281), phase work (A248) or your temper (A245) -- all of which already exist. What is new is
that YOU choose it, every pre-season, and that how much the season builds depends on a
challenge counted from your own matches: meet it and the season builds a full step, miss it
and it builds half. The work shows from the next season. The skill itself is still earned by
a season that plays it (A278): a focus never hands out a badge.

Pure: nothing here plays a match or reads the deck. `Counter` is a `ball_log` callback
(A278's per-ball record, which draws nothing) that keeps your figures match by match; the
season loop tells it when one of your matches starts. Every threshold here is declared game
design, and each challenge's target count is MEASURED (`tools.career_focus_targets`) so a
season on that focus meets it about half the time.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

FOCUS_ON = True                      # tests of rules written before A285 turn it off
KEEP_LOG = False                     # the target tool keeps each season's matches to count them
FIRST_SEASON = 0                     # a focus is asked from the debut season on
SWITCH_FACTOR = 0.5                  # the first season on a new focus builds half as much
MISSED_STEP = 0.5                    # a missed challenge still builds half a step
DEATH_OVER = 15                      # overs 16-20, 0-based (SPEC 4.8/A5)
PP_OVERS = 6


@dataclass(frozen=True)
class Focus:
    key: str
    label: str                       # the menu's words
    skill: str                       # the skill it works toward (etl.skills key)
    lever: tuple                     # ("drill", key) | ("phase", disc, idx) | ("temper", sign)
    disc: str                        # which discipline it belongs to
    challenge: str                   # what a match must hold, in words
    target: int                      # matches a season needs (measured)
    blurb: str                       # what the work does, in words, never numbers
    power: float = 1.0               # steps of the lever a met season builds (tuned, A285)
    reach: float = 1.0               # how far its lever may go, as a multiple of the usual cap


# A match's figures for you, all counted from the per-ball record: off the bat, wides and
# no-ball runs not included (`ball_log` is every ball faced).
FIELDS = ("faced", "runs", "out", "spin_runs", "pace_runs", "pp_runs", "death_runs",
          "bowled", "conceded", "wkts", "pp_bowled", "pp_conceded", "death_bowled",
          "death_conceded", "death_wkts")


def _met(f: str, m: dict) -> bool:
    """Whether one match meets focus `f`'s challenge."""
    if f == "spin":
        return m["spin_runs"] >= 25
    if f == "pace":
        return m["pace_runs"] >= 30
    if f == "burn":
        return m["faced"] >= 30
    if f == "anchor":
        return m["faced"] >= 15 and not m["out"]
    if f == "aggressor":
        return m["faced"] >= 12 and m["runs"] >= 1.75 * m["faced"]
    if f == "pp_bat":
        return m["pp_runs"] >= 25
    if f == "death_bat":
        return m["death_runs"] >= 20
    if f == "yorker":
        return m["death_wkts"] >= 1
    if f == "new_ball":
        return m["pp_bowled"] >= 12 and m["pp_conceded"] * 6 < 6 * m["pp_bowled"]
    if f == "death_ball":
        return m["death_bowled"] >= 12 and m["death_conceded"] * 6 < 9 * m["death_bowled"]
    if f == "strike":
        return m["wkts"] >= 3
    if f == "miser":
        return m["bowled"] >= 24 and m["conceded"] * 6 < 7 * m["bowled"]
    raise KeyError(f)


# The targets are MEASURED (`tools.career_focus_targets`, 16 automatic careers a role, focus
# off): the count of matches whose share of regular seasons reaching it is nearest a half --
# spin 45%, pace 52%, burn 45%, anchor 42%, aggressor 46%, pp_bat 50%, death_bat 49%, yorker
# ~50% (median 5), miser 55%, strike 59%. new_ball and death_ball were measured where the side
# does not bowl you there, and a phase focus is bowled there, so their 3 is checked in play.
# Tuned after the acceptance run (8 careers a role and focus): four foci barely moved their
# skill. Building faster (`power` 2) alone helped only `strike` (temper; 11% -> 15% of skills
# shown) -- the drill and phase levers simply hit their caps sooner. Letting the lever go twice
# as far (`reach` 2) is what moved `new_ball` (spin 4% -> 12%, all-rounder 5% -> 10%) and
# `yorker` (34% -> 39%). `aggressor` moved under no setting: it is already the commonest
# skill, so it is left alone. A pace bowler's New-ball specialist stays out of reach (elite).
FOCI: tuple[Focus, ...] = (
    Focus("spin", "Take on the spinners", "spin_basher", ("drill", "spin"), "bat",
          "25+ runs off the spinners", 2,
          "Better against spin, a little less so against pace."),
    Focus("pace", "Take on the quicks", "pace_basher", ("drill", "pace"), "bat",
          "30+ runs off the quicks", 2,
          "Better against pace, a little less so against spin."),
    Focus("burn", "Learn to bat time", "slow_burner", ("drill", "burn"), "bat",
          "face 30+ balls in an innings", 3,
          "Slower to start, faster once set."),
    Focus("anchor", "Value your wicket", "anchor", ("temper", -1), "bat",
          "not out after facing 15+ balls", 2,
          "Patience: out less often, scoring a little slower."),
    Focus("aggressor", "Play for the runs", "aggressor", ("temper", 1), "bat",
          "a strike rate of 175+ off 12+ balls", 2,
          "Aggression: scoring faster, out a little more often."),
    Focus("pp_bat", "Own the powerplay", "pp_specialist", ("phase", "bat", 0), "bat",
          "25+ runs in the powerplay", 4,
          "Sharper in the first six overs, a little less so after."),
    Focus("death_bat", "Finish innings", "death_hitter", ("phase", "bat", 2), "bat",
          "20+ runs in overs 16-20", 2,
          "Sharper in the last five overs, a little less so before."),
    Focus("yorker", "Yorkers at the death", "death_wickets", ("drill", "death_wkts"), "bowl",
          "a wicket in overs 16-20", 5,
          "More wickets at the death, a few fewer with the new ball.", power=2, reach=2),
    Focus("new_ball", "Take the new ball", "new_ball", ("phase", "bowl", 0), "bowl",
          "2+ powerplay overs at under 6 an over", 3,
          "Tighter in the powerplay, a little less so later -- and the side bowls you there.",
          power=2, reach=2),
    Focus("death_ball", "Own the death overs", "death_bowler", ("phase", "bowl", 2), "bowl",
          "2+ overs at the death at under 9 an over", 3,
          "Tighter at the death, a little less so earlier -- and the side bowls you there."),
    Focus("strike", "Bowl to take wickets", "strike_bowler", ("temper", 1), "bowl",
          "3+ wickets in a match", 2,
          "Attack: more wickets, a few more runs conceded.", power=2),
    Focus("miser", "Bowl dry", "miser", ("temper", -1), "bowl",
          "4 overs at under 7 an over", 3,
          "Control: fewer runs conceded, a few fewer wickets."),
)
FOCUS_BY_KEY = {f.key: f for f in FOCI}


def available(kind: str, band: str, bowls: bool, style: str | None) -> list[str]:
    """The foci that fit you, menu order. `kind` is batter / bowler / allrounder (the card's
    role), `band` top / middle / finisher, `bowls` whether you bowl at all, `style` pace /
    spin. A pure bowler's temper is a bowling focus; a batter's, or an all-rounder's (one
    temper for both disciplines), a batting one, so the two never pull against each other."""
    out: list[str] = []
    if kind != "bowler":
        out += ["spin", "pace", "burn", "anchor", "aggressor"]
        out.append("pp_bat" if band == "top" else "death_bat")
    if bowls and kind != "batter":
        if style == "pace":
            out += ["yorker", "death_ball"]
        out.append("new_ball")
        if kind == "bowler":
            out += ["strike", "miser"]
    return out


def built_out(key: str, drills: dict, phase: dict, traits: dict, drill_max: float,
              phase_max: float, trait_max: float) -> bool:
    """Whether a focus's lever is already built as far as it goes, so working on it adds
    nothing (the menu says so rather than offering it)."""
    f = FOCUS_BY_KEY[key]
    lever = f.lever
    if lever[0] == "drill":
        return drills.get(lever[1], 0.0) >= drill_max * f.reach
    if lever[0] == "phase":
        return (phase.get(lever[1]) or (0.0, 0.0, 0.0))[lever[2]] >= phase_max * f.reach - 1e-9
    return lever[1] * traits.get("temper", 0.0) >= trait_max - 1e-9


def step(met: bool, switched: bool) -> float:
    """How far a season builds its focus: a step met, half missed, halved again on the first
    season of a new focus."""
    s = 1.0 if met else MISSED_STEP
    return s * (SWITCH_FACTOR if switched else 1.0)


# --- counting your matches -----------------------------------------------------------------

class Counter:
    """A `ball_log` callback keeping one person's figures match by match. The season loop
    calls `next_match()` before each match your side plays, so `matches[i]` is your i-th
    match in the order the feed shows them, played or not."""

    def __init__(self, who: str):
        self.who = who
        self.matches: list[dict] = []

    def next_match(self) -> None:
        self.matches.append(dict.fromkeys(FIELDS, 0))

    def __call__(self, over, wickets, striker, bowler, outcome, par) -> None:
        if not self.matches:
            return
        m = self.matches[-1]
        from game.simulator import OFF_THE_BAT
        runs = 0 if outcome == 0 else OFF_THE_BAT[outcome - 1]
        out = outcome == 0
        if striker.player.person_id == self.who:
            m["faced"] += 1
            m["runs"] += runs
            m["out"] = m["out"] or out
            style = bowler.player.bowling_style
            if style == "spin":
                m["spin_runs"] += runs
            elif style == "pace":
                m["pace_runs"] += runs
            if over < PP_OVERS:
                m["pp_runs"] += runs
            elif over >= DEATH_OVER:
                m["death_runs"] += runs
        if bowler.player.person_id == self.who:
            m["bowled"] += 1
            m["conceded"] += runs
            m["wkts"] += out
            if over < PP_OVERS:
                m["pp_bowled"] += 1
                m["pp_conceded"] += runs
            elif over >= DEATH_OVER:
                m["death_bowled"] += 1
                m["death_conceded"] += runs
                m["death_wkts"] += out


def chain(*fns):
    """One `ball_log` callback calling several."""
    def call(*a):
        for f in fns:
            f(*a)
    return call


def progress(key: str, matches: list[dict]) -> list[bool]:
    """For each of your matches, whether it met the focus's challenge."""
    return [_met(key, m) for m in matches]


@dataclass
class Result:
    focus: str
    met: int                         # matches that met the challenge
    target: int
    done: bool
    built: float                     # steps of the lever the season built
    switched: bool
    hits: list[bool] = field(default_factory=list)   # per match, as the feed shows them


def judge(key: str, matches: list[dict], switched: bool) -> Result:
    f = FOCUS_BY_KEY[key]
    hits = progress(key, matches)
    met = sum(hits)
    done = met >= f.target
    return Result(key, met, f.target, done, step(done, switched) * f.power, switched, hits)


def tally(counters: list[list[dict]]) -> dict[str, list[int]]:
    """For the target tool: every focus's matches met, one entry per season."""
    out: dict[str, list[int]] = defaultdict(list)
    for matches in counters:
        for f in FOCI:
            out[f.key].append(sum(progress(f.key, matches)))
    return out
