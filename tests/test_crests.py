"""Franchise crests (web/crests.py, tools/build_crests.py). No database: the deck's
franchise-seasons come from the committed snapshot, which check 26 keeps honest."""

from __future__ import annotations

import gzip
import json
import pathlib

import pytest

from game.auction import FRANCHISES
from tools import build_crests
from web import crests

ROOT = pathlib.Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "data" / "deck_snapshot.json.gz"


def _franchise_seasons() -> set[tuple[str, int]]:
    deck = json.loads(gzip.open(SNAPSHOT).read())["deck"]["cards_by_fs"]
    return {(cards[0]["franchise"], cards[0]["season_year"]) for cards in deck.values()}


def test_every_franchise_season_in_the_deck_has_a_crest():
    seasons = _franchise_seasons()
    assert len(seasons) == 166
    missing = sorted(fs for fs in seasons if crests.crest_url(*fs) is None)
    assert missing == []


def test_every_current_franchise_has_a_crest():
    # auction teams are the franchise as it is now, asked for with no year
    assert [s for s, name in FRANCHISES if crests.crest_url(name) is None] == []


def test_no_name_and_year_matches_two_crests():
    # A range overlap would let the table's ORDER decide a crest silently.
    for name in {n for c in crests.CRESTS for n in c.names}:
        for year in range(2008, 2031):
            hits = [c.key for c in crests.CRESTS if name in c.names
                    and (c.first is None or year >= c.first)
                    and (c.last is None or year <= c.last)]
            assert len(hits) <= 1, (name, year, hits)


@pytest.mark.parametrize("name, year, key", [
    ("Delhi Daredevils", 2012, "DD"),
    ("Delhi Capitals", 2019, "DC"),
    ("Kings XI Punjab", 2019, "KXIP"),
    ("Kings XI Punjab", 2020, "PBKS"),
    ("Royal Challengers Bangalore", 2008, "RCB"),
    ("Royal Challengers Bangalore", 2020, "RCB"),
    ("Royal Challengers Bengaluru", 2024, "RCB"),
    ("Rising Pune Supergiants", 2016, "RPS"),
    ("Rising Pune Supergiant", 2017, "RPS"),
])
def test_the_crest_follows_the_era(name, year, key):
    assert crests.crest_key(name, year) == key


def test_an_unknown_side_has_no_crest():
    assert crests.crest_url("Your eleven", 2026) is None
    assert crests.crest_url(None) is None


def test_every_source_is_used_and_every_key_has_a_source():
    keys = {c.key for c in crests.CRESTS}
    sources = {p.stem for p in build_crests.SOURCES.glob("*.webp")}
    assert keys == sources


def test_the_built_crests_are_current():
    assert build_crests.check() == 0


def test_a_crest_url_names_a_file_that_exists():
    url = crests.crest_url("Mumbai Indians", 2013)
    assert url and (ROOT / "web" / url.lstrip("/")).exists()


def test_with_no_year_an_old_name_gets_its_latest_crest():
    # The current names have one entry each, so only a name that changed crest can
    # tell "the entry that runs to this day" from "the first entry listed".
    assert crests.crest_key("Kings XI Punjab") == "PBKS"
    assert crests.crest_key("Royal Challengers Bangalore") == "RCB"


def test_the_historical_opponents_the_game_builds_carry_their_crest():
    # The wiring, not the table: a Side built by the engine must name its franchise
    # and season, or every table row reads as nobody's even with a perfect table.
    import random
    from game.season import historical_sides
    from tools import snapshot_deck
    deck = snapshot_deck.deck_from(snapshot_deck.read_document())
    sides = historical_sides(deck, random.Random(3), 9)
    assert len(sides) == 9
    assert all(crests.crest_url(s.franchise, s.year) for s in sides)
    # and the era is the side's own, not the franchise's latest
    for s in sides:
        assert s.name == f"{s.franchise} {s.year}"


def _independent_contrast(a: str, b: str) -> float:
    # WCAG relative luminance, written out here rather than imported from the tool,
    # so the tool's own contrast code is not the thing checking itself.
    def lum(h):
        chans = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in chans]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_every_crest_has_team_colours_and_its_ink_is_readable():
    import re
    css = build_crests.CSS.read_text()
    rules = dict(re.findall(r"\.crest-(\w+)\{([^}]*)\}", css))
    assert set(rules) == {c.key for c in crests.CRESTS}
    for key, body in rules.items():
        v = dict(re.findall(r"--([\w-]+):(#[0-9a-f]{6})", body))
        assert _independent_contrast(v["team-ink"], v["team-deep"]) >= 4.5, key


def test_flashback_withholds_a_crest_that_would_hint_at_the_year():
    assert crests.unambiguous_crest("Kings XI Punjab") is None
    # Since A332 every Bangalore-era season wears the one RCB crest, so it gives nothing away.
    assert crests.unambiguous_crest("Royal Challengers Bangalore") == crests.crest_url(
        "Royal Challengers Bengaluru", 2024)
    assert crests.unambiguous_crest("Delhi Daredevils") == crests.crest_url("Delhi Daredevils", 2010)


def test_analysis_rows_get_a_crest_only_when_their_label_is_unambiguous():
    # Season Analysis names a side by its short label alone, so the label is mapped back
    # to a crest -- and a label two different crests share must map to neither.
    from types import SimpleNamespace
    from game.season import Side
    from web.app import _crests_by_short
    dd = Side("Delhi Daredevils 2012", "DD 2012", [], franchise="Delhi Daredevils", year=2012)
    kx = Side("Kings XI Punjab 2014", "KXIP 2014", [], franchise="Kings XI Punjab", year=2014)
    clash = Side("Kings XI Punjab 2021", "KXIP 2014", [], franchise="Kings XI Punjab", year=2021)
    you = Side("Your eleven", "YOU", [], you=True)
    got = _crests_by_short([SimpleNamespace(home=dd, away=kx), SimpleNamespace(home=you, away=clash)])
    assert got == {"DD 2012": crests.crest_url("Delhi Daredevils", 2012)}


# --- hand-picked team colours [A333] -------------------------------------------------------

def _lab(h: str) -> tuple[float, float, float]:
    # sRGB -> CIE Lab (D65), written out here so the test shares no code with the build.
    def ch(x):
        x /= 255
        return x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4
    r, g, b = (ch(int(h[i:i + 2], 16)) for i in (1, 3, 5))
    x = (r * .4124 + g * .3576 + b * .1805) / .95047
    y = r * .2126 + g * .7152 + b * .0722
    z = (r * .0193 + g * .1192 + b * .9505) / 1.08883
    f = lambda t: t ** (1 / 3) if t > .008856 else 7.787 * t + 16 / 116
    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def _served_team_colours() -> dict[str, str]:
    import re
    css = build_crests.CSS.read_text()
    return {k: re.search(r"--team:(#[0-9a-f]{6})", body).group(1)
            for k, body in re.findall(r"\.crest-(\w+)\{([^}]*)\}", css)}


def test_every_crest_key_has_a_hand_picked_colour_and_it_is_what_is_served():
    served = _served_team_colours()
    assert set(crests.COLOURS) == {c.key for c in crests.CRESTS}
    assert served == crests.COLOURS


def test_teams_that_shared_a_season_never_share_a_colour():
    import itertools
    import math
    by_year: dict[int, set[str]] = {}
    for name, year in _franchise_seasons():
        by_year.setdefault(year, set()).add(crests.crest_key(name, year))
    served, close = _served_team_colours(), []
    for year, keys in by_year.items():
        for a, b in itertools.combinations(sorted(keys), 2):
            d = math.dist(_lab(served[a]), _lab(served[b]))
            if d < crests.MIN_SEASON_DELTA_E:
                close.append((year, a, b, round(d, 1)))
    assert crests.MIN_SEASON_DELTA_E >= 25          # stated, not read back blindly
    assert close == []


def test_one_franchise_wears_one_colour_across_a_rename():
    c = crests.COLOURS
    assert c["DD"] == c["DC"] and c["KXIP"] == c["PBKS"]


def test_every_team_colour_reads_against_the_page():
    # A stripe or glow in --team must stand off the site's navy, not sink into it.
    for key, team in _served_team_colours().items():
        assert _independent_contrast(team, "#060d1f") >= 3, key
