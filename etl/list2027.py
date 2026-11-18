"""2027 mini auction: OUR provisional auction list, until the real register lands on 3 Dec.

    uv run python -m etl.list2027          # writes etl/overrides/auction_2027_list.csv

[A297] The real register is published on 3 December 2026. Until then the game plays a list
of our own, built from three sources and labelled as ours everywhere it is shown:

1. **Released** -- everyone who played IPL 2026 and is not in a retained 2027 squad
   (`retained_2027.csv`, A296) and has not retired (Wikipedia's 2027 "Retired players").
2. **Last year's list** -- the official 2026 register's unsold players
   (`unsold_2026_list.txt`, read off iplt20.com), the most direct evidence there is of who
   registers for an IPL auction and whom franchises shortlist.
3. **Newcomers** -- players with no IPL record who did well THIS season: overseas players
   from the T20 leagues played since November 2025 (Cricsheet), and Indian players from the
   2025-26 Syed Mushtaq Ali Trophy's published leader tables (Wikipedia), because Cricsheet's
   SMAT archive stops at 2024-25 and no later Indian domestic T20 is published there.

A newcomer is chosen only with a KNOWN passport (A23): an overseas player's country comes
from the Test/ODI/T20I archives, so an uncapped overseas player cannot be chosen at all, and
Afghanistan (absent from those archives, A30) cannot either. Pakistani players are left out
because none has played the IPL since 2008 (A51).

`include` and `person_id_checked` are decision columns: blank `include` lists the player,
`n` leaves him out; `person_id_checked` overrides or confirms the IPL record a name was
matched to. Everything else is evidence and is refreshed on every run (A30).
"""
from __future__ import annotations

import collections
import csv
import json
import re
import sys
import time
import zipfile
from dataclasses import dataclass, field

from etl.db import data_dir
from etl.derive_people import OVERRIDES, merge_override, national_teams, read_override, report_merge
from game.mini2027 import listed
from etl.mini2027 import FULL_NAMES, PAGE, RETAINED_CSV, fetch, name_index, norm, resolve, title_of

LIST_CSV = OVERRIDES / "auction_2027_list.csv"
UNSOLD_LIST = OVERRIDES / "unsold_2026_list.txt"
SMAT_PAGE = "2025–26 Syed Mushtaq Ali Trophy"

# The other T20 leagues downloaded for this (etl.download, A297) and the season that counts.
LEAGUES = ("bbl", "sat", "ilt", "cpl", "mlc", "psl", "ntb", "bpl", "ssm", "lpl")
SINCE = "2025-11-01"
WICKET_RUNS = 20            # A183's scale: a wicket is worth twenty runs
MIN_BALLS = 120             # a season's evidence: 120 balls faced, or 120 legal balls bowled
OVERSEAS_NEWCOMERS = 30     # how many overseas newcomers the list carries
RECENT_T20I = "2024-01-01"  # an overseas newcomer must have played a T20I since this date:
                            # a county or league veteran with no recent cap is not a
                            # player franchises shortlist, however good his season
# A surname link that turned out wrong on a hand check: England's Jamie Smith is not the
# South African J Smith of SA20.
NOT_LINKED = frozenset({"Jamie Smith"})
NAME_PREFIXES = frozenset({"md", "mohd", "mohammad", "mohammed", "muhammad"})
NOT_ELIGIBLE = frozenset({"Pakistan"})   # no Pakistani has played the IPL since 2008 (A51)
HOME = "India"

ROLE = {"batter": "Batter", "bowler": "Bowler", "allrounder": "All-Rounder", "keeper": "Wicketkeeper"}

COLUMNS = ["source", "name", "person_id", "how", "ipl_last", "country", "overseas", "role",
           "capped", "base_2026_lakh", "cricsheet_id", "evidence", "balance", "include",
           "person_id_checked"]


# ---------------------------------------------------------------- league season (pure)

@dataclass
class Line:
    runs: int = 0
    balls: int = 0
    outs: int = 0
    matches: int = 0
    legal: int = 0
    conceded: int = 0
    wickets: int = 0
    leagues: set = field(default_factory=set)


BOWLER_WICKETS_EXCLUDED = {"run out", "retired hurt", "retired out", "obstructing the field"}


def add_match(lines: dict[str, Line], match: dict, league: str) -> None:
    """One match into per-player lines. A wide is not a ball faced (A22); a bowler is charged
    neither byes nor leg-byes (A1) and credited only his own wickets (A36)."""
    info = match["info"]
    reg = info["registry"]["people"]
    for team in info["players"].values():
        for p in team:
            ln = lines.setdefault(reg[p], Line())
            ln.matches += 1
            ln.leagues.add(league)
    for inn in match["innings"]:
        if inn.get("super_over"):
            continue
        for over in inn["overs"]:
            for d in over["deliveries"]:
                ex = d.get("extras", {})
                bat = lines.setdefault(reg[d["batter"]], Line())
                bowl = lines.setdefault(reg[d["bowler"]], Line())
                if "wides" not in ex:
                    bat.runs += d["runs"]["batter"]
                    bat.balls += 1
                if "wides" not in ex and "noballs" not in ex:
                    bowl.legal += 1
                bowl.conceded += d["runs"]["total"] - ex.get("byes", 0) - ex.get("legbyes", 0)
                for w in d.get("wickets", []):
                    if w["player_out"] == d["batter"]:
                        bat.outs += 1
                    if w["kind"] not in BOWLER_WICKETS_EXCLUDED:
                        bowl.wickets += 1


def pars(lines: dict[str, Line]) -> tuple[float, float, float]:
    """Runs a ball faced, runs conceded a legal ball and wickets a legal ball, pooled."""
    balls = sum(l.balls for l in lines.values()) or 1
    legal = sum(l.legal for l in lines.values()) or 1
    return (sum(l.runs for l in lines.values()) / balls,
            sum(l.conceded for l in lines.values()) / legal,
            sum(l.wickets for l in lines.values()) / legal)


def impact(ln: Line, par: tuple[float, float, float]) -> float:
    """Runs above par with the bat, plus runs saved and wickets above par with the ball."""
    rpb, cpb, wpb = par
    return (ln.runs - rpb * ln.balls) + (cpb * ln.legal - ln.conceded) + WICKET_RUNS * (ln.wickets - wpb * ln.legal)


def role_of(ln: Line) -> str:
    bats, bowls = ln.balls >= MIN_BALLS, ln.legal >= MIN_BALLS
    return "All-Rounder" if bats and bowls else ("Bowler" if bowls else "Batter")


def evidence(ln: Line) -> str:
    bits = [f"{ln.matches} m ({', '.join(sorted(ln.leagues)).upper()})"]
    if ln.balls:
        bits.append(f"{ln.runs} runs off {ln.balls}" + (f", SR {100 * ln.runs / ln.balls:.0f}" if ln.balls else ""))
    if ln.legal:
        bits.append(f"{ln.wickets} wkt{'' if ln.wickets == 1 else 's'}, econ {6 * ln.conceded / ln.legal:.2f}")
    return "; ".join(bits)


def pick_newcomers(lines: dict[str, Line], nationality: dict[str, str], ipl: set[str],
                   taken: set[str], n: int, recent: set[str]) -> list[str]:
    """The n best non-IPL overseas players of the season with a known, eligible passport
    and a T20I cap since RECENT_T20I."""
    par = pars(lines)
    pool = [pid for pid, ln in lines.items()
            if pid not in ipl and pid not in taken and pid in recent
            and nationality.get(pid) and nationality[pid] != HOME and nationality[pid] not in NOT_ELIGIBLE
            and (ln.balls >= MIN_BALLS or ln.legal >= MIN_BALLS)]
    return sorted(pool, key=lambda p: -impact(lines[p], par))[:n]


# ---------------------------------------------------------------- the list's other parts (pure)

def parse_unsold(text: str) -> list[dict]:
    """name|role|capped|base crore|overseas lines, '#' comments skipped."""
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, role, capped, base, ovs = (line.split("|") + [""] * 5)[:5]
        rows.append(dict(name=name.strip(), role=role.strip(), capped="yes" if capped == "Y" else "no",
                         base_2026_lakh=round(float(base) * 100), overseas="yes" if ovs == "O" else "no"))
    return rows


def parse_retired(wikitext: str) -> set[str]:
    """Article titles in the 2027 page's 'Retired players' table."""
    m = re.search(r"\|\+\s*Retired players(.*?)\n\|\}", wikitext, re.S)
    return {t for ln in (m.group(1).splitlines() if m else []) if "sortname" in ln or "[[" in ln
            for t in [title_of(ln)] if t}


def name_key(name: str) -> str:
    """A full name for comparison: ascii letters only, a leading Md/Mohammad dropped."""
    words = [norm(w) for w in re.split(r"[\s.\-]+", name) if norm(w)]
    if len(words) >= 3 and words[0] in NAME_PREFIXES:
        words = words[1:]
    return "".join(words)


def surname_link(name: str, candidates: dict[str, str]) -> str:
    """A registry id for a full name whose surname matches exactly one candidate's (archive
    names are initials plus surname) and whose first letter agrees. Blank if not unique."""
    words = [norm(w) for w in re.split(r"[\s.\-]+", name) if norm(w)]
    if len(words) >= 3 and words[0] in NAME_PREFIXES:   # "Md Shoriful Islam" is archive "Shoriful Islam"
        words = words[1:]
    if not words:
        return ""
    hits = [pid for pid, cname in candidates.items()
            if [norm(w) for w in cname.split()][-1:] == words[-1:] and cname[:1].lower() == words[0][:1]]
    return hits[0] if len(hits) == 1 else ""


# ---------------------------------------------------------------- build

def league_lines() -> dict[str, Line]:
    lines: dict[str, Line] = {}
    for lg in LEAGUES:
        with zipfile.ZipFile(data_dir() / f"{lg}_male_json.zip") as zf:
            for n in zf.namelist():
                if n.endswith(".json"):
                    match = json.loads(zf.read(n))
                    if match["info"]["dates"][0] >= SINCE:
                        add_match(lines, match, lg)
    return lines


def recent_t20i() -> set[str]:
    """Registry ids of everyone who has played a T20I since RECENT_T20I."""
    out: set[str] = set()
    with zipfile.ZipFile(data_dir() / "t20s_male_json.zip") as zf:
        for n in zf.namelist():
            if n.endswith(".json"):
                info = json.loads(zf.read(n))["info"]
                if info["dates"][0] >= RECENT_T20I:
                    out.update(info["registry"]["people"].values())
    return out


def register_names() -> dict[str, tuple[str, str]]:
    """registry id -> (archive name, cricinfo id) from Cricsheet's people register."""
    with open(data_dir() / "people.csv", encoding="utf-8") as f:
        return {r["identifier"]: (r["name"], r["key_cricinfo"]) for r in csv.DictReader(f)}


def full_names_for(cricinfo: dict[str, str]) -> dict[str, str]:
    """registry id -> English Wikipedia title via Wikidata P2697 (A252), disambiguator dropped."""
    out, ids = {}, [c for c in cricinfo.values() if c]
    back = {c: pid for pid, c in cricinfo.items()}
    for i in range(0, len(ids), 100):
        vals = " ".join(f'"{c}"' for c in ids[i:i + 100])
        q = (f"SELECT ?cid ?t WHERE {{ VALUES ?cid {{ {vals} }} ?p wdt:P2697 ?cid . "
             f"?a schema:about ?p ; schema:isPartOf <https://en.wikipedia.org/> ; schema:name ?t . }}")
        for b in fetch({"_url": "https://query.wikidata.org/sparql", "query": q, "format": "json"})["results"]["bindings"]:
            out[back[b["cid"]["value"]]] = re.sub(r"\s*\(.*\)$", "", b["t"]["value"])
        time.sleep(1)
    return out


# [A299] SMAT 2025-26 form, read off every match summary on the season's Wikipedia page: each
# innings names its top scorer and its leading wicket-taker, and each match its Player of
# the Match. A showing is one of those three, good enough to be worth a scout's note; a
# player with SMAT_MIN_SHOWINGS of them joins the list. Declared, not fitted -- the page
# records only each innings' best, so a steady 30 every match is invisible here.
SMAT_KNOCK_RUNS, SMAT_KNOCK_SR = 40, 130.0   # a top score of 40+ at a strike rate of 130+
SMAT_HAUL_WKTS, SMAT_HAUL_ECON = 3, 8.0      # leading the attack with 3+ for 8 an over or less
SMAT_MIN_SHOWINGS = 2
# SMAT page names that are a listed player under another spelling, checked by hand: the
# page links K.S. Bharat by a redirect, last year's register misspells Thyagarajan, and it
# writes two names in full or with initials where the page does not.
SMAT_ALIASES = {"Srikar Bharat": "K.S. Bharat", "Tanay Thyagarajan": "Tanay Thyagarajann",
                "Satyanarayana Raju": "PV.Satyanarayana Raju",
                "Vishvaraj Jadeja": "Vishvarajsinh Jadeja"}
# The Plate group plays only itself, against the weakest sides in the country, so a
# showing there is not counted.
SMAT_PLATE = frozenset({"man", "miz", "meg", "nag", "sik", "ap"})
SMAT_TEAMS = {
    "mum": "Mumbai", "vid": "Vidarbha", "and": "Andhra", "ker": "Kerala", "rai": "Railways",
    "ass": "Assam", "chh": "Chhattisgarh", "odi": "Odisha", "mp": "Madhya Pradesh",
    "up": "Uttar Pradesh", "cha": "Chandigarh", "jak": "Jammu and Kashmir", "hyd": "Hyderabad",
    "mah": "Maharashtra", "goa": "Goa", "bih": "Bihar", "bar": "Baroda", "ben": "Bengal",
    "guj": "Gujarat", "pun": "Punjab", "har": "Haryana", "hp": "Himachal Pradesh",
    "ser": "Services", "pon": "Puducherry", "del": "Delhi", "sau": "Saurashtra",
    "raj": "Rajasthan", "jha": "Jharkhand", "kar": "Karnataka", "tn": "Tamil Nadu",
    "utt": "Uttarakhand", "tri": "Tripura", "man": "Manipur", "miz": "Mizoram",
    "meg": "Meghalaya", "nag": "Nagaland", "sik": "Sikkim", "ap": "Arunachal Pradesh",
}


@dataclass
class SmatForm:
    """One player's 2025-26 SMAT showings, as the match summaries record them."""
    name: str
    title: str = ""            # his Wikipedia article, where the page links one
    team: str = ""
    knocks: int = 0            # top scores of SMAT_KNOCK_RUNS+ at SMAT_KNOCK_SR+
    runs: int = 0              # in every innings he top-scored, qualifying or not
    balls: int = 0
    tops: int = 0
    hauls: int = 0             # leading the attack with SMAT_HAUL_WKTS+ at SMAT_HAUL_ECON or less
    wickets: int = 0           # in every innings he led the attack
    overs_balls: int = 0
    conceded: int = 0
    leads: int = 0
    poms: int = 0

    @property
    def showings(self) -> int:
        return self.knocks + self.hauls + self.poms

    def role(self) -> str:
        if self.tops and self.leads:
            return "All-Rounder"
        return "Bowler" if self.leads else "Batter"

    def evidence(self) -> str:
        bits = []
        if self.tops:
            bits.append(f"top-scored {_times(self.tops)}, {self.runs} runs off {self.balls}")
        if self.leads:
            econ = self.conceded * 6 / self.overs_balls if self.overs_balls else 0.0
            bits.append(f"led the attack {_times(self.leads)}, {self.wickets} wkts at {econ:.2f} an over")
        if self.poms:
            bits.append(f"Player of the Match {_times(self.poms)}")
        return f"SMAT 2025-26 for {SMAT_TEAMS.get(self.team, self.team)}: " + "; ".join(bits)


def _times(n: int) -> str:
    return {1: "once", 2: "twice"}.get(n, f"{n} times")


def _overs_to_balls(overs: str) -> int:
    whole, _, part = overs.partition(".")
    return int(whole) * 6 + (int(part) if part else 0)


def _linked(cell: str) -> tuple[str, str, str]:
    """(shown name, article title, the rest) from '[[Title|Name]] 61 (30)' or 'Name 61 (30)'."""
    m = re.match(r"\s*\[\[([^\]|]+)(?:\|([^\]]+))?\]\]\s*(.*)", cell)
    if m:
        return (m.group(2) or m.group(1)).strip(), m.group(1).strip(), m.group(3)
    m = re.match(r"\s*([^\d\[<]+?)\s+(\d.*)", cell)
    return (m.group(1).strip(), "", m.group(2)) if m else ("", "", cell)


def smat_form(wikitext: str) -> dict[str, SmatForm]:
    """Every player the 2025-26 SMAT page's match summaries name, with his showings. Plate
    group matches are skipped (SMAT_PLATE). A player's team is the one he played for."""
    out: dict[str, SmatForm] = {}

    def form(name: str, title: str, team: str) -> SmatForm:
        f = out.setdefault(name, SmatForm(name))
        f.title, f.team = f.title or title, f.team or team
        return f

    for block in re.findall(r"\{\{Single-innings cricket match collapsible(.*?)\n\}\}", wikitext, re.S):
        field_ = dict(re.findall(r"\|\s*(\w+)\s*=\s*(.*)", block))
        code = {k: (re.search(r"cr-IND\|(\w+)", field_.get(f"team{k}", "")) or [None, ""])[1] for k in "12"}
        if code["1"] in SMAT_PLATE or code["2"] in SMAT_PLATE:
            continue
        for side, other in (("1", "2"), ("2", "1")):
            for cell in re.split(r"<br\s*/?>", field_.get("runs" + side, "")):
                name, title, rest = _linked(cell)
                m = re.match(r"(\d+)\*?\s*\((\d+)\)", rest.strip())
                if name and m:
                    f, r, b = form(name, title, code[side]), int(m.group(1)), int(m.group(2))
                    f.tops, f.runs, f.balls = f.tops + 1, f.runs + r, f.balls + b
                    f.knocks += r >= SMAT_KNOCK_RUNS and b and 100 * r / b >= SMAT_KNOCK_SR
            for cell in re.split(r"<br\s*/?>", field_.get("wickets" + side, "")):
                name, title, rest = _linked(cell)
                m = re.match(r"(\d+)/(\d+)\s*\(([\d.]+)\s*overs?\)", rest.strip())
                if name and m:
                    # team1's bowlers are named under wickets2: they bowled at team2
                    f = form(name, title, code[other])
                    w, c, bb = int(m.group(1)), int(m.group(2)), _overs_to_balls(m.group(3))
                    f.leads, f.wickets, f.conceded, f.overs_balls = f.leads + 1, f.wickets + w, f.conceded + c, f.overs_balls + bb
                    f.hauls += w >= SMAT_HAUL_WKTS and bb and c * 6 / bb <= SMAT_HAUL_ECON
        name, title, _ = _linked(re.sub(r"<small>.*", "", field_.get("motm", "")))
        if name:
            form(name, title, "").poms += 1
    return out


def balance_cut(rows: list[dict], places: int, os_places: int) -> int:
    """How many Indian lots the list keeps so each passport has as many lots a place [A299]:
    Indian lots / Indian places = overseas lots / overseas places."""
    os_lots = sum(r["overseas"] == "yes" for r in rows)
    return round(os_lots * (places - os_places) / os_places)


def balance(rows: list[dict], places: int, os_places: int) -> None:
    """Mark Indian lots past the balance `out` in the generated `balance` column, the
    weakest evidence first: last year's unsold with no IPL record and no SMAT form, by lowest
    2026 base, last on the register. A hand `include = y` still lists one (main reads it)."""
    keep = balance_cut(rows, places, os_places)
    indian = [i for i, r in enumerate(rows) if r["overseas"] != "yes"]

    def strength(i: int) -> tuple:
        r = rows[i]
        return (r["source"] != "unsold", bool(r["person_id"]), r["evidence"].startswith("SMAT"),
                int(r["base_2026_lakh"] or 0), -i)

    ranked = sorted(indian, key=strength, reverse=True)
    for n, i in enumerate(ranked):
        rows[i]["balance"] = "" if n < keep else "out"
    for r in rows:
        r.setdefault("balance", "")


SMAT_TABLE_ROLE = (          # which statistics table a name comes from says what he does
    ("Most dismissals", "Wicketkeeper"), ("Most Wickets", "Bowler"), ("Best bowling", "Bowler"),
    ("Most Runs", "Batter"), ("Highest Individual", "Batter"), ("Most catches", ""),
)


def smat_leaders(wikitext: str) -> list[tuple[str, str]]:
    """(name, role) for every name in the 2025-26 SMAT page's individual statistics tables;
    a name in several tables takes the first role found in SMAT_TABLE_ROLE's order."""
    i, j = wikitext.find("=== Individual statistics"), wikitext.find("=== Team statistics")
    sec = wikitext[i:j] if i >= 0 else ""
    found: dict[str, list[str]] = {}
    for part in re.split(r"\n====\s*", sec)[1:]:
        heading = part.split("====")[0]
        role = next((r for h, r in SMAT_TABLE_ROLE if h.lower() in heading.lower()), "")
        for n in re.findall(r'style="text-align: left;"\s*\|\s*(?:\[\[(?:[^|\]]*\|)?)?([^\[\]|<]+?)(?:\]\])?\s*<small>', part):
            found.setdefault(n.strip(), []).append(role)
    order = [r for _, r in SMAT_TABLE_ROLE]
    return [(n, min(rs, key=order.index)) for n, rs in found.items()]


def build() -> list[dict]:
    from tools.snapshot_deck import deck_from, read_document
    deck = deck_from(read_document())
    last: dict[str, object] = {}
    for c in sorted((c for cs in deck.cards_by_fs.values() for c in cs), key=lambda c: c.season_year):
        last[c.person_id] = c
    fn = read_override(FULL_NAMES)
    shown = {r["person_id"]: r["full_name"] or r["name"] for r in fn}
    by_title, by_name = name_index(fn)
    retained_rows = read_override(RETAINED_CSV)
    retained = {r["person_id_checked"] or r["person_id"] for r in retained_rows} - {"", "debutant"}
    retained_names = {norm(r["name"]) for r in retained_rows}
    retained_keys = {name_key(r["name"]) for r in retained_rows}
    page = fetch({"action": "parse", "page": PAGE, "prop": "wikitext", "format": "json", "formatversion": "2"})
    retired = {by_title[t] for t in parse_retired(page["parse"]["wikitext"]) if t in by_title}
    decided = {r["name"]: r for r in read_override(LIST_CSV)} if LIST_CSV.exists() else {}

    def card_row(source: str, pid: str, how: str, extra: dict) -> dict:
        c = last[pid]
        return dict(source=source, name=shown.get(pid, c.name), person_id=pid, how=how,
                    ipl_last=f"{c.season_year} {c.franchise}", country="",
                    overseas="" if c.overseas is None else ("yes" if c.overseas else "no"),
                    role=ROLE.get(c.role or "", ""), capped="", base_2026_lakh="", cricsheet_id=pid,
                    evidence=f"last card {c.display}", **extra)

    rows, seen = [], set(retained | retired)
    # 1. released
    for pid, c in sorted(last.items(), key=lambda kv: -(kv[1].display or 0)):
        if c.season_year == 2026 and pid not in seen:
            rows.append(card_row("released", pid, "deck", {}))
            seen.add(pid)
    # 2. last year's list
    for u in parse_unsold(UNSOLD_LIST.read_text(encoding="utf-8")):
        checked = decided.get(u["name"], {}).get("person_id_checked", "")
        pid, how = (checked, "checked") if checked else resolve({"wikipedia": "", "name": u["name"]}, by_title, by_name)
        if pid in seen or norm(u["name"]) in retained_names:
            continue
        if pid and pid in last:
            r = card_row("unsold", pid, how, {})
            # the register's own spelling stays the row's name: it is the row's key, and a
            # hand-checked link must not turn the row into a new one
            r.update(name=u["name"], role=u["role"], capped=u["capped"], base_2026_lakh=u["base_2026_lakh"], overseas=u["overseas"])
            seen.add(pid)
        else:
            r = dict(source="unsold", name=u["name"], person_id="", how=how, ipl_last="", country="",
                     overseas=u["overseas"], role=u["role"], capped=u["capped"],
                     base_2026_lakh=u["base_2026_lakh"], cricsheet_id="", evidence="")
        rows.append(r)
    # 3. newcomers
    lines = league_lines()
    nationality, _ = national_teams(set(lines) | set(last))
    # Capped = has played a Test, ODI or T20I; the archives say so for anyone with an IPL card.
    for r in rows:
        if r["person_id"] and not r["capped"]:
            r["capped"] = "yes" if r["person_id"] in nationality else "no"
    register = register_names()
    # Last year's unsold overseas names are linked to this season's league lines where the
    # surname is unique, both as evidence and so a newcomer is never listed twice.
    pool = {pid: register[pid][0] for pid, ln in lines.items()
            if pid in register and pid not in last and nationality.get(pid, HOME) != HOME}
    par = pars(lines)
    for r in rows:
        if r["source"] == "unsold" and not r["person_id"] and r["overseas"] == "yes":
            cs = "" if r["name"] in NOT_LINKED else surname_link(r["name"], pool)
            if cs:
                r["cricsheet_id"], r["country"] = cs, nationality.get(cs, "")
                r["evidence"] = evidence(lines[cs])
                r["how"] = "surname"
    taken = {r["cricsheet_id"] for r in rows if r["cricsheet_id"]}
    # Twice as many candidates as needed, because a candidate may turn out to be a name on
    # last year's list that the surname link could not settle ("Md Shoriful Islam"): his
    # full name is compared once it is known, and he joins that row instead of being added.
    picks = pick_newcomers(lines, nationality, set(last), taken, 2 * OVERSEAS_NEWCOMERS, recent_t20i())
    titles = full_names_for({p: register[p][1] for p in picks})
    unsold_by_key = {name_key(r["name"]): r for r in rows if r["source"] == "unsold" and not r["cricsheet_id"]}
    added = 0
    for pid in picks:
        ln = lines[pid]
        name = titles.get(pid, register[pid][0])
        if name_key(name) in retained_keys:      # already in a 2027 squad
            continue
        same = unsold_by_key.get(name_key(name))
        if same is not None:
            same.update(cricsheet_id=pid, country=nationality[pid], how="full name",
                        evidence=evidence(ln))
            continue
        if added == OVERSEAS_NEWCOMERS:
            continue
        rows.append(dict(source="newcomer", name=name, person_id="", how="league",
                         ipl_last="", country=nationality[pid], overseas="yes", role=role_of(ln), capped="yes",
                         base_2026_lakh="", cricsheet_id=pid,
                         evidence=f"{evidence(ln)}; {impact(ln, par):+.0f} runs above par"))
        added += 1
    # 4. [A299] SMAT 2025-26 form: a listed Indian gets his figures as evidence; a player
    # with SMAT_MIN_SHOWINGS joins the list. The leader tables count as showings too, and
    # are where a keeper is named.
    smat_text = fetch({"action": "parse", "page": SMAT_PAGE, "prop": "wikitext", "format": "json",
                       "formatversion": "2"})["parse"]["wikitext"]
    forms = smat_form(smat_text)
    # a catches table says nothing about his batting or bowling, so it qualifies nobody
    leaders = {n: role for n, role in smat_leaders(smat_text) if role}
    for name in leaders:                       # a leader-table name the summaries never name
        forms.setdefault(name, SmatForm(name))
    by_key = {name_key(r["name"]): r for r in rows if r["overseas"] != "yes"}
    by_pid = {r["person_id"]: r for r in rows if r["person_id"]}
    for f in sorted(forms.values(), key=lambda f: (-f.showings, -(f.runs + 25 * f.wickets), f.name)):
        checked = decided.get(f.name, {}).get("person_id_checked", "")
        pid, how = (checked, "checked") if checked else resolve({"wikipedia": f.title, "name": f.name},
                                                                 by_title, by_name)
        same = by_key.get(name_key(SMAT_ALIASES.get(f.name, f.name))) or by_pid.get(pid)
        if same is not None:                   # already listed: his SMAT form is evidence,
            if not same["evidence"] or same["evidence"].startswith("last card"):  # over a card
                same["evidence"] = f.evidence() if f.tops or f.leads or f.poms else same["evidence"]
            continue
        leader = f.name in leaders
        if (f.showings < SMAT_MIN_SHOWINGS and not leader) or pid in seen \
                or name_key(f.name) in retained_keys or norm(f.name) in retained_names:
            continue
        if pid and pid in last and last[pid].season_year == 2026:
            continue                           # a 2026 player is retained or released already
        role = leaders.get(f.name) if leaders.get(f.name) == "Wicketkeeper" else f.role()
        r = (card_row("newcomer", pid, how, {}) if pid in last else
             dict(source="newcomer", name=f.name, person_id="", how="smat", ipl_last="",
                  country=HOME, overseas="no", role=role, capped="no", base_2026_lakh="",
                  cricsheet_id=""))
        r.update(name=f.name, how="smat", country=HOME, overseas="no", role=role,
                 evidence=f.evidence() if f.showings or f.tops or f.leads else
                 "a 2025-26 Syed Mushtaq Ali Trophy leader (Wikipedia's statistics tables)")
        rows.append(r)
        by_key[name_key(f.name)] = r
        if pid:
            seen.add(pid)
    purse = read_override(OVERRIDES / "purse_2027.csv")
    balance(rows, sum(int(x["slots"]) for x in purse), sum(int(x["overseas_slots"]) for x in purse))
    return rows


def main(argv: list[str] | None = None) -> None:
    rows = build()
    report_merge(LIST_CSV, merge_override(LIST_CSV, COLUMNS[:-2] + ["include"], rows, key=("source", "name"),
                                          decided="include"))
    kept = [r for r in read_override(LIST_CSV) if listed(r)]
    by = collections.Counter((r["source"], r["overseas"]) for r in kept)
    print(f"  {len(kept)} listed: " + ", ".join(f"{s}/{'overseas' if o == 'yes' else 'Indian' if o == 'no' else '?'} {n}"
                                               for (s, o), n in sorted(by.items())))


if __name__ == "__main__":
    main(sys.argv[1:])
