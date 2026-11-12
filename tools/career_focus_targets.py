"""Measure each Build a Career focus's challenge target [A285].

Plays automatic careers (database-free) and counts, for every season and every focus the
career could have chosen, how many of its matches met that focus's challenge. A target is the
median of those counts over regular seasons (rounded, never under 2), so a season spent on a
focus meets its challenge about half the time before the focus has built anything.

    uv run python -m tools.career_focus_targets [--careers 20]
"""

from __future__ import annotations

import argparse
import random
import statistics as st
from collections import defaultdict
from multiprocessing import Pool

ROLE_FIT = {  # (kind, band, bowls, style) a created role reads as
    "opener": ("batter", "top", False, None), "middle": ("batter", "middle", False, None),
    "finisher": ("batter", "finisher", False, None), "keeper": ("batter", "middle", False, None),
    "pace": ("bowler", "finisher", True, "pace"), "spin": ("bowler", "finisher", True, "spin"),
    "allrounder": ("allrounder", "middle", True, "pace"),
}
_S: dict = {}


def _init() -> None:
    from game.puzzle_facts import players_from, read_document as rf
    from tools.snapshot_deck import deck_from, model_from, read_document
    from game import career_focus
    career_focus.FOCUS_ON = False      # the baseline: a season before any focus work
    career_focus.KEEP_LOG = True       # ...with each season's matches kept to be counted
    d = read_document()
    _S["deck"], _S["model"], _S["facts"] = deck_from(d), model_from(d), players_from(rf())


def _one(job: tuple[str, int]) -> list[tuple[str, int]]:
    from game import career as C, career_focus as F
    role, seed = job
    setup = C.Setup("T", role, random.Random(f"ft:{role}:{seed}").randint(C.FIRST_DEBUT, C.LAST_DEBUT))
    car, _ = C.auto_career(_S["deck"], _S["model"], seed, setup, "random", _S["facts"])
    kind, band, bowls, style = ROLE_FIT[role]
    keys = F.available(kind, band, bowls, style)
    out = []
    for r in car.seasons:
        if r.squad or r.matches < 7:
            continue
        log = getattr(r, "focus_hist", None) or r.focus_log
        for k in keys:
            out.append((k, sum(F.progress(k, log))))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--careers", type=int, default=20)
    a = ap.parse_args(argv)
    jobs = [(r, s) for r in ROLE_FIT for s in range(a.careers)]
    with Pool(8, initializer=_init) as p:
        rows = [x for part in p.map(_one, jobs) for x in part]
    by = defaultdict(list)
    for k, n in rows:
        by[k].append(n)
    for k, v in sorted(by.items()):
        q = st.quantiles(v, n=4)
        print(f"{k:11s} seasons {len(v):4d}  median {st.median(v):4.1f}  quartiles {q[0]:.0f}-{q[2]:.0f}"
              f"  met>=2 {sum(x >= 2 for x in v) / len(v):.0%}  >=3 {sum(x >= 3 for x in v) / len(v):.0%}"
              f"  >=4 {sum(x >= 4 for x in v) / len(v):.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
