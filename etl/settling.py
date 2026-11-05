"""The settling-in curve: how a batter's scoring and his chance of getting out change with the
balls he has already faced in an innings [A260].

Measured, never chosen (A257 found it first: a batter's first ball runs about 30-47 runs per
100 balls below his own settled rate). Two estimates, each controlling what would otherwise
fake the curve:

- SCORING by balls faced, with every INNINGS as its own baseline (fixed effects, solved by
  alternating projections). That removes the batter's quality and his form on the day, so a
  long innings cannot look fast merely because good days last longer.
- GETTING OUT by balls faced, with every BATTER-SEASON as its own baseline. Innings cannot be
  the baseline here: each innings ends on its one dismissal, so an innings baseline would
  manufacture the curve. Survivorship runs the safe way: in-form days last longer AND get
  out less, so if anything the late-ball hazard is understated.

Both are deviations from par for the ball's state (over x wickets), so the archive's own
state model already holds the AVERAGE new/set mix in every state. The curve is therefore
CENTRED WITHIN EACH ENGINE STATE (over x bucketed wickets, A31) on that state's real mix of
balls faced, measured on the fitting set (first innings, full length, A15/A21): within a
state it only moves scoring from new batters to set ones, and each state's total stays
exactly as fitted. Openers at over 0 are all new, and the state already prices that.

Pure apart from `load`.
"""

from __future__ import annotations

from collections import defaultdict

from etl.skills import PHASES, Ball, phase_of
from etl.state_model import bucket_of

# Balls already faced, bucketed. The curve settles within about nine balls (A257); 15+ is
# "set".
FACED_BUCKETS = ((0, 0), (1, 1), (2, 2), (3, 4), (5, 9), (10, 14), (15, 10**6))
ITERATIONS = 30


def faced_bucket(faced: int) -> int:
    for i, (lo, hi) in enumerate(FACED_BUCKETS):
        if lo <= faced <= hi:
            return i
    raise AssertionError(faced)


def scoring_curve(balls: list[Ball]) -> dict[tuple[str, int], float]:
    """{(phase, bucket): runs a ball}, from innings fixed effects. Identified up to one
    constant, which the per-state centring removes."""
    inn = [(b.match, b.inn, b.batter) for b in balls]
    cell = [(phase_of(b.over), faced_bucket(b.faced)) for b in balls]
    r = [b.runs - b.par_runs for b in balls]
    a: dict = defaultdict(float)
    eff: dict = defaultdict(float)
    for _ in range(ITERATIONS):
        s, n = defaultdict(float), defaultdict(int)
        for i, k in enumerate(cell):
            s[k] += r[i] - a[inn[i]]
            n[k] += 1
        eff = {k: s[k] / n[k] for k in s}
        s2, n2 = defaultdict(float), defaultdict(int)
        for i, k in enumerate(inn):
            s2[k] += r[i] - eff[cell[i]]
            n2[k] += 1
        a = {k: s2[k] / n2[k] for k in s2}
    return eff


def hazard_curve(balls: list[Ball]) -> dict[tuple[str, int], float]:
    """{(phase, bucket): dismissal hazard relative to his own phase rate}, from
    batter-season fixed effects on the out-above-par of each ball, as a RATIO: (phase par
    hazard + effect) / phase par hazard."""
    who = [(b.batter, b.season) for b in balls]
    cell = [(phase_of(b.over), faced_bucket(b.faced)) for b in balls]
    o = [b.out - b.par_out for b in balls]
    a: dict = defaultdict(float)
    eff: dict = defaultdict(float)
    for _ in range(ITERATIONS):
        s, n = defaultdict(float), defaultdict(int)
        for i, k in enumerate(cell):
            s[k] += o[i] - a[who[i]]
            n[k] += 1
        eff = {k: s[k] / n[k] for k in s}
        s2, n2 = defaultdict(float), defaultdict(int)
        for i, k in enumerate(who):
            s2[k] += o[i] - eff[cell[i]]
            n2[k] += 1
        a = {k: s2[k] / n2[k] for k in s2}
    par = defaultdict(lambda: [0.0, 0])
    for b in balls:
        p = par[phase_of(b.over)]
        p[0] += b.par_out
        p[1] += 1
    base = {ph: v[0] / v[1] for ph, v in par.items()}
    # the effects carry an arbitrary constant too: anchor each phase's mean effect, over its
    # own balls, at zero before turning them into ratios
    mean = defaultdict(lambda: [0.0, 0])
    for k in cell:
        mean[k[0]][0] += eff[k]
        mean[k[0]][1] += 1
    return {k: max((base[k[0]] + v - mean[k[0]][0] / mean[k[0]][1]) / base[k[0]], 0.05)
            for k, v in eff.items()}


def state_mix(balls: list[Ball]) -> dict[tuple[int, str], dict[int, float]]:
    """{(over, wicket bucket): {faced bucket: share of that state's balls}} on the fitting
    set: first innings of full length (A15/A21). Super overs are already excluded by `load`."""
    n: dict = defaultdict(lambda: defaultdict(int))
    for b in balls:
        if b.inn == 1:
            n[(b.over, bucket_of(b.wk))][faced_bucket(b.faced)] += 1
    return {k: {fb: c / sum(v.values()) for fb, c in v.items()} for k, v in n.items()}


def centred(curve: dict, mix: dict, ratio: bool) -> dict[tuple[int, str, int], float]:
    """Per engine state and faced bucket: the curve less (or, for a ratio, divided by) that
    state's own average over its real mix, so the state's total is untouched."""
    out = {}
    for (over, wb), shares in mix.items():
        ph = phase_of(over)
        avg = sum(sh * curve[(ph, fb)] for fb, sh in shares.items())
        for fb in range(len(FACED_BUCKETS)):
            v = curve[(ph, fb)]
            out[(over, wb, fb)] = v / avg if ratio else v - avg
    return out


def build(balls: list[Ball]) -> dict:
    """What the engine needs: per (over, wicket bucket, faced bucket), runs a ball to add and
    a multiplier on the dismissal odds. JSON-shaped for the model inputs."""
    mix = state_mix(balls)
    runs = centred(scoring_curve(balls), mix, ratio=False)
    outs = centred(hazard_curve(balls), mix, ratio=True)
    return {"buckets": [list(b) for b in FACED_BUCKETS],
            "cells": sorted([o, wb, fb, round(runs[(o, wb, fb)], 6), round(outs[(o, wb, fb)], 6)]
                            for (o, wb, fb) in runs)}


def player_offsets(balls: list[Ball], table: dict) -> dict[tuple[str, int], tuple[float, float]]:
    """{(batter, season): (mean runs adjustment, mean log dismissal multiplier)} over HIS OWN
    real balls. A batter's measured level (A160/A259) already includes how often he was set,
    so the engine must take this back out of his level before it adds the curve per ball, or
    an anchor is paid twice for being set."""
    import math
    cells = {(o, wb, fb): (r, m) for o, wb, fb, r, m in table["cells"]}
    acc: dict = defaultdict(lambda: [0.0, 0.0, 0])
    for b in balls:
        c = cells.get((b.over, bucket_of(b.wk), faced_bucket(b.faced)))
        if c is None:
            continue
        a = acc[(b.batter, b.season)]
        a[0] += c[0]
        a[1] += math.log(c[1])
        a[2] += 1
    return {k: (v[0] / v[2], v[1] / v[2]) for k, v in acc.items()}
