"""The Almanack, phase three [A317]: every IPL auction from 2008 to 2026, and what each buy did.

Reads `etl/overrides/auction_history.csv` (written by `etl.auction_history` from Wikipedia's
season pages) and nothing on the network, so `tools.build_almanack --check` stays a pure
function of the repository and the local database.

Prices are `price_checked` where a human corrected the source (the IPL's own sold lists, read
for 2021-2026, found six wrong prices and two missing sales in Wikipedia's tables), else `price`.

A sale is joined to what the player then did FOR THE FRANCHISE THAT BOUGHT HIM IN THAT SEASON --
matches, runs, wickets and the season's card -- through `person_id_checked` where a human
decided it, else the linked `person_id`. A sale that names nobody in the archive (a buy who
never played) is listed with Wikipedia's name and nothing after it.

Prices stay in the currency the source gives (A317): dollars to 2013, rupees from 2014. No
table ranks across the two, because doing so would need an exchange rate of our choosing.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from statistics import median

from tools import build_history as H
from tools.build_history import esc, para, plural, section, table

CSV_PATH = H.ROOT / "etl" / "overrides" / "auction_history.csv"
VALUE_MATCHES = 7       # "best value" counts buys who played at least this many matches that season
DEAREST = 10            # rows in "the dearest buys"
BEST_VALUE = 5          # rows in "best value"

TEAM_WORDS = [("Chennai", "Chennai Super Kings"), ("Mumbai", "Mumbai Indians"), ("Kolkata", "Kolkata Knight Riders"),
              ("Royal Challengers", "Royal Challengers Bengaluru"), ("Delhi", "Delhi Capitals"),
              ("Punjab", "Punjab Kings"), ("Kings XI", "Punjab Kings"), ("Rajasthan", "Rajasthan Royals"),
              ("Sunrisers", "Sunrisers Hyderabad"), ("Deccan", "Deccan Chargers"), ("Gujarat Lions", "Gujarat Lions"),
              ("Gujarat Titans", "Gujarat Titans"), ("Rising Pune", "Rising Pune Supergiant"),
              ("Pune Warriors", "Pune Warriors"), ("Kochi", "Kochi Tuskers Kerala"), ("Lucknow", "Lucknow Super Giants")]


def franchise_of(team: str) -> str | None:
    """A team as Wikipedia names it -> the archive's canonical franchise."""
    for word, canon in TEAM_WORDS:
        if word in team:
            return canon
    return None


@dataclass
class Sale:
    year: int
    name: str            # Wikipedia's article title, or the page's text where there is none
    team: str            # as Wikipedia names it
    franchise: str
    base: float | None
    price: float
    currency: str        # usd_thousand | usd | inr_lakh
    person: str | None

    @property
    def amount(self) -> float:
        """In the era's own unit: dollars to 2013, lakh from 2014."""
        return self.price * 1000 if self.currency == "usd_thousand" else self.price


def money(amount: float | None, currency: str) -> str:
    if amount is None:
        return "-"
    if currency == "inr_lakh":
        if amount >= 100:
            return f"₹{amount / 100:.2f}".rstrip("0").rstrip(".") + " cr"
        return f"₹{amount:g} lakh"
    dollars = amount * 1000 if currency == "usd_thousand" else amount
    return f"${dollars / 1e6:.2f}".rstrip("0").rstrip(".") + "m" if dollars >= 1e6 else f"${dollars:,.0f}"


def read_sales(path=CSV_PATH) -> list[Sale]:
    out = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            checked = (r.get("person_id_checked") or "").strip()
            person = None if checked == "none" else (checked or r["person_id"] or None)
            title = r["title"]
            name = title.split(" (")[0] if title.endswith(")") else title
            out.append(Sale(int(r["year"]), name, r["team"], franchise_of(r["team"]) or r["team"],
                            float(r["base"]) if r["base"] else None, float(r.get("price_checked") or r["price"]),
                            r["currency"], person))
    return out


class Auctions:
    def __init__(self, a, sales: list[Sale]):
        self.a, self.rec = a, a.rec
        self.sales = sales
        self.by_year: dict[int, list[Sale]] = defaultdict(list)
        for s in sales:
            self.by_year[s.year].append(s)
        for v in self.by_year.values():
            v.sort(key=lambda s: (-s.amount, s.name))
        self.by_person: dict[str, list[Sale]] = defaultdict(list)
        for s in sales:
            if s.person:
                self.by_person[s.person].append(s)

    # ---------------------------------------------------------------- joins
    def fs(self, s: Sale) -> int | None:
        return self.rec.fs_of.get((s.franchise, s.year))

    def season(self, s: Sale) -> dict | None:
        """What the buy did for the buying franchise that season, or None."""
        fs = self.fs(s)
        c = self.a.careers.get(s.person) if s.person else None
        if c is None or fs is None:
            return None
        return next((x for x in c.seasons if x["fs"] == fs), None)

    def card(self, s: Sale) -> int | None:
        fs = self.fs(s)
        card = self.a.p.cards.get((s.person, fs)) if s.person and fs else None
        return card.display if card is not None else None

    def team(self, s: Sale) -> str:
        fs = self.fs(s)
        return self.rec.team_link(fs) if fs is not None else esc(s.team)

    def who(self, s: Sale) -> str:
        return self.a.link(s.person) if s.person in self.a.careers else esc(s.name)

    def did(self, s: Sale) -> list[str]:
        se = self.season(s)
        if se is None:
            return ["-", "-", "-", "-"]   # card, matches, runs, wickets
        card = self.card(s)
        # The card first: on a narrow screen it is the column still in view beside the price.
        return [f"<b>{card}</b>" if card else "-", str(se["matches"]),
                f"{se['bat']['runs']:,}" if se["bat"]["inns"] else "-",
                str(se["bowl"]["wickets"]) if se["bowl"]["balls"] else "-"]

    # ---------------------------------------------------------------- pages
    def year_page(self, year: int) -> str:
        sales = self.by_year[year]
        cur = sales[0].currency
        total = sum(s.amount for s in sales)
        top = sales[0]
        lead = [f"The {year} auction, held before the {year} season, sold {plural(len(sales), 'player')} for "
                f"{money(total, cur if cur != 'usd_thousand' else 'usd')} in all.",
                f"The dearest buy was {self.who(top)}, to {esc(top.franchise)} for {money(top.price, top.currency)}."]
        if cur != "inr_lakh":
            lead.append("Auctions to 2013 were conducted in US dollars, and the prices here are as they were bid.")
        body = section("The sale", para(*lead))

        spend: dict[str, list[Sale]] = defaultdict(list)
        for s in sales:
            spend[s.franchise].append(s)
        rows = []
        for f, ss in sorted(spend.items(), key=lambda kv: -sum(s.amount for s in kv[1])):
            dear = max(ss, key=lambda s: s.amount)
            rows.append([self.team(dear), str(len(ss)), f"<b>{money(sum(s.amount for s in ss), cur if cur != 'usd_thousand' else 'usd')}</b>",
                         f"{self.who(dear)} ({money(dear.price, dear.currency)})"])
        body += section("By franchise", table(["Franchise", "Bought", "Spent", "Dearest buy"], rows, num_from=4))

        dear = sales[:DEAREST]
        rows = [[self.who(s), self.team(s), f"<b>{money(s.price, s.currency)}</b>"] + self.did(s) for s in dear]
        body += section("How the dearest buys went", table(["Player", "Bought by", "Price", "Card", "M", "Runs", "Wkts"],
                                                           rows, num_from=3)
                        + '<p class="hx-note">What each player did that season for the franchise that made the buy: '
                          "matches, runs, wickets and the season's card on this game's 72-99 scale "
                          '(<a href="/guides/ratings">how it is worked out</a>). A dash means no match that season.</p>')

        mid = median(s.amount for s in sales)
        cheap = [s for s in sales if s.amount <= mid and (self.season(s) or {}).get("matches", 0) >= VALUE_MATCHES
                 and self.card(s)]
        best = sorted(cheap, key=lambda s: (-self.card(s), s.amount, s.name))[:BEST_VALUE]
        if best:
            rows = [[self.who(s), self.team(s), money(s.price, s.currency)] + self.did(s) for s in best]
            body += section("Best value", para(
                f"The highest season cards among buys that cost no more than the auction's median price "
                f"({money(mid, cur if cur != 'usd_thousand' else 'usd')}) and played at least {VALUE_MATCHES} matches "
                "that season.") + table(["Player", "Bought by", "Price", "Card", "M", "Runs", "Wkts"], rows, num_from=3))

        rows = [[self.who(s), self.team(s), money(s.base, s.currency) if s.base is not None else "-",
                 f"<b>{money(s.price, s.currency)}</b>"] + self.did(s) for s in sales]
        body += section("Every sale", table(["Player", "Bought by", "Base", "Price", "Card", "M", "Runs", "Wkts"], rows,
                                            num_from=4)) + _source_note()
        years = sorted(self.by_year)
        i = years.index(year)
        nav = "".join(f'<a class="go ghost" href="/almanack/auctions/{y}">{y}</a>'
                      for y in (years[i - 1] if i else None, years[i + 1] if i + 1 < len(years) else None) if y)
        body += f'    <p class="hx-pager actions">{nav}<a class="go ghost" href="/almanack/auctions">Every auction</a></p>\n'
        return _page(f"/almanack/auctions/{year}", f"IPL {year} auction: every sale and how the buys went",
                     f"Every player sold at the IPL {year} auction: prices, franchise spending, and what the dearest "
                     "buys and the best-value buys did that season.", "ALMANACK · AUCTION", f"The {year} auction", body)

    def index_page(self) -> str:
        rows = []
        for y in sorted(self.by_year):
            ss = self.by_year[y]
            cur = ss[0].currency
            rows.append([f'<a href="/almanack/auctions/{y}">{y}</a>', str(len(ss)),
                         money(sum(s.amount for s in ss), cur if cur != "usd_thousand" else "usd"),
                         f"{self.who(ss[0])} ({money(ss[0].price, ss[0].currency)})"])
        body = section("Every auction", para(
            f"Every player sold at an IPL auction from {min(self.by_year)} to {max(self.by_year)}: "
            f"{len(self.sales):,} sales, each joined to what the player then did for the franchise that bought them.",
            "The early auctions were bid in US dollars and the later ones in rupees; each is shown in its own "
            "currency and never converted.")
            + table(["Season", "Sold", "Total", "Dearest buy"], rows, num_from=1))
        for title, keep in (("The dearest buys in rupees, 2014 on", lambda s: s.currency == "inr_lakh"),
                            ("The dearest buys in dollars, 2008 to 2013", lambda s: s.currency != "inr_lakh")):
            top = sorted((s for s in self.sales if keep(s)), key=lambda s: (-s.amount, s.year, s.name))[:15]
            rows = [[self.who(s), str(s.year), self.team(s), f"<b>{money(s.price, s.currency)}</b>"] + self.did(s) for s in top]
            body += section(title, table(["Player", "Season", "Bought by", "Price", "Card", "M", "Runs", "Wkts"], rows,
                                         num_from=3))
        body += _source_note() + '    <p class="hx-pager actions"><a class="go ghost" href="/almanack">The Almanack</a></p>\n'
        return _page("/almanack/auctions", "Every IPL auction, 2008 to 2026", "Every IPL auction since 2008: what each "
                     "season's buys cost, the dearest players ever bought, and what the buys did.", "ALMANACK",
                     "Auctions", body)

    def player_section(self, person: str) -> str:
        ss = sorted(self.by_person.get(person, []), key=lambda s: s.year)
        if not ss:
            return ""
        rows = [[f'<a href="/almanack/auctions/{s.year}">{s.year}</a>', self.team(s),
                 money(s.base, s.currency) if s.base is not None else "-", f"<b>{money(s.price, s.currency)}</b>"]
                + self.did(s)[:2] for s in ss]
        return section("At the auction", table(["Season", "Bought by", "Base", "Price", "Card", "M"], rows, num_from=2)
                       + '<p class="hx-note">Sales at the auction only: a retained player or a trade is not an auction '
                         "sale and is not listed. Prices from Wikipedia's season pages, checked against the IPL's own lists "
                         "for 2021 to 2026, in the currency of the day.</p>")


def _source_note() -> str:
    return ('    <p class="hx-note">Sales and prices from Wikipedia\'s season pages ("List of Indian Premier League '
            'personnel changes"), read on 25 November 2026, and checked sale by sale against the IPL\'s own lists for '
            "2021 to 2026, which corrected six prices and added two sales Wikipedia had missed. Each player is matched "
            "to the archive by article, never by name. Retentions and trades are not auction sales and are not "
            "listed.</p>\n")


def _page(path, title, desc, over, heading, body):
    from tools.build_almanack import page
    return page(path, title, desc, over, heading, body)
