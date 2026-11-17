"""2027 mini auction, Phase 0: the real retained squads and every player's birth date.

    uv run python -m etl.mini2027 --retained       # re-run as the lists fill in
    uv run python -m etl.mini2027 --birth-dates    # Wikidata + Wikipedia, two sources

Both write override CSVs through `etl.derive_people.merge_override`, so a hand decision is
never written over (A30) and a re-run only refreshes the evidence.

**Retained squads** come from Wikipedia's "List of 2027 Indian Premier League personnel
changes", the source that turned out complete for 2024 and 2026 (the 2026 sold list
matched the official 77 players and Rs 215.45 cr exactly). A player is resolved to a
person id by his Wikipedia article title, which `full_names.csv` already stores for
everyone (A252), and only then by his full name -- never by the archive's initials. A
row nobody could resolve keeps a blank `person_id` and is listed for a hand check; a
debutant with no IPL record has no person id and that is correct, not a failure.
`person_id_checked` is the decision column: fill it to override or confirm a match.

**Birth dates** use a date only where Wikidata and the Wikipedia infobox AGREE. Measured
2026-11-17: 719 of 770 agree and 51 do not, and some of Wikidata's are plainly wrong
(Deepak Chahar 1972, Daryl Mitchell 1988, Venkatesh Iyer 1990 -- two other sources give
1991 and 1994 for the last two). So `birth_date` is filled only on agreement and left
blank on disagreement for a human, never defaulted to either source (A23).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field

from etl.derive_people import OVERRIDES, merge_override, read_override, report_merge

PAGE = "List_of_2027_Indian_Premier_League_personnel_changes"
UA = "cricket-stats-research/0.1 (generic client)"
RETAINED_CSV = OVERRIDES / "retained_2027.csv"
# A hand transcription of a published full team list, for teams Wikipedia has not
# finished. Used per team only where it lists MORE players than Wikipedia does, and
# cross-checked against Wikipedia wherever both list a team.
RETAINED_LIST = OVERRIDES / "retained_2027_list.txt"
# Purse left, places left and overseas places left per team, as officially announced.
PURSE_CSV = OVERRIDES / "purse_2027.csv"
BIRTH_CSV = OVERRIDES / "birth_dates.csv"
FULL_NAMES = OVERRIDES / "full_names.csv"

# The real rules for a squad. What each team has LEFT to spend comes from the official
# broadcast figures in `purse_2027.csv`; the full purse is not needed and is not assumed.
SQUAD_MIN, SQUAD_MAX, OVERSEAS_MAX = 18, 25, 8

TEAMS = {
    "Chennai Super Kings": "CSK", "Mumbai Indians": "MI", "Royal Challengers Bengaluru": "RCB",
    "Kolkata Knight Riders": "KKR", "Rajasthan Royals": "RR", "Delhi Capitals": "DC",
    "Sunrisers Hyderabad": "SRH", "Punjab Kings": "PBKS", "Gujarat Titans": "GT",
    "Lucknow Super Giants": "LSG",
}


def fetch(params: dict) -> dict:
    """One Wikipedia/Wikidata API call with a generic user agent and polite backoff."""
    url = params.pop("_url", "https://en.wikipedia.org/w/api.php") + "?" + urllib.parse.urlencode(params)
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            return json.load(urllib.request.urlopen(req, timeout=60))
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            time.sleep(5 * 2 ** attempt)
    raise SystemExit(f"gave up on {url[:80]} after repeated 429s")


# ---------------------------------------------------------------- parsing (pure)

def lakh(cell: str) -> int | None:
    """{{INRConvert|14.2|c}} -> 1420; {{INRConvert|75|l}} -> 75."""
    m = re.search(r"INRConvert\|([0-9.]+)\|([cl])", cell)
    if not m:
        return None
    v = float(m.group(1))
    return round(v * 100) if m.group(2) == "c" else round(v)


def title_of(cell: str) -> str | None:
    """The Wikipedia article a cell links to."""
    m = re.search(r"\{\{sortname\|([^|}]*)\|([^|}]*)((?:\|[^}]*)?)\}\}", cell)
    if m:
        first, last, rest = m.group(1).strip(), m.group(2).strip(), m.group(3)
        bits = [b.strip() for b in rest.split("|") if b.strip()]
        link = next((b[5:] for b in bits if b.startswith("link=")), None)
        if link:
            return link
        dab = next((b[4:] for b in bits if b.startswith("dab=")), None)
        plain = [b for b in bits if "=" not in b]
        if plain:                       # {{sortname|First|Last|Target article}}
            return plain[0]
        return f"{first} {last}" + (f" ({dab})" if dab else "")
    m = re.search(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", cell)
    return m.group(1).strip() if m else None


def shown_name(cell: str) -> str:
    m = re.search(r"\{\{sortname\|([^|}]*)\|([^|}]*)", cell)
    if m:
        return f"{m.group(1).strip()} {m.group(2).strip()}".strip()
    m = re.search(r"\[\[[^\]|]+\|([^\]]+)\]\]", cell) or re.search(r"\[\[([^\]]+)\]\]", cell)
    return m.group(1).strip() if m else cell.strip()


def parse_retained(wikitext: str) -> list[dict]:
    """Every row of the 'Player retention' tables: one dict per retained player."""
    start = wikitext.find("==Player retention==")
    if start < 0:
        start = wikitext.find("== Player retention ==")
    if start < 0:
        return []
    end = wikitext.find("\n==Auction==", start)
    if end < 0:
        end = wikitext.find("\n== Auction ==", start)
    section = wikitext[start: end if end > 0 else len(wikitext)]
    rows = []
    for table in section.split("{|")[1:]:
        caption = re.search(r"\|\+(.*)", table)
        team = next((t for t in TEAMS if caption and t in caption.group(1)), None)
        if not team:
            continue
        for chunk in table.split("\n|-")[1:]:
            name_cell = next((ln for ln in chunk.splitlines() if ln.lstrip().startswith("!")
                              and ("sortname" in ln or "[[" in ln)), None)
            if name_cell is None:   # a row-header written across two lines
                m = re.search(r"!\s*scope=\"row\"\s*\|\s*\n\s*(\{\{sortname[^\n]*\}\}|\[\[[^\n]*\]\])", chunk)
                name_cell = m.group(1) if m else None
            if name_cell is None:
                continue
            nat = re.search(r"\{\{cr\|([A-Z]+)\}\}", chunk)
            rows.append(dict(team=team, short=TEAMS[team], wikipedia=title_of(name_cell),
                             name=shown_name(name_cell), country=nat.group(1) if nat else "",
                             overseas="" if not nat else ("no" if nat.group(1) == "IND" else "yes"),
                             salary_lakh=lakh(chunk)))
    return rows


# ---------------------------------------------------------------- resolution (pure)

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"^mohd\.?\s+|^md\.?\s+", "mohammed ", s.strip())   # "Mohd. Siraj" is Mohammed Siraj
    return re.sub(r"[^a-z]", "", re.sub(r"\(.*?\)", "", s))


def parse_list(text: str) -> list[dict]:
    """TEAM|Name|O|T lines, '#' comments skipped."""
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        short, name, ovs, traded = (line.split("|") + ["", "", ""])[:4]
        if short not in TEAMS.values():
            raise ValueError(f"unknown team {short!r} in the retained list")
        team = next(t for t, s in TEAMS.items() if s == short)
        rows.append(dict(team=team, short=short, wikipedia="", name=name.strip(), country="",
                         overseas="yes" if ovs.strip() == "O" else "no", salary_lakh=None,
                         traded="yes" if traded.strip() == "T" else ""))
    return rows


def ident(row: dict) -> str:
    """What makes a retained row the same player across sources: his person id, else his name."""
    return row.get("person_id") or "name:" + norm(row.get("name") or "")


def choose_rows(wiki: list[dict], listed: list[dict]) -> tuple[list[dict], list[str]]:
    """Per team, Wikipedia's rows when it lists at least as many as the transcription, else
    the transcription's, carrying a salary across from Wikipedia where the same player is
    on both. Returns the rows and every disagreement where both list a team."""
    out, notes = [], []
    for short in TEAMS.values():
        w = [r for r in wiki if r["short"] == short]
        l = [r for r in listed if r["short"] == short]
        if w and l:
            wi, li = {ident(r): r for r in w}, {ident(r): r for r in l}
            for k in sorted(set(li) - set(wi)):
                if len(w) >= len(l):
                    notes.append(f"{short}: list has {li[k]['name']}, Wikipedia does not")
            for k in sorted(set(wi) - set(li)):
                if len(w) >= len(l):
                    notes.append(f"{short}: Wikipedia has {wi[k]['name']}, the list does not")
            for k in set(wi) & set(li):
                if wi[k]["overseas"] and wi[k]["overseas"] != li[k]["overseas"]:
                    notes.append(f"{short}: overseas disagrees for {wi[k]['name']}")
        if len(w) >= len(l):
            out += [dict(r, source="wikipedia") for r in w]
        else:
            salary = {ident(r): r["salary_lakh"] for r in w}
            out += [dict(r, source="list", salary_lakh=salary.get(ident(r), r["salary_lakh"])) for r in l]
    return out, notes


def name_index(full_names: list[dict]) -> tuple[dict, dict]:
    by_title = {r["wikipedia"]: r["person_id"] for r in full_names if r.get("wikipedia")}
    by_name: dict[str, set] = defaultdict(set)
    for r in full_names:
        if r.get("full_name"):
            by_name[norm(r["full_name"])].add(r["person_id"])
            by_name[swapped(r["full_name"])].add(r["person_id"])
    return by_title, by_name


def swapped(name: str) -> str:
    """The name's words in sorted order, so 'Vyshak Vijaykumar' finds 'Vijaykumar Vyshak'."""
    return "~" + "".join(sorted(norm(w) for w in re.split(r"\s+", re.sub(r"\(.*?\)", "", name)) if norm(w)))


def resolve(row: dict, by_title: dict, by_name: dict) -> tuple[str, str]:
    """(person_id, how). By article title first; by full name only when that name is unique."""
    if row.get("wikipedia") and row["wikipedia"] in by_title:
        return by_title[row["wikipedia"]], "title"
    ids = by_name.get(norm(row.get("name") or ""), set())
    if len(ids) == 1:
        return next(iter(ids)), "name"
    if ids:
        return "", "ambiguous"
    ids = by_name.get(swapped(row.get("name") or ""), set())
    if len(ids) == 1:
        return next(iter(ids)), "name order"
    return "", ("ambiguous" if ids else "none")


# ---------------------------------------------------------------- squads (pure)

@dataclass
class Squad:
    team: str
    players: int = 0
    overseas: int = 0
    spent_lakh: int = 0
    unpriced: int = 0
    unresolved: list[str] = field(default_factory=list)

    @property
    def slots(self) -> int:
        return SQUAD_MAX - self.players

    @property
    def overseas_slots(self) -> int:
        return OVERSEAS_MAX - self.overseas



def squads(rows: list[dict]) -> dict[str, Squad]:
    out = {short: Squad(short) for short in TEAMS.values()}
    for r in rows:
        s = out[r["short"]]
        s.players += 1
        s.overseas += r["overseas"] == "yes"
        if r["salary_lakh"] in (None, ""):
            s.unpriced += 1
        else:
            s.spent_lakh += int(r["salary_lakh"])
        if not (r.get("person_id_checked") or r.get("person_id")) and r.get("how") != "debutant":
            s.unresolved.append(r["name"])
    return out


# ---------------------------------------------------------------- birth dates (pure)

def birth_rows(people: list[dict], wikidata: dict[str, list[str]], wikipedia: dict[str, str]) -> list[dict]:
    """One row a person; `birth_date` only where both sources hold one date and agree."""
    out = []
    for p in people:
        pid = p["person_id"]
        wd = wikidata.get(pid, [])
        wd_one = wd[0] if len(wd) == 1 else ""
        wp = wikipedia.get(pid, "")
        agree = bool(wd_one) and wd_one == wp
        out.append(dict(person_id=pid, name=p.get("full_name") or p.get("name", ""),
                        cricinfo_id=p.get("cricinfo_id", ""), wikidata="|".join(wd), wikipedia=wp,
                        agree="yes" if agree else "no", birth_date=wd_one if agree else ""))
    return out


# ---------------------------------------------------------------- commands

def do_retained() -> None:
    wikitext = fetch({"action": "parse", "page": PAGE, "prop": "wikitext", "format": "json",
                      "formatversion": "2"})["parse"]["wikitext"]
    wiki = parse_retained(wikitext)
    listed = parse_list(RETAINED_LIST.read_text(encoding="utf-8")) if RETAINED_LIST.exists() else []
    by_title, by_name = name_index(read_override(FULL_NAMES))
    for r in wiki + listed:
        r["person_id"], r["how"] = resolve(r, by_title, by_name)
    rows, notes = choose_rows(wiki, listed)
    for r in rows:
        if r["salary_lakh"] is None:      # unknown, written blank -- never the string "None"
            r["salary_lakh"] = ""
    # Keyed on the name AS THAT SOURCE PRINTS IT, never on a resolved id: a better match
    # on a later run must not turn one player into two rows. A team switching source
    # (the list now, Wikipedia once complete) does leave orphans; they are reported and
    # left out of the squads, since merge_override keeps an orphan in case it holds a decision.
    key = ("short", "source", "name")
    columns = ["team", "short", "source", "wikipedia", "name", "country", "overseas", "salary_lakh",
               "person_id", "how", "person_id_checked"]
    counts = merge_override(RETAINED_CSV, columns, rows, key=key, decided="person_id_checked")
    report_merge(RETAINED_CSV, counts)
    print(f"  Wikipedia {len(wiki)} rows, transcribed list {len(listed)} rows")
    for n in notes:
        print("  CHECK:", n)
    current = {tuple(str(r[c]) for c in key) for r in rows}
    print_summary([r for r in read_override(RETAINED_CSV) if tuple(r[c] for c in key) in current])


def reconcile(sq: dict[str, Squad], official: dict[str, dict]) -> tuple[list[str], dict[str, int]]:
    """Our squads against the official purse table. Returns every disagreement in places or
    overseas places, and the full purse each fully-priced team implies (spent + left) --
    one league purse means those must all agree, so a team that differs is a salary we
    have wrong, not a different purse."""
    problems, implied = [], {}
    for short, s in sq.items():
        o = official.get(short)
        if o is None:
            problems.append(f"{short}: no official purse figures")
            continue
        if s.slots != int(o["slots"]):
            problems.append(f"{short}: {s.slots} places left by our squad, {o['slots']} officially")
        if s.overseas_slots != int(o["overseas_slots"]):
            problems.append(f"{short}: {s.overseas_slots} overseas places left by our squad, {o['overseas_slots']} officially")
        if s.players and not s.unpriced:
            implied[short] = s.spent_lakh + int(o["purse_left_lakh"])
    totals = sorted(set(implied.values()))
    if len(totals) > 1:
        shared = [t for t in totals if sum(v == t for v in implied.values()) >= 2]
        if len(shared) == 1:          # most teams agree on one purse: the odd ones out have a salary wrong
            for short, t in implied.items():
                if t != shared[0]:
                    problems.append(f"{short}: salaries + purse left = Rs {t / 100:g} cr against the "
                                    f"Rs {shared[0] / 100:g} cr other teams imply: a salary is wrong")
        else:                         # no majority: say so, and blame no team in particular
            problems.append("salaries + purse left disagree between teams with no majority ("
                            + ", ".join(f"{k} Rs {v / 100:g} cr" for k, v in implied.items())
                            + "): the source's salaries are not reliable enough to infer the purse")
    return problems, implied


def print_summary(rows: list[dict]) -> None:
    for r in rows:
        r["salary_lakh"] = int(r["salary_lakh"]) if r.get("salary_lakh") else None
    sq = squads(rows)
    official = {r["short"]: r for r in read_override(PURSE_CSV)}
    print(f"\n{len(rows)} retained players. Squads {SQUAD_MIN}-{SQUAD_MAX}, overseas up to {OVERSEAS_MAX}.")
    print(f"  {'team':5} {'kept':>4} {'ovs':>4} {'spent':>8} {'left':>8} {'slots':>5} {'ovs slots':>9}  unresolved")
    for s in sq.values():
        o = official.get(s.team)
        spent = "?" if s.unpriced else f"{s.spent_lakh / 100:.2f}c"   # a partial sum is not a figure
        left = f"{int(o['purse_left_lakh']) / 100:.2f}c" if o else "?"
        print(f"  {s.team:5} {s.players:4} {s.overseas:4} {spent:>8} {left:>8} {s.slots:5} {s.overseas_slots:9}  "
              f"{', '.join(s.unresolved) or '-'}")
    problems, implied = reconcile(sq, official)
    if implied:
        print("  implied full purse: " + ", ".join(f"{k} Rs {v / 100:g} cr" for k, v in implied.items()))
    for p in problems:
        print("  CHECK:", p)
    if not any("places" in p for p in problems):
        print("  places and overseas places agree with the official figures for every team")
    if sum(s.players for s in sq.values()) < 100:
        print("\n  Fewer than 100 retained in all: the lists are probably still being published.")


def do_birth_dates() -> None:
    people = read_override(FULL_NAMES)
    by_cid = {p["cricinfo_id"]: p["person_id"] for p in people if p.get("cricinfo_id")}
    wikidata: dict[str, list[str]] = defaultdict(list)
    cids = list(by_cid)
    for i in range(0, len(cids), 200):
        vals = " ".join(f'"{c}"' for c in cids[i:i + 200])
        q = f"SELECT ?cid ?dob WHERE {{ VALUES ?cid {{ {vals} }} ?p wdt:P2697 ?cid ; wdt:P569 ?dob . }}"
        for b in fetch({"_url": "https://query.wikidata.org/sparql", "query": q, "format": "json"})["results"]["bindings"]:
            d = b["dob"]["value"][:10]
            if d not in wikidata[by_cid[b["cid"]["value"]]]:
                wikidata[by_cid[b["cid"]["value"]]].append(d)
        time.sleep(1)
    by_title = {p["wikipedia"]: p["person_id"] for p in people if p.get("wikipedia")}
    titles, wikipedia = list(by_title), {}
    for i in range(0, len(titles), 20):
        data = fetch({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                      "rvsection": "0", "format": "json", "formatversion": "2", "redirects": "1",
                      "titles": "|".join(titles[i:i + 20])})
        back = {r["to"]: r["from"] for r in data["query"].get("redirects", [])}
        back.update({n["to"]: n["from"] for n in data["query"].get("normalized", [])})
        for pg in data["query"]["pages"]:
            t = back.get(pg["title"], pg["title"])
            txt = pg.get("revisions", [{}])[0].get("slots", {}).get("main", {}).get("content", "")
            m = re.search(r"\{\{\s*birth[ _]date(?:[ _]and[ _]age)?\s*\|(?:[a-z]+=[^|]*\|)*\s*(\d{4})\s*\|"
                          r"\s*(\d{1,2})\s*\|\s*(\d{1,2})", txt, re.I)
            if m and t in by_title:
                wikipedia[by_title[t]] = "%04d-%02d-%02d" % tuple(map(int, m.groups()))
        time.sleep(2)
    rows = birth_rows(people, wikidata, wikipedia)
    columns = ["person_id", "name", "cricinfo_id", "wikidata", "wikipedia", "agree", "birth_date"]
    report_merge(BIRTH_CSV, merge_override(BIRTH_CSV, columns, rows, decided="birth_date"))
    agree = sum(r["agree"] == "yes" for r in rows)
    print(f"  {agree} of {len(rows)} dated by two agreeing sources; "
          f"{sum(1 for r in rows if r['agree'] == 'no' and r['wikidata'] and r['wikipedia'])} disagree "
          f"(left blank for a hand check)")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--retained", action="store_true")
    ap.add_argument("--birth-dates", action="store_true")
    a = ap.parse_args(argv)
    if a.birth_dates:
        do_birth_dates()
    if a.retained:
        do_retained()
    if not (a.birth_dates or a.retained):
        ap.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
