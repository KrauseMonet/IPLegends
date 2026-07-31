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
from game.simulator import BALLS_PER_OVER, OVERS, Model, play_innings

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


@dataclass
class Side:
    name: str
    short: str
    xi: list[Card]
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
         stage: str = "league") -> Result:
    """One match. The side batting first is the home side, which is all `home` means here.

    A tie is left as a tie rather than taken to a super over: the archive has sixteen of
    them in nineteen years and the league awards a point each, so the rarer path is the
    one that would need the evidence.
    """
    from game.__main__ import attack, batting_order

    first = play_innings(model, batting_order(home.xi, model), attack(away.xi, model), rng)
    second = play_innings(model, batting_order(away.xi, model), attack(home.xi, model),
                          rng, target=first.runs)

    r = Result(home=home, away=away, stage=stage,
               home_runs=first.runs, home_wickets=first.wickets, home_balls=first.balls,
               away_runs=second.runs, away_wickets=second.wickets, away_balls=second.balls)
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


def run_league(model: Model, sides: list[Side], rng: random.Random) -> Season:
    season = Season(sides=sides)
    standings = {s.name: Standing(side=s) for s in sides}

    for i, j in fixtures(len(sides)):
        r = play(model, sides[i], sides[j], rng)
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


def run_playoffs(model: Model, season: Season, rng: random.Random) -> Season:
    """The IPL's own four-team finish, which is not a straight semi-final bracket.

    Finishing first or second is worth a second life: Qualifier 1's loser drops into
    Qualifier 2 rather than out. Reproducing that matters because it is most of what the
    league table is FOR -- a bracket would make positions 1 and 3 nearly equivalent.
    """
    first, second, third, fourth = (s.side for s in season.table[:4])

    q1 = play(model, first, second, rng, stage="Qualifier 1")
    elim = play(model, third, fourth, rng, stage="Eliminator")
    q1_loser = second if q1.winner is first else first
    elim_winner = elim.winner or third            # a tied eliminator falls to the higher seed

    q2 = play(model, q1_loser, elim_winner, rng, stage="Qualifier 2")
    finalist = q1.winner or first
    other = q2.winner or q1_loser
    final = play(model, finalist, other, rng, stage="Final")

    season.playoffs = [q1, elim, q2, final]
    season.champion = final.winner or finalist
    return season


def historical_sides(deck: Deck, rng: random.Random, n: int) -> list[Side]:
    """`n` real franchise-seasons, each fielding the best legal eleven it could.

    Drawn uniformly over franchise-seasons like the deck itself (A10), so Mumbai turns up
    nineteen times more often than Kochi -- and skipping any squad that cannot field a
    legal eleven, which is a property of the squad rather than a failure here.
    """
    from game.__main__ import choose_xi, viable

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
        xi = choose_xi(squad)
        if len(xi) != 11:
            continue
        card = squad[0]
        sides.append(Side(name=f"{card.franchise} {card.season_year}",
                          short=_abbrev(card.franchise, card.season_year), xi=xi))
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
