"""The Almanack's compare page: any two IPL players side by side [A320].

    /almanack/compare?a=virat-kohli&b=rohit-sharma

One static file carries every player's figures (`compare.json`, named by its own hash like the
head-to-head file, A316, since `/static` is cached for a year), and `web/static/compare.js`
draws the comparison from it: the two players' cards in their club colours, every figure as a
pair of bars pointing away from the middle with the better one lit, the phase and pace-or-spin
edges against league par, the two careers season by season on one chart, and the balls between
the two where they met. Nothing is asked of a server, so the page is as fast as a static file.

The rows -- which figures, in what order, and whether higher or lower is better -- are defined
here once and travel in the file, so the page cannot judge a row differently from this module.
The page itself is written with one comparison in plain HTML (`DEFAULT`), so a reader without
the script, or a crawler, gets a real comparison and not an empty box.

The counting rules are the player pages' own (`tools.build_almanack`): a ball faced is any ball
that was not a wide (A22), a bowler is charged neither byes nor leg-byes (A1), and par is the
league in the player's own seasons, weighted by the player's balls in each.
"""

from __future__ import annotations

import hashlib
import json

from tools.build_history import esc, para, section, table

DEFAULT = ("virat-kohli", "rohit-sharma")
FAMOUS = [("virat-kohli", "rohit-sharma"), ("ms-dhoni", "ab-de-villiers"), ("jasprit-bumrah", "lasith-malinga"),
          ("chris-gayle", "david-warner"), ("sunil-narine", "rashid-khan"), ("andre-russell", "hardik-pandya"),
          ("yuzvendra-chahal", "piyush-chawla"), ("shubman-gill", "yashasvi-jaiswal")]
MIN_SPLIT_BALLS = 30   # the player pages' bar for a phase or style figure
BAT_ROWS_FROM = 100    # a batting row is shown when either player made this many runs
BOWL_ROWS_FROM = 5     # a bowling row when either took this many wickets

# key, label, group, which way is better, decimals
ROWS = [
    ("m", "Matches", "career", "high", 0),
    ("pom", "Player of the Match", "career", "high", 0),
    ("card", "Best season card", "career", "high", 0),
    ("seasons", "Seasons", "career", "high", 0),
    ("runs", "Runs", "bat", "high", 0),
    ("sr", "Strike rate", "bat", "high", 1),
    ("avg", "Average", "bat", "high", 2),
    ("hs", "Highest score", "bat", "high", 0),
    ("h100", "Hundreds", "bat", "high", 0),
    ("h50", "Fifties", "bat", "high", 0),
    ("sixes", "Sixes", "bat", "high", 0),
    ("fours", "Fours", "bat", "high", 0),
    ("wkts", "Wickets", "bowl", "high", 0),
    ("econ", "Economy", "bowl", "low", 2),
    ("bavg", "Bowling average", "bowl", "low", 2),
    ("bsr", "Balls per wicket", "bowl", "low", 1),
    ("best", "Best figures", "bowl", "high", 0),
    ("w4", "Four-wicket hauls", "bowl", "high", 0),
]
PAR_ROWS = [("pp", "Powerplay"), ("mid", "Middle overs"), ("death", "Death overs"), ("pace", "Against pace"),
            ("spin", "Against spin")]
PHASE_KEY = {"powerplay": "pp", "middle": "mid", "death": "death"}


def _r(x: float | None, places: int) -> float | None:
    return None if x is None else round(x, places)


def stats(a, c) -> dict:
    """One player's figures, each a number (or None where it has no meaning, e.g. an average
    with no dismissal) and, where a number is not how it is printed, a `text`."""
    from tools.build_almanack import player_path
    b, w = c.bat, c.bowl
    seasons = []
    for s in c.seasons:
        card = a.p.cards.get((c.person, s["fs"]))
        seasons.append([s["year"], s["bat"]["runs"], s["bowl"]["wickets"],
                        card.display if card is not None and card.display else None, a.key(s["fs"])])
    best_card = max((x[3] for x in seasons if x[3]), default=None)
    fs = a.main_fs(c)
    hs_runs, hs_not = b["hs"]
    bw, br = w["best"]
    out = {
        "name": c.name, "path": player_path(c.name), "crest": a.crest(fs) or "", "key": a.key(fs) or "",
        "team": a.rec.team(fs) if fs is not None else "", "role": a.p.role.get(c.person, ""),
        "country": a.p.meta.get(c.person, {}).get("nationality") or "",
        "years": [c.seasons[0]["year"], c.seasons[-1]["year"]] if c.seasons else [],
        "m": c.matches, "pom": a.p.pom.get(c.person, 0), "card": best_card,
        "seasons_n": len({s["year"] for s in c.seasons}),
        "runs": b["runs"], "sr": _r(100 * b["runs"] / b["balls"], 1) if b["balls"] else None,
        "avg": _r(b["runs"] / b["outs"], 2) if b["outs"] else None,
        "hs": hs_runs if b["inns"] else None, "hs_t": f"{hs_runs}{'*' if hs_not else ''}" if b["inns"] else None,
        "h100": b["hundreds"], "h50": b["fifties"], "sixes": b["sixes"], "fours": b["fours"],
        "wkts": w["wickets"], "econ": _r(6 * w["runs"] / w["balls"], 2) if w["balls"] else None,
        "bavg": _r(w["runs"] / w["wickets"], 2) if w["wickets"] else None,
        "bsr": _r(w["balls"] / w["wickets"], 1) if w["wickets"] else None,
        "best": bw * 1000 - br if w["wickets"] else None, "best_t": f"{bw}/{br}" if w["wickets"] else None,
        "w4": w["fours"], "s": seasons, "id": c.person,
    }
    out["seasons"] = out.pop("seasons_n")
    par_bat, par_bowl = {}, {}
    for ph, sp in a.p.bat_phase.get(c.person, {}).items():
        if ph in PHASE_KEY and sp.balls >= MIN_SPLIT_BALLS:
            par_bat[PHASE_KEY[ph]] = _r(sp.above_par(), 1)
    for st, sp in a.p.bat_style.get(c.person, {}).items():
        if st in ("pace", "spin") and sp.balls >= MIN_SPLIT_BALLS:
            par_bat[st] = _r(sp.above_par(), 1)
    for ph, sp in a.p.bowl_phase.get(c.person, {}).items():
        if ph in PHASE_KEY and sp.balls >= MIN_SPLIT_BALLS:
            par_bowl[PHASE_KEY[ph]] = _r(100 * (sp.par_runs - sp.runs) / sp.balls, 1)
    out["pb"], out["pw"] = par_bat, par_bowl
    return out


def document(a) -> str:
    """Every player, keyed by URL slug, with the row definitions."""
    from tools.build_almanack import player_slug
    players = {player_slug(c.name): stats(a, c) for c in sorted(a.careers.values(), key=lambda c: c.name)}
    doc = {"rows": [list(r) for r in ROWS], "par": [list(r) for r in PAR_ROWS],
           "bat_from": BAT_ROWS_FROM, "bowl_from": BOWL_ROWS_FROM, "players": players}
    return json.dumps(doc, separators=(",", ":"), ensure_ascii=False, sort_keys=True) + "\n"


def url(doc: str) -> str:
    return "/static/almanack/compare.json?v=" + hashlib.sha256(doc.encode()).hexdigest()[:10]


def shown_rows(x: dict, y: dict) -> list[tuple]:
    """The rows worth a line for these two: batting rows if either batted to any extent,
    bowling rows if either bowled, and never a row where neither has a figure."""
    keep = []
    for key, label, group, better, places in ROWS:
        if group == "bat" and max(x["runs"], y["runs"]) < BAT_ROWS_FROM:
            continue
        if group == "bowl" and max(x["wkts"], y["wkts"]) < BOWL_ROWS_FROM:
            continue
        if x.get(key) is None and y.get(key) is None:
            continue
        keep.append((key, label, group, better, places))
    return keep


def leader(x: dict, y: dict, key: str, better: str) -> int:
    """-1 if the first is better on this row, 1 if the second, 0 for level or no contest."""
    p, q = x.get(key), y.get(key)
    if p is None or q is None or p == q:
        return 0
    first = p > q if better == "high" else p < q
    return -1 if first else 1


def shown(s: dict, key: str, places: int) -> str:
    if key == "hs":
        return s.get("hs_t") or "-"
    if key == "best":
        return s.get("best_t") or "-"
    v = s.get(key)
    if v is None:
        return "-"
    return f"{v:,.{places}f}" if places else f"{v:,}"


def static_comparison(doc: dict, slug_a: str, slug_b: str) -> str:
    """The default pair in plain HTML: a sentence and the table of every shown row."""
    x, y = doc["players"][slug_a], doc["players"][slug_b]
    rows, wins = [], [0, 0]
    for key, label, _g, better, places in shown_rows(x, y):
        lead = leader(x, y, key, better)
        if lead < 0:
            wins[0] += 1
        elif lead > 0:
            wins[1] += 1
        mark = lambda s, me: f"<b>{s}</b>" if me else s  # noqa: E731
        rows.append([mark(shown(x, key, places), lead < 0), esc(label), mark(shown(y, key, places), lead > 0)])
    head = [f'<a href="{x["path"]}">{esc(x["name"])}</a>', "", f'<a href="{y["path"]}">{esc(y["name"])}</a>']
    if wins[0] == wins[1]:
        verdict = f"{esc(x['name'])} and {esc(y['name'])} are level, {wins[0]} figures each."
    else:
        ahead, behind = (x, y) if wins[0] > wins[1] else (y, x)
        verdict = (f"{esc(ahead['name'])} is ahead on {max(wins)} of the {sum(wins)} figures where the two differ, "
                   f"{esc(behind['name'])} on {min(wins)}.")
    return para(verdict) + table(head, rows, num_from=3, cls="al-cmp-table")


def page_body(a, doc_text: str, data_url: str, h2h_url: str) -> str:
    doc = json.loads(doc_text)
    famous = "".join(
        f'<li><a href="/almanack/compare?a={p}&amp;b={q}">{esc(doc["players"][p]["name"])} v '
        f'{esc(doc["players"][q]["name"])}</a></li>' for p, q in FAMOUS if p in doc["players"] and q in doc["players"])
    names = "".join(f'<option value="{esc(c.name)}">' for c in sorted(a.careers.values(), key=lambda c: c.name))
    pickers = (f'<div class="al-cmp-pick" id="cmpPick" data-src="{data_url}" data-h2h="{h2h_url}">'
               '<input id="cmpA" class="room-input" list="cmpNames" autocomplete="off" placeholder="First player" '
               'aria-label="First player"><button type="button" class="al-cmp-swap" onclick="cmpSwap()" '
               'aria-label="Swap the two players">⇄</button>'
               '<input id="cmpB" class="room-input" list="cmpNames" autocomplete="off" placeholder="Second player" '
               f'aria-label="Second player"><datalist id="cmpNames">{names}</datalist></div>'
               '<div id="cmpOut" class="al-cmp-out" aria-live="polite"></div>')
    a_slug, b_slug = DEFAULT
    intro = para("Any two players who have played in the IPL, side by side: their careers figure by figure, "
                 "how each did in every phase and against pace and spin measured against the league in the "
                 "same seasons, both careers season by season on one chart, and every ball the two bowled to "
                 "each other.",
                 "Type two names. The comparison has its own link, so it can be sent to someone.")
    return (section("Compare two players", intro + pickers)
            + f'<section class="hx-sec al-cmp-static" id="cmpStatic"><h2>{esc(doc["players"][a_slug]["name"])} v '
              f'{esc(doc["players"][b_slug]["name"])}</h2>{static_comparison(doc, a_slug, b_slug)}</section>\n'
            + section("Famous comparisons", f'<ul class="al-cmp-famous">{famous}</ul>')
            + '    <p class="hx-pager actions"><a class="go ghost" href="/almanack/players">All players</a>'
              '<a class="go ghost" href="/almanack">The Almanack</a></p>\n'
            + '    <script src="/static/compare.js"></script>\n    <script src="/static/statcard.js"></script>\n')
