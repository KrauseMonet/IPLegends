"""Chasing intent: how batters change their game with the scoreboard [A261].

The engine plays a second innings with the first innings' state model and uses the target
only to stop. Real chasers do not: measured over the archive's full-length chases, a side
well behind the rate scores about 22 runs per 100 balls above par at 1.31x the dismissal
risk, and one well ahead about 13 below at 0.84x -- the same trade of runs against risk
that A204 measured independently (about 1.25 log-odds per run a ball). This module measures
that dial; the engine turns it.

PRESSURE is the same number here and in the engine: the runs still needed, less what the
ENGINE ITSELF expects an average side to score from this ball and these wickets
(`Costs.remaining`, A160, plus extras at the engine's own rates). Positive is behind.

Each ball's residual is against the engine's own par for its state (`Model.state`) and the
settling curve (A260), and then against the BATTER-SEASON's own mean residual, so a weak
side that falls behind is not mistaken for a side that chose to attack. Effects are read per
phase and pressure bucket, and the engine interpolates between bucket centres.

Pure apart from `measure`'s inputs; no database of its own.
"""

from __future__ import annotations

import math
from collections import defaultdict

from etl.skills import phase_of

# Bucket edges in runs of pressure, and the centre the engine interpolates from. Beyond the
# outer centres the effect is held flat rather than extrapolated.
EDGES = (-40, -20, -5, 5, 20, 40)
CENTRES = (-50.0, -30.0, -12.5, 0.0, 12.5, 30.0, 50.0)
BALLS = 120


def bucket(pressure: float) -> int:
    for i, e in enumerate(EDGES):
        if pressure < e:
            return i
    return len(EDGES)


def expected_remaining(model, legal: int, wickets: int) -> float:
    """What the engine expects an average side to add from here: runs off the bat by its own
    chain (A160), plus extras at its flat rates."""
    if legal >= BALLS or wickets >= 10:
        return 0.0
    left = BALLS - legal
    return (model.costs.remaining[legal][min(wickets, 9)]
            + left * (model.extras_rate + model.wide_rate * model.wide_runs))


def pressure(model, target: int, runs: int, legal: int, wickets: int) -> float:
    """Runs needed to win (target beaten by one) less what the engine expects from here."""
    return (target + 1 - runs) - expected_remaining(model, legal, wickets)


def measure(rows, model, settle: dict) -> dict:
    """`rows`: ordered deliveries (match, season, innings, over, ball, batter, runs off the
    bat, extras, wides, no-balls, player out, wicket kind) of full-length innings.

    Returns {"cells": [[phase, bucket, runs a ball, dismissal multiplier, balls], ...]}."""
    from etl.settling import faced_bucket
    from etl.state_model import bucket_of

    totals: dict = {}
    recs = []
    key = None
    for (mid, season, inn, over, _b, bat, rb, rx, wd, nb, pout, kind) in rows:
        if (mid, inn) != key:
            key = (mid, inn)
            runs = wk = legal = 0
            faced: dict = defaultdict(int)
        if wd == 0:
            probs, values = model.state(over, wk)
            par = sum(q * v for q, v in zip(probs[1:], values[1:]))
            pout_ = probs[0]
            s = settle.get((over, bucket_of(wk), faced_bucket(faced[bat])))
            if s is not None:
                par += s[0]
                pout_ *= s[1]
            recs.append((mid, inn, season, bat, over, runs, legal, wk, rb - par,
                         pout == bat and kind != "retired hurt", pout_))
            faced[bat] += 1
        runs += rb + rx
        if wd == 0 and nb == 0:
            legal += 1
        if pout is not None and kind != "retired hurt":
            wk += 1
        totals[(mid, inn)] = runs

    # each batter-season's own mean, both innings, so a side's strength is not pressure
    own_r: dict = defaultdict(lambda: [0.0, 0])
    own_o: dict = defaultdict(lambda: [0.0, 0.0])
    for (mid, inn, season, bat, over, runs, legal, wk, res, out, po) in recs:
        a = own_r[(bat, season)]
        a[0] += res
        a[1] += 1
        b = own_o[(bat, season)]
        b[0] += out
        b[1] += po
    cells: dict = defaultdict(lambda: [0.0, 0.0, 0.0, 0])
    for (mid, inn, season, bat, over, runs, legal, wk, res, out, po) in recs:
        if inn != 2 or (mid, 1) not in totals:
            continue
        p = pressure(model, totals[(mid, 1)], runs, legal, wk)
        a, b = own_r[(bat, season)], own_o[(bat, season)]
        ratio = (b[0] + 0.5) / (b[1] + 0.5)
        c = cells[(phase_of(over), bucket(p))]
        c[0] += res - a[0] / a[1]
        c[1] += out
        c[2] += po * ratio
        c[3] += 1
    return {"centres": list(CENTRES),
            "cells": sorted([ph, bk, round(v[0] / v[3], 6), round(v[1] / v[2], 6), v[3]]
                            for (ph, bk), v in cells.items())}


MIN_CELL = 1000     # balls: a thinner cell is noise (death, 40+ ahead, holds 230), and the
                    # engine holds its neighbour's effect flat across it instead


def table(doc: dict) -> dict[str, list[tuple[float, float, float]]]:
    """{phase: [(centre, runs a ball, dismissal multiplier), ...]} sorted by centre, cells
    under MIN_CELL balls left out."""
    out: dict = defaultdict(list)
    centres = doc.get("centres", CENTRES)
    for ph, bk, r, m, n in doc.get("cells", []):
        if n >= MIN_CELL:
            out[ph].append((centres[bk], r, m))
    return {ph: sorted(v) for ph, v in out.items()}


def at(curve: list[tuple[float, float, float]], p: float) -> tuple[float, float]:
    """(runs a ball, dismissal multiplier) at pressure `p`, linear between centres and flat
    beyond the outer ones; the multiplier is interpolated on the log scale."""
    if p <= curve[0][0]:
        return curve[0][1], curve[0][2]
    if p >= curve[-1][0]:
        return curve[-1][1], curve[-1][2]
    for (x0, r0, m0), (x1, r1, m1) in zip(curve, curve[1:]):
        if x0 <= p <= x1:
            f = (p - x0) / (x1 - x0)
            return r0 + f * (r1 - r0), math.exp(math.log(m0) + f * (math.log(m1) - math.log(m0)))
    raise AssertionError(p)
