"""Freeze the cut-offs a Build a Career season's skills are judged against [A278].

The engine replays every real season -- each year's real sides, everybody against everybody
home and away -- REPLAYS times, every ball recorded with its par (`game.simulator.ball_log`).
Every simulated player-season is then valued exactly as `game.career_skills.judge` values a
career season (his career so far as the prior, the archive's own spreads), and each skill's
cut is the value the archive's share of qualified seasons clears (`etl.skills`' SEASON_TOP,
ALONE_TOP and ELITE_CAREER_TOP): a simulated season earns a skill about as often as a real
one does, judged against seasons the same engine played. Database-free, deterministic.

    uv run python -m tools.snapshot_career_skills            # write data/career_skill_cuts.json.gz
    uv run python -m tools.snapshot_career_skills --check    # exit 1 if it would change
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import random
import statistics as st
import sys
from collections import defaultdict

from etl import skills as S
from game import career_skills as CS
from game import skills as GS
from game import season as SEA
from game.simulator import ball_log
from tools.snapshot_deck import deck_from, model_from, read_document

REPLAYS = (1, 2, 3, 4)


def replay(deck, model, seed: int) -> dict:
    """{(person, disc): {year: summary}} for one replay of all nineteen seasons."""
    from game.__main__ import opposition_twelve, viable
    rng = random.Random(f"career-skills:{seed}")
    by_year = defaultdict(list)
    for fs, cards in deck.cards_by_fs.items():
        if cards:
            by_year[cards[0].season_year].append(fs)
    out: dict = defaultdict(dict)
    for year in sorted(by_year):
        sides = []
        for fs in sorted(by_year[year]):
            squad = list(deck.cards_by_fs[fs])
            if len(squad) < 11 or not viable(squad):
                continue
            xi, imp = opposition_twelve(squad)
            if len(xi) != 11:
                continue
            c = squad[0]
            sides.append(SEA.Side(name=f"{c.franchise} {year}", short=str(fs), xi=xi,
                                  impact=imp, franchise=c.franchise, year=year))
        col = CS.Collector()
        with ball_log(col):
            for h in sides:
                for a in sides:
                    if h is not a:
                        SEA.play(model, h, a, rng)
        for (pid, disc), tags in col.acc.items():
            out[(pid, disc)][year] = tags
    return out


def build(deck, model) -> dict:
    sk = GS.document()
    spreads = {k: (v["career_sd"] ** 2, v["season_sd"] ** 2) for k, v in sk["metrics"].items()}
    careers = {}
    for seed in REPLAYS:
        for (pid, disc), seasons in replay(deck, model, seed).items():
            careers[(f"{pid}#{seed}", disc)] = seasons
    pooled = {}
    for disc in ("bat", "bowl"):
        n = r = q = 0.0
        for (p, d), seasons in careers.items():
            if d == disc:
                for tags in seasons.values():
                    a = tags.get("all")
                    if a:
                        n, r, q = n + a.n, r + a.resid, q + a.sq
        pooled[disc] = q / n - (r / n) ** 2
    S.POOLED_VAR.update(pooled)
    # every simulated player-season valued the career's way: the seasons before as the prior
    values: dict = defaultdict(dict)          # metric -> (player, year) -> metric_values
    for (p, disc), seasons in careers.items():
        years = sorted(seasons)
        for i, y in enumerate(years):
            this = seasons[y]
            if this.get("all") is None:
                continue
            before = S._add(seasons[x] for x in years[:i]) if i else {}
            career = S._add(seasons[x] for x in years[: i + 1])
            for m in S.METRICS:
                if m.disc != disc:
                    continue
                got = CS.metric_values(this, before, career, m, spreads)
                if got is not None:
                    values[m.key][(p, y)] = got
    whole = {}                                  # whole careers, for the elite career cut
    for (p, disc), seasons in careers.items():
        whole[(p, disc)] = S._add(seasons.values())
    cuts, played_sd = {}, {}
    for skill in CS.CAREER_SKILLS:
        arch = sk["cuts"].get(skill.key)
        if not arch:
            continue
        rows, floor = _series(skill, values, spreads)
        q = [r for r in rows if r["balls"] >= S.SEASON_FLOOR[floor]]
        cut = {}
        if "season" in arch:
            f = "alone" if skill.tier == 3 else "season"
            cut["season"] = S._top([r[f] for r in q if r[f] is not None], S.SEASON_TOP[skill.tier])
        if "career" in arch:
            pool = []
            for (p, disc), acc in whole.items():
                if skill.metric in ("style", "complete") or disc != S.METRIC_BY_KEY[skill.metric].disc:
                    continue
                v = S.value(S.METRIC_BY_KEY[skill.metric], acc)
                if v and v[2] >= S.POOL_FLOOR[floor]:
                    pool.append(skill.sign * S._shrink(v[0], v[1], 0.0, spreads[skill.metric][0]))
            cut["career"] = S._top(pool, S.ELITE_CAREER_TOP)
        if "played" in arch:
            pv = [r["played"] for r in q if r["played"] is not None]
            cut["played"] = S._top(pv, S.ALONE_TOP[skill.tier])
            played_sd[skill.key] = round(st.pstdev(pv), 6) if len(pv) > 1 else 0.0
        cut = {k: round(v, 6) for k, v in cut.items() if v is not None}
        if skill.tier == 3 and not {"season", "career"} <= cut.keys():
            continue
        if cut:
            cuts[skill.key] = cut
    return {"format": CS.FORMAT, "replays": list(REPLAYS),
            "pooled_var": {k: round(v, 8) for k, v in pooled.items()},
            "spreads": {k: [round(a, 10), round(b, 10)] for k, (a, b) in sorted(spreads.items())},
            "cuts": dict(sorted(cuts.items())), "played_sd": dict(sorted(played_sd.items()))}


def _series(skill, values, spreads):
    """[{season, alone, played, balls}] signed so higher is more of the skill, and the floor."""
    if skill.metric in ("style", "complete"):
        a, b = values["scoring"], values["survival"]
        rows = []
        for k in a.keys() & b.keys():
            r = {f: CS._combo(skill.metric, a[k], b[k], spreads, f) for f in ("season", "played")}
            r = {f: None if v is None else skill.sign * v for f, v in r.items()}
            r["alone"], r["balls"] = r["season"], a[k]["balls"]
            rows.append(r)
        return rows, "all"
    rows = []
    for v in values[skill.metric].values():
        rows.append({f: None if v[f] is None else skill.sign * v[f]
                     for f in ("season", "alone", "played")} | {"balls": v["balls"]})
    return rows, S.METRIC_BY_KEY[skill.metric].floor


def _dump(doc: dict) -> bytes:
    return gzip.compress(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode(), mtime=0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    doc = read_document()
    if doc is None:
        print("no deck snapshot", file=sys.stderr)
        return 1
    data = _dump(build(deck_from(doc), model_from(doc)))
    if args.check:
        try:
            same = CS.SNAPSHOT.read_bytes() == data
        except OSError:
            same = False
        if not same:
            print("the career skill cut-offs disagree with the engine:\n"
                  "run: uv run python -m tools.snapshot_career_skills", file=sys.stderr)
            return 1
        print("career skill cut-offs current")
        return 0
    CS.SNAPSHOT.write_bytes(data)
    print(f"wrote {CS.SNAPSHOT} ({len(data):,} bytes), {len(json.loads(gzip.decompress(data))['cuts'])} skills")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
