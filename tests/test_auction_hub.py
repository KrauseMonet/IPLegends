"""The IPL 2027 auction hub [A324]: a team's gaps, the register, the page and its script.

The gaps are checked against `game.auction.need` itself -- the rule the mock auction's
computer teams bid on -- rather than against `game.auction_hub`, so the page cannot quietly
describe a market the game does not play. Targets are stated as numbers, never read back from
the code they test (A207's trap).
"""

import json
import pathlib
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


def card(pid, name, lo, hi, role="batter", overseas=False, keeper=False, bowl=None, style=None):
    return Card(fs_id=0, person_id=pid, name=name, positions=frozenset(range(lo, hi + 1)),
                role=role, overseas=overseas, keeper_eligible=keeper, bowl=bowl, bowling_style=style)


def tiny_preset():
    squad = [card("a", "Zed Opener", 1, 3), card("b", "Yan Opener", 1, 3, overseas=True),
             card("c", "Xav Keeper", 3, 5, role="keeper", keeper=True),
             card("d", "Wes Quick", 8, 11, role="bowler", bowl=0.1, style="pace"),
             card("e", "Vic Spin", 8, 11, role="bowler", overseas=True, bowl=0.1, style="spin")]
    lots = [(card("p", "Bob Late", 5, 8), 30, False), (card("q", "Al Early", 1, 3), 200, True)]
    return Preset(squads={"CSK": squad}, purse={"CSK": 1000}, squad_size={"CSK": 25},
                  overseas_cap={"CSK": 8}, lots=lots)


def test_targets_are_the_engines_at_25_and_18():
    assert hub.targets(25) == {"top": 6, "middle": 4, "finisher": 6, "tail": 7, "keeper": 3, "bowling": 10}
    assert hub.targets(18) == {"top": 4, "middle": 3, "finisher": 4, "tail": 5, "keeper": 2, "bowling": 7}


def test_a_hand_built_team():
    t = hub.team_view(tiny_preset(), "CSK", "Chennai Super Kings")
    assert (t.size, t.places, t.must_buy, t.overseas, t.overseas_places) == (5, 20, 13, 2, 6)
    assert t.bands == {"top": 2, "middle": 1, "finisher": 0, "tail": 2}
    assert (t.keepers, t.bowlers, t.pace, t.spin) == (1, 2, 1, 1)
    assert {(g.what, g.have, g.want) for g in t.gaps} == {
        ("top", 2, 6), ("middle", 1, 4), ("finisher", 0, 6), ("tail", 2, 7),
        ("keeper", 1, 3), ("bowling", 2, 10)}
    assert [g.what for g in t.gaps][0] == "bowling"          # largest gap first
    assert t.per_place == 50.0                               # 1000 lakh over 20 places
    assert [c.name for c in t.squad] == ["Yan Opener", "Zed Opener", "Xav Keeper", "Vic Spin", "Wes Quick"]


def test_the_register_is_alphabetical_not_the_set_order():
    """The preset's order is the auction's sets, cut strongest first -- a ranking (A298)."""
    assert [c.name for c, _ in hub.lots(tiny_preset())] == ["Al Early", "Bob Late"]


@pytest.fixture(scope="module")
def preset():
    p = load_preset()
    if p is None:
        pytest.skip("no mini preset")
    return p


def test_each_real_gap_is_one_the_mock_auction_bids_for(preset):
    """A band gap on the page iff `need` raises a plain card of that band for that team."""
    teams = {t.short: t for t in au.make_mini_teams(preset, seed=1)}
    for view in hub.teams(preset):
        team = teams[view.short]
        short = {g.what for g in view.gaps}
        for b, (lo, hi) in zip(hub.BANDS, ((1, 3), (3, 5), (5, 8), (8, 11))):
            plain = card("x", "Plain", lo, hi)                 # no gloves, no bowling, Indian
            assert (au.need(team, plain) > 1) == (b in short), (view.short, b)


def test_every_real_team_and_player_is_counted_once(preset):
    views = hub.teams(preset)
    assert len(views) == 10
    assert sum(v.places for v in views) == 66 and sum(v.overseas_places for v in views) == 25
    assert [v.purse for v in views] == sorted((v.purse for v in views), reverse=True)
    names = [c.person_id for c, _ in hub.lots(preset)]
    assert len(names) == len(set(names)) == len(preset.lots)


def test_the_committed_page_is_current(preset):
    assert build.OUT.read_text() == build.build(), "run uv run python -m tools.build_auction_hub"


def test_the_page_lists_every_player_and_shows_no_rating(preset):
    text = build.OUT.read_text()
    assert text.count("<tr data-name=") == len(preset.lots)
    rows = [c.name for c, _ in hub.lots(preset)]
    positions = [text.index(f'data-name="{build.esc(n.lower())}"') for n in rows]
    assert positions == sorted(positions)
    article = text[text.index("<article"):text.rindex("</article>")].lower()
    note = "nothing on this page rates a player"
    assert note in article
    article = article[:article.index(note)]               # the note may say what it leaves out
    assert "rating" not in article and "rated" not in article
    for t in hub.teams(preset):
        assert f'id="{t.short.lower()}"' in text
        for c in t.squad:
            assert build.esc(c.name) in text


@pytest.mark.parametrize("lakh,words", [(4745, "₹47.45 cr"), (875, "₹8.75 cr"), (3950, "₹39.5 cr"),
                                        (2000, "₹20 cr"), (75, "₹75 lakh"), (30, "₹30 lakh")])
def test_money(lakh, words):
    assert build.money(lakh) == words


def test_season_line_leaves_out_token_figures():
    bowler = Card(fs_id=0, person_id="s", name="S", bat_runs=0, bat_balls=2,
                  bowl_wickets=11, bowl_runs=224, bowl_balls=132)
    assert build.season_line(bowler, "played") == "2026: 11 wkts, econ 10.18"
    tail = Card(fs_id=0, person_id="t", name="T", bat_runs=2, bat_balls=5)
    assert build.season_line(tail, "played") == "2026: 2 runs, SR 40"
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
def test_count_text_and_filters():
    assert node("[19, 1, 0, -1].map(m.ahCountText)") == ["19 days to go", "tomorrow", "today", ""]
    row = '{name: "mitchell starc", role: "bowler", band: "tail", os: "os", status: "played"}'
    assert node(f'm.ahMatches({row}, {{q: "starc"}})') is True
    assert node(f'm.ahMatches({row}, {{q: "kohli"}})') is False
    assert node(f'm.ahMatches({row}, {{role: "bowler", os: "os", band: "tail", status: "played"}})') is True
    assert node(f'm.ahMatches({row}, {{os: "in"}})') is False
    assert node(f'm.ahMatches({row}, {{status: "new"}})') is False
