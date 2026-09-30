"""Flashback -- a ten-question IPL quiz, built from real archive figures only. [A147]

A pure function of the deck already in memory (A107) and a seed: no database, no stored
state, and the same seed always produces the same ten questions, which is what lets a
player send a friend the exact quiz they just played.

**Every clue and every option is a real figure.** A wrong option is never an invented
number: it is another real season, another real player, another real franchise. So
"How many runs did V Kohli make in 2016?" offers four of Kohli's own season totals, and
"Which of these did NOT play for Chennai in 2010?" offers three men who did and one who
played for somebody else that year. The answer is the only thing that makes an option
right; nothing is plausible-looking filler.

Six kinds of question:

    season       a franchise's top scorer and top wicket-taker -- which year?
    top_scorer   who made the most runs for this team-season?
    top_wickets  who took the most wickets for this team-season?
    which_team   which franchise did this player play for in this year?
    odd_one_out  three played for this team-season, one did not -- which?
    more_runs    two big batting seasons -- which was bigger?  (or wickets)
    stat_year    how many runs (or wickets) did this player make in this year?

Questions are drawn from NOTABLE seasons (a real volume of runs or wickets) rather than
the whole deck, because a quiz about a player who faced nine balls is a quiz nobody can
answer. The thresholds are declared game-design constants, like the draft's, not
measurements.

Two numbers in the deck are the same number in cricket's own sense and are compared
exactly: a question is only asked where its answer is UNIQUE -- a strict top scorer, two
seasons that differ, four distinct season totals. A tie would make two options right.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from etl.feasibility import Card, Deck
from etl.franchise_map import canonical

QUIZ_LENGTH = 10
OPTIONS = 4

# Declared, not measured: what makes a season worth asking about.
NOTABLE_RUNS = 300
NOTABLE_WICKETS = 14
BIG_RUNS = 450             # the "which was bigger" batting seasons
BIG_WICKETS = 18
MIN_STAT_SEASONS_RUNS = 150  # a season counted among a player's own totals
MIN_STAT_SEASONS_WICKETS = 6
SEASON_MIN_LINEAGE = 4     # a franchise needs four seasons to offer three decoy years
FAST_SCORER_BALLS = 100    # A33's batting floor, for the "fastest scorer" clue

KINDS = ("season", "top_scorer", "top_wickets", "which_team", "odd_one_out",
         "more_runs", "stat_year")


@dataclass(frozen=True)
class Option:
    label: str
    detail: str = ""               # shown once the question is answered
    franchise: str | None = None   # facts, for the web layer's crest
    year: int | None = None


@dataclass(frozen=True)
class Question:
    kind: str
    prompt: str
    options: list[Option]
    answer: int
    reveal: str
    clues: list[str] = field(default_factory=list)
    franchise: str | None = None
    year: int | None = None
    # True where showing the team's crest would give the answer away or mislead -- the
    # season question, whose whole point is the year (A145's unambiguous_crest rule).
    year_hidden: bool = False


@dataclass(frozen=True)
class Quiz:
    seed: int
    questions: list[Question]


class _Skip(Exception):
    """This draw cannot make a fair question; try another."""


def _team(c: Card) -> str:
    return f"{c.franchise} {c.season_year}"


def _lineage(name: str) -> str:
    try:
        return canonical(name)
    except RuntimeError:
        return name


class _Pool:
    """The deck, indexed the ways the question builders need it."""

    def __init__(self, deck: Deck):
        self.squads: list[list[Card]] = [deck.cards_by_fs[fs] for fs in sorted(deck.cards_by_fs)
                                         if deck.cards_by_fs[fs]]
        self.cards = [c for squad in self.squads for c in squad]
        self.by_year: dict[int, list[list[Card]]] = {}
        self.by_lineage: dict[str, list[list[Card]]] = {}
        self.by_person: dict[str, list[Card]] = {}
        for squad in self.squads:
            head = squad[0]
            self.by_year.setdefault(head.season_year, []).append(squad)
            self.by_lineage.setdefault(_lineage(head.franchise), []).append(squad)
        for c in self.cards:
            self.by_person.setdefault(c.person_id, []).append(c)
        self.lineage_of_person = {pid: {_lineage(c.franchise) for c in cs}
                                  for pid, cs in self.by_person.items()}
        self.batters = [c for c in self.cards if (c.bat_runs or 0) >= NOTABLE_RUNS]
        self.bowlers = [c for c in self.cards if (c.bowl_wickets or 0) >= NOTABLE_WICKETS]
        bat_ids = {id(c) for c in self.batters}
        self.notable = self.batters + [c for c in self.bowlers if id(c) not in bat_ids]
        self.notable_ids = {id(c) for c in self.notable}


def _ranked(squad: list[Card], stat: str) -> list[Card]:
    return sorted((c for c in squad if getattr(c, stat)), key=lambda c: (-getattr(c, stat), c.name))


def _options(rng: random.Random, right: Option, wrong: list[Option]) -> tuple[list[Option], int]:
    opts = wrong + [right]
    rng.shuffle(opts)
    return opts, opts.index(right)


def _runs(c: Card) -> str:
    return f"{c.bat_runs} runs"


def _wkts(c: Card) -> str:
    return f"{c.bowl_wickets} wicket{'s' if c.bowl_wickets != 1 else ''}"


# --- the question builders ------------------------------------------------------------------
# Each takes the pool, the rng and the set of people already used, and returns a Question
# or raises _Skip. `used` keeps one quiz from asking about the same man twice.

def _season(pool: _Pool, rng, used) -> Question:
    lineage = rng.choice([n for n, s in pool.by_lineage.items() if len(s) >= SEASON_MIN_LINEAGE])
    squads = pool.by_lineage[lineage]
    squad = rng.choice(squads)
    head = squad[0]
    bats, balls = _ranked(squad, "bat_runs"), _ranked(squad, "bowl_wickets")
    if (len(bats) < 2 or len(balls) < 2 or bats[0].bat_runs < NOTABLE_RUNS
            or bats[0].bat_runs == bats[1].bat_runs
            or balls[0].bowl_wickets == balls[1].bowl_wickets):
        raise _Skip               # a shared lead is not "the" leading anything
    top_bat, top_ball = bats[0], balls[0]
    clues = [f"Leading run-scorer: {top_bat.name}, {_runs(top_bat)}",
             f"Leading wicket-taker: {top_ball.name}, {_wkts(top_ball)}"]
    fast = [c for c in squad if (c.bat_balls or 0) >= FAST_SCORER_BALLS]
    if fast:
        f = max(fast, key=lambda c: (c.strike_rate, c.name))
        clues.append(f"Fastest scorer (100+ balls): {f.name}, strike rate {f.strike_rate}")
    years = sorted({s[0].season_year for s in squads} - {head.season_year})
    decoys = rng.sample(years, OPTIONS - 1)
    # No franchise on ANY option: a crest on the right one alone would be the answer.
    right = Option(str(head.season_year), head.franchise)
    wrong = [Option(str(y)) for y in decoys]
    opts, answer = _options(rng, right, wrong)
    used.add(top_bat.person_id)
    return Question("season", f"{lineage}: which season was this?", opts, answer,
                    f"It was {_team(head)}.", clues, franchise=lineage, year_hidden=True)


def _top(pool: _Pool, rng, used, stat: str) -> Question:
    source = pool.batters if stat == "bat_runs" else pool.bowlers
    pick = rng.choice(source)
    squad = next(s for s in pool.squads if s[0].fs_id == pick.fs_id)
    ranked = _ranked(squad, stat)
    if len(ranked) < OPTIONS or getattr(ranked[0], stat) == getattr(ranked[1], stat):
        raise _Skip
    top = ranked[0]
    if top.person_id in used:
        raise _Skip
    fmt = _runs if stat == "bat_runs" else _wkts
    right = Option(top.name, fmt(top))
    wrong = [Option(c.name, fmt(c)) for c in ranked[1:OPTIONS]]
    opts, answer = _options(rng, right, wrong)
    used.add(top.person_id)
    what = "made the most runs" if stat == "bat_runs" else "took the most wickets"
    return Question("top_scorer" if stat == "bat_runs" else "top_wickets",
                    f"Who {what} for {_team(top)}?", opts, answer,
                    f"{top.name}, with {fmt(top)}.", franchise=top.franchise,
                    year=top.season_year)


def _which_team(pool: _Pool, rng, used) -> Question:
    c = rng.choice(pool.notable)
    if c.person_id in used:
        raise _Skip
    others = [s[0] for s in pool.by_year[c.season_year] if s[0].fs_id != c.fs_id]
    if len(others) < OPTIONS - 1:
        raise _Skip
    right = Option(c.franchise, "", c.franchise, c.season_year)
    wrong = [Option(h.franchise, "", h.franchise, h.season_year)
             for h in rng.sample(others, OPTIONS - 1)]
    opts, answer = _options(rng, right, wrong)
    used.add(c.person_id)
    feat = _runs(c) if (c.bat_runs or 0) >= NOTABLE_RUNS else _wkts(c)
    return Question("which_team", f"Who did {c.name} play for in {c.season_year}?", opts,
                    answer, f"{_team(c)} -- {feat} that season.")


def _odd_one_out(pool: _Pool, rng, used) -> Question:
    squad = rng.choice(pool.squads)
    head = squad[0]
    lineage = _lineage(head.franchise)
    stars = [c for c in squad if id(c) in pool.notable_ids and c.person_id not in used]
    if len(stars) < OPTIONS - 1:
        raise _Skip
    members = rng.sample(stars, OPTIONS - 1)
    # The decoy played that SAME year for somebody else, and never for this franchise in
    # any season -- a man who played here the year before would be a trick, not a question.
    outsiders = [c for c in pool.notable if c.season_year == head.season_year
                 and c.fs_id != head.fs_id and c.person_id not in used
                 and lineage not in pool.lineage_of_person[c.person_id]]
    if not outsiders:
        raise _Skip
    odd = rng.choice(outsiders)
    right = Option(odd.name, f"played for {odd.franchise}")
    wrong = [Option(c.name, f"{_runs(c)}" if (c.bat_runs or 0) >= NOTABLE_RUNS else _wkts(c))
             for c in members]
    opts, answer = _options(rng, right, wrong)
    used.update(c.person_id for c in members + [odd])
    return Question("odd_one_out", f"Three of these played for {_team(head)}. Which one did not?",
                    opts, answer, f"{odd.name} played for {odd.franchise} that year.",
                    franchise=head.franchise, year=head.season_year)


def _more(pool: _Pool, rng, used) -> Question:
    batting = rng.random() < 0.6
    stat, floor = ("bat_runs", BIG_RUNS) if batting else ("bowl_wickets", BIG_WICKETS)
    big = [c for c in pool.cards if (getattr(c, stat) or 0) >= floor and c.person_id not in used]
    a, b = rng.sample(big, 2)
    if a.person_id == b.person_id or getattr(a, stat) == getattr(b, stat):
        raise _Skip
    fmt = _runs if batting else _wkts
    winner = a if getattr(a, stat) > getattr(b, stat) else b
    loser = b if winner is a else a
    right = Option(winner.name, fmt(winner), winner.franchise, winner.season_year)
    wrong = [Option(loser.name, fmt(loser), loser.franchise, loser.season_year)]
    opts, answer = _options(rng, right, wrong)
    opts = [Option(f"{o.label} ({o.franchise} {o.year})", o.detail, o.franchise, o.year)
            for o in opts]
    used.update({a.person_id, b.person_id})
    what = "more runs" if batting else "more wickets"
    return Question("more_runs", f"Which season had {what}?", opts, answer,
                    f"{winner.name}: {fmt(winner)}, against {fmt(loser)}.")


def _stat_year(pool: _Pool, rng, used) -> Question:
    batting = rng.random() < 0.65
    stat, floor = (("bat_runs", MIN_STAT_SEASONS_RUNS) if batting
                   else ("bowl_wickets", MIN_STAT_SEASONS_WICKETS))
    c = rng.choice(pool.batters if batting else pool.bowlers)
    if c.person_id in used:
        raise _Skip
    seasons = [s for s in pool.by_person[c.person_id] if (getattr(s, stat) or 0) >= floor]
    values = {getattr(s, stat) for s in seasons}
    if len(values) < OPTIONS or len(seasons) != len(values):
        raise _Skip           # two seasons with one total would make two answers right
    fmt = _runs if batting else _wkts
    others = rng.sample([s for s in seasons if s is not c], OPTIONS - 1)
    # The year only as a detail for after the answer, and no franchise: a crest per
    # option would show which seasons were at which team -- a clue to the year asked about.
    right = Option(fmt(c), f"{c.season_year}, {c.franchise}")
    wrong = [Option(fmt(s), f"{s.season_year}, {s.franchise}") for s in others]
    opts, answer = _options(rng, right, wrong)
    used.add(c.person_id)
    what = "runs did" if batting else "wickets did"
    verb = "make" if batting else "take"
    return Question("stat_year", f"How many {what} {c.name} {verb} in {c.season_year}?", opts,
                    answer, f"{fmt(c)} for {_team(c)}. Every option is one of his own seasons.",
                    franchise=c.franchise, year=c.season_year)


_BUILDERS = {
    "season": _season,
    "top_scorer": lambda p, r, u: _top(p, r, u, "bat_runs"),
    "top_wickets": lambda p, r, u: _top(p, r, u, "bowl_wickets"),
    "which_team": _which_team,
    "odd_one_out": _odd_one_out,
    "more_runs": _more,
    "stat_year": _stat_year,
}

_POOLS: dict[int, _Pool] = {}


def _pool(deck: Deck) -> _Pool:
    key = id(deck)
    if key not in _POOLS:
        _POOLS.clear()
        _POOLS[key] = _Pool(deck)
    return _POOLS[key]


def _kind_order(rng: random.Random) -> list[str]:
    """Ten kinds, as varied as seven allow: every kind before any repeats, and never the
    same kind twice in a row."""
    order: list[str] = []
    while len(order) < QUIZ_LENGTH:
        batch = list(KINDS)
        rng.shuffle(batch)
        if order and batch[0] == order[-1]:
            batch.append(batch.pop(0))
        order.extend(batch)
    return order[:QUIZ_LENGTH]


def make_quiz(deck: Deck, seed: int) -> Quiz:
    rng = random.Random(seed)
    pool = _pool(deck)
    used: set[str] = set()
    questions = []
    for kind in _kind_order(rng):
        for _ in range(200):
            try:
                questions.append(_BUILDERS[kind](pool, rng, used))
                break
            except _Skip:
                continue
        else:
            raise RuntimeError(f"no fair {kind} question found for seed {seed}")
    return Quiz(seed, questions)


def new_seed() -> int:
    return random.SystemRandom().randrange(1, 10**9)
