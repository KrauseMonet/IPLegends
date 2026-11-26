"""Freeze every real IPL match, ball by ball, to a committed file the app can replay [A319].

    uv run python -m tools.snapshot_matches           # write data/real_matches.json.gz
    uv run python -m tools.snapshot_matches --check   # exit 1 if the committed file is stale

WHY A FILE. A replay and a what-if are both read on the solo path, which touches no database
(A107): the deck and the model come from a snapshot, and so does this. It is read lazily, on
the first replay request, never at boot, so a cold start does not pay for it.

WHAT IS IN IT, per match: the two sides, the toss, the result as the archive records it, the
stage, the ground (the venues override's one name for it, A316), who was named for each side
(`appearances`, so a player who neither batted nor bowled is still in the eleven), and every
delivery in ball order (`delivery_id`, A316) as a short row:

    [over, striker, non-striker, bowler, runs off the bat, wides, no-balls, byes, leg-byes,
     penalty, player out (-1 for none), wicket kind (index into `kinds`, -1 for none)]

where a player is an index into the innings' own list of person ids. Super overs are kept,
flagged, so a tied match replays to its real end. Written with `mtime=0` and sorted keys so
the same database gives the same bytes (A107's rule for a committed artefact).
"""

from __future__ import annotations

import gzip
import json
import pathlib
import sys
from collections import defaultdict

from tools import build_history as H

ROOT = pathlib.Path(__file__).resolve().parent.parent
PATH = ROOT / "data" / "real_matches.json.gz"
VERSION = 1


def build_document(conn) -> dict:
    from tools.almanack_records import read_venues
    arch = H.load(conn)
    venues = read_venues()
    cur = conn.cursor()
    cur.execute("select match_id, toss_winner_fs_id, toss_decision from matches")
    toss = {m: (w, d) for m, w, d in cur.fetchall()}
    cur.execute("""select match_id, franchise_season_id, person_id from appearances
                   where named_in_squad or participated order by match_id, franchise_season_id, person_id""")
    named: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for m, fs, p in cur.fetchall():
        named[m][fs].append(p)

    kinds: list[str] = []
    innings: dict[str, list[dict]] = defaultdict(list)
    cur.execute("""select match_id, innings_no, is_super_over, batting_fs_id, over_no, batter_id, non_striker_id,
                          bowler_id, runs_batter, extra_wides, extra_noballs, extra_byes, extra_legbyes,
                          extra_penalty, player_out_id, wicket_kind
                   from deliveries order by delivery_id""")
    for (mid, inn, so, fs, over, bat, ns, bowl, rb, wd, nb, by, lb, pen, out, kind) in cur.fetchall():
        rows = innings[mid]
        if not rows or rows[-1]["n"] != inn or rows[-1]["so"] != so:
            rows.append({"n": inn, "so": so, "fs": fs, "p": [], "b": []})
        cur_inn = rows[-1]
        people = cur_inn["p"]

        def ix(pid):
            if pid not in people:
                people.append(pid)
            return people.index(pid)
        k = -1
        if kind is not None:
            if kind not in kinds:
                kinds.append(kind)
            k = kinds.index(kind)
        cur_inn["b"].append([over, ix(bat), ix(ns), ix(bowl), rb, wd, nb, by, lb, pen,
                             ix(out) if out else -1, k])

    matches = []
    used: set[str] = set()
    for m in sorted(arch.matches.values(), key=lambda m: (m.day, m.match_id)):
        ground = venues.get((m.venue or "", m.city or ""), {}).get("ground") or m.venue
        ins = [{"fs": i["fs"], "so": int(i["so"]), "p": i["p"], "b": i["b"]} for i in innings.get(m.match_id, [])]
        xi = {str(fs): ps for fs, ps in named[m.match_id].items()}
        for i in ins:
            used.update(i["p"])
        for ps in xi.values():
            used.update(ps)
        if m.pom:
            used.add(m.pom)
        tw, td = toss.get(m.match_id, (None, None))
        matches.append({"id": m.match_id, "day": m.day.isoformat(), "year": m.year, "ground": ground,
                        "city": m.city, "stage": m.stage, "number": m.number, "a": m.a, "b": m.b,
                        "toss": tw, "elect": td, "winner": m.winner, "result": m.result_type,
                        "margin": m.margin, "by": m.decided_by, "pom": m.pom, "xi": xi, "inns": ins})
    fs = {str(k): list(v) for k, v in arch.fs.items()}
    people = {p: arch.names.get(p, p) for p in sorted(used)}
    return {"version": VERSION, "kinds": kinds, "fs": fs, "people": people, "matches": matches}


def encode(doc: dict) -> bytes:
    text = json.dumps(doc, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    return gzip.compress(text.encode(), compresslevel=9, mtime=0)


def read(path: pathlib.Path = PATH) -> dict | None:
    try:
        doc = json.loads(gzip.decompress(path.read_bytes()))
    except (OSError, ValueError):
        return None
    return doc if doc.get("version") == VERSION else None


def main(check: bool = False) -> int:
    with H._connect() as conn:
        doc = build_document(conn)
    data = encode(doc)
    if check:
        same = PATH.exists() and PATH.read_bytes() == data
        print("real matches current" if same else "real matches STALE (run tools.snapshot_matches)")
        return 0 if same else 1
    PATH.write_bytes(data)
    balls = sum(len(i["b"]) for m in doc["matches"] for i in m["inns"])
    print(f"wrote {PATH.name}: {len(doc['matches'])} matches, {balls:,} deliveries, {len(data) / 1024:.0f} kB")
    return 0


if __name__ == "__main__":
    sys.exit(main(check="--check" in sys.argv))
