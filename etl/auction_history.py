"""Every IPL auction sale, 2008-2026, from Wikipedia's season pages, into a committed CSV [A317].

    uv run python -m etl.auction_history            # fetch, parse, link, merge into the CSV
    uv run python -m etl.auction_history --offline  # re-parse the cached pages in data/auction_wiki/

The archive holds no prices at all (A229), so the auction record has to come from outside.
Wikipedia keeps a page per season ("List of YYYY Indian Premier League personnel changes";
2008's is "...auctions and personnel signings"), each with a table of every player sold, the
buying franchise, the base price and the price paid. Measured in Phase 0 (2026-11-25): 1,647
priced sales parse, and the counts and totals match the published ones where a page states them
(2014 154; 2018 169; 2019 60 for Rs 106.8 cr; 2020 62 for Rs 140.30 cr; 2022 204; 2026 77 for
Rs 215.45 cr). 2025's tables hold 180 of the 182 the page itself says were sold -- a gap in the
source, not the parser, and said so on the page rather than filled from memory.

CURRENCY IS KEPT AS THE SOURCE GIVES IT, never converted: 2008 in thousands of US dollars,
2009-2013 in US dollars, 2014 on in lakh of rupees. Converting would put an exchange rate of
our choosing under every figure.

A SALE IS LINKED TO A PERSON by Wikipedia title (redirects resolved), through the article
titles `full_names.csv` already holds (A252) -- never by a name string. `person_id` is that
evidence; `person_id_checked` is the decision column (A30): blank means "trust the link",
a person id overrides it, and `none` says the sale is not any player in the archive. A link is
also cross-checked against the archive -- a linked buy who played that season must have played
for the buying franchise -- and `played_for` records what the archive says so a mismatch shows.
"""

from __future__ import annotations

import csv
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "etl" / "overrides" / "auction_history.csv"
CACHE = ROOT / "data" / "auction_wiki"
FULL_NAMES = ROOT / "etl" / "overrides" / "full_names.csv"
UA = {"User-Agent": "FineLegXI-auction-history/1.0"}
API = "https://en.wikipedia.org/w/api.php"
YEARS = range(2008, 2027)
COLUMNS = ["year", "title", "name", "team", "base", "price", "currency", "person_id", "played_for"]
# `note` (why a hand decision was made) is written by hand and, not being generated here, is
# carried across untouched by merge_override (A112).
DECIDED = "person_id_checked"

FRANCHISE_WORDS = re.compile(r"Kings|Riders|Indians|Royals|Daredevils|Capitals|Sunrisers|Chargers|Lions|Titans|"
                             r"Giant|Warriors|Tuskers|Batting|Bowling|cricket\)$|wicket", re.I)


def page_title(year: int) -> str:
    if year == 2008:
        return "List of 2008 Indian Premier League auctions and personnel signings"
    return f"List of {year} Indian Premier League personnel changes"


def currency(year: int) -> str:
    return "usd_thousand" if year == 2008 else "usd" if year <= 2013 else "inr_lakh"


def fetch(year: int) -> str:
    path = CACHE / f"{year}.wiki"
    url = "https://en.wikipedia.org/w/index.php?" + urllib.parse.urlencode({"title": page_title(year), "action": "raw"})
    for attempt in range(5):
        try:
            text = urllib.request.urlopen(urllib.request.Request(url, headers=UA)).read().decode()
            break
        except Exception:
            time.sleep(5 * (attempt + 1))
    else:
        raise SystemExit(f"could not fetch {page_title(year)}")
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return text


# --------------------------------------------------------------------------- parsing

def _mw():
    import mwparserfromhell
    return mwparserfromhell


def _clean(c: str) -> str:
    return re.sub(r'^\s*(?:(?:align|style|data-sort-value|rowspan|colspan|scope)\s*=\s*"?[^|"]*"?\s*)+\|(?!\|)', "", c)


def _text(c: str) -> str:
    return _mw().parse(_clean(c)).strip_code().strip()


def _links(c: str) -> list[str]:
    w = _mw().parse(c)
    out = [str(link.title) for link in w.filter_wikilinks()]
    for t in w.filter_templates():
        if str(t.name).strip().lower() == "sortname":
            ps = [str(p.value).strip() for p in t.params if not p.showkey or str(p.name).strip() in "1234"]
            # {{sortname|First|Last|Article|sort key}}: the THIRD value is the article linked,
            # e.g. "Amit Mishra (cricketer, born 1991)" -- reading the bare name instead linked
            # Gujarat Lions' 2016 buy to the leg-spinner, who was at Delhi that season.
            if len(ps) >= 2:
                out.append(ps[2] if len(ps) >= 3 and ps[2] else f"{ps[0]} {ps[1]}")
    return out


def _cells(row: str) -> list[str]:
    cells: list[str] = []
    for line in row.split("\n")[1:]:
        if line.startswith(("|}", "|+")):
            continue
        if line.startswith(("|", "!")):
            cells += re.split(r"\|\||!!", line[1:])
        elif cells:
            cells[-1] += "\n" + line
    out = []
    for c in cells:
        m = re.search(r'colspan\s*=\s*"?(\d+)', c.split("|")[0] if "|" in c and not c.lstrip().startswith("[[") else "")
        out += [c] * (int(m.group(1)) if m else 1)
    return out


def parse(year: int, raw: str) -> list[dict]:
    """Every priced sale on one season's page: name (Wikipedia title), team, base, price."""
    m = re.search(r"==+\s*(Sold players|Auction-1)\s*==+(.*?)(?=\n==[^=]|\Z)", raw, re.S)
    if not m:
        return []
    sales = []
    for tbl in re.findall(r"\{\|.*?\n\|\}", m.group(2), re.S):
        segs = re.split(r"\n\|-", tbl)
        hi = next((i for i, sg in enumerate(segs) if any(line.startswith("!") for line in sg.split("\n"))), None)
        if hi is None:
            continue
        hdr = [_text(h).lower() for line in segs[hi].split("\n") if line.startswith("!") for h in re.split(r"!!", line[1:])]
        segs = segs[hi + 1:]
        col = lambda *ps: next((i for i, h in enumerate(hdr) if any(re.search(p, h) for p in ps)), None)  # noqa: E731
        # "Salary" heads 2008's Under-19 draft, which was a signing, not an auction sale.
        ip, ib, iname = col(r"auctioned|winning|sold price"), col(r"base|reserve"), col(r"^name$|^player$")
        it = next((i for i, h in enumerate(hdr) if re.search(r"team|franchise", h) and "previous" not in h
                   and (not re.search(r"(19|20)\d\d", h) or str(year) in h) and not re.search(r"team\(s\)", h)), None)
        if ip is None or it is None:
            continue
        carry: dict[int, tuple[int, str]] = {}
        for row in segs:
            lines = row.split("\n")[1:]
            if all(not ln.strip() or ln.startswith(("!", "|}", " class")) for ln in lines):
                continue
            cells = _cells(row)
            full, it_cells = [], iter(cells)
            for i in range(len(hdr)):
                if carry.get(i, (0, ""))[0] > 0:
                    n, v = carry[i]
                    carry[i] = (n - 1, v)
                    full.append(v)
                    continue
                c = next(it_cells, None)
                if c is None:
                    break
                rs = re.search(r'rowspan\s*=\s*"?(\d+)', c)
                if rs:
                    carry[i] = (int(rs.group(1)) - 1, c)
                full.append(c)
            if len(full) != len(hdr) or list(it_cells):
                raise ValueError(f"{year}: a row does not fit its header ({len(cells)} cells, {len(hdr)} columns)")
            if iname is not None:
                # The Name column: its link where the player has an article, its text where not
                # (2020-2024 list a few uncapped buys with no article; they still were sold).
                own = [x for x in _links(full[iname]) if not FRANCHISE_WORDS.search(x)]
                name = own[0] if own else (_text(full[iname]) or None)
            else:
                name = next((ls[0] for c in full for ls in [[x for x in _links(c) if not FRANCHISE_WORDS.search(x)]] if ls), None)
            team_links = _links(full[it])
            team = (team_links[0] if team_links else _text(full[it])).split("|")[0].strip()

            def num(i):
                if i is None:
                    return None
                mm = re.search(r"\d+(?:\.\d+)?", _text(full[i]).replace(",", ""))
                return mm.group() if mm else None

            price = num(ip)
            if name and price:
                sales.append(dict(year=str(year), title=name.split("|")[0].strip(), team=team, base=num(ib) or "",
                                  price=price, currency=currency(year)))
    return sales


# --------------------------------------------------------------------------- linking

def resolve(titles: list[str]) -> dict[str, str]:
    """Each title as Wikipedia's canonical article title (normalised, redirects followed)."""
    out: dict[str, str] = {}
    for i in range(0, len(titles), 50):
        chunk = titles[i:i + 50]
        data = urllib.parse.urlencode({"action": "query", "titles": "|".join(chunk), "redirects": 1,
                                       "format": "json"}).encode()
        for attempt in range(6):
            try:
                q = json.load(urllib.request.urlopen(urllib.request.Request(API, data=data, headers=UA)))["query"]
                break
            except Exception:
                time.sleep(5 * (attempt + 1))
        else:
            raise SystemExit("could not resolve titles on Wikipedia")
        norm = {n["from"]: n["to"] for n in q.get("normalized", [])}
        redir = {n["from"]: n["to"] for n in q.get("redirects", [])}
        for t in chunk:
            n = norm.get(t, t)
            out[t] = redir.get(n, n)
        time.sleep(1.5)
    return out


def people_by_title() -> dict[str, str]:
    out: dict[str, str] = {}
    with open(FULL_NAMES, newline="") as fh:
        for r in csv.DictReader(fh):
            for k in ("wikipedia", "full_name"):
                if r.get(k):
                    out.setdefault(r[k].replace("_", " ").strip().lower(), r["person_id"])
    return out


def played_for(conn) -> dict[tuple[str, int], set[str]]:
    cur = conn.cursor()
    cur.execute("""select sm.person_id, fs.season_year, f.canonical_name from squad_members sm
                   join franchise_seasons fs using (franchise_season_id) join franchises f using (franchise_id)""")
    out: dict[tuple[str, int], set[str]] = {}
    for p, y, f in cur.fetchall():
        out.setdefault((p, y), set()).add(f)
    return out


def main(argv: list[str]) -> int:
    from etl.derive_people import merge_override
    offline = "--offline" in argv
    sales = []
    for y in YEARS:
        raw = (CACHE / f"{y}.wiki").read_text() if offline else fetch(y)
        sales += parse(y, raw)
    canon = {} if offline else resolve(sorted({s["title"] for s in sales}))
    if offline and OUT.exists():
        with open(OUT, newline="") as fh:
            known = {(r["year"], r["name"]): r["title"] for r in csv.DictReader(fh)}
        canon = {s["title"]: known.get((s["year"], s["title"]), s["title"]) for s in sales}
    by_title = people_by_title()
    import psycopg
    from dotenv import load_dotenv
    import os
    load_dotenv(ROOT / ".env")
    with psycopg.connect(os.environ["DIRECT_URL"]) as conn:
        played = played_for(conn)
    rows = []
    for s in sales:
        title = canon.get(s["title"], s["title"])
        pid = by_title.get(title.lower()) or by_title.get(s["title"].lower()) or ""
        clubs = sorted(played.get((pid, int(s["year"])), set())) if pid else []
        rows.append(dict(year=s["year"], title=title, name=s["title"], team=s["team"], base=s["base"],
                         price=s["price"], currency=s["currency"], person_id=pid, played_for="; ".join(clubs)))
    kept, added, removed = merge_override(OUT, COLUMNS + [DECIDED], rows, key=("year", "name", "team"), decided=DECIDED)
    print(f"{len(rows)} sales, {sum(1 for r in rows if r['person_id'])} linked; wrote {OUT.relative_to(ROOT)} "
          f"({kept} kept, {added} added, {removed} not regenerated)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
