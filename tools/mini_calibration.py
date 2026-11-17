"""Measure the 2027 mini auction's computer market against the real 2024 and 2026 minis.

    uv run python -m tools.mini_calibration [--seeds 20] [NAME=VALUE ...]

Plays the mini auction with no human `--seeds` times from the committed preset and prints
what the constants in `game.auction`'s mini section are tuned against [A297]: the share of
the purses spent, the top prices, how many players went for Rs 10 cr and Rs 5 cr or more,
the share of debutants bought, and the share bought by age. NAME=VALUE overrides a constant
for one run (e.g. `AGE_FACTOR=0.75`). Database-free.
"""
from __future__ import annotations

import argparse
import statistics
from collections import defaultdict

from game import auction as au
from tools.snapshot_mini import load_preset

AGE_BINS = ((26, "<26"), (30, "26-30"), (33, "30-33"), (36, "33-36"), (200, "36+"))


def measure(preset, seeds: int) -> dict:
    tot = sum(preset.purse.values())
    out = defaultdict(list)
    ages = defaultdict(lambda: [0, 0])
    for seed in range(seeds):
        a = au.run_mini_auction(preset, seed)
        paid = sorted((s.price for s in a.sales if s.winner is not None and s.round != "fill"), reverse=True)
        bought = {s.lot.card.person_id for s in a.sales if s.winner is not None}
        out["spent"].append(sum(s.price for s in a.sales if s.winner is not None) / tot)
        out["top"].append(paid[0] if paid else 0)
        out["over10"].append(sum(p >= 1000 for p in paid))
        out["over5"].append(sum(p >= 500 for p in paid))
        out["fills"].append(a.fills)
        out["stranded"].append(len(a.stranded))
        debut = [l for l in a.lots if l.card.person_id in preset.debutants]
        out["debut"].append(sum(l.card.person_id in bought for l in debut) / max(1, len(debut)))
        for lot in a.lots:
            age = preset.ages.get(lot.card.person_id)
            if age is not None:
                label = next(lbl for hi, lbl in AGE_BINS if age < hi)
                ages[label][0] += 1
                ages[label][1] += lot.card.person_id in bought
    res = {k: statistics.fmean(v) for k, v in out.items()}
    res["top_max"] = max(out["top"])
    res["ages"] = {lbl: ages[lbl][1] / ages[lbl][0] for _, lbl in AGE_BINS if ages[lbl][0]}
    return res


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("set", nargs="*")
    a = ap.parse_args(argv)
    for kv in a.set:
        name, value = kv.split("=", 1)
        setattr(au, name, eval(value))       # a developer's own constant, not user input
    r = measure(load_preset(), a.seeds)
    print(f"spent {r['spent']:.0%} of the purses; top Rs {r['top'] / 100:.1f} cr (max {r['top_max'] / 100:.1f}); "
          f"Rs 10 cr+ {r['over10']:.1f}, Rs 5 cr+ {r['over5']:.1f}; debutants bought {r['debut']:.0%}; "
          f"fills {r['fills']:.1f}, stranded {r['stranded']:.1f}")
    print("bought by age: " + ", ".join(f"{k} {v:.0%}" for k, v in r["ages"].items()))
    print("real minis:   spent ~87%; top Rs 24.75-25.2 cr; Rs 10 cr+ 5-6, Rs 5 cr+ 13-19; debutants 11-17%;")
    print("              by age <26 47%, 26-30 31%, 30-33 35%, 33-36 29%, 36+ 19%")


if __name__ == "__main__":
    main()
