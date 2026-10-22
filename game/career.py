"""Build a Career -- one player, real IPL seasons, from debut to retirement. [A197]

You create a player (name, role, Indian or overseas, debut year), two spins set how good
he starts and how good he can become, and then each season you pick a franchise from that
year's offers, answer one event card, and the year is played: that year's REAL teams, in
the real number (8, 9 or 10), a league and the IPL's own playoffs, with you in your
franchise's real squad. At the end there is a career card.

**Ability is a smooth, declared curve; the swings come from the matches.** Measured before
building (A197): holding a long-career batter at one fixed ability and simulating thirty
seasons reproduces about 75% of his real season-to-season variance in runs per innings,
and a career curve measured from the archive is flat (ratings are compared within each
season, and players who decline drop out). So the engine already supplies the ups and
downs, and adding real players' rating swings on top would count luck twice. The curve
(`ARC`) is game design, in the same category as `REPUTATION`.

**Every number your player plays with is a real player's.** One 70-99 rating cannot drive
the engine (measured: R2 0.03-0.43 against the engine's two numbers per role), so your
ability is not made up from a rating. Two real seasons of your role are drawn -- where you
START and your CEILING -- and each season's ability is a declared mix of their engine
numbers (`ability`), scoring halves linearly and dismissal multipliers geometrically.

**Your season card rates what you actually did.** Your season is scored against the other
regulars in the SAME simulated season with the card's own statistics weights (A163), and
the percentile is read off the real cards of that year and role (`season_display`). So a
91 means "as good, relative to that year, as the real 91s of that year", and a dip after a
91 is form, which you can see, rather than a hidden decline.

Pure: a function of the deck, the model, a seed, the setup and the moves -- no database.
Each season draws from its own random stream (`{seed}:season:{k}`), so a season's result
depends only on its own inputs, never on how much randomness earlier seasons used (A93's
lesson).
"""

from __future__ import annotations

import copy
import dataclasses
from collections import defaultdict
import math
import random
import statistics
from dataclasses import dataclass, field

from etl.feasibility import Card, Deck, team_rating
from etl.franchise_map import canonical
from game.analysis import SeasonAnalysis, batting_rank, bowling_rank, season_analysis
from game.auction import value_curve
from game.season import (
    DOUBLE_AT, Result, Side, _abbrev, balanced_order, fixtures, run_league, run_playoffs,
)
from game.simulator import Model

# --- who you can be ------------------------------------------------------------------

YOU_ID = "career-you"                # your person_id; no real person id has a hyphenated word
FIRST_DEBUT = 2008
LAST_DEBUT = 2017                    # so every career can run at least ten seasons
LAST_YEAR = 2026
MAX_SEASONS = 19                     # a 2008 debut can run to 2026, as Kohli and Rohit did (A198)

# What each creation choice means, as a filter on real cards. The role and band are the
# archive's own (A26, A76); a template carries his band, batting positions and bowling style
# into your player, so an "opener" bats where real openers bat.
ROLES: dict[str, dict] = {
    "opener":     {"label": "Opening batter", "role": ("batter",), "band": ("opener",)},
    "middle":     {"label": "Middle-order batter", "role": ("batter",),
                   "band": ("top_order", "middle")},
    # A finisher's templates are anybody who batted regularly in the finisher band, his
    # BATTING only (A198): only 22 real cards in nineteen years are batters there, too few
    # to spin from, and real finishers are mostly all-rounders. Your finisher still does not
    # bowl -- `ability` drops the bowling of a batting-only role.
    # [A198] A finisher only loses his place in the bottom 15% of his band, not 30%: ratified
    # by the user as a declared exception, because at the general bar 88-94% of finisher
    # careers ended in release -- realistic (real batting-only finishers rarely held a
    # place), but it made the role unplayable.
    "finisher":   {"label": "Finisher", "role": ("batter", "keeper", "allrounder"),
                   "band": ("finisher",), "batting_only": True, "poor_percentile": 0.15},
    "keeper":     {"label": "Wicketkeeper-batter", "role": ("keeper",)},
    "pace":       {"label": "Fast bowler", "role": ("bowler",), "style": "pace"},
    "spin":       {"label": "Spinner", "role": ("bowler",), "style": "spin"},
    "allrounder": {"label": "All-rounder", "role": ("allrounder",)},
}

# A33's floors: below them a season's numbers are noise, so it cannot be a template and
# does not count as a "regular" when reading a real year's card distribution.
BAT_REGULAR_BALLS = 100
BOWL_REGULAR_BALLS = 150

# The two spins. Declared game design, tuned by phase 2's calibration rather than fitted:
# a start is an ordinary regular's season, a ceiling anything from good to the best ever.
# Drawn uniformly over the real seasons in range, so how rare a 99 ceiling is comes from
# how rare 99s really are.
START_DISPLAY = (72, 80)
CEILING_MIN_DISPLAY = 82
CEILING_REDRAWS = 50

# The share of the way from START to CEILING, by career season. Declared game design (see
# the module docstring for why it is not measured): rise for five seasons, a peak, then a
# decline that keeps going. There is no cap on a career but 2026 [A198]: every real batter
# in the all-time top five played 13 to 19 seasons, so a twelve-season cap made a batting
# legend impossible. Past season twelve ability falls below where you started, and form
# (POOR_PERCENTILE) ends the career, as it ends real ones.
ARC = (0.0, 0.3, 0.55, 0.75, 0.9, 1.0, 1.0, 0.95, 0.85, 0.7, 0.5, 0.3,
       0.1, -0.1, -0.3, -0.5, -0.5, -0.5, -0.5)
ABILITY_FLOOR = -0.5                 # an injury can drop you below where you started
ABILITY_CAP = 1.0                    # nothing lifts you above your ceiling

# [A198] How much of the team's season you play, drawn per season from the real
# distribution: the share of their team's matches that players with ten or more seasons
# played in seasons they were REGULARS (A33's floors), 838 seasons, 20 quantiles (5%, 10%,
# ... 100%). Real regulars play every match in 42% of seasons and 86% of matches on average.
# Regular seasons, not all seasons, because your player is always selected: a real long
# career's fringe years come from not being good enough, which the ability curve decides,
# not this. The missed matches are one block of the league (an injury), never the playoffs.
AVAILABILITY_SHARES = (0.25, 0.571, 0.643, 0.706, 0.75, 0.786, 0.812, 0.857, 0.875, 0.929,
                       0.933, 0.938, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)

# [A198] Form decides whether you keep your place, ratified by the user: real careers are
# rare because players lose it (real bowlers with ten or more seasons were regulars in a
# median 8 of 12), and a player who is a regular every year is, realistically, near the top
# of the all-time lists -- every real bowler who was a regular for twelve seasons is in the
# all-time top five for wickets. A season in the bottom POOR_PERCENTILE of your kind of
# player that season makes the next a SQUAD season; a second in a row and nobody signs you.
# Relative rather than a card number, because card levels differ by role: one threshold
# released 71% of middle-order batters and 33% of all-rounders. POOR_PERCENTILE is set by
# the calibration (tools/career_calibration.py) so careers last as long as real careers
# that began as regulars.
POOR_PERCENTILE = 0.30

# How much of the season a squad player plays: the share of their team's matches that
# players with ten or more seasons played in seasons they were NOT regulars (277 seasons,
# 20 quantiles). A squad player sits out the playoffs.
SQUAD_SHARES = (0.053, 0.062, 0.071, 0.111, 0.133, 0.176, 0.188, 0.214, 0.222, 0.25, 0.286,
                0.316, 0.357, 0.375, 0.429, 0.429, 0.5, 0.533, 0.615, 0.714)

# --- key moments (A204) ----------------------------------------------------------------
# A few matches a season stop and ask how you will play them. The approach moves your two
# engine numbers for that match only, in opposite directions, at an exchange rate MEASURED
# in the engine's own state model: at the same wickets down, going from middle overs to
# death overs -- a side deciding to attack -- costs +0.96 to +1.41 in log dismissal rate per
# extra run a ball (about 1.2 where most balls are faced). Season-to-season comparisons
# could not price it: within a player, better seasons score faster AND get out less, which
# is form, not intent. ATTACK_SHIFT is declared game design (about one and a half times the
# within-player season spread in scoring).

ATTACK_SHIFT = 0.15                  # runs a ball, about 15 on a strike rate
TRADE_SLOPE = 1.25                   # d ln(dismissal) per run a ball, measured
MUST_WIN_WINDOW = 3                  # league matches left, this one included
MUST_WIN_EDGE = 2                    # points either side of the qualification line
MUST_WIN_MAX = 1                     # league moments a season; every playoff is one

APPROACH_LABELS = {
    "batter": ("Anchor", "Balanced", "Attack"),
    "bowler": ("Contain", "Balanced", "Attack the stumps"),
    "allrounder": ("Play it safe", "Balanced", "All out"),
}


def approach_card(me: Card, choice: int) -> Card:
    """Your card for one match, played anchored (0), balanced (1) or attacking (2): scoring
    up and dismissals up together, by TRADE_SLOPE. A bowler who attacks takes more wickets
    and concedes more runs -- his runs SAVED fall."""
    d = (choice - 1) * ATTACK_SHIFT
    if d == 0:
        return me
    mult = math.exp(TRADE_SLOPE * d)
    return dataclasses.replace(
        me,
        bat_scoring=None if me.bat_scoring is None else me.bat_scoring + d,
        bat_dismissal=None if me.bat_dismissal is None else me.bat_dismissal * mult,
        bowl_scoring=None if me.bowl_scoring is None else me.bowl_scoring - d,
        bowl_dismissal=None if me.bowl_dismissal is None else me.bowl_dismissal * mult)


PLAYOFF_STAKES = {
    "Qualifier 1": "Win and you are in the final. Lose and you get a second chance in Qualifier 2.",
    "Eliminator": "Lose and your season is over.",
    "Qualifier 2": "Win and you are in the final. Lose and your season is over.",
    "Final": "The final.",
}


def league_stakes(season, yours: Side, left: int) -> str | None:
    """Why a league match matters, or None. A must-win: few matches left and your side
    within MUST_WIN_EDGE points of the qualification line, read from the table as it stands
    before the match (a career plays its league matchday by matchday, so it is a real one)."""
    from game.season import Standing, credit_result, rank_table

    if left > MUST_WIN_WINDOW:
        return None
    standings = {s.name: Standing(side=s) for s in season.sides}
    for r in season.results:
        credit_result(standings, r)
    table = rank_table(standings)
    pos = next(i for i, s in enumerate(table, 1) if s.side is yours)
    mine = table[pos - 1].points
    line = table[4].points if pos <= 4 else table[3].points
    if abs(mine - line) > MUST_WIN_EDGE:
        return None
    games = f"{left} match{'es' if left != 1 else ''} to play"
    if pos <= 4:
        gap = "level with fifth" if mine == line else f"{mine - line} clear of fifth"
        return (f"You are {_ordinal(pos)} on {mine} points, {gap}, with {games}. Win and the "
                f"top four is close; lose and it is out of your hands.")
    gap = "level with fourth" if mine == line else f"{line - mine} behind fourth"
    return (f"You are {_ordinal(pos)} on {mine} points, {gap}, with {games}. "
            f"This one is must-win.")


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


# [A205] Form, and being dropped for it. Measured over 1,707 real regular seasons: a player
# whose LAST THREE matches sit in the worst third of his kind misses the next two 3-4 times
# as often as one in the best third (batters 5.4% against 1.6%, bowlers 7.6% against 2.4%),
# while a bad FIRST HALF barely moves the second (29% against 22% missing two or more). So
# form is read off the last three matches, ranked against everybody of your kind in the same
# simulated season. A form score is declared, not fitted: a batter's runs; a bowler's runs
# saved against par (FORM_PAR_PER_BALL a ball) plus FORM_WICKET_RUNS a wicket; an all-rounder
# both. The most common real stretch out of the side, past a single match, is two matches.
# A batter is ranked among batters of his band, as the season card ranks him (A198): ranked
# with openers, every middle-order batter read out of form (31% of seasons dropped, against
# an opener's 17%). An all-rounder gets the form line but never a make-or-break: for real
# all-rounders recent form barely predicts a drop (4.0% against 2.7%).
FORM_MATCHES = 3                     # matches a form reading looks back over
FORM_WICKET_RUNS = 20                # a wicket in a form score, in runs
FORM_PAR_PER_BALL = 1.3              # runs a ball conceded at par
FORM_POOR = 1 / 3                    # the worst third of your kind is out of form
FORM_GOOD = 2 / 3                    # the best third is in form
# A make-or-break is failed when that match alone sits in the worst sixth of your kind's
# matches (A207, swept against the real drop rates of FORM_DROP_RATE): at the worst third
# careers were dropped 0.243 / 0.252 times a regular season (batting / bowling), at the
# worst sixth 0.140 / 0.126 against a real 0.113 / 0.153 -- 0.136 against 0.131 weighted.
FORM_FAIL = 1 / 6
FORM_SHOWN = 5                       # matches on the live form line
DROP_MATCHES = 2                     # matches a failed make-or-break costs you
# Real form drops a regular season, as two-match drops: worst-third readings a season (3.03
# batting, 2.89 bowling) times how much likelier they make the next two missed than a best
# third does (+3.7 and +5.3 points). The missed block's shares were measured from real
# seasons, so they already hold these: a droppable player's block is drawn this much
# shorter, and his own drops are then added to it (A205).
FORM_DROP_RATE = {"batter": 0.113, "bowler": 0.153}
_WORDS = {2: "two", 3: "three", 4: "four"}
MAKE_OR_BREAK_MAX = 1                # a season


class FormBook:
    """Every player's matches this season, in the order played, for reading form. A match
    counts for a player when he is in his side's eleven (its batting card lists all eleven).
    Keyed on (person, side): a person can appear for two sides in a deck year (A96)."""

    def __init__(self, roster: dict[int, dict[str, Card]]):
        self.roster = roster
        # (runs, wickets, runs conceded, balls bowled, out: True/False, None did not bat)
        self.games: dict[tuple[str, int], list[tuple]] = defaultdict(list)
        self.seen = 0

    def kind(self, key: tuple[str, int]) -> str | None:
        card = self.roster.get(key[1], {}).get(key[0])
        return _kind(card) if card is not None else None

    def add(self, results: list[Result]) -> None:
        """Take in any results not yet read (the list only grows)."""
        for r in results[self.seen:]:
            for side, inn, other in ((r.home, r.home_innings, r.away_innings),
                                     (r.away, r.away_innings, r.home_innings)):
                bowled = {w.player.person_id: w for w in (other.bowling if other else [])}
                for b in (inn.batting if inn else []):
                    w = bowled.get(b.player.person_id)
                    batted, bowled_any = b.faced_any or b.out, bool(w and w.balls)
                    key = (b.player.person_id, id(side))
                    kind = self.kind(key)
                    # Not asked to do your job is not a failure: a batter who did not bat, a
                    # bowler who did not bowl, anybody who did neither.
                    if (not batted and not bowled_any or kind == "batter" and not batted
                            or kind == "bowler" and not bowled_any):
                        continue
                    self.games[key].append(
                        (b.runs, w.wickets if w else 0, w.runs if w else 0,
                         w.balls if w else 0, b.out if batted else None))
        self.seen = len(results)

    @staticmethod
    def score(kind: str, game: tuple[int, int, int, int]) -> float:
        runs, wickets, conceded, balls = game[:4]
        bowling = FORM_WICKET_RUNS * wickets - conceded + FORM_PAR_PER_BALL * balls
        return runs if kind == "batter" else bowling if kind == "bowler" else runs + bowling

    def _rank(self, mine: float, pool: list[float]) -> float:
        """Your place among `pool` (yours included): 0 the worst, 1 the best."""
        below = sum(v < mine for v in pool)
        return below / max(1, len(pool) - 1)

    def _peers(self, key: tuple[str, int], kind: str, ready) -> list[tuple[str, int]]:
        """Everyone of your kind passing `ready`; a batter's narrowed to his band when the
        band holds enough of them (the season card's rule, A198)."""
        peers = [k for k, gs in self.games.items() if self.kind(k) == kind and ready(gs)]
        if kind == "batter":
            group = BAND_GROUP.get(self.roster[key[1]][key[0]].band)
            band = [k for k in peers if k == key or
                    BAND_GROUP.get(self.roster[k[1]][k[0]].band) == group]
            if len(band) - 1 >= MIN_BAND_PEERS:
                return band
        return peers

    def form_rank(self, key: tuple[str, int]) -> float | None:
        """Your last FORM_MATCHES against the last FORM_MATCHES of everyone of your kind who
        has played that many; None before you have."""
        kind, games = self.kind(key), self.games.get(key, [])
        if kind is None or len(games) < FORM_MATCHES:
            return None
        pool = [sum(self.score(kind, g) for g in self.games[k][-FORM_MATCHES:])
                for k in self._peers(key, kind, lambda gs: len(gs) >= FORM_MATCHES)]
        return self._rank(sum(self.score(kind, g) for g in games[-FORM_MATCHES:]), pool)

    def last_match_rank(self, key: tuple[str, int]) -> float:
        """Your latest match against every match played by anyone of your kind so far."""
        kind = self.kind(key)
        pool = [self.score(kind, g) for k in self._peers(key, kind, bool)
                for g in self.games[k]]
        return self._rank(self.score(kind, self.games[key][-1]), pool)


def form_word(rank: float | None) -> str | None:
    if rank is None:
        return None
    return "Out of form" if rank < FORM_POOR else "In form" if rank > FORM_GOOD else "Steady"


def form_figure(kind: str, game: tuple) -> str:
    """One match on the form line: runs for a batter (`*` not out, `-` did not bat),
    wickets/runs for a bowler, both for an all-rounder who bowled."""
    runs, wickets, conceded, balls, out = game
    bat = "-" if out is None else f"{runs}{'' if out else '*'}"
    bowl = f"{wickets}/{conceded}"
    if kind == "batter":
        return bat
    if kind == "bowler":
        return bowl if balls else "-"
    return f"{bat} & {bowl}" if balls else bat


@dataclass
class Moment:
    """A key moment the season stopped at: what is at stake and against whom."""
    stage: str
    opponent: str
    opp_short: str
    opp_franchise: str | None
    opp_year: int | None
    stakes: str
    labels: tuple[str, str, str]
    kind: str = "must_win"             # "must_win" | "playoff" | "make_or_break" (A205)


class _NeedMoment(Exception):
    def __init__(self, moment: Moment, season, yours: Side, approaches: list,
                 roster: dict | None = None, dropped: list | None = None,
                 verdicts: dict | None = None):
        self.moment, self.season, self.yours = moment, season, yours
        self.approaches = approaches
        self.roster, self.dropped, self.verdicts = roster or {}, dropped or [], verdicts or {}


# --- the market ------------------------------------------------------------------------

OFFERS = 3                           # franchises bidding each season, besides your own
DEBUT_PRICE = 20                     # lakh: an uncapped debutant's base price
PRICE_NOISE = 0.20                   # each franchise's view of you varies by +/- this
STAY_DISCOUNT = 0.9                  # staying put costs a little money
PRICE_CAP = 2700                     # lakh: the real record (Pant, 2025)

# --- event cards -----------------------------------------------------------------------
# One per season, drawn from the seed (and the setup, for who may get which card) -- never
# from your choices. Declared game design. Each option is (label, this-season ability
# change, permanent ability change, captain this season, extra league matches missed,
# retire now). An event with one option is shown, not asked.


@dataclass(frozen=True)
class EventOption:
    label: str
    season: float = 0.0
    permanent: float = 0.0
    captain: bool = False
    missed: int = 0                     # extra league matches missed this season (A200)
    retire: bool = False                # ends the career before the season is played


@dataclass(frozen=True)
class Event:
    key: str
    title: str
    text: str
    weight: int
    options: tuple[EventOption, ...]
    # [A200] "{name}" in the text is a REAL player: "mentor" the best of your own kind in
    # your franchise's real squad that year, "rival" the best player in another team that
    # year. Never a captain: captains are not in the archive.
    who: str | None = None
    # [A200] Who can draw the card: "late" only from LATE_FROM seasons on, "overseas" only
    # an overseas player. Decided by the seed and the setup, never by a choice.
    when: str | None = None


EVENTS: tuple[Event, ...] = (
    Event("quiet", "A quiet pre-season", "Nothing to report. Just cricket.", 4,
          (EventOption("Get on with it"),)),
    Event("form", "In form", "A huge domestic season. You arrive hitting the ball sweetly.", 2,
          (EventOption("Ride it", season=0.10),)),
    Event("niggle", "A hamstring niggle", "Pre-season brings a niggle. How do you handle it?", 2,
          (EventOption("Play through it", season=-0.15, permanent=-0.05),
           EventOption("Rest and rehab", season=-0.30))),
    Event("captain", "The captaincy", "The franchise asks you to lead the side.", 1,
          (EventOption("Take the armband", season=-0.05, captain=True),
           EventOption("Not yet"))),
    Event("coach", "A new coach", "A coach wants to rebuild your technique from scratch.", 2,
          (EventOption("Rebuild it", season=-0.10, permanent=0.10),
           EventOption("Stick with what works"))),
    # [A200] six more, each declared like the five above and checked by the calibration.
    Event("injury", "An injury", "A side strain in the first fortnight. The physio wants six weeks.", 1,
          (EventOption("Rush back", season=-0.10, missed=2),
           EventOption("Take the full six weeks", missed=5))),
    Event("mentor", "A mentor", "{name} offers to work with you through the season.", 1,
          (EventOption("Learn from him", season=-0.05, permanent=0.05),
           EventOption("Do it your way")), who="mentor"),
    Event("rivalry", "A rivalry", "The papers have built up your duel with {name}.", 1,
          (EventOption("Rise to it", season=0.05),), who="rival"),
    Event("slump", "Out of form", "Nothing is coming off in the nets.", 1,
          (EventOption("Work through it", season=-0.10),
           EventOption("Take a break", missed=3))),
    Event("home_league", "A league back home",
          "Your home board wants you for its own T20 league, which runs into the IPL's opening weeks.", 2,
          (EventOption("Skip it"),
           EventOption("Play there first", permanent=0.03, missed=4)), when="overseas"),
    Event("one_more", "One more season?", "You are past your best, and the franchise still wants you.", 3,
          (EventOption("One more year"),
           EventOption("Retire on a high", retire=True)), when="late"),
)

LATE_FROM = 10                       # "One more season?" from the eleventh season on


# Cards that make no sense for a man who has lost his place: in a squad season they become
# a quiet pre-season (A199). Found in the page: "Captain" beside "Squad player, 5 matches".
NOT_IN_A_SQUAD_SEASON = frozenset({"captain"})

# How often a card may come up in one career. Unlisted: no limit beyond no-repeats.
EVENT_LIMIT = {"coach": 2, "captain": 2, "niggle": 3, "injury": 2, "mentor": 2, "slump": 2}


class CareerError(ValueError):
    """A setup or a move that does not describe a playable career."""


# --- setup and templates ---------------------------------------------------------------


@dataclass(frozen=True)
class Setup:
    name: str
    role: str
    debut: int
    overseas: bool = False

    def check(self) -> None:
        if self.role not in ROLES:
            raise CareerError(f"no such role {self.role!r}")
        if not FIRST_DEBUT <= self.debut <= LAST_DEBUT:
            raise CareerError(f"debut must be {FIRST_DEBUT}-{LAST_DEBUT}")
        if not self.name.strip():
            raise CareerError("a player needs a name")


def _regular(c: Card) -> bool:
    if c.role in ("batter", "keeper"):
        return (c.bat_balls or 0) >= BAT_REGULAR_BALLS
    if c.role == "bowler":
        return (c.bowl_balls or 0) >= BOWL_REGULAR_BALLS
    return ((c.bat_balls or 0) >= BAT_REGULAR_BALLS
            or (c.bowl_balls or 0) >= BOWL_REGULAR_BALLS)


def _fits(c: Card, role: str) -> bool:
    spec = ROLES[role]
    if c.role not in spec["role"] or c.display is None or c.bat_scoring is None and c.bowl_scoring is None:
        return False
    if "band" in spec and c.band not in spec["band"]:
        return False
    if "style" in spec and c.bowling_style != spec["style"]:
        return False
    if spec.get("batting_only"):
        return c.bat_scoring is not None and (c.bat_balls or 0) >= BAT_REGULAR_BALLS
    if c.role == "allrounder" and (c.bat_scoring is None or c.bowl_scoring is None):
        return False
    return _regular(c)


def main_rating(c: Card, role: str | None = None) -> float:
    """What "better" means for a template of this role: the card's own per-ball rating in
    the discipline the role is for, both summed for an all-rounder. A batting-only role
    (`role`, the creation choice) compares batting alone, whoever the template was."""
    if c.role in ("batter", "keeper") or (role and ROLES[role].get("batting_only")):
        return c.bat if c.bat is not None else -9.9
    if c.role == "bowler":
        return c.bowl if c.bowl is not None else -9.9
    return (c.bat or 0.0) + (c.bowl or 0.0)


def template_pools(deck: Deck, role: str) -> tuple[list[Card], list[Card]]:
    """(starts, ceilings) for a role, in a fixed order so a seed always draws the same."""
    cards = sorted((c for cs in deck.cards_by_fs.values() for c in cs if _fits(c, role)),
                   key=lambda c: (c.season_year, c.fs_id, c.person_id))
    if ROLES[role].get("batting_only"):
        # A card's rating includes the bowling a batting-only role leaves behind, so the
        # spins are drawn on batting alone: a start from the middle third of the band's
        # batting, a ceiling from the top third (A198).
        by_bat = sorted(cards, key=lambda c: c.bat if c.bat is not None else -9.9)
        n = len(by_bat)
        return by_bat[n // 3:2 * n // 3], by_bat[2 * n // 3:]
    starts = [c for c in cards if START_DISPLAY[0] <= c.display <= START_DISPLAY[1]]
    ceilings = [c for c in cards if c.display >= CEILING_MIN_DISPLAY]
    return starts, ceilings


def spin(deck: Deck, seed: int, role: str) -> tuple[Card, Card]:
    """The two spins: a starting season and a ceiling that is better at the role's job.
    A ceiling no better than the start would be a career that can only decline, so the
    ceiling is redrawn until it beats the start (and, failing that, is the best there is)."""
    starts, ceilings = template_pools(deck, role)
    if not starts or not ceilings:
        raise CareerError(f"no real seasons to build a {role} from")
    rng = random.Random(f"{seed}:spin")
    start = rng.choice(starts)
    for _ in range(CEILING_REDRAWS):
        ceiling = rng.choice(ceilings)
        if main_rating(ceiling, role) > main_rating(start, role):
            return start, ceiling
    return start, max(ceilings, key=lambda c: main_rating(c, role))


# --- ability ---------------------------------------------------------------------------


def _lin(a: float | None, b: float | None, t: float) -> float | None:
    if a is None:
        return None
    return a if b is None else a + (b - a) * t


def _geo(a: float | None, b: float | None, t: float) -> float | None:
    """Multipliers mix geometrically: halfway between 0.5x and 2x is 1x, not 1.25x."""
    if a is None:
        return None
    return a if b is None else a * (b / a) ** t


def ability(start: Card, ceiling: Card, t: float, *, setup: Setup, fs_id: int,
            year: int, franchise: str | None) -> Card:
    """Your player for one season: the start template moved `t` of the way to the ceiling.
    `t = 0` is the start's engine numbers exactly and `t = 1` the ceiling's. The start
    decides which disciplines you have and where you bat; the ceiling only moves numbers
    you already have. A batting-only role keeps the batting and drops the rest."""
    if ROLES[setup.role].get("batting_only"):
        start = dataclasses.replace(start, role="batter", bowl=None, bowl_scoring=None,
                                    bowl_dismissal=None)
    return Card(
        fs_id=fs_id, person_id=YOU_ID, name=setup.name,
        bat=_lin(start.bat, ceiling.bat, t), bowl=_lin(start.bowl, ceiling.bowl, t),
        band=start.band, role=start.role, keeper_eligible=start.role == "keeper",
        season_year=year, franchise=franchise, overseas=setup.overseas,
        bowling_style=start.bowling_style, display=None, positions=start.positions,
        bat_scoring=_lin(start.bat_scoring, ceiling.bat_scoring, t),
        bat_dismissal=_geo(start.bat_dismissal, ceiling.bat_dismissal, t),
        bowl_scoring=_lin(start.bowl_scoring, ceiling.bowl_scoring, t),
        bowl_dismissal=_geo(start.bowl_dismissal, ceiling.bowl_dismissal, t),
        # [A205] A batter's bowling is part-time by definition (A26: under an over a match),
        # so it is marked thin, as a real part-timer's is (A203): he bowls only where his
        # side has no five bowlers with real volume. With no volume recorded he ranked with
        # the real bowlers on a rating made from a few overs, and middle-order careers
        # bowled four overs in 80 of 143 matches.
        bowl_balls=0 if start.role in ("batter", "keeper") and start.bowl is not None else None,
    )


def arc_point(k: int, permanent: float, season: float) -> float:
    return max(ABILITY_FLOOR, min(ABILITY_CAP, ARC[k] + permanent + season))


# --- a real year's league ----------------------------------------------------------------


def year_fs_ids(deck: Deck, year: int) -> list[int]:
    return sorted(fs for fs, cs in deck.cards_by_fs.items() if cs and cs[0].season_year == year)


def double_at_for(n: int) -> tuple[int, ...]:
    """Who meets twice. Eight or nine teams: everybody, as in the real seasons with eight or
    nine (14 and 16 matches). Ten: the ten-team circle the solo season already uses."""
    return DOUBLE_AT if n == 10 else tuple(range(1, n // 2 + 1))


def xi_with(you: Card, squad: list[Card]) -> tuple[list[Card], Card | None]:
    """Your franchise's eleven with you in it, then its Impact Player by the opposition's
    own rule. If the squad's normal selection leaves you out, you replace the lowest-rated
    player whose departure keeps the eleven legal (a keeper, five bowlers, four overseas)."""
    from game.__main__ import OVERSEAS_LIMIT, opposition_xi, twelve_from_xi, viable

    pool = squad + [you]
    xi = opposition_xi(pool)
    if you not in xi:
        swaps = []
        for out in xi:
            new = [c for c in xi if c is not out] + [you]
            if viable(new) and sum(c.overseas is True for c in new) <= OVERSEAS_LIMIT:
                swaps.append((out.rating, out.person_id, new))
        if not swaps:
            raise CareerError("no legal eleven has room for you")
        xi = min(swaps, key=lambda s: s[:2])[2]
    if not bowls_for_you(you):
        return twelve_from_xi(xi, pool)
    xi, you = _in_the_attack(xi, you)
    # The bench never holds a better bowler than you, or a bowling Impact Player would
    # take your overs in the matches he plays.
    # Your own original card leaves the pool too: the ranked copy in the eleven is you,
    # and the original on the bench would otherwise be your side's Impact Player.
    pool = [c for c in pool if c.person_id != YOU_ID and (c in xi or not _outbowls(c, you))]
    return twelve_from_xi(xi, pool + [you])


def bowls_for_you(you: Card) -> bool:
    """A bowler or all-rounder bowls every match. A batter's part-time overs are left to
    the side, as they would be."""
    return you.role in ("bowler", "allrounder") and you.has_bowl


def _outbowls(c: Card, you: Card) -> bool:
    """Would the side bowl `c` ahead of you (A203's order: real volume, then rating)?"""
    from game.__main__ import bowling_order

    return c.has_bowl and c.person_id != you.person_id and bowling_order(c) < bowling_order(you)


def _in_the_attack(xi: list[Card], you: Card) -> tuple[list[Card], Card]:
    """Rank you among the five who bowl. The engine gives the overs to the eleven's five
    best by `Card.bowl` (`attack`), and a franchise that signed you as a bowler bowls you:
    its fifth-best bowler gives up his overs instead.

    This moves ONLY the side's ranking key. Ball outcomes never read `Card.bowl` -- they
    come from your two A160 numbers (`bowl_scoring`, `bowl_dismissal`), which are left
    exactly as `ability` made them -- so you bowl as well as you really are, just every
    match. Raising the key was chosen over choosing which better bowlers to drop because a
    thin squad can make that impossible: Chennai 2015 has fourteen rated men, ten of whom
    outbowl a weak spinner, and no legal eleven holds him with four or fewer of them."""
    from game.__main__ import BOWLERS_IN_TWELVE

    from game.__main__ import bowling_order

    others = sorted((c for c in xi if c is not you and c.has_bowl), key=bowling_order)
    if len(others) < BOWLERS_IN_TWELVE or bowling_order(you) < bowling_order(others[BOWLERS_IN_TWELVE - 1]):
        return xi, you
    # You have no recorded volume, so you rank with the real bowlers; within them, by rating.
    ranked = dataclasses.replace(you, bowl=math.nextafter(others[BOWLERS_IN_TWELVE - 1].bowl, math.inf))
    return [ranked if c is you else c for c in xi], ranked


def _side(cards: list[Card], xi: list[Card], impact: Card | None) -> Side:
    c = cards[0]
    return Side(name=f"{c.franchise} {c.season_year}", short=_abbrev(c.franchise, c.season_year),
                xi=xi, impact=impact, franchise=c.franchise, year=c.season_year)


def year_sides(deck: Deck, year: int, you: Card | None = None) -> tuple[list[Side], Side | None]:
    """Every real team of `year`, yours with you in it. Returns (sides, yours)."""
    from game.__main__ import opposition_twelve

    sides, yours = [], None
    for fs in year_fs_ids(deck, year):
        squad = list(deck.cards_by_fs[fs])
        if you is not None and fs == you.fs_id:
            side = _side(squad, *xi_with(you, squad))
            side.you = True
            yours = side
        else:
            side = _side(squad, *opposition_twelve(squad))
        sides.append(side)
    return sides, yours


# --- one season's numbers --------------------------------------------------------------


@dataclass
class Line:
    """One (person, side)'s season. Keyed by side identity as well as person, as the caps
    are (A96): the engine never puts one person on two sides of a year, but a key that
    allowed it would sum two stints into a season nobody had."""

    card: Card
    side: Side
    matches: int = 0
    innings: int = 0
    runs: int = 0
    balls: int = 0
    outs: int = 0
    fifties: int = 0
    hundreds: int = 0
    highest: int = 0
    bowl_balls: int = 0
    bowl_runs: int = 0
    wickets: int = 0
    four_fors: int = 0
    five_fors: int = 0
    best: tuple[int, int] = (0, 0)       # (wickets, runs) in one innings


def season_lines(results: list[Result],
                 roster: dict[int, dict[str, Card]] | None = None) -> dict[tuple[str, int], Line]:
    """Every player's season from the full scorecards. A match counts as played if he is on
    either card of either innings: an Impact Player swapped out at the break has already
    batted or bowled (A78), so he played. Bowling figures belong to the FIELDING side
    (A101).

    `roster[id(side)]` maps a person to his card for every man the side may field. It is
    needed whenever a side's eleven changed during the season (a career's missed matches,
    A198): the side's eleven at the END of the season is then not everybody who played."""
    lines: dict[tuple[str, int], Line] = {}

    def cards_of(side: Side) -> dict[str, Card]:
        if roster is not None and id(side) in roster:
            return roster[id(side)]
        return {c.person_id: c for c in side.xi + ([side.impact] if side.impact else [])}

    def line(card: Card, side: Side) -> Line:
        key = (card.person_id, id(side))
        if key not in lines:
            lines[key] = Line(card=card, side=side)
        return lines[key]

    for r in results:
        played: set[tuple[str, int]] = set()
        for batting, fielding, inn in ((r.home, r.away, r.home_innings),
                                        (r.away, r.home, r.away_innings)):
            if inn is None:
                continue
            cards, fcards = cards_of(batting), cards_of(fielding)
            for b in inn.batting:
                card = cards.get(b.player.person_id)
                if card is None:
                    continue
                ln = line(card, batting)
                played.add((card.person_id, id(batting)))
                if b.faced_any or b.out:
                    ln.innings += 1
                    ln.runs += b.runs
                    ln.balls += b.balls
                    ln.outs += b.out
                    ln.highest = max(ln.highest, b.runs)
                    ln.hundreds += b.runs >= 100
                    ln.fifties += 50 <= b.runs < 100
            for w in inn.bowling:
                card = fcards.get(w.player.person_id)
                if card is None or not w.balls:
                    continue
                ln = line(card, fielding)
                played.add((card.person_id, id(fielding)))
                ln.bowl_balls += w.balls
                ln.bowl_runs += w.runs
                ln.wickets += w.wickets
                ln.four_fors += w.wickets >= 4
                ln.five_fors += w.wickets >= 5
                if (w.wickets, -w.runs) > (ln.best[0], -ln.best[1]):
                    ln.best = (w.wickets, w.runs)
        for key in played:
            lines[key].matches += 1
    return lines


# --- the season card ---------------------------------------------------------------------
# A163's statistics half, against the season's regulars. Weights and shrinkage are A163's;
# the peers are the simulated season's, so the engine is compared with itself and never
# with the archive (the engine's mean runs per innings is not the archive's to the run).

SHRINK_BALLS = 100
SHRINK_OUTS = 5
PEER_MIN_INNINGS = 5
PEER_MIN_BOWL_BALLS = 60
MIN_REAL_CARDS = 8           # fewer real regulars of a role in a year: use every year
BAND_GROUP = {"opener": "top", "top_order": "top", "middle": "middle",
              "finisher": "finisher", "tail": "finisher"}
MIN_BAND_PEERS = 6           # fewer batters of your band in a season: rank among all batters


def _kind(card: Card) -> str:
    return "allrounder" if card.role == "allrounder" else (
        "bowler" if card.role == "bowler" else "batter")


def _z(values: list[float]) -> list[float]:
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values) or 1.0
    return [(v - mean) / sd for v in values]


def _bat_scores(lines: list[Line]) -> list[float]:
    runs = sum(l.runs for l in lines)
    balls = sum(l.balls for l in lines) or 1
    outs = sum(l.outs for l in lines) or 1
    rpb, avg = runs / balls, runs / outs
    per_match = _z([l.runs / max(l.matches, 1) for l in lines])
    sr = _z([(l.runs + SHRINK_BALLS * rpb) / (l.balls + SHRINK_BALLS) for l in lines])
    av = _z([(l.runs + SHRINK_OUTS * avg) / (l.outs + SHRINK_OUTS) for l in lines])
    return [0.40 * a + 0.35 * b + 0.25 * c for a, b, c in zip(per_match, sr, av)]


def _bowl_scores(lines: list[Line]) -> list[float]:
    runs = sum(l.bowl_runs for l in lines)
    balls = sum(l.bowl_balls for l in lines) or 1
    rpb = runs / balls
    per_match = _z([l.wickets / max(l.matches, 1) for l in lines])
    total = _z([float(l.wickets) for l in lines])
    econ = _z([-(l.bowl_runs + SHRINK_BALLS * rpb) / (l.bowl_balls + SHRINK_BALLS)
               for l in lines])
    return [0.45 * a + 0.15 * b + 0.40 * c for a, b, c in zip(per_match, total, econ)]


def season_percentile(lines: dict[tuple[str, int], Line], you: tuple[str, int]) -> float:
    """Your place among the season's regulars of your kind, 0 (worst) to 1 (best), by the
    card's statistics score. You are always in the pool, whatever your volume.

    A batter is ranked among batters and keepers of his band, a bowler among bowlers, scored
    against everybody who batted (or bowled) enough. An all-rounder scores both halves and
    is ranked among everybody who did enough of both."""
    me = lines[you]
    kind = _kind(me.card)
    bat = [l for k, l in lines.items()
           if k == you or (l.card.role != "bowler" and l.innings >= PEER_MIN_INNINGS)]
    bowl = [l for k, l in lines.items()
            if k == you or (l.card.role in ("bowler", "allrounder")
                            and l.bowl_balls >= PEER_MIN_BOWL_BALLS)]
    bat_score = {id(l): s for l, s in zip(bat, _bat_scores(bat))}
    bowl_score = {id(l): s for l, s in zip(bowl, _bowl_scores(bowl))}
    if kind == "batter":
        score = bat_score
        # Among batters of your own band, as real cards are compared within role (A163): a
        # finisher's twenty off twelve is not an opener's failure (A198).
        # Everybody who BATS in your band counts -- the finisher band is mostly all-rounders,
        # so batters alone were too few to rank among -- but not bowlers: ranked against
        # tail-enders' batting, every finisher became the season's best (A198, measured).
        band = [l for k, l in lines.items() if k == you or (
            l.innings >= PEER_MIN_INNINGS and l.card.role != "bowler"
            and BAND_GROUP.get(l.card.band) == BAND_GROUP.get(me.card.band))]
        if len(band) - 1 >= MIN_BAND_PEERS:
            score = {id(l): s for l, s in zip(band, _bat_scores(band))}
            peers = band
        else:
            peers = [l for l in bat if _kind(l.card) == "batter"]
    elif kind == "bowler":
        score = bowl_score
        peers = [l for l in bowl if _kind(l.card) == "bowler"]
    else:
        # Everybody who did enough of BOTH, whatever the archive calls him: ranked among
        # the season's handful of listed all-rounders, the best of five read as a 99 (A198).
        bat_any = [l for k, l in lines.items() if k == you or l.innings >= PEER_MIN_INNINGS]
        bowl_any = [l for k, l in lines.items() if k == you or l.bowl_balls >= PEER_MIN_BOWL_BALLS]
        in_bowl = {id(l) for l in bowl_any}
        both = [l for l in bat_any if id(l) in in_bowl]
        b1 = {id(l): s for l, s in zip(bat_any, _bat_scores(bat_any))}
        b2 = {id(l): s for l, s in zip(bowl_any, _bowl_scores(bowl_any))}
        score = {id(l): b1[id(l)] + b2[id(l)] for l in both}
        peers = both
    others = [score[id(l)] for l in peers if l is not me]
    if not others:
        return 0.5
    mine = score[id(me)]
    below = sum(s < mine for s in others) + 0.5 * sum(s == mine for s in others)
    return below / len(others)


def real_displays(deck: Deck, year: int, kind: str) -> list[int]:
    """The real regulars' card ratings of `year` for your kind, sorted; every year's if the
    year has too few to read a distribution from. Read for the whole kind, not per batting
    band (A198, measured): only 22 real cards in nineteen years are batters in the finisher
    band, best 85, which is too few to be a scale -- reading finishers off it capped every
    finisher's career at 85."""
    def ok(c: Card) -> bool:
        return c.display is not None and _kind(c) == kind and _regular(c)

    cards = [c.display for fs in year_fs_ids(deck, year) for c in deck.cards_by_fs[fs] if ok(c)]
    if len(cards) < MIN_REAL_CARDS:
        cards = [c.display for cs in deck.cards_by_fs.values() for c in cs if ok(c)]
    return sorted(cards)


def quantile(sorted_values: list[int], p: float) -> int:
    """Linear interpolation, so p = 0 is the lowest real card and p = 1 the highest."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    x = p * (len(sorted_values) - 1)
    lo = math.floor(x)
    hi = min(lo + 1, len(sorted_values) - 1)
    return round(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (x - lo))


def season_display(deck: Deck, year: int, card: Card, percentile: float) -> int:
    return max(70, min(99, quantile(real_displays(deck, year, _kind(card)), percentile)))


# --- offers ---------------------------------------------------------------------------


# [A200] Contracts, ratified by the user: a three-year deal is paid less and locks you in,
# and form cannot end your career while a year of it remains. The IPL itself runs on
# three-year cycles between mega auctions.
CONTRACT_YEARS = 3
CONTRACT_DISCOUNT = 0.85


@dataclass(frozen=True)
class Offer:
    fs_id: int
    franchise: str
    short: str
    price: int                  # lakh, a one-year deal
    strength: int | None        # the franchise's twelve's mean card rating, without you
    stay: bool = False
    price3: int | None = None   # lakh a year on a three-year deal; None when not offered
    contract_year: int = 0      # 2 or 3: the season of a three-year deal already signed
    position: int | None = None  # where you would bat in that eleven (A200)
    attack_rank: int | None = None  # where you would come in its attack, if you bowl


def offers_for(deck: Deck, seed: int, k: int, year: int, current: str | None,
               last_display: int | None, contract: "Contract | None" = None) -> list[Offer]:
    """Your current franchise first (if it still exists this year), then OFFERS others.
    `current` is a CANONICAL franchise, so Delhi Daredevils and Delhi Capitals are one club
    and staying across the rename is staying. A debutant is uncapped and every offer is
    the base price."""
    from game.__main__ import opposition_twelve

    rng = random.Random(f"{seed}:offers:{k}")
    fss = year_fs_ids(deck, year)
    club = {fs: canonical(deck.cards_by_fs[fs][0].franchise) for fs in fss}
    if contract is not None and contract.left > 0:
        signed = [fs for fs in fss if club[fs] == contract.club]
        if signed:                     # a club with no team this year voids the deal
            fs = signed[0]
            squad = deck.cards_by_fs[fs]
            xi, impact = opposition_twelve(list(squad))
            c = squad[0]
            return [Offer(fs, c.franchise, _abbrev(c.franchise, year), contract.price,
                          team_rating(xi + ([impact] if impact else [])).overall, True,
                          contract_year=CONTRACT_YEARS - contract.left + 1)]
    others = [fs for fs in fss if club[fs] != current]
    picked = sorted(rng.sample(others, min(OFFERS, len(others))))
    value = DEBUT_PRICE if last_display is None else value_curve(last_display, mega=True)

    def offer(fs: int, stay: bool) -> Offer:
        squad = deck.cards_by_fs[fs]
        xi, impact = opposition_twelve(list(squad))
        price = value * (STAY_DISCOUNT if stay else rng.uniform(1 - PRICE_NOISE, 1 + PRICE_NOISE))
        price = (DEBUT_PRICE if last_display is None
                 else max(DEBUT_PRICE, min(PRICE_CAP, 5 * round(price / 5))))
        c = squad[0]
        price3 = price if last_display is None else max(
            DEBUT_PRICE, 5 * round(price * CONTRACT_DISCOUNT / 5))
        return Offer(fs, c.franchise, _abbrev(c.franchise, year), price,
                     team_rating(xi + ([impact] if impact else [])).overall, stay, price3)

    out = [offer(fs, True) for fs in fss if current is not None and club[fs] == current]
    return out + [offer(fs, False) for fs in picked]


def where_you_play(deck: Deck, you: Card, fs_id: int) -> tuple[int, int | None]:
    """(batting position, place in the attack or None) for you in that franchise's eleven,
    by the same selection the season itself makes."""
    xi, _ = xi_with(you, list(deck.cards_by_fs[fs_id]))
    position = next(i for i, c in enumerate(xi, 1) if c.person_id == YOU_ID)
    if not bowls_for_you(you):
        return position, None
    me = next(c for c in xi if c.person_id == YOU_ID)
    from game.__main__ import bowling_order

    attack = sorted((c for c in xi if c.has_bowl), key=bowling_order)
    return position, next(i for i, c in enumerate(attack, 1) if c is me)


def event_sequence(seed: int, overseas: bool = False) -> list[Event]:
    """The career's event cards, one per season, from the seed and the passport alone (never
    from your choices, so an answer cannot change which card comes next). No card twice
    running, and none beyond `EVENT_LIMIT` times in a career: four coaches in a row reads as
    a bug and stacks a permanent effect the curve was not drawn for. A card's `when` decides
    who may draw it at all."""
    rng = random.Random(f"{seed}:events")
    out: list[Event] = []
    used: dict[str, int] = {}
    for k in range(MAX_SEASONS):
        allowed = [e for e in EVENTS
                   if (not out or e.key == "quiet" or e is not out[-1])
                   and used.get(e.key, 0) < EVENT_LIMIT.get(e.key, MAX_SEASONS)
                   and (e.when != "late" or k >= LATE_FROM)
                   and (e.when != "overseas" or overseas)]
        e = rng.choices(allowed, weights=[e.weight for e in allowed])[0]
        used[e.key] = used.get(e.key, 0) + 1
        out.append(e)
    return out


def draw_event(seed: int, k: int, overseas: bool = False) -> Event:
    return event_sequence(seed, overseas)[k]


def event_name(deck: Deck, event: Event, fs_id: int, year: int, you: Card) -> str | None:
    """The real player an event names: the best of your kind in your own squad (a mentor),
    or the best player of any other team that year (a rival), by his card that season."""
    if event.who == "mentor":
        # Somebody who actually did your job that season, by A33's volume floors and
        # whatever his listed role -- Jadeja, an "all-rounder", is Gujarat Lions 2017's real
        # spinner, where the first version picked a man with 24 balls whose card was mostly
        # prior. A bowler's mentor shares his style where the squad has one (a pace bowler
        # coaching a leg-spinner reads wrong). The old rule is the fallback for a thin squad.
        bowls = you.role in ("bowler", "allrounder")
        mates = [c for c in deck.cards_by_fs[fs_id] if c.display is not None and (
            (c.bowl_balls or 0) >= BOWL_REGULAR_BALLS if bowls
            else (c.bat_balls or 0) >= BAT_REGULAR_BALLS)]
        if not mates:
            mates = [c for c in deck.cards_by_fs[fs_id]
                     if c.display is not None and _kind(c) == _kind(you)]
        same = [c for c in mates if bowls and you.bowling_style
                and c.bowling_style == you.bowling_style]
        best = max(same or mates, key=lambda c: (c.display, c.person_id), default=None)
    elif event.who == "rival":
        others = [c for fs in year_fs_ids(deck, year) if fs != fs_id
                  for c in deck.cards_by_fs[fs] if c.display is not None]
        best = max(others, key=lambda c: (c.display, c.person_id), default=None)
    else:
        return None
    return best.name if best else None


# --- a season, and a career -------------------------------------------------------------


@dataclass
class Highlight:
    stage: str
    opponent: str
    text: str


@dataclass
class SeasonRecord:
    k: int
    year: int
    franchise: str
    short: str
    price: int
    stayed: bool
    event: str
    choice: str
    captain: bool
    ability: float
    teams: int
    matches: int = 0
    innings: int = 0
    runs: int = 0
    balls: int = 0
    outs: int = 0
    fifties: int = 0
    hundreds: int = 0
    highest: int = 0
    wickets: int = 0
    bowl_balls: int = 0
    bowl_runs: int = 0
    four_fors: int = 0
    five_fors: int = 0                 # (A209)
    hat_tricks: int = 0                # (A209)
    best: tuple[int, int] = (0, 0)
    squad: bool = False                # a squad season: a poor one went before (A198)
    extra_missed: int = 0              # matches an event card cost you, inside `missed` (A200)
    contract_year: int = 0             # 1-3 on a three-year deal, 0 on a one-year (A200)
    missed: int = 0                    # league matches missed (A198)
    dropped: int = 0                   # of them, dropped for form after a make-or-break (A205)
    percentile: float = 0.0
    form: int = 70                     # the season's own card, before the reputation floor
    display: int = 70
    position: int = 0
    playoffs: bool = False
    finalist: bool = False
    champion: bool = False
    orange_cap: bool = False
    purple_cap: bool = False
    highlights: list[Highlight] = field(default_factory=list)
    milestones: list["CareerMilestone"] = field(default_factory=list)   # (A209)


def _best_bat(inn, but: str = YOU_ID):
    """The innings' top scorer other than `but`, or None if nobody else scored."""
    others = [b for b in (inn.batting if inn else []) if b.player.person_id != but and b.runs]
    return max(others, key=lambda b: (b.runs, -b.balls), default=None)


def _best_ball(inn, but: str = YOU_ID):
    others = [w for w in (inn.bowling if inn else []) if w.player.person_id != but and w.wickets]
    return max(others, key=lambda w: (w.wickets, -w.runs), default=None)


def _bat_line(b) -> str:
    return f"{b.player.name} {b.runs}{'' if b.out else '*'}"


def _ball_line(w) -> str:
    return f"{w.player.name} {w.wickets}/{w.runs}"


def _highlights(results: list[Result], yours: Side) -> list[Highlight]:
    """Your best few moments -- fifties and three-fors, a final -- each with the real
    teammate who did most beside you in that match, off the scorecard (A200). The names
    are the real squad's; a partnership is not claimed, because the engine keeps none."""
    out: list[tuple[int, Highlight]] = []
    for r in results:
        if yours is not r.home and yours is not r.away:
            continue
        opp = r.away if yours is r.home else r.home
        bat = r.home_innings if yours is r.home else r.away_innings
        bowl = r.away_innings if yours is r.home else r.home_innings
        for b in (bat.batting if bat else []):
            if b.player.person_id == YOU_ID and b.runs >= 50:
                mate = _best_bat(bat)
                beside = f" · {_bat_line(mate)}" if mate else ""
                out.append((b.runs, Highlight(r.stage, opp.short,
                            f"{b.runs}{'' if b.out else '*'} off {b.balls}{beside}")))
        for w in (bowl.bowling if bowl else []):
            if w.player.person_id == YOU_ID and w.wickets >= 3:
                mate = _best_ball(bowl)
                beside = f" · {_ball_line(mate)}" if mate else ""
                out.append((25 * w.wickets, Highlight(r.stage, opp.short,
                            f"{w.wickets}/{w.runs} in {w.overs} overs{beside}")))
        if r.stage == "Final":
            won = r.winner is yours
            star = _best_bat(bat, but="")          # the side's top scorer, you included
            # `margin` names the winner ("RCB 2015 by 9 wickets"), so it reads as an aside.
            text = f"{'Won' if won else 'Lost'} the final{f' ({r.margin})' if r.margin else ''}"
            if star and star.player.person_id == YOU_ID:
                text += f" · you top-scored with {star.runs}"
            elif star:
                text += f" · {_bat_line(star)} top-scored"
            out.append((1000 if won else 500, Highlight(r.stage, opp.short, text)))
    return [h for _, h in sorted(out, key=lambda x: -x[0])[:4]]


# A season is a pure function of its inputs, so a warm process keeps the ones it has
# played: a replay (every request, and every step of `auto_career`) then costs nothing for
# the seasons already behind it. Cleared when full rather than managed, like A181's cache.
_SEASON_CACHE: dict[tuple, tuple] = {}
SEASON_CACHE_MAX = 4000


def _cached_season(deck: Deck, model: Model, seed: int, k: int, you: Card,
                   record: SeasonRecord, key: tuple,
                   answers: tuple[int, ...] | None = None) -> tuple[SeasonRecord, "SeasonDetail"]:
    """The record is copied (the career goes on to change it); the detail is shared, since
    nothing changes it after it is built. Your answers are part of the key: a different call
    at a key moment is a different season. A season that pauses raises and is not cached."""
    full = (id(deck), id(model), seed, k, key, answers)
    hit = _SEASON_CACHE.get(full)
    if hit is None:
        if len(_SEASON_CACHE) >= SEASON_CACHE_MAX:
            _SEASON_CACHE.clear()
        hit = _SEASON_CACHE[full] = play_season_detail(deck, model, seed, k, you, record,
                                                       answers)
    return copy.deepcopy(hit[0]), hit[1]


@dataclass
class Year:
    season: object
    yours: Side
    results: list[Result]
    roster: dict[int, dict[str, Card]]
    missed: int                        # league matches you missed
    approaches: list = field(default_factory=list)   # your key moments (A204)
    dropped: list = field(default_factory=list)      # your match numbers, dropped for (A205)
    verdicts: dict = field(default_factory=dict)     # make-or-break number -> kept | dropped


def missed_block(seed: int, k: int, league_matches: int, squad: bool = False,
                 extra: int = 0, form_drop: float = 0.0) -> range:
    """Which of your league matches you miss: one block, its length from the real share of
    matches a regular (or, in a squad season, a non-regular) plays. Its own random stream,
    so missing matches never changes how the matches themselves are drawn.

    `form_drop` is the chance this season's share already held a real two-match form drop
    (FORM_DROP_RATE): for a player who can be dropped for form in the season itself, that
    much is taken off the block, on a stream of its own (A205)."""
    rng = random.Random(f"{seed}:avail:{k}")
    shares = SQUAD_SHARES if squad else AVAILABILITY_SHARES
    base = round((1 - rng.choice(shares)) * league_matches)
    if form_drop and random.Random(f"{seed}:avail-form:{k}").random() < form_drop:
        base = max(0, base - DROP_MATCHES)
    missed = min(league_matches, base + extra)
    start = rng.randrange(league_matches - missed + 1)
    return range(start, start + missed)


# [A198] Real all-rounders in regular seasons bowl 14.4 balls a match (195 seasons); a
# bowler gets all four overs or none in this engine, so an all-rounder bowls in 14.4 / 24
# of his matches instead of four overs in every one -- which had sent 21-38% of all-rounder
# careers into the all-time top five. Bowlers keep four overs every match: real regulars
# bowl 21.3 balls a match, close enough that sitting a specialist out of the attack would
# be stranger than the 13% it corrects.
ALLROUNDER_BOWL_SHARE = 0.60
_RESTED_BOWL = -9.9                  # below anybody: out of the five, as `bat_delta`'s sentinel


def _rested_lineup(side: Side, me: Card) -> tuple[list[Card], Card | None] | None:
    """Your side with you NOT among its five bowlers this match, or None when that is not
    possible (you are not an all-rounder, or the eleven has no five other bowling options:
    an attack short of five cannot get through twenty overs, A133)."""
    from game.__main__ import BOWLERS_IN_TWELVE

    if me.role != "allrounder" or not me.has_bowl:
        return None
    if sum(c.has_bowl for c in side.xi if c is not me) < BOWLERS_IN_TWELVE:
        return None
    resting = dataclasses.replace(me, bowl=_RESTED_BOWL, bowl_balls=0)   # thin: never in the five
    return [resting if c is me else c for c in side.xi], side.impact


def simulate_year(deck: Deck, model: Model, seed: int, k: int, you: Card,
                  squad: bool = False, extra_missed: int = 0,
                  answers: tuple[int, ...] | None = None) -> Year:
    """The year itself. Separate from the scoring so a test can hold the season's own
    leaders against `tournament_leaders`.

    `answers` are your approaches at the season's key moments (A204), in order. None plays
    every key moment Balanced without stopping; a tuple plays it with moments, and when one
    comes up with no answer left the season pauses (`_NeedMoment`).

    A make-or-break (A205) is a key moment like any other, but its outcome is real whatever
    the call: fail it and you are dropped for DROP_MATCHES league matches. The season's
    missed block was measured from real seasons, so it already held real form drops; it is
    drawn shorter by their real rate (`FORM_DROP_RATE`) for anyone who can be dropped, and
    a drop then adds to it."""
    from game.__main__ import opposition_twelve

    sides, yours = year_sides(deck, you.season_year, you)
    rng = random.Random(f"{seed}:season:{k}")
    rng.shuffle(sides)                  # who meets twice in a ten-team year is the draw's
    double = double_at_for(len(sides))
    with_you = (yours.xi, yours.impact)
    without = opposition_twelve(list(deck.cards_by_fs[you.fs_id]))
    me = next(c for c in yours.xi if c.person_id == YOU_ID)
    roster = {id(s): {c.person_id: c for c in deck.cards_by_fs[
        next(fs for fs in year_fs_ids(deck, you.season_year)
             if deck.cards_by_fs[fs][0].franchise == s.franchise)]} for s in sides}
    roster[id(yours)][YOU_ID] = me
    yours_index = sides.index(yours)
    schedule = balanced_order(fixtures(len(sides), double), lambda pair: pair)
    league_matches = sum(yours_index in pair for pair in schedule)
    droppable = not squad and _kind(you) != "allrounder"
    out = set(missed_block(seed, k, league_matches, squad, extra_missed,
                           FORM_DROP_RATE[_kind(you)] if droppable else 0.0))
    dropped: set[int] = set()                      # league indices you were dropped for
    dropped_seen: list[int] = []                   # ... as your match numbers, for the feed
    verdicts: dict[int, str] = {}                  # make-or-break match -> "kept" | "dropped"
    book = FormBook(roster)
    you_key = (YOU_ID, id(yours))
    breaks = 0
    pending_break: int | None = None               # your match number of an unresolved one
    played = 0
    rested = _rested_lineup(yours, me)
    bowl_rng = random.Random(f"{seed}:bowls:{k}")
    labels = APPROACH_LABELS[_kind(me)]
    used = 0                                       # answers consumed
    must_wins = 0
    seen = 0                                       # your side's matches so far, this included
    # (your match number, stage, opponent, approach). The NUMBER identifies the match: a
    # league meets every side twice, and keying on the opponent tagged the wrong meeting.
    approaches: list[tuple[int, str, str, str]] = []

    def moment(a: Side, b: Side, season, stage: str, left: int,
               make_or_break: str | None = None) -> None:
        """Stop for a key moment, or apply its answer to your card for this match. A
        make-or-break carries its own stakes; a league match otherwise stops only as a
        must-win."""
        nonlocal used, must_wins
        if not any(c.person_id == YOU_ID for c in yours.xi):
            return
        if make_or_break is not None:
            kind, stakes = "make_or_break", make_or_break
        elif stage == "league":
            if must_wins >= MUST_WIN_MAX:
                return
            kind, stakes = "must_win", league_stakes(season, yours, left)
            if stakes is None:
                return
        else:
            kind, stakes = "playoff", PLAYOFF_STAKES.get(stage, stage)
        if answers is None:
            return                                 # every call Balanced, nobody asked
        opp = b if a is yours else a
        if used >= len(answers):
            raise _NeedMoment(Moment(stage, opp.name, opp.short, opp.franchise, opp.year,
                                     stakes, labels, kind), season, yours, list(approaches),
                              roster, list(dropped_seen), dict(verdicts))
        choice = answers[used]
        used += 1
        if kind == "must_win":
            must_wins += 1
        approaches.append((seen, stage, opp.short, labels[choice], kind))
        yours.xi = [approach_card(c, choice) if c.person_id == YOU_ID else c
                    for c in yours.xi]

    def drop_from(idx: int) -> None:
        """Dropped for this match and the next. The missed block was already drawn short
        by the real rate of drops, so a drop adds to it rather than coming off it."""
        dropped.update(range(idx, min(idx + DROP_MATCHES, league_matches)))

    def lineup() -> tuple[list[Card], Card | None]:
        """An all-rounder bowls in ALLROUNDER_BOWL_SHARE of the matches he plays."""
        if rested is None or bowl_rng.random() < ALLROUNDER_BOWL_SHARE:
            return with_you
        return rested

    def league_hook(a: Side, b: Side, season, stage: str) -> None:
        nonlocal played, seen, breaks, pending_break
        if yours is a or yours is b:
            # Form is read as it stood straight after your own last match -- the moment the
            # feed shows it -- never shifted by other sides' matches played since.
            mine = [i for i, r in enumerate(season.results) if yours in (r.home, r.away)]
            if mine:
                book.add(season.results[:mine[-1] + 1])
            idx = played
            if pending_break is not None:
                # the make-or-break was your last match: did you fail again?
                failed = book.last_match_rank(you_key) < FORM_FAIL
                verdicts[pending_break] = "dropped" if failed else "kept"
                if failed:
                    drop_from(idx)
                pending_break = None
            benched = idx in out or idx in dropped
            yours.xi, yours.impact = without if benched else lineup()
            played += 1
            seen += 1
            if idx in dropped:
                dropped_seen.append(seen)
            stakes = None
            rank = None if benched or not droppable else book.form_rank(you_key)
            if (rank is not None and rank < FORM_POOR and breaks < MAKE_OR_BREAK_MAX
                    and idx + 1 < league_matches):
                last = book.games[you_key][-FORM_MATCHES:]
                figures = ", ".join(form_figure(_kind(me), g) for g in last)
                stakes = (f"Your last three: {figures}. Fail again and you are out of the "
                          f"side for the next {_WORDS.get(DROP_MATCHES, DROP_MATCHES)} matches.")
                breaks += 1
                pending_break = seen
            moment(a, b, season, stage, league_matches - played + 1, stakes)

    def playoff_hook(a: Side, b: Side, season, stage: str) -> None:
        nonlocal seen
        if yours is a or yours is b:
            yours.xi, yours.impact = without if squad else lineup()
            seen += 1
            moment(a, b, season, stage, 0)

    try:
        season = run_league(model, sides, rng, double_at=double, before_match=league_hook,
                            schedule=schedule)
        season = run_playoffs(model, season, rng, before_match=playoff_hook)
    finally:
        yours.xi, yours.impact = with_you
    if answers is not None and used < len(answers):
        raise CareerError("more answers than the season had moments")
    year = Year(season, yours, season.results + season.playoffs, roster, len(out | dropped))
    year.approaches = approaches
    year.dropped, year.verdicts = dropped_seen, verdicts
    return year


def season_caps(lines: dict[tuple[str, int], Line]) -> tuple[Line, Line | None]:
    """The Orange and Purple Cap lines, by A175's one ordering over (person, side) lines,
    as `tournament_leaders` ranks them."""
    top_bat = min(lines.values(), key=lambda l: batting_rank(l.runs, l.balls, l.card.name))
    bowled = [l for l in lines.values() if l.bowl_balls]
    top_bowl = min(bowled, key=lambda l: bowling_rank(l.wickets, l.bowl_runs, l.bowl_balls,
                                                       l.card.name)) if bowled else None
    return top_bat, top_bowl


# --- the season as it happened (A202) -----------------------------------------------------
# What the page shows while a season unfolds: each of your matches in order, the table as it
# stood after each, the Orange and Purple Cap race, and the full scorecards of the matches
# worth watching ball by ball. Returned once, with the season just played, and never kept in
# the checkpoint: it is presentation, and the token would grow by every season's matches.


@dataclass
class MatchLine:
    stage: str
    opponent: str                       # "Kolkata Knight Riders 2016"
    opp_short: str
    opp_franchise: str | None
    opp_year: int | None
    result: str                         # "W" | "L" | "T"
    score_for: str                      # "185/6"
    score_against: str
    margin: str
    played: bool                        # were you in the side
    batted: bool = False
    runs: int = 0
    balls: int = 0
    out: bool = False
    wickets: int = 0
    bowl_runs: int = 0
    bowl_balls: int = 0
    wins: int = 0                       # your side's record so far, playoffs included
    losses: int = 0
    position: int | None = None         # in the league table after it; None in the playoffs
    season_runs: int = 0                # yours so far
    season_wickets: int = 0
    orange: tuple[str, int, bool] = ("", 0, False)   # (leader, runs, is it you) after it
    purple: tuple[str, int, bool] = ("", 0, False)
    watch: int | None = None            # index into SeasonDetail.watch
    approach: str | None = None         # your call, when this was a key moment (A204)
    stop: str | None = None             # what kind of key moment: must_win, playoff, make_or_break
    verdict: str | None = None          # a make-or-break's outcome: "kept" | "dropped" (A205)
    dropped: bool = False               # left out for form (A205)
    form: list[str] = field(default_factory=list)   # your last FORM_SHOWN matches, oldest first
    form_word: str | None = None        # In form / Steady / Out of form, after this match
    # This match's own landmarks for you: "fifty"|"hundred", "four_for"|"five_for", "hattrick"
    # (A209). Whether one is a career FIRST is the career's business (`career_milestones`).
    landmarks: list[str] = field(default_factory=list)


@dataclass
class TableRow:
    short: str
    name: str
    franchise: str | None
    year: int | None
    played: int
    won: int
    lost: int
    tied: int
    points: int
    nrr: float
    you: bool


@dataclass
class SeasonDetail:
    feed: list[MatchLine]
    table: list[TableRow]               # the league as it finished (or stands, if paused)
    watch: list[Result]                 # your key moments and playoff matches, in order
    complete: bool = True               # False: the season is paused at a key moment
    # The draft season's chart set for this season (A211), your side tracked. A pure
    # function of the season alone, so it can ride in the shared cache with the rest.
    analysis: SeasonAnalysis | None = None
    results: list[Result] = field(default_factory=list)   # every match, for the analysis' crests


def _landmarks(line: MatchLine, theirs) -> list[str]:
    out = []
    if line.batted and line.runs >= 100:
        out.append("hundred")
    elif line.batted and line.runs >= 50:
        out.append("fifty")
    if line.wickets >= 5:
        out.append("five_for")
    elif line.wickets >= 4:
        out.append("four_for")
    # The engine records a hat-trick on the ball it happens (A167); it belongs to the
    # innings you BOWLED in, which is the other side's.
    if any(m.kind == "hattrick" and m.person_id == YOU_ID
           for m in (theirs.milestones if theirs else [])):
        out.append("hattrick")
    return out


def _score(runs: int, wickets: int) -> str:
    return f"{runs}" if wickets >= 10 else f"{runs}/{wickets}"


def season_detail(year: "Year", complete: bool = True) -> SeasonDetail:
    """Walk the season match by match and record, after each of your matches, where things
    stood. A career's league is PLAYED in matchday order (`balanced_order`, A204), so the
    results arrive in the order to tell them: in the engine's own fixture order your fourth
    match came after another side's fourteenth and the cap race read 399 runs (A202). A
    paused season (`complete=False`) is told up to the pause. The table uses the league's own crediting and ranking
    (`credit_result`, `rank_table`), the caps A175's ordering, so the last line of each
    agrees with the season's own table and leaders by construction."""
    from game.season import Standing, balanced_order, credit_result, rank_table

    season, yours = year.season, year.yours
    standings = {s.name: Standing(side=s) for s in season.sides}
    totals: dict[tuple[str, int], list] = {}       # (person, side) -> [name, runs, balls, w, br, bb]
    feed: list[MatchLine] = []
    watch: list[Result] = []
    wins = losses = 0

    def leader(batting: bool) -> tuple[str, int, bool]:
        if batting:
            key = min(totals, key=lambda k: batting_rank(totals[k][1], totals[k][2], totals[k][0]))
            return totals[key][0], totals[key][1], key[0] == YOU_ID
        bowled = [k for k in totals if totals[k][5]]
        if not bowled:
            return "", 0, False
        key = min(bowled, key=lambda k: bowling_rank(totals[k][3], totals[k][4], totals[k][5],
                                                     totals[k][0]))
        return totals[key][0], totals[key][3], key[0] == YOU_ID

    moments = list(year.approaches)
    book = FormBook(year.roster)
    you_key = (YOU_ID, id(yours))
    you_card = year.roster.get(id(yours), {}).get(YOU_ID)
    you_kind = _kind(you_card) if you_card is not None else "batter"
    n = 0                                          # your side's match number
    for i, r in enumerate(year.results):
        for side, inn, other in ((r.home, r.home_innings, r.away_innings),
                                 (r.away, r.away_innings, r.home_innings)):
            for b in (inn.batting if inn else []):
                t = totals.setdefault((b.player.person_id, id(side)), [b.player.name, 0, 0, 0, 0, 0])
                t[1] += b.runs
                t[2] += b.balls
            for w in (other.bowling if other else []):
                t = totals.setdefault((w.player.person_id, id(side)), [w.player.name, 0, 0, 0, 0, 0])
                t[3] += w.wickets
                t[4] += w.runs
                t[5] += w.balls
        if r.stage == "league":
            credit_result(standings, r)
        if yours is not r.home and yours is not r.away:
            continue
        home = yours is r.home
        opp = r.away if home else r.home
        n += 1
        mine = r.home_innings if home else r.away_innings
        theirs = r.away_innings if home else r.home_innings
        result = "T" if r.winner is None else "W" if r.winner is yours else "L"
        wins += result == "W"
        losses += result == "L"
        line = MatchLine(
            stage=r.stage, opponent=opp.name, opp_short=opp.short, opp_franchise=opp.franchise,
            opp_year=opp.year, result=result,
            score_for=_score(r.home_runs if home else r.away_runs,
                             r.home_wickets if home else r.away_wickets),
            score_against=_score(r.away_runs if home else r.home_runs,
                                 r.away_wickets if home else r.home_wickets),
            margin=r.margin, played=False, wins=wins, losses=losses)
        for b in (mine.batting if mine else []):
            if b.player.person_id == YOU_ID:
                line.played = True
                line.batted = b.faced_any or b.out
                line.runs, line.balls, line.out = b.runs, b.balls, b.out
        for w in (theirs.bowling if theirs else []):
            if w.player.person_id == YOU_ID and w.balls:
                line.played = True
                line.wickets, line.bowl_runs, line.bowl_balls = w.wickets, w.runs, w.balls
        line.landmarks = _landmarks(line, theirs)
        me = totals.get((YOU_ID, id(yours)))
        line.season_runs, line.season_wickets = (me[1], me[3]) if me else (0, 0)
        if moments and moments[0][0] == n:
            m = moments.pop(0)
            line.approach, line.stop = m[3], m[4]
        line.verdict = year.verdicts.get(n)
        line.dropped = n in year.dropped
        book.add(year.results[:i + 1])     # form as it stands straight after this match
        line.form = [form_figure(you_kind, g) for g in book.games.get(you_key, [])[-FORM_SHOWN:]]
        line.form_word = form_word(book.form_rank(you_key))
        if r.stage == "league":
            line.position = next(i for i, s in enumerate(rank_table(standings), 1)
                                 if s.side is yours)
        if r.stage != "league" or line.approach:
            line.watch = len(watch)
            watch.append(r)
        line.orange, line.purple = leader(True), leader(False)
        feed.append(line)

    table = [TableRow(short=s.side.short, name=s.side.name, franchise=s.side.franchise,
                      year=s.side.year, played=s.played, won=s.won, lost=s.lost, tied=s.tied,
                      points=s.points, nrr=round(s.nrr, 3), you=s.side is yours)
             for s in rank_table(standings)]
    return SeasonDetail(feed, table, watch, complete)


def play_season(deck: Deck, model: Model, seed: int, k: int, you: Card,
                record: SeasonRecord, answers: tuple[int, ...] | None = None) -> SeasonRecord:
    return play_season_detail(deck, model, seed, k, you, record, answers)[0]


def play_season_detail(deck: Deck, model: Model, seed: int, k: int, you: Card,
                       record: SeasonRecord, answers: tuple[int, ...] | None = None
                       ) -> tuple[SeasonRecord, SeasonDetail]:
    year = simulate_year(deck, model, seed, k, you, record.squad, record.extra_missed, answers)
    season, yours, results = year.season, year.yours, year.results
    lines = season_lines(results, year.roster)
    record.missed = year.missed
    record.dropped = len(year.dropped)
    key = (YOU_ID, id(yours))
    me = lines.get(key) or Line(card=you, side=yours)
    lines[key] = me
    for name in ("matches", "innings", "runs", "balls", "outs", "fifties", "hundreds",
                 "highest", "wickets", "bowl_balls", "bowl_runs", "four_fors", "five_fors",
                 "best"):
        setattr(record, name, getattr(me, name))
    record.teams = len(season.sides)
    record.percentile = season_percentile(lines, key)
    record.display = season_display(deck, you.season_year, you, record.percentile)
    record.position = next(i for i, s in enumerate(season.table, 1) if s.side is yours)
    record.playoffs = record.position <= 4
    final = season.playoffs[-1]
    record.finalist = yours is final.home or yours is final.away
    record.champion = season.champion is yours
    top_bat, top_bowl = season_caps(lines)
    record.orange_cap = top_bat is me and me.runs > 0
    record.purple_cap = top_bowl is me and me.wickets > 0
    record.highlights = _highlights(results, yours)
    detail = season_detail(year)
    record.hat_tricks = sum("hattrick" in l.landmarks for l in detail.feed)
    detail.analysis = season_analysis(results, track=yours)
    detail.results = results
    return record, detail


# --- career milestones [A209] -----------------------------------------------------------------
# Firsts and round numbers, on the match they happened in. Counted from the season RECORDS
# before this one, never from milestones already stored: a career saved in a browser before
# milestones existed has none stored, and would otherwise fire "maiden fifty" for a veteran.

RUN_MARK = 1000           # every thousand career runs
WICKET_MARK = 50          # every fifty career wickets
MATCH_MARK = 50           # every fifty matches
_NTH = {2: "Second", 3: "Third", 4: "Fourth", 5: "Fifth", 6: "Sixth", 7: "Seventh",
        8: "Eighth", 9: "Ninth", 10: "Tenth"}


@dataclass
class CareerMilestone:
    key: str                  # "maiden_fifty", "runs", "title", ... (what kind, for the page)
    text: str
    match: int | None = None  # index into that season's feed; None for a season honour


def _marks(before: int, after: int, step: int) -> list[int]:
    return list(range((before // step + 1) * step, after + 1, step))


def career_milestones(previous: list[SeasonRecord], feed: list[MatchLine] | None,
                      record: SeasonRecord | None = None) -> list[CareerMilestone]:
    """This season's milestones, given the seasons before it. With `record` None (a season
    paused at a key moment) only the ones on matches already played: the season's honours
    are not known until it ends."""
    prior = lambda f: sum(getattr(r, f) for r in previous)
    matches, runs, wickets = prior("matches"), prior("runs"), prior("wickets")
    fifty_plus, hundreds = prior("fifties") + prior("hundreds"), prior("hundreds")
    four_plus, five_plus = prior("four_fors"), prior("five_fors")
    out: list[CareerMilestone] = []
    for i, l in enumerate(feed or []):
        if not l.played:
            continue
        add = lambda key, text: out.append(CareerMilestone(key, text, i))
        opp = l.opp_short
        matches += 1
        for m in _marks(matches - 1, matches, MATCH_MARK):
            add("matches", f"{m} IPL matches")
        if l.batted:
            score = f"{l.runs}{'' if l.out else '*'}"
            if l.runs >= 100 and hundreds == 0:
                add("maiden_hundred", f"Maiden IPL hundred: {score} v {opp}")
            elif 50 <= l.runs < 100 and fifty_plus == 0:
                add("maiden_fifty", f"Maiden IPL fifty: {score} v {opp}")
            fifty_plus += l.runs >= 50
            hundreds += l.runs >= 100
            for m in _marks(runs, runs + l.runs, RUN_MARK):
                add("runs", f"{m:,} IPL runs")
            runs += l.runs
        if l.bowl_balls:
            figures = f"{l.wickets}/{l.bowl_runs}"
            if l.wickets >= 5 and five_plus == 0:
                add("maiden_five_for", f"First five-wicket haul: {figures} v {opp}")
            elif l.wickets >= 4 and four_plus == 0:
                add("maiden_four_for", f"First four-wicket haul: {figures} v {opp}")
            four_plus += l.wickets >= 4
            five_plus += l.wickets >= 5
            for m in _marks(wickets, wickets + l.wickets, WICKET_MARK):
                add("wickets", f"{m} IPL wickets")
            wickets += l.wickets
        if "hattrick" in l.landmarks:
            add("hattrick", f"Hat-trick v {opp}")
    if record is None:
        return out
    if record.champion:
        n = prior("champion") + 1
        final = next((i for i in range(len(feed or []) - 1, -1, -1)
                      if feed[i].stage == "Final"), None)
        out.append(CareerMilestone("title", "IPL champions" if n == 1
                                   else f"{_NTH.get(n, f'{n}th')} IPL title", final))
    if record.orange_cap:
        out.append(CareerMilestone("orange_cap", f"Orange Cap: {record.runs:,} runs"))
    if record.purple_cap:
        out.append(CareerMilestone("purple_cap", f"Purple Cap: {record.wickets} wickets"))
    if record.display >= 90 and all(r.display < 90 for r in previous):
        out.append(CareerMilestone("elite", f"First 90+ season card: {record.display}"))
    if record.captain and not any(r.captain for r in previous):
        out.append(CareerMilestone("captain", f"Captain of {record.franchise}"))
    return out


@dataclass
class Pending:
    kind: str                          # "offer" | "event"
    year: int
    k: int
    squad: bool = False                # the season ahead is a squad season
    event_text: str | None = None      # the card's text, with any real name filled in
    moment: "Moment | None" = None     # a key moment the season is paused at (A204)
    offers: list[Offer] = field(default_factory=list)
    event: Event | None = None


@dataclass
class Summary:
    seasons: int
    matches: int
    runs: int
    balls: int
    outs: int
    fifties: int
    hundreds: int
    highest: int
    wickets: int
    bowl_balls: int
    bowl_runs: int
    four_fors: int
    titles: int
    finals: int
    playoffs: int
    orange_caps: int
    purple_caps: int
    captaincies: int
    regular_seasons: int
    franchises: list[str]
    earnings: int                      # lakh
    peak_display: int
    peak_year: int
    runs_rank: int | None = None       # 1 + real IPL players with more career runs
    wickets_rank: int | None = None
    five_fors: int = 0
    hat_tricks: int = 0
    milestones: list[tuple[int, str]] = field(default_factory=list)   # (year, text), A209


@dataclass
class Career:
    setup: Setup
    seed: int
    start: Card
    ceiling: Card
    seasons: list[SeasonRecord] = field(default_factory=list)
    pending: Pending | None = None
    retired: bool = False
    released: bool = False             # two poor seasons running: nobody signed you
    summary: Summary | None = None
    checkpoint: "Checkpoint | None" = None
    # The season played in THIS call, as it happened (A202); None when no season was played.
    detail: "SeasonDetail | None" = None
    # Its milestones (A209), each on its match in `detail.feed`. Kept apart from the detail,
    # which the season cache shares between careers that reached the same season by
    # different paths and so with different careers behind them.
    live_milestones: list[CareerMilestone] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.pending is None


def summarise(career: Career, real_runs: list[int] | None = None,
              real_wickets: list[int] | None = None) -> Summary:
    s = career.seasons
    peak = max(s, key=lambda r: (r.display, -r.year))
    franchises: list[str] = []
    for r in s:
        if r.franchise not in franchises:
            franchises.append(r.franchise)
    total = Summary(
        seasons=len(s), matches=sum(r.matches for r in s), runs=sum(r.runs for r in s),
        balls=sum(r.balls for r in s), outs=sum(r.outs for r in s),
        fifties=sum(r.fifties for r in s), hundreds=sum(r.hundreds for r in s),
        highest=max(r.highest for r in s), wickets=sum(r.wickets for r in s),
        bowl_balls=sum(r.bowl_balls for r in s), bowl_runs=sum(r.bowl_runs for r in s),
        four_fors=sum(r.four_fors for r in s), titles=sum(r.champion for r in s),
        finals=sum(r.finalist for r in s), playoffs=sum(r.playoffs for r in s),
        orange_caps=sum(r.orange_cap for r in s), purple_caps=sum(r.purple_cap for r in s),
        captaincies=sum(r.captain for r in s), regular_seasons=sum(not r.squad for r in s),
        franchises=franchises,
        earnings=sum(r.price for r in s), peak_display=peak.display, peak_year=peak.year,
        five_fors=sum(r.five_fors for r in s), hat_tricks=sum(r.hat_tricks for r in s),
        milestones=[(r.year, m.text) for r in s for m in r.milestones])
    if real_runs is not None:
        total.runs_rank = all_time_rank(total.runs, real_runs)
    if real_wickets is not None:
        total.wickets_rank = all_time_rank(total.wickets, real_wickets)
    return total


def all_time_rank(n: int, real: list[int]) -> int:
    """Where a career total would sit on a real all-time list: one more than the real
    players above it. The career card and the Hall of Fame (A210) both read it."""
    return 1 + sum(x > n for x in real)


# [A198] Real cards carry a reputation floor (A159): a season below the player's own career
# is lifted halfway back toward it. The career card applies the same rule, so a 91 here
# still sits among real 91s -- with the career so far as the reference, since a career in
# progress has no future seasons to look at. Real cards apply it to merit in runs; this
# applies it to the 70-99 card, the scale the career has. A161's cap ("never above the
# career") has nothing to do here: the floor is only taken when it beats the season's own
# card, and halfway between a career and a LOWER season is always below the career.
REPUTATION_WEIGHT = 0.5


def reputation_floor(previous: list[int], form: int) -> int:
    if not previous:
        return form
    career = statistics.fmean(previous)
    return max(form, round(REPUTATION_WEIGHT * career + (1 - REPUTATION_WEIGHT) * form))


def poor_bar(role: str) -> float:
    """Below this place among your kind, a season is poor: `POOR_PERCENTILE`, unless the
    role declares its own."""
    return ROLES[role].get("poor_percentile", POOR_PERCENTILE)


def form_after(squad: bool, percentile: float, bar: float | None = None) -> tuple[bool, bool]:
    """(next season is a squad season, released) after a season at this place among your
    kind of player. A poor season costs you your place; a second in a row costs you your
    career (A198)."""
    poor = percentile < (POOR_PERCENTILE if bar is None else bar)
    return poor, poor and squad


@dataclass
class Contract:
    club: str                             # canonical franchise
    price: int                            # lakh a year
    left: int                             # seasons of it still to play


@dataclass
class Checkpoint:
    """Everything the career loop carries from one season to the next, so a career can be
    resumed after its last completed season without replaying the seasons before it (A199).
    A season takes up to ~2 s on a cold production server, so a career of nineteen replayed
    per request would be unplayable; resuming is exactly equivalent, which a test pins."""

    k: int = 0
    permanent: float = 0.0
    current: str | None = None            # canonical franchise
    last_display: int | None = None
    squad: bool = False
    seasons: list[SeasonRecord] = field(default_factory=list)
    # How the career ended, or None while it goes on. Without it a finished career's
    # checkpoint resumes into another season, which the API test caught.
    ended: str | None = None              # "retired" | "released"
    contract: Contract | None = None      # a three-year deal in progress (A200)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Checkpoint":
        seasons = []
        for r in d["seasons"]:
            r = dict(r)
            r["best"] = tuple(r["best"])
            r["highlights"] = [Highlight(**h) for h in r["highlights"]]
            r["milestones"] = [CareerMilestone(**m) for m in r.get("milestones", [])]
            seasons.append(SeasonRecord(**r))
        contract = Contract(**d["contract"]) if d.get("contract") else None
        return cls(k=d["k"], permanent=d["permanent"], current=d["current"],
                   last_display=d["last_display"], squad=d["squad"], seasons=seasons,
                   ended=d.get("ended"), contract=contract)


def play_career(deck: Deck, model: Model, seed: int, setup: Setup, moves: list[str],
                facts=None, resume: Checkpoint | None = None) -> Career:
    """Replay a career from its setup and moves, stopping at the first decision with no
    recorded answer. Moves, in order: `o{i}` takes offer i (or `r` to retire, from the
    second season on), then `e{i}` answers the season's event when it has a choice.

    With `resume`, start from that checkpoint instead of the debut: `moves` are then only
    the moves made since it. `career.checkpoint` is always where the loop stood when it
    stopped, ready to be resumed from."""
    setup.check()
    start, ceiling = spin(deck, seed, setup.role)
    cp = copy.deepcopy(resume) if resume is not None else Checkpoint()
    career = Career(setup=setup, seed=seed, start=start, ceiling=ceiling,
                    seasons=cp.seasons)
    queue = list(moves)

    def nxt() -> str | None:
        return queue.pop(0) if queue else None

    def take_answers() -> tuple[int, ...]:
        """This season's answers at its key moments: the `m{i}` moves that follow its offer
        and event (A204)."""
        out = []
        while queue and isinstance(queue[0], str) and queue[0].startswith("m"):
            out.append(_index(queue.pop(0), "m", 3))
        return tuple(out)

    career.retired, career.released = cp.ended == "retired", cp.ended == "released"
    while cp.ended is None and cp.k < MAX_SEASONS and setup.debut + cp.k <= LAST_YEAR:
        k = cp.k
        year = setup.debut + k
        offers = offers_for(deck, seed, k, year, cp.current, cp.last_display, cp.contract)
        if cp.contract is not None and (len(offers) != 1 or not offers[0].contract_year):
            cp.contract = None                 # the club has no team this year: deal void
        career.checkpoint = copy.deepcopy(cp)
        move = nxt()
        if move is None:
            here = ability(start, ceiling, arc_point(k, cp.permanent, 0.0), setup=setup,
                           fs_id=offers[0].fs_id, year=year, franchise=offers[0].franchise)
            placed = []
            for o in offers:
                pos, rank = where_you_play(deck, dataclasses.replace(
                    here, fs_id=o.fs_id, franchise=o.franchise), o.fs_id)
                placed.append(dataclasses.replace(o, position=pos, attack_rank=rank))
            career.pending = Pending("offer", year, k, offers=placed, squad=cp.squad)
            return career
        if move == "r":
            if k == 0:
                raise CareerError("a career has to start before it can end")
            career.retired, cp.ended = True, "retired"
            break
        if move.startswith("c"):
            offer = offers[_index(move, "c", len(offers))]
            if offer.contract_year or offer.price3 is None:
                raise CareerError("that offer is not a three-year deal")
            cp.contract = Contract(canonical(offer.franchise), offer.price3, CONTRACT_YEARS)
            offer = dataclasses.replace(offer, price=offer.price3)
        else:
            offer = offers[_index(move, "o", len(offers))]
            if not offer.contract_year:
                cp.contract = None
        event = draw_event(seed, k, setup.overseas)
        if cp.squad and event.key in NOT_IN_A_SQUAD_SEASON:
            event = EVENTS[0]              # nobody asks a squad player to captain the side
        if len(event.options) == 1:
            option = event.options[0]
        else:
            move = nxt()
            if move is None:
                text = event.text
                if event.who:
                    probe = ability(start, ceiling, 0.0, setup=setup, fs_id=offer.fs_id,
                                    year=year, franchise=offer.franchise)
                    text = text.replace("{name}", event_name(deck, event, offer.fs_id, year,
                                                             probe) or "A senior player")
                career.pending = Pending("event", year, k, offers=[offer], event=event,
                                         squad=cp.squad, event_text=text)
                return career
            option = event.options[_index(move, "e", len(event.options))]
        if option.retire:
            career.retired, cp.ended = True, "retired"
            break
        cp.permanent += option.permanent
        t = arc_point(k, cp.permanent, option.season)
        you = ability(start, ceiling, t, setup=setup, fs_id=offer.fs_id, year=year,
                      franchise=offer.franchise)
        record = SeasonRecord(k=k, year=year, franchise=offer.franchise, short=offer.short,
                              price=offer.price, stayed=offer.stay, event=event.key,
                              choice=option.label, captain=option.captain, ability=t,
                              teams=0, squad=cp.squad, extra_missed=option.missed,
                              contract_year=(CONTRACT_YEARS - cp.contract.left + 1
                                             if cp.contract else 0))
        # Read the PLAYED record, never `record`: on a cache hit `record` is the blank one
        # this loop built, and the next season's offers would price you as a 70.
        answers = take_answers()
        try:
            played, career.detail = _cached_season(deck, model, seed, k, you, record,
                                                   (setup, start, ceiling, offer, option, t,
                                                    cp.squad), answers)
        except _NeedMoment as need:
            # The season stops at a key moment: told up to here, waiting for your call.
            so_far = Year(need.season, need.yours,
                          list(need.season.results) + list(need.season.playoffs or []),
                          need.roster, 0, need.approaches, need.dropped, need.verdicts)
            career.detail = season_detail(so_far, complete=False)
            career.live_milestones = career_milestones(cp.seasons, career.detail.feed)
            career.pending = Pending("moment", year, k, squad=cp.squad, offers=[offer],
                                     moment=need.moment)
            return career
        played.form = played.display
        played.display = reputation_floor([r.display for r in cp.seasons], played.display)
        played.milestones = career_milestones(
            cp.seasons, career.detail.feed if career.detail else None, played)
        career.live_milestones = played.milestones
        cp.seasons.append(played)
        cp.current, cp.last_display = canonical(offer.franchise), played.display
        cp.k += 1
        if cp.contract is not None:
            cp.contract.left -= 1
            if cp.contract.left == 0:
                cp.contract = None
        cp.squad, released = form_after(cp.squad, played.percentile, poor_bar(setup.role))
        if released and cp.contract is not None:
            released = False                   # a contract year remains: you stay, benched
        if released:
            career.released, cp.ended = True, "released"
            break
    if nxt() is not None:
        raise CareerError("moves left over after the career ended")
    career.checkpoint = copy.deepcopy(cp)
    career.pending = None
    real_runs = [p.runs for p in facts] if facts is not None else None
    real_wkts = [p.wickets for p in facts] if facts is not None else None
    career.summary = summarise(career, real_runs, real_wkts)
    return career


def _index(move: str, kind: str, n: int) -> int:
    if not isinstance(move, str) or not move.startswith(kind) or not move[1:].isdigit():
        raise CareerError(f"expected a {'n offer' if kind == 'o' else 'n event'} move, got {move!r}")
    i = int(move[1:])
    if not 0 <= i < n:
        raise CareerError(f"no choice {i} here")
    return i


# --- automatic players, for the CLI and phase 2's calibration ---------------------------


def policy_move(career: Career, policy: str, rng: random.Random) -> str:
    p = career.pending
    if p.kind == "moment":
        return f"m{rng.randrange(3) if policy == 'random' else 1}"
    if p.kind == "event":
        return f"e{rng.randrange(len(p.event.options)) if policy == 'random' else 0}"
    if policy == "money":
        i = max(range(len(p.offers)), key=lambda i: p.offers[i].price)
    elif policy == "strength":
        i = max(range(len(p.offers)), key=lambda i: p.offers[i].strength or 0)
    elif policy == "loyal":
        i = 0
    else:
        i = rng.randrange(len(p.offers))
        if p.offers[i].price3 is not None and not p.offers[i].contract_year \
                and rng.random() < 0.5:
            return f"c{i}"                     # a careless player signs long half the time
    return f"o{i}"


def auto_career(deck: Deck, model: Model, seed: int, setup: Setup, policy: str = "random",
                facts=None) -> tuple[Career, list[str]]:
    rng = random.Random(f"{seed}:policy")
    moves: list[str] = []
    while True:
        career = play_career(deck, model, seed, setup, moves, facts)
        if career.complete:
            return career, moves
        moves.append(policy_move(career, policy, rng))


# --- the command line -------------------------------------------------------------------


def _print(career: Career) -> None:
    from game.auction import crore

    st, ce = career.start, career.ceiling
    print(f"{career.setup.name}, {ROLES[career.setup.role]['label']}"
          f"{' (overseas)' if career.setup.overseas else ''}, debut {career.setup.debut}")
    print(f"  starts like {st.name} {st.season_year} ({st.display}); "
          f"ceiling {ce.name} {ce.season_year} ({ce.display})\n")
    for r in career.seasons:
        bat = f"{r.runs} runs ({r.balls}b, SR {100 * r.runs / r.balls:.0f})" if r.balls else "-"
        bowl = (f"{r.wickets} wkts, econ {6 * r.bowl_runs / r.bowl_balls:.2f}"
                if r.bowl_balls else "-")
        flags = " ".join(f for f, on in (("CHAMPIONS", r.champion), ("final", r.finalist and not r.champion),
                                          ("orange cap", r.orange_cap), ("purple cap", r.purple_cap),
                                          ("captain", r.captain)) if on)
        print(f"  {r.year} {r.short:9s} {crore(r.price):>9s}  card {r.display}  "
              f"ab {r.ability:+.2f}  {r.matches}m  {bat:26s} {bowl:20s} "
              f"{r.position}/{r.teams}  {flags}  [{r.event}: {r.choice}]")
    s = career.summary
    print(f"\n  {s.seasons} seasons, {s.matches} matches, {s.runs} runs, {s.wickets} wickets, "
          f"{s.titles} titles, {s.orange_caps} orange / {s.purple_caps} purple caps, "
          f"peak {s.peak_display} ({s.peak_year}), earned {crore(s.earnings)}")
    if s.runs_rank is not None:
        print(f"  all-time: {s.runs_rank} for runs, {s.wickets_rank} for wickets")


def main(argv: list[str] | None = None) -> int:
    import argparse
    import time

    from game.puzzle_facts import players_from, read_document as read_facts
    from tools.snapshot_deck import deck_from, model_from, read_document

    ap = argparse.ArgumentParser(description="Play a career with an automatic player.")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--role", default="opener", choices=sorted(ROLES))
    ap.add_argument("--debut", type=int, default=2010)
    ap.add_argument("--overseas", action="store_true")
    ap.add_argument("--name", default="You")
    ap.add_argument("--policy", default="random", choices=("random", "money", "strength", "loyal"))
    args = ap.parse_args(argv)
    doc = read_document()
    if doc is None:
        raise SystemExit("no deck snapshot; run tools.snapshot_deck")
    facts_doc = read_facts()
    facts = players_from(facts_doc) if facts_doc else None
    t0 = time.time()
    career, moves = auto_career(deck_from(doc), model_from(doc), args.seed,
                                Setup(args.name, args.role, args.debut, args.overseas),
                                args.policy, facts)
    _print(career)
    print(f"\n  moves {' '.join(moves)}  ({time.time() - t0:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
