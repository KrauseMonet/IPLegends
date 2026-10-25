"""Calibrate Build a Career (A197/A198): simulated careers against real long careers.

    uv run python -m tools.career_calibration                       # 30 careers per role x policy
    uv run python -m tools.career_calibration --careers 60 --workers 8
    uv run python -m tools.career_calibration --roles opener,pace --policies random

Every simulated career lasts ten to twelve seasons, so the yardstick is the real players
who lasted that long (ten or more seasons in the deck). For each kind of player -- batter
(opener, middle, finisher, keeper), bowler (pace, spin), all-rounder -- it prints the
simulated distribution beside the real one:

- matches a season, and career totals in the kind's own stat (runs; wickets; runs + 20 x
  wickets for an all-rounder, the puzzles' prominence measure);
- how often a career reaches the all-time top 5 / 10 / 25 in that stat;
- the peak season card, and how far cards move from one season to the next;
- titles (team-driven, so only reported).

Database-free: the real side comes from the deck and puzzle-facts snapshots. Read-only.
The automatic players are `random` (careless) and `strength` (always the strongest offer,
an upper bound on someone chasing titles); a real person sits between the two.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import random
import statistics as st
import time
from collections import defaultdict

KINDS = {"opener": "batter", "middle": "batter", "finisher": "batter", "keeper": "batter",
         "pace": "bowler", "spin": "bowler", "allrounder": "allrounder"}

_STATE: dict = {}


def _init(overrides: dict | None = None) -> None:
    """Load the snapshots once per worker, and apply any `--set` overrides to game.career's
    declared constants, so a variant is tried without editing the code."""
    from game import career
    from game.puzzle_facts import players_from, read_document as read_facts
    from tools.snapshot_deck import deck_from, model_from, read_document

    doc = read_document()
    _STATE["deck"], _STATE["model"] = deck_from(doc), model_from(doc)
    _STATE["facts"] = players_from(read_facts())
    for name, value in (overrides or {}).items():
        if not hasattr(career, name):
            raise SystemExit(f"game.career has no constant {name}")
        setattr(career, name, value)


def _one(job: tuple[str, str, int]) -> dict:
    from game import career as C

    role, policy, seed = job
    rng = random.Random(f"calibrate:{role}:{policy}:{seed}")
    setup = C.Setup("Calibration", role, rng.randint(C.FIRST_DEBUT, C.LAST_DEBUT),
                    overseas=rng.random() < 0.3)
    career, _ = C.auto_career(_STATE["deck"], _STATE["model"], seed, setup, policy,
                              _STATE["facts"])
    s = career.summary
    cards = [r.display for r in career.seasons]
    return {
        "role": role, "policy": policy, "kind": KINDS[role], "seasons": s.seasons,
        "matches": s.matches, "runs": s.runs, "wickets": s.wickets, "titles": s.titles,
        "peak": s.peak_display, "yoy": [b - a for a, b in zip(cards, cards[1:])],
        "mean_card": st.fmean(cards), "missed": sum(r.missed for r in career.seasons),
        "regular": s.regular_seasons, "released": career.released,
        "bowled_every_season": all(r.bowl_balls > 0 for r in career.seasons),
        "start": career.start.display, "ceiling": career.ceiling.display,
        # [A224] where the Rating peaked, as a share of the career, and the potential earned
        "peak_pos": _peak_pos([r.rating for r in career.seasons]),
        "peak_rating": s.peak_rating,
        "pot_gain": round(getattr(career, "top", career.ceiling).display - career.ceiling.display),
        "shape": str(getattr(career, "shape", "")),
    }


def _peak_pos(xs: list[float]) -> float | None:
    """Where a career peaked, 0 its first season and 1 its last, on a three-season average
    so one season's luck does not decide it. None for a career too short to say."""
    if len(xs) < 3:
        return None
    sm = [st.fmean(xs[max(0, i - 1):i + 2]) for i in range(len(xs))]
    return max(range(len(sm)), key=lambda i: sm[i]) / (len(sm) - 1)


def stat(kind: str, runs: int, wickets: int) -> int:
    return runs if kind == "batter" else wickets if kind == "bowler" else runs + 20 * wickets


def real_starters() -> dict:
    """Real players who were REGULARS in one of their first two seasons, debut 2008-2014,
    however their careers went -- the right yardstick for a career that begins with you a
    regular (A198)."""
    from collections import Counter

    deck, facts = _STATE["deck"], {f.person_id: f for f in _STATE["facts"]}
    from game.career import _kind, _regular
    by_person: dict[str, list] = defaultdict(list)
    for cs in deck.cards_by_fs.values():
        for c in cs:
            by_person[c.person_id].append(c)
    out: dict[str, list] = defaultdict(list)
    for pid, cards in by_person.items():
        years = sorted({c.season_year for c in cards})
        first = years[0]
        if not 2008 <= first <= 2014 or not any(_regular(c) for c in cards
                                                if c.season_year <= first + 1):
            continue
        kind = Counter(_kind(c) for c in cards).most_common(1)[0][0]
        f = facts[pid]
        out[kind].append({"span": years[-1] - first + 1,
                          "total": stat(kind, f.runs, f.wickets)})
    return out


def real_side() -> tuple[dict, dict]:
    """(real long careers by kind, all-time stat lists by kind). A real career's kind is
    the kind of most of its seasons in the deck (A26's roles)."""
    from collections import Counter

    deck, facts = _STATE["deck"], _STATE["facts"]
    by_person: dict[str, list] = defaultdict(list)
    for cs in deck.cards_by_fs.values():
        for c in cs:
            by_person[c.person_id].append(c)
    kind_of = {"batter": "batter", "keeper": "batter", "bowler": "bowler",
               "allrounder": "allrounder"}
    real: dict[str, dict] = defaultdict(lambda: {"total": [], "peak": [], "yoy": [], "mean": [],
                                                 "peak_pos": []})
    for f in facts:
        cards = sorted(by_person.get(f.person_id, []), key=lambda c: c.season_year)
        if len({c.season_year for c in cards}) < 10:
            continue
        kind = Counter(kind_of.get(c.role, "batter") for c in cards).most_common(1)[0][0]
        ds = [c.display for c in cards if c.display is not None]
        real[kind]["total"].append(stat(kind, f.runs, f.wickets))
        real[kind]["peak"].append(max(ds))
        real[kind]["mean"].append(st.fmean(ds))
        real[kind]["yoy"] += [b - a for a, b in zip(ds, ds[1:])]
        best = {}
        for c in cards:
            if c.display is not None:
                best[c.season_year] = max(best.get(c.season_year, 0), c.display)
        real[kind]["peak_pos"].append(_peak_pos([best[y] for y in sorted(best)]))
    alltime = {k: sorted((stat(k, f.runs, f.wickets) for f in facts), reverse=True)
               for k in ("batter", "bowler", "allrounder")}
    return real, alltime


def _q(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))]


def report(rows: list[dict], real: dict, alltime: dict) -> None:
    for kind in ("batter", "bowler", "allrounder"):
        mine = [r for r in rows if r["kind"] == kind]
        if not mine:
            continue
        top = alltime[kind]
        unit = {"batter": "runs", "bowler": "wickets", "allrounder": "runs+20w"}[kind]
        print(f"\n{kind.upper()}  ({unit};  all-time 5th {top[4]}, 10th {top[9]}, 25th {top[24]})")
        starters = real_starters()[kind]
        if starters:
            tot = [x["total"] for x in starters]
            print(f"  {'real, began regular':22s} n={len(tot):3d}  "
                  f"total med {st.median(tot):6.0f} p90 {_q(tot, .9):6.0f}  "
                  f"top5 {sum(t >= top[4] for t in tot) / len(tot):4.0%} "
                  f"top10 {sum(t >= top[9] for t in tot) / len(tot):4.0%} "
                  f"top25 {sum(t >= top[24] for t in tot) / len(tot):4.0%}  "
                  f"span med {st.median(x['span'] for x in starters):4.1f}, "
                  f"lasted 10+ {sum(x['span'] >= 10 for x in starters) / len(tot):3.0%} "
                  f"15+ {sum(x['span'] >= 15 for x in starters) / len(tot):3.0%}")
        r = real.get(kind)
        if r and r["total"]:
            print(f"  {'real long careers':22s} n={len(r['total']):3d}  "
                  f"total med {st.median(r['total']):6.0f} p90 {_q(r['total'], .9):6.0f}  "
                  f"top5 {sum(t >= top[4] for t in r['total']) / len(r['total']):4.0%} "
                  f"top10 {sum(t >= top[9] for t in r['total']) / len(r['total']):4.0%} "
                  f"top25 {sum(t >= top[24] for t in r['total']) / len(r['total']):4.0%}  "
                  f"peak med {st.median(r['peak']):4.0f} p90 {_q(r['peak'], .9):3.0f}  "
                  f"swing SD {st.pstdev(r['yoy']):4.1f}  mean card {st.median(r['mean']):4.1f}  "
                  f"peak at {_q(r['peak_pos'], .25):.2f}/{st.median(r['peak_pos']):.2f}/{_q(r['peak_pos'], .75):.2f}")
        groups = defaultdict(list)
        for x in mine:
            groups[(x["role"], x["policy"])].append(x)
        for (role, policy), g in sorted(groups.items()):
            tot = [stat(kind, x["runs"], x["wickets"]) for x in g]
            yoy = [d for x in g for d in x["yoy"]]
            mps = sum(x["matches"] for x in g) / sum(x["seasons"] for x in g)
            print(f"  {role + ' / ' + policy:22s} n={len(g):3d}  "
                  f"total med {st.median(tot):6.0f} p90 {_q(tot, .9):6.0f}  "
                  f"top5 {sum(t >= top[4] for t in tot) / len(tot):4.0%} "
                  f"top10 {sum(t >= top[9] for t in tot) / len(tot):4.0%} "
                  f"top25 {sum(t >= top[24] for t in tot) / len(tot):4.0%}  "
                  f"peak med {st.median(x['peak'] for x in g):4.0f} "
                  f"p90 {_q([x['peak'] for x in g], .9):3.0f}  "
                  f"swing SD {st.pstdev(yoy):4.1f}  mean card {st.median(x['mean_card'] for x in g):4.1f}  "
                  f"seasons {st.median(x['seasons'] for x in g):4.1f} "
                  f"lasted 10+ {sum(x['seasons'] >= 10 for x in g) / len(g):3.0%} "
                  f"15+ {sum(x['seasons'] >= 15 for x in g) / len(g):3.0%} "
                  f"(regular {st.median(x['regular'] for x in g):4.1f}, "
                  f"released {sum(x['released'] for x in g) / len(g):3.0%})  "
                  f"{mps:4.1f} m/season (missed {sum(x['missed'] for x in g) / sum(x['seasons'] for x in g):.1f})  titles {st.mean(x['titles'] for x in g):.1f}"
                  + ("" if kind == "batter" else
                     f"  bowled every season {sum(x['bowled_every_season'] for x in g)}/{len(g)}")
                  + _shape_line(g))


def _shape_line(g: list[dict]) -> str:
    """[A224] Where Ratings peaked (quartiles of the share of the career), the peak Rating,
    and the potential earned."""
    pos = [x["peak_pos"] for x in g if x["peak_pos"] is not None]
    gain = [x["pot_gain"] for x in g]
    return (f"\n  {'':22s}        peak at {_q(pos, .25):.2f}/{st.median(pos):.2f}/{_q(pos, .75):.2f}  "
            f"peak Rating med {st.median(x['peak_rating'] for x in g):4.0f}  "
            f"potential +{st.median(gain):.0f} med, p90 +{_q(gain, .9):.0f}, "
            f"{sum(v > 0 for v in gain) / len(gain):3.0%} raised")


def main(argv: list[str] | None = None) -> int:
    from game.career import ROLES

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--careers", type=int, default=30)
    ap.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 2))
    ap.add_argument("--roles", default=",".join(ROLES))
    ap.add_argument("--policies", default="random,strength")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                    help="override a game.career constant, e.g. --set CEILING_MIN_DISPLAY=80")
    args = ap.parse_args(argv)
    jobs = [(role, policy, seed) for role in args.roles.split(",")
            for policy in args.policies.split(",") for seed in range(args.careers)]
    t0 = time.time()
    import ast

    overrides = {k: ast.literal_eval(v) for k, v in (x.split("=", 1) for x in args.set)}
    if overrides:
        print("overrides:", overrides)
    with mp.Pool(args.workers, initializer=_init, initargs=(overrides,)) as pool:
        rows = []
        for i, row in enumerate(pool.imap_unordered(_one, jobs, chunksize=2), 1):
            rows.append(row)
            if i % 20 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)} careers, {time.time() - t0:.0f}s", flush=True)
    _init()
    real, alltime = real_side()
    report(rows, real, alltime)
    print(f"\n{len(rows)} careers in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
