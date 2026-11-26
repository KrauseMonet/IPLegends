"""The stats explorer's data: every IPL ball, packed for the browser [A322].

    uv run python -m tools.snapshot_explorer           # write web/static/almanack/explorer.{json,bin.gz}
    uv run python -m tools.snapshot_explorer --check   # exit 1 if a committed file is stale

WHY IN THE BROWSER. The explorer answers any question of the form "this figure, filtered by
season, team, opponent, ground, phase, batting position, bowling type, innings and stage, shown
by any of those". Answering that on the server would read `deliveries` on every request, which
is the very cost A173 moved development off Neon to avoid. So every ball since 2008 is packed into
one static file (columns of small integers, gzipped, ~1 MB) that the page fetches once and adds
up itself in well under a second. The server is never asked anything.

THE FILES. `explorer.json` is the index: people, teams, grounds, matches, and each innings as a
run of balls (its offset and count in the columns). `explorer.bin.gz` holds the columns, one after
another, each the same length (one entry a ball, super overs left out, A15):

    over (u8) · striker, non-striker, bowler (u16, into `people`) · runs off the bat, wides,
    no-balls, other extras (u8) · wicket (u8: 0, or 1 + index into `kinds`) · who was out (u8: 0
    nobody, 1 the striker, 2 the non-striker) · phase (u8: 0 unknown, 1-3 powerplay to death)

THE RULES are the Almanack's own (`tools.build_almanack`): a ball faced is any ball that was not
a wide (A22); a bowler is charged runs off the bat, wides and no-balls, never byes, leg-byes or
penalties (A1), and credited only his own wickets (A36); retired hurt is not a dismissal; a batting
position is the order of first arrival at either end (A4); a phase is the one `deliveries` already
records (A5), so the six innings of unknown length belong to none (A17). `aggregate` below is the
reference implementation, and `web/static/explorer.js` must agree with it ball for ball, which a
test checks on the real data.
"""

from __future__ import annotations

import gzip
import json
import pathlib
import sys
from array import array
from collections import defaultdict

from tools import build_history as H

STATIC = H.STATIC / "almanack"
INDEX = STATIC / "explorer.json"
DATA = STATIC / "explorer.bin.gz"
VERSION = 1
COLS = [("over", "B"), ("striker", "H"), ("nonstriker", "H"), ("bowler", "H"), ("rb", "B"), ("wd", "B"),
        ("nb", "B"), ("other", "B"), ("wk", "B"), ("out", "B"), ("phase", "B")]
PHASES = {"powerplay": 1, "middle": 2, "death": 3}
STYLE = {"pace": 1, "spin": 2}
NOT_OUT = {"retired hurt"}
NOT_BOWLERS = {"run out", "retired hurt", "retired out", "obstructing the field"}


def build(conn) -> tuple[dict, bytes]:
    from tools.almanack_records import read_venues
    from tools.build_almanack import player_slug
    arch = H.load(conn)
    venues = read_venues()
    cur = conn.cursor()
    cur.execute("select person_id, coalesce(display_name, primary_name), bowling_style from people order by 2, 1")
    people, pix = [], {}
    played = {b.person for b in arch.bats} | {b.person for b in arch.bowls}
    for pid, name, style in cur.fetchall():
        pix[pid] = len(people)
        people.append([name, player_slug(name) if pid in played else "", STYLE.get(style, 0)])
    teams = sorted({f for f, _, _ in arch.fs.values()})
    tix = {t: i for i, t in enumerate(teams)}
    grounds, gix = [], {}
    matches, mix = [], {}
    for m in sorted(arch.matches.values(), key=lambda m: (m.day, m.match_id)):
        g = venues.get((m.venue or "", m.city or ""), {}).get("ground") or (m.venue or "?")
        if g not in gix:
            gix[g] = len(grounds)
            grounds.append([g, H.slug(g)])
        stage = 0 if not m.stage else (2 if m.stage.lower() == "final" else 1)
        mix[m.match_id] = len(matches)
        matches.append([m.year, m.day.isoformat(), gix[g], stage, m.match_id])
    kinds: list[str] = []
    cols = {name: array(t) for name, t in COLS}
    innings: list[list] = []
    cur.execute("""select match_id, innings_no, batting_fs_id, bowling_fs_id, over_no, batter_id, non_striker_id,
                          bowler_id, runs_batter, extra_wides, extra_noballs,
                          extra_byes + extra_legbyes + extra_penalty, wicket_kind, player_out_id, phase
                   from deliveries where not is_super_over order by match_id, innings_no, delivery_id""")
    rows = cur.fetchall()
    order = {mid: i for i, mid in enumerate(m[4] for m in matches)}
    rows.sort(key=lambda r: (order[r[0]], r[1]))          # matches in date order, balls in ball order
    for mid, inn, bat_fs, bowl_fs, over, st, ns, bw, rb, wd, nb, oth, kind, out, phase in rows:
        if not innings or innings[-1][0] != mix[mid] or innings[-1][1] != inn:
            innings.append([mix[mid], inn, tix[arch.fs[bat_fs][0]], tix[arch.fs[bowl_fs][0]], len(cols["over"]), 0])
        innings[-1][5] += 1
        k = 0
        if kind is not None:
            if kind not in kinds:
                kinds.append(kind)
            k = kinds.index(kind) + 1
        who = 0 if out is None else (1 if out == st else 2)
        for name, v in (("over", over), ("striker", pix[st]), ("nonstriker", pix[ns]), ("bowler", pix[bw]),
                        ("rb", rb), ("wd", wd), ("nb", nb), ("other", oth), ("wk", k), ("out", who),
                        ("phase", PHASES.get(phase, 0))):
            cols[name].append(v)
    n = len(cols["over"])
    blob, layout = bytearray(), []
    for name, t in COLS:
        a = cols[name]
        if sys.byteorder != "little":
            a.byteswap()
        layout.append([name, "u8" if t == "B" else "u16", len(blob)])
        blob += a.tobytes()
        if len(blob) % 2:
            blob += b"\0"                                   # keep every u16 column aligned
    index = {"version": VERSION, "n": n, "cols": layout, "kinds": kinds, "people": people, "teams": teams,
             "grounds": grounds, "matches": matches, "innings": innings}
    return index, bytes(blob)


def encode(index: dict, blob: bytes) -> tuple[str, bytes]:
    return (json.dumps(index, separators=(",", ":"), ensure_ascii=False) + "\n",
            gzip.compress(blob, compresslevel=9, mtime=0))


# --------------------------------------------------------------------------- reading it back

def read() -> tuple[dict, dict]:
    index = json.loads(INDEX.read_text())
    blob = gzip.decompress(DATA.read_bytes())
    cols = {}
    for name, kind, off in index["cols"]:
        a = array("B" if kind == "u8" else "H")
        size = index["n"] * (1 if kind == "u8" else 2)
        a.frombytes(blob[off:off + size])
        if sys.byteorder != "little" and kind == "u16":
            a.byteswap()
        cols[name] = a
    return index, cols


# --------------------------------------------------------------------------- the reference aggregate

GROUPS = ("player", "season", "team", "opponent", "ground", "phase", "position", "style", "innings", "stage")


def aggregate(index: dict, cols: dict, q: dict) -> dict:
    """Totals per group for one query. `q`: mode ("bat" | "bowl"), by (one of GROUPS) and any of
    from/to (seasons), team/opp/ground (index), phase (1-3), pos (1-11), style (1 pace, 2 spin),
    inn (1 | 2), stage (0 league, 1 playoff, 2 final), player (index)."""
    bat = q.get("mode", "bat") == "bat"
    by = q.get("by", "player")
    kinds = index["kinds"]
    people = index["people"]
    credited = [k not in NOT_BOWLERS for k in kinds]
    dismissal = [k not in NOT_OUT for k in kinds]
    out: dict = {}

    def acc(key):
        if key not in out:
            out[key] = dict(inns=set(), runs=0, balls=0, outs=0, fours=0, sixes=0, dots=0, conceded=0,
                            wkts=0, gone=set(), wk_inn=defaultdict(int),
                            run_inn=defaultdict(int))
        return out[key]

    over, st, ns, bw = cols["over"], cols["striker"], cols["nonstriker"], cols["bowler"]
    rb, wd, nb, wk, who, ph = cols["rb"], cols["wd"], cols["nb"], cols["wk"], cols["out"], cols["phase"]
    for ii, (mi, inn_no, bt, bl, start, count) in enumerate(index["innings"]):
        year, _day, ground, stage, _id = index["matches"][mi]
        if q.get("from") and year < q["from"] or q.get("to") and year > q["to"]:
            continue
        team, opp = (bt, bl) if bat else (bl, bt)
        if q.get("team") is not None and team != q["team"]:
            continue
        if q.get("opp") is not None and opp != q["opp"]:
            continue
        if q.get("ground") is not None and ground != q["ground"]:
            continue
        if q.get("inn") and inn_no != q["inn"]:
            continue
        if q.get("stage") is not None and stage != q["stage"]:
            continue
        pos: dict[int, int] = {}
        for i in range(start, start + count):
            for p in (st[i], ns[i]):
                if p not in pos:
                    pos[p] = len(pos) + 1
            if q.get("phase") and ph[i] != q["phase"]:
                continue
            style = people[bw[i]][2]
            if q.get("style") and style != q["style"]:
                continue

            def key(person):
                return {"player": person, "season": year, "team": team, "opponent": opp, "ground": ground,
                        "phase": ph[i], "position": pos[person if bat else st[i]], "style": style,
                        "innings": inn_no, "stage": stage}[by]

            if bat:
                for person, striker in ((st[i], True), (ns[i], False)):
                    if q.get("player") is not None and person != q["player"]:
                        continue
                    if q.get("pos") and pos[person] != q["pos"]:
                        continue
                    a = acc(key(person))
                    a["inns"].add((ii, person))
                    if striker:
                        a["runs"] += rb[i]
                        a["run_inn"][(ii, person)] += rb[i]
                        if wd[i] == 0:
                            a["balls"] += 1
                            a["dots"] += rb[i] == 0
                        a["fours"] += rb[i] == 4
                        a["sixes"] += rb[i] == 6
                    gone = wk[i] and dismissal[wk[i] - 1] and who[i] == (1 if striker else 2)
                    if gone:
                        a["outs"] += 1
                        a["gone"].add((ii, person))
            else:
                if q.get("player") is not None and bw[i] != q["player"]:
                    continue
                if q.get("pos") and pos[st[i]] != q["pos"]:
                    continue
                a = acc(key(bw[i]))
                a["inns"].add((ii, bw[i]))
                legal = wd[i] == 0 and nb[i] == 0
                a["balls"] += legal
                a["conceded"] += rb[i] + wd[i] + nb[i]
                a["run_inn"][(ii, bw[i])] += rb[i] + wd[i] + nb[i]
                a["dots"] += legal and rb[i] == 0
                a["fours"] += rb[i] == 4
                a["sixes"] += rb[i] == 6
                if wk[i] and credited[wk[i] - 1]:
                    a["wkts"] += 1
                    a["wk_inn"][(ii, bw[i])] += 1
    return {k: finish(v, bat) for k, v in out.items()}


def finish(a: dict, bat: bool) -> dict:
    """Counts only, in integers, so the browser's answer can be compared exactly."""
    if bat:
        scores = [(r, k not in a["gone"]) for k, r in a["run_inn"].items()]
        best = max(scores, default=(0, False))
        return dict(inns=len(a["inns"]), runs=a["runs"], balls=a["balls"], outs=a["outs"], fours=a["fours"],
                    sixes=a["sixes"], dots=a["dots"], fifties=sum(50 <= r < 100 for r, _ in scores),
                    hundreds=sum(r >= 100 for r, _ in scores), hs=best[0], hs_not=best[1] and best[0] > 0)
    figs = [(a["wk_inn"].get(k, 0), r) for k, r in a["run_inn"].items()]
    best = max(figs, key=lambda f: (f[0], -f[1]), default=(0, 0))
    return dict(inns=len(a["inns"]), balls=a["balls"], runs=a["conceded"], wkts=a["wkts"], dots=a["dots"],
                fours=a["fours"], sixes=a["sixes"], w4=sum(w >= 4 for w, _ in figs), best_w=best[0], best_r=best[1])


def main(check: bool = False) -> int:
    with H._connect() as conn:
        index, blob = build(conn)
    text, data = encode(index, blob)
    if check:
        same = INDEX.exists() and DATA.exists() and INDEX.read_text() == text and DATA.read_bytes() == data
        print("explorer data current" if same else "explorer data STALE (run tools.snapshot_explorer)")
        return 0 if same else 1
    INDEX.write_text(text)
    DATA.write_bytes(data)
    print(f"wrote explorer: {index['n']:,} balls, {len(text) / 1024:.0f} kB index, {len(data) / 1024:.0f} kB data "
          f"({len(blob) / 1024:.0f} kB unpacked)")
    return 0


if __name__ == "__main__":
    sys.exit(main(check="--check" in sys.argv))
