"""The IPL 2027 auction hub [A324, A326]: a team's needs, the released players matched to them,
the page and its script.

Band gaps are checked against `game.auction.need` itself -- the rule the mock auction's
computer teams bid on -- rather than against `game.auction_hub`, so the page cannot quietly
describe a market the game does not play. Targets are stated as numbers, never read back
from the code they test (A207's trap).
"""

import json
import pathlib
import re
import shutil
import subprocess

import pytest

from etl.feasibility import Card
from game import auction as au
from game import auction_hub as hub
from game.mini2027 import Preset
from tools import build_auction_hub as build
from tools.snapshot_mini import load_preset

ROOT = pathlib.Path(__file__).resolve().parent.parent
JS = ROOT / "web" / "static" / "auction_hub.js"


def card(pid, name, lo, hi, role="batter", overseas=False, keeper=False, bowl=None, style=None, **kw):
    return Card(fs_id=0, person_id=pid, name=name, positions=frozenset(range(lo, hi + 1)),
                role=role, overseas=overseas, keeper_eligible=keeper, bowl=bowl, bowling_style=style, **kw)


def squad_a():
    return [card("a", "Zed Opener", 1, 3), card("b", "Yan Opener", 1, 3, overseas=True),
            card("c", "Xav Keeper", 3, 5, role="keeper", keeper=True),
            card("d", "Wes Quick", 8, 11, role="bowler", bowl=0.1, style="pace"),
            card("e", "Vic Spin", 8, 11, role="bowler", overseas=True, bowl=0.1, style="spin"),
            card("f", "Uli Partime", 1, 3, bowl=0.1, style="spin")]          # a batter who bowls


def tiny_preset():
    pace = [card(f"p{i}", f"Pace {i}", 8, 11, role="bowler", bowl=0.1, style="pace") for i in range(3)]
    return Preset(squads={"CSK": squad_a(), "MI": pace}, purse={"CSK": 1000, "MI": 500},
                  squad_size={"CSK": 25, "MI": 25}, overseas_cap={"CSK": 8, "MI": 8}, lots=[])


def test_targets_are_the_engines_at_25_and_18():
    assert hub.targets(25) == {"top": 6, "middle": 4, "finisher": 6, "tail": 7, "keeper": 3, "bowling": 10}
    assert hub.targets(18) == {"top": 4, "middle": 3, "finisher": 4, "tail": 5, "keeper": 2, "bowling": 7}


def test_a_hand_built_team():
    t = hub.team_view(tiny_preset(), "CSK", "Chennai Super Kings")
    assert (t.size, t.places, t.must_buy, t.overseas, t.overseas_places) == (6, 19, 12, 2, 6)
    assert t.bands == {"top": 3, "middle": 1, "finisher": 0, "tail": 2}
    # a batter who bowls the odd over is a bowling option but not a specialist spinner
    assert (t.keepers, t.bowlers, t.pace, t.spin) == (1, 3, 1, 1)
    assert t.per_place == pytest.approx(1000 / 19)
    assert [c.name for c in t.squad][:3] == ["Uli Partime", "Yan Opener", "Zed Opener"]


def test_style_wants_are_the_league_median_rounded_up():
    class V:
        def __init__(self, pace, spin):
            self.pace, self.spin = pace, spin
    assert hub.style_wants([V(p, s) for p, s in [(5, 2), (6, 3), (7, 4), (8, 4)]]) == {"pace": 7, "spin": 4}


def test_meters_are_the_six_needs_in_order():
    views = {v.short: v for v in hub.teams(tiny_preset())}
    csk, mi = views["CSK"], views["MI"]
    assert [m.what for m in csk.meters] == ["top", "middle", "finisher", "keeper", "pace", "spin"]
    # league medians over the two squads: pace (1, 3) -> 2, spin (1, 0) -> ceil(0.5) = 1
    assert [(m.have, m.want) for m in csk.meters] == [(3, 6), (1, 4), (0, 6), (1, 3), (1, 2), (1, 1)]
    assert csk.short_of == {"top", "middle", "finisher", "keeper", "pace"}
    assert "spin" in mi.short_of and "pace" not in mi.short_of
    assert [v.short for v in hub.teams(tiny_preset())] == ["CSK", "MI"]      # most money first


@pytest.mark.parametrize("c,want", [
    (card("1", "Opener", 1, 3), {"top"}),
    (card("2", "Keeper", 3, 5, role="keeper", keeper=True), {"middle", "keeper"}),
    (card("3", "Batter who bowls", 5, 8, bowl=0.1, style="spin"), {"finisher"}),
    (card("4", "All-rounder", 3, 5, role="allrounder", bowl=0.1, style="pace"), {"middle", "pace"}),
    (card("5", "Quick", 8, 11, role="bowler", bowl=0.1, style="pace"), {"pace"}),
    (card("6", "Spinner who bats 5-8", 5, 8, role="bowler", bowl=0.1, style="spin"), {"spin"}),
])
def test_what_a_player_fills(c, want):
    assert hub.fills(c) == want


def view(short, top_have, places=5, purse=500, overseas_places=2):
    v = hub.TeamView(short=short, name=short, squad=[], purse=purse, places=places, must_buy=0,
                     overseas=0, overseas_places=overseas_places, bands={}, keepers=0, bowlers=0,
                     pace=0, spin=0, gaps=[])
    v.meters = [hub.Gap("top", top_have, 6)]
    return v


def test_fits_are_the_most_short_never_the_old_team_and_need_an_overseas_place():
    teams = [view("CSK", 5), view("MI", 2), view("RR", 3), view("DC", 1), view("KKR", 2, purse=900),
             view("GT", 6), view("SRH", 0, overseas_places=0)]
    opener = card("x", "Opener", 1, 3)
    assert build.fits(opener, teams, former="DC") == ["SRH", "KKR", "MI"]   # DC is 5 short but released him
    os_opener = card("y", "Opener", 1, 3, overseas=True)
    assert build.fits(os_opener, teams, former="") == ["DC", "KKR", "MI"]  # SRH has no overseas place
    assert build.fits(card("z", "Quick", 8, 11, role="bowler", bowl=0.1, style="pace"), teams, "") == []


@pytest.fixture(scope="module")
def preset():
    p = load_preset()
    if p is None:
        pytest.skip("no mini preset")
    return p


def test_each_real_band_gap_is_one_the_mock_auction_bids_for(preset):
    """A band gap iff `need` raises a plain card of that band for that team."""
    teams = {t.short: t for t in au.make_mini_teams(preset, seed=1)}
    for v in hub.teams(preset):
        team = teams[v.short]
        short = {g.what for g in v.gaps}
        for b, (lo, hi) in zip(hub.BANDS, ((1, 3), (3, 5), (5, 8), (8, 11))):
            plain = card("x", "Plain", lo, hi)
            assert (au.need(team, plain) > 1) == (b in short), (v.short, b)
        for m in v.meters:
            if m.what in hub.BANDS:
                assert (m.have < m.want) == (m.what in short), (v.short, m.what)


def test_every_real_team_is_counted(preset):
    views = hub.teams(preset)
    assert len(views) == 10
    assert sum(v.places for v in views) == 66 and sum(v.overseas_places for v in views) == 25
    assert [v.purse for v in views] == sorted((v.purse for v in views), reverse=True)


def test_released_are_the_players_back_after_ipl_2026(preset):
    rel = hub.released(preset)
    rows = {r["person_id"] for r in build.list_rows() if r["source"] == "released" and r.get("include") != "n"}
    assert {c.person_id for c in rel} == rows


def test_former_teams_name_the_franchise_that_released_him(preset):
    former = build.former_teams()
    names = {c.name: former[c.person_id] for c in hub.released(preset)}
    assert names["Pathum Nissanka"] == "DC" and names["Trent Boult"] == "MI"
    assert names["Nehal Wadhera"] == "PBKS" and names["Avesh Khan"] == "LSG"


def test_the_committed_page_is_current(preset):
    assert build.OUT.read_text() == build.build(), "run uv run python -m tools.build_auction_hub"


def test_the_page_shows_released_players_not_the_whole_list_and_no_rating(preset):
    text = build.OUT.read_text()
    for c in hub.released(preset):
        assert build.esc(c.name) in text, c.name
    others = [c.name for c, _, _ in preset.lots if preset.status.get(c.person_id) != "played"]
    in_squads = {c.name for cs in preset.squads.values() for c in cs}
    assert not [n for n in others if n not in in_squads and f">{build.esc(n)}<" in text]
    assert text.count('class="ah-team ') == 10 and text.count('<ul class="ah-meters"') == 10
    article = text[text.index("<article"):text.rindex("</article>")].lower()
    note = "nothing on this page rates a player"
    assert note in article
    article = article[:article.index(note)]               # the note may say what it leaves out
    assert "rating" not in article and "rated" not in article


@pytest.mark.parametrize("lakh,words", [(4745, "₹47.45 cr"), (875, "₹8.75 cr"), (3950, "₹39.5 cr"),
                                        (2000, "₹20 cr"), (75, "₹75 lakh"), (30, "₹30 lakh")])
def test_money(lakh, words):
    assert build.money(lakh) == words


def test_season_line_and_the_discipline_shown():
    bowler = Card(fs_id=0, person_id="s", name="S", role="bowler", bat_runs=12, bat_balls=20,
                  bowl_wickets=11, bowl_runs=224, bowl_balls=132)
    assert build.season_line(bowler, "played") == "2026: 12 runs, SR 60 · 11 wkts, econ 10.18"
    assert build.line_for(bowler) == "11 wkts, econ 10.18"
    batter = Card(fs_id=0, person_id="t", name="T", role="batter", bat_runs=225, bat_balls=149,
                  bowl_wickets=0, bowl_runs=55, bowl_balls=24)
    assert build.line_for(batter) == "225 runs, SR 151"
    token = Card(fs_id=0, person_id="u", name="U", role="bowler", bat_runs=0, bat_balls=2,
                 bowl_wickets=0, bowl_runs=0, bowl_balls=0)
    assert build.line_for(token) == "barely used"
    assert build.season_line(bowler, "unsold") == ""


def test_the_hub_is_routed_linked_and_in_the_sitemap():
    vercel = json.loads((ROOT / "vercel.json").read_text())
    assert any(r.get("src") == "/ipl-auction-2027" and r["dest"] == "/web/static/ipl-auction-2027.html"
               for r in vercel["routes"])
    from web.app import app
    assert "/ipl-auction-2027" in {getattr(r, "path", None) for r in app.routes}
    assert "https://fineleg.app/ipl-auction-2027<" in (ROOT / "web" / "static" / "sitemap.xml").read_text()
    for page in ("index.html", "auction.html"):
        assert 'href="/ipl-auction-2027"' in (ROOT / "web" / "static" / page).read_text()


def node(expr):
    out = subprocess.run(["node", "-e", f"const m = require({json.dumps(str(JS))}); "
                                        f"console.log(JSON.stringify({expr}))"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@needs_node
@pytest.mark.parametrize("now,days", [
    ("2026-11-27T12:00:00Z", 19),
    ("2026-12-15T17:59:00Z", 1),       # 23:29 in India on the 15th: tomorrow
    ("2026-12-15T18:31:00Z", 0),       # 00:01 in India on the 16th: today, though UTC says the 15th
    ("2026-12-17T06:00:00Z", -1),
])
def test_days_to_the_auction_are_counted_in_indian_time(now, days):
    assert node(f'm.ahDaysTo("2026-12-16", new Date("{now}"))') == days


@needs_node
def test_count_text():
    assert node("[19, 1, 0, -1].map(m.ahCountText)") == ["19 days to go", "tomorrow", "today", ""]


def test_every_team_card_opens_its_own_section_of_the_needs_article(preset):
    """[A327] A card's "View analysis" link must land on that team's section, which must exist."""
    text = build.OUT.read_text()
    article = (ROOT / "web" / "static" / "articles" / "what-each-team-needs.html").read_text()
    links = re.findall(r'class="ah-analysis" href="/articles/what-each-team-needs#([a-z]+)"', text)
    assert sorted(links) == sorted(v.short.lower() for v in hub.teams(preset))
    for short in links:
        assert f'<section class="hx-sec" id="{short}">' in article, short
    sections = re.findall(r'<section class="hx-sec" id="([a-z]+)">\s*<h2>([^<]+)</h2>', article)
    assert dict(sections) == {s.lower(): n for s, n in au.FRANCHISES}


# ---------------------------------------------------------------- [A329] trades so far

def test_every_trade_lands_in_the_squad_and_at_the_fee_the_retentions_record(preset):
    """The trades come from the IPL's own releases; the squads and fees from the retention lists.
    The two are separate sources, so a player in the wrong squad or at the wrong fee is a real
    disagreement, not one copy agreeing with itself."""
    from etl.derive_people import OVERRIDES, read_override
    retained = {}
    for r in read_override(OVERRIDES / "retained_2027.csv"):
        retained[r["name"].replace(".", "")] = (r["short"], int(r["salary_lakh"]))
    rows = build.trades()
    assert len(rows) == 14
    for r in rows:
        assert r["from"] != r["to"]
        assert retained[r["player"].replace(".", "")] == (r["to"], int(r["fee_lakh"])), r["player"]
        if r["person_id"]:
            assert r["person_id"] in {c.person_id for c in preset.squads[r["to"]]}, r["player"]


def test_a_deal_is_two_teams_on_one_date_and_deals_run_oldest_first():
    deals: dict[str, list] = {}
    for r in build.trades():
        deals.setdefault(r["deal"], []).append(r)
    assert len(deals) == 6
    dates = []
    for rows in deals.values():
        assert len({r["date"] for r in rows}) == 1
        assert len({frozenset((r["from"], r["to"])) for r in rows}) == 1
        dates.append(rows[0]["date"])
    assert dates == sorted(dates)


def test_the_page_draws_every_deal_both_ways_with_a_cash_side(preset):
    html = build.OUT.read_text()
    sec = html[html.index("Trades so far"):html.index("Released, and where they fit")]
    assert sec.count('class="ah-tr"') == 6
    for r in build.trades():
        assert r["player"] in sec
    # Harshal Patel went for cash: Hyderabad's side of that deal holds no player
    assert sec.count('class="ah-tr-cash"') == 1
    assert "Rishabh Pant" in sec[sec.index("23 June 2026"):sec.index("14 October 2026")]
