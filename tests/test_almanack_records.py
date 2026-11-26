"""[A316] The Almanack's record book, grounds and head to head.

The sequence rules (hat-tricks, fastest fifties, partnerships) are tested on hand-built balls
fed through `ordered_pass` exactly as the database would hand them over. The committed pages are
held to the public record where a figure has one.
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import re
from pathlib import Path

import pytest

from tools import almanack_records as R

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "web" / "static"
AL = STATIC / "almanack"


class FakeConn:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return self

    def execute(self, *a, **k):
        pass

    def __iter__(self):
        return iter(self.rows)


def ball(bat="A", ns="B", bowler="X", rb=0, rx=0, wides=0, legal=True, credited=False, out=None, kind=None,
         mid="m1", inn=1):
    # (mid, inn, fs, bfs, bat, ns, bowler, rb, rx, wides, legal, credited, pout, kind, scheduled)
    return (mid, inn, 1, 2, bat, ns, bowler, rb, rx, wides, legal, credited, out, kind, 120)


def W(bat, ns="Z", bowler="X"):
    return ball(bat=bat, ns=ns, bowler=bowler, credited=True, out=bat, kind="caught")


def hats(rows):
    return R.ordered_pass(FakeConn(rows)).hat_tricks


# --------------------------------------------------------------------------- hat-tricks

def test_three_wickets_in_three_balls_is_a_hat_trick():
    assert hats([W("A"), W("B"), W("C")]) == [("X", "m1", 2)]


def test_a_wide_between_the_wickets_breaks_it():
    """Rabada, Qualifier 2 2020: caught, caught, a wide, caught -- not in the public record."""
    wide = ball(bat="C", bowler="X", rx=1, wides=1, legal=False)
    assert hats([W("A"), W("B"), wide, W("C")]) == []


def test_a_wicket_off_a_wide_counts():
    """Tambe 2014: the first wicket was a stumping off a wide, and the record counts it."""
    stumped_wide = ball(bat="A", bowler="X", rx=1, wides=1, legal=False, credited=True, out="A", kind="stumped")
    assert hats([stumped_wide, W("B"), W("C")]) == [("X", "m1", 2)]


def test_a_hat_trick_runs_across_overs_through_another_bowlers_balls():
    other = [ball(bowler="Y", rb=1) for _ in range(6)]
    assert hats([W("A"), W("B")] + other + [W("C")]) == [("X", "m1", 2)]


def test_a_run_out_is_not_the_bowlers_and_breaks_it():
    run_out = ball(bat="C", bowler="X", out="C", kind="run out", credited=False)
    assert hats([W("A"), W("B"), run_out, W("D")]) == []


def test_a_fourth_wicket_in_a_row_is_still_one_hat_trick():
    assert len(hats([W("A"), W("B"), W("C"), W("D")])) == 1


# --------------------------------------------------------------------------- fifties and stands

def test_a_fifty_is_reached_on_the_ball_that_crosses_fifty_and_a_wide_is_not_faced():
    rows = [ball(rb=6) for _ in range(8)]                      # 48 off 8
    rows.append(ball(rx=1, wides=1, legal=False))              # a wide: not a ball faced
    rows.append(ball(rb=4))                                    # 52 off 9
    ps = R.ordered_pass(FakeConn(rows))
    assert [(b, r, p) for b, r, p, _m, _f in ps.fifties] == [(9, 52, "A")]


def test_a_partnership_is_the_run_of_balls_with_the_same_two_and_counts_extras():
    rows = [ball(bat="A", ns="B", rb=1), ball(bat="B", ns="A", rb=4), ball(bat="B", ns="A", rx=1, wides=1, legal=False),
            ball(bat="A", ns="B", out="A", kind="bowled", credited=True),
            ball(bat="C", ns="B", rb=6)]
    ps = R.ordered_pass(FakeConn(rows))
    first, second = ps.partnerships
    assert (first.wicket, first.runs, first.balls, {first.a, first.b}) == (1, 6, 3, {"A", "B"})
    assert (second.wicket, second.runs, {second.a, second.b}) == (2, 6, {"B", "C"})


def test_retired_hurt_starts_a_new_stand_at_the_same_wicket():
    rows = [ball(bat="A", ns="B", rb=2), ball(bat="A", ns="B", out="A", kind="retired hurt"),
            ball(bat="C", ns="B", rb=1)]
    ps = R.ordered_pass(FakeConn(rows))
    assert [p.wicket for p in ps.partnerships] == [1, 1]
    assert ps.innings[0].wickets == 0


# --------------------------------------------------------------------------- the committed pages

def records() -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", (AL / "records.html").read_text())))


@pytest.mark.parametrize("fact", [
    "175* (66) Chris Gayle Pune Warriors 23 April 2013",
    "13 balls Yashasvi Jaiswal Kolkata Knight Riders 11 May 2023",
    "30 balls Chris Gayle Pune Warriors 23 April 2013",
    "973 Virat Kohli 2016",
    "6/12 (3.4 ov) Alzarri Joseph Sunrisers Hyderabad 6 April 2019",
    "229 (96) 2nd Virat Kohli & AB de Villiers Gujarat Lions 14 May 2016",
    "287/3 (20 ov) Sunrisers Hyderabad Royal Challengers Bengaluru 15 April 2024",
    "49 (9.4 ov) Royal Challengers Bangalore Kolkata Knight Riders 23 April 2017",
    "146 runs Mumbai Indians Delhi Daredevils 6 May 2017",
    "Super overs (16)",
])
def test_the_record_book_matches_the_public_record(fact):
    assert fact in records()


def test_the_hat_tricks_are_the_public_records_list_in_order():
    """Wikipedia's 'List of Indian Premier League records and statistics', hat-tricks, read
    2026-11-26: the same 23, in date order."""
    part = (AL / "records.html").read_text().split("Hat-tricks (", 1)[1].split("</table>", 1)[0]
    names = [html.unescape(re.sub(r"<[^>]+>", "", n)).strip() for n in re.findall(r"<tr><td[^>]*>(.*?)</td>", part)]
    assert names == ["Lakshmipathy Balaji", "Amit Mishra", "Makhaya Ntini", "Yuvraj Singh", "Rohit Sharma",
                     "Yuvraj Singh", "Praveen Kumar", "Amit Mishra", "Ajit Chandila", "Sunil Narine", "Amit Mishra",
                     "Pravin Tambe", "Shane Watson", "Axar Patel", "Samuel Badree", "Andrew Tye", "Jaydev Unadkat",
                     "Sam Curran", "Shreyas Gopal", "Harshal Patel", "Yuzvendra Chahal", "Rashid Khan",
                     "Yuzvendra Chahal"]


def test_every_venue_name_belongs_to_one_ground_and_a_hand_decision_says_why():
    rows = list(csv.DictReader(open(ROOT / "etl" / "overrides" / "venues.csv")))
    assert len({(r["venue"], r["city"]) for r in rows}) == len(rows)
    for r in rows:
        assert r["ground"] and r["ground_city"], r
        assert r["source"] in ("wikipedia", "hand"), r
        assert (r["source"] == "hand") == bool(r["note"]), r
    one = {r["ground"] for r in rows if r["venue"] in ("Feroz Shah Kotla", "Arun Jaitley Stadium, Delhi")}
    assert one == {"Arun Jaitley Stadium"}
    assert {r["ground"] for r in rows if "Sahara" in r["venue"] or r["venue"].startswith("Maharashtra")} == {"MCA Stadium"}


def test_the_grounds_hold_every_match_once():
    text = (AL / "grounds.html").read_text()
    body = text.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    counts = [int(re.sub(r"<[^>]+>", "", r[2])) for r in
              (re.findall(r"<td[^>]*>(.*?)</td>", tr) for tr in re.findall(r"<tr>(.*?)</tr>", body))]
    assert len(counts) == 36 and sum(counts) == 1243


def test_a_ground_page_is_indexed_exactly_when_it_has_enough_matches_and_the_sitemap_agrees():
    sitemap = (STATIC / "sitemap.xml").read_text()
    for p in (AL / "grounds").glob("*.html"):
        t = p.read_text()
        n = int(re.search(r"has staged ([\d,]+) IPL match", t).group(1))
        noindex = 'content="noindex' in t
        assert noindex == (n < R.GROUND_MIN_MATCHES), p.name
        assert (f"/almanack/grounds/{p.stem}</loc>" in sitemap) == (not noindex), p.name


def test_the_head_to_head_page_names_the_file_by_its_own_hash():
    """/static is cached for a year (A106), so a stale name would serve old pairs for a year."""
    page = (AL / "head-to-head.html").read_text()
    url = re.search(r'data-src="(/static/almanack/h2h\.json\?v=([0-9a-f]+))"', page)
    doc = (AL / "h2h.json").read_text()
    assert url and url.group(2) == hashlib.sha256(doc.encode()).hexdigest()[:10]


def test_the_head_to_head_file_agrees_with_the_player_page():
    doc = json.loads((AL / "h2h.json").read_text())
    names = {p[0]: i for i, p in enumerate(doc["people"])}
    pair = next(p for p in doc["pairs"] if p[0] == names["Virat Kohli"] and p[1] == names["Jasprit Bumrah"])
    kohli = (AL / "players" / "virat-kohli.html").read_text().split("Bowlers faced most", 1)[1]
    row = re.search(r"Jasprit Bumrah</a></td><td class=n>(\d+)</td><td class=n>(\d+)</td><td class=n>[\d.]+</td><td class=n>(\d+)", kohli)
    assert pair[2:] == [int(row.group(1)), int(row.group(2)), int(row.group(3))]
    assert min(p[2] for p in doc["pairs"]) >= R.H2H_LIST_MIN


def test_the_new_pages_are_routed_in_the_app_and_on_vercel():
    from fastapi.testclient import TestClient
    from web.app import app
    c = TestClient(app)
    for path in ("/almanack/records", "/almanack/grounds", "/almanack/head-to-head", "/almanack/grounds/eden-gardens"):
        assert c.get(path).status_code == 200, path
    assert c.get("/almanack/secrets").status_code == 404
    srcs = {r["src"] for r in json.loads((ROOT / "vercel.json").read_text())["routes"]}
    assert "/almanack/(records|grounds|head-to-head|auctions)" in srcs and "/almanack/grounds/([a-z0-9-]+)" in srcs
