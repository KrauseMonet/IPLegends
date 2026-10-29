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
import re
import statistics
from dataclasses import dataclass, field

from etl.feasibility import Card, Deck, team_rating
from etl.franchise_map import canonical
from game.analysis import SeasonAnalysis, batting_rank, bowling_rank, season_analysis
from game.auction import value_curve
from game.season import (
    DOUBLE_AT, Result, Side, _abbrev, balanced_order, fixtures, run_league, run_playoffs,
)
from game import career_incidents as incidents, national, press
from game.simulator import Model

# --- who you can be ------------------------------------------------------------------

YOU_ID = "career-you"                # your person_id; no real person id has a hyphenated word
FIRST_DEBUT = 2008
LAST_DEBUT = 2017                    # so every career can run at least ten seasons
LAST_YEAR = 2026
MAX_SEASONS = 19                     # a 2008 debut can run to 2026, as Kohli and Rohit did (A198)

TOP_SLOTS = frozenset({1, 2, 3})        # A76's batting roles, as `Card.positions` holds them
MIDDLE_SLOTS = frozenset({3, 4, 5})
FINISHER_SLOTS = frozenset({5, 6, 7})

# What each creation choice means, as a filter on real cards. The role and band are the
# archive's own (A26, A76); a template carries his band, batting positions and bowling style
# into your player, so an "opener" bats where real openers bat.
ROLES: dict[str, dict] = {
    # [A214] Batting roles filter on where the template REALLY batted that season (A76's
    # `positions`), not the squad `band`, which a few innings can move: an opener's ceiling
    # was drawn as a finisher's season, which reads as two careers cobbled together.
    # `blurb` is the create screen's one line per role [A219]. Every claim in it is a rule
    # in this file: the slots, the 15% bar, INJURY_RISK, ALLROUNDER_BOWL_SHARE.
    "opener":     {"label": "Opening batter", "role": ("batter",), "slots": TOP_SLOTS,
                   "blurb": "Bats 1 to 3: the new ball, and the most balls to score from."},
    "middle":     {"label": "Middle-order batter", "role": ("batter",), "slots": MIDDLE_SLOTS,
                   "blurb": "Bats 3 to 5 and holds the innings together."},
    # A finisher's templates are anybody who batted regularly in the finisher band, his
    # BATTING only (A198): only 22 real cards in nineteen years are batters there, too few
    # to spin from, and real finishers are mostly all-rounders. Your finisher still does not
    # bowl -- `ability` drops the bowling of a batting-only role.
    # [A198] A finisher only loses his place in the bottom 15% of his band, not 30%: ratified
    # by the user as a declared exception, because at the general bar 88-94% of finisher
    # careers ended in release -- realistic (real batting-only finishers rarely held a
    # place), but it made the role unplayable.
    "finisher":   {"label": "Finisher", "role": ("batter", "keeper", "allrounder"),
                   "slots": FINISHER_SLOTS, "batting_only": True, "poor_percentile": 0.15,
                   "blurb": "Bats 5 to 7, fast at the end. The hardest place to lose."},
    "keeper":     {"label": "Wicketkeeper-batter", "role": ("keeper",),
                   "blurb": "Keeps wicket and bats. Every side needs one."},
    "pace":       {"label": "Fast bowler", "role": ("bowler",), "style": "pace",
                   "blurb": "Pace, up to four overs a match. The most injury-prone job."},
    "spin":       {"label": "Spinner", "role": ("bowler",), "style": "spin",
                   "blurb": "Spin, up to four overs a match, and fewer injuries than a quick."},
    "allrounder": {"label": "All-rounder", "role": ("allrounder",),
                   "blurb": "Bats, and bowls in about six matches in ten."},
}

# A33's floors: below them a season's numbers are noise, so it cannot be a template and
# does not count as a "regular" when reading a real year's card distribution.
BAT_REGULAR_BALLS = 100
BOWL_REGULAR_BALLS = 150

# The two spins. Declared game design, tuned by phase 2's calibration rather than fitted:
# a start is an ordinary regular's season, a ceiling anything from good to the best ever.
# Drawn uniformly over the real seasons in range, so how rare a 99 ceiling is comes from
# how rare 99s really are.
# [A214] A start is a YOUNGSTER's level, 70-75 (it was 72-80, so a debutant arrived at
# about 79, already an established player, with ten points to grow).
START_DISPLAY = (70, 75)
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

# [A214] Your RATING: the ability the engine plays, on the card scale, and nothing else. It
# runs from the start's card (`t = 0`) to the ceiling's (`t = 1`) and on down past the start
# as a career declines, so it moves only with age, events and development -- never with a
# season's luck, which is the Season grade's job (the 70-99 card `season_display` gives).
# Measured before it existed: the season card follows ability at a correlation of 0.39 over
# 402 simulated seasons, with luck (SD 6.6) larger than the whole rise from start to peak.
RATING_FLOOR = 60
# What you did feeds what you become, a little: the best season of your kind adds this many
# Rating points to the next, the worst takes as many away, the middle nothing.
DEV_POINTS = 1.0
# ...but only while you are still growing: past the ARC's peak a good season does not make you
# better. Counted every season it ran a consistently good player along the ceiling for nine
# seasons, the decline held off by points banked from form.
DEV_SEASONS = 6
# [A224] Your POTENTIAL grows, so a low ceiling is where a career starts rather than a verdict
# on it. Measured before building, over 35 careers: 21% of seasons already graded above the
# potential (38% under a potential of 85), most of it a season's luck, so the bar is a
# MARGIN over the potential, read off the season's own grade before the reputation floor.
# While you are still growing, a season that clears it adds a step; the bar then rises with
# the potential, which is what stops it feeding on itself. Declared game design.
POTENTIAL_MARGIN = 3                 # the season's own grade must beat your potential by this
POTENTIAL_BIG = 8                    # ...and by this for the bigger step
POTENTIAL_STEP = 1
POTENTIAL_BIG_STEP = 2
POTENTIAL_MAX = 8                    # Rating points of potential a career can earn
# [A224] The SHAPE of a career: when it peaks and how fast it declines, drawn at the spin
# from the seed and never shown. Real players with nine or more seasons peak anywhere from
# their first season to their last (median 0.38 of the way through) and fall 1 to 10 points
# from their peak, so one curve for everybody was the wrong model. Each shape stretches or
# squeezes the standard ARC rather than replacing it: `standard` x `standard` IS the ARC.
PEAK_SEASONS = {"early": 3, "standard": 5, "late": 7}         # seasons to the peak
DECLINE_SPEED = {"gentle": 0.7, "standard": 1.0, "steep": 1.4}  # ARC seasons per season
PEAK_WEIGHTS = {"early": 1, "standard": 2, "late": 1}
DECLINE_WEIGHTS = {"gentle": 1, "standard": 2, "steep": 1}
# [A224] ...and a CLOCK that form moves: past the peak, a season graded this far above your
# Rating ages you half a season, and a poor one (POOR_PERCENTILE) a season and a half.
CLOCK_MARGIN = 3
CLOCK_SLOW = 0.5
CLOCK_FAST = 1.5
DEBUT_AGE = 19                      # shown only: a career's age is its debut age plus seasons
# Franchises pay for what you are AND what you just did: an offer is priced on this blend.
MARKET_FORM_WEIGHT = 0.4
# A youngster's first two seasons cannot cost him his place (ratified with the lower start).
YOUTH_SEASONS = 2
# Nobody lets a star go: at this Rating two poor seasons still cost you your place, but some
# club always signs you. Found by playing one: a fast bowler rated 96 was released.
STAR_RATING = 85

# [A198] How much of the team's season you play, drawn per season from the real
# distribution: the share of their team's matches that players with ten or more seasons
# played in seasons they were REGULARS (A33's floors), 838 seasons, 20 quantiles (5%, 10%,
# ... 100%). Real regulars play every match in 42% of seasons and 86% of matches on average.
# Regular seasons, not all seasons, because your player is always selected: a real long
# career's fringe years come from not being good enough, which the ability curve decides,
# not this. The missed matches are one block of the league (an injury), never the playoffs.
AVAILABILITY_SHARES = (0.25, 0.571, 0.643, 0.706, 0.75, 0.786, 0.812, 0.857, 0.875, 0.929,
                       0.933, 0.938, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)

# [A216] ...and by how long you have been at your club, which the user pointed out and the
# archive bears out: real regulars play every match in 26% of their first seasons at a club
# (Indian; overseas 16%) and in 52% from their third (overseas 35%), and miss three or more
# of fourteen in 42% against 20%. Ratified: a player SETTLED at his club -- SETTLED_SEASONS
# there before this one -- loses nothing to chance; only an injury card (mostly pacers) or a
# make-or-break drop keeps him out. His first and second seasons draw from the real players
# in theirs, measured the same way as the table above (regular seasons by A33's floors, a
# team's matches as the denominator, 20 quantiles) over every regular season, keyed on
# seasons in a row at the same franchise (any squad) and passport: 381 / 316 first seasons,
# 260 / 163 second. The table above stays for older callers with no Rating.
SETTLED_SEASONS = 2
SETTLED_SHARES = (1.0,)
AVAILABILITY_BY_TENURE = {
    (0, False): (0.471, 0.562, 0.588, 0.643, 0.688, 0.714, 0.733, 0.786, 0.812, 0.857,
                 0.867, 0.929, 0.929, 0.938, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
    (0, True): (0.429, 0.5, 0.5, 0.562, 0.571, 0.625, 0.643, 0.688, 0.733, 0.75,
                0.786, 0.8, 0.812, 0.867, 0.895, 0.929, 1.0, 1.0, 1.0, 1.0),
    (1, False): (0.5, 0.588, 0.647, 0.706, 0.714, 0.769, 0.786, 0.846, 0.875, 0.929,
                 0.929, 0.938, 0.941, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
    (1, True): (0.438, 0.5, 0.571, 0.643, 0.667, 0.706, 0.714, 0.786, 0.812, 0.846,
                0.857, 0.923, 0.929, 0.933, 0.938, 1.0, 1.0, 1.0, 1.0, 1.0),
}
# [A216] Ratified by the user over the measured tail: a star does not lose a big chunk of a
# season to chance, even in his first seasons at a club. At this Rating the random absence
# is at most this share of the league; only an event card can keep him out longer.
STAR_AVAILABILITY_RATING = 88
STAR_MAX_ABSENCE = 0.25
# [A216] Why you missed the matches you missed. Declared, since the archive records no
# reasons: an overseas player's absence is international duty half the time (at the start of
# the season or the end of it). Only a player new to his club, or a squad player, has one.
OVERSEAS_DUTY = 0.5
# [A226] ...and otherwise it is SELECTION, which is what it measures: the block's length comes
# from how many matches real regulars new to a club played, and real players miss those
# mostly by being left out -- team balance, the overseas combination, rotation -- not by being
# hurt. A216 labelled every block an injury, an illness or a family matter, which gave a
# median of five "injuries" a career (up to twelve, 168 careers measured) and a fifteen-match
# illness. Injuries now come from the injury cards alone. Illness and a family matter remain,
# but only as the reason for a SHORT absence (`SHORT_ABSENCE` matches or fewer), as do rests.
SHORT_ABSENCE = 2
SHORT_REASONS = (("rested", 0.30), ("illness", 0.15), ("a family matter", 0.10))
LEFT_OUT = ("team balance", "rotation")
LEFT_OUT_OVERSEAS = ("the overseas combination", "team balance")
NOT_SELECTED = "not selected"        # a squad season's absence: he is not in the eleven

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
    def __init__(self, moment: Moment | None, season, yours: Side, approaches: list,
                 roster: dict | None = None, dropped: list | None = None,
                 verdicts: dict | None = None, absent: list | None = None,
                 absence_reason: str | None = None, prompt: dict | None = None,
                 incident_out: dict | None = None, incident: dict | None = None):
        self.moment, self.season, self.yours = moment, season, yours
        self.approaches = approaches
        self.roster, self.dropped, self.verdicts = roster or {}, dropped or [], verdicts or {}
        self.absent, self.absence_reason = absent or [], absence_reason
        # [A230] A mid-season incident instead of a key moment: {key, title, text, options}
        self.prompt = prompt
        self.incident_out = incident_out or {}
        self.incident = incident                   # an incident already answered this season


# --- the market ------------------------------------------------------------------------

OFFERS = 3                           # franchises bidding each season, besides your own
DEBUT_PRICE = 20                     # lakh: an uncapped debutant's base price
PRICE_NOISE = 0.20                   # each franchise's view of you varies by +/- this
STAY_DISCOUNT = 0.9                  # staying put costs a little money
PRICE_CAP = 2700                     # lakh: the real record (Pant, 2025)

# --- event cards -----------------------------------------------------------------------
# One per season, before it starts, drawn from the seed and the setup. Declared game design.
# [A214] Effects are in RATING POINTS, turned into ability at your own scale (`points_t`).
# [A230] Every card is a decision made in the off-season, and none is worded in numbers: an
# option carries a HINT in the voice of somebody at the club, and a STORY the season card
# tells afterwards. Injuries and slumps are no longer cards: they happen DURING a season, set
# off by a real match (`game.career_incidents`). An answer can also move your STANDING with
# the club and start a THREAD a later season remembers; a follow-up card (`FOLLOW_UPS`) can
# take a season's place when one comes due, which reverses A197's rule that an answer never
# changes which card comes next -- ratified with the user, for the sake of arcs.


@dataclass(frozen=True)
class Outcome:
    """One way an answer can turn out [A214]. An option's outcomes have chances summing to 1."""
    chance: float
    permanent: int = 0                  # Rating points, for good
    missed: int = 0                     # extra league matches missed
    text: str = ""                      # what the season card says when it lands
    injury: str = ""                    # a line for the injury record, if it is one
    next_season: bool = True            # lands next season, or this one
    season: int = 0                     # [A230] Rating points, this season only
    standing: int = 0                   # [A230]


@dataclass(frozen=True)
class EventOption:
    label: str
    season: int = 0                     # Rating points, this season only
    permanent: int = 0                  # Rating points, for good
    captain: bool = False
    missed: int = 0                     # extra league matches missed this season (A200)
    retire: bool = False                # ends the career before the season is played
    outcomes: tuple[Outcome, ...] = ()  # [A214]
    story: str = ""                     # [A214] what the season card says about the choice
    injury: str = ""                    # [A214] a line for the injury record
    age: float = 0.0                    # [A224] seasons added to the career clock (minus: held back)
    hint: str = ""                      # [A230] what it means, in a voice, never in numbers
    standing: int = 0                   # [A230] how the club sees you
    money: bool = False                 # [A230] an endorsement fee (`endorsement_fee`)
    trade: bool = False                 # [A230] take the trade your club has agreed
    thread: str = ""                    # [A230] something a later season remembers
    tone: str = ""                      # [A230] 'up' | 'down' | '' for the season card's mark


@dataclass(frozen=True)
class Event:
    key: str
    title: str
    text: str
    weight: int
    options: tuple[EventOption, ...]
    # [A200] "{name}" in the text is a REAL player: "mentor" the best of your own kind in
    # your franchise's real squad that year, "rival" the best player in another team that
    # year, "pupil" (A230) the debutant in your squad who would go furthest. Never a
    # captain: captains are not in the archive. "{club}" is a trade's club.
    who: str | None = None
    # [A200] Who can draw the card: "late" only from LATE_FROM seasons on, "overseas" only
    # an overseas player, "veteran" from VETERAN_FROM, "senior" from SENIOR_FROM (A230).
    # "staying" and "capped" depend on the season, not the seed: a card that does not fit
    # the season gives way to `fallback_event`.
    when: str | None = None
    injury: bool = False                # kept for old records; no card is an injury now (A230)


EVENTS: tuple[Event, ...] = (
    Event("quiet", "A quiet pre-season", "Nothing to report. Just cricket.", 1,
          (EventOption("Get on with it"),)),
    Event("form", "Flying in the nets",
          "A big season back home, and you arrive playing as well as you ever have.", 4,
          (EventOption("Make your case to the captain", season=2, standing=-1, tone="up",
                       hint="Tell him you are ready for more. Not everyone likes being told.",
                       story="You told the captain you were ready for more, and backed it up."),
           EventOption("Let your cricket do the talking", season=1, standing=1, tone="up",
                       hint="They will notice soon enough.",
                       story="You kept quiet and let your cricket speak."))),
    Event("captain", "The captaincy", "The franchise asks you to lead the side.", 2,
          (EventOption("Take the armband", season=-1, captain=True, standing=1,
                       hint="Lead the side. It will take something from your own game at first.",
                       story="The armband weighed on your own game a little at first."),
           EventOption("Not yet", hint="Your own game comes first, for now.",
                       story="You turned the armband down, for now."))),
    Event("coach", "A new coach", "A new coach wants to rebuild your technique from scratch.", 3,
          (EventOption("Rebuild it", season=-3, tone="down",
                       hint="A hard season for a better game, if it works.",
                       story="Rebuilding your technique cost you this season.",
                       outcomes=(Outcome(0.65, permanent=5,
                                         text="The rebuild paid off: the new technique is yours now."),
                                 Outcome(0.35, permanent=1, text="The rebuild never quite took."))),
           EventOption("Stick with what works", hint="It got you here.",
                       story="You thanked the coach and kept your own game."))),
    Event("mentor", "A mentor", "{name} offers to work with you through the season.", 2,
          (EventOption("Learn from him", season=-1, permanent=3, thread="mentor", tone="up",
                       hint="Give up a little of this season to learn from the best.",
                       story="Time with {name} took a little from this season and added to every one after."),
           EventOption("Do it your way", hint="You know your own game.",
                       story="You thanked {name} and did it your way.")), who="mentor"),
    Event("rivalry", "A rivalry", "The papers have built up your duel with {name}.", 2,
          (EventOption("Talk it up", hint="Give the papers their story, and then live up to it.",
                       story="You talked up the duel with {name}.",
                       outcomes=(Outcome(0.6, season=2, next_season=False,
                                         text="It brought out your best."),
                                 Outcome(0.4, season=-2, next_season=False,
                                         text="It got into your head."))),
           EventOption("Keep your head down", hint="Do not give him anything.",
                       story="You refused to bite.")), who="rival"),
    Event("endorsement", "A sponsor calls",
          "A sportswear brand wants you for a campaign shoot. It falls in the week of the "
          "pre-season camp.", 2,
          (EventOption("Do the shoot", season=-1, standing=-1, money=True, tone="even",
                       hint="Good money. The camp will go on without you.",
                       story="You did the shoot and missed the camp. The money was good."),
           EventOption("Go to the camp", standing=1, hint="The money will come if the runs do.",
                       story="You turned the brand down and went to camp."))),
    Event("trade", "A trade", "Your club has agreed to trade you to {club}. It only needs your yes.", 2,
          (EventOption("Go to {club}", trade=True, hint="A fresh start somewhere that wants you.",
                       story="You agreed to the trade."),
           EventOption("Stay put", standing=-1, tone="down",
                       hint="Tell them you are going nowhere. They were ready to let you go.",
                       story="You turned the trade down. The club kept you, but it had been ready to let you go.")),
          who="trade", when="staying"),
    Event("youngster", "A youngster", "{name}, in his first IPL season, asks if he can shadow you.", 2,
          (EventOption("Take him under your wing", season=-1, standing=1, thread="pupil", tone="even",
                       hint="Time spent on him is time not spent on you.",
                       story="You took {name} under your wing."),
           EventOption("Keep your focus on your own game", hint="Your place is not safe either.",
                       story="You kept your focus on your own game.")), who="pupil", when="senior"),
    Event("workload", "The national board calls",
          "Your national board wants to manage your workload, and asks you to sit out the IPL's "
          "first fortnight.", 3,
          (EventOption("Agree", missed=3, age=-0.5, tone="up",
                       hint="Rest now, and last longer.",
                       story="You sat out the first fortnight on the board's orders, and felt fresher for it."),
           EventOption("Tell them you want to play", standing=1, hint="Your club needs you from day one.",
                       story="You turned the board down and played from the first match.")),
          when="capped"),
    Event("home_league", "A league back home",
          "Your home board wants you for its own T20 league, which runs into the IPL's opening weeks.", 2,
          (EventOption("Skip it", standing=1, hint="The IPL comes first.",
                       story="You skipped the league at home."),
           EventOption("Play there first", permanent=1, missed=4, standing=-1,
                       hint="A month of cricket at home, and four IPL matches missed.",
                       story="A month of cricket at home sharpened you, at the cost of four IPL matches.")),
          when="overseas"),
    # [A224] A decision about how long you last, offered only to a senior player.
    Event("fitness", "A fitness programme", "A fitness coach offers a punishing off-season "
          "programme to keep you going for longer.", 1,
          (EventOption("Commit to it", season=-1, age=-0.5, tone="up",
                       hint="Hard months now, more seasons later.",
                       story="The off-season programme left you heavy-legged this year, and younger for the next."),
           EventOption("Not this year", hint="Your body knows what it needs.",
                       story="You gave the programme a miss.")), when="veteran"),
    Event("one_more", "One more season?", "You are past your best, and the franchise still wants you.", 3,
          (EventOption("One more year", hint="There is a little left in the legs."),
           EventOption("Retire on a high", retire=True, hint="Go out while they still want you.")),
          when="late"),
)

# [A230] Cards a season can bring because of what came before, in place of its own.
FOLLOW_UPS: dict[str, Event] = {e.key: e for e in (
    Event("press_feud", "The papers have not forgotten",
          "A new season, and the columnist you hit back at is still writing about you.", 0,
          (EventOption("Make peace with him", standing=1, tone="up",
                       hint="Buy him a coffee. Make it go away.",
                       story="You cleared the air, and the story went away."),
           EventOption("Let your bat do the talking", hint="Make him eat his words.",
                       story="You let your cricket answer him.",
                       outcomes=(Outcome(0.5, season=2, next_season=False,
                                         text="He had to eat his words."),
                                 Outcome(0.5, season=-2, next_season=False,
                                         text="Every failure became another column."))))),
    Event("lead", "The dressing room's choice",
          "The players want you to lead them, and the club agrees.", 0,
          (EventOption("Take the armband", season=-1, captain=True, standing=1,
                       hint="They asked for you. That counts for something.",
                       story="You took the armband the dressing room offered you."),
           EventOption("Not yet", hint="Your own game comes first, for now.",
                       story="You turned the armband down, for now."))),
)}

LATE_FROM = 10                       # "One more season?" from the eleventh season on
VETERAN_FROM = 7                     # [A224] the fitness programme from the eighth
SENIOR_FROM = 5                      # [A230] a youngster asks from the sixth
TRADE_FROM = 2                       # [A230] a trade from the third

# [A230] Injury risk by role now lives with the mid-season incidents, as a chance a season.
INJURY_RISK = incidents.INJURY_RISK


# Cards that make no sense for a man who has lost his place: in a squad season they give
# way (A199). Found in the page: "Captain" beside "Squad player, 5 matches".
NOT_IN_A_SQUAD_SEASON = frozenset({"captain", "trade", "workload"})

# How often a card may come up in one career. Unlisted: no limit beyond no-repeats.
EVENT_LIMIT = {"coach": 2, "captain": 2, "mentor": 2, "fitness": 2, "trade": 1,
               "endorsement": 3, "youngster": 2, "workload": 3, "rivalry": 3, "form": 4,
               "quiet": 2}
FALLBACK = ("form", "coach", "rivalry", "endorsement", "mentor")

# [A230] How the club sees you: a hidden number, never shown as one. A move to a new club
# starts it again. Declared game design.
STANDING_MIN, STANDING_MAX = -3, 3
STANDING_BACKED = 2                  # the club keeps you as a regular after a poor season
STANDING_COLD = -2                   # ... or does not offer to keep you at all
STANDING_LEAD = 3                    # the dressing room asks you to lead
STANDING_WORDS = {3: "The club sees you as one of its own", 2: "The club trusts you",
                  1: "Well thought of at the club", -1: "Some at the club have doubts",
                  -2: "Relations with the club are strained", -3: "The club wants you gone"}
RETAIN_TRUSTED = 3                   # a trusted player is retained this far below RETAIN_CARD
PUPIL_STAR = 85                      # a youngster you helped has made it at this card


def endorsement_fee(rating: int) -> int:
    """A sponsor's fee in lakh, by your Rating: 25L at 70, 125L at 90. Declared."""
    return max(25, 25 + 5 * (rating - 70))


HOME_MAX = 24
AMBITIONS_MAX = 3
# [A228] What a career can set out to do, each checked against the season records alone.
# key -> (label, check over the seasons so far). A target reached is a milestone and a
# clipping in the season it falls; `one_club` can also be broken, by a move.
AMBITIONS: dict[str, tuple[str, object]] = {
    "title": ("Win the IPL", lambda ss: any(r.champion for r in ss)),
    "titles3": ("Win three IPL titles", lambda ss: sum(r.champion for r in ss) >= 3),
    "orange": ("Win the Orange Cap", lambda ss: any(r.orange_cap for r in ss)),
    "purple": ("Win the Purple Cap", lambda ss: any(r.purple_cap for r in ss)),
    "captain": ("Captain your franchise", lambda ss: any(r.captain for r in ss)),
    "hundred": ("Score an IPL hundred", lambda ss: any(r.hundreds for r in ss)),
    "five_for": ("Take a five-wicket haul", lambda ss: any(r.five_fors for r in ss)),
    "runs4000": ("Score 4,000 IPL runs", lambda ss: sum(r.runs for r in ss) >= 4000),
    "wickets150": ("Take 150 IPL wickets", lambda ss: sum(r.wickets for r in ss) >= 150),
    "matches150": ("Play 150 IPL matches", lambda ss: sum(r.matches for r in ss) >= 150),
    "pom10": ("Ten Player of the Match awards", lambda ss: sum(r.poms for r in ss) >= 10),
    "one_club": ("Ten seasons at one club", lambda ss: len(ss) >= 10 and one_club(ss)),
    "international": ("Play for your country", lambda ss: any(r.national == "debut" for r in ss)),
}


def one_club(seasons: list) -> bool:
    return len({canonical(r.franchise) for r in seasons}) <= 1


def ambition_status(key: str, seasons: list) -> tuple[str, int | None]:
    """("done", the year it was reached) | ("broken", the year it was lost) | ("open", None)."""
    check = AMBITIONS[key][1]
    for i in range(len(seasons)):
        if check(seasons[:i + 1]):
            return "done", seasons[i].year
    if key == "one_club":
        for i in range(len(seasons)):
            if not one_club(seasons[:i + 1]):
                return "broken", seasons[i].year
    return "open", None


class CareerError(ValueError):
    """A setup or a move that does not describe a playable career."""


# --- setup and templates ---------------------------------------------------------------


@dataclass(frozen=True)
class Setup:
    name: str
    role: str
    debut: int
    overseas: bool = False
    # [A228] Who you are, for the papers only: a shirt number, a home town, a celebration,
    # and up to three ambitions the career card keeps score of. None of it touches a match.
    number: int | None = None
    home: str = ""
    celebration: str = ""
    ambitions: tuple[str, ...] = ()
    # [A229] The country an overseas career plays for, "" for none. An Indian career plays
    # for India and leaves this empty.
    country: str = ""

    @property
    def nation(self) -> str:
        return national.nation(self.overseas, self.country)

    def check(self) -> None:
        if self.role not in ROLES:
            raise CareerError(f"no such role {self.role!r}")
        if not FIRST_DEBUT <= self.debut <= LAST_DEBUT:
            raise CareerError(f"debut must be {FIRST_DEBUT}-{LAST_DEBUT}")
        if not self.name.strip():
            raise CareerError("a player needs a name")
        # [A215] A legend's name reaches other people's screens in a friends' room (A152's
        # rule for room names): no angle brackets, no control characters.
        for text in (self.name, self.home):
            if any(ch in "<>" or ord(ch) < 32 or ord(ch) == 127 for ch in text):
                raise CareerError("a name cannot contain < or > or control characters")
        if self.number is not None and not 0 <= self.number <= 99:
            raise CareerError("a shirt number is 0 to 99")
        if len(self.home) > HOME_MAX:
            raise CareerError(f"a home town is at most {HOME_MAX} characters")
        if self.celebration and self.celebration not in press.CELEBRATIONS:
            raise CareerError(f"no such celebration {self.celebration!r}")
        if len(self.ambitions) > AMBITIONS_MAX or len(set(self.ambitions)) != len(self.ambitions):
            raise CareerError(f"choose up to {AMBITIONS_MAX} different ambitions")
        for a in self.ambitions:
            if a not in AMBITIONS:
                raise CareerError(f"no such ambition {a!r}")
        if self.country and (not self.overseas or self.country not in national.OVERSEAS_NATIONS):
            raise CareerError(f"an overseas player picks a country from {', '.join(national.OVERSEAS_NATIONS)}")
        if "international" in self.ambitions and not self.nation:
            raise CareerError("pick a country to aim to play for it")


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
    if "slots" in spec and c.positions != spec["slots"]:
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
            year: int, franchise: str | None, role: dict | None = None) -> Card:
    """Your player for one season: the start template moved `t` of the way to the ceiling.
    `t = 0` is the start's engine numbers exactly and `t = 1` the ceiling's. The start
    decides which disciplines you have and where you bat; the ceiling only moves numbers
    you already have. A batting-only role keeps the batting and drops the rest. `role` is
    a changed role (A238, `role_state`); None plays the role you were created in."""
    if ROLES[setup.role].get("batting_only"):
        start = dataclasses.replace(start, role="batter", bowl=None, bowl_scoring=None,
                                    bowl_dismissal=None)
    return apply_role(_ability(start, ceiling, t, setup=setup, fs_id=fs_id, year=year,
                               franchise=franchise), role, t)


def _ability(start: Card, ceiling: Card, t: float, *, setup: Setup, fs_id: int,
             year: int, franchise: str | None) -> Card:
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


# --- changing role [A238] ------------------------------------------------------------------
# A batter who bowls well can become an all-rounder, a bowler who bats well can move up and
# become one, and an all-rounder can become a full-time bowler. Every change is an OFFER
# FROM THE TEAM COACH, which the player may turn down. Measured in the archive first: real
# role changes are mostly how a player was USED that year (38% revert the next season), an
# all-rounder becomes a bowler when his batting fades rather than when his bowling shines
# (47% of the worst batters against 27% of the best), and a tail-ender's batting proves
# nothing (1-3 balls a match). So the way in is a TRIAL SEASON the coach offers, judged on
# the new discipline alone; a good trial brings the full-time offer; a poor season in the
# new role sends you back. Every constant here is declared game design.
COACH_ON = True                      # tests of rules written before A238 turn the coach off
COACH_TRIAL_CHANCE = 0.25            # an eligible season's chance of the coach offering a trial
COACH_TRIALS_MAX = 2                 # trial offers a career, taken or not
COACH_TRIAL_FROM, COACH_TRIAL_UNTIL = 1, 9   # career seasons (0-based) a trial can be offered in
# A trial passes when it was not a poor season in the new discipline: the way back's own bar,
# so a trial can never pass at a level that would send a full-time player back. Eased from
# 0.5 at the user's request after measuring 494 trial seasons: 9% passed at 0.5, 21% at 0.3.
TRIAL_PASS = 0.3
COMING_ON = 0.5                      # a season in the top half: the new skill is coming on
SPECIALISE_BOWL = 0.6                # an all-rounder bowling this well is asked to specialise...
FADING_BAT, FADING_BOWL = 0.3, 0.4   # ...as is one whose batting fades while his bowling holds
WAY_BACK = 0.3                       # POOR_PERCENTILE's bar, on the discipline the change was about
SECOND_STEP, SECOND_FORM = 0.25, 0.2 # how fast a new discipline grows toward its second ceiling;
                                     # a passed trial counts as a season of it, and the first
                                     # full-time season cannot send you back (the grace)
TRIAL_ROLES = ("opener", "middle", "pace", "spin")   # finisher and keeper do not change (scope)
# A FULL-TIME BOWLER bowls his four overs every match and keeps his place in the batting
# order: an all-rounder who is never rested from the attack, which is what the user meant by
# the coach's bowling offer. (First built as a bowler batting eight to eleven, and corrected.)
FULL_TIME = "fulltime"
FULL_TIME_LABEL = "Full-time bowler"


def role_kind(role: str) -> str:
    """'batter', 'bowler' or 'allrounder' for a ROLES key (a keeper bats)."""
    return {"pace": "bowler", "spin": "bowler", "allrounder": "allrounder",
            FULL_TIME: "allrounder"}.get(role, "batter")


def role_label(role: str) -> str:
    """A role's name for the page: a ROLES key, or a full-time bowler."""
    return FULL_TIME_LABEL if role == FULL_TIME else ROLES[role]["label"]


def bat_value(scoring: float, dismissal: float) -> float:
    """A batting half's runs a ball over average, the dismissal priced at the engine's wicket."""
    return scoring - (dismissal - 1) * LEAGUE_OUT_RATE * WICKET_RUNS


def bowl_value(scoring: float, dismissal: float) -> float:
    return scoring + (dismissal - 1) * LEAGUE_OUT_RATE * WICKET_RUNS


_PART_TIME: dict[int, tuple[float, float]] = {}
_SECOND_POOL: dict[tuple, list[Card]] = {}


def part_time_bowling(deck: Deck) -> tuple[float, float]:
    """Where a batter who never bowled starts with the ball: the pooled bowling of the real
    part-timers (batters and keepers who bowled), weighted by balls. A48's rule on the other
    discipline -- the pooled level of those like him, never league average (A23). Measured
    from the deck: about -0.06 runs a ball and 5% fewer wickets than an average bowler."""
    hit = _PART_TIME.get(id(deck))
    if hit is None:
        cards = [c for cs in deck.cards_by_fs.values() for c in cs
                 if c.role in ("batter", "keeper") and c.bowl_scoring is not None and c.bowl_balls]
        balls = sum(c.bowl_balls for c in cards)
        hit = _PART_TIME[id(deck)] = (sum(c.bowl_scoring * c.bowl_balls for c in cards) / balls,
                                      sum(c.bowl_dismissal * c.bowl_balls for c in cards) / balls)
    return hit


def finisher_batting(model: Model, year: int) -> tuple[float, float]:
    """Where a bowler who moves up to six or seven starts with the bat: the finisher band's
    pooled level (A48), centred on the season as the engine's own `bat_split` does. His own
    batting numbers are measured against tail-end par, so at six they would play him as an
    average number six (A208)."""
    raw, dismissal = model.unrated_split["finisher"]
    return raw - model.season_mean_scoring[("batting", year)], dismissal


def _second_pool(deck: Deck, add: str, style: str | None) -> list[Card]:
    key = (id(deck), add, style)
    hit = _SECOND_POOL.get(key)
    if hit is None:
        if add == "bowl":
            cards = [c for cs in deck.cards_by_fs.values() for c in cs
                     if c.role in ("bowler", "allrounder") and c.bowl_scoring is not None
                     and (c.bowl_balls or 0) >= BOWL_REGULAR_BALLS and c.bowling_style == style]
        else:
            cards = [c for cs in deck.cards_by_fs.values() for c in cs if _fits(c, "finisher")]
        hit = _SECOND_POOL[key] = sorted(cards, key=lambda c: (c.season_year, c.fs_id, c.person_id))
    return hit


def second_ceiling(deck: Deck, seed: int, add: str, style: str | None,
                   base: tuple[float, float]) -> tuple[float, float]:
    """How good the new discipline can get: a third spin, from the real seasons of that job
    (regular bowlers of your style, or batters who batted regularly at five to seven) that
    are better at it than where you start. Every number you play is a real player's (A197).
    From the seed alone, so it is the same every time it is asked."""
    value = bowl_value if add == "bowl" else bat_value
    halves = ((lambda c: (c.bowl_scoring, c.bowl_dismissal)) if add == "bowl"
              else (lambda c: (c.bat_scoring, c.bat_dismissal)))
    better = [c for c in _second_pool(deck, add, style) if value(*halves(c)) > value(*base)]
    if not better:
        return base
    return halves(random.Random(f"{seed}:second:{add}:{style}").choice(better))


def role_state(deck: Deck, model: Model, seed: int, setup: Setup, start: Card, role: str,
               second: float, style: str, year: int, trial: bool = False) -> dict | None:
    """The changed role you play this season, as a plain dict a season record can keep, or
    None for the role you were created in. Where you bat follows the job: an all-rounder
    made from a batter keeps his place in the order, one made from a bowler bats at five to
    seven, and a full-time bowler bats in the tail. The discipline you took up (`add`) runs
    from `base` toward `top`, `second` of the way."""
    if not role or role == setup.role:
        return None
    origin = _kind(start)
    if role in ("pace", "spin") and origin != "bowler":
        role = FULL_TIME                       # a checkpoint saved before the correction
    now = role_kind(role)
    full_time = role == FULL_TIME
    if now == "allrounder" and origin == "batter":
        positions, band = start.positions, start.band
    elif now == "allrounder" and origin == "bowler":
        positions, band = FINISHER_SLOTS, "finisher"
    elif full_time and origin == "allrounder":
        positions, band = start.positions, start.band
    else:
        raise CareerError(f"a {setup.role} does not become a {role}")
    style = start.bowling_style or style or None
    add, base, top = "", None, None
    if origin == "batter":
        add = "bowl"
        base = ((start.bowl_scoring, start.bowl_dismissal) if start.bowl_scoring is not None
                else part_time_bowling(deck))
    elif origin == "bowler":
        add = "bat"
        base = finisher_batting(model, year)
    if add:
        top = second_ceiling(deck, seed, add, style if add == "bowl" else None, base)
    return {"role": role, "kind": now, "positions": sorted(positions), "band": band,
            "style": style, "add": add, "base": list(base) if base else None,
            "top": list(top) if top else None, "second": round(second, 6), "trial": trial,
            "full_time": full_time}


def apply_role(card: Card, rd: dict | None, t: float) -> Card:
    """Your card in a changed role. The discipline you took up follows its own progress,
    never past what your body allows: `min(second, t)`, so it declines with you."""
    if rd is None:
        return card
    fields = dict(role=rd["kind"], band=rd["band"], positions=frozenset(rd["positions"]),
                  bowling_style=rd["style"] or card.bowling_style)
    if rd["kind"] in ("allrounder", "bowler"):
        fields["bowl_balls"] = None           # no longer a part-timer (A206): you bowl
    if rd["add"]:
        ts = max(ABILITY_FLOOR, min(rd["second"], t))
        s = _lin(rd["base"][0], rd["top"][0], ts)
        d = _geo(rd["base"][1], rd["top"][1], ts)
        if rd["add"] == "bowl":
            fields.update(bowl_scoring=s, bowl_dismissal=d, bowl=bowl_value(s, d))
        else:
            fields.update(bat_scoring=s, bat_dismissal=d, bat=bat_value(s, d))
    return dataclasses.replace(card, **fields)


def role_key(rd: dict | None) -> tuple | None:
    """A changed role, as part of a season's cache key."""
    if rd is None:
        return None
    return (rd["role"], rd["trial"], tuple(rd["positions"]), rd["style"], rd["add"],
            tuple(rd["base"] or ()), tuple(rd["top"] or ()), rd["second"],
            rd.get("full_time", False))


def discipline_rank(lines: dict, key: tuple, disc: str) -> float | None:
    """Your place, 0 to 1, in ONE discipline among the season's regulars of it: batting
    among those who bat in your band (as a batter's season is ranked, A198), bowling among
    the bowlers and all-rounders. None without the volume to be judged (A33's spirit, the
    peer floors). This is what the coach reads."""
    me = lines[key]
    if disc == "bat":
        if me.innings < PEER_MIN_INNINGS:
            return None
        group = BAND_GROUP.get(me.card.band)
        pool = [l for k, l in lines.items() if k == key or (
            l.innings >= PEER_MIN_INNINGS and l.card.role != "bowler"
            and BAND_GROUP.get(l.card.band) == group)]
        if len(pool) - 1 < MIN_BAND_PEERS:
            pool = [l for k, l in lines.items()
                    if k == key or (l.innings >= PEER_MIN_INNINGS and l.card.role != "bowler")]
        scores = _bat_scores(pool)
    else:
        if me.bowl_balls < PEER_MIN_BOWL_BALLS:
            return None
        pool = [l for k, l in lines.items() if k == key or (
            l.card.role in ("bowler", "allrounder") and l.bowl_balls >= PEER_MIN_BOWL_BALLS)]
        scores = _bowl_scores(pool)
    mine = scores[next(i for i, l in enumerate(pool) if l is me)]
    others = [s for l, s in zip(pool, scores) if l is not me]
    if not others:
        return 0.5
    return (sum(s < mine for s in others) + 0.5 * sum(s == mine for s in others)) / len(others)


def coach_card(seed: int, setup: Setup, start: Card, cp: "Checkpoint", k: int,
               squad: bool) -> dict | None:
    """What the coach offers before this season, if anything: the full-time role after a
    good trial, a full-time bowler's job to an all-rounder whose bowling carries him, or a
    trial. One a season, in that order. Its own random stream, so it never moves a match."""
    if not COACH_ON:
        return None
    now = cp.roles[-1] if cp.roles else setup.role
    kind, origin = role_kind(now), _kind(start)
    if cp.coach_next:
        return dict(COACH_SWITCH, target=cp.coach_next)
    if squad:
        return None
    prev = cp.seasons[-1] if cp.seasons else None
    style = start.bowling_style or cp.style
    if (kind == "allrounder" and now != FULL_TIME and prev is not None and style
            and prev.bowl_rank is not None
            and cp.coach_declined != k - 1 and not (prev.role or {}).get("trial")
            and (prev.role or {}).get("kind", _kind(start)) == "allrounder"):
        fading = (prev.bat_rank is not None and prev.bat_rank < FADING_BAT
                  and prev.bowl_rank >= FADING_BOWL)
        if prev.bowl_rank >= SPECIALISE_BOWL or fading:
            card = COACH_FADING if fading and prev.bowl_rank < SPECIALISE_BOWL else COACH_SPECIALISE
            return dict(card, target=FULL_TIME)
    if (now == setup.role and setup.role in TRIAL_ROLES
            and cp.trials < COACH_TRIALS_MAX and COACH_TRIAL_FROM <= k <= COACH_TRIAL_UNTIL
            and random.Random(f"{seed}:coach:{k}").random() < COACH_TRIAL_CHANCE):
        if origin == "bowler":
            return dict(COACH_TRIAL_BAT, target="allrounder")
        if style:
            return dict(COACH_TRIAL_BOWL, target="allrounder")
        return dict(COACH_TRIAL_STYLE, target="allrounder")
    return None


def _opt(label: str, hint: str, story: str, take: bool, style: str = "") -> dict:
    return {"label": label, "hint": hint, "story": story, "take": take, "style": style}


_NO_BOWL = _opt("Stick to batting", "Keep your mind on the job that got you here.",
                "You told the coach you would rather keep your mind on batting.", False)
COACH_TRIAL_BOWL = {
    "key": "trial_bowl", "title": "The coach has an idea",
    "text": "The coach has been watching you in the nets. \"You can bowl. I want to see it in "
            "matches.\" A season as an all-rounder, bowling when the side needs you?",
    "options": [_opt("Give it a go", "Bowl in matches this season. Do well and the coach will "
                     "want more.", "You took the coach up on it: a season with the ball as well "
                     "as the bat.", True), _NO_BOWL]}
COACH_TRIAL_STYLE = dict(COACH_TRIAL_BOWL, key="trial_bowl", options=[
    _opt("Try seam", "Run in and hit the deck.",
         "You took the coach up on it: a season bowling seam as well as batting.", True, "pace"),
    _opt("Try spin", "Turn it and give it air.",
         "You took the coach up on it: a season bowling spin as well as batting.", True, "spin"),
    _NO_BOWL])
COACH_TRIAL_BAT = {
    "key": "trial_bat", "title": "The coach has an idea",
    "text": "The coach has seen you hit the ball in the nets. \"Pad up higher this season. "
            "Let's see what you can do at six or seven.\"",
    "options": [_opt("Take the chance", "Bat in the lower middle order, and keep bowling.",
                     "You moved up the order for the season, bat and ball both.", True),
                _opt("Stay at the tail", "Bowling is your job. Leave the runs to others.",
                     "You told the coach bowling is your job.", False)]}
COACH_SWITCH = {
    "key": "switch", "title": "The coach makes an offer",
    "text": "\"The trial worked. I want you as a full-time all-rounder.\"",
    "options": [_opt("Make it permanent", "An all-rounder from now on. If it goes wrong, the "
                     "coach can change it back.", "You are a full-time all-rounder now.", True),
                _opt("Not yet", "Go back to your old job. The offer may not come again.",
                     "You thanked the coach and went back to your old job.", False)]}
_SPECIALISE_OPTS = [
    _opt("Bowl every match", "Your four overs every game, and you keep your place in the "
         "order.", "You are a full-time bowler now: four overs every match.", True),
    _opt("Keep things as they are", "Bowl when the side needs you, as now.",
         "You told the coach you are happy bowling when the side needs you.", False)]
COACH_SPECIALISE = {
    "key": "specialise", "title": "The coach makes an offer",
    "text": "\"Your bowling is winning us matches. I want your four overs every game, and you "
            "keep your place in the order.\"", "options": _SPECIALISE_OPTS}
COACH_FADING = dict(COACH_SPECIALISE, text="\"The runs have dried up, but your bowling can "
                    "carry you. I want your four overs every game.\"")


def _grown(second: float, rank: float) -> float:
    return max(0.0, min(1.0, second + SECOND_STEP + SECOND_FORM * (rank - 0.5)))


def coach_after(cp: "Checkpoint", setup: Setup, rd: dict | None, played: "SeasonRecord",
                say) -> None:
    """After a season in a trial or a changed role: the trial's verdict, the new discipline's
    growth, and the way back after a poor season in the new role."""
    if rd is None:
        return
    def rank_of(disc: str) -> float | None:
        return played.bowl_rank if disc == "bowl" else played.bat_rank

    disc = "bowl" if rd.get("full_time") else (rd["add"] or "bowl")
    rank = rank_of(disc)
    if rd["trial"]:
        if rank is not None and rank >= TRIAL_PASS:
            cp.coach_next = rd["role"]
            # a passed trial counts as a season in the job: the new skill does not start
            # again from scratch if the full-time offer is taken
            cp.second = _grown(cp.second, rank)
            say("The trial worked. The coach wants a word before next season.", "up")
        else:
            say("The trial did not come off. Back to your old job.", "down")
        return
    grow = rank_of(rd["add"]) if rd["add"] else None
    if grow is not None:
        cp.second = _grown(cp.second, grow)
        if grow >= COMING_ON and cp.second < 1.0:
            say("Your bowling is coming on." if rd["add"] == "bowl" else "Your batting is coming on.",
                "up")
    if rank is None:
        return
    if rank < WAY_BACK and played.k == cp.role_since:
        # the first full-time season is the coach's to give: a poor one is not yet the end
        say("A hard first season in the new job, but the coach is giving you time.")
    elif rank < WAY_BACK:
        left = cp.roles.pop()
        back = cp.roles[-1] if cp.roles else setup.role
        if left == FULL_TIME:
            say("The coach has taken you off full-time bowling: you bowl when the side needs "
                "you again.", "down")
            return
        say({"batter": "The bowling did not work out. The coach has taken you out of the "
                       "attack: you are a batter again.",
             "bowler": "The runs did not come. The coach has sent you back down the order.",
             "allrounder": "The coach has seen enough. You are back to doing both."}
            [role_kind(back)], "down")


@dataclass(frozen=True)
class Shape:
    """When a career peaks and how fast it declines [A224]. Read through a career clock,
    which runs one season a season unless form moves it in the decline. The rise is the
    ARC's first five seasons stretched to `PEAK_SEASONS`, then one season at the peak, then
    the ARC's decline at `DECLINE_SPEED`: so the standard shape at clock k is ARC[k]."""

    peak: str = "standard"
    decline: str = "standard"

    @property
    def rise(self) -> int:
        return PEAK_SEASONS[self.peak]

    def arc_age(self, clock: float) -> float:
        """Where on the standard ARC this clock stands."""
        r = self.rise
        if clock <= r:
            return clock * 5 / r
        if clock <= r + 1:
            return 5 + (clock - r)
        return 6 + (clock - r - 1) * DECLINE_SPEED[self.decline]

    def arc(self, clock: float) -> float:
        a = self.arc_age(clock)
        if a >= len(ARC) - 1:
            return ARC[-1]
        i = int(a)
        return ARC[i] + (ARC[i + 1] - ARC[i]) * (a - i)

    def growing(self, clock: float) -> bool:
        """Still rising or at the peak: development and potential can still grow."""
        return clock < self.rise + 1

    def declining(self, clock: float) -> bool:
        return not self.growing(clock)


STANDARD = Shape()


def draw_shape(seed: int) -> Shape:
    """A new career's shape, from the seed alone (never a choice), hidden from the player."""
    rng = random.Random(f"{seed}:shape")
    peak = rng.choices(list(PEAK_WEIGHTS), weights=list(PEAK_WEIGHTS.values()))[0]
    decline = rng.choices(list(DECLINE_WEIGHTS), weights=list(DECLINE_WEIGHTS.values()))[0]
    return Shape(peak, decline)


def arc_point(k: float, permanent: float, season: float, shape: Shape = STANDARD) -> float:
    """Ability `t` at career clock `k` (a season count for a career from before A224)."""
    return max(ABILITY_FLOOR, min(ABILITY_CAP, shape.arc(k) + permanent + season))


def potential_legend(deck: Deck, role: str, ceiling: Card) -> Card | None:
    """Where a growing potential heads [A224]: the role's best real season that is better than
    your ceiling both on the card and at the role's job. Raising the potential moves your top
    card toward it, so every number you play with is still a mix of real players' (A197),
    never an extrapolation past one. None when the ceiling is already the best there is."""
    _, ceilings = template_pools(deck, role)
    better = [c for c in ceilings if c.display > ceiling.display
              and main_rating(c, role) >= main_rating(ceiling, role)]
    return max(better, key=lambda c: (c.display, main_rating(c, role), c.season_year, c.person_id),
               default=None)


def potential_room(ceiling: Card, legend: Card | None) -> float:
    """Rating points of potential this career can ever earn."""
    if legend is None:
        return 0.0
    return float(max(0, min(POTENTIAL_MAX, min(99, legend.display) - ceiling.display)))


def top_card(ceiling: Card, legend: Card | None, points: float) -> Card:
    """Your top card with `points` of potential earned: the ceiling moved toward the legend
    by that many card points, its numbers mixed as `ability` mixes them."""
    if points <= 0 or legend is None or legend.display <= ceiling.display:
        return ceiling
    s = min(1.0, points / (legend.display - ceiling.display))
    return dataclasses.replace(
        ceiling, display=ceiling.display + points,
        bat=_lin(ceiling.bat, legend.bat, s), bowl=_lin(ceiling.bowl, legend.bowl, s),
        bat_scoring=_lin(ceiling.bat_scoring, legend.bat_scoring, s),
        bat_dismissal=_geo(ceiling.bat_dismissal, legend.bat_dismissal, s),
        bowl_scoring=_lin(ceiling.bowl_scoring, legend.bowl_scoring, s),
        bowl_dismissal=_geo(ceiling.bowl_dismissal, legend.bowl_dismissal, s))


def rating_at(start: Card, ceiling: Card, t: float) -> int:
    """Your Rating at ability `t` [A214]: linear between the two spins' own cards, so the
    spin screen's two numbers are exactly where a career starts and where it can peak."""
    r = start.display + (ceiling.display - start.display) * t
    return max(RATING_FLOOR, min(99, round(r)))


def market_for(start: Card, ceiling: Card, cp: "Checkpoint", shape: Shape = STANDARD,
               legend: Card | None = None) -> int | None:
    """What clubs price you on going into the season the checkpoint stands before."""
    top = top_card(ceiling, legend, cp.potential)
    return market_value(rating_at(start, top, arc_point(cp.clock, cp.permanent, 0.0, shape)),
                        cp.last_display)


def market_value(rating: int, last_grade: int | None) -> int | None:
    """What clubs price you on [A214]: your Rating blended with last season's grade. None
    for a debutant, who is uncapped and paid the base price."""
    if last_grade is None:
        return None
    return round((1 - MARKET_FORM_WEIGHT) * rating + MARKET_FORM_WEIGHT * last_grade)


# --- a real year's league ----------------------------------------------------------------


_CLUBS: dict[int, dict] = {}


def clubs_by_year(deck: Deck) -> dict[int, dict[str, str]]:
    """year -> {canonical franchise: the name it played under}, from the deck (A228's league
    news). Once per deck."""
    hit = _CLUBS.get(id(deck))
    if hit is None:
        hit = defaultdict(dict)
        for cards in deck.cards_by_fs.values():
            if cards:
                hit[cards[0].season_year][canonical(cards[0].franchise)] = cards[0].franchise
        hit = _CLUBS[id(deck)] = dict(hit)
    return hit


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
    # [A225] what the engine expected of his balls (`BatterCard`/`BowlerCard`)
    exp_runs: float = 0.0
    exp_outs: float = 0.0
    bowl_off_bat: int = 0
    bowl_exp_runs: float = 0.0
    bowl_exp_wickets: float = 0.0


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
                    ln.exp_runs += b.exp_runs
                    ln.exp_outs += b.exp_outs
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
                ln.bowl_off_bat += w.off_bat
                ln.bowl_exp_runs += w.exp_runs
                ln.bowl_exp_wickets += w.exp_wickets
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


# [A225] A season measured like a real card. A real card's two engine numbers per discipline
# come from what the player did that season against par, pulled in by k = 100 balls (A160,
# A35). Here the par is exact: on every ball the engine knows the outcome probabilities it
# drew from, for that batter against that bowler in that state, so a season's LUCK is simply
# what happened minus what the engine expected (`BatterCard.exp_runs`, `exp_outs`). Your
# numbers that season plus that luck, pulled in by the same 100 balls, is the season as a
# card -- Kohli 2016's card is the season, not his average.
#
# Two ways of doing this were measured and rejected first, by replaying 42 careers' best
# seasons. Comparing your rates with the pooled rates of the regulars you were graded
# against, and pulling toward the pool's level as a real card is pulled toward its band
# (A46): a good player's best season came out WEAKER than his ordinary self in 16 of 30
# batting careers, because the pool sits below him. Pulling toward your own level instead
# fixed batting, but bowling still compared a death bowler's economy with everybody's, and
# death overs cost more whoever bowls them: the season card conceded MORE than the ordinary
# one in 10 of 18 bowling careers. The engine's own expectation has neither problem.
MEASURE_BALLS = 100


def _measure(balls: int, outs: int, runs: float, exp_runs: float, exp_outs: float,
             own: tuple[float | None, float | None], cheaper: bool
             ) -> tuple[float | None, float | None]:
    """(scoring, dismissal) for one discipline: your numbers `own` moved by the season's
    luck, pulled in by `MEASURE_BALLS`. Scoring is runs per ball faced, as A160's scoring
    half is; `cheaper` is bowling, where the scoring half counts runs saved."""
    my_s, my_d = own
    if balls <= 0 or exp_outs <= 0 or my_s is None or not my_d:
        return None, None
    w = balls / (balls + MEASURE_BALLS)
    luck = (runs - exp_runs) / balls
    scoring = my_s + w * (-luck if cheaper else luck)
    rate = exp_outs / balls
    dismissal = my_d * (outs + MEASURE_BALLS * rate) / (exp_outs + MEASURE_BALLS * rate)
    return scoring, dismissal


def measured_halves(lines: dict, you: tuple) -> list | None:
    """This season's four engine numbers, measured from its figures [A225]: the disciplines
    the season is graded on (a batter's batting, a bowler's bowling, an all-rounder's both)."""
    me = lines[you]
    kind = _kind(me.card)
    out: list = [None, None, None, None]
    if kind in ("batter", "allrounder"):
        out[0], out[1] = _measure(me.balls, me.outs, me.runs, me.exp_runs, me.exp_outs,
                                  (me.card.bat_scoring, me.card.bat_dismissal), cheaper=False)
    if kind in ("bowler", "allrounder"):
        out[2], out[3] = _measure(me.bowl_balls, me.wickets, me.bowl_off_bat, me.bowl_exp_runs,
                                  me.bowl_exp_wickets, (me.card.bowl_scoring, me.card.bowl_dismissal),
                                  cheaper=True)
    return out if any(v is not None for v in out) else None


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
               last_display: int | None, contract: "Contract | None" = None, *,
               capped: bool) -> list[Offer]:
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
    value = (DEBUT_PRICE if last_display is None
             else value_curve(last_display, mega=True) * (1 if capped else UNCAPPED_VALUE))

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


# --- mega-auction years [A212] ---------------------------------------------------------------
# In the real mega-auction years every contract ends. Your club may approach you to retain you
# at one of the real 2025 capped slabs, and it is your call: stay at that price, or go into
# the auction at a base price you choose. There every club that year sets a ceiling for you
# on the auction engine's own mega value curve (A142), with its own view of you, your old
# club paying the auction's loyalty premium, and the bidding is the auction's own ladder
# (`bid_log`). A base nobody reaches leaves you unsold, and one club signs you late at your
# base as a squad player (ratified). All of it is seeded, so a career stays a pure function
# of its moves.

MEGA_YEARS = frozenset({2011, 2014, 2018, 2022, 2025})
RETAIN_CARD = 85                         # below this card your club does not come to you
RETAIN_TIERS = ((93, 1800), (89, 1400), (RETAIN_CARD, 1100))   # the real capped slabs, lakh
BASE_PRICES = (200, 150, 125, 100, 75, 50, 40, 30)              # the real base-price choices
# [A229] An uncapped player -- one who has not played for his country -- is retained at the
# real 2025 rule's uncapped price, and clubs value him at UNCAPPED_VALUE of a capped player
# with the same card in offers and at a mega auction. The capped price is the one the
# offers were calibrated on (A212/A214), so being called up restores it; UNCAPPED_VALUE is
# declared game design, like REPUTATION, since the archive holds no prices.
UNCAPPED_RETAIN = 400
UNCAPPED_VALUE = 0.8
MEGA_SPREAD = 0.30                       # each club's view of you, a lognormal sigma


@dataclass(frozen=True)
class MegaOffer:
    retain: Offer | None                 # your club's retention offer, None when it makes none
    bases: tuple[int, ...]
    suggested: int                       # index into `bases`: the auction's own base for your card


@dataclass
class MegaResult:
    base: int
    sold: bool                           # False: unsold, signed late at the base
    retained: bool
    fs_id: int
    franchise: str
    short: str
    price: int
    bids: list[tuple[str, str, int]] = field(default_factory=list)   # (franchise, short, price)


def retention_slab(last_display: int | None, *, capped: bool) -> int | None:
    """What your club pays to keep you [A212]: a capped slab by your card, or the real 2025
    rule's uncapped price (A229) for a player who has never played for his country."""
    slab = next((price for floor, price in RETAIN_TIERS if (last_display or 0) >= floor), None)
    return slab if slab is None or capped else UNCAPPED_RETAIN


def _club_offer(deck: Deck, fs: int, year: int, price: int, stay: bool) -> Offer:
    from game.__main__ import opposition_twelve

    squad = deck.cards_by_fs[fs]
    xi, impact = opposition_twelve(list(squad))
    c = squad[0]
    return Offer(fs, c.franchise, _abbrev(c.franchise, year), price,
                 team_rating(xi + ([impact] if impact else [])).overall, stay)


def mega_offer(deck: Deck, year: int, current: str | None, last_display: int | None, *,
               capped: bool, trusted: bool = False) -> MegaOffer:
    from game.auction import BASE_PRICE_TIERS

    retain = None
    slab = retention_slab(last_display, capped=capped)
    if slab is None and trusted and last_display is not None:
        # [A230] A club that trusts you retains you a little below the usual bar.
        slab = retention_slab(last_display + RETAIN_TRUSTED, capped=capped)
    if slab is not None and current is not None:
        mine = [fs for fs in year_fs_ids(deck, year)
                if canonical(deck.cards_by_fs[fs][0].franchise) == current]
        if mine:
            retain = _club_offer(deck, mine[0], year, slab, True)
    d = last_display or 0
    base = next(price for floor, price in BASE_PRICE_TIERS if d >= floor)
    return MegaOffer(retain, BASE_PRICES, BASE_PRICES.index(base))


def mega_ceilings(deck: Deck, seed: int, k: int, year: int, current: str | None,
                  last_display: int | None, *, capped: bool) -> dict[int, int]:
    """What each club that year will pay for you, lakh, by franchise-season: the auction's mega
    value for your card, each club's own view of it, and your old club's loyalty premium."""
    from game.auction import LOYALTY

    value = value_curve(last_display, mega=True) * (1 if capped else UNCAPPED_VALUE)
    out = {}
    for fs in year_fs_ids(deck, year):
        view = value * math.exp(random.Random(f"{seed}:mega:{k}:{fs}").gauss(0, MEGA_SPREAD))
        if current is not None and canonical(deck.cards_by_fs[fs][0].franchise) == current:
            view *= LOYALTY
        out[fs] = min(PRICE_CAP, int(view))
    return out


def mega_auction(deck: Deck, seed: int, k: int, year: int, current: str | None,
                 last_display: int | None, base: int, *, capped: bool) -> MegaResult:
    from game.auction import Lot, bid_log

    fss = year_fs_ids(deck, year)
    by_fs = mega_ceilings(deck, seed, k, year, current, last_display, capped=capped)
    ceilings = {i: by_fs[fs] for i, fs in enumerate(fss)}
    lot = Lot(index=k, card=Card(fs_id=0, person_id=YOU_ID, name="you"), set_code="", base=base)
    bids = bid_log(lot, ceilings, seed, 0)
    if bids:
        fs, price = fss[bids[-1].team], bids[-1].price
    else:
        fs, price = random.Random(f"{seed}:mega-late:{k}").choice(fss), base
    c = deck.cards_by_fs[fs][0]
    return MegaResult(base, bool(bids), False, fs, c.franchise, _abbrev(c.franchise, year), price,
                      [(deck.cards_by_fs[fss[b.team]][0].franchise,
                        _abbrev(deck.cards_by_fs[fss[b.team]][0].franchise, year), b.price)
                       for b in bids])


def mega_text(res: MegaResult) -> str:
    from game.auction import crore

    if res.retained:
        return f"Retained by {res.franchise} at {crore(res.price)}"
    if not res.sold:
        return f"Unsold at {crore(res.base)}; signed late by {res.franchise}"
    return f"Mega auction: sold to {res.franchise} for {crore(res.price)} (base {crore(res.base)})"


# --- the captain's bowling plan [A213] --------------------------------------------------------
# A captain gives each of his side's bowlers a phase: powerplay, middle or death. It is set
# before the season and may be changed at any key moment (A204); `choose_bowler` honours it
# without ever leaving an innings unable to finish. A move: "p" and one digit per bowler, in
# the order `plan_bowlers` lists them -- 0 any, 1 powerplay, 2 middle, 3 death.

PLAN_MAX = 6                              # the five who bowl and an Impact Player who might
PLAN_CODES = {"0": None, "1": "powerplay", "2": "middle", "3": "death"}


def plan_bowlers(deck: Deck, you: Card, fs_id: int) -> list[Card]:
    """Who a plan is for: your side's bowlers, as the side would rank them (A203)."""
    from game.__main__ import bowling_order

    xi, impact = xi_with(you, list(deck.cards_by_fs[fs_id]))
    pool = [c for c in xi + ([impact] if impact else []) if c.has_bowl]
    return sorted(pool, key=bowling_order)[:PLAN_MAX]


def parse_plan(digits: str, bowlers: list[Card]) -> tuple[tuple[str, str], ...]:
    """A plan as a hashable tuple of (person id, phase), from its digits."""
    if len(digits) != len(bowlers) or any(d not in PLAN_CODES for d in digits):
        raise CareerError(f"a bowling plan needs one of 0-3 for each of {len(bowlers)} bowlers")
    return tuple(sorted((c.person_id, PLAN_CODES[d]) for c, d in zip(bowlers, digits)
                        if PLAN_CODES[d]))


def next_captain(captain_now: bool, poor: bool, club: str) -> str | None:
    """Whose armband you hold next season (A213): the club you captained this one, unless the
    season was poor. Changing club ends it too, because the armband is checked against the
    club you sign for."""
    return club if captain_now and not poor else None


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


def event_sequence(seed: int, overseas: bool = False, role: str | None = None) -> list[Event]:
    """The career's event cards, one per season, from the seed and the setup alone. No card
    twice running, and none beyond `EVENT_LIMIT` times in a career. A card's `when` decides
    who may draw it at all; "staying" and "capped" depend on the season and are settled in
    the loop, where a card that does not fit gives way to `fallback_event` (A230)."""
    rng = random.Random(f"{seed}:events")
    out: list[Event] = []
    used: dict[str, int] = {}
    for k in range(MAX_SEASONS):
        allowed = [e for e in EVENTS
                   if (not out or e is not out[-1])
                   and used.get(e.key, 0) < EVENT_LIMIT.get(e.key, MAX_SEASONS)
                   and (e.when != "late" or k >= LATE_FROM)
                   and (e.when != "veteran" or k >= VETERAN_FROM)
                   and (e.when != "senior" or k >= SENIOR_FROM)
                   and (e.when != "staying" or k >= TRADE_FROM)
                   and (e.when != "overseas" or overseas)]
        e = rng.choices(allowed, weights=[e.weight for e in allowed])[0]
        used[e.key] = used.get(e.key, 0) + 1
        out.append(e)
    return out


def fallback_event(seed: int, k: int, not_this: str = "") -> Event:
    """The card a season gets when its own does not fit it (a trade for a player not staying,
    a workload card for one not in his national side): one of `FALLBACK`, from its own stream."""
    keys = [x for x in FALLBACK if x != not_this]
    key = random.Random(f"{seed}:fallback:{k}").choice(keys)
    return next(e for e in EVENTS if e.key == key)


def draw_event(seed: int, k: int, overseas: bool = False, role: str | None = None) -> Event:
    return event_sequence(seed, overseas, role)[k]


def points_t(points: float, start: Card, ceiling: Card) -> float:
    """Rating points as ability `t` at your own scale [A214]: one point is one point of
    `rating_at`, whatever the gap between your two spins."""
    return points / max(1, ceiling.display - start.display)


def draw_outcome(seed: int, k: int, option: EventOption) -> Outcome | None:
    """Which way an answer turns out: seeded by the season, decided the moment you choose."""
    if not option.outcomes:
        return None
    rng = random.Random(f"{seed}:outcome:{k}")
    return rng.choices(option.outcomes, weights=[o.chance for o in option.outcomes])[0]


def absence_words(reason: str, missed: int, first: int) -> str:
    """The season card's line for a random absence [A216]."""
    span = (f"match {first}" if missed == 1 else f"matches {first}-{first + missed - 1}")
    plural = "match" if missed == 1 else "matches"
    if reason == "international duty":
        return f"You missed {missed} {plural} on international duty ({span})."
    if reason == "rested":
        return f"You were rested for {missed} {plural} ({span})."
    if reason == "illness":
        return f"Illness kept you out of {missed} {plural} ({span})."
    if reason == "a family matter":
        return f"You went home for a family matter and missed {missed} {plural} ({span})."
    if reason == NOT_SELECTED:
        return f"Not selected for {missed} {plural} ({span})."
    if reason == "rotation":
        return f"Rotated out for {missed} {plural} ({span})."
    return f"Left out for {missed} {plural} for {reason} ({span})."


# [A230] Each line has a few wordings, taken in turn by season, so a long career does not say
# the same sentence every year.
POTENTIAL_GRADE = ("A season like that has people talking about you as a future great.",
                   "Seasons like that change what people think you can become.",
                   "The talk now is of how far you could go.")
POTENTIAL_GROWN = ("You are growing into a better player than anyone expected.",
                   "You keep finding more in your game.",
                   "There is more in you than anyone thought.")
CLOCK_SLOW_WORDS = ("Form like that keeps the years at bay.",
                    "Playing like that, nobody is talking about your age.")
CLOCK_FAST_WORDS = ("A poor season this late in a career takes more out of you.",
                    "A season like that, at your age, makes people wonder how long you have left.")
DEV_UP = ("The coaches like what they are seeing.", "You are learning fast.",
          "Every season you look more at home at this level.")
DEV_DOWN = ("A hard season, and you will carry some of it into the next.",
            "A season to learn from, the hard way.",
            "It was a tough year, and it set you back a little.")


def potential_words(before: int, after: int, grade: int | None, k: int = 0) -> str:
    """The season card's line for a raised potential [A224], in words (A230): the bar on the
    player card shows how far."""
    words = POTENTIAL_GRADE if grade is not None else POTENTIAL_GROWN
    return words[k % len(words)]


def clock_words(step: float, k: int = 0) -> str:
    """The season card's line when form moves the career clock [A224], in words (A230)."""
    words = CLOCK_SLOW_WORDS if step < 1 else CLOCK_FAST_WORDS
    return words[k % len(words)]


def shape_words(shape: Shape, before: float, after: float, age: int) -> str | None:
    """The hints that give a hidden career shape away as it happens [A224]: an early peak as it
    arrives, a late bloomer still rising when most players have peaked, and how the body is
    holding up as the decline begins."""
    if shape.peak == "early" and before < shape.rise <= after:
        return f"At {age} you are already close to your best."
    if shape.peak == "late" and before < PEAK_SEASONS["standard"] <= after:
        return f"Still improving at {age}: you are coming into your own late."
    if before <= shape.rise + 1 < after:
        return {"gentle": "Past your peak now, but your body is holding up well.",
                "steep": "Past your peak now, and the years are catching up with you quickly.",
                }.get(shape.decline, "Past your peak now.")
    return None


def development_words(dev: float, k: int = 0) -> str:
    """The season card's line for a young player's development [A214], in words (A230)."""
    words = DEV_UP if dev > 0 else DEV_DOWN
    return words[k % len(words)]


def age_words(age: float) -> str:
    """A change to the career clock in words [A224]."""
    span = ("half a season" if abs(age) == 0.5 else "a season" if abs(age) == 1
            else f"{abs(age):g} seasons")
    return f"{span} of ageing held back" if age < 0 else f"{span} older"


# Which way a season-card note points, for the page's ▲/▼ [A218]. Read off the wording the
# functions above and below produce, so it lives beside them: a change of wording that this
# stops recognising fails `tests/test_career_web.py`, which feeds it each generator's output.
_NOTE_UP = re.compile(r"\badds \d+ Rating to next season|more runs per 100 balls"
                      r"|get out \d+% less often|fewer runs an over|take wickets \d+% more often"
                      r"|potential raised to|raised to \d+\.$|holds back the years"
                      r"|of ageing held back|already close to your best|Still improving at"
                      r"|holding up well|^Called up by|^Recalled by|^Kept your place with")
_NOTE_DOWN = re.compile(r"\btakes \d+ Rating from next season|fewer runs per 100 balls"
                        r"|get out \d+% more often|more runs an over|take wickets \d+% less often"
                        r"|takes more out of you|catching up with you quickly|season older\b"
                        r"|seasons older\b|^Dropped by")


def note_tone(text: str) -> str:
    """'up', 'down', or '' for a note that is mixed or changes nothing. A Rating change
    decides; matches missed count against only when nothing else in the note moves."""
    signs = re.findall(r"([+-])\d+ Rating (?:this season|for good)", text)
    ups = signs.count("+") + len(_NOTE_UP.findall(text))
    downs = signs.count("-") + len(_NOTE_DOWN.findall(text))
    if not ups and not downs and re.search(r"\b\d+ match(?:es)? missed\b", text):
        downs = 1
    return "up" if ups and not downs else "down" if downs and not ups else ""


def skill_change(before: Card, after: Card) -> list[tuple[str, bool]]:
    """What a lasting change did to your game, as phrases and whether each is for the better
    [A214, in words since A230]: scoring and staying in, or being hard to score off and
    taking wickets."""
    out = []
    if before.bat_scoring is not None and after.bat_scoring is not None and before.role != "bowler":
        runs = 100 * (after.bat_scoring - before.bat_scoring)
        outs = 100 * (after.bat_dismissal / before.bat_dismissal - 1)
        if round(runs):
            out.append(("scoring more freely" if runs > 0 else "scoring less freely", runs > 0))
        if round(outs):
            out.append(("getting out more often" if outs > 0 else "harder to get out", outs < 0))
    if before.bowl_scoring is not None and after.bowl_scoring is not None and before.role != "batter":
        econ = 6 * (after.bowl_scoring - before.bowl_scoring)      # positive: cheaper
        strike = 100 * (after.bowl_dismissal / before.bowl_dismissal - 1)
        if round(econ, 1):
            out.append(("harder to score off" if econ > 0 else "easier to score off", econ > 0))
        if round(strike):
            out.append(("taking wickets more often" if strike > 0 else "taking wickets less often",
                        strike > 0))
    return out


def skill_words(before: Card, after: Card) -> str | None:
    """The season card's line for a lasting change to your game, or None for none."""
    change = skill_change(before, after)
    if not change:
        return None
    phrases = [p for p, _ in change]
    said = phrases[0] if len(phrases) == 1 else ", ".join(phrases[:-1]) + " and " + phrases[-1]
    return f"Something has changed: you are {said}."


def skill_tone(before: Card, after: Card) -> str:
    ups = [up for _, up in skill_change(before, after)]
    return "up" if ups and all(ups) else "down" if ups and not any(ups) else ""


def event_person(deck: Deck, event: Event, fs_id: int, year: int, you: Card) -> Card | None:
    """The real player an event names, by his card that season: the best of your kind in your
    own squad (a mentor), the best player of any other team that year (a rival), or the
    debutant in your squad who would go furthest in the archive (a pupil, A230)."""
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
        return max(same or mates, key=lambda c: (c.display, c.person_id), default=None)
    if event.who == "rival":
        others = [c for fs in year_fs_ids(deck, year) if fs != fs_id
                  for c in deck.cards_by_fs[fs] if c.display is not None]
        return max(others, key=lambda c: (c.display, c.person_id), default=None)
    if event.who == "pupil":
        _, years = _deck_index(deck)
        best = _best_cards(deck)
        new = [c for c in deck.cards_by_fs[fs_id] if c.person_id != YOU_ID
               and min(years.get(c.person_id, {year})) == year]
        return max(new, key=lambda c: (best.get(c.person_id, 0), c.person_id), default=None)
    return None


def event_name(deck: Deck, event: Event, fs_id: int, year: int, you: Card) -> str | None:
    c = event_person(deck, event, fs_id, year, you)
    return c.name if c is not None else None


_BEST: dict[int, dict[str, int]] = {}


def _best_cards(deck: Deck) -> dict[str, int]:
    """Each person's best card in the archive, once per deck."""
    hit = _BEST.get(id(deck))
    if hit is None:
        hit = {}
        for cards in deck.cards_by_fs.values():
            for c in cards:
                if c.display is not None and c.display > hit.get(c.person_id, 0):
                    hit[c.person_id] = c.display
        _BEST[id(deck)] = hit
    return hit


def trade_target(deck: Deck, seed: int, k: int, year: int, current: str) -> int | None:
    """The club your club has agreed to trade you to: another team that year, from the seed."""
    others = sorted(fs for fs in year_fs_ids(deck, year)
                    if canonical(deck.cards_by_fs[fs][0].franchise) != current)
    return random.Random(f"{seed}:trade:{k}").choice(others) if others else None


def fill(text: str, name: str = "", club: str = "") -> str:
    return text.replace("{name}", name or "A senior player").replace("{club}", club)


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
    mega: str = ""                     # how a mega-auction year went (A212)
    plan: list[list[str]] = field(default_factory=list)   # [name, phase] as the season began (A213)
    rating: int = 0                    # your Rating this season, events included (A214); 0 = older token
    development: float = 0.0           # Rating points the season's form adds to the next (A214)
    notes: list[str] = field(default_factory=list)   # what happened, in words (A214)
    absence: str = ""                  # why the season's absence happened (A216)
    absent_from: int = 0               # its first league match, 1-based; 0 = none
    tenure: int = 0                    # seasons in a row at this club before this one (A216)
    potential: float = 0.0             # potential points earned before this season (A224)
    clock: float | None = None         # the career clock this season was played at (A224)
    # [A225] The engine's four numbers as THIS season's figures measure them, the way a real
    # card's are measured from its season: [bat scoring, bat dismissal, bowl scoring, bowl
    # dismissal], None where the season is not judged on that discipline. None on a season
    # played before A225.
    measured: list | None = None
    # [A228] Player of the Match awards this season, and the season's newsworthy facts
    # (game.press renders them into clippings each time a page asks). Empty on older tokens.
    poms: int = 0
    press: list[dict] = field(default_factory=list)
    # [A229] What the selectors made of this season ("debut", "kept", "dropped", "recalled" or
    # ""), the internationals played in the window after it, and the career total after them.
    national: str = ""
    caps: int = 0
    caps_total: int = 0
    # [A230] Each note's mark ('up' | 'down' | ''), beside `notes`; empty on an older token,
    # whose notes are read by `note_tone`. The season's mid-season incident (what happened,
    # what you chose, how it went), the rival named before it (so a key moment against his
    # side can say so), and money beyond the contract.
    tones: list[str] = field(default_factory=list)
    incident: dict | None = None
    rival: list | None = None
    extra_earnings: int = 0
    # [A238] The changed role this season was played in (`role_state`, None for the role you
    # were created in), what the coach offered before it and what you said, and your place
    # in each discipline among its regulars (None without the volume to be judged).
    role: dict | None = None
    coach: dict | None = None
    bat_rank: float | None = None
    bowl_rank: float | None = None


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
                   answers: tuple | None = None,
                   plan: tuple[tuple[str, str], ...] = (),
                   spent: tuple[str, ...] = ()) -> tuple[SeasonRecord, "SeasonDetail"]:
    """The record is copied (the career goes on to change it); the detail is shared, since
    nothing changes it after it is built. Your answers are part of the key: a different call
    at a key moment is a different season. A season that pauses raises and is not cached."""
    full = (id(deck), id(model), seed, k, key, answers, plan, spent)
    hit = _SEASON_CACHE.get(full)
    if hit is None:
        if len(_SEASON_CACHE) >= SEASON_CACHE_MAX:
            _SEASON_CACHE.clear()
        hit = _SEASON_CACHE[full] = play_season_detail(deck, model, seed, k, you, record,
                                                       answers, plan, spent)
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
    absent: list = field(default_factory=list)       # league indices of the season's absence (A216)
    absence_reason: str | None = None                # why, or None for an event card's own
    incident: dict | None = None                     # the season's incident and your answer (A230)
    incident_out: dict = field(default_factory=dict) # league index -> why it kept you out (A230)


def availability_shares(tenure: int | None, overseas: bool) -> tuple[float, ...]:
    """The share of the league you are available for, drawn from real players in the same
    season at their club as you are, with your passport; all of it once you are settled."""
    if tenure is None:
        return AVAILABILITY_SHARES
    if tenure >= SETTLED_SEASONS:
        return SETTLED_SHARES
    return AVAILABILITY_BY_TENURE[(tenure, bool(overseas))]


def tenure_at(seasons: list, franchise: str) -> int:
    """Your seasons in a row at this club, counted back from the last (A216). By the
    canonical franchise, so Delhi Daredevils to Delhi Capitals is one club."""
    club, n = canonical(franchise), 0
    for r in reversed(seasons):
        if canonical(r.franchise) != club:
            break
        n += 1
    return n


def absence_reason(seed: int, k: int, missed: int, overseas: bool, squad: bool = False) -> str:
    """Why the season's absence happened [A216, A226], from its own stream. Never an injury:
    those are the injury cards' to give, and they name themselves."""
    rng = random.Random(f"{seed}:absence:{k}")
    if squad:
        return NOT_SELECTED
    if overseas and rng.random() < OVERSEAS_DUTY:
        return "international duty"
    if missed <= SHORT_ABSENCE:
        roll = rng.random()
        for what, p in SHORT_REASONS:
            if roll < p:
                return what
            roll -= p
    return rng.choice(LEFT_OUT_OVERSEAS if overseas else LEFT_OUT)


def missed_block(seed: int, k: int, league_matches: int, squad: bool = False,
                 extra: int = 0, form_drop: float = 0.0, rating: int | None = None,
                 overseas: bool = False, role: str | None = None, tenure: int = 0) -> range:
    return absence(seed, k, league_matches, squad, extra, form_drop, rating, overseas, role,
                   tenure)[0]


def absence(seed: int, k: int, league_matches: int, squad: bool = False,
            extra: int = 0, form_drop: float = 0.0, rating: int | None = None,
            overseas: bool = False, role: str | None = None,
            tenure: int = 0) -> tuple[range, str | None]:
    """Which of your league matches you miss, and why: one block, its length from the real
    share of matches a regular like you plays (or, in a squad season, a non-regular).
    `tenure` is your seasons in a row at this club before this one: settled
    (SETTLED_SEASONS+), a regular season costs you nothing by chance. Its
    own random stream, so missing matches never changes how the matches themselves are drawn.

    `form_drop` is the chance this season's share already held a real two-match form drop
    (FORM_DROP_RATE): for a player who can be dropped for form in the season itself, that
    much is taken off the block, on a stream of its own (A205).

    [A216] `extra` is an event card's absence (an injury, a break, a league back home). In a
    regular season it REPLACES the random one, whose real figures already hold injuries,
    rather than adding to it; in a squad season the larger of the two stands. A star (Rating
    STAR_AVAILABILITY_RATING+) loses at most STAR_MAX_ABSENCE of the league to chance. The
    reason is None when the absence is the event card's own, which names itself."""
    rng = random.Random(f"{seed}:avail:{k}")
    shares = (SQUAD_SHARES if squad else
              availability_shares(tenure if rating is not None else None, overseas))
    base = round((1 - rng.choice(shares)) * league_matches)
    if form_drop and random.Random(f"{seed}:avail-form:{k}").random() < form_drop:
        base = max(0, base - DROP_MATCHES)
    if rating and rating >= STAR_AVAILABILITY_RATING and not squad:
        base = min(base, int(STAR_MAX_ABSENCE * league_matches))
    if rating is None:                         # the table before A216, for older callers
        missed = min(league_matches, base + extra)
        reason = None
    elif extra and not (squad and base > extra):
        missed, reason = min(league_matches, extra), None
    else:
        missed = min(league_matches, base)
        reason = absence_reason(seed, k, missed, overseas, squad) if missed else None
    if reason == "international duty":
        # duty takes you away at the start of the season or the end of it
        start = 0 if rng.random() < 0.5 else league_matches - missed
    else:
        start = rng.randrange(league_matches - missed + 1)
    return range(start, start + missed), reason


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


def incidents_spent(seasons: list) -> tuple[str, ...]:
    """The incidents a career has had as often as `incidents.LIMIT` allows (A230)."""
    seen: dict[str, int] = defaultdict(int)
    for r in seasons:
        if r.incident:
            seen[r.incident["key"]] += 1
    return tuple(sorted(key for key, n in seen.items() if n >= incidents.LIMIT.get(key, 99)))


# [A230] What a season's feed and season card say kept you out, for an incident's absence.
INCIDENT_ABSENCE = {"hamstring": "a hamstring", "side_strain": "a side strain",
                    "back": "a back injury", "wedding": "a family wedding", "slump": "a break"}


def _your_match(r: Result, yours: Side) -> tuple["incidents.Match", Side]:
    """Your last match, as an incident's trigger reads it, and the side you played."""
    home = yours is r.home
    opp = r.away if home else r.home
    mine = r.home_innings if home else r.away_innings
    theirs = r.away_innings if home else r.home_innings
    b = next((b for b in (mine.batting if mine else []) if b.player.person_id == YOU_ID), None)
    w = next((w for w in (theirs.bowling if theirs else []) if w.player.person_id == YOU_ID), None)
    lost = r.winner is not None and r.winner is not yours
    runs, wkts = (r.home_runs, r.home_wickets) if home else (r.away_runs, r.away_wickets)
    heavy = None
    if lost and ((home and (r.away_wickets or 0) <= 10 - incidents.HEAVY_WICKETS)
                 or (not home and r.home_runs - r.away_runs >= incidents.HEAVY_RUNS)):
        # whichever half failed: a small total is the batters', a big one conceded the bowlers'
        heavy = "the batters" if runs < incidents.BLAME_TOTAL else "the bowlers"
    collapse = (f"Bowled out for {runs}" if wkts >= 10 and runs < incidents.COLLAPSE_TOTAL + 10
                else f"Kept to {runs} batting first" if home and runs < incidents.COLLAPSE_TOTAL
                else None)
    hit = None
    for o in (theirs.over_log if theirs else []):
        if (o.over >= incidents.DEATH_OVER and o.over_runs >= incidents.DEATH_HIT
                and getattr(o, "bowler_id", "") != YOU_ID and o.bowler
                and (hit is None or o.over_runs > hit[1])):
            hit = (o.bowler, o.over_runs, o.over + 1)
    return incidents.Match(
        opp=opp.name, played=b is not None or w is not None,
        batted=b is not None and (b.faced_any or b.out), runs=b.runs if b else 0,
        out=bool(b and b.out), bowl_balls=w.balls if w else 0, bowl_runs=w.runs if w else 0,
        wickets=w.wickets if w else 0, lost=lost, heavy=heavy, collapse=collapse,
        death_hit=hit), opp


def simulate_year(deck: Deck, model: Model, seed: int, k: int, you: Card,
                  squad: bool = False, extra_missed: int = 0,
                  answers: tuple | None = None,
                  plan: tuple[tuple[str, str], ...] = (),
                  rating: int | None = None, extra_reason: str = "", tenure: int = 0,
                  rival: tuple[str, str] | None = None, spent: tuple[str, ...] = (),
                  full_time: bool = False) -> Year:
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
    yours.bowling_plan = dict(plan) or None          # a captain's plan (A213)
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
    kind_of_injury = (you.bowling_style if you.role == "bowler" else
                      "allrounder" if you.role == "allrounder" else "batter")
    block, absence_why = absence(seed, k, league_matches, squad, extra_missed,
                                 FORM_DROP_RATE[_kind(you)] if droppable else 0.0,
                                 rating, bool(you.overseas), kind_of_injury, tenure)
    if block and absence_why is None:
        absence_why = extra_reason or None        # the event card's own absence
    out = set(block)
    dropped: set[int] = set()                      # league indices you were dropped for
    dropped_seen: list[int] = []                   # ... as your match numbers, for the feed
    verdicts: dict[int, str] = {}                  # make-or-break match -> "kept" | "dropped"
    book = FormBook(roster)
    you_key = (YOU_ID, id(yours))
    breaks = 0
    pending_break: int | None = None               # your match number of an unresolved one
    played = 0
    # a full-time bowler is never rested from the attack (A238)
    rested = None if full_time else _rested_lineup(yours, me)
    bowl_rng = random.Random(f"{seed}:bowls:{k}")
    labels = APPROACH_LABELS[_kind(me)]
    used = 0                                       # answers consumed
    must_wins = 0
    seen = 0                                       # your side's matches so far, this included
    # (your match number, stage, opponent, approach). The NUMBER identifies the match: a
    # league meets every side twice, and keying on the opponent tagged the wrong meeting.
    approaches: list[tuple[int, str, str, str]] = []
    # [A230] The season's mid-season incident: which one it may be, and once it has come,
    # what it set going -- form for a while, a call kept for good, matches out.
    inc_kind = incidents.kind_of(me)
    inc_key, inc_target = (incidents.candidate(seed, k, inc_kind, league_matches)
                           if not squad and answers is not None else (None, 0))
    if inc_key in spent:
        inc_key = None                             # it has happened as often as a career allows
    if (inc_key is None and not squad and answers is not None
            and random.Random(f"{seed}:slump:{k}").random() < incidents.SLUMP_CHANCE):
        inc_key = "slump"                          # a season with nothing else may bring a slump
    season_now: list = [None]
    incident: dict | None = None
    incident_out: dict[int, str] = {}
    forms: list[tuple[int, int | None, int]] = []   # (from, until or None, level)
    kept_call: list[int] = []                      # an approach kept for the rest of the season
    season_over = [False]
    recent_balls: list[int] = []

    def form_at(idx: int) -> int:
        return sum(lv for a, b, lv in forms if a <= idx and (b is None or idx < b))

    def ask(key: str, detail: str, opp: Side, idx: int) -> None:
        """Stop for an incident, or apply its recorded answer from this match on."""
        nonlocal used, incident
        inc = incidents.INCIDENTS[key]
        text = inc.text.replace("{opp}", opp.name.rsplit(" ", 1)[0]).replace("{what}", detail)
        if used >= len(answers):
            raise _NeedMoment(None, season_now[0], yours, list(approaches), roster,
                              list(dropped_seen), dict(verdicts),
                              sorted(set(out) - set(incident_out)), absence_why,
                              prompt={"key": key, "title": inc.title, "text": text,
                                      "options": inc.options},
                              incident_out=dict(incident_out))
        ans = answers[used]
        if not (isinstance(ans, tuple) and ans and ans[0] == "i"):
            raise CareerError("expected an answer to what happened, not a key-moment call")
        if not 0 <= ans[1] < len(inc.options):
            raise CareerError(f"no choice {ans[1]} here")
        used += 1
        opt = inc.options[ans[1]]
        turn = incidents.turn_for(seed, k, opt)
        why = INCIDENT_ABSENCE.get(key, inc.title.lower())
        back = idx
        if opt.out == -1:
            gone = range(idx, league_matches)
            season_over[0] = True
        else:
            gone = range(idx, min(idx + opt.out, league_matches))
        for i in gone:
            incident_out[i] = why                  # out because of this now, whatever else
        out.update(gone)
        back = idx + max(opt.out, 0)
        if opt.form:
            forms.append((back, back + opt.form_matches if opt.form_matches else None, opt.form))
        if turn is not None and turn.out:
            late = range(back + 1, min(back + 1 + turn.out, league_matches))
            for i in late:
                incident_out[i] = why
            out.update(late)
        if turn is not None and turn.form:
            forms.append((back, None, turn.form))
        if opt.approach is not None:
            kept_call.append(opt.approach)
        if opt.death:
            yours.bowling_plan = {**(yours.bowling_plan or {}), YOU_ID: "death"}
        tone = opt.tone or ("up" if turn is not None and (turn.form > 0 or turn.permanent > 0)
                            else "down" if turn is not None and (turn.form < 0 or turn.permanent < 0
                                                                 or turn.out or turn.standing < 0)
                            else "")
        incident = {"key": key, "title": inc.title, "text": text, "choice": opt.label,
                    "story": opt.story, "turn": turn.text if turn else "", "at": seen + 1,
                    "standing": opt.standing + (turn.standing if turn else 0),
                    "permanent": opt.permanent + (turn.permanent if turn else 0),
                    "age": opt.age, "thread": opt.thread, "tone": tone,
                    "injuries": [x for x in (opt.injury, turn.injury if turn else "") if x],
                    "out": len([i for i in incident_out])}

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
        if rival and opp.franchise == rival[0]:
            stakes = f"{stakes} {rival[1]} is in their side."
        if used >= len(answers):
            raise _NeedMoment(Moment(stage, opp.name, opp.short, opp.franchise, opp.year,
                                     stakes, labels, kind), season, yours, list(approaches),
                              roster, list(dropped_seen), dict(verdicts),
                              sorted(set(out) - set(incident_out)), absence_why,
                              incident_out=dict(incident_out), incident=incident)
        ans = answers[used]
        if isinstance(ans, tuple) and ans and ans[0] == "i":
            raise CareerError("expected a key-moment call, not an answer to what happened")
        used += 1
        # An answer is a call, or a call with a new bowling plan from this match on (A213).
        choice, replan = (ans, None) if isinstance(ans, int) else ans
        if replan is not None:
            yours.bowling_plan = dict(replan) or None
        if kind == "must_win":
            must_wins += 1
        approaches.append((seen, stage, opp.short, labels[choice], kind))
        yours.xi = [approach_card(c, choice) if c.person_id == YOU_ID else c
                    for c in yours.xi]

    def drop_from(idx: int) -> None:
        """Dropped for this match and the next. The missed block was already drawn short
        by the real rate of drops, so a drop adds to it rather than coming off it."""
        dropped.update(range(idx, min(idx + DROP_MATCHES, league_matches)))

    def lineup(idx: int) -> tuple[list[Card], Card | None]:
        """An all-rounder bowls in ALLROUNDER_BOWL_SHARE of the matches he plays. Your form
        and any call you keep for the season (A230) are applied to your card."""
        if rested is None or bowl_rng.random() < ALLROUNDER_BOWL_SHARE:
            xi, impact = with_you
        else:
            xi, impact = rested
        level = form_at(idx)
        if not level and not kept_call:
            return xi, impact

        def mine(c: Card) -> Card:
            if c.person_id != YOU_ID:
                return c
            c = incidents.formed(c, level)
            return approach_card(c, kept_call[-1]) if kept_call else c
        return [mine(c) for c in xi], impact

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
            season_now[0] = season
            if mine:
                last, last_opp = _your_match(season.results[mine[-1]], yours)
                if last.played:
                    recent_balls.append(last.bowl_balls)
            if incident is None and inc_key is not None and not benched and mine:
                inc = incidents.INCIDENTS[inc_key]
                if inc.trigger != "form":
                    detail = incidents.fires(inc, inc_kind, last, idx, inc_target, recent_balls)
                    if detail is not None:
                        ask(inc_key, detail, last_opp, idx)
                elif idx + 1 < league_matches:
                    rank0 = book.form_rank(you_key)
                    if rank0 is not None and rank0 < FORM_POOR:
                        games = book.games[you_key][-FORM_MATCHES:]
                        ask("slump", ", ".join(form_figure(_kind(me), g) for g in games),
                            last_opp, idx)
                benched = idx in out or idx in dropped
            yours.xi, yours.impact = without if benched else lineup(idx)
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
            yours.xi, yours.impact = without if squad or season_over[0] else lineup(10 ** 6)
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
    year = Year(season, yours, season.results + season.playoffs, roster, len(out | dropped),
                absent=sorted(set(out) - set(incident_out)), absence_reason=absence_why,
                incident=incident, incident_out=incident_out)
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
    card: int | None = None             # index into SeasonDetail.cards: its scorecard (A214)
    absence: str | None = None          # why you missed this match (A216)
    approach: str | None = None         # your call, when this was a key moment (A204)
    stop: str | None = None             # what kind of key moment: must_win, playoff, make_or_break
    verdict: str | None = None          # a make-or-break's outcome: "kept" | "dropped" (A205)
    incident: str | None = None         # what happened before this match, and your answer (A230)
    dropped: bool = False               # left out for form (A205)
    form: list[str] = field(default_factory=list)   # your last FORM_SHOWN matches, oldest first
    form_word: str | None = None        # In form / Steady / Out of form, after this match
    # This match's own landmarks for you: "fifty"|"hundred", "four_for"|"five_for", "hattrick"
    # (A209). Whether one is a career FIRST is the career's business (`career_milestones`).
    landmarks: list[str] = field(default_factory=list)
    # [A228] What the papers need: your boundaries, whether you chased, the side's best other
    # batter that day ("KP Pietersen 39", off the scorecard), and Player of the Match.
    fours: int = 0
    sixes: int = 0
    chasing: bool = False
    mate: str | None = None
    mate_runs: int = 0
    pom: bool = False


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
    # The IPL table's For/Against and the side's last five league results, oldest first
    # (A153's points table, shared with the solo season since this change). Balls, not
    # overs: the page's table writes them as overs.
    runs_for: int = 0
    balls_for: int = 0
    runs_against: int = 0
    balls_against: int = 0
    form: list[str] = field(default_factory=list)


def form_letter(r: Result, side) -> str:
    """One league result as a side's form letter: W, L, or T for a match still level after
    the super overs (A135). By identity, as `Side` has no `eq=False`."""
    return "T" if r.winner is None else "W" if r.winner is side else "L"


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
    cards: list[Result] = field(default_factory=list)     # every match of yours, for its scorecard (A214)


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


# [A228] Player of the Match, decided the way a match is usually read: the winning side's
# best day (97.6% of the archive's 1,218 decided matches gave it to a winner), scored as
# runs, plus strike against par, plus wickets at FORM_WICKET_RUNS and overs under par --
# the form score's own pieces (A205). Measured against the real awards on the archive's own
# scorecards, this picks the real Player of the Match in 68% of matches (runs and wickets
# alone: 66%). A match level after the super overs looks at both sides.
POM_STRIKE_WEIGHT = 0.5


def pom_score(runs: int, balls: int, wickets: int, bowl_balls: int, bowl_runs: int) -> float:
    return (runs + POM_STRIKE_WEIGHT * (runs - FORM_PAR_PER_BALL * balls)
            + FORM_WICKET_RUNS * wickets + (FORM_PAR_PER_BALL * bowl_balls - bowl_runs))


def player_of_match(r: Result) -> str | None:
    """The person id of the match's Player of the Match, or None for a match with no cards."""
    if r.home_innings is None or r.away_innings is None:
        return None
    sides = [r.winner] if r.winner is not None else [r.home, r.away]
    best: tuple | None = None
    for side in sides:
        bat = r.home_innings if side is r.home else r.away_innings
        bowled = r.away_innings if side is r.home else r.home_innings
        figs: dict[str, list[int]] = {}
        for b in bat.batting:
            figs.setdefault(b.player.person_id, [0, 0, 0, 0, 0])[:2] = [b.runs, b.balls]
        for w in bowled.bowling:
            f = figs.setdefault(w.player.person_id, [0, 0, 0, 0, 0])
            f[2:] = [w.wickets, w.balls, w.runs]
        for pid, f in figs.items():
            key = (pom_score(*f), pid)
            if best is None or key > best:
                best = key
    return best[1] if best else None


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
    cards: list[Result] = []
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
    form: dict[int, list[str]] = {id(s): [] for s in season.sides}   # league results, in order
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
            for side in (r.home, r.away):
                form[id(side)].append(form_letter(r, side))
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
                line.fours, line.sixes = b.fours, b.sixes
        line.chasing = not home                # the engine's home side bats first
        mate = _best_bat(mine)
        line.mate = _bat_line(mate) if mate else None
        line.mate_runs = mate.runs if mate else 0
        line.pom = player_of_match(r) == YOU_ID
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
        if year.incident and year.incident["at"] == n:
            line.incident = f"{year.incident['title']}: {year.incident['choice']}"
        if not line.played and not line.dropped and r.stage == "league":
            if n - 1 in year.incident_out:
                line.absence = year.incident_out[n - 1]
            elif n - 1 in year.absent:
                line.absence = year.absence_reason or "out"
        book.add(year.results[:i + 1])     # form as it stands straight after this match
        line.form = [form_figure(you_kind, g) for g in book.games.get(you_key, [])[-FORM_SHOWN:]]
        line.form_word = form_word(book.form_rank(you_key))
        if r.stage == "league":
            line.position = next(i for i, s in enumerate(rank_table(standings), 1)
                                 if s.side is yours)
        if r.stage != "league" or line.approach:
            line.watch = len(watch)
            watch.append(r)
        line.card = len(cards)
        cards.append(r)
        line.orange, line.purple = leader(True), leader(False)
        feed.append(line)

    table = [TableRow(short=s.side.short, name=s.side.name, franchise=s.side.franchise,
                      year=s.side.year, played=s.played, won=s.won, lost=s.lost, tied=s.tied,
                      points=s.points, nrr=round(s.nrr, 3), you=s.side is yours,
                      runs_for=s.runs_for, balls_for=s.balls_for, runs_against=s.runs_against,
                      balls_against=s.balls_against, form=form[id(s.side)][-5:])
             for s in rank_table(standings)]
    return SeasonDetail(feed, table, watch, complete, cards=cards)


def play_season(deck: Deck, model: Model, seed: int, k: int, you: Card,
                record: SeasonRecord, answers: tuple[int, ...] | None = None) -> SeasonRecord:
    return play_season_detail(deck, model, seed, k, you, record, answers)[0]


def play_season_detail(deck: Deck, model: Model, seed: int, k: int, you: Card,
                       record: SeasonRecord, answers: tuple | None = None,
                       plan: tuple[tuple[str, str], ...] = (),
                       spent: tuple[str, ...] = ()
                       ) -> tuple[SeasonRecord, SeasonDetail]:
    year = simulate_year(deck, model, seed, k, you, record.squad, record.extra_missed, answers,
                         plan, record.rating if record.rating else None, record.absence,
                         record.tenure, tuple(record.rival) if record.rival else None, spent,
                         full_time=bool(record.role and record.role.get("full_time")))
    season, yours, results = year.season, year.yours, year.results
    lines = season_lines(results, year.roster)
    record.missed = year.missed
    record.dropped = len(year.dropped)
    record.incident = year.incident
    record.absence = year.absence_reason or ""
    record.absent_from = year.absent[0] + 1 if year.absent else 0
    key = (YOU_ID, id(yours))
    me = lines.get(key) or Line(card=you, side=yours)
    lines[key] = me
    for name in ("matches", "innings", "runs", "balls", "outs", "fifties", "hundreds",
                 "highest", "wickets", "bowl_balls", "bowl_runs", "four_fors", "five_fors",
                 "best"):
        setattr(record, name, getattr(me, name))
    record.teams = len(season.sides)
    record.percentile = season_percentile(lines, key)
    record.bat_rank = discipline_rank(lines, key, "bat")
    record.bowl_rank = discipline_rank(lines, key, "bowl")
    record.display = season_display(deck, you.season_year, you, record.percentile)
    record.measured = measured_halves(lines, key)
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
    record.poms = sum(l.pom for l in detail.feed)
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
POM_MARK = 10             # every ten Player of the Match awards (A228)
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
    poms = prior("poms")
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
        if l.pom:
            poms += 1
            if poms == 1:
                add("pom", f"First Player of the Match award, v {opp}")
            elif poms % POM_MARK == 0:
                add("pom", f"{poms} Player of the Match awards")
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
        out.append(CareerMilestone("elite", f"First 90+ season grade: {record.display}"))
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
    mega: MegaOffer | None = None      # a mega-auction year's choice (A212)
    plan_bowlers: list[Card] = field(default_factory=list)   # a captain's plan is for these (A213)
    plan: dict[str, str] = field(default_factory=dict)        # ... and stands at this
    incident: dict | None = None       # what happened mid-season, and the answers (A230)
    event_fill: tuple[str, str] = ("", "")                    # the name and club a card names
    cold: bool = False                 # your club has not offered to keep you (A230)
    coach: dict | None = None          # the coach's offer, answered `t{i}` (A238)


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
    peak_rating: int = 0               # your best Rating (A214)
    peak_rating_year: int = 0
    poms: int = 0                      # Player of the Match awards (A228)
    epilogue: str = ""                 # where the player went after the game (A228)


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
    # A mega auction decided in THIS call (A212), for the page to play; None otherwise.
    auction: MegaResult | None = None
    shape: Shape = STANDARD            # hidden (A224)
    legend: Card | None = None         # where a growing potential heads (A224)
    # [A238] Your role now (a ROLES key), and the one the season pending is played in -- a
    # trial season's differs from it.
    role_now: str = ""
    season_role: str = ""

    @property
    def potential_points(self) -> float:
        return self.checkpoint.potential if self.checkpoint else 0.0

    @property
    def top(self) -> Card:
        """Your top card now: the ceiling, moved by the potential earned so far (A224)."""
        return top_card(self.ceiling, self.legend, self.potential_points)

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
        earnings=sum(r.price + r.extra_earnings for r in s), peak_display=peak.display, peak_year=peak.year,
        five_fors=sum(r.five_fors for r in s), hat_tricks=sum(r.hat_tricks for r in s),
        milestones=[(r.year, m.text) for r in s for m in r.milestones])
    best = max(s, key=lambda r: (r.rating, -r.year))
    total.peak_rating, total.peak_rating_year = best.rating, best.year
    total.poms = sum(r.poms for r in s)
    total.epilogue = career.checkpoint.epilogue or "" if career.checkpoint else ""
    if real_runs is not None:
        total.runs_rank = all_time_rank(total.runs, real_runs)
    if real_wickets is not None:
        total.wickets_rank = all_time_rank(total.wickets, real_wickets)
    return total


def legend_card(career: Career) -> tuple[SeasonRecord, Card] | None:
    """The best version of you: what a friends' auction room brings in when its career-legends
    setting is on (A215).

    [A225] Your BEST SEASON -- the highest Season grade, then the higher Rating, then the
    earlier year -- carded the way a real season is: rated at its grade, playing the four
    numbers its own figures measure (`measured_halves`). Seasons played before A225 carry no
    measurement; a career with none falls back to A215's rule, the season of your highest
    Rating, played and rated at that Rating. None for a career whose seasons carry no Rating
    (a token from before A214)."""
    measured = [r for r in career.seasons if r.measured]
    if measured:
        best = max(measured, key=lambda r: (r.display, r.rating, -r.year))
        card = ability(career.start, top_card(career.ceiling, career.legend, best.potential),
                       best.ability, setup=career.setup, fs_id=0, year=best.year, franchise=None,
                       role=best.role)
        fields = dict(zip(("bat_scoring", "bat_dismissal", "bowl_scoring", "bowl_dismissal"),
                          best.measured))
        return best, dataclasses.replace(card, display=best.display,
                                         **{k: v for k, v in fields.items() if v is not None})
    rated = [r for r in career.seasons if r.rating]
    if not rated:
        return None
    best = max(rated, key=lambda r: (r.rating, -r.year))
    card = ability(career.start, top_card(career.ceiling, career.legend, best.potential),
                   best.ability, setup=career.setup, fs_id=0, year=best.year, franchise=None,
                   role=best.role)
    return best, dataclasses.replace(card, display=best.rating)


# [A234] What a legend is worth to a twelve, in runs a match above an average player. The
# 70-99 number on his card does not decide a match: the engine plays the card's four numbers
# (A160), and sixteen of one account's legends, all rated 99, are worth +12.0 down to +2.4. So the
# lobby picker orders legends by this, worked out when read, never stored.
# Balls faced a team innings at each batting position, measured over the 2,421 full-length
# innings of the archive (super overs excluded, a wide is not a ball faced, A22):
BALLS_FACED_AT = {1: 21.3, 2: 21.9, 3: 19.5, 4: 17.4, 5: 13.8, 6: 9.5, 7: 6.2,
                  8: 3.4, 9: 1.9, 10: 1.0, 11: 0.4}
BALLS_BOWLED = 24             # a bowler in the five bowls his four overs
LEAGUE_OUT_RATE = 0.051       # dismissals a ball, the league's own (about 6 an innings)
WICKET_RUNS = 5.8             # the engine's own price of a wicket (A160's Costs, weighted)


def legend_value(c: Card) -> float:
    """Runs a match this card adds over an average player: his batting at the average
    exposure of the places he may bat, plus four overs of bowling unless his bowling is
    thin, since a side with five real bowlers never bowls a thin one (A203)."""
    from game.__main__ import ATTACK_MIN_BALLS

    v = 0.0
    if c.bat_scoring is not None and c.positions:
        faced = statistics.fmean(BALLS_FACED_AT[p] for p in c.positions)
        v += (c.bat_scoring - (c.bat_dismissal - 1) * LEAGUE_OUT_RATE * WICKET_RUNS) * faced
    thin = c.bowl_balls is not None and c.bowl_balls < ATTACK_MIN_BALLS
    if c.bowl_scoring is not None and c.has_bowl and not thin:
        v += (c.bowl_scoring + (c.bowl_dismissal - 1) * LEAGUE_OUT_RATE * WICKET_RUNS) * BALLS_BOWLED
    return v


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
    return ROLES.get(role, {}).get("poor_percentile", POOR_PERCENTILE)


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
    captain: str | None = None            # the club whose armband you hold (A213)
    plan: dict[str, str] = field(default_factory=dict)   # your last bowling plan, as a default
    # [A214] What an answer set in motion for next season (`Outcome` as a dict), and the
    # injuries of the career so far, [year, what].
    later: list[dict] = field(default_factory=list)
    injuries: list[list] = field(default_factory=list)
    # [A224] The career clock (one season a season unless form moves it in the decline), the
    # potential points earned, and whether the career has a drawn shape. A career from before
    # A224 has none: its clock picks up at its season count and its shape stays standard, so
    # its curve does not change shape halfway through.
    clock: float = 0.0
    potential: float = 0.0
    shaped: bool = True
    # [A228] Where the player went after the game: an `EPILOGUES` key, chosen once the career
    # ends. None: not asked yet. "" on a checkpoint from before A228, which is never asked,
    # so a career already finished stays finished.
    epilogue: str | None = None
    # [A229] The national record so far: {"capped", "in_side", "caps"}; empty is uncapped.
    national: dict = field(default_factory=dict)
    # [A230] How your club sees you (STANDING_MIN..STANDING_MAX, never shown as a number), and
    # the threads a later season remembers: a mentor, a youngster you helped, a feud with the
    # papers, the armband the dressing room has already offered at a club.
    standing: int = 0
    threads: list[dict] = field(default_factory=list)
    # [A238] The roles the coach has moved you into, in order (empty: the role you were
    # created in; the way back pops one), how far the discipline you took up has come, the
    # bowling style you chose if your start never bowled, the full-time role a good trial has
    # earned an offer of, the trials offered so far, and the season you last turned down
    # the coach's offer to specialise.
    roles: list[str] = field(default_factory=list)
    second: float = 0.0
    style: str = ""
    coach_next: str = ""
    trials: int = 0
    coach_declined: int = -9
    role_since: int = -1                  # the season the last change took effect: its grace

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
                   ended=d.get("ended"), contract=contract, captain=d.get("captain"),
                   plan=dict(d.get("plan") or {}), later=list(d.get("later") or []),
                   injuries=[list(i) for i in d.get("injuries") or []],
                   clock=d.get("clock", float(d["k"])), potential=d.get("potential", 0.0),
                   shaped=d.get("shaped", False), epilogue=d.get("epilogue", ""),
                   national=dict(d.get("national") or {}), standing=d.get("standing", 0),
                   threads=[dict(t) for t in d.get("threads") or []],
                   roles=list(d.get("roles") or []), second=d.get("second", 0.0),
                   style=d.get("style", ""), coach_next=d.get("coach_next", ""),
                   trials=d.get("trials", 0), coach_declined=d.get("coach_declined", -9),
                   role_since=d.get("role_since", -1))


def grow_potential(cp: Checkpoint, grade: int, growing: bool, start: Card, ceiling: Card,
                   legend: Card | None, shape: Shape) -> str | None:
    """Raise the potential after a season, if it earned a raise [A224], and say so.

    Two ways, both only while you are still growing. A season whose own grade beats your
    potential by `POTENTIAL_MARGIN` adds a step (two by `POTENTIAL_BIG`). And Rating points
    for good that the potential would otherwise swallow -- next season's ability already at
    the cap -- move into it, so a mentor's "+3 for good" is never wasted at your peak. What
    stays as points for good is rescaled so it is still worth the same Rating points on the
    bigger gap. `cp.clock` must already be next season's."""
    if not growing:
        return None
    top = top_card(ceiling, legend, cp.potential)
    before = round(top.display)
    gain = 0
    if grade >= before + POTENTIAL_MARGIN:
        gain = POTENTIAL_BIG_STEP if grade >= before + POTENTIAL_BIG else POTENTIAL_STEP
    gap = max(1, top.display - start.display)
    spill = (max(0.0, min(cp.permanent, shape.arc(cp.clock) + cp.permanent - ABILITY_CAP))
             if cp.permanent > 0 else 0.0)
    total = max(0.0, min(gain + spill * gap, potential_room(ceiling, legend) - cp.potential))
    if total <= 0:
        return None
    cp.permanent -= max(0.0, total - gain) / gap
    cp.potential += total
    new_gap = max(1, top_card(ceiling, legend, cp.potential).display - start.display)
    cp.permanent *= gap / new_gap
    after = round(top_card(ceiling, legend, cp.potential).display)
    if after == before:
        return None
    return potential_words(before, after, grade if gain else None, cp.k)


# --- the papers [A228] -------------------------------------------------------------------------
# A real teammate's last IPL season: told only where the archive is sure it was the last --
# MATE_LAST_BEFORE leaves two seasons of room, since a man missing from 2025 and 2026 may
# yet come back -- and only for a player with MATE_MIN_SEASONS in the league.
MATE_MIN_SEASONS = 6
MATE_LONG_SEASONS = 10                 # ...or ten, to be news when you shared only this one
MATE_LAST_BEFORE = LAST_YEAR - 1
_INDEX: dict[int, tuple] = {}


def _deck_index(deck: Deck) -> tuple[dict, dict]:
    """((franchise, year) -> fs_id, person -> his seasons in the archive), once per deck."""
    hit = _INDEX.get(id(deck))
    if hit is None:
        fs_of, years = {}, defaultdict(set)
        for fs, cards in deck.cards_by_fs.items():
            if not cards:
                continue
            fs_of[(cards[0].franchise, cards[0].season_year)] = fs
            for c in cards:
                years[c.person_id].add(c.season_year)
        hit = _INDEX[id(deck)] = (fs_of, dict(years))
    return hit


def mate_farewell(deck: Deck, year: int, fs_id: int, previous: list) -> dict | None:
    """The most-travelled real teammate in your squad this year for whom it was his last
    IPL season: {"name", "seasons", "together"}, or None."""
    if year > MATE_LAST_BEFORE:
        return None
    fs_of, years = _deck_index(deck)
    squads = [{c.person_id for c in deck.cards_by_fs.get(fs_of.get((r.franchise, r.year)), [])}
              for r in previous]
    best = None
    for c in deck.cards_by_fs.get(fs_id, []):
        ys = years.get(c.person_id, set())
        if c.person_id == YOU_ID or max(ys, default=0) != year or len(ys) < MATE_MIN_SEASONS:
            continue
        together = 1 + sum(c.person_id in sq for sq in squads)
        if together < 2 and len(ys) < MATE_LONG_SEASONS:
            continue                       # a near-stranger: only a long career makes news
        key = (len(ys), together, c.name)
        if best is None or key > best[0]:
            best = (key, {"name": c.name, "seasons": len(ys), "together": together})
    return best[1] if best else None


def _press_facts(deck: Deck, cp: "Checkpoint", played: SeasonRecord, offer: Offer, k: int,
                 year: int, auction: "MegaResult | None", setup: Setup) -> "press.SeasonFacts":
    prev = cp.seasons
    last = prev[-1] if prev else None
    mega = None
    if auction is not None and year in MEGA_YEARS and k > 0:
        rivals = []
        for f, _short, _price in reversed(auction.bids):
            if canonical(f) != canonical(auction.franchise) and f not in rivals:
                rivals.append(f)
        mega = {"retained": auction.retained, "sold": auction.sold, "price": auction.price,
                "base": auction.base, "bids": len(auction.bids), "rivals": rivals[:2]}
    moved = (last.franchise if last is not None and mega is None
             and canonical(last.franchise) != canonical(offer.franchise) else None)
    return press.SeasonFacts(
        k=k, year=year, club=offer.franchise, price=played.price, age=DEBUT_AGE + k,
        moved_from=moved, contract=played.contract_year == 1, mega=mega,
        champion=played.champion, finalist=played.finalist,
        title_number=sum(r.champion for r in prev) + 1,
        orange=played.runs if played.orange_cap else None,
        purple=played.wickets if played.purple_cap else None,
        first_captaincy=played.captain and not any(r.captain for r in prev),
        injuries=[t for y, t in cp.injuries if y == year],
        mate=mate_farewell(deck, year, offer.fs_id, prev),
        first_elite=(played.display if played.display >= 90 and all(r.display < 90 for r in prev)
                     else None),
        ambitions=[m.text.removeprefix("Ambition achieved: ") for m in played.milestones
                   if m.key == "ambition"],
        first_hundred=not any(r.hundreds for r in prev),
        first_five_for=not any(r.five_fors for r in prev),
        national=({"country": setup.nation, "event": played.national, "caps": played.caps,
                   "total": played.caps_total, "grade": played.display}
                  if setup.nation else None),
        caps_marks=_marks(played.caps_total - played.caps, played.caps_total, national.CAP_MARK)
        if played.caps else [],
        traded=played.event == "trade" and moved is not None,
        stories=(["endorse"] if played.event == "endorsement" and played.extra_earnings else [])
        + (["hit_back"] if played.incident and played.incident.get("thread") == "press_feud"
           else []))


def season_event(cp: "Checkpoint", k: int, year: int, offer: Offer, squad: bool,
                 already: bool, seed: int, setup: Setup) -> Event:
    """The season's card: a follow-up when one is due (A230), else the seeded card, giving
    way to `fallback_event` where it does not fit the season."""
    if any(t["k"] == "press_feud" and t["year"] == year - 1 for t in cp.threads):
        return FOLLOW_UPS["press_feud"]
    club = canonical(offer.franchise)
    if (cp.standing >= STANDING_LEAD and not squad and not already and k >= 2
            and not any(t["k"] == "lead" and t.get("club") == club for t in cp.threads)):
        return FOLLOW_UPS["lead"]
    event = draw_event(seed, k, setup.overseas, cp.roles[-1] if cp.roles else setup.role)
    unfit = ((squad and event.key in NOT_IN_A_SQUAD_SEASON)
             or (already and event.key == "captain")
             or (event.key == "trade" and (not offer.stay or year in MEGA_YEARS))
             or (event.key == "workload" and not (setup.nation and cp.national.get("in_side"))))
    return fallback_event(seed, k, event.key) if unfit else event


_BY_YEAR: dict[int, dict[str, dict[int, int]]] = {}


def _cards_by_year(deck: Deck) -> dict[str, dict[int, int]]:
    """Each person's best card in each archive year, once per deck."""
    hit = _BY_YEAR.get(id(deck))
    if hit is None:
        hit = defaultdict(dict)
        for cards in deck.cards_by_fs.values():
            for c in cards:
                if c.display is not None and c.display > hit[c.person_id].get(c.season_year, 0):
                    hit[c.person_id][c.season_year] = c.display
        _BY_YEAR[id(deck)] = hit = dict(hit)
    return hit


def threads_due(deck: Deck, cp: "Checkpoint", year: int, say) -> None:
    """The payoffs of what a career started (A230), each told once, in the archive's own
    terms: a mentor's last IPL season, and a youngster you helped who became a star."""
    _, years = _deck_index(deck)
    by_year = _cards_by_year(deck)
    keep = []
    for th in cp.threads:
        k = th["k"]
        if k == "mentor":
            last = max(years.get(th["id"], {0}))
            if last == year - 1 and last <= MATE_LAST_BEFORE:
                say(f"{th['name']}, who took you under his wing in {th['year']}, has played "
                    f"his last IPL season.")
                continue
            if last < year - 1:
                continue
        elif k == "pupil":
            if by_year.get(th["id"], {}).get(year, 0) >= PUPIL_STAR:
                say(f"{th['name']}, the youngster who shadowed you in {th['year']}, is now one "
                    f"of the best players in the league.", "up")
                continue
            if max(years.get(th["id"], {0})) < year:
                continue
        elif k == "press_feud" and th["year"] < year - 1:
            continue
        keep.append(th)
    cp.threads = keep


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
    shape = draw_shape(seed) if cp.shaped else STANDARD
    legend = potential_legend(deck, setup.role, ceiling)
    career = Career(setup=setup, seed=seed, start=start, ceiling=ceiling,
                    seasons=cp.seasons, shape=shape, legend=legend)
    career.role_now = career.season_role = cp.roles[-1] if cp.roles else setup.role
    queue = list(moves)

    def nxt() -> str | None:
        return queue.pop(0) if queue else None

    def take_answers(bowlers: list[Card]) -> tuple:
        """This season's answers at its key moments: the `m{i}` moves that follow its offer
        and event (A204). A captain's may carry a new bowling plan, `m{i}p{digits}` (A213)."""
        out = []
        while queue and isinstance(queue[0], str) and queue[0][:1] in ("m", "i"):
            mv = queue.pop(0)
            if mv.startswith("i"):                 # an answer to what happened (A230)
                if not mv[1:].isdigit():
                    raise CareerError(f"expected an answer, got {mv!r}")
                out.append(("i", int(mv[1:])))
                continue
            call, _, digits = mv.partition("p")
            choice = _index(call, "m", 3)
            if not _:
                out.append(choice)
            elif not bowlers:
                raise CareerError("only a captain sets a bowling plan")
            else:
                out.append((choice, parse_plan(digits, bowlers)))
        return tuple(out)

    career.retired, career.released = cp.ended == "retired", cp.ended == "released"
    while cp.ended is None and cp.k < MAX_SEASONS and setup.debut + cp.k <= LAST_YEAR:
        k = cp.k
        clock = cp.clock
        top = top_card(ceiling, legend, cp.potential)
        year = setup.debut + k
        squad_now, mega_note = cp.squad, ""
        # [A238] the changed role you go into the season in, before the coach says anything
        rd = role_state(deck, model, seed, setup, start, cp.roles[-1] if cp.roles else "",
                        cp.second, cp.style, year)
        career.role_now = career.season_role = cp.roles[-1] if cp.roles else setup.role
        market = market_for(start, ceiling, cp, shape, legend)
        if year in MEGA_YEARS and k > 0:
            cp.contract = None                 # a mega auction ends every deal (A212)
            capped = bool(cp.national.get("capped"))
            mo = mega_offer(deck, year, cp.current, market, capped=capped,
                            trusted=cp.standing >= STANDING_BACKED)
            career.checkpoint = copy.deepcopy(cp)
            move = nxt()
            if move is None:
                if mo.retain is not None:
                    here = ability(start, top, arc_point(clock, cp.permanent, 0.0, shape), setup=setup,
                                   fs_id=mo.retain.fs_id, year=year, franchise=mo.retain.franchise,
                                   role=rd)
                    pos, rank = where_you_play(deck, here, mo.retain.fs_id)
                    mo = dataclasses.replace(mo, retain=dataclasses.replace(
                        mo.retain, position=pos, attack_rank=rank))
                career.pending = Pending("mega", year, k, squad=cp.squad, mega=mo)
                return career
            if move == "r":
                career.retired, cp.ended = True, "retired"
                break
            if move == "k":
                if mo.retain is None:
                    raise CareerError("your club has not offered to retain you")
                offer = mo.retain
                career.auction = MegaResult(0, True, True, offer.fs_id, offer.franchise,
                                            offer.short, offer.price)
            else:
                base = mo.bases[_index(move, "b", len(mo.bases))]
                a = career.auction = mega_auction(deck, seed, k, year, cp.current,
                                                  market, base, capped=capped)
                offer = _club_offer(deck, a.fs_id, year, a.price, canonical(a.franchise) == cp.current)
                squad_now = squad_now or not a.sold          # a late signing is a squad player
            mega_note = mega_text(career.auction)
        else:
            offers = offers_for(deck, seed, k, year, cp.current, market, cp.contract,
                                capped=bool(cp.national.get("capped")))
            if cp.contract is not None and (len(offers) != 1 or not offers[0].contract_year):
                cp.contract = None                 # the club has no team this year: deal void
            cold = (cp.standing <= STANDING_COLD and not offers[0].contract_year
                    and offers[0].stay)
            if cold:
                offers = offers[1:]                # [A230] the club does not ask you to stay
            career.checkpoint = copy.deepcopy(cp)
            move = nxt()
            if move is None:
                here = ability(start, top, arc_point(clock, cp.permanent, 0.0, shape), setup=setup,
                               fs_id=offers[0].fs_id, year=year, franchise=offers[0].franchise,
                               role=rd)
                placed = []
                for o in offers:
                    pos, rank = where_you_play(deck, dataclasses.replace(
                        here, fs_id=o.fs_id, franchise=o.franchise), o.fs_id)
                    placed.append(dataclasses.replace(o, position=pos, attack_rank=rank))
                career.pending = Pending("offer", year, k, offers=placed, squad=cp.squad,
                                         cold=cold)
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
        if canonical(offer.franchise) != cp.current:
            cp.standing = 0                    # a new club starts from nothing (A230)
        # The armband stays once taken, until you change club or have a poor season (A213).
        already = cp.captain is not None and cp.captain == canonical(offer.franchise)
        # [A238] What the coach offers before the season: a trial, the full-time role a good
        # trial earned, or a full-time bowler's job. Answered `t{i}`; turning it down is fine.
        coach = coach_card(seed, setup, start, cp, k, squad_now)
        coach_said, coach_rec = None, None
        if coach is not None:
            move = nxt()
            if move is None:
                career.pending = Pending("coach", year, k, squad=squad_now, offers=[offer],
                                         coach=coach)
                return career
            pick = coach["options"][_index(move, "t", len(coach["options"]))]
            coach_said = (pick["story"], "up" if pick["take"] else "")
            coach_rec = {"key": coach["key"], "choice": pick["label"], "take": pick["take"]}
            trial = False
            if coach["key"] == "switch":
                cp.coach_next = ""
                if pick["take"]:
                    cp.roles.append(coach["target"])
                    cp.role_since = k
            elif coach["key"] == "specialise":
                if pick["take"]:
                    cp.roles.append(coach["target"])
                    cp.role_since = k
                else:
                    cp.coach_declined = k
            else:
                cp.trials += 1
                if pick["take"]:
                    trial = True
                    cp.style = pick["style"] or cp.style
            rd = role_state(deck, model, seed, setup, start,
                            coach["target"] if trial else (cp.roles[-1] if cp.roles else ""),
                            cp.second, cp.style, year, trial=trial)
            career.role_now = cp.roles[-1] if cp.roles else setup.role
            career.season_role = rd["role"] if rd else setup.role
        event = season_event(cp, k, year, offer, squad_now, already, seed, setup)
        probe = ability(start, top, 0.0, setup=setup, fs_id=offer.fs_id, year=year,
                        franchise=offer.franchise, role=rd)
        person = event_person(deck, event, offer.fs_id, year, probe) if event.who else None
        target = (trade_target(deck, seed, k, year, canonical(offer.franchise))
                  if event.who == "trade" else None)
        club_name = deck.cards_by_fs[target][0].franchise if target is not None else ""
        name = person.name if person is not None else ""
        if len(event.options) == 1:
            option = event.options[0]
        else:
            move = nxt()
            if move is None:
                career.pending = Pending("event", year, k, offers=[offer], event=event,
                                         squad=squad_now,
                                         event_text=fill(event.text, name, club_name),
                                         event_fill=(name, club_name))
                return career
            option = event.options[_index(move, "e", len(event.options))]
        if option.retire:
            career.retired, cp.ended = True, "retired"
            break
        # [A214] What last season set in motion lands now; then this season's answer. Every
        # note is a sentence with its mark beside it, never a number (A230).
        notes: list[str] = []
        tones: list[str] = []

        def say(text: str, tone: str = "") -> None:
            if text:
                notes.append(text)
                tones.append(tone)

        if coach_said:
            say(*coach_said)

        perm_before = cp.permanent
        landed_missed = 0
        for lt in cp.later:
            cp.permanent += points_t(lt["permanent"], start, top)
            landed_missed += lt["missed"]
            say(lt["text"], "up" if lt["permanent"] > 0 else "down"
                if lt["permanent"] < 0 or lt["missed"] else "")
            if lt["injury"]:
                cp.injuries.append([year, lt["injury"]])
        cp.later = []
        threads_due(deck, cp, year, say)
        if option.trade and target is not None:
            mates = cp.contract
            offer = _club_offer(deck, target, year, offer.price, False)
            if mates is not None:
                mates.club = canonical(offer.franchise)
            cp.standing = 1                    # they asked for you
            already = False
        outcome = draw_outcome(seed, k, option)
        now = outcome if outcome is not None and not outcome.next_season else None
        if outcome is not None and outcome.next_season:
            cp.later.append(dataclasses.asdict(outcome))
        cp.permanent += points_t(option.permanent, start, top)
        season_pts = option.season + (now.season if now else 0)
        t = arc_point(clock, cp.permanent, points_t(season_pts, start, top), shape)
        extra = option.missed + landed_missed + (now.missed if now else 0)
        cp.standing += option.standing + (now.standing if now else 0)
        for line in (option.injury, now.injury if now else ""):
            if line:
                cp.injuries.append([year, line])
        tone = "" if option.tone == "even" else option.tone or (
            "up" if option.permanent > 0 or option.season > 0 else
            "down" if option.permanent < 0 or option.season < 0 else "")
        story = fill(option.story, name, club_name) if option.story else ""
        if option.trade and target is not None:
            story = f"You agreed to the trade, and started the season at {offer.franchise}."
        if now is not None:
            say(f"{story} {fill(now.text, name, club_name)}".strip(),
                "up" if now.season > 0 or now.permanent > 0 else "down")
        else:
            say(story, tone)
        if outcome is not None and outcome.next_season and any(o.permanent > 0 for o in option.outcomes):
            say("Whether it works, you will see next season.")
        money = endorsement_fee(rating_at(start, top, t)) if option.money else 0
        if option.thread == "mentor" and person is not None:
            cp.threads.append({"k": "mentor", "id": person.person_id, "name": person.name,
                               "year": year})
        if option.thread == "pupil" and person is not None:
            cp.threads.append({"k": "pupil", "id": person.person_id, "name": person.name,
                               "year": year})
        if event.key == "lead":
            cp.threads.append({"k": "lead", "club": canonical(offer.franchise)})
        cp.threads = [th for th in cp.threads if not (th["k"] == "press_feud" and event.key == "press_feud")]
        before = ability(start, top, arc_point(clock, perm_before, 0.0, shape), setup=setup,
                         fs_id=offer.fs_id, year=year, franchise=offer.franchise, role=rd)
        after = ability(start, top, arc_point(clock, cp.permanent, 0.0, shape), setup=setup,
                        fs_id=offer.fs_id, year=year, franchise=offer.franchise, role=rd)
        say(skill_words(before, after) or "", skill_tone(before, after))
        option = dataclasses.replace(option, missed=extra)
        # what keeps you out when an event card does (A216): it names itself
        absence_label = (event.title.lower() if option.injury or (now and now.missed) or
                         event.key in ("workload", "home_league") and extra else
                         "an injury that came back" if landed_missed else "")
        you = ability(start, top, t, setup=setup, fs_id=offer.fs_id, year=year,
                      franchise=offer.franchise, role=rd)
        captain_now = option.captain or (already and not squad_now)
        plan: tuple[tuple[str, str], ...] = ()
        bowlers: list[Card] = plan_bowlers(deck, you, offer.fs_id) if captain_now else []
        if bowlers:
            move = nxt()
            if move is None:
                career.pending = Pending("plan", year, k, squad=squad_now, offers=[offer],
                                         plan_bowlers=bowlers,
                                         plan={c.person_id: cp.plan[c.person_id]
                                               for c in bowlers if c.person_id in cp.plan})
                return career
            if not move.startswith("p"):
                raise CareerError(f"expected a bowling plan, got {move!r}")
            plan = parse_plan(move[1:], bowlers)
        record = SeasonRecord(k=k, year=year, franchise=offer.franchise, short=offer.short,
                              price=offer.price, stayed=offer.stay, event=event.key,
                              choice=fill(option.label, name, club_name), captain=captain_now,
                              ability=t,
                              teams=0, squad=squad_now, extra_missed=option.missed, mega=mega_note,
                              contract_year=(CONTRACT_YEARS - cp.contract.left + 1
                                             if cp.contract else 0),
                              rating=rating_at(start, top, t), absence=absence_label,
                              tenure=tenure_at(cp.seasons, offer.franchise),
                              role=rd, coach=coach_rec)
        # Read the PLAYED record, never `record`: on a cache hit `record` is the blank one
        # this loop built, and the next season's offers would price you as a 70.
        record.plan = [[c.name, dict(plan)[c.person_id]] for c in bowlers
                       if c.person_id in dict(plan)]
        if event.who == "rival" and person is not None:
            record.rival = [person.franchise, person.name]
        answers = take_answers(bowlers)
        current = dict(plan)               # the plan as it stands after this season's replans
        for a in answers:
            if isinstance(a, tuple) and a[0] != "i":
                current = dict(a[1])
        try:
            played, career.detail = _cached_season(deck, model, seed, k, you, record,
                                                   (setup, start, top, offer, option, t,
                                                    squad_now, role_key(rd), record.tenure),
                                                   answers, plan,
                                                   incidents_spent(cp.seasons))
        except _NeedMoment as need:
            # The season stops at a key moment, or at something that happened (A230): told up
            # to here, waiting for your call.
            so_far = Year(need.season, need.yours,
                          list(need.season.results) + list(need.season.playoffs or []),
                          need.roster, 0, need.approaches, need.dropped, need.verdicts,
                          need.absent, need.absence_reason, incident=need.incident,
                          incident_out=need.incident_out)
            career.detail = season_detail(so_far, complete=False)
            career.live_milestones = career_milestones(cp.seasons, career.detail.feed)
            career.pending = Pending("incident" if need.prompt else "moment", year, k,
                                     squad=squad_now, offers=[offer], moment=need.moment,
                                     incident=need.prompt,
                                     plan_bowlers=bowlers, plan=current)
            return career
        played.form = played.display
        # Set on the PLAYED record (A224's reason): the season cache keys on the role, but two
        # careers can reach one season having said different things to the coach.
        played.role, played.coach = rd, coach_rec
        played.display = reputation_floor([r.display for r in cp.seasons], played.display)
        played.extra_earnings = money
        # [A230] What happened during the season, and what your answer set going.
        inc = played.incident
        inc_age = 0.0
        if inc:
            say(" ".join(x for x in (inc["story"], inc["turn"]) if x), inc["tone"])
            cp.permanent += points_t(inc["permanent"], start, top)
            cp.standing += inc["standing"]
            inc_age = inc["age"]
            for line in inc["injuries"]:
                cp.injuries.append([year, line])
            if inc["thread"]:
                cp.threads.append({"k": inc["thread"], "year": year})
        cp.standing = max(STANDING_MIN, min(STANDING_MAX, cp.standing))
        played.milestones = career_milestones(
            cp.seasons, career.detail.feed if career.detail else None, played)
        # [A229] The selectors read the season; a new international is a milestone.
        country = setup.nation
        if country:
            sel = national.select(cp.national, played.display, seed, k, setup.overseas)
            played.national, played.caps, played.caps_total = sel.event, sel.caps, sel.total
            cp.national = {"capped": sel.capped, "in_side": sel.in_side, "caps": sel.total}
            if sel.event == "debut":
                played.milestones.append(CareerMilestone("international", f"{country} debut"))
            for m in _marks(sel.total - sel.caps, sel.total, national.CAP_MARK):
                played.milestones.append(CareerMilestone("caps", f"{m} internationals for {country}"))
            said = national.words(sel, country)
            if said:
                say(said, "up" if sel.event in ("debut", "kept", "recalled") else
                    "down" if sel.event == "dropped" else "")
        for key in setup.ambitions:
            state, when = ambition_status(key, cp.seasons + [played])
            if state == "done" and when == year:
                played.milestones.append(CareerMilestone(
                    "ambition", f"Ambition achieved: {AMBITIONS[key][0]}"))
        career.live_milestones = played.milestones
        played.press = press.season_facts(_press_facts(deck, cp, played, offer, k, year,
                                                        career.auction, setup),
                                          career.detail.feed if career.detail else None)
        cp.seasons.append(played)
        cp.current, cp.last_display = canonical(offer.franchise), played.display
        cp.k += 1
        if cp.contract is not None:
            cp.contract.left -= 1
            if cp.contract.left == 0:
                cp.contract = None
        was_squad = cp.squad
        season_role = rd["role"] if rd else setup.role
        cp.squad, released = form_after(cp.squad, played.percentile, poor_bar(season_role))
        if k < YOUTH_SEASONS:
            if cp.squad:
                say(f"A poor season, but at {DEBUT_AGE + k} the franchise is giving you time.")
            cp.squad, released = False, False      # a youngster gets time (A214)
        elif cp.squad and not was_squad and cp.standing >= STANDING_BACKED:
            # [A230] A club that trusts you keeps you in the side through a poor season, once:
            # it spends some of that trust.
            cp.squad = False
            cp.standing -= 1
            say("A poor season, but the club stood by you. You keep your place.", "up")
        growing = shape.growing(clock)
        dev = round(2 * DEV_POINTS * (played.percentile - 0.5), 1) if growing else 0.0
        played.development = dev
        cp.permanent += points_t(dev, start, top)
        if abs(dev) >= 0.5:
            say(development_words(dev, k), "up" if dev > 0 else "down")
        if played.absence and not played.extra_missed:
            block = played.missed - played.dropped - (inc["out"] if inc else 0)
            if block > 0:
                say(absence_words(played.absence, block, played.absent_from))
        # [A224] Set on the PLAYED record, never on `record` before the season: the season
        # cache shares a record between careers that reach it with different clocks.
        played.potential, played.clock = cp.potential, clock
        step = 1.0 + option.age + inc_age
        if shape.declining(clock):
            if played.form >= played.rating + CLOCK_MARGIN:
                step += CLOCK_SLOW - 1.0
                say(clock_words(CLOCK_SLOW, k), "up")
            elif played.percentile < poor_bar(season_role):
                step += CLOCK_FAST - 1.0
                say(clock_words(CLOCK_FAST, k), "down")
        cp.clock = clock + step
        hint = shape_words(shape, clock, cp.clock, DEBUT_AGE + k)
        if hint:
            say(hint, "down" if "catching up" in hint else "up" if hint.startswith(
                ("At ", "Still", "Past your peak now, but")) else "")
        raised = grow_potential(cp, played.form, growing, start, ceiling, legend, shape)
        if raised:
            say(raised, "up")
        coach_after(cp, setup, rd, played, say)
        career.role_now = career.season_role = cp.roles[-1] if cp.roles else setup.role
        played.notes, played.tones = notes, tones
        cp.captain = next_captain(captain_now, cp.squad, canonical(offer.franchise))
        if bowlers:
            cp.plan = current
        if released and cp.contract is not None:
            released = False                   # a contract year remains: you stay, benched
        if released and played.rating >= STAR_RATING:
            released = False                   # a star is never out of work (A214)
            played.notes.append("Two poor seasons, but at your level somebody always signs you.")
            played.tones.append("")
        if released:
            career.released, cp.ended = True, "released"
            break
    # [A228] Where did the player go after the game? Asked once, when the career ends; a
    # checkpoint from before A228 carries "" and is never asked.
    if cp.epilogue is None:
        move = nxt()
        if move is None:
            last = max(cp.k - 1, 0)
            career.checkpoint = copy.deepcopy(cp)
            career.pending = Pending("epilogue", setup.debut + last, last)
            return career
        cp.epilogue = list(press.EPILOGUES)[_index(move, "w", len(press.EPILOGUES))]
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
        what = {"o": "an offer", "c": "an offer", "e": "an event answer", "m": "a key-moment call",
                "i": "an answer to what happened", "t": "an answer to the coach",
                "b": "a base price", "w": "a where-are-they-now"}.get(kind, "a")
        raise CareerError(f"expected {what} move, got {move!r}")
    i = int(move[1:])
    if not 0 <= i < n:
        raise CareerError(f"no choice {i} here")
    return i


# --- automatic players, for the CLI and phase 2's calibration ---------------------------


def policy_move(career: Career, policy: str, rng: random.Random) -> str:
    p = career.pending
    if p.kind == "epilogue":                   # A228
        return f"w{rng.randrange(len(press.EPILOGUES)) if policy == 'random' else 0}"
    if p.kind == "coach":                      # A238: careless at random, the rest say yes
        return f"t{rng.randrange(len(p.coach['options'])) if policy == 'random' else 0}"
    if p.kind == "plan":                       # A213: the automatic players set none
        return "p" + "0" * len(p.plan_bowlers)
    if p.kind == "mega":                       # A212
        m = p.mega
        if policy == "random":
            return rng.choice((["k"] if m.retain else []) + [f"b{i}" for i in range(len(m.bases))])
        return "k" if m.retain else f"b{m.suggested}"
    if p.kind == "moment":
        return f"m{rng.randrange(3) if policy == 'random' else 1}"
    if p.kind == "incident":                   # A230
        return f"i{rng.randrange(len(p.incident['options'])) if policy == 'random' else 0}"
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
        print(f"  {r.year} {r.short:9s} {crore(r.price):>9s}  rating {r.rating}  grade {r.display}  "
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
