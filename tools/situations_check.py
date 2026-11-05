"""Does the engine play situations the way the archive does? [A260] Measurement only.

    uv run python -m tools.situations_check            # all three, local database only
    uv run python -m tools.situations_check curve      # one

  curve  -- league-average sides, ball by ball: scoring and the chance of getting out by
            balls already faced, against the archive's own curve (`etl.settling`).
  leans  -- whole leagues of real historical sides: each batter's simulated phase lean
            against the lean his card carries, with the leans on and with them off.
  win    -- the A257 table, on both sides with one definition: of two batter innings of 15+
            balls with the same runs above 1.3 a ball, does the longer one win more often?
  chase  -- [A261] how often a chase succeeds, by first-innings total, archive against engine.

The engine itself is not changed to measure this: the curve is recorded by wrapping three
engine calls (`Model.state`, `faced_bucket`, `draw`) for the length of the run.
"""

from __future__ import annotations

import random
import statistics as st
import sys
from collections import defaultdict

import game.simulator as S
from etl import settling, skills
from etl.db import connect
from etl.feasibility import load_deck
from game.season import historical_sides, run_league
from web.daily import plain_deck

PHASE_OF = {"powerplay": "pp", "middle": "mid", "death": "death"}


def _phase(over: int) -> str:
    return skills.phase_of(over)


def curve(model: S.Model, innings: int = 4000, seed: int = 7) -> dict:
    """{(phase, bucket): [runs above par, outs, par outs, balls]} from simulated balls."""
    got: dict = defaultdict(lambda: [0.0, 0.0, 0.0, 0])
    now: dict = {}
    real_state, real_fb, real_draw = model.state, S.faced_bucket, S.draw

    def state(over, wickets):
        probs, values = real_state(over, wickets)
        now["over"], now["probs"], now["values"] = over, probs, values
        return probs, values

    def fb(balls):
        now["fb"] = real_fb(balls)
        return now["fb"]

    def draw(dist, rng):
        k = real_draw(dist, rng)
        if "fb" in now:
            p, v = now["probs"], now["values"]
            par = sum(q * x for q, x in zip(p[1:], v[1:]))
            g = got[(_phase(now["over"]), now["fb"])]
            g[0] += (v[k] if k else 0) - par
            g[1] += k == 0
            g[2] += p[0]
            g[3] += 1
            del now["fb"]
        return k

    model.state, S.faced_bucket, S.draw = state, fb, draw
    try:
        rng = random.Random(seed)
        bat = [S.Player(f"b{i}", 0.0, 0.0) for i in range(11)]
        bowl = [S.Player(f"w{i}", 0.0, 0.0) for i in range(11)]
        for _ in range(innings):
            S.play_innings(model, bat, bowl, rng)
    finally:
        model.state, S.faced_bucket, S.draw = real_state, real_fb, real_draw
    return got


def print_curve(model: S.Model, balls: list[skills.Ball]) -> None:
    print("\n== THE SETTLING CURVE: engine against archive, relative to a set batter (15+) ==")
    sim = curve(model)
    sc, hz = settling.scoring_curve(balls), settling.hazard_curve(balls)
    last = len(settling.FACED_BUCKETS) - 1
    for ph in ("pp", "mid", "death"):
        s_set = sim[(ph, last)]
        rs = s_set[0] / s_set[3]
        hs = s_set[1] / s_set[2]
        row = []
        for i, (lo, _hi) in enumerate(settling.FACED_BUCKETS[:-1]):
            g = sim.get((ph, i))
            if not g or g[3] < 500:
                continue
            row.append(f"{lo}: {(g[0] / g[3] - rs) * 100:+5.0f}/{(sc[(ph, i)] - sc[(ph, last)]) * 100:+5.0f}"
                       f" x{(g[1] / g[2]) / hs:.2f}/{hz[(ph, i)] / hz[(ph, last)]:.2f}")
        print(f"  {ph:5} " + "   ".join(row))
    print("  (each: runs per 100 balls engine/archive, then hazard ratio engine/archive)")


def _per_phase(results) -> dict:
    """{(person, season, phase): [runs, balls]} from the over log: each batter's figures at
    the end of every completed over, differenced. A batter out mid-over loses that over's
    balls; the archive side is not used here, so the omission only thins the sample."""
    out: dict = defaultdict(lambda: [0, 0])
    for r in results:
        for side, inn in ((r.home, r.home_innings), (r.away, r.away_innings)):
            if inn is None:
                continue
            seen: dict = {}
            year = {p.person_id: getattr(side, "year", None) for p in side.xi}
            for o in inn.over_log:
                for c in (o.striker, o.non_striker):
                    if c is None or not c.person_id:
                        continue
                    r0, b0 = seen.get(c.person_id, (0, 0))
                    d = out[(c.person_id, year.get(c.person_id), _phase(o.over))]
                    d[0] += c.runs - r0
                    d[1] += c.balls - b0
                    seen[c.person_id] = (c.runs, c.balls)
    return out


def print_leans(conn, model: S.Model, leagues: int = 40, seed: int = 11) -> None:
    print("\n== PHASE LEANS: simulated against the lean each card carries ==")
    deck = load_deck(conn)
    card = {(c.person_id, c.season_year): c for cs in deck.cards_by_fs.values() for c in cs}
    for label, d in (("leans on ", deck), ("leans off", plain_deck(deck))):
        rng = random.Random(seed)
        agg: dict = defaultdict(lambda: [0, 0])
        for _ in range(leagues):
            sides = historical_sides(d, rng, 10)
            for s in sides:
                s.year = s.xi[0].season_year if hasattr(s.xi[0], "season_year") else None
            season = run_league(model, sides, rng)
            for k, v in _per_phase(season.results).items():
                agg[k][0] += v[0]
                agg[k][1] += v[1]
        by: dict = defaultdict(dict)
        for (p, _y, ph), (r, b) in agg.items():
            by[p][ph] = (r, b)
        xs = {ph: ([], []) for ph in ("pp", "death")}
        for (p, y), c in card.items():
            ph_runs = by.get(p)
            if not ph_runs or c.phase_bat is None:
                continue
            tot_r = sum(v[0] for v in ph_runs.values())
            tot_b = sum(v[1] for v in ph_runs.values())
            if tot_b < 400:
                continue
            for ph, i in (("pp", 0), ("death", 2)):
                r, b = ph_runs.get(ph, (0, 0))
                if b >= 120:
                    xs[ph][0].append(c.phase_bat[i])
                    xs[ph][1].append(r / b - tot_r / tot_b)
        for ph, (want, simd) in xs.items():
            if len(want) > 10:
                r = st.correlation(want, simd)
                slope = st.linear_regression(want, simd).slope
                print(f"  {label} {ph:5} {len(want):>4} batters: correlation {r:+.2f}, "
                      f"slope {slope:+.2f} (1.00 = played exactly as measured)")


def print_win(conn, model: S.Model, balls: list[skills.Ball], leagues: int = 30,
              seed: int = 13) -> None:
    print("\n== LONGER INNINGS WIN MORE? batter innings of 15+ balls, same runs above 1.3 a ball ==")
    inn: dict = defaultdict(lambda: [0, 0, None])
    for b in balls:
        if b.won is None:
            continue
        t = inn[(b.match, b.inn, b.batter)]
        t[0] += b.runs
        t[1] += 1
        t[2] = b.won
    real = [(r - 1.3 * n, n, w) for r, n, w in inn.values() if n >= 15]
    deck = load_deck(conn)
    rng = random.Random(seed)
    sim = []
    for _ in range(leagues):
        for res in run_league(model, historical_sides(deck, rng, 10), rng).results:
            if res.winner is None:
                continue
            for side, i in ((res.home, res.home_innings), (res.away, res.away_innings)):
                for bc in i.batting:
                    if bc.balls >= 15:
                        sim.append((bc.runs - 1.3 * bc.balls, bc.balls, res.winner is side))
    for label, rows in (("archive", real), ("engine ", sim)):
        parts = []
        for lo, hi in ((-99, -10), (-10, -3), (-3, 3), (3, 10), (10, 99)):
            g = [x for x in rows if lo <= x[0] < hi]
            if len(g) < 100:
                continue
            mid = st.median(x[1] for x in g)
            long_ = [x[2] for x in g if x[1] > mid]
            short = [x[2] for x in g if x[1] <= mid]
            parts.append(f"{lo:>3}..{hi:<3} {st.fmean(long_):.0%}/{st.fmean(short):.0%}")
        print(f"  {label} (longer/shorter won): " + "   ".join(parts))


def print_chase(conn, model: S.Model, leagues: int = 30, seed: int = 17) -> None:
    print("\n== CHASES: how often the side batting second wins, by first-innings total ==")
    real = conn.execute("""
        select i1.runs, m.winner_fs_id = i2.fs
          from (select match_id, sum(runs_batter + runs_extras) runs from deliveries
                 where innings_no = 1 and not is_super_over and innings_scheduled_balls = 120
                 group by match_id) i1
          join (select match_id, min(batting_fs_id) fs from deliveries
                 where innings_no = 2 and not is_super_over and innings_scheduled_balls = 120
                 group by match_id) i2 using (match_id)
          join matches m using (match_id)
         where m.winner_fs_id is not null""").fetchall()
    deck = load_deck(conn)
    rng = random.Random(seed)
    sim = []
    for _ in range(leagues):
        for res in run_league(model, historical_sides(deck, rng, 10), rng).results:
            if res.winner is not None:
                sim.append((res.home_runs, res.winner is res.away))
    for label, rows in (("archive", real), ("engine ", sim)):
        parts = []
        for lo, hi in ((0, 140), (140, 160), (160, 180), (180, 200), (200, 999)):
            g = [w for r, w in rows if lo <= r < hi]
            if len(g) >= 30:
                parts.append(f"{lo}-{hi if hi < 999 else ''}: {st.fmean(g):.0%} (n{len(g)})")
        print(f"  {label} chase won: " + "   ".join(parts))


def main(argv: list[str]) -> int:
    want = set(argv) or {"curve", "leans", "win", "chase"}
    with connect() as conn:
        model = S.load_model(conn)
        balls = skills.load(conn)
        if "curve" in want:
            print_curve(model, balls)
        if "leans" in want:
            print_leans(conn, model)
        if "win" in want:
            print_win(conn, model, balls)
        if "chase" in want:
            print_chase(conn, model)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
