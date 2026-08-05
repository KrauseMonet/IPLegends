"""SPEC 12. A whole season: a ten-team league, fourteen matches each, then the playoffs.

One match was never the game. A drafted eleven is judged over a season, and a season is
where a squad's shape shows -- a side that wins twice and loses twelve had a good night,
not a good team.

The opposition is HISTORICAL rather than drafted. Nine real franchise-seasons -- Mumbai
2018, Kolkata 2024 -- each fielding the best legal eleven that squad could actually put
out. That is a better test than nine synthetic drafts and it is also the only opposition
this archive can vouch for: every one of those elevens really took the field.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from etl.feasibility import BOWLERS_IN_TWELVE, Card, Deck
from game.simulator import BALLS_PER_OVER, Innings, OVERS, Model, play_innings

TEAMS = 10                 # you and nine historical sides
MATCHES_EACH = 14          # the IPL's own league length
POINTS_WIN = 2
POINTS_TIE = 1

# Which pairs meet twice. On ten teams each side needs five double fixtures to reach
# fourteen matches (5 x 2 + 4 x 1), so the "plays twice" relation must be a 5-regular
# graph. Circular distances 1, 2 and 5 give exactly that: two neighbours each side, and
# the team directly opposite. It is the same trick a real fixture list uses -- near
# rivals twice, the rest once -- and it is symmetric, so no side gets an easier draw.
DOUBLE_AT = (1, 2, 5)

# [A78] Situational Impact Player, decided AT THE INNINGS BREAK, not pre-match. Real IPL
# usage skews heavily toward a BATTING swap (a bowling swap is capped at 4 overs/24 balls,
# a batting one is not) and the decision is made live, informed by the actual first-
# innings result -- our own pre-match, argmax-between-two-disciplines model measured the
# opposite: 62.5% bowling swaps against 23.1% batting, the inverse of the real pattern.
# The fix is structural, not a rebalanced weight: at the break each side has exactly ONE
# discipline left to weigh (whichever innings it hasn't played yet), so there is no more
# argmax between an unfair pair of references (a batting gain measured against a genuine
# all-rounder, a bowling gain measured against the side's OWN WORST bowler) to bias.
#
# Declared, not measured -- same category as REPUTATION/ALLROUNDER_RUNS: there is no
# historical "did the Impact Player play" record to fit these against, so they were
# checked against a batch of simulated matches for a sane play/sit-out rate.
#
# A sentinel, not an exclusion, for a discipline a card has no rating in at all: he still
# has to be comparable to a teammate who does have one, and "clearly weaker than anyone
# rated" is the directionally honest answer, not a special case that drops him from the
# comparison entirely.
IMPACT_NO_RATING = -1.0
# A raw gain (over the man he'd replace, ignoring the match situation entirely) this big
# plays regardless of how the game is going -- a player that much better than his
# replacement plays, full stop.
IMPACT_TOO_GOOD_GAIN = 0.5
# The situational score has to clear this before he is worth using at all; short of it he
# sits out and the drafted/algorithmic XI plays the second innings unchanged.
IMPACT_SIT_OUT_BAR = 0.05
# Weight on the real first-innings result: a total this far above/below IMPACT_PAR_SCORE
# contributes one full IMPACT_SIT_OUT_BAR of situational push. Chosen so the situational
# term can plausibly tip a genuinely borderline gain, never so large it overrides a clearly
# bad swap on situation alone.
IMPACT_SITUATIONAL_K = 0.0025
# The pivot the situational read compares the real first-innings total against -- close to
# check 20's own measured league-average first-innings total (170.4 runs), declared as a
# round number in the same spirit as the other constants here.
IMPACT_PAR_SCORE = 170


def _bat_rating(c: Card) -> float:
    return c.bat if c.bat is not None else IMPACT_NO_RATING


def _bowl_rating(c: Card) -> float:
    return c.bowl if c.bowl is not None else IMPACT_NO_RATING


def _tailender_bowler(xi: list[Card]) -> Card | None:
    """The batting swap-out candidate for a batting Impact Player: whoever bats in the
    TAIL of this arranged XI (positions 8-11, A76's own `tail` band) and also bowls --
    exactly how a real side frees up a batting slot for an extra batter, never its best
    bowler and never a bowling all-rounder who bats too high up to count as a tailender.
    Never the keeper. The weakest bat among any that qualify."""
    candidates = [c for i, c in enumerate(xi)
                  if i >= 7 and c.has_bowl and c.role != "keeper"]
    return min(candidates, key=_bat_rating) if candidates else None


def _weakest_pure_batter(xi: list[Card]) -> Card | None:
    """The weakest-rated XI member who does not bowl at all -- the bowling swap-out
    candidate for a bowling Impact Player. Never the keeper: a substitution must not cost
    the side its wicketkeeper."""
    candidates = [c for c in xi if not c.has_bowl and c.role != "keeper"]
    return min(candidates, key=_bat_rating) if candidates else None


def _weakest_bowler(xi: list[Card]) -> Card | None:
    """The weakest-rated bowling option already in the side -- what a bowling Impact
    Player's OWN quality is measured against. Not the same as `_weakest_pure_batter`,
    which is WHO he structurally displaces: that player by definition does not bowl at
    all, so comparing a bowling rating to his would compare against a sentinel every
    time, making any bowler look like an enormous upgrade regardless of how good he
    actually is. "Is he better than the option I have" is the real question; "who makes
    room for him" is a separate one, answered by `_weakest_pure_batter`."""
    candidates = [c for c in xi if c.has_bowl]
    return min(candidates, key=_bowl_rating) if candidates else None


def _bowling_depth_shortfall(side: "Side") -> Card | None:
    """A76-legal twelve can rely on the Impact Player ALONE to reach BOWLERS_IN_TWELVE
    (order_errors guarantees this over the TWELVE, not the eleven alone) -- when the
    ELEVEN by itself falls short, he must bowl, full stop: not a situational call, and not
    something a break-time read is allowed to override. `attack()` needs BOWLERS_IN_TWELVE
    candidates from whichever eleven actually takes the field, and letting him sit out or
    bat instead would leave `choose_bowler` with nobody left partway through the innings.

    Called from two places: before whichever innings a side bowls FIRST in (no first
    innings exists yet at that point to weigh a situational read against, and there is
    nothing situational about a hard legality floor anyway), and from inside
    `decide_impact`'s own "bowl" branch, in case the eleven is short even though this
    side's bowling innings is the SECOND one, decided at the break."""
    if side.impact is None or sum(c.has_bowl for c in side.xi) >= BOWLERS_IN_TWELVE:
        return None
    target = _weakest_pure_batter(side.xi)
    if side.impact.has_bowl and target is not None:
        return target
    # A legally drafted twelve cannot reach this branch (order_errors already requires
    # the TWELVE to clear BOWLERS_IN_TWELVE, and if the Impact Player himself cannot bowl
    # the eleven must already clear it alone) -- reachable only from a hand-built Side a
    # test constructs directly, so it is reported rather than silently producing an
    # attack `choose_bowler` cannot serve.
    raise ValueError(
        f"{side.name}'s eleven has only {sum(c.has_bowl for c in side.xi)} bowling "
        f"option(s) and the Impact Player cannot make up the difference -- not a "
        f"legally drafted twelve")


def decide_impact(side: "Side", opponent: "Side", discipline: str,
                   first: Innings | None) -> Card | None:
    """Decided AT THE BREAK, informed by the just-finished first innings -- never
    pre-match. `discipline` is always the ONE thing left for this side to do this match:
    "bowl" if it has already batted, "bat" if it has already bowled. There is no more
    argmax between two disciplines -- see this module's own constants section for why
    that structural change, not a rebalanced weight, is what fixes the measured
    bat/bowl inversion.

    `first` is the first innings actually played; only `None` for the "too good regardless"
    and bowling-depth-override paths, which need no situational read at all (kept optional
    so a test can exercise them without constructing a real `Innings`). Used for the
    situational term: a total well above IMPACT_PAR_SCORE raises a chasing side's need for
    batting insurance; a total well below it raises a defending side's need for another
    bowler.

    Returns the TARGET being replaced, or `None` if the Impact Player sits out this
    innings entirely -- the caller already holds `side.impact` and applies the actual
    substitution (`_apply_batting_impact`/`_apply_bowling_impact` below); a batting swap
    is never a straight one-for-one swap into the vacated slot (see `_insert_batting_impact`
    for why), so returning just the target keeps that decision out of this function.
    """
    if side.impact is None:
        return None

    if discipline == "bat":
        target = _tailender_bowler(side.xi)
        if target is None or not side.impact.has_bat:
            return None
        gain = side.impact.bat - _bat_rating(target)
        situational = (first.runs - IMPACT_PAR_SCORE) if first is not None else 0.0
    else:
        forced = _bowling_depth_shortfall(side)
        if forced is not None:
            return forced
        target = _weakest_pure_batter(side.xi)
        reference = _weakest_bowler(side.xi)
        if target is None or reference is None or not side.impact.has_bowl:
            return None
        gain = side.impact.bowl - _bowl_rating(reference)
        situational = (IMPACT_PAR_SCORE - first.runs) if first is not None else 0.0

    if gain >= IMPACT_TOO_GOOD_GAIN:
        return target
    score = gain + IMPACT_SITUATIONAL_K * situational
    return target if score >= IMPACT_SIT_OUT_BAR else None


def _insert_batting_impact(xi: list[Card], target: Card, impact: Card) -> list[Card]:
    """`target` (a tailender bowler) drops out of the eleven; `impact` is inserted at HIS
    OWN natural batting position -- the lowest slot of his A76 batting-role band -- not
    at the slot `target` vacated. Everyone from that point down to where `target` used to
    bat shifts down by one place: a top-order Impact bats at the top and the whole order
    below him compresses by one seat, exactly how a real side slots a specialist batter
    into his actual role rather than just taking over the dropped man's exact spot."""
    without_target = [c for c in xi if c is not target]
    insertion = min(min(impact.positions) - 1, len(without_target))
    return without_target[:insertion] + [impact] + without_target[insertion:]


def _apply_batting_impact(xi: list[Card], target: Card | None,
                          impact: Card | None) -> list[Card]:
    if target is None or impact is None:
        return xi
    return _insert_batting_impact(xi, target, impact)


def _apply_bowling_impact(xi: list[Card], target: Card | None,
                          impact: Card | None) -> list[Card]:
    if target is None or impact is None:
        return xi
    return [impact if c is target else c for c in xi]


@dataclass
class Side:
    name: str
    short: str
    xi: list[Card]                    # already in batting order -- position i bats i+1
    impact: Card | None = None        # the Impact Player -- decide_impact (below) decides
                                       # whether he plays, as a batter or a bowler, per match
    you: bool = False


@dataclass
class Standing:
    side: Side
    played: int = 0
    won: int = 0
    lost: int = 0
    tied: int = 0
    runs_for: int = 0
    balls_for: int = 0
    runs_against: int = 0
    balls_against: int = 0

    @property
    def points(self) -> int:
        return self.won * POINTS_WIN + self.tied * POINTS_TIE

    @property
    def nrr(self) -> float:
        """Net run rate, per over, exactly as the IPL computes it.

        A side bowled out before its twenty overs is charged the FULL twenty on the
        run-rate denominator -- that is the competition's own rule, and leaving it out
        would quietly reward being dismissed cheaply.
        """
        if not self.balls_for or not self.balls_against:
            return 0.0
        return (self.runs_for / (self.balls_for / BALLS_PER_OVER)
                - self.runs_against / (self.balls_against / BALLS_PER_OVER))


@dataclass
class Result:
    home: Side
    away: Side
    home_runs: int = 0
    home_wickets: int = 0
    home_balls: int = 0
    away_runs: int = 0
    away_wickets: int = 0
    away_balls: int = 0
    winner: Side | None = None
    margin: str = ""
    stage: str = "league"
    # The full ball-by-ball detail `play()` already computed for this match -- kept
    # rather than discarded once folded into the summary ints above, so a scorecard can
    # be shown without a second simulation. `None` only for a `Result` built by hand
    # (tests construct one directly to exercise table/points arithmetic in isolation).
    home_innings: Innings | None = None
    away_innings: Innings | None = None
    # None for every match that isn't the human's own -- there is no toss concept at
    # all for an opponent-vs-opponent fixture (`play()` never sets these). Only
    # `_play_human_match` ever populates them.
    toss_won_by_you: bool | None = None
    toss_elected: str | None = None


@dataclass
class JourneyAccumulator:
    """Runs and wickets for ONE tracked side's own twelve, folded in match by match as
    the season is played -- not a second pass over stored scorecards, since none are
    stored (SPEC 11: nothing here touches a database). `play()` feeds this from the two
    `Innings` it already computes; nothing re-simulates anything to get it.

    Keyed by `person_id`, not name (CLAUDE.md's own standing rule) -- two different
    drafted seasons can share a registry name, and this dict is looked up per person for
    the journey card's per-player breakdown, not just for the single tournament-wide
    leader `_leader` used to pick out. `names` carries the display name alongside, since
    the engine's own `Player` is the only place that name still lives.
    """

    runs: dict[str, int] = field(default_factory=dict)
    balls_faced: dict[str, int] = field(default_factory=dict)
    wickets: dict[str, int] = field(default_factory=dict)
    runs_conceded: dict[str, int] = field(default_factory=dict)
    balls_bowled: dict[str, int] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    total_runs: int = 0
    total_wickets: int = 0

    def add_batting(self, innings) -> None:
        for b in innings.batting:
            if not b.faced_any:
                continue
            pid = b.player.person_id
            self.names[pid] = b.player.name
            self.runs[pid] = self.runs.get(pid, 0) + b.runs
            self.balls_faced[pid] = self.balls_faced.get(pid, 0) + b.balls
            self.total_runs += b.runs

    def add_bowling(self, innings) -> None:
        for bo in innings.bowling:
            if not bo.balls:
                continue
            pid = bo.player.person_id
            self.names[pid] = bo.player.name
            self.wickets[pid] = self.wickets.get(pid, 0) + bo.wickets
            self.runs_conceded[pid] = self.runs_conceded.get(pid, 0) + bo.runs
            self.balls_bowled[pid] = self.balls_bowled.get(pid, 0) + bo.balls
            self.total_wickets += bo.wickets


def _leader(totals: dict[str, int], names: dict[str, str] | None = None) -> tuple[str, int]:
    """The name with the highest total, ties broken alphabetically (lowest name) for a
    deterministic answer -- the same shape of tie-break `positions.py`'s `modal_position`
    already uses, rather than an arbitrary dict-iteration-order pick.

    `totals` is keyed by person_id; `names` maps that back to a display name. Left
    optional, and falling back to the key itself when omitted, so a caller with no name
    to give (or a test with no collision to worry about) can still key by name directly.
    """
    if not totals:
        return "", 0
    names = names or {}
    best = max(totals.values())
    key = min((k for k, v in totals.items() if v == best), key=lambda k: names.get(k, k))
    return names.get(key, key), best


@dataclass
class JourneyStats:
    """One tracked side's whole tournament, for the shareable card -- not stored, built
    fresh from a `Season` and its `JourneyAccumulator` right after the match is played."""

    runs: int
    wickets: int
    played: int
    won: int
    lost: int
    tied: int
    champion: bool
    top_scorer: tuple[str, int]
    top_wicket_taker: tuple[str, int]


def journey_stats(season: Season, track: Side, acc: JourneyAccumulator) -> JourneyStats:
    """The tracked side's whole tournament -- league record plus however far the playoffs
    took them, not just the fourteen league games `season.table` itself counts.
    """
    standing = next(s for s in season.table if s.side is track)
    played, won, lost, tied = standing.played, standing.won, standing.lost, standing.tied
    for r in season.playoffs:
        if r.home is not track and r.away is not track:
            continue
        played += 1
        if r.winner is None:
            tied += 1
        elif r.winner is track:
            won += 1
        else:
            lost += 1

    return JourneyStats(
        runs=acc.total_runs, wickets=acc.total_wickets,
        played=played, won=won, lost=lost, tied=tied,
        champion=season.champion is track,
        top_scorer=_leader(acc.runs, acc.names),
        top_wicket_taker=_leader(acc.wickets, acc.names),
    )


@dataclass
class TournamentLeaders:
    """The Orange Cap and Purple Cap for the WHOLE field -- every side that batted or
    bowled a ball this tournament, not one tracked team's own twelve the way
    `JourneyAccumulator` usually scopes (`journey_stats`'s own docstring). Takes a flat
    list of `Result` rather than a `Season`, since a room's own fixtures are never
    wrapped in one (A79's own note in `web/room_match.py`) -- the caller passes
    whichever `Result`s it has, `season.results + season.playoffs` for solo."""

    top_scorer: tuple[str, int]
    top_wicket_taker: tuple[str, int]


def _best_across(entries: list[tuple[int, str]]) -> tuple[str, int]:
    """Same tie-break as `_leader` -- highest value, alphabetically-lowest name on a tie
    -- but over a flat list of (value, name) pairs rather than a dict keyed by person.
    `tournament_leaders` needs one entry PER (person, side): a dict keyed by person_id
    alone would silently merge two different sides' totals for the same man into one
    slot before a leader could even be picked, which is exactly the bug this function
    exists to avoid."""
    if not entries:
        return "", 0
    best_value = max(v for v, _ in entries)
    name = min(n for v, n in entries if v == best_value)
    return name, best_value


def tournament_leaders(results: list[Result]) -> TournamentLeaders:
    """A real person can legally appear on MORE THAN ONE side across a single
    tournament -- the historical opposition sides are nine franchise-SEASONS drawn
    independently (A63), so the same person_id can turn up in two different drawn
    squads in the same tournament, or in both a human's own draft and a historical
    side, if that man's real career touched more than one of the drawn years. Summing
    his stats across every side he happened to appear for would crown him Purple Cap
    on a combined season no single team actually got. Fixed by keeping one
    `JourneyAccumulator` PER SIDE -- `id(side)`, the identity-comparison convention
    this file already uses everywhere else (`journey_stats`'s own `is` lookup;
    `Side` is a plain `@dataclass` with no `eq=False`, so it is not safely hashable or
    comparable by value) -- and picking the single best (person, side) entry across
    every side's own accumulator, never summed across sides."""
    per_side: dict[int, JourneyAccumulator] = {}

    def _acc_for(side: Side) -> JourneyAccumulator:
        return per_side.setdefault(id(side), JourneyAccumulator())

    for r in results:
        if r.home_innings is not None:
            acc = _acc_for(r.home)
            acc.add_batting(r.home_innings)
            acc.add_bowling(r.home_innings)
        if r.away_innings is not None:
            acc = _acc_for(r.away)
            acc.add_batting(r.away_innings)
            acc.add_bowling(r.away_innings)

    return TournamentLeaders(
        top_scorer=_best_across([
            (v, acc.names[pid]) for acc in per_side.values() for pid, v in acc.runs.items()
        ]),
        top_wicket_taker=_best_across([
            (v, acc.names[pid]) for acc in per_side.values() for pid, v in acc.wickets.items()
        ]),
    )


@dataclass
class Season:
    sides: list[Side]
    results: list[Result] = field(default_factory=list)
    table: list[Standing] = field(default_factory=list)
    playoffs: list[Result] = field(default_factory=list)
    champion: Side | None = None


def fixtures(n: int = TEAMS) -> list[tuple[int, int]]:
    """Index pairs for the league, each side appearing MATCHES_EACH times."""
    out: list[tuple[int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            gap = min((j - i) % n, (i - j) % n)
            out.append((i, j))
            if gap in DOUBLE_AT:
                out.append((j, i))          # the reverse fixture, home side swapped
    return out


def _finish_result(home: Side, away: Side, stage: str, first: Innings, second: Innings,
                    toss_won_by_you: bool | None = None,
                    toss_elected: str | None = None) -> Result:
    """The winner/margin arithmetic every match needs, factored out once rather than
    duplicated between `play()` and `_play_human_match` -- the only difference between
    an ordinary match and one the human's own side played in is whether a toss actually
    happened."""
    r = Result(home=home, away=away, stage=stage,
               home_runs=first.runs, home_wickets=first.wickets, home_balls=first.balls,
               away_runs=second.runs, away_wickets=second.wickets, away_balls=second.balls,
               home_innings=first, away_innings=second,
               toss_won_by_you=toss_won_by_you, toss_elected=toss_elected)
    if second.runs > first.runs:
        r.winner, r.margin = away, f"{away.short} by {10 - second.wickets} wickets"
    elif first.runs > second.runs:
        r.winner, r.margin = home, f"{home.short} by {first.runs - second.runs} runs"
    else:
        r.margin = "tied"
    return r


def play(model: Model, home: Side, away: Side, rng: random.Random,
         stage: str = "league", track: Side | None = None,
         stats: JourneyAccumulator | None = None) -> Result:
    """One match. The side batting first is the home side, which is all `home` means here.

    A tie is left as a tie rather than taken to a super over: the archive has sixteen of
    them in nineteen years and the league awards a point each, so the rarer path is the
    one that would need the evidence.

    [A72] `home.xi`/`away.xi` are already in batting order -- the human drafter's own
    arrangement, or `opposition_order`'s algorithmic one for a historical side -- so
    `lineup` only converts, it does not sort.

    [A78] Innings 1 is played with both sides' PLAIN, undecorated elevens -- no Impact
    decision exists yet for anyone, since there is no first-innings result to react to.
    At the break, `home`'s one remaining discipline is "bowl" (it has already batted) and
    `away`'s is "bat" (it has already bowled); each side's `decide_impact` call now weighs
    only that one option, informed by the real `first` innings. Whoever bowls FIRST is the
    one exception: if that side's plain eleven alone falls short of BOWLERS_IN_TWELVE, its
    Impact Player must already be bowling from over one of innings 1, or `attack()` cannot
    field five bowlers at all -- see `_bowling_depth_shortfall`.

    `track`/`stats` are optional and additive: when `track` is one of the two sides in
    THIS match, its own batting and bowling innings are folded into `stats` right here,
    from the exact `Innings` objects the match already computed -- not a second
    simulation, and not something every caller has to know or care about.

    No toss here. This function is for every match that is NOT the tracked human's own;
    see `_play_human_match` below for the interactive one.
    """
    from game.__main__ import attack, lineup

    away_forced_target = _bowling_depth_shortfall(away)
    away_forced = away_forced_target is not None
    away_bowling_1 = (_apply_bowling_impact(away.xi, away_forced_target, away.impact)
                       if away_forced else away.xi)
    away_bowl_impact_1 = away.impact if away_forced else None
    first = play_innings(model, lineup(home.xi, model, None),
                          attack(away_bowling_1, model, away_bowl_impact_1), rng)

    home_target = None if home.impact is None else decide_impact(home, away, "bowl", first)
    away_target = (None if away.impact is None or away_forced
                    else decide_impact(away, home, "bat", first))

    away_batting = _apply_batting_impact(away.xi, away_target, away.impact)
    away_bat_impact = away.impact if away_target is not None else None
    home_bowling = _apply_bowling_impact(home.xi, home_target, home.impact)
    home_bowl_impact = home.impact if home_target is not None else None
    second = play_innings(model, lineup(away_batting, model, away_bat_impact),
                          attack(home_bowling, model, home_bowl_impact),
                          rng, target=first.runs)

    if stats is not None and track is not None:
        if track is home:
            stats.add_batting(first)
            stats.add_bowling(second)
        elif track is away:
            stats.add_batting(second)
            stats.add_bowling(first)

    return _finish_result(home, away, stage, first, second)


# --- the toss, and a human match that pauses for it -------------------------------------

# Declared, not measured -- same category as REPUTATION/ALLROUNDER_RUNS. Serves BOTH the
# algorithmic opponent's own choice whenever THEY win the toss, and the fallback answer
# for a human match's toss when it is being auto-resolved rather than asked live -- one
# rule for both roles is what lets every skip/bypass path reduce to the same underlying
# mechanism instead of needing a second invented rule for the human side.
TOSS_DEFAULT_ELECTS = "bowl"


def toss(rng: random.Random) -> bool:
    """True if the tracked human side wins this match's toss. One rng draw, consumed
    only for a fixture the human's own side actually plays in -- an opponent-vs-opponent
    fixture has no toss concept at all and never calls this."""
    return rng.random() < 0.5


@dataclass(frozen=True)
class TossElect:
    elects: str           # "bat" | "bowl" -- only ever consumed when the human WON the toss


@dataclass(frozen=True)
class ImpactPick:
    slot: int | None       # 1-11: swap this member of your drafted XI out for the Impact
                            # Player. None = decline -- falls back to
                            # decide_impact(human, opponent, discipline, first) exactly as
                            # today, NOT "force him to sit out."


class _MatchNeedsToss(Exception):
    """Raised from inside `_play_human_match` when no toss choice is available for a
    match the human's side just won the toss of. Match-scoped only, no season context
    -- the same narrow scope `web/session.py`'s own `_NeedChoice` has inside `run_draft`."""

    def __init__(self, opponent: Side):
        self.opponent = opponent


class _MatchNeedsImpact(Exception):
    """Raised at the innings break when no Impact choice is available for the human's
    side. Carries what the caller needs to present the decision: the discipline this
    choice actually affects (always the human's SECOND innings this match -- see
    `_play_human_match`'s own docstring for why), and the first innings already played,
    so it can be revealed before asking."""

    def __init__(self, opponent: Side, discipline: str, first: Innings,
                 human_bats_first: bool):
        self.opponent = opponent
        self.discipline = discipline
        self.first = first
        self.human_bats_first = human_bats_first


class NeedToss(Exception):
    """The season-level wrapping of `_MatchNeedsToss` -- enriched with what only
    `run_league`/`run_playoffs`'s own loop has: the `Season` accumulated so far, which
    stage, and which of the human's own matches this is (0-indexed, in play order).
    A bare match-level exception has no way to carry these, since `_play_human_match`
    knows nothing about the season it is part of."""

    def __init__(self, match: _MatchNeedsToss, season: "Season", stage: str,
                 human_match_no: int):
        self.match = match
        self.season = season
        self.stage = stage
        self.human_match_no = human_match_no


class NeedImpact(Exception):
    def __init__(self, match: _MatchNeedsImpact, season: "Season", stage: str,
                 human_match_no: int):
        self.match = match
        self.season = season
        self.stage = stage
        self.human_match_no = human_match_no


def _next_toss(moves, opponent: Side, human_match_no: int) -> TossElect:
    """`moves=None` (`game.__main__`'s own CLI, or any caller wanting fully-automatic
    simulation) behaves exactly like an always-auto source -- no `web/` dependency
    needed for the common case; a real move source (`web/season_session.py`) is only
    required when a human is actually choosing live."""
    if moves is None:
        return TossElect(TOSS_DEFAULT_ELECTS)
    choice = moves.next_toss(human_match_no)
    if choice is None:
        raise _MatchNeedsToss(opponent)
    return choice


def _next_impact(moves, opponent: Side, discipline: str, first: Innings,
                  human_bats_first: bool, human_match_no: int) -> ImpactPick:
    if moves is None:
        return ImpactPick(None)
    choice = moves.next_impact(human_match_no)
    if choice is None:
        raise _MatchNeedsImpact(opponent, discipline, first, human_bats_first)
    return choice


def _play_human_match(model: Model, human: Side, opponent: Side, rng: random.Random,
                       stage: str, human_match_no: int, moves=None,
                       stats: JourneyAccumulator | None = None) -> Result:
    """One match the human's own side plays in -- `play()`'s shape, with a real toss and
    a break-time Impact choice instead of both being decided automatically.

    [A78] Innings 1 is played with BOTH sides' plain elevens, human included -- no Impact
    decision exists for anyone yet, symmetric with `play()`. The human's own Impact Player
    can only ever affect their SECOND innings this match: deciding at the break means their
    first innings has already been played, with their originally-drafted XI unmodified.
    This is the accepted consequence of deciding live at a natural pause point rather than
    retroactively -- not a bug, and not something a later change should "fix" by moving
    the decision earlier.

    The opponent's own decision is the mirror discipline, and is now ALSO made at the
    break (never pre-match) using the same `decide_impact` the algorithmic `play()` uses --
    the only asymmetry left is WHO is asked (the human, live, via `_next_impact`) versus
    WHO is computed (the opponent, via `decide_impact` directly).

    Whoever bowls FIRST -- human or opponent -- is the one exception, exactly as in
    `play()`: if that side's plain eleven alone falls short of BOWLERS_IN_TWELVE, its
    Impact Player already bowls from over one of innings 1, using up that side's one
    substitution before the break is ever reached.
    """
    from game.__main__ import attack, lineup

    won_toss = toss(rng)
    elects = _next_toss(moves, opponent, human_match_no).elects if won_toss \
        else TOSS_DEFAULT_ELECTS

    home, away = (human, opponent) if elects == "bat" else (opponent, human)
    human_bats_first = home is human
    human_is_away = not human_bats_first

    away_forced_target = _bowling_depth_shortfall(away)
    away_forced = away_forced_target is not None
    away_bowling_1 = (_apply_bowling_impact(away.xi, away_forced_target, away.impact)
                       if away_forced else away.xi)
    away_bowl_impact_1 = away.impact if away_forced else None
    first = play_innings(model, lineup(home.xi, model, None),
                          attack(away_bowling_1, model, away_bowl_impact_1), rng)

    # At the break: the discipline the human is asked about is always their SECOND
    # innings -- "bowl" if they batted first, "bat" if they bowled first. The opponent's
    # own remaining discipline is the exact mirror.
    discipline = "bowl" if human_bats_first else "bat"
    opp_discipline = "bat" if human_bats_first else "bowl"

    if opponent.impact is None or (not human_is_away and away_forced):
        opp_target = None
    else:
        opp_target = decide_impact(opponent, human, opp_discipline, first)

    if human.impact is None or (human_is_away and away_forced):
        human_target = None
    else:
        impact_pick = _next_impact(moves, opponent, discipline, first, human_bats_first,
                                    human_match_no)
        if impact_pick.slot is None:
            human_target = decide_impact(human, opponent, discipline, first)
        else:
            human_target = human.xi[impact_pick.slot - 1]

    # `home` is the human when `human_bats_first` (they bowl in innings 2, so it is their
    # decision that belongs to `home`) -- otherwise `away` is the human (they bat in
    # innings 2, so it belongs to `away`). Overwriting the OTHER pair would silently write
    # the human's own decision into the opponent's slot instead.
    if human_bats_first:
        home_target, away_target = human_target, opp_target
    else:
        home_target, away_target = opp_target, human_target

    away_batting = _apply_batting_impact(away.xi, away_target, away.impact)
    away_bat_impact = away.impact if away_target is not None else None
    home_bowling = _apply_bowling_impact(home.xi, home_target, home.impact)
    home_bowl_impact = home.impact if home_target is not None else None
    second = play_innings(model, lineup(away_batting, model, away_bat_impact),
                          attack(home_bowling, model, home_bowl_impact),
                          rng, target=first.runs)

    if stats is not None:
        if human is home:
            stats.add_batting(first)
            stats.add_bowling(second)
        else:
            stats.add_batting(second)
            stats.add_bowling(first)

    return _finish_result(home, away, stage, first, second,
                           toss_won_by_you=won_toss, toss_elected=elects)


class _OpenMatchNeedsToss(Exception):
    """Raised from `play_open` when the toss has been won but no elect is available yet.
    `winner` is whichever of the two `Side`s passed to `play_open` won it -- carried by
    IDENTITY, not by a fixed home/away or human/opponent role, since neither is decided
    until the winner's own call is known. The caller (a room, where more than one seat can
    be a real human) maps `winner` back to whichever seat owns it however it likes;
    `game.season` itself has no concept of a player_id to do that mapping with."""

    def __init__(self, winner: Side):
        self.winner = winner


def play_open(model: Model, side_a: Side, side_b: Side, rng: random.Random,
               stage: str = "league", moves=None,
               stats: JourneyAccumulator | None = None,
               track: Side | None = None) -> Result:
    """One match between two PEER sides, where EITHER can win the toss -- unlike `play()`
    (fully automatic, no toss at all) and `_play_human_match` (one FIXED tracked human and
    a real toss). Built for rooms, where more than one seat at the table can be a real
    human and there is no single "the human" role to special-case around.

    `moves` exposes one method, the same shape as `_next_toss` generalised to not assume a
    fixed side: `next_toss(winner: Side) -> str | None` (the WINNER's own call, "bat" or
    "bowl"; `None` pauses via `_OpenMatchNeedsToss`). `moves=None` behaves exactly like
    every other automatic move source in this module: `TOSS_DEFAULT_ELECTS` for the toss.

    The break-time Impact decision, for EITHER side, always goes straight to
    `decide_impact` -- A78's own algorithm, trusted rather than routed to a human as a
    manual override (rooms used to let the host pick a slot for either side; that
    capability is gone, not merely hidden, since it was never a meaningful choice for
    whoever wasn't the host's own team). `play()` already worked this way; `play_open`
    now matches it, and only the toss remains a live decision.

    WHO is allowed to actually answer `next_toss` -- the toss winner's own seat and
    nobody else's -- is entirely the caller's concern, enforced by whatever `moves`
    object it passes in. `play_open` itself has no concept of a player_id and does not
    need one: it only ever asks "whose decision is this" via the `Side` identity carried
    on the pause exception.

    A real toss is drawn here (`rng.random() < 0.5` decides which of the two SIDES wins
    it, not which of two fixed roles) -- `toss()` above is left exactly as it is, written
    around a single tracked human, since generalising the coin flip here rather than
    there keeps that function's own contract, and every existing caller of it, completely
    undisturbed. Whoever bowls FIRST is the one A50 exception, exactly as in `play()`/
    `_play_human_match`: if that side's plain eleven alone falls short of
    BOWLERS_IN_TWELVE, its Impact Player already bowls from over one of innings 1, using
    up that side's one substitution before the break is ever reached.
    """
    from game.__main__ import attack, lineup

    a_wins_toss = rng.random() < 0.5
    winner, loser = (side_a, side_b) if a_wins_toss else (side_b, side_a)
    if moves is None:
        elects = TOSS_DEFAULT_ELECTS
    else:
        elects = moves.next_toss(winner)
        if elects is None:
            raise _OpenMatchNeedsToss(winner)

    home, away = (winner, loser) if elects == "bat" else (loser, winner)

    away_forced_target = _bowling_depth_shortfall(away)
    away_forced = away_forced_target is not None
    away_bowling_1 = (_apply_bowling_impact(away.xi, away_forced_target, away.impact)
                       if away_forced else away.xi)
    away_bowl_impact_1 = away.impact if away_forced else None
    first = play_innings(model, lineup(home.xi, model, None),
                          attack(away_bowling_1, model, away_bowl_impact_1), rng)

    def _decide(side: Side, opponent: Side, discipline: str, forced: bool) -> Card | None:
        if side.impact is None or forced:
            return None
        return decide_impact(side, opponent, discipline, first)

    home_target = _decide(home, away, "bowl", forced=False)
    away_target = _decide(away, home, "bat", forced=away_forced)

    away_batting = _apply_batting_impact(away.xi, away_target, away.impact)
    away_bat_impact = away.impact if away_target is not None else None
    home_bowling = _apply_bowling_impact(home.xi, home_target, home.impact)
    home_bowl_impact = home.impact if home_target is not None else None
    second = play_innings(model, lineup(away_batting, model, away_bat_impact),
                          attack(home_bowling, model, home_bowl_impact),
                          rng, target=first.runs)

    if stats is not None and track is not None:
        if track is home:
            stats.add_batting(first)
            stats.add_bowling(second)
        elif track is away:
            stats.add_batting(second)
            stats.add_bowling(first)

    return _finish_result(home, away, stage, first, second)


def _credit(standing: Standing, runs: int, balls: int, wickets: int,
            against_runs: int, against_balls: int, against_wickets: int) -> None:
    """Add one match to a side's record, applying the all-out full-quota rule."""
    full = OVERS * BALLS_PER_OVER
    standing.runs_for += runs
    standing.balls_for += full if wickets >= 10 else balls
    standing.runs_against += against_runs
    standing.balls_against += full if against_wickets >= 10 else against_balls


def _play_fixture(model: Model, a: Side, b: Side, rng: random.Random, stage: str,
                   human_match_no: int, track: Side | None, moves,
                   stats: JourneyAccumulator | None, season: "Season") -> Result:
    """One fixture, human-tracked or not -- shared by `run_league`'s loop and
    `run_playoffs`'s four call sites.

    `is`, never `==`: `Side` has no `eq=False`, so `==` is structural, and the existing
    code is already careful to compare identity everywhere (`play()`'s own `if track is
    home`). A fixture "involves the human" only when `track` IS one of these two
    objects, never merely equal to one.

    Raises the season-scoped `NeedToss`/`NeedImpact`, enriched with the `Season`
    accumulated so far, if the human's own match needs a live decision not yet supplied
    -- `moves=None` never raises (see `_next_toss`/`_next_impact`), so this only ever
    happens with a real move source that has run out of recorded answers.
    """
    if track is not None and (track is a or track is b):
        human, opponent = (a, b) if track is a else (b, a)
        try:
            return _play_human_match(model, human, opponent, rng, stage,
                                      human_match_no, moves, stats)
        except _MatchNeedsToss as exc:
            raise NeedToss(exc, season, stage, human_match_no) from None
        except _MatchNeedsImpact as exc:
            raise NeedImpact(exc, season, stage, human_match_no) from None
    return play(model, a, b, rng, stage=stage, track=track, stats=stats)


def run_league(model: Model, sides: list[Side], rng: random.Random,
               track: Side | None = None, stats: JourneyAccumulator | None = None,
               moves=None) -> Season:
    season = Season(sides=sides)
    standings = {s.name: Standing(side=s) for s in sides}
    human_match_no = 0

    for i, j in fixtures(len(sides)):
        is_human_fixture = track is not None and (track is sides[i] or track is sides[j])
        r = _play_fixture(model, sides[i], sides[j], rng, "league", human_match_no,
                           track, moves, stats, season)
        if is_human_fixture:
            human_match_no += 1
        season.results.append(r)
        h, a = standings[r.home.name], standings[r.away.name]
        h.played += 1
        a.played += 1
        _credit(h, r.home_runs, r.home_balls, r.home_wickets,
                r.away_runs, r.away_balls, r.away_wickets)
        _credit(a, r.away_runs, r.away_balls, r.away_wickets,
                r.home_runs, r.home_balls, r.home_wickets)
        if r.winner is None:
            h.tied += 1
            a.tied += 1
        elif r.winner is r.home:
            h.won += 1
            a.lost += 1
        else:
            a.won += 1
            h.lost += 1

    season.table = sorted(standings.values(), key=lambda s: (-s.points, -s.nrr, s.side.name))
    return season


def run_playoffs(model: Model, season: Season, rng: random.Random,
                  track: Side | None = None, stats: JourneyAccumulator | None = None,
                  moves=None) -> Season:
    """The IPL's own four-team finish, which is not a straight semi-final bracket.

    Finishing first or second is worth a second life: Qualifier 1's loser drops into
    Qualifier 2 rather than out. Reproducing that matters because it is most of what the
    league table is FOR -- a bracket would make positions 1 and 3 nearly equivalent.
    """
    first, second, third, fourth = (s.side for s in season.table[:4])
    # The league always contributes exactly MATCHES_EACH human-tracked fixtures (every
    # side plays exactly fourteen, win lose or draw -- test_every_side_plays_exactly_
    # fourteen pins it), so a playoff-stage human match continues the SAME move sequence
    # from here, never restarting it.
    human_match_no = MATCHES_EACH

    def _play(a: Side, b: Side, stage: str) -> Result:
        nonlocal human_match_no
        is_human_fixture = track is not None and (track is a or track is b)
        r = _play_fixture(model, a, b, rng, stage, human_match_no, track, moves,
                           stats, season)
        if is_human_fixture:
            human_match_no += 1
        return r

    q1 = _play(first, second, "Qualifier 1")
    elim = _play(third, fourth, "Eliminator")
    q1_loser = second if q1.winner is first else first
    elim_winner = elim.winner or third            # a tied eliminator falls to the higher seed

    q2 = _play(q1_loser, elim_winner, "Qualifier 2")
    finalist = q1.winner or first
    other = q2.winner or q1_loser
    final = _play(finalist, other, "Final")

    season.playoffs = [q1, elim, q2, final]
    season.champion = final.winner or finalist
    return season


def run_cup(model: Model, sides: list[Side], rng: random.Random) -> list[Result]:
    """A four-side knockout for a room's "cup" format: two semi-finals (1v4, 2v3 by
    JOIN order -- a room has no league table to seed from, unlike `run_playoffs`) then a
    final between the winners. `play()` is reused verbatim; only the bracket shape here
    is new -- three matches total, no table, no net run rate.
    """
    if len(sides) != 4:
        raise ValueError(f"a cup needs exactly four sides, got {len(sides)}")
    semi1 = play(model, sides[0], sides[3], rng, stage="Semi-final 1")
    semi2 = play(model, sides[1], sides[2], rng, stage="Semi-final 2")
    finalist1 = semi1.winner or sides[0]   # a tied semi falls to the higher seed
    finalist2 = semi2.winner or sides[1]
    final = play(model, finalist1, finalist2, rng, stage="Final")
    return [semi1, semi2, final]


def historical_sides(deck: Deck, rng: random.Random, n: int) -> list[Side]:
    """`n` real franchise-seasons, each fielding the best legal eleven it could.

    Drawn uniformly over franchise-seasons like the deck itself (A10), so Mumbai turns up
    nineteen times more often than Kochi -- and skipping any squad that cannot field a
    legal eleven, which is a property of the squad rather than a failure here.
    """
    from game.__main__ import opposition_twelve, viable

    sides: list[Side] = []
    seen: set[int] = set()
    for fs_id in _shuffled(deck.fs_ids, rng):
        if len(sides) == n:
            break
        if fs_id in seen:
            continue
        seen.add(fs_id)
        squad = list(deck.cards_by_fs.get(fs_id, ()))
        if len(squad) < 11 or not viable(squad):
            continue
        xi, impact = opposition_twelve(squad)
        if len(xi) != 11:
            continue
        card = squad[0]
        sides.append(Side(name=f"{card.franchise} {card.season_year}",
                          short=_abbrev(card.franchise, card.season_year),
                          xi=xi, impact=impact))
    return sides


def _shuffled(items, rng: random.Random) -> list:
    out = list(items)
    rng.shuffle(out)
    return out


def _abbrev(franchise: str | None, year: int | None) -> str:
    """MI 2018, KKR 2024 -- initials of the franchise's words, and the season."""
    if not franchise:
        return str(year or "")
    skip = {"of", "the"}
    letters = "".join(w[0] for w in franchise.split() if w.lower() not in skip)
    return f"{letters.upper()[:4]} {year}"
