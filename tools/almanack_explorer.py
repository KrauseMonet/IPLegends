"""The stats explorer's page [A322]: the form the browser fills, and worked examples in plain HTML.

The explorer itself runs in the browser (`web/static/explorer.js`) over the packed ball file
(`tools.snapshot_explorer`). What a reader without the script, or a crawler, gets is this page's
worked examples: each a real question answered here by the reference aggregate, as a table, with
a button that loads the same question into the explorer. So the page carries real figures, and
each one is also a test that the browser's answer will match (both run the same rules).
"""

from __future__ import annotations

import hashlib
import json

from tools import snapshot_explorer as E
from tools.build_history import esc, para, section, table

MIN_PLAYER_BALLS = 60      # a player row needs this many balls unless a question says otherwise

# title, sentence, the question (as the explorer reads it), how many rows to print
EXAMPLES = [
    ("Fastest scorers at the death since 2020",
     "Strike rate in overs 16 to 20 since the 2020 season, for batters who faced at least 150 balls there.",
     {"mode": "bat", "by": "player", "phase": 3, "from": 2020, "min": 150, "sort": "sr"}, 10),
    ("Most sixes against spin",
     "Sixes off spin bowling across every season.",
     {"mode": "bat", "by": "player", "style": 2, "sort": "sixes"}, 10),
    ("Tightest bowlers in the powerplay",
     "Economy in overs 1 to 6, for bowlers who sent down at least 300 legal balls there.",
     {"mode": "bowl", "by": "player", "phase": 1, "min": 300, "sort": "econ", "asc": 1}, 10),
    ("Most runs in finals",
     "Runs in IPL finals, every season.",
     {"mode": "bat", "by": "player", "stage": 2, "min": 0, "sort": "runs"}, 10),
    ("How each batting position scores",
     "Every ball faced, grouped by where the batter came in.",
     {"mode": "bat", "by": "position", "sort": "sr"}, 11),
    ("Pace against spin",
     "Every ball bowled, by the type of bowling: wickets, economy and how often each takes a wicket.",
     {"mode": "bowl", "by": "style", "sort": "wkts"}, 3),
]

BAT = [("Inns", lambda r: r["inns"], 0), ("Runs", lambda r: r["runs"], 0), ("Balls", lambda r: r["balls"], 0),
       ("SR", lambda r: 100 * r["runs"] / r["balls"] if r["balls"] else None, 1),
       ("Avg", lambda r: r["runs"] / r["outs"] if r["outs"] else None, 2),
       ("4s", lambda r: r["fours"], 0), ("6s", lambda r: r["sixes"], 0)]
BOWL = [("Inns", lambda r: r["inns"], 0), ("Overs", lambda r: r["balls"], "overs"), ("Runs", lambda r: r["runs"], 0),
        ("Wkts", lambda r: r["wkts"], 0), ("Econ", lambda r: 6 * r["runs"] / r["balls"] if r["balls"] else None, 2),
        ("Avg", lambda r: r["runs"] / r["wkts"] if r["wkts"] else None, 2),
        ("SR", lambda r: r["balls"] / r["wkts"] if r["wkts"] else None, 1)]
SORT_KEY = {"runs": "Runs", "sr": "SR", "sixes": "6s", "econ": "Econ", "wkts": "Wkts", "avg": "Avg"}
ORDERED_GROUPS = {"season", "position", "phase", "innings", "stage"}   # printed in their own order, as the page does


NAMES = {"phase": ["Unknown", "Powerplay", "Middle overs", "Death overs"], "style": ["Unknown", "Pace", "Spin"],
         "stage": ["League", "Playoffs", "Final"], "innings": ["", "Batting first", "Batting second"]}


def label(index: dict, by: str, k: int) -> str:
    if by == "player":
        name, slug, _ = index["people"][k]
        return f'<a href="/almanack/players/{slug}">{esc(name)}</a>' if slug else esc(name)
    if by in ("team", "opponent"):
        return esc(index["teams"][k])
    if by == "ground":
        return f'<a href="/almanack/grounds/{index["grounds"][k][1]}">{esc(index["grounds"][k][0])}</a>'
    if by in NAMES:
        return NAMES[by][k]
    return f"No. {k}" if by == "position" else str(k)


def answer(index: dict, cols: dict, q: dict, rows: int) -> list[tuple[int, dict]]:
    """The rows the explorer would show for `q`, in the same order (sort, then the group's own
    order where it has one)."""
    res = E.aggregate(index, cols, q)
    bat = q["mode"] == "bat"
    spec = {c[0]: c[1] for c in (BAT if bat else BOWL)}
    f = spec[SORT_KEY[q["sort"]]]
    floor = q.get("min", MIN_PLAYER_BALLS if q["by"] == "player" else 0)
    keep = [(k, r) for k, r in res.items() if r["balls"] >= floor and f(r) is not None]
    keep.sort(key=lambda kr: f(kr[1]), reverse=not q.get("asc"))
    if q["by"] in ORDERED_GROUPS:
        keep.sort(key=lambda kr: kr[0])
    return keep[:rows]


def shown(v, places) -> str:
    if v is None:
        return "-"
    if places == "overs":
        return f"{v // 6}.{v % 6}"
    return f"{v:,.{places}f}" if places else f"{v:,}"


def example(index: dict, cols: dict, title: str, sentence: str, q: dict, rows: int) -> str:
    spec = BAT if q["mode"] == "bat" else BOWL
    got = answer(index, cols, q, rows)
    body = [[label(index, q["by"], k)] + [shown(f(r), p) for _h, f, p in spec] for k, r in got]
    head = [{"player": "Player", "position": "Position", "style": "Bowling", "season": "Season"}.get(q["by"], "")] \
        + [h for h, _f, _p in spec]
    button = (f'<button type="button" class="act ex-preset" data-q="{esc(json.dumps(q, separators=(",", ":")))}">'
              "Open in the explorer</button>")
    return f"<h3>{esc(title)}</h3>" + para(sentence) + table(head, body, num_from=1) + f'<p class="ex-open">{button}</p>'


def form() -> str:
    groups = "".join(f'<option value="{k}">{esc(t)}</option>' for k, t in (
        ("player", "Player"), ("season", "Season"), ("team", "Team"), ("opponent", "Opponent"), ("ground", "Ground"),
        ("phase", "Phase"), ("position", "Batting position"), ("style", "Bowling type"), ("innings", "Innings"),
        ("stage", "Stage")))
    sel = lambda id, any, opts="": f'<select id="{id}" class="room-input"><option value="">{any}</option>{opts}</select>'  # noqa: E731
    phase = '<option value="1">Powerplay</option><option value="2">Middle overs</option><option value="3">Death overs</option>'
    pos = "".join(f'<option value="{i}">No. {i}</option>' for i in range(1, 12))
    style = '<option value="1">Pace</option><option value="2">Spin</option>'
    inn = '<option value="1">Batting first</option><option value="2">Batting second</option>'
    stage = '<option value="0">League</option><option value="1">Playoffs</option><option value="2">Final</option>'
    field = lambda lab, inner, lid="": f'<label class="ex-f"><span{f" id={chr(34)}{lid}{chr(34)}" if lid else ""}>{lab}</span>{inner}</label>'  # noqa: E731
    return ('<div class="ex-form">'
            '<div class="ex-row"><div class="al-switch ex-mode" role="group" aria-label="Figures">'
            '<button type="button" data-mode="bat" aria-pressed="true">Batting</button>'
            '<button type="button" data-mode="bowl" aria-pressed="false">Bowling</button></div>'
            + field("Show by", f'<select id="exBy" class="room-input">{groups}</select>')
            + field("Player", '<input id="exPlayer" class="room-input" list="exPlayers" autocomplete="off" '
                              'placeholder="Anyone"><datalist id="exPlayers"></datalist>')
            + field("Min balls", '<input id="exMin" class="room-input" type="number" min="0" step="10" '
                                 'placeholder="60 for players">') + "</div>"
            '<div class="ex-row">'
            + field("From", sel("exFrom", "First season")) + field("To", sel("exTo", "Last season"))
            + field("Batting for", sel("exTeam", "Any team"), "exTeamLabel") + field("Against team", sel("exOpp", "Any opponent"))
            + field("Ground", sel("exGround", "Any ground")) + "</div>"
            '<div class="ex-row">'
            + field("Phase", sel("exPhase", "Every phase", phase)) + field("Batting position", sel("exPos", "Any", pos))
            + field("Against", sel("exStyle", "Pace or spin", style), "exStyleLabel")
            + field("Innings", sel("exInn", "Either", inn)) + field("Stage", sel("exStage", "Every match", stage))
            + "</div></div>")


def page_body(a) -> str:
    index, cols = E.read()
    idx_text = E.INDEX.read_text()
    data = E.DATA.read_bytes()
    idx_url = "/static/almanack/explorer.json?v=" + hashlib.sha256(idx_text.encode()).hexdigest()[:10]
    data_url = "/static/almanack/explorer.bin.gz?v=" + hashlib.sha256(data).hexdigest()[:10]
    intro = para(f"Every one of the {index['n']:,} balls bowled in the IPL since {index['matches'][0][0]}, ready to be "
                 "asked anything: runs, strike rate, wickets or economy, for any season, team, opponent, ground, "
                 "phase, batting position or type of bowling, shown by player, season, team or any of the rest.",
                 "The whole archive loads once into this page and every answer is worked out here, in a fraction "
                 "of a second. The question goes into the address, so an answer can be sent to someone.")
    quick = "".join(f'<button type="button" class="ex-chip ex-preset" data-q="{esc(json.dumps(q, separators=(",", ":")))}">'
                    f"{esc(t)}</button>" for t, _s, q, _n in EXAMPLES)
    box = (f'<div id="exBox" class="ex-box" data-index="{idx_url}" data-data="{data_url}">{form()}'
           f'<div class="ex-chips">{quick}</div><p id="exCount" class="ex-count" aria-live="polite"></p>'
           '<div id="exOut"></div></div>')
    examples = "".join(example(index, cols, t, s, q, n) for t, s, q, n in EXAMPLES)
    rules = para("The counting follows the rest of the Almanack: a ball faced is any ball that was not a wide; a "
                 "bowler is charged runs off the bat, wides and no-balls, never byes or leg-byes, and credited only "
                 "the wickets that are the bowler's own; a batting position is the order in which batters first "
                 "reached the crease; super overs are left out. An innings is counted for a batter who reached the "
                 "crease, so a run out without facing a ball is an innings.")
    return (section("Ask the archive", intro + box) + section("Worked examples", examples)
            + section("How it counts", rules)
            + '    <p class="hx-pager actions"><a class="go ghost" href="/almanack">The Almanack</a></p>\n'
            + '    <script src="/static/explorer.js"></script>\n')
