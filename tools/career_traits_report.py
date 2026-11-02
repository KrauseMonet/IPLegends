"""What the A245 traits do to a career, measured on paired careers.

    uv run python -m tools.career_traits_report                   # 20 careers per role
    uv run python -m tools.career_traits_report --careers 40 --roles opener,pace

For each role and seed, the same player (same setup, same seed) is played four ways:

- `base`   the careless player with the traits switched off (`TRAITS_ON = False`): the
           career as it played before A245, choices and all, minus what they make of you
- `random` the careless player, traits on
- `virtue` every choice the one that builds a better player (nerve, work, lead, standing)
- `vice`   every choice the one that undoes it

and prints peak Rating, mean Rating, seasons, titles and the career's main stat, as medians
and as the paired difference from `base`. The guardrail A245 was ratified on: `random` within
about two points of `base`, `virtue` and `vice` about four to six either side at the peak.
Read-only and database-free."""

from __future__ import annotations

import argparse
import multiprocessing as mp
import random
import statistics as st
import time
from collections import defaultdict

from tools.career_calibration import KINDS, _init, _STATE

WAYS = ("base", "random", "virtue", "vice")


def _one(job: tuple[str, int, str]) -> dict:
    from game import career as C

    role, seed, way = job
    rng = random.Random(f"calibrate:{role}:random:{seed}")
    setup = C.Setup("Calibration", role, rng.randint(C.FIRST_DEBUT, C.LAST_DEBUT),
                    overseas=rng.random() < 0.3)
    C.TRAITS_ON = way != "base"
    C._SEASON_CACHE.clear()
    career, _ = C.auto_career(_STATE["deck"], _STATE["model"], seed, setup,
                              "random" if way == "base" else way, _STATE["facts"])
    s = career.summary
    ratings = [r.rating for r in career.seasons]
    kind = KINDS[role]
    stat = {"batter": s.runs, "bowler": s.wickets, "allrounder": s.runs + 20 * s.wickets}[kind]
    traits = career.checkpoint.traits if career.checkpoint else {}
    return {"role": role, "seed": seed, "way": way, "peak": max(ratings),
            "mean": st.fmean(ratings), "seasons": s.seasons, "titles": s.titles,
            "stat": stat, "traits": {k: round(v, 2) for k, v in traits.items()}}


def report(rows: list[dict]) -> None:
    by = {(r["role"], r["seed"], r["way"]): r for r in rows}
    roles = sorted({r["role"] for r in rows})
    print(f"\n{'':22s} {'peak':>6s} {'d.peak':>7s} {'mean':>6s} {'d.mean':>7s} "
          f"{'seasons':>8s} {'titles':>7s} {'stat':>7s}   traits (median)")
    for role in roles + ["ALL"]:
        mine = [r for r in rows if role == "ALL" or r["role"] == role]
        seeds = sorted({r["seed"] for r in mine if (r["role"], r["seed"], "base") in by
                        or role == "ALL"})
        for way in WAYS:
            g = [r for r in mine if r["way"] == way]
            if not g:
                continue
            dp = [r["peak"] - by[(r["role"], r["seed"], "base")]["peak"] for r in g
                  if (r["role"], r["seed"], "base") in by]
            dm = [r["mean"] - by[(r["role"], r["seed"], "base")]["mean"] for r in g
                  if (r["role"], r["seed"], "base") in by]
            tr = defaultdict(list)
            for r in g:
                for k, v in r["traits"].items():
                    tr[k].append(v)
            trs = " ".join(f"{k} {st.median(v):+.1f}" for k, v in sorted(tr.items()))
            print(f"  {role + ' / ' + way:20s} {st.median(r['peak'] for r in g):6.1f} "
                  f"{st.fmean(dp):+7.2f} {st.median(r['mean'] for r in g):6.1f} "
                  f"{st.fmean(dm):+7.2f} {st.median(r['seasons'] for r in g):8.1f} "
                  f"{st.fmean(r['titles'] for r in g):7.2f} {st.median(r['stat'] for r in g):7.0f}"
                  f"   {trs}")
        _ = seeds


def main(argv: list[str] | None = None) -> int:
    from game.career import ROLES

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--careers", type=int, default=20)
    ap.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 2))
    ap.add_argument("--roles", default=",".join(ROLES))
    args = ap.parse_args(argv)
    jobs = [(role, seed, way) for role in args.roles.split(",")
            for seed in range(args.careers) for way in WAYS]
    t0 = time.time()
    with mp.Pool(args.workers, initializer=_init) as pool:
        rows = []
        for i, row in enumerate(pool.imap_unordered(_one, jobs, chunksize=2), 1):
            rows.append(row)
            if i % 40 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)} careers, {time.time() - t0:.0f}s", flush=True)
    report(rows)
    print(f"\n{len(rows)} careers in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
