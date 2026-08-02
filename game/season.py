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

from etl.feasibility import Card, Deck
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

# Situational Impact Player: a declared game-design term, same category as REPUTATION or
# ALLROUNDER_RUNS in the data pipeline's own vocabulary for this -- there is no historical
# "did the Impact Player play" record to fit these against, so they were checked against a
# batch of simulated matches for a sane play/sit-out rate rather than measured.
#
# A sentinel, not an exclusion, for a discipline a card has no rating in at all: he still
# has to be comparable to a teammate who does have one, and "clearly weaker than anyone
# rated" is the directionally honest answer, not a special case that drops him from the
# comparison entirely.
IMPACT_NO_RATING = -1.0
# Weight on the OPPONENT's matching threat -- a strong opposing attack raises the value of
# batting insurance, a strong opposing batting order raises the value of another bowler.
IMPACT_SITUATIONAL_K = 0.25
# A raw gain (over his own side's weakest link, ignoring the opponent entirely) this big
# plays regardless of the matchup -- a player that much better than his replacement plays,
# full stop.
IMPACT_TOO_GOOD_GAIN = 0.5
# The situational score has to clear this before he is worth using at all; short of it he
# sits out and the drafted/algorithmic XI plays unchanged.
IMPACT_SIT_OUT_BAR = 0.05


def _bat_rating(c: Card) -> float:
    return c.bat if c.bat is not None else IMPACT_NO_RATING


def _bowl_rating(c: Card) -> float:
    return c.bowl if c.bowl is not None else IMPACT_NO_RATING


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _pure_bowler_678(xi: list[Card]) -> Card | None:
    """Whoever bats at position 6, 7 or 8 (1-indexed) and bowls -- the batting swap-out
    candidate for a batting Impact Player, exactly the way a real side frees up a slot for
    an extra batter: not its best bowler, whichever bowling-capable player is already
    batting deepest in the order. The weakest batter among any that qualify, if more than
    one of those three positions holds one."""
    candidates = [c for i, c in enumerate(xi) if i in (5, 6, 7) and c.has_bowl]
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


def decide_impact(side: "Side", opponent: "Side") -> tuple[str | None, Card | None]:
    """Whether the Impact Player plays, as a batter or a bowler, and in place of whom --
    a pre-match tactical read, not a mid-innings one: both XIs are fixed for the whole
    innings once `play_innings` starts (game/simulator.py), so the call has to be made
    before a ball is bowled, exactly like a real team naming its XI at the toss with the
    sub already in mind.

    WHO makes room and WHAT he is measured against are not always the same player. For a
    batting swap both questions have one answer, `_pure_bowler_678` -- a real batting
    rating to compare against, no different in kind from any other teammate's. For a
    bowling swap they split: `_weakest_pure_batter` is WHO structurally makes room (he
    does not bowl at all, by definition), but comparing a bowling rating to his would
    compare against a sentinel every time and make any bowler look like an enormous
    upgrade regardless of how good he actually is -- so the bowling GAIN is measured
    against `_weakest_bowler`, the side's own worst existing bowling option, instead. A
    SITUATIONAL weight adds the opponent's matching threat on top of whichever gain
    applies: a strong opposing attack raises the value of batting insurance, a strong
    opposing batting order raises the value of another bowler. Whichever discipline
    scores highest plays, provided it clears IMPACT_SIT_OUT_BAR; a big enough raw gain on
    its own (IMPACT_TOO_GOOD_GAIN) overrides the matchup read entirely -- a player that
    much better than the relevant reference plays regardless of who is on the other side.

    Returns `(discipline, target)`, never the substituted XI itself -- what actually bats
    and what actually bowls are two DIFFERENT elevens once he plays (`_impact_xi` below
    builds each), because a real Impact substitution reverses between the two innings: a
    batting pick only ever appears while HIS side is batting, freeing the bowler he'd
    otherwise have displaced to bowl normally when his side takes the field; a bowling
    pick only ever appears while his side is bowling, and the batter he'd have displaced
    still opens the batting the rest of the match. `(None, None)` means he sits out
    entirely and both elevens stay exactly as drafted.
    """
    if side.impact is None:
        return None, None

    bat_target = _pure_bowler_678(side.xi)
    bowl_target = _weakest_pure_batter(side.xi)
    bowl_reference = _weakest_bowler(side.xi)

    options: list[tuple[str, Card, float]] = []
    if side.impact.has_bat and bat_target is not None:
        options.append(("bat", bat_target, side.impact.bat - _bat_rating(bat_target)))
    if side.impact.has_bowl and bowl_target is not None and bowl_reference is not None:
        options.append(
            ("bowl", bowl_target, side.impact.bowl - _bowl_rating(bowl_reference)))
    if not options:
        return None, None

    too_good = [o for o in options if o[2] >= IMPACT_TOO_GOOD_GAIN]
    if too_good:
        discipline, target, _ = max(too_good, key=lambda o: o[2])
        return discipline, target

    opp_bowl_strength = _mean([_bowl_rating(c) for c in opponent.xi if c.has_bowl])
    opp_bat_strength = _mean([_bat_rating(c) for c in opponent.xi])
    need = {"bat": opp_bowl_strength, "bowl": opp_bat_strength}
    scored = [(discipline, target, gain + IMPACT_SITUATIONAL_K * need[discipline])
              for discipline, target, gain in options]
    discipline, target, score = max(scored, key=lambda s: s[2])
    if score < IMPACT_SIT_OUT_BAR:
        return None, None
    return discipline, target


def _impact_xi(side: "Side", discipline: str | None, target: Card | None,
               wanted: str) -> tuple[list[Card], Card | None]:
    """`side.xi` with `target` swapped for `side.impact`, but only when `decide_impact`
    chose `wanted` ("bat" or "bowl") for this side -- otherwise `side.xi` unchanged.
    Called once per side per innings with the discipline THAT innings needs, so the same
    (discipline, target) pair from `decide_impact` naturally produces two different
    elevens across the match: the substitution only ever shows up in the one innings it
    was chosen for."""
    if discipline == wanted and target is not None:
        return [side.impact if c is target else c for c in side.xi], side.impact
    return side.xi, None


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


@dataclass
class JourneyAccumulator:
    """Runs and wickets for ONE tracked side's own twelve, folded in match by match as
    the season is played -- not a second pass over stored scorecards, since none are
    stored (SPEC 11: nothing here touches a database). `play()` feeds this from the two
    `Innings` it already computes; nothing re-simulates anything to get it.
    """

    runs: dict[str, int] = field(default_factory=dict)
    wickets: dict[str, int] = field(default_factory=dict)
    total_runs: int = 0
    total_wickets: int = 0

    def add_batting(self, innings) -> None:
        for b in innings.batting:
            if not b.faced_any:
                continue
            self.runs[b.player.name] = self.runs.get(b.player.name, 0) + b.runs
            self.total_runs += b.runs

    def add_bowling(self, innings) -> None:
        for bo in innings.bowling:
            if not bo.balls:
                continue
            self.wickets[bo.player.name] = self.wickets.get(bo.player.name, 0) + bo.wickets
            self.total_wickets += bo.wickets


def _leader(totals: dict[str, int]) -> tuple[str, int]:
    """The name with the highest total, ties broken alphabetically (lowest name) for a
    deterministic answer -- the same shape of tie-break `positions.py`'s `modal_position`
    already uses, rather than an arbitrary dict-iteration-order pick."""
    if not totals:
        return "", 0
    best = max(totals.values())
    name = min(n for n, v in totals.items() if v == best)
    return name, best


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
        top_scorer=_leader(acc.runs), top_wicket_taker=_leader(acc.wickets),
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


def play(model: Model, home: Side, away: Side, rng: random.Random,
         stage: str = "league", track: Side | None = None,
         stats: JourneyAccumulator | None = None) -> Result:
    """One match. The side batting first is the home side, which is all `home` means here.

    A tie is left as a tie rather than taken to a super over: the archive has sixteen of
    them in nineteen years and the league awards a point each, so the rarer path is the
    one that would need the evidence.

    [A72] `home.xi`/`away.xi` are already in batting order -- the human drafter's own
    arrangement, or `opposition_order`'s algorithmic one for a historical side -- so
    `lineup` only converts, it does not sort. `decide_impact` decides, for each side
    independently and before a ball is bowled, whether the Impact Player plays as a
    batter or a bowler; `_impact_xi` then builds each side's batting eleven and bowling
    pool SEPARATELY, because a real Impact substitution reverses between the two innings
    -- see both functions' own docstrings for the rule.

    `track`/`stats` are optional and additive: when `track` is one of the two sides in
    THIS match, its own batting and bowling innings are folded into `stats` right here,
    from the exact `Innings` objects the match already computed -- not a second
    simulation, and not something every caller has to know or care about.
    """
    from game.__main__ import attack, lineup

    home_discipline, home_target = decide_impact(home, away)
    away_discipline, away_target = decide_impact(away, home)

    home_batting, home_bat_impact = _impact_xi(home, home_discipline, home_target, "bat")
    away_bowling, away_bowl_impact = _impact_xi(away, away_discipline, away_target, "bowl")
    first = play_innings(model, lineup(home_batting, model, home_bat_impact),
                          attack(away_bowling, model, away_bowl_impact), rng)

    away_batting, away_bat_impact = _impact_xi(away, away_discipline, away_target, "bat")
    home_bowling, home_bowl_impact = _impact_xi(home, home_discipline, home_target, "bowl")
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

    r = Result(home=home, away=away, stage=stage,
               home_runs=first.runs, home_wickets=first.wickets, home_balls=first.balls,
               away_runs=second.runs, away_wickets=second.wickets, away_balls=second.balls,
               home_innings=first, away_innings=second)
    if second.runs > first.runs:
        r.winner, r.margin = away, f"{away.short} by {10 - second.wickets} wickets"
    elif first.runs > second.runs:
        r.winner, r.margin = home, f"{home.short} by {first.runs - second.runs} runs"
    else:
        r.margin = "tied"
    return r


def _credit(standing: Standing, runs: int, balls: int, wickets: int,
            against_runs: int, against_balls: int, against_wickets: int) -> None:
    """Add one match to a side's record, applying the all-out full-quota rule."""
    full = OVERS * BALLS_PER_OVER
    standing.runs_for += runs
    standing.balls_for += full if wickets >= 10 else balls
    standing.runs_against += against_runs
    standing.balls_against += full if against_wickets >= 10 else against_balls


def run_league(model: Model, sides: list[Side], rng: random.Random,
               track: Side | None = None, stats: JourneyAccumulator | None = None
               ) -> Season:
    season = Season(sides=sides)
    standings = {s.name: Standing(side=s) for s in sides}

    for i, j in fixtures(len(sides)):
        r = play(model, sides[i], sides[j], rng, track=track, stats=stats)
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
                  track: Side | None = None, stats: JourneyAccumulator | None = None
                  ) -> Season:
    """The IPL's own four-team finish, which is not a straight semi-final bracket.

    Finishing first or second is worth a second life: Qualifier 1's loser drops into
    Qualifier 2 rather than out. Reproducing that matters because it is most of what the
    league table is FOR -- a bracket would make positions 1 and 3 nearly equivalent.
    """
    first, second, third, fourth = (s.side for s in season.table[:4])

    q1 = play(model, first, second, rng, stage="Qualifier 1", track=track, stats=stats)
    elim = play(model, third, fourth, rng, stage="Eliminator", track=track, stats=stats)
    q1_loser = second if q1.winner is first else first
    elim_winner = elim.winner or third            # a tied eliminator falls to the higher seed

    q2 = play(model, q1_loser, elim_winner, rng, stage="Qualifier 2", track=track, stats=stats)
    finalist = q1.winner or first
    other = q2.winner or q1_loser
    final = play(model, finalist, other, rng, stage="Final", track=track, stats=stats)

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
