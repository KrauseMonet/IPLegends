"""How much the engine's two numbers truly differ between players, and between one player's
seasons, on the engine's own scale [A259]. Measurement only; writes nothing.

    uv run python -m tools.engine_spread

Reads `player_season_impact` (A160's halves, already scored against the state model) and the
state model itself. Each season's half is taken as an EXCESS over its own band prior (A46),
per ball. Then, per discipline and half:

  career var  -- covariance of a player's odd-season and even-season excess (balls-weighted
                 means), players with enough balls on both sides;
  season var  -- method of moments: the variance of a season's excess about the rest of his
                 career (leave-one-season-out, shrunk), less the season's own sampling noise
                 and the prior's own uncertainty;
  noise       -- runs: the state model's per-ball variance of runs off the bat, weighted by
                 how often each state is faced; outs: binomial, p(1 - p) a ball. Each is
                 multiplied by a measured DESIGN EFFECT: balls in one innings are not
                 independent (a good day lifts all of them), so the real noise of a season is
                 larger than the per-ball formula says. Measured from two halves of a season
                 (alternate matches), whose difference is pure noise.

These are the constants the view's two-level shrinkage uses (migration 052). Local database
only (A173).
"""

from __future__ import annotations

import math
import statistics as st
import sys
from collections import defaultdict

from etl.db import connect

CAREER_FLOOR = 300          # balls on each side of the odd/even split
PRIOR_FLOOR = 200           # balls in the rest of his career for a season to be judged
SEASON_FLOOR = 60           # balls in the season itself


def runs_noise(conn) -> float:
    """Per-ball variance of runs off the bat, averaged over the states as faced."""
    rows = conn.execute(
        "select faced, runs_0, runs_1, runs_2, runs_3, runs_4, runs_5, runs_6 "
        "from state_ball_outcomes where faced > 0").fetchall()
    num = den = 0.0
    for faced, *c in rows:
        m = sum(k * n for k, n in enumerate(c)) / faced
        v = sum(k * k * n for k, n in enumerate(c)) / faced - m * m
        num += v * faced
        den += faced
    return num / den


def design_effects(conn) -> dict[tuple[str, str], float]:
    """Real season noise over independent-ball noise, per discipline and half: the mean
    squared difference of a season's two halves against what independent balls predict."""
    from etl import skills as S
    acc = S.accumulate(S.load(conn))
    out = {}
    for disc, half, key in (("batting", "runs", "scoring"), ("batting", "outs", "survival"),
                            ("bowling", "runs", "economy"), ("bowling", "outs", "wickets")):
        m = S.METRIC_BY_KEY[key]
        obs, pred = [], []
        for (p, d), seasons in acc.items():
            if d != m.disc:
                continue
            for parts in seasons.values():
                a, b = S.value(m, parts[0]), S.value(m, parts[1])
                if a and b and min(a[2], b[2]) >= SEASON_FLOOR:
                    obs.append((a[0] - b[0]) ** 2)
                    pred.append(a[1] + b[1])
        out[(disc, half)] = st.fmean(obs) / st.fmean(pred)
    return out


def rows(conn) -> list[tuple]:
    return conn.execute("""
        select i.person_id, i.discipline, f.season_year, i.balls,
               i.runs_total / i.balls - i.runs_prior_per_ball,
               i.outs_excess_total / i.balls - i.outs_prior_per_ball,
               i.outs_expected / i.balls
          from player_season_impact i join franchise_seasons f using (franchise_season_id)
         where i.balls > 0""").fetchall()


def _cov(xs, ys) -> float:
    mx, my = st.fmean(xs), st.fmean(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (len(xs) - 1)


def measure(data: list[tuple], sigma_runs: float, design: dict) -> dict:
    """{(discipline, half): {"career": var, "season": var, ...}} for half in runs / outs."""
    out = {}
    for disc in ("batting", "bowling"):
        mine = [r for r in data if r[1] == disc]
        by_p = defaultdict(list)
        for r in mine:
            by_p[r[0]].append(r)
        for half, xi in (("runs", 4), ("outs", 5)):
            de = design[(disc, half)]

            def noise(r):
                if half == "runs":
                    return de * sigma_runs / r[3]
                return de * r[6] * (1 - r[6]) / r[3]

            def wmean(rs):
                b = sum(r[3] for r in rs)
                return (sum(r[xi] * r[3] for r in rs) / b, b) if b else (None, 0)

            xs, ys = [], []
            for rs in by_p.values():
                (a, na), (c, nc) = wmean([r for r in rs if r[2] % 2]), \
                                   wmean([r for r in rs if not r[2] % 2])
                if na >= CAREER_FLOOR and nc >= CAREER_FLOOR:
                    xs.append(a)
                    ys.append(c)
            cv = max(_cov(xs, ys), 0.0)
            base = de * (sigma_runs if half == "runs"
                         else st.fmean(r[6] * (1 - r[6]) for r in mine))
            devs, noises, posts = [], [], []
            for rs in by_p.values():
                for r in rs:
                    rest = [o for o in rs if o is not r]
                    m, b = wmean(rest)
                    if b < PRIOR_FLOOR or r[3] < SEASON_FLOOR:
                        continue
                    pn = base / b
                    prior = m * cv / (cv + pn)
                    devs.append(r[xi] - prior)
                    noises.append(noise(r))
                    posts.append(cv * pn / (cv + pn))
            sv = max(st.pvariance(devs) - st.fmean(noises) - st.fmean(posts), 0.0)
            out[(disc, half)] = {"career": cv, "season": sv, "players": len(xs), "design": de,
                                 "seasons": len(devs), "noise_per_ball": base,
                                 "k_career": base / cv if cv else math.inf,
                                 "k_season": base / sv if sv else math.inf}
    return out


def main() -> int:
    with connect() as conn:
        sigma = runs_noise(conn)
        data = rows(conn)
        design = design_effects(conn)
    print(f"per-ball variance of runs off the bat (state model): {sigma:.4f}")
    for (disc, half), m in measure(data, sigma, design).items():
        unit = 6 if half == "runs" else 1
        print(f"  {disc:8} {half:5} career sd {math.sqrt(m['career']) * unit:.4f} "
              f"season sd {math.sqrt(m['season']) * unit:.4f}  "
              f"({'runs an over' if half == 'runs' else 'outs a ball'})  "
              f"k career {m['k_career']:.0f} balls, k season {m['k_season']:.0f} balls  "
              f"design {m['design']:.2f} [{m['players']} players, {m['seasons']} seasons]")
    print("the view today uses k = 100 balls for both halves, toward the band prior")
    return 0


if __name__ == "__main__":
    sys.exit(main())
