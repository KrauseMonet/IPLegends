"""Phase 0 of the specialisation plan: measurements only. Nothing here writes anywhere.

    uv run python -m tools.specialisation_report            # every section
    uv run python -m tools.specialisation_report settling   # one section

Reads `deliveries` from the LOCAL database (A173: never run this against Neon -- it pulls
the whole table into Python). Every figure in the report is recomputed from the archive on
each run, so re-running it is how a quoted number gets re-checked.

Sections:
  settling   -- the same batter's scoring and dismissal rate by balls already faced
  shrink     -- how reliable each half of a season is (scoring, getting out), and the
                pseudo-ball k that reliability implies, against the k = 100 the view uses
  roles      -- batting job by when a batter came in, bowling job by phase share: counts per
                season, how often a job holds from one season to the next, captains' death use
  skills     -- which leans (phase, spin/pace, chasing) repeat from one season to the next
  anchors    -- outside judges: Player of the Match, and whether long innings win matches
  partner    -- does a batter score faster beside a low-dismissal or a set partner
  career     -- every candidate skill measured over a career; named players placed on it
"""

from __future__ import annotations

import math
import statistics as st
import sys
from collections import Counter, defaultdict

from etl.db import connect

from etl.skills import PHASES, Ball, phase_of  # noqa: F401  (one ball scan, A258)
from etl.skills import load as _load


def load() -> list[Ball]:
    with connect() as conn:
        return _load(conn)


def add_par(balls: list[Ball]) -> None:
    """`etl.skills.load` already sets par; kept so the sections read as before."""


def pearson(xs, ys) -> float:
    if len(xs) < 3:
        return float("nan")
    mx, my = st.fmean(xs), st.fmean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / math.sqrt(sxx * syy) if sxx and syy else float("nan")


def k_from(r: float, n: float) -> float:
    """Pseudo-balls of shrinkage implied by a split-half correlation r at n balls a half:
    reliability n / (n + k) = r."""
    return n * (1 - r) / r if r > 0 else float("inf")


# --------------------------------------------------------------------------- settling

BUCKETS = ((0, 0), (1, 1), (2, 2), (3, 4), (5, 9), (10, 14), (15, 19), (20, 29), (30, 999))


def bucket(f: int) -> str:
    for lo, hi in BUCKETS:
        if lo <= f <= hi:
            return f"{lo}-{hi}" if hi < 999 else f"{lo}+"
    raise AssertionError


def settling(balls: list[Ball]) -> None:
    print("\n== SETTLING: the same batter, by balls already faced ==")
    print("Residuals are runs (or outs) above par for the state, minus the batter-season's own")
    print("mean, so a better batter does not make long innings look faster. Runs per 100 balls.")
    tot: dict = defaultdict(lambda: [0.0, 0.0, 0])
    for b in balls:
        t = tot[(b.batter, b.season)]
        t[0] += b.runs - b.par_runs
        t[1] += b.out - b.par_out
        t[2] += 1
    mean = {k: (v[0] / v[2], v[1] / v[2]) for k, v in tot.items() if v[2] >= 150}
    long_inn: Counter = Counter()
    for b in balls:
        long_inn[(b.match, b.inn, b.batter)] += 1
    for label, keep in (("all innings", lambda b: True),
                        ("innings that lasted 25+ balls (no survivor bias in scoring)",
                         lambda b: long_inn[(b.match, b.inn, b.batter)] >= 25)):
        print(f"\n  {label}")
        print(f"  {'faced':>7}" + "".join(f"{ph:>18}" for ph in PHASES))
        cells: dict = defaultdict(lambda: [0.0, 0.0, 0])
        for b in balls:
            m = mean.get((b.batter, b.season))
            if m is None or not keep(b):
                continue
            c = cells[(bucket(b.faced), phase_of(b.over))]
            c[0] += b.runs - b.par_runs - m[0]
            c[1] += b.out - b.par_out - m[1]
            c[2] += 1
        for lo, hi in BUCKETS:
            name = f"{lo}-{hi}" if hi < 999 else f"{lo}+"
            row = f"  {name:>7}"
            for ph in PHASES:
                c = cells.get((name, ph))
                row += (f"{c[0] / c[2] * 100:+7.1f} {c[1] / c[2] * 100:+5.2f}% n{c[2]:>5}"
                        if c and c[2] >= 300 else f"{'':>18}")
            print(row)
    # The cost of a new batter: runs his first balls fall short of his settled (15+) rate.
    print("\n  Cost of a new batter (all innings): runs his first 15 balls fall short of his")
    print("  own rate once settled (15+ balls faced), in each phase he arrives in")
    cells2: dict = defaultdict(lambda: [0.0, 0])
    for b in balls:
        m = mean.get((b.batter, b.season))
        if m is None:
            continue
        c = cells2[(min(b.faced, 15), phase_of(b.over))]
        c[0] += b.runs - b.par_runs - m[0]
        c[1] += 1
    for ph in PHASES:
        settled = cells2[(15, ph)][0] / max(cells2[(15, ph)][1], 1)
        d = sum(settled - cells2[(f, ph)][0] / cells2[(f, ph)][1]
                for f in range(15) if cells2[(f, ph)][1] >= 100)
        print(f"    {ph:>5}: {d:.2f} runs")


# --------------------------------------------------------------------------- shrink

def shrink(balls: list[Ball]) -> None:
    print("\n== SHRINKAGE: how reliable each half of a season is ==")
    print("Each batter-season (and bowler-season) split by alternate matches. r is the")
    print("correlation of the two halves; k is the pseudo-balls of shrinkage it implies.")
    print("The view shrinks BOTH halves with k = 100 balls (A160).")
    for who, key, outf in (("batting", lambda b: b.batter, lambda b: b.out),
                           ("bowling", lambda b: b.bowler, lambda b: b.out and b.credited)):
        halves: dict = defaultdict(lambda: [[0.0, 0.0, 0.0, 0] for _ in range(2)])
        matches: dict = defaultdict(list)
        for b in balls:
            ms = matches[(key(b), b.season)]
            if not ms or ms[-1] != b.match:
                ms.append(b.match)
        idx = {k: {m: i % 2 for i, m in enumerate(v)} for k, v in matches.items()}
        for b in balls:
            k = (key(b), b.season)
            h = halves[k][idx[k][b.match]]
            h[0] += b.runs - b.par_runs
            h[1] += outf(b)
            h[2] += b.par_out
            h[3] += 1
        for floor in (150, 300):
            xs, ys, ds, es, ns = [], [], [], [], []
            for (a, c) in halves.values():
                if a[3] + c[3] < floor or min(a[3], c[3]) < floor / 3 or not a[2] or not c[2]:
                    continue
                xs.append(a[0] / a[3])
                ys.append(c[0] / c[3])
                # log ratio of actual to expected outs, with half an out added each side so a
                # half with no dismissal is not minus infinity
                ds.append(math.log((a[1] + 0.5) / (a[2] + 0.5)))
                es.append(math.log((c[1] + 0.5) / (c[2] + 0.5)))
                ns.append((a[3] + c[3]) / 2)
            n = st.fmean(ns)
            rs, rd = pearson(xs, ys), pearson(ds, es)
            print(f"  {who:8} {len(xs):>4} seasons, {floor}+ balls, ~{n:.0f} balls a half: "
                  f"scoring r {rs:.2f} (k {k_from(rs, n):.0f}), "
                  f"{'getting out' if who == 'batting' else 'wickets'} r {rd:.2f} "
                  f"(k {k_from(rd, n):.0f})")
        # Across a CAREER: odd seasons against even seasons, pooled, for players with plenty
        # of balls in both. A trait that is noise in one season can still be real over many.
        car: dict = defaultdict(lambda: [[0.0, 0.0, 0.0, 0] for _ in range(2)])
        for b in balls:
            h = car[key(b)][b.season % 2]
            h[0] += b.runs - b.par_runs
            h[1] += outf(b)
            h[2] += b.par_out
            h[3] += 1
        for floor in (400, 1000):
            xs, ys, ds, es, ns = [], [], [], [], []
            for a, c in car.values():
                if min(a[3], c[3]) < floor or not a[2] or not c[2]:
                    continue
                xs.append(a[0] / a[3])
                ys.append(c[0] / c[3])
                ds.append(math.log((a[1] + 0.5) / (a[2] + 0.5)))
                es.append(math.log((c[1] + 0.5) / (c[2] + 0.5)))
                ns.append((a[3] + c[3]) / 2)
            n = st.fmean(ns)
            rs, rd = pearson(xs, ys), pearson(ds, es)
            print(f"  {who:8} CAREER odd v even seasons, {len(xs):>3} players, {floor}+ balls "
                  f"each side, ~{n:.0f} a side: scoring r {rs:.2f} (k {k_from(rs, n):.0f}), "
                  f"{'getting out' if who == 'batting' else 'wickets'} r {rd:.2f} "
                  f"(k {k_from(rd, n):.0f}), true spread of ln multiplier "
                  f"{math.sqrt(max(rd, 0) * st.pvariance(ds)):.2f}")


# --------------------------------------------------------------------------- roles

def bat_job(entry_overs: list[int], positions: list[int]) -> str:
    pos = st.median(positions)
    ov = st.median(entry_overs)
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
    print("\n== ROLES ==")
    inns: dict = {}
    bat_balls: Counter = Counter()
    for b in balls:
        inns.setdefault((b.batter, b.season, b.match, b.inn), (b.entry_over, b.pos))
        bat_balls[(b.batter, b.season)] += 1
    per: dict = defaultdict(lambda: ([], []))
    for (p, s, _m, _i), (ov, pos) in inns.items():
        per[(p, s)][0].append(ov)
        per[(p, s)][1].append(pos)
    bjob = {k: bat_job(*v) for k, v in per.items() if bat_balls[k] >= 100 and len(v[0]) >= 5}
    bowl_ph: dict = defaultdict(Counter)
    for b in balls:
        bowl_ph[(b.bowler, b.season)][phase_of(b.over)] += 1
    wjob = {}
    for k, c in bowl_ph.items():
        n = sum(c.values())
        if n >= 150:
            wjob[k] = bowl_job({ph: c[ph] / n for ph in PHASES})
    for label, jobs in (("batting (100+ balls faced)", bjob), ("bowling (150+ balls)", wjob)):
        print(f"\n  {label}: regular seasons per job, per season (min / median / max)")
        names = sorted(set(jobs.values()))
        for j in names:
            v = [sum(1 for (p, s), jj in jobs.items() if s == y and jj == j)
                 for y in range(2008, 2027)]
            print(f"    {j:10} {min(v):>3} {st.median(v):>5} {max(v):>4}   total {sum(v)}")
        same = moved = 0
        trans: Counter = Counter()
        for (p, s), j in jobs.items():
            nxt = jobs.get((p, s + 1))
            if nxt:
                trans[(j, nxt)] += 1
                same += nxt == j
                moved += nxt != j
        print(f"    same job next season: {same} of {same + moved} "
              f"({same / max(same + moved, 1):.0%})")
        for j in names:
            row = {k[1]: v for k, v in trans.items() if k[0] == j}
            tot = sum(row.values())
            if tot:
                print(f"      from {j:10} -> " + ", ".join(
                    f"{k} {v / tot:.0%}" for k, v in sorted(row.items(), key=lambda x: -x[1])))
    # captains' death use: who bowls overs 16-20 of an innings
    print("\n  Death overs (16-20) in real innings: distinct bowlers used, and the share bowled")
    print("  by that season's designated death bowlers (40%+ of their own overs at the death)")
    dist: list[int] = []
    by_inn: dict = defaultdict(set)
    death_by: Counter = Counter()
    death_all = 0
    for b in balls:
        if b.over >= 15:
            by_inn[(b.match, b.inn)].add(b.bowler)
            death_all += 1
            if wjob.get((b.bowler, b.season)) == "death":
                death_by["death"] += 1
    dist = [len(v) for v in by_inn.values()]
    print(f"    distinct bowlers in the death overs: mean {st.fmean(dist):.2f}, "
          + ", ".join(f"{k}: {v / len(dist):.0%}" for k, v in sorted(Counter(dist).items())))
    print(f"    share of death balls bowled by death specialists: "
          f"{death_by['death'] / death_all:.0%}")
    return {"bat": bjob, "bowl": wjob}


# --------------------------------------------------------------------------- skills

def lean_pairs(rows: dict, min_each: int) -> tuple[list, list, float]:
    xs, ys, ns = [], [], []
    for (p, s), (lean, n) in rows.items():
        nxt = rows.get((p, s + 1))
        if nxt and n >= min_each and nxt[1] >= min_each:
            xs.append(lean)
            ys.append(nxt[0])
            ns.append((n + nxt[1]) / 2)
    return xs, ys, st.fmean(ns) if ns else 0.0


def skills(balls: list[Ball]) -> None:
    print("\n== SKILLS: does a season's lean repeat the next season? ==")
    print("A lean is runs a ball above par in one situation minus the player's own rate in the")
    print("rest. r: correlation, season t to season t+1, same player. k: pseudo-balls implied")
    print("at the thinner side's average sample. A skill is only worth shipping if r > ~0.2.")

    def lean_table(keyf, splitf, labels, min_each, who):
        acc: dict = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
        for b in balls:
            side = splitf(b)
            if side is None:
                continue
            a = acc[(keyf(b), b.season)][side]
            # a bowler's lean is runs SAVED: flip the sign so positive is good for both
            a[0] += (b.runs - b.par_runs) * (1 if who == "bat" else -1)
            a[1] += 1
        for lab in labels:
            rows = {}
            for k, d in acc.items():
                a, rest = d.get(lab), [v for kk, v in d.items() if kk != lab]
                if not a or not rest:
                    continue
                rn = sum(v[1] for v in rest)
                if a[1] < 1 or rn < 1:
                    continue
                rows[k] = (a[0] / a[1] - sum(v[0] for v in rest) / rn, min(a[1], rn))
            xs, ys, n = lean_pairs(rows, min_each)
            r = pearson(xs, ys)
            sd = st.pstdev(xs) if xs else 0
            print(f"  {who} {lab:12} n {len(xs):>4}  r {r:+.2f}  k {k_from(r, n):>6.0f}"
                  f"  spread of observed leans {sd * 6:.2f} runs an over")

    print("\n  batting, by phase")
    lean_table(lambda b: b.batter, lambda b: phase_of(b.over), PHASES, 60, "bat")
    print("\n  batting, against spin or pace")
    lean_table(lambda b: b.batter, lambda b: b.style, ("spin", "pace"), 60, "bat")
    print("\n  batting, setting (innings 1) or chasing (innings 2)")
    lean_table(lambda b: b.batter, lambda b: "chasing" if b.inn == 2 else "setting",
               ("chasing",), 60, "bat")
    print("\n  bowling (economy), by phase")
    lean_table(lambda b: b.bowler, lambda b: phase_of(b.over), PHASES, 60, "bowl")
    print("\n  bowling (economy), setting or defending")
    lean_table(lambda b: b.bowler, lambda b: "defending" if b.inn == 2 else "setting",
               ("defending",), 60, "bowl")


# --------------------------------------------------------------------------- anchors

def anchors(balls: list[Ball], jobs: dict | None) -> None:
    print("\n== ANCHORS: outside judges ==")
    with connect() as conn:
        pom = Counter((p, s) for p, s in conn.execute(
            "select player_of_match_id, season_year from matches "
            "where player_of_match_id is not null"))
    tot: dict = defaultdict(lambda: [0.0, 0.0, 0.0, 0, set()])
    for b in balls:
        t = tot[(b.batter, b.season)]
        t[0] += b.runs - b.par_runs
        t[1] += b.out
        t[2] += b.par_out
        t[3] += 1
        t[4].add(b.match)
    rows = []
    for k, (r, o, e, n, ms) in tot.items():
        if n < 150 or not e:
            continue
        if jobs and jobs["bat"].get(k) not in ("opener", "top", "middle"):
            continue
        scoring = r / n
        dm = (o + 0.5) / (e + 0.5)
        # balls a match and runs a match above par: what the season was worth, simply
        rows.append(dict(k=k, scoring=scoring, dm=dm, bpm=n / len(ms),
                         above=r / len(ms), pom=pom[k] / len(ms), m=len(ms), outs=o))
    # anchor-ness: low scoring and low dismissal, i.e. the trade-off axis
    zs = [x["scoring"] for x in rows]
    zd = [math.log(x["dm"]) for x in rows]
    ms_, ss_ = st.fmean(zs), st.pstdev(zs)
    md_, sd_ = st.fmean(zd), st.pstdev(zd)
    for x in rows:
        x["style"] = (x["scoring"] - ms_) / ss_ + (math.log(x["dm"]) - md_) / sd_
    rows.sort(key=lambda x: x["style"])
    third = len(rows) // 3
    groups = (("anchor", rows[:third]), ("balanced", rows[third:2 * third]),
              ("aggressor", rows[2 * third:]))
    print(f"  {len(rows)} top/middle-order batting seasons, 150+ balls, split into thirds by")
    print("  style (scoring rate plus log dismissal rate, each standardised).")
    print(f"  {'':10} {'SR+':>6} {'out x':>6} {'balls/m':>8} {'runs/m above par':>17} "
          f"{'PoM/m':>6}")
    for name, g in groups:
        print(f"  {name:10} {st.fmean(x['scoring'] for x in g) * 100:+6.1f} "
              f"{st.fmean(x['dm'] for x in g):6.2f} {st.fmean(x['bpm'] for x in g):8.1f} "
              f"{st.fmean(x['above'] for x in g):+17.2f} {st.fmean(x['pom'] for x in g):6.3f}")
    # Does Player of the Match, a human judge, reward survival beyond runs above par?
    # Least squares of PoM a match on (runs a match above par, log dismissal multiplier).
    X = [(1.0, x["above"], math.log(x["dm"])) for x in rows]
    Y = [x["pom"] for x in rows]
    beta = ols(X, Y)
    print(f"\n  Player of the Match a match = {beta[0]:.3f} + {beta[1]:.4f} x runs a match "
          f"above par {beta[2]:+.4f} x ln(dismissal multiplier)")
    print(f"  so halving the dismissal rate at equal runs above par is worth "
          f"{-beta[2] * math.log(2) / beta[1]:+.1f} runs a match to that judge")
    # Do long, slow innings win matches? Innings of 15+ balls, binned by runs above par;
    # within a bin, win rate for innings that faced more against fewer balls.
    print("\n  Batter innings of 15+ balls in decided matches: win rate by runs above par for")
    print("  his balls, slower-and-longer (more balls) against faster-and-shorter, same bin.")
    inn: dict = defaultdict(lambda: [0.0, 0, None, False, 0])
    for b in balls:
        if b.won is None:
            continue
        t = inn[(b.match, b.inn, b.batter)]
        t[0] += b.runs - b.par_runs
        t[1] += 1
        t[2] = b.won
        t[3] = t[3] or b.out
        t[4] = b.inn
    xs = [v for v in inn.values() if v[1] >= 15]
    bins = ((-99, -10), (-10, -3), (-3, 3), (3, 10), (10, 99))
    for lo, hi in bins:
        g = [v for v in xs if lo <= v[0] < hi]
        if len(g) < 100:
            continue
        mid = st.median(v[1] for v in g)
        longg = [v for v in g if v[1] > mid]
        short = [v for v in g if v[1] <= mid]
        print(f"    runs above par {lo:>4}..{hi:<4} n {len(g):>5}  balls median {mid:>4.0f}"
              f"  won: longer {st.fmean(v[2] for v in longg):.0%}"
              f"  shorter {st.fmean(v[2] for v in short):.0%}"
              f"  not out {st.fmean(not v[3] for v in g):.0%}")


def ols(X, Y):
    """Least squares by the normal equations, for three columns."""
    n = len(X[0])
    A = [[sum(x[i] * x[j] for x in X) for j in range(n)] for i in range(n)]
    b = [sum(x[i] * y for x, y in zip(X, Y)) for i in range(n)]
    for i in range(n):                         # Gauss-Jordan
        p = A[i][i]
        for j in range(n):
            A[i][j] /= p
        b[i] /= p
        for r in range(n):
            if r != i:
                f = A[r][i]
                for j in range(n):
                    A[r][j] -= f * A[i][j]
                b[r] -= f * b[i]
    return b


# --------------------------------------------------------------------------- partner

def partner(balls: list[Ball]) -> None:
    print("\n== PARTNER: does the batter at the other end change the striker's scoring? ==")
    print("Striker's runs above par, minus his own season mean (runs per 100 balls).")
    tot: dict = defaultdict(lambda: [0.0, 0.0, 0.0, 0])
    for b in balls:
        t = tot[(b.batter, b.season)]
        t[0] += b.runs - b.par_runs
        t[1] += b.out
        t[2] += b.par_out
        t[3] += 1
    mean = {k: v[0] / v[3] for k, v in tot.items() if v[3] >= 150}
    dm = {k: (v[1] + 0.5) / (v[2] + 0.5) for k, v in tot.items() if v[3] >= 150}
    cut = sorted(dm.values())
    lo, hi = cut[len(cut) // 3], cut[2 * len(cut) // 3]
    cells: dict = defaultdict(lambda: [0.0, 0])
    for b in balls:
        m = mean.get((b.batter, b.season))
        nd = dm.get((b.ns, b.season))
        if m is None or nd is None:
            continue
        kind = "low-dismissal" if nd < lo else ("high-dismissal" if nd > hi else "middle")
        setp = "partner set (15+)" if b.ns_faced >= 15 else "partner new (<15)"
        for key in (("partner type", kind), ("partner settled", setp),
                    (f"{phase_of(b.over)}: partner type", kind)):
            c = cells[key]
            c[0] += b.runs - b.par_runs - m
            c[1] += 1
    for k in sorted(cells):
        c = cells[k]
        print(f"  {k[0]:22} {k[1]:20} {c[0] / c[1] * 100:+6.1f}  n {c[1]}")


# --------------------------------------------------------------------------- career skills

BAT_SKILLS = ("powerplay", "middle", "death", "spin", "pace", "slow start", "fast finish")
BOWL_SKILLS = ("powerplay econ", "middle econ", "death econ",
               "powerplay wkts", "middle wkts", "death wkts", "partnership breaker")


def _bat_tags(b: Ball) -> list[str]:
    tags = [{"pp": "powerplay", "mid": "middle", "death": "death"}[phase_of(b.over)]]
    if b.style in ("spin", "pace"):
        tags.append(b.style)
    tags.append("slow start" if b.faced < 10 else "fast finish")
    return tags


def _bowl_tags(b: Ball) -> list[str]:
    ph = {"pp": "powerplay", "mid": "middle", "death": "death"}[phase_of(b.over)]
    tags = [ph + " econ", ph + " wkts"]
    if b.faced >= 15 and b.ns_faced >= 15:
        tags.append("partnership breaker")
    return tags


def career_skills(balls: list[Ball], names: dict[str, str]) -> None:
    """For every candidate skill, a CAREER lean: the player's rate in that situation minus
    his own rate elsewhere (so it is about where his game lies, not how good he is), and an
    ABSOLUTE edge: his rate there against par (so a specialist is someone who is very good
    there). Reliability is odd seasons against even seasons, pooled over the career."""
    print("\n== CAREER SKILLS: odd seasons against even seasons ==")
    print("lean: rate in the situation minus the player's own rate elsewhere.")
    print("edge: rate in the situation against par. Runs an over; wickets as % per ball.")
    print("true sd: the spread left once noise is removed, sqrt(r) x observed sd.")
    print("Bowling 'econ' is runs SAVED an over (positive is good).")

    def run(who, keyf, tagsf, skills, minb):
        # acc[player][half][tag] = [runs resid, wickets resid, balls]; half 2 = whole career
        acc: dict = defaultdict(lambda: [defaultdict(lambda: [0.0, 0.0, 0]) for _ in range(3)])
        for b in balls:
            r = (b.runs - b.par_runs) * (1 if who == "bat" else -1)
            w = (b.out and b.credited) - b.par_out if who == "bowl" else 0.0
            for half in (b.season % 2, 2):
                d = acc[keyf(b)][half]
                for t in tagsf(b) + ["*all"]:
                    c = d[t]
                    c[0] += r
                    c[1] += w
                    c[2] += 1
        out = {}
        for sk in skills:
            wk = sk.endswith("wkts") or sk == "partnership breaker"
            j = 1 if wk else 0
            unit = 100 if wk else 6
            res = {}
            for kind in ("lean", "edge"):
                xs, ys, career = [], [], {}
                for p, halves in acc.items():
                    vals = []
                    for h in range(3):
                        d = halves[h]
                        a, al = d.get(sk), d["*all"]
                        if not a or a[2] < minb or al[2] - a[2] < minb:
                            vals.append(None)
                            continue
                        rate = a[j] / a[2]
                        rest = (al[j] - a[j]) / (al[2] - a[2])
                        vals.append((rate - rest if kind == "lean" else rate) * unit)
                    if vals[0] is not None and vals[1] is not None:
                        xs.append(vals[0])
                        ys.append(vals[1])
                    if vals[2] is not None:
                        career[p] = vals[2]
                r = pearson(xs, ys)
                sd = st.pstdev(list(career.values())) if career else 0
                res[kind] = (r, len(xs), math.sqrt(max(r, 0)) * sd, career)
                print(f"  {who} {sk:20} {kind}: r {r:+.2f}  players {len(xs):>3}"
                      f"  true sd {math.sqrt(max(r, 0)) * sd:.2f}")
            out[sk] = res
        return out

    bat = run("bat", lambda b: b.batter, _bat_tags, BAT_SKILLS, 120)
    bowl = run("bowl", lambda b: b.bowler, _bowl_tags, BOWL_SKILLS, 120)

    def show(table, people, skills):
        for nm in people:
            pid = next((p for p, n in names.items() if n == nm), None)
            if pid is None:
                print(f"  {nm}: not found")
                continue
            parts = []
            for sk in skills:
                for kind in ("lean", "edge"):
                    career = table[sk][kind][3]
                    if pid in career:
                        v = career[pid]
                        pct = sum(x < v for x in career.values()) / len(career)
                        parts.append(f"{sk} {kind} {v:+.2f} ({pct:.0%})")
            print(f"  {nm}:\n    " + "\n    ".join(parts))

    print("\n  Named batters: career value and its percentile among qualified players")
    show(bat, ("Jos Buttler", "Ajinkya Rahane", "Andre Russell", "Virat Kohli",
               "AB de Villiers", "Virender Sehwag"), BAT_SKILLS)
    print("\n  Named bowlers")
    show(bowl, ("Dwayne Bravo", "Lasith Malinga", "Jasprit Bumrah", "Sunil Narine",
                "Rashid Khan", "Yuzvendra Chahal"), BOWL_SKILLS)


def main(argv: list[str]) -> None:
    want = set(argv) or {"settling", "shrink", "roles", "skills", "anchors", "partner",
                         "career"}
    balls = load()
    add_par(balls)
    print(f"{len(balls):,} balls faced in full-length innings")
    jobs = None
    if "settling" in want:
        settling(balls)
    if "shrink" in want:
        shrink(balls)
    if "roles" in want or "anchors" in want:
        jobs = roles(balls) if "roles" in want else None
    if "skills" in want:
        skills(balls)
    if "anchors" in want:
        anchors(balls, jobs)
    if "partner" in want:
        partner(balls)
    if "career" in want:
        with connect() as conn:
            names = dict(conn.execute(
                "select person_id, coalesce(display_name, primary_name) from people"))
        career_skills(balls, names)


if __name__ == "__main__":
    main(sys.argv[1:])
