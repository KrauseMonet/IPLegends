"""The figures `articles/what-each-team-needs` quotes [A327]: each franchise's IPL 2026 by phase,
what it kept, and who on the 2027 list fills its gaps. Called by `tools.article_figures`.

Every number is ball by ball from the LOCAL archive (A173), counted by the project's rules: a
wide is not a ball faced (A22); a bowler is charged wides and no-balls, never byes or leg-byes
(A1), and credited only his own wickets (A36); "retired hurt" is not a dismissal; super overs
are left out. Phases are the archive's own (overs 1-6, 7-15, 16-20) in full-length innings only.
Retained squads, purses and the list come from the mini-auction preset (`data/mini2027.json.gz`).
"""
from __future__ import annotations

import re
from collections import defaultdict

YEAR = 2026
FORM_FROM = 2024                 # a candidate's record: IPL 2024-26
PHASES = {"powerplay": "pp", "middle": "mid", "death": "death"}
CAND_BAT_BALLS = 90              # balls in the situation before a candidate's rate is quoted
CAND_BOWL_BALLS = 90

ROWS_SQL = """
    select m.season_year, d.batting_fs_id, d.bowling_fs_id, d.phase, d.batter_id, d.bowler_id,
           d.runs_batter, d.runs_extras, d.extra_wides, d.extra_noballs, d.legal_ball,
           d.wicket_kind, d.player_out_id, d.credited_to_bowler, p.bowling_style
    from deliveries d join matches m using (match_id) join people p on p.person_id = d.bowler_id
    where m.season_year >= %s and not d.is_super_over and d.innings_scheduled_balls = 120
      and d.phase is not null"""

# The teams' gaps the article names, and the candidates it quotes, by display name.
DEPARTED = ("Ajinkya Rahane", "Travis Head", "Mohammed Shami", "Arshdeep Singh", "Allah Ghazanfar",
            "Pathum Nissanka", "Rishabh Pant", "Mitchell Starc", "Kuldeep Yadav", "T Natarajan",
            "Deepak Chahar", "Trent Boult", "Khaleel Ahmed", "Hardik Pandya")
CANDIDATES = ("Pathum Nissanka", "Jonny Bairstow", "Jake Fraser-McGurk", "Abishek Porel",
              "Daryl Mitchell", "Nitish Rana", "Nehal Wadhera", "Mahipal Lomror", "Shahrukh Khan",
              "Trent Boult", "Avesh Khan", "Deepak Chahar", "Maheesh Theekshana", "Matheesha Pathirana")
KKR_MIDDLE = ("Rinku Singh", "Rovman Powell", "Ramandeep Singh", "Manish Pandey")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def ordinal(k: int) -> str:
    return f"{k}{'th' if 10 <= k % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(k % 10, 'th')}"


def money(lakh: int) -> str:
    return f"₹{lakh / 100:.2f}".rstrip("0").rstrip(".") + " cr"


def compute(conn, figs: dict, f1, f2, pct, n, check_claims) -> None:
    from etl.franchise_map import canonical
    from game import auction as au
    from game import auction_hub as hub
    from tools.snapshot_mini import load_preset

    short_of_name = {nm: s for s, nm in au.FRANCHISES}
    fs_short = {fs: short_of_name[canonical(nm)] for fs, nm in conn.execute(
        "select franchise_season_id, display_name from franchise_seasons where season_year = %s", (YEAR,))}
    pid = {nm: p for p, nm in conn.execute(
        "select person_id, coalesce(display_name, primary_name) from people")}
    preset = load_preset()
    kept = {s: {c.person_id for c in cs} for s, cs in preset.squads.items()}

    bat = defaultdict(lambda: [0, 0, 0])      # (team, ph) -> runs incl. extras, legal balls, wickets
    bowl = defaultdict(lambda: [0, 0, 0])     # (team, ph) -> conceded incl. extras, legal balls, wickets
    pruns = defaultdict(lambda: [0, 0])       # (team, ph) -> runs off the bat, of them by players kept
    povers = defaultdict(lambda: [0, 0, 0, 0])  # (team, ph) -> legal balls, kept; wickets, kept
    vspin = defaultdict(lambda: [0, 0, 0])    # (team, style) middle overs -> runs off bat, balls faced, outs
    league_vspin = [0, 0]                     # 2026 middle overs v spin: runs, balls faced
    pl = defaultdict(lambda: defaultdict(int))  # (pid, team) 2026 -> stats
    cand = defaultdict(lambda: defaultdict(int))  # pid 2024-26 -> stats
    vspin_late = [0, 0]                       # 2024-26 overs 7-20 v spin, league: runs, balls faced

    for (yr, bfs, wfs, phase, b, w, rb, rx, wd, nb, legal, wk, out, cred, style) in conn.execute(ROWS_SQL, (FORM_FROM,)):
        ph = PHASES[phase]
        out_b = out == b and wk != "retired hurt"
        faced = wd == 0
        # 2024-26: candidates and the league's rate against spin in overs 7-20
        c = cand[b]
        if faced:
            c[f"{ph}_bf"] += 1
            c[f"{ph}_r"] += rb
            if ph != "pp" and style == "spin":
                c["late_spin_bf"] += 1
                c["late_spin_r"] += rb
                c["late_spin_out"] += out_b
                vspin_late[0] += rb
                vspin_late[1] += 1
        cw = cand[w]
        cw[f"{ph}_bb"] += legal
        cw[f"{ph}_conc"] += rb + wd + nb
        cw[f"{ph}_w"] += bool(cred)
        if yr != YEAR:
            continue
        T, O = fs_short[bfs], fs_short[wfs]
        t = bat[(T, ph)]
        t[0] += rb + rx
        t[1] += legal
        t[2] += wk is not None and wk != "retired hurt"
        o = bowl[(O, ph)]
        o[0] += rb + rx
        o[1] += legal
        o[2] += wk is not None and wk != "retired hurt"
        r = pruns[(T, ph)]
        r[0] += rb
        r[1] += rb if b in kept[T] else 0
        v = povers[(O, ph)]
        v[0] += legal
        v[1] += legal if w in kept[O] else 0
        v[2] += bool(cred)
        v[3] += bool(cred) if w in kept[O] else 0
        if ph == "mid" and faced and style in ("pace", "spin"):
            s = vspin[(T, style)]
            s[0] += rb
            s[1] += 1
            s[2] += out_b
            if style == "spin":
                league_vspin[0] += rb
                league_vspin[1] += 1
        p = pl[(b, T)]
        if faced:
            p["bf"] += 1
            p["r"] += rb
        pw = pl[(w, O)]
        pw["bb"] += legal
        pw["w"] += bool(cred)
        pw[f"{ph}_bb"] += legal

    teams = sorted(kept)
    rate = {
        "bat_rr": lambda t, ph: 6 * bat[(t, ph)][0] / bat[(t, ph)][1],
        "bat_bpw": lambda t, ph: bat[(t, ph)][1] / max(1, bat[(t, ph)][2]),
        "bowl_econ": lambda t, ph: 6 * bowl[(t, ph)][0] / bowl[(t, ph)][1],
        "bowl_bpw": lambda t, ph: bowl[(t, ph)][1] / max(1, bowl[(t, ph)][2]),
    }
    higher_is_better = {"bat_rr": True, "bat_bpw": True, "bowl_econ": False, "bowl_bpw": False}
    rank: dict = {}
    for key, fn in rate.items():
        for ph in PHASES.values():
            order = sorted(teams, key=lambda t: fn(t, ph), reverse=higher_is_better[key])
            for i, t in enumerate(order, 1):
                rank[(t, key, ph)] = i
    spin_sr = {t: 100 * vspin[(t, "spin")][0] / vspin[(t, "spin")][1] for t in teams}
    spin_order = sorted(teams, key=lambda t: -spin_sr[t])

    for t in teams:
        k = t.lower()
        for ph in PHASES.values():
            for key, fn in rate.items():
                figs[f"need_{k}_{key}_{ph}"] = (f2 if key.endswith(("rr", "econ")) else f1)(fn(t, ph))
                figs[f"need_{k}_{key}_{ph}_rank"] = ordinal(rank[(t, key, ph)])
            r, v = pruns[(t, ph)], povers[(t, ph)]
            figs[f"need_{k}_kept_runs_{ph}"] = pct(r[1] / r[0])
            figs[f"need_{k}_kept_overs_{ph}"] = pct(v[1] / v[0])
            figs[f"need_{k}_kept_wkts_{ph}"] = pct(v[3] / v[2])
        figs[f"need_{k}_spin_sr"] = f"{spin_sr[t]:.0f}"
        figs[f"need_{k}_spin_sr_rank"] = ordinal(spin_order.index(t) + 1)
        figs[f"need_{k}_pace_sr"] = f"{100 * vspin[(t, 'pace')][0] / vspin[(t, 'pace')][1]:.0f}"
        figs[f"need_{k}_purse"] = money(preset.purse[t])
        figs[f"need_{k}_places"] = str(preset.squad_size[t] - len(preset.squads[t]))
        os_kept = sum(c.overseas is True for c in preset.squads[t])
        figs[f"need_{k}_os_places"] = str(preset.overseas_cap[t] - os_kept)
        ages = [preset.ages[c.person_id] for c in preset.squads[t] if c.person_id in preset.ages]
        figs[f"need_{k}_age"] = f1(sum(ages) / len(ages))
    views = {v.short: v for v in hub.teams(preset)}
    for t in teams:
        for m in views[t].meters:
            figs[f"need_{t.lower()}_have_{m.what}"] = str(m.have)
            figs[f"need_{t.lower()}_want_{m.what}"] = str(m.want)
    for ph in PHASES.values():
        r = sum(bat[(t, ph)][0] for t in teams)
        b = sum(bat[(t, ph)][1] for t in teams)
        figs[f"need_league_rr_{ph}"] = f2(6 * r / b)
    # where each finished, from the archive's own table (wins, points, how far it went)
    from tools.build_history import Record, load
    rec = Record(load(conn))
    for row in rec.table(YEAR):
        t = fs_short[row["fs"]].lower()
        figs[f"need_{t}_wins"] = str(row["w"])
        figs[f"need_{t}_points"] = str(row["pts"])
        figs[f"need_{t}_finish"] = {"Champions": "champions", "Runners-up": "runners-up",
                                    "Playoffs": "out in the playoffs",
                                    "League stage": "out in the league stage"}[rec.finish(row["fs"])]
    figs["need_league_spin_sr"] = f"{100 * league_vspin[0] / league_vspin[1]:.0f}"
    figs["need_league_late_spin_sr"] = f"{100 * vspin_late[0] / vspin_late[1]:.0f}"
    figs["need_total_purse"] = money(sum(preset.purse.values()))
    figs["need_total_places"] = str(sum(views[t].places for t in teams))
    ages = {t: float(figs[f"need_{t.lower()}_age"]) for t in teams}

    # the players who left, by what they did for the team that let them go
    for nm in DEPARTED:
        p = pid[nm]
        team = max((tm for (q, tm) in pl if q == p), key=lambda tm: pl[(p, tm)]["bf"] + pl[(p, tm)]["bb"])
        s, k = pl[(p, team)], slug(nm)
        figs[f"need_p_{k}_runs"] = n(s["r"])
        figs[f"need_p_{k}_balls"] = n(s["bf"])
        figs[f"need_p_{k}_overs"] = n(s["bb"] // 6)
        figs[f"need_p_{k}_wkts"] = n(s["w"])
        figs[f"need_p_{k}_pp_overs"] = n(s["pp_bb"] // 6)
        figs[f"need_p_{k}_death_overs"] = n(s["death_bb"] // 6)
        figs[f"need_p_{k}_team"] = team

    # the candidates' IPL 2024-26
    for nm in CANDIDATES:
        c, k = cand[pid[nm]], slug(nm)
        for ph in PHASES.values():
            if c[f"{ph}_bf"] >= CAND_BAT_BALLS // (3 if ph == "death" else 1):
                figs[f"need_c_{k}_{ph}_sr"] = f"{100 * c[f'{ph}_r'] / c[f'{ph}_bf']:.0f}"
                figs[f"need_c_{k}_{ph}_bf"] = n(c[f"{ph}_bf"])
            if c[f"{ph}_bb"] >= CAND_BOWL_BALLS // (3 / 2 if ph == "death" else 1):
                figs[f"need_c_{k}_{ph}_econ"] = f2(6 * c[f"{ph}_conc"] / c[f"{ph}_bb"])
                figs[f"need_c_{k}_{ph}_wkts"] = n(c[f"{ph}_w"])
        if c["late_spin_bf"] >= 60:
            figs[f"need_c_{k}_late_spin_sr"] = f"{100 * c['late_spin_r'] / c['late_spin_bf']:.0f}"
            figs[f"need_c_{k}_late_spin_bf"] = n(c["late_spin_bf"])
    group = [cand[pid[nm]] for nm in KKR_MIDDLE]
    gr, gb = sum(c["late_spin_r"] for c in group), sum(c["late_spin_bf"] for c in group)
    figs["need_kkr_middle_vspin_sr"] = f"{100 * gr / gb:.0f}"
    figs["need_kkr_middle_vspin_bf"] = n(gb)

    listed = {c.person_id for c, _b, _cap in preset.lots}
    retained = {p for ps in kept.values() for p in ps}
    check_claims({
        "Lucknow were the slowest middle-overs batting side of 2026":
            rank[("LSG", "bat_rr", "mid")] == 10,
        "Lucknow were the slowest against spin in the middle overs": spin_order[-1] == "LSG",
        "Kolkata's middle-overs bowling was the cheapest of 2026": rank[("KKR", "bowl_econ", "mid")] == 1,
        "Delhi's powerplay batting was the slowest of 2026": rank[("DC", "bat_rr", "pp")] == 10,
        "Delhi's middle-overs bowling was the most expensive of 2026": rank[("DC", "bowl_econ", "mid")] == 10,
        "Hyderabad bowled the best death overs and the dearest powerplay":
            rank[("SRH", "bowl_econ", "death")] == 1 and rank[("SRH", "bowl_econ", "pp")] == 10,
        "Rajasthan scored fastest in the powerplay": rank[("RR", "bat_rr", "pp")] == 1,
        "Punjab scored fastest at the death and bowled the dearest death overs":
            rank[("PBKS", "bat_rr", "death")] == 1 and rank[("PBKS", "bowl_econ", "death")] == 10,
        "Gujarat scored slowest at the death": rank[("GT", "bat_rr", "death")] == 10,
        "Gujarat took middle-overs wickets more often than anyone": rank[("GT", "bowl_bpw", "mid")] == 1,
        "Hyderabad scored fastest in the middle overs": rank[("SRH", "bat_rr", "mid")] == 1,
        "Kolkata took death wickets quicker than anyone": rank[("KKR", "bowl_bpw", "death")] == 1,
        "Chennai's middle-overs bowling was second best": rank[("CSK", "bowl_econ", "mid")] == 2,
        "Bengaluru ranked in the top four in every phase with the bat":
            all(rank[("RCB", "bat_rr", ph)] <= 4 for ph in PHASES.values()),
        "Bengaluru's lowest bowling rank is taking middle-overs wickets":
            max(((rank[("RCB", k, ph)], (k, ph)) for k in ("bowl_econ", "bowl_bpw") for ph in PHASES.values()))[1]
            == ("bowl_bpw", "mid"),
        "Bengaluru and Gujarat kept nearly but not quite all of their 2026 runs":
            all(0.9 <= pruns[(t, ph)][1] / pruns[(t, ph)][0] < 1 for t in ("RCB",) for ph in ("pp",))
            and any(pruns[("GT", ph)][1] < pruns[("GT", ph)][0] for ph in PHASES.values()),
        "Chennai lost powerplay wickets fastest": rank[("CSK", "bat_bpw", "pp")] == 10,
        "Bengaluru have the oldest retained squad": max(ages, key=ages.get) == "RCB",
        "Kolkata's middle order is slower against spin than the league":
            gr / gb < vspin_late[0] / vspin_late[1],
        "every candidate the article names is on the list": all(pid[nm] in listed for nm in CANDIDATES),
        "no departure the article names is still in his old squad":
            all(pid[nm] not in kept[figs[f"need_p_{slug(nm)}_team"]] for nm in DEPARTED),
        "Head and Starc are off the list (opted out)":
            pid["Travis Head"] not in listed and pid["Mitchell Starc"] not in listed,
        "Rahane is on no list and in no squad (retired)":
            pid["Ajinkya Rahane"] not in listed and pid["Ajinkya Rahane"] not in retained,
    })
