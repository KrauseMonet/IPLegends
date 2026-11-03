"""Each player's phase specialisation, measured from his whole career and shrunk [A248].

How much better (or worse) a player scored, or saved runs, in the powerplay, the middle
overs and the death than his OWN average across the phases he played in -- so a phase skill
is a lean, never a level: the level stays the card's (A160). Each season is measured against
that season's par in that phase, then pooled over the career and shrunk toward no lean by
`SHRINK_BALLS` pseudo-balls.

The shrinkage is MEASURED, not chosen: splitting careers into odd and even years, the
covariance of the two halves' leans estimates the true spread, and the per-ball variance in
that phase over it is the pseudo-ball count that makes a lean as reliable as the archive
says it is (a 373-ball half at 1,201 is 24% reliable, against a measured 25%). Wickets and
dismissals by phase were measured too and carry over not at all (r about 0), so only runs
are leaned. Only Build a Career reads this, for its own player.

Phases are the deliveries' own (SPEC 4.8/A5), from full-length innings only.
"""

from __future__ import annotations

from collections import defaultdict

PHASES = ("powerplay", "middle", "death")
# Measured 2026-11-03 over career halves with 120+ balls in the phase in each half.
SHRINK_BALLS = {
    ("bat", "powerplay"): 970, ("bat", "middle"): 920, ("bat", "death"): 620,
    ("bowl", "powerplay"): 1200, ("bowl", "middle"): 1150, ("bowl", "death"): 750,
}

BOWLING_SQL = """
select d.bowler_id, m.season_year, d.phase,
       count(*) filter (where d.extra_wides = 0 and d.extra_noballs = 0),
       sum(d.runs_batter + d.extra_wides + d.extra_noballs)
from deliveries d join matches m using (match_id)
where not d.is_super_over and d.innings_scheduled_balls = 120 and d.phase is not null
group by 1, 2, 3
"""
BATTING_SQL = """
select d.batter_id, m.season_year, d.phase,
       count(*) filter (where d.extra_wides = 0),
       sum(d.runs_batter)
from deliveries d join matches m using (match_id)
where not d.is_super_over and d.innings_scheduled_balls = 120 and d.phase is not null
group by 1, 2, 3
"""


def leans(rows, disc: str) -> dict[str, list[float]]:
    """(person, season, phase, balls, runs) rows -> {person: [powerplay, middle, death]},
    each the shrunk runs a ball above his own average in that phase. For bowling the sign
    is the engine's: positive is runs SAVED (A160's `bowl_scoring`)."""
    par = defaultdict(lambda: [0, 0])
    for _p, y, ph, b, r in rows:
        if b:
            par[(y, ph)][0] += b
            par[(y, ph)][1] += r
    excess = defaultdict(lambda: {ph: [0, 0.0] for ph in PHASES})
    for p, y, ph, b, r in rows:
        if not b:
            continue
        e = r - b * par[(y, ph)][1] / par[(y, ph)][0]
        excess[p][ph][0] += b
        excess[p][ph][1] += e
    sign = -1.0 if disc == "bowl" else 1.0
    out = {}
    for p, d in excess.items():
        balls = sum(v[0] for v in d.values())
        if not balls:
            continue
        own = sum(v[1] for v in d.values()) / balls
        lean = []
        for ph in PHASES:
            n, e = d[ph]
            obs = (e / n - own) if n else 0.0
            lean.append(round(sign * obs * n / (n + SHRINK_BALLS[(disc, ph)]), 6))
        if any(lean):
            out[p] = lean
    return out


def fetch(conn) -> dict[str, dict[str, list[float]]]:
    """{person: {"bat": [...], "bowl": [...]}}, a discipline left out where he has none."""
    out: dict[str, dict] = defaultdict(dict)
    for disc, sql in (("bat", BATTING_SQL), ("bowl", BOWLING_SQL)):
        rows = conn.execute(sql).fetchall()
        for p, lean in leans(rows, disc).items():
            out[p][disc] = lean
    return dict(sorted(out.items()))
