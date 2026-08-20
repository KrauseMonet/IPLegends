"""The daily challenge's scenario: what today asks of you, and whether you did it.

Deliberately PURE. Nothing here touches a database, the deck, or the simulator -- a
scenario is evaluated from a finished `Result` and the side that belongs to the player,
both of which the caller already has. That is what lets every rule in this file be tested
without a fixture more elaborate than a hand-built innings, and it is why scoring can be
re-checked against a stored result later without replaying anything.

**The margin unit is fixed by the scenario, so it is homogeneous within a day.** This is
the thing that makes ranking tractable: if today is "chase 187" everyone qualifying is
ranked on wickets in hand, and if today is "win by more than 20" everyone is ranked on
runs. There is never a runs-against-wickets comparison to fudge, because everybody is
answering the same question. Ranking is therefore `objective_met`, then `margin`, then
`bonus_points` -- the order ratified for the leaderboard.

Three scenario kinds ship first. `chase` and `chase_with_wickets` differ only in whether a
wickets-in-hand floor is part of the OBJECTIVE or merely the margin, and `defend_by` is the
bat-first inversion. The opposition franchise-season and the stage label ("Final") are
PARAMETERS on all three rather than a fourth kind, so "chase 187 against CSK 2013 in a
final" is a `chase` with three fields set.
"""

from __future__ import annotations

from dataclasses import dataclass

from game.simulator import BALLS_PER_OVER, Innings

# An innings ends at ten wickets; "wickets in hand" is what is left of that.
WICKETS_PER_INNINGS = 10
# A full twenty overs, used to express "balls to spare" for a chase.
BALLS_PER_INNINGS = 20 * BALLS_PER_OVER

CHASE = "chase"
CHASE_WITH_WICKETS = "chase_with_wickets"
DEFEND_BY = "defend_by"
SCENARIO_KINDS = (CHASE, CHASE_WITH_WICKETS, DEFEND_BY)


@dataclass(frozen=True)
class Scenario:
    """One day's challenge.

    `target` is the score to BEAT, exactly as `play_innings`'s own `target` argument means
    it -- the opposition's total, not the total plus one -- so nothing here has to remember
    an off-by-one the engine has already settled.

    `wickets_required` is meaningful only for CHASE_WITH_WICKETS and `runs_required` only
    for DEFEND_BY; each is None on the kinds that do not use it rather than carrying a
    zero, so a scenario that forgot to set one fails loudly instead of silently asking for
    nothing (A23's rule about an unobserved value never acquiring a plausible default).
    """

    kind: str
    opposition_fs_id: int
    opposition_name: str
    stage: str = "Final"
    target: int | None = None
    wickets_required: int | None = None
    runs_required: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in SCENARIO_KINDS:
            raise ValueError(f"unknown scenario kind {self.kind!r}")
        if self.kind in (CHASE, CHASE_WITH_WICKETS) and self.target is None:
            raise ValueError(f"{self.kind} needs a target")
        if self.kind == CHASE_WITH_WICKETS and self.wickets_required is None:
            raise ValueError("chase_with_wickets needs wickets_required")
        if self.kind == DEFEND_BY and self.runs_required is None:
            raise ValueError("defend_by needs runs_required")

    @property
    def player_bats_first(self) -> bool:
        """Which innings is the player's. A chase is by definition second."""
        return self.kind == DEFEND_BY

    @property
    def margin_unit(self) -> str:
        return "runs" if self.kind == DEFEND_BY else "wickets"

    def describe(self) -> str:
        """The one line shown before a ball is bowled. Written here rather than in the
        page so the wording cannot drift from the rule it describes."""
        # "the Final" and "the Eliminator" take an article; "Qualifier 1" does not.
        # Player-facing copy, so it is worth the one conditional.
        stage = self.stage if self.stage.startswith("Qualifier") else f"the {self.stage}"
        against = f"{self.opposition_name} in {stage}"
        if self.kind == CHASE:
            return f"Chase {self.target + 1} against {against}."
        if self.kind == CHASE_WITH_WICKETS:
            return (f"Chase {self.target + 1} against {against} "
                    f"with at least {self.wickets_required} wickets in hand.")
        return f"Bat first against {against} and win by more than {self.runs_required} runs."


@dataclass(frozen=True)
class Outcome:
    objective_met: bool
    margin: int                 # in `scenario.margin_unit`
    bonus_points: int
    bonuses_met: tuple[str, ...]
    summary: str


# --- bonuses ---------------------------------------------------------------------------
#
# Each is a pure question about the player's own innings (and, for a bowling one, the
# innings they bowled at). Points are declared game-design values, not measurements --
# the same category as REPUTATION and ALLROUNDER_RUNS (A57/A59).

OPENER_CENTURY = "opener_century"
FOUR_WICKET_HAUL = "four_wicket_haul"
FINISHED_EARLY = "finished_early"

BONUS_POINTS = {OPENER_CENTURY: 25, FOUR_WICKET_HAUL: 15, FINISHED_EARLY: 10}
BONUS_LABELS = {
    OPENER_CENTURY: "An opener scored a century",
    FOUR_WICKET_HAUL: "A bowler took four wickets",
    FINISHED_EARLY: "Chased with two overs to spare",
}
# "Two overs to spare" in balls -- expressed from the constant rather than written as 12,
# so it stays two overs if an over ever stops being six balls.
EARLY_FINISH_BALLS = 2 * BALLS_PER_OVER


def _openers(innings: Innings) -> list:
    """Positions one and two. `Innings.batting` is built in batting order (the list index
    IS the position, A110), so this is a slice rather than a search."""
    return innings.batting[:2]


def bonuses_earned(scenario: Scenario, mine: Innings, theirs: Innings | None) -> tuple[str, ...]:
    """Which bonuses this performance earned, in a stable order.

    `theirs` is the innings the player BOWLED at, so a bowling bonus reads the opposition's
    card -- getting this the wrong way round is the A101 failure exactly (that bug credited
    every side's bowling figures to its opponent), so the two are named rather than
    positional at every call site."""
    earned = []
    if any(b.runs >= 100 for b in _openers(mine)):
        earned.append(OPENER_CENTURY)
    if theirs is not None and any(b.wickets >= 4 for b in theirs.bowling):
        earned.append(FOUR_WICKET_HAUL)
    if not scenario.player_bats_first and mine.chased:
        if BALLS_PER_INNINGS - mine.balls >= EARLY_FINISH_BALLS:
            earned.append(FINISHED_EARLY)
    return tuple(earned)


def evaluate(scenario: Scenario, mine: Innings, theirs: Innings | None = None) -> Outcome:
    """Did the player do what today asked, by how much, and what did they pick up on the way.

    `mine` is always the player's own innings; the caller resolves that from
    `scenario.player_bats_first`, so nothing in here reasons about home and away.

    `theirs` is None for a CHASE, and that is a real property of the format rather than a
    missing argument. A chase is against a target the day fixed in advance -- the same
    number for everybody, which is what makes two chases comparable at all -- so the
    opposition's innings was played once when the day was created and is not replayed per
    player. Nobody bowls at them, so there is no card to read a bowling bonus off. A
    DEFEND_BY is the opposite: the opposition really does chase what the player set, so
    both innings exist and both are the player's own doing."""
    if scenario.kind == DEFEND_BY and theirs is None:
        raise ValueError("defend_by is decided by the opposition's reply; it needs one")
    bonuses = bonuses_earned(scenario, mine, theirs)
    points = sum(BONUS_POINTS[b] for b in bonuses)

    if scenario.kind == DEFEND_BY:
        margin = mine.runs - theirs.runs
        met = margin > scenario.runs_required
        summary = (f"Won by {margin} runs" if margin > 0
                   else f"Lost by {-margin} runs" if margin < 0 else "Tied")
    else:
        # A chase's margin is what was left in hand -- the conventional way a chase is
        # reported, and the only unit in which two successful chases are comparable.
        margin = WICKETS_PER_INNINGS - mine.wickets
        chased = mine.chased
        met = chased and (scenario.kind == CHASE
                          or margin >= scenario.wickets_required)
        summary = (f"Chased with {margin} wickets in hand" if chased
                   else f"Fell {scenario.target + 1 - mine.runs} short")

    return Outcome(objective_met=met, margin=margin, bonus_points=points,
                   bonuses_met=bonuses, summary=summary)


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


# Plausible floors for the two kinds that carry one. Declared game-design values, in the
# same category as BONUS_POINTS: the range is what makes a day feel different from the last
# one, not a measurement of anything.
WICKET_FLOORS = (5, 6, 7)
RUN_MARGINS = (15, 20, 25, 30)
STAGES = ("Final", "Qualifier 1", "Eliminator", "Qualifier 2")


def generate(rng, opposition_fs_id: int, opposition_name: str,
             opposition_score: int) -> Scenario:
    """One day's scenario, given an opposition and the score they posted.

    `opposition_score` is passed IN rather than invented here, so the target a chase asks
    for is a total that side really made against this engine rather than a number picked
    from a plausible-looking band. That keeps this function pure and testable, and it keeps
    the challenge honest: the target is a real innings.

    A DEFEND_BY day ignores the score, since the player bats first and the opposition's
    total is whatever they manage in reply."""
    kind = rng.choice(SCENARIO_KINDS)
    stage = rng.choice(STAGES)
    if kind == CHASE:
        return Scenario(kind, opposition_fs_id, opposition_name, stage,
                        target=opposition_score)
    if kind == CHASE_WITH_WICKETS:
        return Scenario(kind, opposition_fs_id, opposition_name, stage,
                        target=opposition_score,
                        wickets_required=rng.choice(WICKET_FLOORS))
    return Scenario(kind, opposition_fs_id, opposition_name, stage,
                    runs_required=rng.choice(RUN_MARGINS))
