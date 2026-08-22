"""The daily challenge's scenario: what today asks of you, and whether you did it.

Deliberately PURE. Nothing here touches a database, the deck, or the simulator -- a
scenario is evaluated from a finished `Result` and the side that belongs to the player,
both of which the caller already has. That is what lets every rule in this file be tested
without a fixture more elaborate than a hand-built innings, and it is why scoring can be
re-checked against a stored result later without replaying anything.

**Every day generated today is a FULL two-innings match, and the kind is the WIN
CONDITION.** Win by more than N runs; win with at least N wickets in hand; chase inside N
overs. That is the whole point of the shape: both innings are real, so the five bowlers in
a drafted twelve are on the field every single day rather than on two days in three.

**This reverses the target rule the first three kinds were built on, deliberately.** Those
kinds fixed a chase's target when the day was created, against a synthetic league-average
attack, so that everybody chased the same number -- and the price was that nobody ever
bowled. A live kind's target is set by the opposition batting against the PLAYER'S OWN
attack, so it differs from player to player. Comparability survives because the objective
is now RELATIVE rather than absolute: everyone faces the same historical side and is asked
for the same margin over it. It also compounds the right way round -- a good attack sets a
lower target, which is easier to chase both quickly and with wickets intact.

**The margin unit is fixed by the scenario, so it is homogeneous within a day.** This is
the thing that makes ranking tractable: if today is "win by 20" everyone qualifying is
ranked on runs, and if today is "chase inside 17 overs" everyone is ranked on balls to
spare. There is never a runs-against-wickets comparison to fudge, because everybody is
answering the same question. Ranking is therefore `objective_met`, then `margin`, then
`bonus_points` -- the order ratified for the leaderboard.

**The first three kinds stay readable and playable, and are no longer generated.** A stored
day must keep replaying exactly as it was scored, which is the entire reason migration 032
stores a scenario rather than deriving it; deleting a kind would move every past
leaderboard. `DEFEND_BY` in particular is kept alongside the nearly-identical
`WIN_BY_RUNS` rather than renamed into it, because the two differ in which attack the
player's own innings faces -- collapsing them would silently re-score old days.
"""

from __future__ import annotations

from dataclasses import dataclass

from game.simulator import BALLS_PER_OVER, Innings

# An innings ends at ten wickets; "wickets in hand" is what is left of that.
WICKETS_PER_INNINGS = 10
# A full twenty overs, used to express "balls to spare" for a chase.
BALLS_PER_INNINGS = 20 * BALLS_PER_OVER
# The powerplay, from game.analysis's own constant rather than a number invented here --
# a bonus that reads a phase must read the same phase every other screen does.
POWERPLAY_OVERS = 6

# Legacy: generated up to 2026-08-21, still stored on past days, still scored exactly as
# they were. A chase against a target fixed at day creation; the player never bowls.
CHASE = "chase"
CHASE_WITH_WICKETS = "chase_with_wickets"
DEFEND_BY = "defend_by"

# Live: a full match, both innings real.
WIN_BY_RUNS = "win_by_runs"
WIN_BY_WICKETS = "win_by_wickets"
CHASE_IN_OVERS = "chase_in_overs"

RUNS = "runs"
WICKETS = "wickets"
BALLS = "balls"


@dataclass(frozen=True)
class _Kind:
    """What a kind IS, as data rather than as a branch repeated at every call site.

    Every behavioural question the rest of the app asks about a day -- which innings is the
    player's, whether their bowlers take the field, whose attack they bat against, what the
    margin is measured in -- is answered from this table. A new kind is a row here plus a
    line of wording, and nothing downstream has to grow another `if kind == ...`."""

    bats_first: bool
    # The target was fixed when the day was created and the opposition is not replayed per
    # player. True only for the two legacy chases; it is also exactly the condition under
    # which the player does not bowl, since a fixed target means nobody bowled at them.
    fixed_target: bool
    # Whether the player's own innings faces the opposition's REAL attack. False on the
    # legacy kinds, which used the synthetic league-average five for both sides.
    real_opposition_attack: bool
    margin_unit: str
    requires: tuple[str, ...]


KINDS: dict[str, _Kind] = {
    CHASE: _Kind(False, True, False, WICKETS, ("target",)),
    CHASE_WITH_WICKETS: _Kind(False, True, False, WICKETS, ("target", "wickets_required")),
    DEFEND_BY: _Kind(True, False, False, RUNS, ("runs_required",)),
    WIN_BY_RUNS: _Kind(True, False, True, RUNS, ("runs_required",)),
    WIN_BY_WICKETS: _Kind(False, False, True, WICKETS, ("wickets_required",)),
    CHASE_IN_OVERS: _Kind(False, False, True, BALLS, ("overs_required",)),
}

SCENARIO_KINDS = tuple(KINDS)
# What a new day may be. A strict subset of the above, and the gap between the two is the
# point: a retired kind stays constructible so a stored day still replays.
GENERATED_KINDS = (WIN_BY_RUNS, WIN_BY_WICKETS, CHASE_IN_OVERS)


@dataclass(frozen=True)
class Scenario:
    """One day's challenge.

    `target` is the score to BEAT, exactly as `play_innings`'s own `target` argument means
    it -- the opposition's total, not the total plus one -- so nothing here has to remember
    an off-by-one the engine has already settled. It is set only on the legacy chases,
    where the number was fixed in advance; a live kind's target is whatever the opposition
    makes on the day, so it is not a property of the scenario at all.

    Each floor is None on the kinds that do not use it rather than carrying a zero, so a
    scenario that forgot to set one fails loudly instead of silently asking for nothing
    (A23's rule about an unobserved value never acquiring a plausible default)."""

    kind: str
    opposition_fs_id: int
    opposition_name: str
    stage: str = "Final"
    target: int | None = None
    wickets_required: int | None = None
    runs_required: int | None = None
    overs_required: int | None = None
    # THE day's one bonus. None only on a day generated before a day carried one, which
    # keeps scoring every stored day exactly as it was scored.
    bonus: str | None = None

    def __post_init__(self) -> None:
        spec = KINDS.get(self.kind)
        if spec is None:
            raise ValueError(f"unknown scenario kind {self.kind!r}")
        for field_name in spec.requires:
            if getattr(self, field_name) is None:
                raise ValueError(f"{self.kind} needs {field_name}")
        if self.bonus is not None:
            if self.bonus not in BONUS_TESTS:
                raise ValueError(f"unknown bonus {self.bonus!r}")
            if not kind_offers(self.kind, self.bonus):
                raise ValueError(f"{self.kind} cannot award {self.bonus}")

    @property
    def spec(self) -> _Kind:
        return KINDS[self.kind]

    @property
    def player_bats_first(self) -> bool:
        """Which innings is the player's."""
        return self.spec.bats_first

    @property
    def player_bowls(self) -> bool:
        """Whether the player's own attack takes the field at all.

        False exactly on the legacy chases: their target was fixed at day creation against
        a synthetic attack, so the opposition's innings is not the player's doing and
        carries no bowling of theirs to read."""
        return not self.spec.fixed_target

    @property
    def opposition_bowls(self) -> bool:
        """Whether the player bats against the opposition's real attack, rather than the
        league-average five the legacy kinds used on both sides."""
        return self.spec.real_opposition_attack

    @property
    def margin_unit(self) -> str:
        return self.spec.margin_unit

    def _against(self) -> str:
        # "the Final" and "the Eliminator" take an article; "Qualifier 1" does not.
        # Player-facing copy, so it is worth the one conditional.
        stage = self.stage if self.stage.startswith("Qualifier") else f"the {self.stage}"
        return f"{self.opposition_name} in {stage}"

    def describe(self) -> str:
        """The one line shown before a ball is bowled. Written here rather than in the
        page so the wording cannot drift from the rule it describes."""
        against = self._against()
        if self.kind == CHASE:
            return f"Chase {self.target + 1} against {against}."
        if self.kind == CHASE_WITH_WICKETS:
            return (f"Chase {self.target + 1} against {against} "
                    f"with at least {self.wickets_required} wickets in hand.")
        if self.kind in (DEFEND_BY, WIN_BY_RUNS):
            return (f"Bat first against {against} and win by more than "
                    f"{self.runs_required} runs.")
        if self.kind == WIN_BY_WICKETS:
            return (f"Bowl first against {against}, then chase their total with at least "
                    f"{self.wickets_required} wickets in hand.")
        return (f"Bowl first against {against}, then chase their total "
                f"inside {self.overs_required} overs.")

    def short(self) -> str:
        """The same challenge in a few words, for a shared line and a board header. One
        function rather than a second branch in `web.daily.share_text`, which is where the
        wording would otherwise drift from the rule."""
        if self.target is not None:
            return f"Chase {self.target + 1}"
        if self.margin_unit == RUNS:
            return f"Win by {self.runs_required}+ runs"
        if self.margin_unit == WICKETS:
            return f"Win by {self.wickets_required}+ wickets"
        return f"Chase inside {self.overs_required} overs"


@dataclass(frozen=True)
class Outcome:
    objective_met: bool
    margin: int                 # in `scenario.margin_unit`
    bonus_points: int
    bonuses_met: tuple[str, ...]
    summary: str


def overs_words(balls: int) -> str:
    """Balls as cricket writes them: 20 balls is 3.2 overs."""
    return f"{balls // BALLS_PER_OVER}.{balls % BALLS_PER_OVER}"


# --- bonuses ---------------------------------------------------------------------------
#
# Each is a pure question about one of the two innings. Points are declared game-design
# values, not measurements -- the same category as REPUTATION and ALLROUNDER_RUNS
# (A57/A59) -- and every test is a cricket landmark rather than a calibrated threshold, so
# none of them needs a number swept out of the engine's own distribution.
#
# WHICH INNINGS a bonus reads is declared beside it, and availability is DERIVED from that
# rather than branched on the kind. The old rule was a hand-written `if kind == DEFEND_BY`,
# which was correct when there were three kinds and would have gone quietly stale on the
# fourth -- a promise of a bonus that cannot be earned is the A70 shape.

MINE = "mine"
THEIRS = "theirs"

OPENER_CENTURY = "opener_century"
TEN_SIXES = "ten_sixes"
NO_POWERPLAY_WICKET = "no_powerplay_wicket"
LOWER_ORDER_FIFTY = "lower_order_fifty"
FINISHED_EARLY = "finished_early"
FOUR_WICKET_HAUL = "four_wicket_haul"
BOWLED_THEM_OUT = "bowled_them_out"
THREE_IN_AN_OVER = "three_in_an_over"
MAIDEN_OVER = "maiden_over"


def _openers(innings: Innings) -> list:
    """Positions one and two. `Innings.batting` is built in batting order (the list index
    IS the position, A110), so this is a slice rather than a search."""
    return innings.batting[:2]


def _wickets_after_powerplay(innings: Innings) -> int:
    """Wickets down at the end of the sixth over.

    `over_log` holds fully completed overs only, so an innings that ended inside the
    powerplay has no sixth entry -- in which case the innings total IS the answer, because
    nothing more can fall."""
    log = innings.over_log
    if len(log) >= POWERPLAY_OVERS:
        return log[POWERPLAY_OVERS - 1].wickets
    return innings.wickets


BONUS_TESTS = {
    OPENER_CENTURY: (MINE, lambda i: any(b.runs >= 100 for b in _openers(i))),
    TEN_SIXES: (MINE, lambda i: i.sixes >= 10),
    NO_POWERPLAY_WICKET: (MINE, lambda i: _wickets_after_powerplay(i) == 0),
    # Position seven and lower: index 6 onward, and `faced_any` because a man who never
    # came out has not made a fifty from anywhere.
    LOWER_ORDER_FIFTY: (MINE, lambda i: any(b.runs >= 50 and b.faced_any
                                            for b in i.batting[6:])),
    FINISHED_EARLY: (MINE, lambda i: i.chased
                     and BALLS_PER_INNINGS - i.balls >= EARLY_FINISH_BALLS),
    FOUR_WICKET_HAUL: (THEIRS, lambda i: any(b.wickets >= 4 for b in i.bowling)),
    BOWLED_THEM_OUT: (THEIRS, lambda i: i.wickets >= WICKETS_PER_INNINGS),
    THREE_IN_AN_OVER: (THEIRS, lambda i: any(o.over_wickets >= 3 for o in i.over_log)),
    MAIDEN_OVER: (THEIRS, lambda i: any(o.over_runs == 0 for o in i.over_log)),
}

# Measured hit rates, same two sweeps, in the BOWL-FIRST orientation (rational / random):
# opener_century 0.8/2.4, ten_sixes 5.2/8.0, no_powerplay_wicket 22.0/18.8,
# lower_order_fifty 0.0/0.4, finished_early 35.2/19.6, four_wicket_haul 17.5/13.6,
# bowled_them_out 16.2/8.0, three_in_an_over 8.0/4.4, maiden_over 12.2/10.0. Batting first
# plays the full twenty rather than ending on a chase, so the tail bats more often there
# and the two batting rarities are floors rather than estimates.
#
# Since a day now carries ONE bonus (`daily_bonus`), everybody on a given day is offered
# the same one, so within a day the points are binary -- earned or not -- and the VALUE
# cannot move the board. It is a statement of rarity to the player, and a way of comparing
# one day's card with another's, and no longer a tiebreak weight.
#
# LOWER_ORDER_FIFTY is priced at a century's 25 for its rarity rather than trimmed to a
# reachable 30: thirty from a number eight is a useful cameo and not a landmark, and a
# calibrated bar is exactly what every other bonus here avoids. A rare bonus that pays well
# is a fine thing; a rare bonus that pays middling is just decoration.
BONUS_POINTS = {
    OPENER_CENTURY: 25,
    LOWER_ORDER_FIFTY: 25,
    THREE_IN_AN_OVER: 20,
    TEN_SIXES: 15,
    FOUR_WICKET_HAUL: 15,
    BOWLED_THEM_OUT: 15,
    NO_POWERPLAY_WICKET: 10,
    MAIDEN_OVER: 10,
    FINISHED_EARLY: 10,
}

BONUS_LABELS = {
    OPENER_CENTURY: "An opener scored a century",
    THREE_IN_AN_OVER: "Three wickets in one over",
    TEN_SIXES: "Ten sixes in the innings",
    LOWER_ORDER_FIFTY: "A number 7 or lower made fifty",
    FOUR_WICKET_HAUL: "A bowler took four wickets",
    BOWLED_THEM_OUT: "Bowled them out",
    NO_POWERPLAY_WICKET: "No wicket lost in the powerplay",
    MAIDEN_OVER: "A maiden over",
    FINISHED_EARLY: "Chased with two overs to spare",
}

# A stable order, so two players earning the same set list it the same way.
BONUS_ORDER = (OPENER_CENTURY, TEN_SIXES, NO_POWERPLAY_WICKET, LOWER_ORDER_FIFTY,
               FINISHED_EARLY, FOUR_WICKET_HAUL, BOWLED_THEM_OUT, THREE_IN_AN_OVER,
               MAIDEN_OVER)

# "Two overs to spare" in balls -- expressed from the constant rather than written as 12,
# so it stays two overs if an over ever stops being six balls.
EARLY_FINISH_BALLS = 2 * BALLS_PER_OVER


def kind_offers(kind: str, bonus: str) -> bool:
    """Whether a KIND can award this bonus at all -- structural, and asked before any day
    has chosen one.

    Two rules, both derived. A bonus reading the opposition's innings needs the player to
    have bowled it. And FINISHED_EARLY is withheld on a CHASE_IN_OVERS day because finishing
    early IS the objective there -- paying a bonus for the thing already being marked is
    paying twice for one piece of cricket."""
    spec = KINDS[kind]
    reads, _test = BONUS_TESTS[bonus]
    if reads is THEIRS and spec.fixed_target:
        return False
    if bonus is FINISHED_EARLY:
        return not spec.bats_first and kind != CHASE_IN_OVERS
    return True


def bonuses_on_offer(scenario: Scenario) -> list[str]:
    """What today can award. ONE bonus, the day's own, so a card is worth chasing rather
    than being one of nine things that might happen to land.

    A scenario with no bonus is a day generated before a day carried one, and it falls back
    to everything its kind could award -- which is exactly how it was scored, and a stored
    day must go on scoring the way it did."""
    if scenario.bonus is not None:
        return [scenario.bonus]
    return [b for b in BONUS_ORDER if kind_offers(scenario.kind, b)]


def bonuses_earned(scenario: Scenario, mine: Innings,
                   theirs: Innings | None) -> tuple[str, ...]:
    """Which bonuses this performance earned, in a stable order.

    `theirs` is the innings the player BOWLED at, so a bowling bonus reads the opposition's
    card -- getting this the wrong way round is the A101 failure exactly (that bug credited
    every side's bowling figures to its opponent), so the two are named rather than
    positional at every call site."""
    earned = []
    for bonus in bonuses_on_offer(scenario):
        reads, test = BONUS_TESTS[bonus]
        innings = mine if reads is MINE else theirs
        if innings is not None and test(innings):
            earned.append(bonus)
    return tuple(earned)


# --- marking a day ----------------------------------------------------------------------

def _target_of(scenario: Scenario, theirs: Innings | None) -> int:
    """The score the player had to beat. Stored on the scenario for a legacy chase, and
    simply whatever the opposition made for a live one."""
    if scenario.spec.fixed_target:
        return scenario.target
    return theirs.runs


def evaluate(scenario: Scenario, mine: Innings, theirs: Innings | None = None) -> Outcome:
    """Did the player do what today asked, by how much, and what did they pick up on the way.

    `mine` is always the player's own innings; the caller resolves that from
    `scenario.player_bats_first`, so nothing in here reasons about home and away.

    `theirs` may be None ONLY for a legacy chase, and that is a real property of that
    format rather than a missing argument: its target was fixed when the day was created,
    so the opposition was not replayed per player and nobody bowled at them. Every kind
    generated today plays both innings for real, so both are always present."""
    if theirs is None and not scenario.spec.fixed_target:
        raise ValueError(f"{scenario.kind} is decided by both innings; it needs the other")
    bonuses = bonuses_earned(scenario, mine, theirs)
    points = sum(BONUS_POINTS[b] for b in bonuses)
    unit = scenario.margin_unit

    if unit == RUNS:
        margin = mine.runs - theirs.runs
        met = margin > scenario.runs_required
        summary = (f"Won by {margin} runs" if margin > 0
                   else f"Lost by {-margin} runs" if margin < 0 else "Tied")
        return Outcome(met, margin, points, bonuses, summary)

    chased = mine.chased
    in_hand = WICKETS_PER_INNINGS - mine.wickets
    short = _target_of(scenario, theirs) + 1 - mine.runs

    if unit == WICKETS:
        met = chased and (scenario.wickets_required is None
                          or in_hand >= scenario.wickets_required)
        # A FAILED chase is ranked on how close it came, as a negative number, and this is
        # a correction rather than a flourish: ranking a failure on wickets in hand rewards
        # not losing any, so a side that blocked out twenty overs for 100/1 would place
        # above one that fell two runs short at 149/9. Nothing is compared across the two,
        # because `objective_met` tiers them first -- margin only ever orders successes
        # against successes and failures against failures.
        margin = in_hand if chased else -short
        summary = (f"Chased with {in_hand} wickets in hand" if chased
                   else f"Fell {short} short")
        return Outcome(met, margin, points, bonuses, summary)

    # CHASE_IN_OVERS. Two DIFFERENT failures live in the same tier here -- a chase
    # completed too slowly, and a chase not completed at all -- and ordering the second
    # above the first would be wrong. Balls to spare is always >= 0 when the target is
    # hit and the runs-short fallback is always <= -1, so within the failed tier every
    # slow winner sits above every loser without needing a fourth key. Same trick the
    # wickets branch above already uses, one tier down.
    spare = BALLS_PER_INNINGS - mine.balls
    met = chased and mine.balls <= scenario.overs_required * BALLS_PER_OVER
    margin = spare if chased else -short
    summary = (f"Chased with {overs_words(spare)} overs to spare" if chased
               else f"Fell {short} short")
    return Outcome(met, margin, points, bonuses, summary)


def rank_key(objective_met: bool, margin: int, bonus_points: int) -> tuple:
    """The ratified leaderboard order: everyone who met the objective above everyone who
    did not, margin within each tier, bonuses breaking the tie.

    A function rather than an ORDER BY written out at each call site, because the board and
    any "your rank" lookup must agree exactly -- two copies of a sort order is two places
    for it to drift, and a leaderboard that disagrees with itself is worse than none."""
    return (objective_met, margin, bonus_points)


# --- generating a day --------------------------------------------------------------------

# The day's shared deck. Sixteen rather than the twelve a drafter needs, and the number is
# MEASURED rather than chosen: dealt exactly twelve squads, a careless drafter strands 21%
# of the time (600 trials against the real deck), because a fixed sequence cannot re-draw
# the way the live draft's guarantee does. Sixteen with the sequence cycling takes that to
# ~1%, and cycling further does not help -- 12 passes measured identical to 3 -- because
# the residual is A73's wildcard-optimism gap rather than running out of squads. The last
# 1% needs the deal-time guarantee, not a bigger deck.
DAILY_DECK_SIZE = 16

# Every player sees the same squads; only the ORDER is theirs. Cycling means a squad passed
# over early can be come back to, which is what the measurement above shows is necessary.
DAILY_DECK_PASSES = 3


def daily_seed(challenge_date) -> int:
    """The date, and nothing else, decides the day. Deliberately not `hash()`: that is
    salted per process in CPython, so two servers would disagree about what today is.

    An ordinal is enough -- it is stable, monotonic, and the scenario generator mixes it
    properly through `random.Random` anyway."""
    return challenge_date.toordinal()


def choose_deck(rng, fs_ids, size: int = DAILY_DECK_SIZE) -> list[int]:
    """The day's shared squads. Sampled WITHOUT replacement, so nobody is dealt the same
    franchise-season twice in one challenge."""
    if len(fs_ids) < size:
        raise ValueError(f"deck has only {len(fs_ids)} franchise-seasons, need {size}")
    return sorted(rng.sample(list(fs_ids), size))


def player_order(deck_fs_ids, account_id: int, challenge_date) -> list[int]:
    """One player's own sequence through the day's shared deck.

    Seeded from the account AND the date, so it is reproducible -- a player who reloads
    gets the same order back, and a result can be re-verified later from nothing but the
    row. Two players on the same day get different orders; one player on two days gets
    different orders too."""
    seq = list(deck_fs_ids)
    __import__("random").Random(f"{challenge_date}:{account_id}").shuffle(seq)
    return seq


# The floors each live kind asks for. The RANGE is a declared game-design value in the same
# category as BONUS_POINTS -- what makes one day feel different from the last -- but which
# values are IN it was measured rather than chosen to look plausible, by
# `tools/daily_calibration.py` over real drafted twelves against real historical sides.
# Re-run it if the ratings or the engine move.
#
# Share of matches meeting the floor, `rational` drafter (400 matches, seed 11) against
# `random` (250, seed 3) -- an upper and a lower bound on a real player, who sees no
# ratings while drafting and so sits between the two:
#
#     win by >    10     15     20     25     30      (won at all: 62.0% / 36.0%)
#              48.8%  43.0%  39.0%  34.0%  29.2%      rational
#              24.8%  21.6%  18.0%  16.0%  14.4%      random
#     wickets      3      4      5      6             (chased at all: 57.8% / 39.2%)
#              55.8%  51.7%  46.2%  40.2%             rational
#              36.8%  35.6%  30.0%  22.4%             random
#     inside      16     17     18     19
#              17.2%  27.0%  35.2%  47.0%             rational
#               8.0%  10.4%  19.6%  28.0%             random
#
# Two values measured and then EXCLUDED, named so the question is cheap to revisit rather
# than re-asked from scratch. A 3-wicket floor is not a floor: at 55.8% against 57.8% who
# chase at all, it rules out two points and asks for nothing beyond winning. And 16 overs
# is met by 8.0% of careless drafts -- a day almost nobody wins still produces a full
# board, since margin ranks the failures too, but three such days a fortnight is a worse
# game than three merely hard ones.
RUN_MARGINS = (10, 15, 20, 25, 30)
WICKET_FLOORS = (4, 5, 6)
OVERS_LIMITS = (17, 18, 19)
STAGES = ("Final", "Qualifier 1", "Eliminator", "Qualifier 2")

# How often each live kind comes up. A dict rather than a bare `rng.choice` over
# GENERATED_KINDS so the mix is one edit rather than a restructure -- the shape of the day
# is a game-design dial, and the three are not equally hard.
KIND_WEIGHTS = {WIN_BY_RUNS: 1, WIN_BY_WICKETS: 1, CHASE_IN_OVERS: 1}


def daily_bonus(challenge_date) -> str:
    """Today's ONE bonus, by strict rotation over `BONUS_ORDER`.

    Rotation rather than a draw, so the cycle is complete and even -- a random pick would
    repeat one bonus three days running and leave another unseen for a fortnight, which is
    the opposite of what makes a single bonus worth chasing. The period is
    `len(BONUS_ORDER)` days and it never repeats on consecutive days, because the index
    advances by exactly one each day and nothing skips.

    Not seeded through the day's rng at all. The rotation IS the date, so it is legible --
    a player can see it come round -- and a change to the scenario generator cannot shift
    it out from under an unfinished week."""
    return BONUS_ORDER[daily_seed(challenge_date) % len(BONUS_ORDER)]


def generate(rng, opposition_fs_id: int, opposition_name: str,
             challenge_date) -> Scenario:
    """One day's scenario, given the side it is played against.

    No opposition score is passed in, and its absence is the reversal this module documents
    at the top: a live kind has no target to fix, because the target is whatever the
    opposition makes against the player's own bowling on the day.

    **The BONUS is chosen first and the KIND accommodates it**, which is the one place these
    two decisions are not independent. Only `win_by_wickets` can offer FINISHED_EARLY --
    batting first there is nothing to finish, and on a `chase_in_overs` day it IS the
    objective -- so on that one day in nine the kind is forced. Doing it the other way round
    (kind first, then skip the rotation forward past a bonus it cannot award) would break
    the rotation's own guarantee and let the same bonus land on two consecutive days."""
    bonus = daily_bonus(challenge_date)
    kinds = [k for k in KIND_WEIGHTS if kind_offers(k, bonus)]
    kind = rng.choices(kinds, weights=[KIND_WEIGHTS[k] for k in kinds])[0]
    stage = rng.choice(STAGES)
    if kind == WIN_BY_RUNS:
        return Scenario(kind, opposition_fs_id, opposition_name, stage,
                        runs_required=rng.choice(RUN_MARGINS), bonus=bonus)
    if kind == WIN_BY_WICKETS:
        return Scenario(kind, opposition_fs_id, opposition_name, stage,
                        wickets_required=rng.choice(WICKET_FLOORS), bonus=bonus)
    return Scenario(kind, opposition_fs_id, opposition_name, stage,
                    overs_required=rng.choice(OVERS_LIMITS), bonus=bonus)
