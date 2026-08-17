"""A T20 innings, ball by ball, drawn from the SPEC 7.1 state model.

The engine is the state model plus one idea. The state model already says what an average
ball looks like from every (over, wickets) position: a distribution over 0-6 off the bat and
a dismissal probability. A rating says how far one player's ball departs from that average,
in runs. So simulating a ball is: take the state's distribution, move its MEAN by the
players' ratings, and draw from the result.

**How the mean is moved matters, and there is exactly one defensible way to do it.** Many
distributions have the required mean; picking one arbitrarily would smuggle in a claim about
shape that no measurement supports -- that a good batter hits more sixes rather than fewer
dots, say, which is a real question this model has no evidence about. The exponential tilt
`q(x) = p(x)e^(theta x) / Z` is the distribution closest to the state's own in KL divergence
among all those with the target mean, so it is the one that adds no information beyond the
mean it was asked for. A better batter's distribution shifts the way the archive's own
better-batting states shift, rather than the way we imagine it should.

**The outcome space is the impact formula's, not the scorecard's.** SPEC 7.1 scores a ball as
`runs - was_out * wicket_cost`, so a dismissal is an outcome worth *negative* runs -- the drop
in expected final total, 2.7 to 24.8 of them. Tilting over that space means one theta moves
scoring and survival together in the proportion the archive shows, instead of needing a second
invented rule for how a rating splits between the two. It also makes the engine consistent
with the ratings by construction: a player whose rating says +0.1 runs per ball produces
+0.1 runs per ball here, in the same units, priced against the same states.

Known simplifications, all of them visible in the scoreboard rather than hidden:

- **A dismissal is assumed to fall on a ball that scored nothing off the bat**, which makes
  the eight outcomes mutually exclusive. Checked against all 80 stored states at load.
- **A wicket removes the striker.** 4.28% of dismissals belong to the non-striker (A36); the
  engine has no non-striker run-out.
- **Extras are a flat rate, not a state.** Two measured scalars, no over-by-over shape.
- **Batting and bowling ratings add.** They are measured on slightly different denominators
  (A22 balls faced against legal balls), so their sum is an approximation rather than an
  identity.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from etl.impact import Cell, Costs, Grid
from etl.state_model import FITTING_SET, Remaining, bucket_of

# Off-the-bat outcomes, in the order the tilt's arrays use. Index 0 is the dismissal, whose
# value is not a constant -- it is minus the wicket cost of the state the ball was bowled in.
OFF_THE_BAT = (0, 1, 2, 3, 4, 5, 6)

OVERS = 20
BALLS_PER_OVER = 6
WICKETS = 10
MAX_OVERS_PER_BOWLER = 4


@dataclass(frozen=True)
class Player:
    """A drafted player-season as the engine sees it: a name and two per-ball deltas.

    `bat` is never None. A number 10 still has to bat, and a card with no batting band at
    all this season has no individual batting rating to give him -- so the delta for one
    comes from the pooled below-floor population of their band instead, which is estimable
    precisely because it is pooled (A43's shape), even though A33's floor means no single
    one of those seasons could be rated alone. `bowl` is None for anyone who cannot bowl,
    and the engine never asks them to.
    """

    name: str
    bat: float
    bowl: float | None = None
    rated_bat: bool = True
    # Whether this IS the side's Impact Player for this match (game/season.py's
    # decide_impact) -- not whether he was drafted into the Impact slot, which every
    # Player here already reflects one way or another (he either plays in someone else's
    # XI spot, having replaced them, or he does not play at all). Carried onto Player
    # rather than looked up from Card so the scorecard can tag him without threading a
    # Card reference through BatterCard/BowlerCard as well.
    is_impact: bool = False
    # Never key a player on the name string (CLAUDE.md's own standing rule) -- carried
    # through so JourneyAccumulator can total a tracked side's own runs/wickets by the
    # person rather than by a name two different drafted seasons could share. Empty only
    # for the synthetic "average XI" filler, which is never looked up by identity.
    person_id: str = ""
    # 'pace' | 'spin' | None, carried from Card for the same reason and in the same shape
    # as `person_id` above: Season Analysis splits an over's economy by style, and the
    # innings is what it aggregates, so the style has to reach the innings. NOT copied onto
    # OverSnapshot -- `Innings.bowling` already holds every Player, so the style is looked
    # up per over from there (A19: a second copy of the same fact on every over would be
    # redundancy with nothing checking it).
    #
    # None is a real value and must never be defaulted to a style (A23). It covers two
    # different populations: the synthetic "average XI" filler, which is given None rather
    # than a guess, and anyone `people.bowling_style` does not know -- nobody who bowls
    # today, since A112 filled all 479, but a revised archive can reintroduce one.
    bowling_style: str | None = None


@dataclass
class Model:
    """Everything the engine needs, all of it read from the database rather than assumed."""

    dist: dict[tuple[int, str], tuple[int, ...]]     # resolved state -> 7 outcome counts
    faced: dict[tuple[int, str], int]
    outs: dict[tuple[int, str], int]
    grid: Grid                                       # the A37 walk, at bucketed grain
    costs: Costs                                     # the A37 walk, at exact-wicket grain
    wide_rate: float          # wides per ball faced
    wide_runs: float          # mean runs conceded per wide, penalty included
    extras_rate: float        # non-wide extra runs per ball faced
    unrated_bat: dict[str, float]                    # batting band -> pooled raw per ball
    season_mean: dict[tuple[str, int], float]        # (discipline, season) -> centring const

    def state(self, over: int, wickets: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
        """The eight outcome probabilities and their values, for one (over, wickets)."""
        key = self.grid.resolve(over, bucket_of(wickets))
        faced, outs, counts = self.faced[key], self.outs[key], self.dist[key]
        cost = self.costs.of(over, wickets)
        probs = ((outs / faced,)
                 + ((counts[0] - outs) / faced,)
                 + tuple(c / faced for c in counts[1:]))
        return probs, (-cost,) + tuple(float(r) for r in OFF_THE_BAT)


def model_inputs(conn) -> dict:
    """Every row `build_model` needs, as plain lists and numbers and nothing else.

    Split out from `load_model` for the snapshot (A107): what gets serialised is the
    QUERY RESULT, never the built `Model`. `Grid` and `Costs` are constructed objects
    carrying their own fallback caches, so freezing them would freeze a derived thing and
    a later change to the A37 walk would be silently ignored by anything reading the file.
    Storing the inputs and re-running the same construction means the snapshot path and
    the database path cannot build different models from the same numbers -- there is only
    one construction, and both go through it.
    """
    return {
        "state_ball_outcomes": [
            list(r) for r in conn.execute(
                """
                select over_no, wicket_bucket, faced, runs_off_bat, dismissals,
                       runs_0, runs_1, runs_2, runs_3, runs_4, runs_5, runs_6
                from state_ball_outcomes
                """
            )
        ],
        "state_runs_remaining": [
            list(r) for r in conn.execute(
                """
                select over_no, wickets, observations, runs_so_far_total, runs_remaining_total
                from state_runs_remaining
                """
            )
        ],
        # A22's two denominators, kept apart. A wide is not a ball faced, so its rate is
        # expressed per ball faced and converted at the point of use.
        "wide_extras": [
            float(x) for x in conn.execute(
                f"""
                select sum(case when extra_wides > 0 then 1 else 0 end),
                       sum(extra_wides),
                       sum(case when extra_wides = 0 then 1 else 0 end),
                       sum(case when extra_wides = 0 then runs_extras else 0 end)
                from deliveries where {FITTING_SET}
                """
            ).fetchone()
        ],
        # [A33/A43] The below-floor population, pooled by band. No individual season here
        # is rateable and none is being rated: this is the level of a band's unrated
        # seasons taken together, which 7,144 tail balls estimate perfectly well even
        # though no single 40-ball season does. Derived, so a revised archive moves it.
        "unrated_bat": [
            [band, float(raw)] for band, raw in conn.execute(
                """
                select s.batting_band, sum(i.impact_total) / sum(i.balls)
                  from player_season_impact i
                  join squad_members s using (franchise_season_id, person_id)
                 where i.discipline = 'batting' and i.not_rateable_reason is not null
                   and s.batting_band is not null
                 group by 1
                """
            )
        ],
        # Read back out of the view rather than recomputed from it: the centring constant
        # is `shrunk - centred` for any row of that season, so this is the view's own
        # answer to its own arithmetic and cannot drift from it (A35's rule about k,
        # applied to A42's mean).
        #
        # [A71] `where shrunk_per_ball is not null` excludes the reputation-only branch,
        # whose rows carry no per-ball evidence and so no `shrunk`/`centred` values at all
        # (NULL, not zero -- there is nothing to compute them from). Without this filter,
        # `distinct` mints a second (discipline, season_year, NULL) row for any season
        # holding one of those four players, and dict-comprehension iteration order decides
        # whether the real mean or the NULL one survives -- caught by running the app
        # rather than by any check, because no validation check reads this query.
        # `min(...) group by`, not `distinct`. The constant is the same for every row of a
        # season by construction, but `distinct` cannot collapse float representations that
        # differ in the last bit, so it returned **163 rows for 38 (discipline, season)
        # pairs** and a dict comprehension then took whichever happened to arrive last.
        # Measured, the within-group spread is at most **8.3e-17** -- unobservable in any
        # rating -- but the CHOICE was arbitrary and depended on Postgres's row order, so
        # two boots of the same code could build models differing in the last bit. Found by
        # snapshotting: the file and the database agreed on everything else and disagreed
        # here on 22 of 38 seasons, purely because the rows arrived in a different order.
        "season_mean": [
            [discipline, int(year), float(mean)] for discipline, year, mean in conn.execute(
                """
                select discipline, season_year, min(shrunk_per_ball - centred_per_ball)
                from player_season_rating
                where shrunk_per_ball is not null
                group by discipline, season_year
                """
            )
        ],
    }


def build_model(inputs: dict) -> Model:
    """The one place a `Model` is constructed, from `model_inputs`' plain rows.

    Nothing here is refitted and nothing is hardcoded. The two state tables are read as
    stored rather than recomputed from `deliveries`, which is what makes check 20 the
    guard it is: it recounts the fitting set and fails if the stored totals have gone
    stale, so a simulator reading them cannot quietly diverge from the ratings that were
    scored against the same grids.
    """
    dist, faced, outs, cells = {}, {}, {}, {}
    for row in inputs["state_ball_outcomes"]:
        over, bucket, n, runs, dismissals = row[0], row[1], row[2], row[3], row[4]
        counts = tuple(row[5:])
        # The mutual-exclusivity assumption, checked rather than asserted in a comment: if a
        # state ever recorded more dismissals than dot balls, treating a wicket as a dot with
        # a negative value would produce a negative probability and the draw would be
        # meaningless. It holds on every state in the archive today.
        if dismissals > counts[0]:
            raise SystemExit(
                f"state ov{over} {bucket} has {dismissals} dismissals off {counts[0]} dot "
                "balls, so a dismissal cannot be modelled as a dot ball there"
            )
        dist[(over, bucket)] = counts
        faced[(over, bucket)] = n
        outs[(over, bucket)] = dismissals
        cells[(over, bucket)] = Cell(n, runs, dismissals, dismissals)

    expected = {
        (over, wickets): Remaining(obs, so_far, remaining)
        for over, wickets, obs, so_far, remaining in inputs["state_runs_remaining"]
    }
    wide_balls, wide_runs, balls_faced, extras_on_faced = inputs["wide_extras"]
    unrated_bat = {band: raw for band, raw in inputs["unrated_bat"]}
    season_mean = {(discipline, year): mean
                   for discipline, year, mean in inputs["season_mean"]}

    return Model(
        dist=dist, faced=faced, outs=outs,
        grid=Grid(cells), costs=Costs(expected),
        wide_rate=wide_balls / balls_faced,
        wide_runs=wide_runs / wide_balls,
        extras_rate=extras_on_faced / balls_faced,
        unrated_bat=unrated_bat, season_mean=season_mean,
    )


def load_model(conn) -> Model:
    """Fetch and build in one call -- every existing caller's own entry point, unchanged."""
    return build_model(model_inputs(conn))


def tilt(probs: tuple[float, ...], values: tuple[float, ...],
         target: float) -> tuple[float, ...]:
    """Reweight `probs` to have mean `target`, changing the shape as little as possible.

    Solved by bisection on theta because the tilted mean is strictly increasing in it, so
    there is exactly one root and no starting point to get wrong. A target outside the
    convex hull of the values has no solution at any finite theta -- it would need all the
    mass on one endpoint -- so it is clamped just inside, which no rating in the deck comes
    close to reaching but a future one might.
    """
    lo_v, hi_v = min(values), max(values)
    target = min(max(target, lo_v + 1e-6), hi_v - 1e-6)

    def mean_at(theta: float) -> float:
        # Shifted before exponentiating: theta * 25 is a plausible argument here and
        # exp of it is not, so the shift is what keeps a heavy tilt from overflowing.
        peak = max(theta * v for v in values)
        weights = [p * math.exp(theta * v - peak) for p, v in zip(probs, values)]
        total = sum(weights)
        return sum(w * v for w, v in zip(weights, values)) / total

    lo, hi = -5.0, 5.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if mean_at(mid) < target:
            lo = mid
        else:
            hi = mid

    theta = (lo + hi) / 2
    peak = max(theta * v for v in values)
    weights = [p * math.exp(theta * v - peak) for p, v in zip(probs, values)]
    total = sum(weights)
    return tuple(w / total for w in weights)


def draw(probs: tuple[float, ...], rng: random.Random) -> int:
    roll, cumulative = rng.random(), 0.0
    for index, p in enumerate(probs):
        cumulative += p
        if roll < cumulative:
            return index
    return len(probs) - 1


@dataclass
class BatterCard:
    player: Player
    runs: int = 0
    balls: int = 0
    out: bool = False
    faced_any: bool = False


@dataclass
class BowlerCard:
    player: Player
    balls: int = 0
    runs: int = 0
    wickets: int = 0

    @property
    def overs(self) -> str:
        return f"{self.balls // BALLS_PER_OVER}.{self.balls % BALLS_PER_OVER}"


@dataclass(frozen=True)
class OverSnapshot:
    """One completed over, for an over-by-over reveal. Cumulative totals plus this
    over's own delta, captured at the same natural end-of-over point `commentary`'s
    fall-of-wicket lines already use -- a pure side effect over state the loop already
    has in hand, consuming no extra rng draw and touching no ball-resolution logic.

    Only ever appended for a FULLY completed over: an innings that ends mid-over (all
    out, or target chased) gets no entry for that last partial over. The existing
    fall-of-wicket commentary and the final scorecard already cover that moment, so a
    reveal stepping through `over_log` simply ends by showing the final scorecard."""

    over: int              # 0-indexed
    bowler: str
    runs: int               # cumulative innings runs after this over
    wickets: int             # cumulative wickets after this over
    balls: int               # cumulative balls after this over
    over_runs: int           # runs scored THIS over alone
    over_wickets: int        # wickets that fell THIS over alone
    # [A110] The person, not the name. Per-over bowler attribution is what makes a
    # phase-by-phase bowling analysis possible at all, and CLAUDE.md's standing rule
    # forbids keying a player on a name string -- two drafted seasons can legitimately
    # share one. Last in the field list because it carries a default; empty only for an
    # OverSnapshot built by an older caller or a test, and the analysis falls back to the
    # name in that case rather than dropping the over.
    bowler_id: str = ""


@dataclass
class Innings:
    batting: list[BatterCard]
    bowling: list[BowlerCard]
    runs: int = 0
    wickets: int = 0
    balls: int = 0
    extras: int = 0
    commentary: list[str] = field(default_factory=list)
    over_log: list[OverSnapshot] = field(default_factory=list)
    chased: bool = False

    @property
    def overs(self) -> str:
        return f"{self.balls // BALLS_PER_OVER}.{self.balls % BALLS_PER_OVER}"


def choose_bowler(bowlers: list[BowlerCard], previous: BowlerCard | None) -> BowlerCard:
    """Fewest overs bowled first, best bowler breaking the tie, never two overs running.

    A real captain saves their best for the death; this one does not, and that is a
    deliberate omission rather than an oversight -- bowling order is a tactical layer, and
    adding one before the engine underneath it has been read would bury the engine's own
    behaviour under a policy's.
    """
    available = [b for b in bowlers
                 if b.balls < MAX_OVERS_PER_BOWLER * BALLS_PER_OVER and b is not previous]
    return min(available, key=lambda b: (b.balls, -(b.player.bowl or 0.0)))


def play_innings(model: Model, batting: list[Player], bowling: list[Player],
                 rng: random.Random, target: int | None = None) -> Innings:
    cards = [BatterCard(p) for p in batting]
    attack = [BowlerCard(p) for p in bowling]
    innings = Innings(cards, attack)

    striker, non_striker, next_in = cards[0], cards[1], 2
    previous: BowlerCard | None = None

    for over in range(OVERS):
        bowler = choose_bowler(attack, previous)
        previous = bowler
        over_start_runs, over_start_wickets = innings.runs, innings.wickets

        for _ in range(BALLS_PER_OVER):
            # A wide is not a ball faced and does not advance the over, so it is drawn
            # before the delivery rather than as one of its outcomes.
            while rng.random() < model.wide_rate / (1 + model.wide_rate):
                conceded = 1 + (1 if rng.random() < model.wide_runs - 1 else 0)
                innings.runs += conceded
                innings.extras += conceded
                bowler.runs += conceded
                if target is not None and innings.runs > target:
                    innings.chased = True
                    return innings

            probs, values = model.state(over, innings.wickets)
            baseline = sum(p * v for p, v in zip(probs, values))
            delta = striker.player.bat - (bowler.player.bowl or 0.0)
            outcome = draw(tilt(probs, values, baseline + delta), rng)

            innings.balls += 1
            striker.balls += 1
            striker.faced_any = True
            bowler.balls += 1

            if outcome == 0:
                striker.out = True
                innings.wickets += 1
                bowler.wickets += 1
                innings.commentary.append(
                    f"  {innings.overs:>5}  {striker.player.name} out "
                    f"{striker.runs} ({striker.balls})  b {bowler.player.name}  "
                    f"-- {innings.runs}/{innings.wickets}"
                )
                if innings.wickets == WICKETS or next_in >= len(cards):
                    return innings
                striker = cards[next_in]
                next_in += 1
            else:
                runs = OFF_THE_BAT[outcome - 1]
                striker.runs += runs
                innings.runs += runs
                bowler.runs += runs
                if runs % 2 == 1:
                    striker, non_striker = non_striker, striker

            # Byes, leg byes and the no-ball penalty, as one flat rate. Charged to the team
            # and not to the batter or the bowler, which is what A1 split the columns for.
            if rng.random() < model.extras_rate:
                innings.runs += 1
                innings.extras += 1

            if target is not None and innings.runs > target:
                innings.chased = True
                return innings

        # Reached only when all six legal-plus-extra balls of the over resolved without
        # an early return above -- a fully completed over, exactly what OverSnapshot
        # documents itself as being.
        innings.over_log.append(OverSnapshot(
            over=over, bowler=bowler.player.name,
            bowler_id=bowler.player.person_id,
            runs=innings.runs, wickets=innings.wickets, balls=innings.balls,
            over_runs=innings.runs - over_start_runs,
            over_wickets=innings.wickets - over_start_wickets,
        ))
        striker, non_striker = non_striker, striker

    return innings
