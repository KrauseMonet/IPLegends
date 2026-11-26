"""[A317] Auction history: the parser on hand-built wikitext, the committed CSV against the
official totals, and the pages against the public record."""

from __future__ import annotations

import csv
import html
import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

from tools import almanack_auctions as AU

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "etl" / "overrides" / "auction_history.csv"
AL = ROOT / "web" / "static" / "almanack"


def rows() -> list[dict]:
    return list(csv.DictReader(open(CSV, newline="")))


# --------------------------------------------------------------------------- money and names

@pytest.mark.parametrize("amount,cur,want", [
    (1625, "inr_lakh", "₹16.25 cr"), (2475, "inr_lakh", "₹24.75 cr"), (100, "inr_lakh", "₹1 cr"),
    (20, "inr_lakh", "₹20 lakh"), (1550000, "usd", "$1.55m"), (750000, "usd", "$750,000"),
    (1500, "usd_thousand", "$1.5m"), (None, "inr_lakh", "-"),
])
def test_money_is_said_in_the_currency_of_the_day(amount, cur, want):
    assert AU.money(amount, cur) == want


@pytest.mark.parametrize("team,want", [
    ("Kings XI Punjab", "Punjab Kings"), ("Delhi Daredevils", "Delhi Capitals"),
    ("Royal Challengers Bangalore", "Royal Challengers Bengaluru"), ("Rising Pune Supergiants", "Rising Pune Supergiant"),
    ("Gujarat Lions", "Gujarat Lions"), ("Gujarat Titans", "Gujarat Titans"), ("Pune Warriors India", "Pune Warriors"),
])
def test_a_team_maps_to_the_archives_franchise(team, want):
    assert AU.franchise_of(team) == want


# --------------------------------------------------------------------------- the parser

mw = pytest.importorskip("mwparserfromhell")


def page(table: str, heading: str = "Sold players") -> str:
    return f"intro\n=={heading}==\n{table}\n==References==\n"


def test_sortnames_third_value_is_the_article():
    """Gujarat Lions' 2016 buy was 'Amit Mishra (cricketer, born 1991)', not the leg-spinner."""
    from etl.auction_history import parse
    t = ('{| class="wikitable"\n! Player !! Team !! Base price !! Winning bid\n|-\n'
         '| {{sortname|Amit|Mishra|Amit Mishra (cricketer, born 1991)}} || [[Gujarat Lions]] || 10 || 10\n|}')
    assert [s["title"] for s in parse(2016, page(t))] == ["Amit Mishra (cricketer, born 1991)"]


def test_a_buy_with_no_article_keeps_the_pages_text():
    from etl.auction_history import parse
    t = ('{| class="wikitable"\n! Name !! Team !! Base price !! Auctioned price\n|-\n'
         '| Plain Name || [[Delhi Capitals]] || 20 || 20\n|}')
    assert [s["title"] for s in parse(2024, page(t))] == ["Plain Name"]


def test_a_salary_table_is_a_draft_not_a_sale():
    from etl.auction_history import parse
    t = '{| class="wikitable"\n! Name !! Team !! Salary\n|-\n| [[Virat Kohli]] || [[Royal Challengers Bangalore]] || 30\n|}'
    assert parse(2008, page(t, "Auction-1")) == []


def test_rowspan_and_colspan_cells_fill_the_row():
    from etl.auction_history import parse
    t = ('{| class="wikitable"\n! Set !! Name !! Base Price !! IPL 2024 Team !! Auctioned Price\n|-\n'
         '| rowspan="2" | BA1\n| [[Harry Brook]]\n| 200\n| [[Delhi Capitals]]\n| 400\n|-\n'
         '| [[Travis Head]]\n| 200\n| [[Sunrisers Hyderabad]]\n| 680\n|-\n'
         '| colspan="2" |ACC [[Sherfane Rutherford]]\n| 150\n| [[Kolkata Knight Riders]]\n| 150\n|}')
    got = [(s["title"], s["team"], s["price"]) for s in parse(2024, page(t))]
    assert got == [("Harry Brook", "Delhi Capitals", "400"), ("Travis Head", "Sunrisers Hyderabad", "680"),
                   ("Sherfane Rutherford", "Kolkata Knight Riders", "150")]


# --------------------------------------------------------------------------- the committed CSV

def totals() -> dict[int, tuple[float, int]]:
    acc: dict[int, list] = defaultdict(lambda: [0.0, 0])
    for r in rows():
        a = acc[int(r["year"])]
        a[0] += float(r.get("price_checked") or r["price"])
        a[1] += 1
    return {y: (t, n) for y, (t, n) in acc.items()}


@pytest.mark.parametrize("year,lakh,sold", [
    (2019, 10680, 60), (2020, 14030, 62),                       # Wikipedia's own stated totals
    (2021, 14530, 57), (2022, 55170, 204), (2023, 16700, 80),   # the IPL's own sold lists,
    (2024, 23045, 72), (2025, 63915, 182), (2026, 21545, 77),   # read 26 November 2026
])
def test_each_season_matches_the_official_count_and_total(year, lakh, sold):
    assert totals()[year] == (lakh, sold)


def test_a_season_bid_in_dollars_stays_in_dollars():
    for r in rows():
        y = int(r["year"])
        assert r["currency"] == ("usd_thousand" if y == 2008 else "usd" if y <= 2013 else "inr_lakh"), r


def test_every_correction_and_addition_says_where_it_came_from():
    for r in rows():
        if r.get("price_checked") or r.get("person_id_checked"):
            assert r.get("note"), r


def test_a_linked_buy_who_played_that_season_played_for_the_buyer():
    """The cross-check the archive offers: a mismatch is a wrong link (A317's Amit Mishra)."""
    for r in rows():
        if r["person_id"] and r["played_for"] and not r.get("person_id_checked"):
            clubs = r["played_for"].split("; ")
            assert AU.franchise_of(r["team"]) in clubs, r


def test_reading_prefers_the_checked_price_and_the_checked_person(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("year,title,name,team,base,price,currency,person_id,played_for,person_id_checked,price_checked,note\n"
                 "2024,X,X,Gujarat Titans,20,500,inr_lakh,aaa,,,60,n\n"
                 "2024,Y,Y,Mumbai Indians,20,20,inr_lakh,bbb,,none,,n\n"
                 "2024,Z,Z,Mumbai Indians,20,20,inr_lakh,ccc,,ddd,,n\n")
    s = AU.read_sales(p)
    assert (s[0].price, s[1].person, s[2].person) == (60, None, "ddd")


# --------------------------------------------------------------------------- the pages

def text(path: Path) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", path.read_text())))


@pytest.mark.parametrize("year,fact", [
    (2008, "The dearest buy was MS Dhoni , to Chennai Super Kings for $1.5m."),
    (2011, "The dearest buy was Gautam Gambhir , to Kolkata Knight Riders for $2.4m."),
    (2024, "sold 72 players for ₹230.45 cr in all. The dearest buy was Mitchell Starc , to Kolkata Knight Riders for ₹24.75 cr."),
    (2025, "sold 182 players for ₹639.15 cr in all. The dearest buy was Rishabh Pant , to Lucknow Super Giants for ₹27 cr."),
    (2026, "sold 77 players for ₹215.45 cr in all. The dearest buy was Cameron Green , to Kolkata Knight Riders for ₹25.2 cr."),
])
def test_season_pages_match_the_public_record(year, fact):
    assert fact in text(AL / "auctions" / f"{year}.html")


def test_a_player_page_lists_the_players_auctions():
    page = (AL / "players" / "pat-cummins.html").read_text().split("At the auction", 1)[1].split("</table>", 1)[0]
    got = re.findall(r'/almanack/auctions/(\d+)">\d+</a></td><td>.*?</td><td class=n>[^<]*</td><td class=n><b>([^<]+)</b>', page)
    assert got == [("2014", "₹1 cr"), ("2017", "₹4.5 cr"), ("2018", "₹5.4 cr"), ("2020", "₹15.5 cr"),
                   ("2022", "₹7.25 cr"), ("2024", "₹20.5 cr")]


def test_there_is_a_page_for_every_season_and_the_routes_serve_them():
    assert sorted(p.stem for p in (AL / "auctions").glob("*.html")) == [str(y) for y in range(2008, 2027)]
    from fastapi.testclient import TestClient
    from web.app import app
    c = TestClient(app)
    assert c.get("/almanack/auctions").status_code == 200
    assert c.get("/almanack/auctions/2024").status_code == 200
    assert c.get("/almanack/auctions/1999").status_code == 404
    srcs = {r["src"] for r in json.loads((ROOT / "vercel.json").read_text())["routes"]}
    assert "/almanack/auctions/(20[0-9][0-9])" in srcs
