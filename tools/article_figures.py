"""Every number the articles at /articles quote, recomputed from the archive and the deck.

    uv run python -m tools.article_figures            # recompute, write the figures file and
                                                      # fill the tables in each article
    uv run python -m tools.article_figures --check    # exit 1 if anything is stale

WHY. The articles exist because AdSense judged the site "low-value content": a reader should
find something here they cannot find on Wikipedia or ESPNcricinfo, and what only this site has
is its own data -- the 70-99 ratings, and measurements made on the ball-by-ball archive. The
prose is written by hand; the NUMBERS in it are not. Each one is a `<span data-fig="key">` in
the article, and `tests/test_articles.py` fails if its text is not exactly the value this tool
produces, so an article cannot quote a figure the data does not give (A22's lesson: check the
figure being claimed, not a neighbouring one).

Tables are written by this tool into `<!-- fig:key -->...<!-- /fig:key -->` markers.

Reads the LOCAL database only (A173), plus the committed deck snapshot; about ten seconds.
The chase table is the one the engine plays (`model_inputs.intent`, A261), read from the
snapshot, which check 26 holds to the database.
"""

from __future__ import annotations

import gzip
import html
import json
import math
import pathlib
import re
import statistics as st
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "data" / "deck_snapshot.json.gz"
FIGURES = ROOT / "tools" / "article_figures.json"
ARTICLES = ROOT / "web" / "static" / "articles"

SQUAD = 12              # a squad's strength is the mean card of its best twelve
DEATH_POOL_BALLS = 480  # 80 overs at the death to be compared with the others
SETTLED_FROM = 15       # balls faced: "set"
OWN_MEAN_BALLS = 150    # a batter-season needs this many balls to be its own baseline
LEAN_MIN_BALLS = 60     # each side of a lean, as in tools.specialisation_report (A257)
PHASES = ("pp", "mid", "death")
PHASE_NAME = {"pp": "powerplay", "mid": "middle overs", "death": "death overs"}


def n(x: int) -> str:
    return f"{x:,}"


def f1(x: float) -> str:
    return f"{x:.1f}"


def f2(x: float) -> str:
    return f"{x:.2f}"


def pct(x: float) -> str:
    return f"{round(x * 100)}%"


def signed(x: float) -> str:
    return f"{x:+.0f}".replace("-", "−")


def phase_of(over: int) -> str:
    return "pp" if over < 6 else "mid" if over < 15 else "death"


def pearson(xs: list[float], ys: list[float]) -> float:
    mx, my = st.fmean(xs), st.fmean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / math.sqrt(sxx * syy)


def check_claims(claims: dict[str, bool]) -> None:
    """A sentence an article states as fact but no single figure carries (an ordering, a
    comparison). If the data stops supporting one, regeneration stops with its wording."""
    broken = [c for c, ok in claims.items() if not ok]
    if broken:
        raise SystemExit("an article's claim no longer holds -- rewrite the sentence:\n  "
                         + "\n  ".join(broken))


def table(head: list[str], rows: list[list[str]], numeric: set[int]) -> str:
    th = "".join(f"<th{' class=n' if i in numeric else ''}>{h}</th>" for i, h in enumerate(head))
    body = "".join("<tr>" + "".join(f"<td{' class=n' if i in numeric else ''}>{c}</td>"
                                    for i, c in enumerate(r)) + "</tr>" for r in rows)
    return (f'<div class="hx-scroll"><table class="hx-table"><thead><tr>{th}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


# --------------------------------------------------------------------------- the deck

def deck_cards() -> list[dict]:
    doc = json.load(gzip.open(SNAPSHOT))["deck"]["cards_by_fs"]
    return [c for cards in doc.values() for c in cards]


def best_seasons(cards: list[dict], figs: dict, tables: dict) -> None:
    figs["deck_cards"] = n(len(cards))
    figs["deck_median"] = str(round(st.median(c["display"] for c in cards)))
    figs["deck_90_share"] = pct(sum(c["display"] >= 90 for c in cards) / len(cards))
    top = [c for c in cards if c["display"] == 99]
    figs["deck_99"] = n(len(top))
    figs["deck_99_bowlers"] = n(sum(c["role"] == "bowler" for c in top))
    figs["deck_99_allrounders"] = n(sum(c["role"] == "allrounder" for c in top))
    figs["deck_99_years"] = n(len({c["season_year"] for c in top}))

    def line(c: dict) -> str:
        bits = []
        if c["bat_balls"] and c["role"] != "bowler":
            bits.append(f"{c['bat_runs']} runs off {c['bat_balls']} balls")
        if c["bowl_balls"] and (c["role"] in ("bowler", "allrounder") or c["bowl_wickets"] >= 5):
            w = c["bowl_wickets"]
            bits.append(f"{w} wicket{'s' if w != 1 else ''} at "
                        f"{c['bowl_runs'] / c['bowl_balls'] * 6:.2f} an over")
        return ", ".join(bits)

    times: dict = defaultdict(list)
    for c in top:
        times[c["name"]].append(c["season_year"])
    three = sorted(k for k, v in times.items() if len(v) == 3)
    run3 = [k for k, v in times.items() if any(y + 1 in v and y + 2 in v for y in v)]
    check_claims({
        "Gayle, de Villiers, Warner and Narine appear three times each, nobody more":
            three == ["AB de Villiers", "Chris Gayle", "David Warner", "Sunil Narine"]
            and max(map(len, times.values())) == 3,
        "only Gayle appears three years running": run3 == ["Chris Gayle"],
        "Narine's three are 2012, 2013 and 2024": sorted(times["Sunil Narine"]) == [2012, 2013, 2024],
        "2026 is the newest year on the list": max(c["season_year"] for c in top) == 2026,
        "Kohli 2016 is the most runs in any season":
            max(cards, key=lambda c: c["bat_runs"] or 0)["name"] == "Virat Kohli",
    })
    role = {"batter": "Batter", "bowler": "Bowler", "allrounder": "All-rounder", "keeper": "Keeper"}
    rows = sorted(top, key=lambda c: (c["season_year"], c["name"]))
    tables["best_99"] = table(["Season", "Player", "Team", "Role", "Season figures"],
                              [[str(c["season_year"]), html.escape(c["name"]),
                                html.escape(c["franchise"]), role[c["role"]], line(c)]
                               for c in rows], {0})
    for key, name, year in (("kohli16", "Virat Kohli", 2016), ("malinga11", "Lasith Malinga", 2011),
                            ("bumrah20", "Jasprit Bumrah", 2020), ("narine24", "Sunil Narine", 2024),
                            ("bhuvi26", "Bhuvneshwar Kumar", 2026), ("gill23", "Shubman Gill", 2023),
                            ("buttler22", "Jos Buttler", 2022), ("vs26", "Vaibhav Sooryavanshi", 2026),
                            ("gayle11", "Chris Gayle", 2011), ("kohli22", "Virat Kohli", 2022),
                            ("maxwell14", "Glenn Maxwell", 2014)):
        c = next(c for c in cards if c["name"] == name and c["season_year"] == year)
        figs[f"{key}_card"] = str(c["display"])
        figs[f"{key}_runs"] = n(c["bat_runs"] or 0)
        figs[f"{key}_balls"] = n(c["bat_balls"] or 0)
        figs[f"{key}_wkts"] = n(c["bowl_wickets"] or 0)


def squads(cards: list[dict], champions: dict[int, int], figs: dict, tables: dict) -> None:
    by_fs: dict = defaultdict(list)
    for c in cards:
        by_fs[c["fs_id"]].append(c)
    strength = {}
    for fs, cs in by_fs.items():
        best = sorted((c["display"] for c in cs), reverse=True)[:SQUAD]
        strength[fs] = (st.fmean(best), cs[0]["franchise"], cs[0]["season_year"])
    by_year: dict = defaultdict(list)
    for fs, (m, name, year) in strength.items():
        by_year[year].append((m, fs, name))
    rows, ranks = [], []
    for year in sorted(by_year):
        order = sorted(by_year[year], reverse=True)
        champ = champions[year]
        rank = next(i + 1 for i, (_, fs, _) in enumerate(order) if fs == champ)
        ranks.append(rank)
        best = order[0]
        rows.append([str(year), html.escape(strength[champ][1]), f1(strength[champ][0]),
                     f"{rank} of {len(order)}", html.escape(best[2]) if rank != 1 else "—",
                     f1(best[0]) if rank != 1 else ""])
    tables["squads"] = table(["Season", "Champion", "Squad", "Rank", "Strongest squad", "Squad"],
                             rows, {0, 2, 3, 5})
    figs["squad_seasons"] = n(len(ranks))
    figs["squad_best_won"] = n(sum(r == 1 for r in ranks))
    figs["squad_second_won"] = n(sum(r == 2 for r in ranks))
    figs["squad_worst_champion"] = n(max(ranks))
    figs["squad_top_two_won"] = n(sum(r <= 2 for r in ranks))
    best_names = [r[4] for r in rows if r[4] != "—"]
    check_claims({
        "the strongest squad lost the title in most seasons": sum(r == 1 for r in ranks) < len(ranks) / 2,
        "RCB were the strongest squad without winning more than once":
            sum(n.startswith("Royal Challengers") for n in best_names) >= 2,
        "RCB's first title came after those seasons":
            min(y for y, fs in champions.items()
                if strength[fs][1].startswith("Royal Challengers")) >= 2025,
    })
    m, fs, name, year = max((v[0], k, v[1], v[2]) for k, v in strength.items())
    figs["squad_strongest"] = f"{html.escape(name)} {year}"
    figs["squad_strongest_mean"] = f1(m)
    figs["squad_strongest_champion"] = "yes" if champions[year] == fs else "no"


# --------------------------------------------------------------------------- the archive

def champions_of(conn) -> dict[int, int]:
    """The winner of each season's last match, by date: the final (A253 checked all 18 then
    on the record against the champions it names)."""
    rows = conn.execute("""
        select distinct on (season_year) season_year, winner_fs_id from matches
        order by season_year, match_date desc, match_id desc""").fetchall()
    return {int(y): fs for y, fs in rows}


def settling(balls, figs: dict) -> None:
    """The same batter against himself: runs above par less his season's own average, by the
    balls he had faced -- as tools.specialisation_report's settling section (A257/A260)."""
    tot: dict = defaultdict(lambda: [0.0, 0])
    for b in balls:
        t = tot[(b.batter, b.season)]
        t[0] += b.runs - b.par_runs
        t[1] += 1
    own = {k: v[0] / v[1] for k, v in tot.items() if v[1] >= OWN_MEAN_BALLS}
    cell: dict = defaultdict(lambda: [0.0, 0])
    raw: dict = defaultdict(lambda: [0, 0])
    for b in balls:
        ph = phase_of(b.over)
        f = min(b.faced, SETTLED_FROM)
        r = raw[(ph, f)]
        r[0] += b.out
        r[1] += 1
        m = own.get((b.batter, b.season))
        if m is None:
            continue
        c = cell[(ph, f)]
        c[0] += b.runs - b.par_runs - m
        c[1] += 1
    for ph in PHASES:
        v = cell[(ph, 0)]
        figs[f"settle_{ph}_first"] = signed(v[0] / v[1] * 100)
        set_ = cell[(ph, SETTLED_FROM)]
        set_rate = set_[0] / set_[1]
        figs[f"settle_{ph}_set"] = signed(set_rate * 100)
        cost = sum(set_rate - cell[(ph, f)][0] / cell[(ph, f)][1] for f in range(SETTLED_FROM))
        figs[f"settle_{ph}_cost"] = f2(cost)
        first, setted = raw[(ph, 0)], raw[(ph, SETTLED_FROM)]
        figs[f"settle_{ph}_out_first"] = f1(first[0] / first[1] * 100)
        figs[f"settle_{ph}_out_set"] = f1(setted[0] / setted[1] * 100)
        figs[f"settle_{ph}_out_ratio"] = f"{first[0] / first[1] / (setted[0] / setted[1]):.2f}"
        catch = next(f for f in range(SETTLED_FROM + 1)
                     if cell[(ph, f)][1] and cell[(ph, f)][0] / cell[(ph, f)][1] >= 0)
        figs[f"settle_{ph}_catch"] = str(catch + 1)
    out = {ph: (raw[(ph, 0)][0] / raw[(ph, 0)][1], raw[(ph, SETTLED_FROM)][0]
                / raw[(ph, SETTLED_FROM)][1]) for ph in PHASES}
    cost = {ph: float(figs[f"settle_{ph}_cost"]) for ph in PHASES}
    check_claims({
        "new batters are out less often than set ones in the powerplay and middle overs":
            out["pp"][0] < out["pp"][1] and out["mid"][0] < out["mid"][1],
        "at the death a new batter is out about as often as a set one (within 10%)":
            0.9 < out["death"][0] / out["death"][1] < 1.1,
        "getting in costs more than three times as much at the death as in the powerplay":
            cost["death"] > 3 * cost["pp"],
        "a set batter at the death scores above his own average": float(
            figs["settle_death_set"].replace("−", "-")) > 0,
    })
    figs["settle_balls"] = n(len(balls))
    figs["settle_balls_floor"] = n(OWN_MEAN_BALLS)


def leans(balls, figs: dict) -> None:
    """Does a batter's lean in one situation carry from a season to the next? A lean is runs
    a ball above par in the situation minus his rate in the rest (tools.specialisation_report)."""
    def repeat(split) -> tuple[float, int]:
        acc: dict = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
        for b in balls:
            a = acc[(b.batter, b.season)][split(b)]
            a[0] += b.runs - b.par_runs
            a[1] += 1
        rows = {}
        for k, d in acc.items():
            a = d.get(True)
            rest = d.get(False)
            if a and rest:
                rows[k] = (a[0] / a[1] - rest[0] / rest[1], min(a[1], rest[1]))
        xs, ys = [], []
        for (p, s), (lean, size) in rows.items():
            nxt = rows.get((p, s + 1))
            if nxt and size >= LEAN_MIN_BALLS and nxt[1] >= LEAN_MIN_BALLS:
                xs.append(lean)
                ys.append(nxt[0])
        return pearson(xs, ys), len(xs)

    r, size = repeat(lambda b: b.inn == 2)
    figs["lean_chase_r"] = f2(r).replace("-", "−")
    figs["lean_chase_n"] = n(size)
    r, size = repeat(lambda b: phase_of(b.over) == "death")
    figs["lean_death_r"] = f2(r)
    figs["lean_death_n"] = n(size)
    r_chase = float(figs["lean_chase_r"].replace("−", "-"))
    r_death = float(figs["lean_death_r"])
    r, size = repeat(lambda b: b.style == "spin")
    figs["lean_spin_r"] = f2(r)
    check_claims({
        "a chasing lean does not repeat (|r| < 0.1)": abs(r_chase) < 0.1,
        "death hitting and playing spin do repeat (r > 0.2)": r_death > 0.2 and r > 0.2,
    })


def intent(figs: dict, tables: dict) -> None:
    doc = json.load(gzip.open(SNAPSHOT))["model_inputs"]["intent"]
    cells = {(ph, bk): (r, m, size) for ph, bk, r, m, size in doc["cells"]}
    label = ("40+ ahead", "20–40 ahead", "5–20 ahead", "level", "5–20 behind", "20–40 behind",
             "40+ behind")
    rows = []
    for bk, lab in enumerate(label):
        row = [lab]
        for ph in PHASES:
            r, m, size = cells[(ph, bk)]
            row += [signed(r * 100), f"{m:.2f}×"] if size >= 1000 else ["—", ""]
        rows.append(row)
    tables["chase"] = table(["Against the rate", "Powerplay runs", "out", "Middle runs", "out",
                             "Death runs", "out"], rows, {1, 2, 3, 4, 5, 6})
    for key, ph, bk in (("mid_behind", "mid", 6), ("mid_ahead", "mid", 1),
                        ("death_ahead", "death", 1), ("pp_behind", "pp", 6)):
        r, m, _ = cells[(ph, bk)]
        figs[f"chase_{key}_runs"] = signed(r * 100)
        figs[f"chase_{key}_out"] = f"{m:.2f}"
        figs[f"chase_{key}_out_pct"] = signed((m - 1) * 100) + "%"
    mb, ma, da, pb = cells[("mid", 6)], cells[("mid", 1)], cells[("death", 1)], cells[("pp", 6)]
    check_claims({
        "behind the rate, middle-overs batters score more and get out more": mb[0] > 0 and mb[1] > 1,
        "ahead of it they score less and get out less": ma[0] < 0 and ma[1] < 1,
        "at the death the holding back is stronger": da[0] < ma[0] and da[1] < ma[1],
        "the powerplay response is gentler": pb[0] < mb[0] and pb[1] < mb[1],
    })


DEATH_SQL = """
    select coalesce(p.display_name, p.primary_name), d.bowler_id,
           sum(case when d.extra_wides = 0 and d.extra_noballs = 0 then 1 else 0 end),
           sum(d.runs_batter + d.extra_wides + d.extra_noballs),
           sum(case when d.credited_to_bowler then 1 else 0 end)
    from deliveries d join people p on p.person_id = d.bowler_id
    where d.phase = 'death' and not d.is_super_over and d.innings_scheduled_balls = 120
    group by 1, 2"""


def death(conn, figs: dict, tables: dict) -> None:
    rows = conn.execute(DEATH_SQL).fetchall()
    balls = sum(r[2] for r in rows)
    figs["death_league_bpw"] = f1(balls / sum(r[4] for r in rows))
    figs["death_league_econ"] = f2(sum(r[3] for r in rows) / balls * 6)
    pool = [(name, b, runs, w) for name, _, b, runs, w in rows if b >= DEATH_POOL_BALLS]
    figs["death_pool"] = n(len(pool))
    figs["death_pool_overs"] = n(DEATH_POOL_BALLS // 6)
    econ = {p[0]: p[2] / p[1] * 6 for p in pool}
    bpw = {p[0]: p[1] / p[3] for p in pool}
    most = max(rows, key=lambda r: r[4])
    figs["death_most_name"] = html.escape(most[0])
    figs["death_most_wkts"] = n(most[4])

    def better(d: dict, name: str) -> str:
        return pct(sum(v > d[name] for v in d.values()) / len(d))

    for key, name in (("malinga", "Lasith Malinga"), ("bumrah", "Jasprit Bumrah"),
                      ("narine", "Sunil Narine"), ("bravo", "Dwayne Bravo"),
                      ("bhuvi", "Bhuvneshwar Kumar"), ("harshal", "Harshal Patel")):
        p = next(p for p in pool if p[0] == name)
        figs[f"death_{key}_wkts"] = n(p[3])
        figs[f"death_{key}_overs"] = n(p[1] // 6)
        figs[f"death_{key}_econ"] = f2(econ[name])
        figs[f"death_{key}_bpw"] = f1(bpw[name])
        figs[f"death_{key}_econ_beats"] = better(econ, name)
        figs[f"death_{key}_bpw_beats"] = better(bpw, name)
    rows_e = sorted(pool, key=lambda p: p[2] / p[1])[:10]
    rows_w = sorted(pool, key=lambda p: p[1] / p[3])[:10]
    tables["death_econ"] = table(["Bowler", "Death overs", "Economy", "Balls a wicket"],
                                 [[html.escape(p[0]), n(p[1] // 6), f2(econ[p[0]]), f1(bpw[p[0]])]
                                  for p in rows_e], {1, 2, 3})
    tables["death_wkts"] = table(["Bowler", "Death overs", "Balls a wicket", "Wickets", "Economy"],
                                 [[html.escape(p[0]), n(p[1] // 6), f1(bpw[p[0]]), n(p[3]),
                                   f2(econ[p[0]])] for p in rows_w], {1, 2, 3, 4})
    # Orderings the death-overs article states in words; a refresh that breaks one fails here
    # rather than leaving a sentence that is no longer true.
    by_econ = [p[0] for p in sorted(pool, key=lambda p: p[2] / p[1])]
    by_wkts = [p[0] for p in sorted(pool, key=lambda p: -p[3])]
    by_overs = [p[0] for p in sorted(pool, key=lambda p: -p[1])]
    claims = {
        "Narine, Malinga, Bumrah are the three cheapest":
            by_econ[:3] == ["Sunil Narine", "Lasith Malinga", "Jasprit Bumrah"],
        "Bhuvneshwar has the most death wickets, Bravo the second most":
            by_wkts[:2] == ["Bhuvneshwar Kumar", "Dwayne Bravo"],
        "Bhuvneshwar, Bumrah, Bravo bowled the most death overs, in that order":
            by_overs[:3] == ["Bhuvneshwar Kumar", "Jasprit Bumrah", "Dwayne Bravo"],
        "Malinga bowled the most death overs of those in the best quarter for both":
            True,   # checked below once `both` is known
    }
    both = [p[0] for p in pool if sum(v < econ[p[0]] for v in econ.values()) < len(pool) / 4
            and sum(v < bpw[p[0]] for v in bpw.values()) < len(pool) / 4]
    figs["death_both_top_quarter"] = ", ".join(html.escape(b) for b in sorted(both))
    claims["Malinga bowled the most death overs of those in the best quarter for both"] = (
        max(both, key=lambda b: next(p[1] for p in pool if p[0] == b)) == "Lasith Malinga")
    league_bpw = balls / sum(r[4] for r in rows)
    league_econ = sum(r[3] for r in rows) / balls * 6
    claims["Bumrah strikes less often than the death average"] = bpw["Jasprit Bumrah"] > league_bpw
    claims["Harshal concedes more than the death average"] = econ["Harshal Patel"] > league_econ
    check_claims(claims)


SPIN_SQL = """
    select d.over_no, m.season_year, p.bowling_style,
           sum(case when d.extra_wides = 0 and d.extra_noballs = 0 then 1 else 0 end),
           sum(d.runs_batter + d.extra_wides + d.extra_noballs),
           sum(case when d.credited_to_bowler then 1 else 0 end)
    from deliveries d join people p on p.person_id = d.bowler_id
         join matches m on m.match_id = d.match_id
    where not d.is_super_over and d.innings_scheduled_balls = 120
    group by 1, 2, 3"""


def spin(conn, figs: dict, tables: dict) -> None:
    rows = conn.execute(SPIN_SQL).fetchall()
    over: dict = defaultdict(lambda: defaultdict(float))
    phase: dict = defaultdict(lambda: [0, 0, 0])
    season: dict = defaultdict(lambda: defaultdict(float))
    unknown = 0
    for ov, year, style, balls, runs, wk in rows:
        if style not in ("pace", "spin"):
            unknown += balls
            continue
        over[ov][style] += balls
        season[year][style] += balls
        p = phase[(phase_of(ov), style)]
        p[0] += balls
        p[1] += runs
        p[2] += wk
    figs["spin_unknown_balls"] = n(unknown)
    share = {ov: v["spin"] / (v["spin"] + v["pace"]) for ov, v in over.items()}
    tables["spin_by_over"] = table(["Over", "Spin", "Pace"],
                                   [[str(ov + 1), pct(share[ov]), pct(1 - share[ov])]
                                    for ov in sorted(share)], {0, 1, 2})
    figs["spin_over1"] = pct(share[0])
    figs["spin_over20"] = pct(share[19])
    peak = max(share, key=share.get)
    figs["spin_peak_over"] = str(peak + 1)
    figs["spin_peak_share"] = pct(share[peak])
    rows_p = []
    for ph in PHASES:
        s, p = phase[(ph, "spin")], phase[(ph, "pace")]
        figs[f"spin_{ph}_share"] = pct(s[0] / (s[0] + p[0]))
        figs[f"spin_{ph}_econ"] = f2(s[1] / s[0] * 6)
        figs[f"pace_{ph}_econ"] = f2(p[1] / p[0] * 6)
        figs[f"spin_{ph}_bpw"] = f1(s[0] / s[2])
        figs[f"pace_{ph}_bpw"] = f1(p[0] / p[2])
        rows_p.append([PHASE_NAME[ph].capitalize(), pct(s[0] / (s[0] + p[0])),
                       f2(s[1] / s[0] * 6), f2(p[1] / p[0] * 6), f1(s[0] / s[2]), f1(p[0] / p[2])])
    tables["spin_by_phase"] = table(["Phase", "Spin share", "Spin economy", "Pace economy",
                                     "Spin balls a wicket", "Pace balls a wicket"],
                                    rows_p, {1, 2, 3, 4, 5})
    sp = {ph: phase[(ph, "spin")] for ph in PHASES}
    pa = {ph: phase[(ph, "pace")] for ph in PHASES}
    check_claims({
        "every ball has a known bowling style": unknown == 0,
        "spin bowls most of every over from the 7th to the 12th":
            all(share[ov] > 0.5 for ov in range(6, 12)),
        "spin's share rises from the 6th over to the 7th by at least 25 points":
            share[6] - share[5] >= 0.25,
        "spin's peak over is in the middle overs": 6 <= max(share, key=share.get) <= 14,
        "spin is cheaper than pace in the middle overs and at the death":
            sp["mid"][1] / sp["mid"][0] < pa["mid"][1] / pa["mid"][0]
            and sp["death"][1] / sp["death"][0] < pa["death"][1] / pa["death"][0],
        "in the middle overs spin and pace take wickets about as often (within 10%)":
            abs(sp["mid"][0] / sp["mid"][2] / (pa["mid"][0] / pa["mid"][2]) - 1) < 0.1,
    })
    yshare = {y: v["spin"] / (v["spin"] + v["pace"]) for y, v in season.items()}
    first, last = min(yshare), max(yshare)
    figs["spin_first_year"], figs["spin_last_year"] = str(first), str(last)
    figs["spin_first_share"], figs["spin_last_share"] = pct(yshare[first]), pct(yshare[last])
    hi = max(yshare, key=yshare.get)
    lo = min(yshare, key=yshare.get)
    figs["spin_high_year"], figs["spin_high_share"] = str(hi), pct(yshare[hi])
    figs["spin_low_year"], figs["spin_low_share"] = str(lo), pct(yshare[lo])
    check_claims({
        "spin's share peaked in the middle years and has fallen since, still above 2008":
            first < hi < last and yshare[last] < yshare[hi] and yshare[last] > yshare[first],
    })


# --------------------------------------------------------------------------- run

def compute() -> dict:
    from etl.db import connect
    from etl.skills import load

    figs: dict = {}
    tables: dict = {}
    cards = deck_cards()
    with connect() as conn:
        champs = champions_of(conn)
        balls = load(conn)
        death(conn, figs, tables)
        spin(conn, figs, tables)
    best_seasons(cards, figs, tables)
    squads(cards, champs, figs, tables)
    settling(balls, figs)
    leans(balls, figs)
    intent(figs, tables)
    return {"figures": dict(sorted(figs.items())), "tables": dict(sorted(tables.items()))}


FIG_TABLE = re.compile(r"(<!-- fig:([a-z0-9_]+) -->)(.*?)(<!-- /fig:\2 -->)", re.S)


def fill(text: str, tables: dict) -> str:
    return FIG_TABLE.sub(lambda m: m.group(1) + tables[m.group(2)] + m.group(4), text)


def main(argv: list[str]) -> int:
    doc = compute()
    want = json.dumps(doc, indent=1, ensure_ascii=False) + "\n"
    pages = {p: p.read_text() for p in sorted(ARTICLES.glob("*.html"))}
    filled = {p: fill(t, doc["tables"]) for p, t in pages.items()}
    if "--check" in argv:
        stale = [] if FIGURES.exists() and FIGURES.read_text() == want else [FIGURES.name]
        stale += [p.name for p in pages if pages[p] != filled[p]]
        if stale:
            print("stale:", ", ".join(stale), "-- run `uv run python -m tools.article_figures`")
            return 1
        print(f"article figures current: {len(doc['figures'])} figures, {len(doc['tables'])} tables")
        return 0
    FIGURES.write_text(want)
    for p, t in filled.items():
        if t != pages[p]:
            p.write_text(t)
    print(f"wrote {len(doc['figures'])} figures and {len(doc['tables'])} tables")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
