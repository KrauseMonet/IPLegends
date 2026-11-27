"""Write the IPL 2027 auction hub, `/ipl-auction-2027`, from the mini-auction preset [A324].

    uv run python -m tools.build_auction_hub           # (re)write web/static/ipl-auction-2027.html
    uv run python -m tools.build_auction_hub --check   # exit 1 if the committed page is stale

WHAT. Everything a fan wants before the auction, shown rather than written [A326]: a stat strip,
each franchise's money, places and six need meters (`game.auction_hub.NEEDS`), where the
shortages are across the league, and the players released after IPL 2026 matched to the teams
short of what they do. Plain HTML because that is what a search engine and AdSense read (A253);
`auction_hub.js` adds only the countdown. The full provisional list is NOT shown: it waits for
the official register on 3 December (the user's call, 2026-11-27).

FROM WHAT. `data/mini2027.json.gz` (`tools.snapshot_mini`), the same preset the mock auction
plays, plus the Almanack's compare file for each player's page and country. No database and
no network, so `--check` runs anywhere. Re-run it after `tools.snapshot_mini`: a list edited
without rebuilding this page would leave the hub describing a different list from the game.

NO RATINGS. The mini auction shows none (A298) and this is the same event, so nothing here
ranks a player: released players are ordered by how much they played in 2026, never by the
auction's set order (cut strongest first), and no base price is shown until the official register gives real ones -- a base
price for a player with no real one comes from the auction's tier rule, which follows the card.
"""
from __future__ import annotations

import json
import pathlib
import sys
from dataclasses import replace

from etl.derive_people import OVERRIDES, read_override
from etl.franchise_map import canonical
from game import auction as au
from game import auction_hub as hub
from game.mini2027 import AUCTION_DAY
from tools.build_history import STATIC, esc, page, para, plural, section
from tools.snapshot_mini import load_preset
from web.crests import crest_key, crest_url

OUT = STATIC / "ipl-auction-2027.html"
PATH = "/ipl-auction-2027"
COMPARE = STATIC / "almanack" / "compare.json"
SHOWN_BALLS = 10      # balls in a discipline before its 2026 figures are worth a line

ROLE = {"batter": "Batter", "bowler": "Bowler", "allrounder": "All-rounder", "keeper": "Wicketkeeper"}
NEED_LABEL = {"top": "Top order", "middle": "Middle order", "finisher": "Finishers",
              "keeper": "Keepers", "pace": "Pace", "spin": "Spin"}
NAMES = dict(au.FRANCHISES)


def money(lakh: int | float) -> str:
    """₹47.45 cr, ₹8.75 cr, ₹75 lakh -- as the auction itself is read out."""
    if lakh < 100:
        return f"₹{lakh:g} lakh"
    return f"₹{lakh / 100:.2f}".rstrip("0").rstrip(".") + " cr"


def people() -> dict[str, tuple[str, str]]:
    """person id -> (Almanack page slug, country), for every player with an IPL record."""
    doc = json.loads(COMPARE.read_text())
    return {p["id"]: (slug, p.get("country") or "") for slug, p in doc["players"].items()}


def list_rows() -> list[dict]:
    return read_override(OVERRIDES / "auction_2027_list.csv")


def former_teams() -> dict[str, str]:
    """person id -> the short code of the franchise that released him after IPL 2026."""
    short = {n: s for s, n in au.FRANCHISES}
    out = {}
    for r in list_rows():
        last = r.get("ipl_last", "")
        if r["source"] == "released" and last.startswith("2026 "):
            out[r["person_id"]] = short.get(canonical(last[5:]), "")
    return out


class Who:
    """How a player is named and placed on the page."""

    def __init__(self) -> None:
        self.pages = people()
        self.countries = {r["name"]: r["country"] for r in list_rows() if r.get("country")}

    def name(self, card) -> str:
        known = self.pages.get(card.person_id)
        if known and (STATIC / "almanack" / "players" / f"{known[0]}.html").exists():
            return f'<a href="/almanack/players/{known[0]}">{esc(card.name)}</a>'
        return esc(card.name)

    def country(self, card) -> str:
        known = self.pages.get(card.person_id)
        c = (known[1] if known else "") or self.countries.get(card.name, "")
        if c:
            return c
        return "India" if card.overseas is False else "Overseas" if card.overseas else ""


def band_tag(card) -> str:
    b = hub.band_of(card)
    if not b:
        return ""
    return (f'<em class="auc-band band-{b}" title="{hub.BAND_LABEL[b]}: can bat at {hub.pos_range(card)}">'
            f'<span class="bn">{hub.BAND_SHORT[b]} </span>{hub.pos_range(card)}</em>')


def marks(card) -> str:
    out = []
    if card.keeper_eligible:
        out.append('<span class="ah-mk" title="Keeps wicket">gloves</span>')
    if card.has_bowl and card.bowling_style:
        out.append(f'<span class="ah-mk">{card.bowling_style}</span>')
    if card.overseas:
        out.append('<span class="ah-mk os" title="Overseas">OS</span>')
    return "".join(out)


def season_line(card, status: str) -> str:
    """A played player's 2026 figures, where the card carries them; nothing that ranks him."""
    if status != "played":
        return ""
    bat, bowl = card.bat_balls or 0, card.bowl_balls or 0
    # A token few balls in the other discipline is left out ("0 runs, SR 0" for a bowler who
    # faced two balls says nothing); a player with only a token is still shown it.
    show_bat = bat >= SHOWN_BALLS or (bat and bowl < SHOWN_BALLS)
    show_bowl = bowl >= SHOWN_BALLS or (bowl and bat < SHOWN_BALLS)
    bits = []
    if show_bat:
        bits.append(f"{card.bat_runs or 0} runs, SR {100 * (card.bat_runs or 0) / bat:.0f}")
    if show_bowl:
        bits.append(f"{plural(card.bowl_wickets or 0, 'wkt')}, econ {6 * (card.bowl_runs or 0) / bowl:.2f}")
    return "2026: " + " · ".join(bits) if bits else ""




def crest_chip(short: str, size: int = 22, gap: int = 0) -> str:
    """A franchise's crest, small, naming it on hover; `gap` adds how many it is short by."""
    name = NAMES[short]
    url = crest_url(name)
    inner = (f'<img src="{url}" alt="{esc(short)}" width="{size}" height="{size}" loading="lazy">'
             if url else esc(short))
    tip = f"{name}, {gap} short" if gap else name
    badge = f'<sup>{gap}</sup>' if gap else ""
    return f'<a class="ah-chip" href="#{short.lower()}" title="{esc(tip)}">{inner}{badge}</a>'


# --------------------------------------------------------------------------- sections

def hero(teams: list[hub.TeamView], released: list) -> str:
    purse = sum(t.purse for t in teams)
    places = sum(t.places for t in teams)
    os_places = sum(t.overseas_places for t in teams)
    tiles = [(money(purse), "to spend"), (str(places), "places to fill"),
             (str(os_places), "overseas places"), (str(len(released)), "released players back in")]
    stats = "".join(f'<div class="ah-stat"><b>{v}</b><span>{k}</span></div>' for v, k in tiles)
    return (
        f'    <section class="hx-sec ah-top"><div class="ah-hero"><div class="ah-date"><b>16 December 2026</b>'
        f'<span class="ah-count" data-day="{AUCTION_DAY.isoformat()}"></span></div>'
        f'<div class="ah-stats">{stats}</div></div>\n'
        + para(f"A mini auction: the ten franchises kept most of their squads and go in with "
               f"{money(purse)} for {places} places. A squad holds 18 to 25 players, no more than "
               f"eight from overseas.")
        + '\n    <p class="actions ah-cta"><a class="go" href="/auction">Run the mock auction</a>'
          '<a class="go ghost" href="/rooms">Bid against friends</a>'
          '<a class="go ghost" href="/almanack/auctions/2026">The 2026 auction</a></p></section>\n')


def meter(m: hub.Gap) -> str:
    pct = min(100.0, 100 * m.have / m.want) if m.want else 100.0
    short = m.have < m.want
    return (f'<li class="{"short" if short else "ok"}"><span>{NEED_LABEL[m.what]}</span>'
            f'<span class="ah-m"><i style="width:{pct:.0f}%"></i></span>'
            f'<b>{m.have}<small>/{m.want}</small></b></li>')


def team_card(t: hub.TeamView, who: Who, top_purse: int) -> str:
    key = crest_key(t.name) or t.short
    crest = crest_url(t.name)
    img = f'<img class="ah-crest" src="{crest}" alt="" width="64" height="64" loading="lazy">' if crest else ""
    pct = 100 * t.purse / top_purse if top_purse else 0
    facts = [("Places", t.places), ("Overseas", t.overseas_places), ("Must buy", t.must_buy),
             ("Per place", money(round(t.per_place)))]
    groups = []
    for b in hub.BANDS:
        rows = [c for c in t.squad if hub.band_of(c) == b]
        if rows:
            lis = "".join(f'<li>{who.name(c)}{marks(c)}</li>' for c in rows)
            groups.append(f'<h4>{hub.BAND_LABEL[b]} <small>{len(rows)}</small></h4><ul>{lis}</ul>')
    return (
        f'<article class="ah-team crest-{key}" id="{t.short.lower()}">{img}'
        f'<header><em>{t.short}</em><h3>{esc(t.name)}</h3></header>'
        f'<div class="ah-purse"><b>{money(t.purse)}</b><span>left</span></div>'
        f'<div class="ah-bar" aria-hidden="true"><i style="width:{pct:.1f}%"></i></div>'
        '<dl class="ah-facts">' + "".join(f'<div><dt>{k}</dt><dd>{v}</dd></div>' for k, v in facts) + '</dl>'
        f'<ul class="ah-meters" aria-label="Retained against a full squad">{"".join(meter(m) for m in t.meters)}</ul>'
        f'<details class="ah-squad"><summary>Retained squad ({t.size})</summary>{"".join(groups)}</details>'
        '</article>')


def teams_section(teams: list[hub.TeamView], who: Who) -> str:
    top = max(t.purse for t in teams)
    size = max(t.size + t.places for t in teams)
    intro = para(f"Retained players against a full squad of {size}. Red is short.")
    cards = "".join(team_card(t, who, top) for t in teams)
    return section("The ten teams", intro + f'<div class="ah-teams">{cards}</div>')


def primary(card) -> str:
    """The one group a released player is listed under: the gloves, his batting band, or his
    bowling style -- in that order."""
    f = hub.fills(card)
    for need in ("keeper", "top", "middle", "finisher", "pace", "spin"):
        if need in f:
            return need
    return ""


BEST_FITS = 3          # teams shown on a released player's tile


def shortfall(t: hub.TeamView, need: str) -> int:
    return next((m.want - m.have for m in t.meters if m.what == need), 0)


def fits(card, teams: list[hub.TeamView], former: str) -> list[str]:
    """The teams most short of anything this player does -- never the one that released him,
    and only a team with an overseas place left if he is from overseas. Largest shortfall
    first, then most money per open place; at most BEST_FITS, since against a full squad of
    25 nearly every team is short of something and a list of nine says nothing."""
    f = hub.fills(card)
    ranked = []
    for t in teams:
        gap = max((shortfall(t, n) for n in f), default=0)
        if gap > 0 and t.short != former and (not card.overseas or t.overseas_places > 0):
            ranked.append((-gap, -t.per_place, t.short))
    return [s for *_, s in sorted(ranked)][:BEST_FITS]


def line_for(card) -> str:
    """His 2026 figures in the discipline he is there for: batting for a batter or keeper,
    bowling for a bowler, both for an all-rounder."""
    if card.role == "bowler":
        card = replace(card, bat_balls=None)
    elif card.role in ("batter", "keeper"):
        card = replace(card, bowl_balls=None)
    line = season_line(card, "played")
    return line[6:] if line else "barely used"


def released_section(preset, teams: list[hub.TeamView], who: Who) -> str:
    rel = hub.released(preset)
    former = former_teams()
    by: dict[str, list] = {n: [] for n in hub.NEEDS}
    for c in rel:
        g = primary(c)
        if g:
            by[g].append(c)
    volume = lambda c: -((c.bat_balls or 0) + (c.bowl_balls or 0))
    blocks = []
    for need in hub.NEEDS:
        players = sorted(by[need], key=lambda c: (volume(c), c.name))
        if not players:
            continue
        short = sorted((t for t in teams if need in t.short_of), key=lambda t: (-shortfall(t, need), t.short))
        tiles = []
        for c in players:
            ex = former.get(c.person_id, "")
            fit = fits(c, teams, ex)
            tiles.append(
                f'<div class="ah-rel"><div class="ah-rel-top">{who.name(c)}{marks(c)}{band_tag(c)}</div>'
                f'<div class="ah-rel-ex">{"Ex " + crest_chip(ex, 18) + " " + ex if ex else ""}'
                f'<span>{esc(line_for(c))}</span></div>'
                f'<div class="ah-rel-fit"><span>Best fits</span>{"".join(crest_chip(s) for s in fit) or "<em>no team short</em>"}</div></div>')
        need_by = "".join(crest_chip(t.short, gap=shortfall(t, need)) for t in short) or "<em>nobody short</em>"
        blocks.append(
            f'<div class="ah-group"><div class="ah-group-h"><h3>{NEED_LABEL[need]}</h3>'
            f'<div class="ah-group-need"><span>Short</span>{need_by}</div></div>'
            f'<div class="ah-rels">{"".join(tiles)}</div></div>')
    intro = para(
        f"The {plural(len(rel), 'player')} released after IPL 2026, with the three teams most short "
        f"of what each does (never his old team). The full list comes with the official register "
        f"on 3 December.")
    return section("Released, and where they fit", intro + "".join(blocks))


def notes_section(teams: list[hub.TeamView]) -> str:
    size = max(t.size + t.places for t in teams)
    want, wants = hub.targets(size), hub.style_wants(teams)
    return ('    <p class="hx-note">What a squad "wants": batting bands and keepers use the targets the '
            f"computer teams in our mock auction bid toward ({want['top']} top order, {want['middle']} middle "
            f"order, {want['finisher']} finishers, {want['keeper']} keepers); pace and spin are against the "
            f"league's median retained squad ({wants['pace']} and {wants['spin']} specialist bowlers), a count "
            "that cannot see quality: a team that lost its best seamer but kept enough others reads as covered. "
            'Retained squads from Wikipedia\'s 2027 personnel-changes page and the '
            'retention lists published on 17 November; money and places left from the JioHotstar '
            'Retention Special broadcast table of the same day, which every squad here matches for '
            'places and overseas places. Nothing on this page rates a player: the mock auction plays every '
            'one of them, but, like the auction floor itself, it shows no ratings.</p>\n')


def build() -> str:
    preset = load_preset()
    if preset is None:
        raise SystemExit("data/mini2027.json.gz is missing: run uv run python -m tools.snapshot_mini")
    teams = hub.teams(preset)
    who = Who()
    body = (hero(teams, hub.released(preset)) + teams_section(teams, who)
            + released_section(preset, teams, who) + notes_section(teams)
            + '    <script src="/static/auction_hub.js"></script>\n')
    return page(PATH, "IPL 2027 auction: squads, purses and needs",
                "Before the IPL 2027 auction on 16 December: each franchise's money and places left, what "
                "its squad is short of, and the released players who fit.",
                "IPL 2027 · AUCTION", "The IPL 2027 auction", body)


def main(check: bool = False) -> int:
    text = build()
    if check:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"stale: {OUT.name} -- run uv run python -m tools.build_auction_hub")
            return 1
        print(f"{OUT.name} current")
        return 0
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(STATIC.parent.parent)} ({len(text) // 1024} kB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(check="--check" in sys.argv))
