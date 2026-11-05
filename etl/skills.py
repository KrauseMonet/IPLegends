"""Skills and season roles, measured from the archive [A258].

A SKILL is a proven edge or shape in one situation (powerplay, against spin, once set...).
A ROLE is the job he was given in one season (when he came in to bat, which overs he
bowled) and says nothing about how well he did it. The two are different on purpose: DJ
Bravo bowled the death -- a role -- and by the archive was not better than others at it, so
he carries no death skill (A257).

Every skill reads a metric of one shape. Each ball he faced (or bowled) is judged against
par for its state (over x wickets down), moved by that season's own level in that phase
(A42). A metric is then an EDGE (his rate in a situation against par), a LEAN (his rate
there minus his own rate elsewhere), or a SURVIVAL ratio (log of dismissals against par's
expectation). Each is measured at TWO levels, neither chosen: the CAREER spread (how much
players differ for good: odd seasons against even) and the SEASON spread (how much one
season truly differs from the rest of his career beyond luck: one half of a season against
the other). A season's value is his leave-one-season-out career, moved toward what the
season showed by exactly as much as those two spreads and the season's own noise allow --
so a season the archive cannot tell from luck reads as his career, and a thin career shows
nothing at all.

Tiers, divided by what was measured (A258): STYLE is a shape (where his game leans, how he
trades scoring against getting out), worth nothing net; SPECIAL is an edge worth runs; both
are earned by the season. ELITE is dominance of a phase and needs a career at the top of it
AND a season at the top of it judged on that season's own evidence, so a career cannot
carry a season that did not show it.

Which skills exist was decided by measurement (A257): only metrics that repeat across a
career are here. Wicket-taking in the powerplay and middle overs, and a 'partnership
breaker', were measured and do not repeat, so they are absent rather than approximated. A
bowler's overall wicket rate is MEASURED (the `wickets` metric) but holds no skill: its true
spread is about 7% either way, too small to be worth a badge (A258).

Pure apart from `load`. Nothing here reads the deck or a rating; the rating BLOCKS that
decide how many skills a card shows are measured in `game.skills.measure_blocks`.
"""

from __future__ import annotations

import math
import statistics as st
from collections import Counter, defaultdict
from dataclasses import dataclass, field

PHASES = ("pp", "mid", "death")
NOT_WICKETS = {"retired hurt"}          # the batter can come back; the side lost nobody
SETTLED = 10                            # balls faced before a batter counts as set (A257)
HALF_FLOOR = 120                        # balls in the situation, each career half, for spread
SEASON_HALF_FLOOR = 40                  # each half of a SEASON, to measure the season spread
POOL_FLOOR = {"all": 300, "situation": 120}   # a CAREER in the pool (elite skills)
SEASON_FLOOR = {"all": 100, "situation": 30}  # a SEASON in the pool: he played there


def phase_of(over: int) -> str:
    return "pp" if over < 6 else ("mid" if over < 15 else "death")


@dataclass
class Ball:
    """One ball FACED (a wide is not a ball faced, A22), in a full-length innings."""
    match: str
    season: int
    inn: int
    over: int
    wk: int                 # wickets down before this ball
    batter: str
    ns: str
    bowler: str
    style: str | None       # the bowler's: pace / spin / None
    runs: int
    out: bool               # the STRIKER dismissed (A36)
    credited: bool          # a wicket the bowler is credited with
    faced: int              # balls the batter had faced before this one
    ns_faced: int
    pos: int                # the batter's place in the order (A4: first at the crease)
    entry_over: int
    team: int
    won: bool | None
    par_runs: float = 0.0
    par_out: float = 0.0    # striker dismissed, per ball
    par_wkt: float = 0.0    # a wicket credited to the bowler, per ball


LOAD_SQL = """
  select d.match_id, m.season_year, d.innings_no, d.over_no, d.ball_no, d.batter_id,
         d.non_striker_id, d.bowler_id, p.bowling_style, d.runs_batter, d.extra_wides,
         d.player_out_id, d.wicket_kind, d.batting_fs_id, m.winner_fs_id,
         coalesce(d.credited_to_bowler, false)
    from deliveries d
    join matches m using (match_id)
    left join people p on p.person_id = d.bowler_id
   where d.innings_no in (1, 2) and not d.is_super_over
     and d.innings_scheduled_balls = 120
   order by d.match_id, d.innings_no, d.over_no, d.ball_no"""


def scan(rows) -> list[Ball]:
    """Delivery rows, in order, to balls faced with the state each was faced in."""
    out: list[Ball] = []
    key = None
    for (mid, season, inn, over, _b, bat, ns, bowl, style, runs, wides, pout, kind,
         team, winner, credited) in rows:
        if (mid, inn) != key:
            key = (mid, inn)
            wk = 0
            faced: Counter = Counter()
            order: dict[str, int] = {}
            entry: dict[str, int] = {}
        for who in (bat, ns):
            if who not in order:
                order[who] = len(order) + 1
                entry[who] = over
        if wides == 0:
            striker_out = pout == bat and kind not in NOT_WICKETS
            out.append(Ball(mid, season, inn, over, wk, bat, ns, bowl, style, runs,
                            striker_out, striker_out and bool(credited), faced[bat],
                            faced[ns], order[bat], entry[bat], team,
                            None if winner is None else winner == team))
            faced[bat] += 1
        if pout is not None and kind not in NOT_WICKETS:
            wk += 1
    return out


def load(conn) -> list[Ball]:
    balls = scan(conn.execute(LOAD_SQL).fetchall())
    add_par(balls)
    return balls


def add_par(balls: list[Ball]) -> None:
    """Par for a ball: the archive's rates in its (over, wickets down) state, moved by the
    season's own level -- runs by phase, dismissals and wickets by season (A42)."""
    cell: dict = defaultdict(lambda: [0, 0, 0, 0])
    for b in balls:
        c = cell[(b.over, min(b.wk, 7))]
        c[0] += b.runs
        c[1] += b.out
        c[2] += b.credited
        c[3] += 1
    for b in balls:
        c = cell[(b.over, min(b.wk, 7))]
        b.par_runs, b.par_out, b.par_wkt = c[0] / c[3], c[1] / c[3], c[2] / c[3]
    shift: dict = defaultdict(lambda: [0.0, 0])
    ratio: dict = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for b in balls:
        s = shift[(b.season, phase_of(b.over))]
        s[0] += b.runs - b.par_runs
        s[1] += 1
        r = ratio[b.season]
        r[0] += b.out
        r[1] += b.par_out
        r[2] += b.credited
        r[3] += b.par_wkt
    for b in balls:
        s = shift[(b.season, phase_of(b.over))]
        b.par_runs += s[0] / s[1]
        r = ratio[b.season]
        b.par_out *= r[0] / r[1]
        b.par_wkt *= r[2] / r[3]


# --------------------------------------------------------------------------- metrics

@dataclass(frozen=True)
class Metric:
    """One measurable quantity. `kind` is "edge" (runs an over against par; for bowling,
    runs SAVED), "lean" (edge in `tag` minus edge in everything else) or "ratio" (log of
    events against par's expectation, signed so that higher is better)."""
    key: str
    disc: str               # "bat" | "bowl"
    kind: str
    tag: str                # the situation; "all" for every ball
    floor: str = "situation"


METRICS: tuple[Metric, ...] = (
    Metric("scoring", "bat", "edge", "all", "all"),
    Metric("survival", "bat", "ratio", "all", "all"),
    Metric("pp", "bat", "edge", "pp"),
    Metric("mid", "bat", "edge", "mid"),
    Metric("death", "bat", "edge", "death"),
    Metric("pp_lean", "bat", "lean", "pp"),
    Metric("mid_lean", "bat", "lean", "mid"),
    Metric("spin", "bat", "edge", "spin"),
    Metric("pace", "bat", "edge", "pace"),
    Metric("slow_burn", "bat", "lean", "set"),     # set rate minus his first ten balls'
    Metric("economy", "bowl", "edge", "all", "all"),
    Metric("wickets", "bowl", "ratio", "all", "all"),
    Metric("pp_econ", "bowl", "edge", "pp"),
    Metric("mid_econ", "bowl", "edge", "mid"),
    Metric("death_econ", "bowl", "edge", "death"),
    Metric("pp_econ_lean", "bowl", "lean", "pp"),
    Metric("mid_econ_lean", "bowl", "lean", "mid"),
    Metric("death_econ_lean", "bowl", "lean", "death"),
    Metric("death_wkts", "bowl", "ratio", "death"),
)
METRIC_BY_KEY = {m.key: m for m in METRICS}


def _tags(b: Ball, disc: str) -> tuple[str, ...]:
    if disc == "bat":
        t = [phase_of(b.over), "set" if b.faced >= SETTLED else "new"]
        if b.style in ("spin", "pace"):
            t.append(b.style)
        return tuple(t)
    return (phase_of(b.over),)


@dataclass
class Acc:
    """Sums for one (player, discipline, part of a season, tag)."""
    n: int = 0
    resid: float = 0.0      # runs above par (bowling: runs saved)
    sq: float = 0.0         # sum of squared residuals, for the noise
    events: float = 0.0     # dismissals (batting) or credited wickets (bowling)
    expected: float = 0.0


def _add(parts) -> dict:
    out: dict = defaultdict(Acc)
    for d in parts:
        for t, a in d.items():
            o = out[t]
            o.n += a.n
            o.resid += a.resid
            o.sq += a.sq
            o.events += a.events
            o.expected += a.expected
    return out


def _minus(whole: dict, part: dict) -> dict:
    out: dict = defaultdict(Acc)
    for t, a in whole.items():
        p = part.get(t, Acc())
        if a.n - p.n > 0:
            out[t] = Acc(a.n - p.n, a.resid - p.resid, a.sq - p.sq, a.events - p.events,
                         a.expected - p.expected)
    return out


def accumulate(balls: list[Ball]) -> dict:
    """{(player, disc): {season: [one half, other half, whole season]}}, each {tag: Acc}.
    A season's halves alternate by match, so they are two independent looks at one season
    (used only to measure how much a skill truly changes from season to season)."""
    acc: dict = defaultdict(lambda: defaultdict(lambda: [defaultdict(Acc) for _ in range(3)]))
    seen: dict = defaultdict(list)
    for b in balls:
        for disc, who in (("bat", b.batter), ("bowl", b.bowler)):
            ms = seen[(who, disc, b.season)]
            if not ms or ms[-1] != b.match:
                ms.append(b.match)
            r = (b.runs - b.par_runs) * (1 if disc == "bat" else -1)
            ev, ex = ((b.out, b.par_out) if disc == "bat" else (b.credited, b.par_wkt))
            parts = acc[(who, disc)][b.season]
            for d in (parts[(len(ms) - 1) % 2], parts[2]):
                for t in ("all",) + _tags(b, disc):
                    a = d[t]
                    a.n += 1
                    a.resid += r
                    a.sq += r * r
                    a.events += ev
                    a.expected += ex
    return acc


# [A260] The per-ball variance of a residual across EVERY ball of a discipline, set by
# `measure` before it reads any value. A handful of balls cannot estimate their own spread:
# three sixes in a row have none, so a sample variance read the noise of a three-ball season
# as zero and shrank it not at all (leans of -11 runs an over were the symptom). A season's
# noise is never less than an ordinary ball's.
POOLED_VAR: dict[str, float] = {}


def _edge(a: Acc | None, disc: str | None = None) -> tuple[float, float, int] | None:
    """(runs an over, noise variance, balls)."""
    if a is None or a.n < 2:
        return None
    mean = a.resid / a.n
    var = max(a.sq / a.n - mean * mean, POOLED_VAR.get(disc, 0.0), 1e-9)
    return mean * 6, var * 36 / a.n, a.n


def _rest(d: dict, tag: str) -> Acc:
    """Every ball NOT in `tag`: 'all' minus the tag (a lean's comparison)."""
    a, t = d.get("all", Acc()), d.get(tag, Acc())
    return Acc(a.n - t.n, a.resid - t.resid, a.sq - t.sq, a.events - t.events,
               a.expected - t.expected)


def value(m: Metric, d: dict) -> tuple[float, float, int] | None:
    """(value, noise variance, balls in the situation) for some stretch of play, or None if
    he never faced (or bowled) a ball there."""
    if m.kind == "edge":
        return _edge(d.get(m.tag), m.disc)
    if m.kind == "lean":
        if m.tag == "set":
            here, there = _edge(d.get("set"), m.disc), _edge(d.get("new"), m.disc)
        else:
            here, there = _edge(d.get(m.tag), m.disc), _edge(_rest(d, m.tag), m.disc)
        if here is None or there is None:
            return None
        return here[0] - there[0], here[1] + there[1], min(here[2], there[2])
    a = d.get(m.tag)
    if a is None or a.n < 2 or a.expected <= 0:
        return None
    # log of (events + 0.5) against (expected + 0.5); noise of a Poisson count on the log
    # scale is about 1 / expected. Signed so higher is better: a batter survives, a bowler
    # takes wickets.
    v = math.log((a.events + 0.5) / (a.expected + 0.5))
    return (-v if m.disc == "bat" else v), 1.0 / (a.expected + 0.5), a.n


def _cov(xs, ys) -> float:
    if len(xs) < 10:
        return 0.0
    mx, my = st.fmean(xs), st.fmean(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (len(xs) - 1)


@dataclass
class Measured:
    """One metric, measured at two levels. The CAREER spread is how much players differ
    for good (odd seasons against even seasons); the SEASON spread is how much one player
    truly differs from his own career in a given season, beyond luck (one half of a season
    against the other, each as a deviation from the rest of his career)."""
    career_var: float
    season_var: float
    players: int                          # who measured the career spread
    r: float                              # odd-v-even correlation, for the record
    seasons: int                          # who measured the season spread
    career: dict = field(default_factory=dict)        # {player: shrunk career value}
    career_balls: dict = field(default_factory=dict)
    season: dict = field(default_factory=dict)        # {(player, season): shrunk value}
    season_balls: dict = field(default_factory=dict)
    # The season on its OWN evidence, shrunk toward the league rather than his career: what
    # "a top season" means for an elite skill, so a career cannot carry a season that did
    # not show it.
    alone: dict = field(default_factory=dict)

    @property
    def season_share(self) -> float:
        """How much of a season's true value is that season's own, not the career's."""
        t = self.career_var + self.season_var
        return self.season_var / t if t else 0.0


def _shrink(v: float, noise: float, prior_mean: float, prior_var: float) -> float:
    return prior_mean + (v - prior_mean) * prior_var / (prior_var + noise) if prior_var > 0 \
        else prior_mean


def measure(acc: dict) -> dict[str, Measured]:
    """Every metric: its career and season spreads, each player's shrunk career value, and
    each player-season's shrunk value. A season is judged against the REST of his career
    (leave-one-season-out), shrunk toward it by that season's own noise against the season
    spread -- so a season the archive cannot tell from luck reads as his career, and a
    season that really was different reads as different."""
    careers = {k: _add(p[2] for p in seasons.values()) for k, seasons in acc.items()}
    for disc in ("bat", "bowl"):
        tot = Acc()
        for (p, d), car in careers.items():
            a = car.get("all")
            if d == disc and a is not None:
                tot.n += a.n
                tot.resid += a.resid
                tot.sq += a.sq
        if tot.n:
            POOLED_VAR[disc] = tot.sq / tot.n - (tot.resid / tot.n) ** 2
    halves = {k: (_add(p[2] for s, p in seasons.items() if s % 2),
                  _add(p[2] for s, p in seasons.items() if not s % 2))
              for k, seasons in acc.items()}
    out: dict[str, Measured] = {}
    for m in METRICS:
        xs, ys = [], []
        for (p, disc), (odd, even) in halves.items():
            if disc != m.disc:
                continue
            a, b = value(m, odd), value(m, even)
            if a and b and a[2] >= HALF_FLOOR and b[2] >= HALF_FLOOR:
                xs.append(a[0])
                ys.append(b[0])
        cv = max(_cov(xs, ys), 0.0)
        r = 0.0
        if len(xs) >= 10:
            sx, sy = st.pstdev(xs), st.pstdev(ys)
            r = _cov(xs, ys) * (len(xs) - 1) / len(xs) / (sx * sy) if sx and sy else 0.0
        res = Measured(cv, 0.0, len(xs), r, 0)
        for (p, disc), car in careers.items():
            if disc != m.disc:
                continue
            v = value(m, car)
            if v is not None:
                res.career[p] = _shrink(v[0], v[1], 0.0, cv)
                res.career_balls[p] = v[2]

        def prior(p, disc, s):
            rest = value(m, _minus(careers[(p, disc)], acc[(p, disc)][s][2]))
            if rest is None or cv <= 0:
                return 0.0, cv
            return _shrink(rest[0], rest[1], 0.0, cv), cv * rest[1] / (cv + rest[1])

        # The season spread: two halves of one season, each as a deviation from the rest
        # of his career. Their shared error is the prior's own, so its variance comes off.
        da, db, shared = [], [], []
        for (p, disc), seasons in acc.items():
            if disc != m.disc:
                continue
            for s, parts in seasons.items():
                a, b = value(m, parts[0]), value(m, parts[1])
                if not a or not b or min(a[2], b[2]) < SEASON_HALF_FLOOR:
                    continue
                rest = value(m, _minus(careers[(p, disc)], parts[2]))
                if rest is None or rest[2] < HALF_FLOOR:
                    continue
                pm, pv = prior(p, disc, s)
                da.append(a[0] - pm)
                db.append(b[0] - pm)
                shared.append(pv)
        res.season_var = max(_cov(da, db) - (st.fmean(shared) if shared else 0.0), 0.0)
        res.seasons = len(da)
        for (p, disc), seasons in acc.items():
            if disc != m.disc:
                continue
            for s, parts in seasons.items():
                v = value(m, parts[2])
                if v is None:
                    continue
                pm, pv = prior(p, disc, s)
                res.season[(p, s)] = _shrink(v[0], v[1], pm, pv + res.season_var)
                res.season_balls[(p, s)] = v[2]
                res.alone[(p, s)] = _shrink(v[0], v[1], 0.0, cv + res.season_var)
        out[m.key] = res
    return out


# --------------------------------------------------------------------------- skills

@dataclass(frozen=True)
class Skill:
    key: str
    label: str
    disc: str
    tier: int               # 1 style, 2 special, 3 elite
    metric: str             # or "style" / "complete", each read off scoring and survival
    blurb: str              # what the evidence is, in words
    sign: int = 1           # -1: the skill is the LOW end of the metric


# The tiers are divided by two measured properties (A258). STYLE is a SHAPE -- where his
# game leans, or how he trades scoring against getting out -- worth nothing net in runs, so
# it describes how he plays rather than how good he is. SPECIAL is an EDGE worth runs
# against everybody or one kind of bowling. ELITE is dominance of one phase. Style and
# special are earned by the season (shrunk toward the career by exactly as much as the
# archive says a season can differ); elite needs both a career at the top of that phase
# and a season at the top of it.
SKILLS: tuple[Skill, ...] = (
    Skill("anchor", "Anchor", "bat", 1, "style",
          "Values his wicket: gets out far less than par, more than he out-scores it"),
    Skill("aggressor", "Aggressor", "bat", 1, "style",
          "Plays for runs: out-scores par by more than he survives it", sign=-1),
    Skill("pp_starter", "Powerplay starter", "bat", 1, "pp_lean",
          "His game leans to the powerplay: better there than in the rest of his batting"),
    Skill("mid_accumulator", "Middle-overs accumulator", "bat", 1, "mid_lean",
          "His game leans to overs 7-15: better there than in the rest of his batting"),
    Skill("slow_burner", "Slow burner", "bat", 1, "slow_burn",
          "Takes time to settle, then accelerates far more than most once set"),
    Skill("pp_controller", "Powerplay controller", "bowl", 1, "pp_econ_lean",
          "His bowling leans to the powerplay: tighter there than in his other overs"),
    Skill("mid_controller", "Middle-overs controller", "bowl", 1, "mid_econ_lean",
          "His bowling leans to overs 7-15: tighter there than in his other overs"),
    Skill("death_controller", "Death-overs bowler", "bowl", 1, "death_econ_lean",
          "His bowling leans to the death: tighter there than in his other overs"),
    Skill("run_machine", "Run machine", "bat", 2, "complete",
          "Worth far more than par per ball: scores above it and seldom gets out"),
    Skill("spin_basher", "Spin basher", "bat", 2, "spin", "Scores far above par against spin"),
    Skill("pace_basher", "Pace basher", "bat", 2, "pace", "Scores far above par against pace"),
    Skill("miser", "Miser", "bowl", 2, "economy",
          "Concedes well below par for the situations he bowls in"),
    Skill("death_wickets", "Death wicket-taker", "bowl", 2, "death_wkts",
          "Takes far more wickets than par in overs 16-20"),
    Skill("pp_specialist", "Powerplay specialist", "bat", 3, "pp",
          "Among the very best batters in the powerplay, over a career and this season"),
    Skill("mid_dominator", "Middle-overs dominator", "bat", 3, "mid",
          "Among the very best batters in overs 7-15, over a career and this season"),
    Skill("death_hitter", "Death specialist", "bat", 3, "death",
          "Among the very best hitters in overs 16-20, over a career and this season"),
    Skill("new_ball", "New-ball specialist", "bowl", 3, "pp_econ",
          "Among the very best powerplay bowlers, over a career and this season"),
    Skill("mid_master", "Middle-overs specialist", "bowl", 3, "mid_econ",
          "Among the very best bowlers in overs 7-15, over a career and this season"),
    Skill("death_bowler", "Death specialist", "bowl", 3, "death_econ",
          "Among the very best bowlers at the death, over a career and this season"),
)
SKILL_BY_KEY = {s.key: s for s in SKILLS}

# How rare each tier is: the share of qualified seasons (and, for elite, careers) that clear
# it. Declared game design, like REPUTATION -- rarity is a choice, not a measurement.
SEASON_TOP = {1: 0.25, 2: 0.15, 3: 0.10}
ELITE_CAREER_TOP = 0.05
# A metric must REPEAT to hold a skill: its odd-season against even-season correlation over
# careers at least this (A257's bar). A quota alone would hand the top quarter of a metric
# that is mostly luck a badge it cannot back -- the middle-overs and death economy leans
# (r 0.08 and 0.14) are measured and badge nobody.
MIN_R = 0.20


def _style(measured: dict[str, Measured]) -> tuple[dict, dict]:
    """Anchor against aggressor, as one SHAPE: survival minus scoring, each in its own true
    standard deviations, so a player good (or poor) at both is neither. Season and career."""
    sc, sv = measured["scoring"], measured["survival"]
    s1 = math.sqrt(sc.career_var + sc.season_var) or 1.0
    s2 = math.sqrt(sv.career_var + sv.season_var) or 1.0
    season = {k: sv.season[k] / s2 - sc.season[k] / s1
              for k in sc.season.keys() & sv.season.keys()}
    c1, c2 = math.sqrt(sc.career_var) or 1.0, math.sqrt(sv.career_var) or 1.0
    career = {p: sv.career[p] / c2 - sc.career[p] / c1
              for p in sc.career.keys() & sv.career.keys()}
    return season, career


# A dismissal priced in runs an over, so survival and scoring add: the engine's own price of
# a wicket (A160, 5.8 runs) times the league's dismissals an over (0.051 a ball, A234).
WICKET_RUNS_AN_OVER = 5.8 * 0.051 * 6


def _complete(measured: dict[str, Measured]) -> tuple[dict, dict]:
    """Overall batting worth in runs an over: scoring edge plus survival priced as runs. For
    a season that is good at everything and best at nothing in particular."""
    sc, sv = measured["scoring"], measured["survival"]
    season = {k: sc.season[k] + WICKET_RUNS_AN_OVER * sv.season[k]
              for k in sc.season.keys() & sv.season.keys()}
    career = {p: sc.career[p] + WICKET_RUNS_AN_OVER * sv.career[p]
              for p in sc.career.keys() & sv.career.keys()}
    return season, career


def _series(s: Skill, measured: dict[str, Measured]):
    """(season values, season balls, career values, career balls, floor), signed so that
    higher is more of the skill."""
    if s.metric in ("style", "complete"):
        season, career = (_style if s.metric == "style" else _complete)(measured)
        m = measured["scoring"]
        return ({k: s.sign * v for k, v in season.items()}, m.season_balls,
                {k: s.sign * v for k, v in career.items()}, m.career_balls, "all")
    m = measured[s.metric]
    # An elite skill's season test reads the season on its own (`alone`); style and special
    # read the season shrunk toward his career, the best estimate of that season's skill.
    return (m.alone if s.tier == 3 else m.season, m.season_balls, m.career, m.career_balls,
            METRIC_BY_KEY[s.metric].floor)


def _top(values, share: float) -> float | None:
    pool = sorted(values, reverse=True)
    if not pool:
        return None
    return max(pool[max(0, math.ceil(len(pool) * share) - 1)], 1e-12)  # never at or below 0


def thresholds(measured: dict[str, Measured]) -> dict[str, dict[str, float]]:
    """Each skill's cuts: the season value its tier's share of qualified SEASONS clears,
    and for elite the career value the top share of qualified CAREERS clears. A metric the
    archive says does not repeat (no career spread, or r under MIN_R) holds no skill."""
    out = {}
    for s in SKILLS:
        if s.metric not in ("style", "complete") and (measured[s.metric].career_var <= 0
                                    or measured[s.metric].r < MIN_R):
            continue
        season, sballs, career, cballs, floor = _series(s, measured)
        cut = {"season": _top([v for k, v in season.items()
                               if sballs[k] >= SEASON_FLOOR[floor]], SEASON_TOP[s.tier])}
        if s.tier == 3:
            cut["career"] = _top([v for p, v in career.items()
                                  if cballs[p] >= POOL_FLOOR[floor]], ELITE_CAREER_TOP)
        if all(v is not None for v in cut.values()):
            out[s.key] = cut
    return out


def strength(s: Skill, v: float, measured: dict[str, Measured]) -> float:
    """How clearly, in true standard deviations: used to choose between two skills of one
    tier, so the clearer evidence is the one shown."""
    if s.metric == "style":
        return v
    if s.metric == "complete":
        sc, sv = measured["scoring"], measured["survival"]
        t = (sc.career_var + sc.season_var
             + WICKET_RUNS_AN_OVER ** 2 * (sv.career_var + sv.season_var))
        return v / math.sqrt(t) if t > 0 else 0.0
    m = measured[s.metric]
    t = m.career_var + m.season_var
    return v / math.sqrt(t) if t > 0 else 0.0


def season_skills(measured: dict[str, Measured], cuts: dict) -> dict:
    """{player: {season: [[skill key, tier, strength], ...]}} for every skill a season
    earns, strongest first. A season earns one if its value clears the season cut with
    enough balls in the situation THAT season to be in the pool (he must have played there);
    an elite skill also needs his career past the career cut, with a career there."""
    out: dict = defaultdict(lambda: defaultdict(list))
    for s in SKILLS:
        cut = cuts.get(s.key)
        if cut is None:
            continue
        season, sballs, career, cballs, floor = _series(s, measured)
        for (p, yr), v in season.items():
            if v < cut["season"] or sballs[(p, yr)] < SEASON_FLOOR[floor]:
                continue
            if s.tier == 3 and not (career.get(p, -1e9) >= cut["career"]
                                    and cballs.get(p, 0) >= POOL_FLOOR[floor]):
                continue
            out[p][str(yr)].append([s.key, s.tier, round(strength(s, v, measured), 4)])
    return {p: {yr: sorted(v, key=lambda x: (-x[2], x[0])) for yr, v in sorted(d.items())}
            for p, d in sorted(out.items())}


# --------------------------------------------------------------------------- roles

BAT_JOBS = ("opener", "top", "middle", "finisher", "lower")
BOWL_JOBS = ("new ball", "middle", "death", "workhorse")
MIN_INNINGS_FOR_JOB = 5                 # A72/A76's own floor for where a man bats
MIN_BALLS_FOR_JOB = 60                  # ten overs in a season


def bat_job(entry_overs: list[int], positions: list[int]) -> str:
    """The job, by WHEN he came in -- never by how he did (A257): a role defined by its own
    outcome would rank slow batters against slow batters and reward slowness."""
    pos, ov = st.median(positions), st.median(entry_overs)
    if pos >= 8:
        return "lower"
    if pos <= 2:
        return "opener"
    if ov < 6:
        return "top"
    if ov < 14:
        return "middle"
    return "finisher"


def bowl_job(share: dict[str, float]) -> str:
    if share["pp"] >= 0.45:
        return "new ball"
    if share["death"] >= 0.40:
        return "death"
    if share["mid"] >= 0.70:
        return "middle"
    return "workhorse"


def roles(balls: list[Ball]) -> dict:
    """{player: {"bat": {season: job}, "bowl": {season: job}, "bat_career": job,
    "bowl_career": job}}. A season too thin to say falls back to the career, as A76 does
    for batting positions; a career too thin to say has no job at all, never a default."""
    inns: dict = {}
    bowled: dict = defaultdict(Counter)
    for b in balls:
        inns.setdefault((b.batter, b.season, b.match, b.inn), (b.entry_over, b.pos))
        bowled[(b.bowler, b.season)][phase_of(b.over)] += 1
    bat_in: dict = defaultdict(lambda: ([], []))
    bat_career: dict = defaultdict(lambda: ([], []))
    for (p, s, _m, _i), (ov, pos) in inns.items():
        for d in (bat_in[(p, s)], bat_career[p]):
            d[0].append(ov)
            d[1].append(pos)
    out: dict = defaultdict(lambda: {"bat": {}, "bowl": {}})
    for p, (ovs, ps) in bat_career.items():
        if len(ovs) >= MIN_INNINGS_FOR_JOB:
            out[p]["bat_career"] = bat_job(ovs, ps)
    for (p, s), (ovs, ps) in bat_in.items():
        job = (bat_job(ovs, ps) if len(ovs) >= MIN_INNINGS_FOR_JOB
               else out[p].get("bat_career"))
        if job:
            out[p]["bat"][str(s)] = job
    career_bowl: dict = defaultdict(Counter)
    for (p, s), c in bowled.items():
        career_bowl[p].update(c)
    for p, c in career_bowl.items():
        n = sum(c.values())
        if n >= MIN_BALLS_FOR_JOB:
            out[p]["bowl_career"] = bowl_job({ph: c[ph] / n for ph in PHASES})
    for (p, s), c in bowled.items():
        n = sum(c.values())
        job = (bowl_job({ph: c[ph] / n for ph in PHASES}) if n >= MIN_BALLS_FOR_JOB
               else out[p].get("bowl_career"))
        if job:
            out[p]["bowl"][str(s)] = job
    return {p: v for p, v in sorted(out.items()) if v["bat"] or v["bowl"]}


def build(balls: list[Ball]) -> dict:
    """Everything the snapshot holds: each metric's measured spreads, the cuts, every
    player-season's skills, and season roles."""
    measured = measure(accumulate(balls))
    cuts = thresholds(measured)
    return {
        "metrics": {k: {"career_sd": round(math.sqrt(m.career_var), 6),
                        "season_sd": round(math.sqrt(m.season_var), 6),
                        "season_share": round(m.season_share, 4),
                        "players": m.players, "seasons": m.seasons, "r": round(m.r, 4)}
                    for k, m in measured.items()},
        "cuts": {k: {kk: round(vv, 6) for kk, vv in v.items()} for k, v in sorted(cuts.items())},
        "skills": season_skills(measured, cuts),
        "roles": roles(balls),
    }
