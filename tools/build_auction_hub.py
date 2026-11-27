"""Write the IPL 2027 auction hub, `/ipl-auction-2027`, from the mini-auction preset [A324].

    uv run python -m tools.build_auction_hub           # (re)write web/static/ipl-auction-2027.html
    uv run python -m tools.build_auction_hub --check   # exit 1 if the committed page is stale

WHAT. Everything a fan wants before the auction, on one page written out as plain HTML: each
franchise's retained squad, the money and places it has left, what it is short of, and every
player on the list. Plain HTML because that is what a search engine and AdSense read (A253);
`auction_hub.js` adds only a countdown and the register's filters.

FROM WHAT. `data/mini2027.json.gz` (`tools.snapshot_mini`), the same preset the mock auction
plays, plus the Almanack's compare file for each player's page and country. No database and
no network, so `--check` runs anywhere. Re-run it after `tools.snapshot_mini`: a list edited
without rebuilding this page would leave the hub describing a different list from the game.

NO RATINGS. The mini auction shows none (A298) and this is the same event, so nothing here
ranks a player: the register is alphabetical, never the auction's set order (cut strongest
first), and no base price is shown until the official register gives real ones -- a base
price for a player with no real one comes from the auction's tier rule, which follows the card.
"""
from __future__ import annotations

import json
import math
import pathlib
import sys
from collections import Counter
from datetime import date

from etl.derive_people import OVERRIDES, read_override
from game import auction as au
from game import auction_hub as hub
from game.mini2027 import AUCTION_DAY
from tools.build_history import STATIC, esc, listing, page, para, plural, section, words
from tools.snapshot_mini import load_preset
from web.crests import crest_key, crest_url

OUT = STATIC / "ipl-auction-2027.html"
PATH = "/ipl-auction-2027"
COMPARE = STATIC / "almanack" / "compare.json"
SHOWN_BALLS = 10      # balls in a discipline before its 2026 figures are worth a line

ROLE = {"batter": "Batter", "bowler": "Bowler", "allrounder": "All-rounder", "keeper": "Wicketkeeper"}
STATUS = {"played": "Played IPL 2026", "unsold": "Unsold in 2026",
          "new": "New this year", "idle": "In a 2026 squad, no games"}
GAP_LABEL = {"top": "top-order batters", "middle": "middle-order batters", "finisher": "finishers",
             "tail": "lower-order players", "keeper": "wicketkeepers", "bowling": "bowling options"}
GAP_CHIP = {"top": "Top order", "middle": "Middle order", "finisher": "Finishers",
            "tail": "Lower order", "keeper": "Keepers", "bowling": "Bowling"}


def money(lakh: int | float) -> str:
    """₹47.45 cr, ₹8.75 cr, ₹75 lakh -- as the auction itself is read out."""
    if lakh < 100:
        return f"₹{lakh:g} lakh"
    return f"₹{lakh / 100:.2f}".rstrip("0").rstrip(".") + " cr"


def people() -> dict[str, tuple[str, str]]:
    """person id -> (Almanack page slug, country), for every player with an IPL record."""
    doc = json.loads(COMPARE.read_text())
    return {p["id"]: (slug, p.get("country") or "") for slug, p in doc["players"].items()}


def list_countries() -> dict[str, str]:
    """name -> country, from the list's own rows, for newcomers the Almanack has no page for."""
    return {r["name"]: r["country"] for r in read_override(OVERRIDES / "auction_2027_list.csv")
            if r.get("country")}


class Who:
    """How a player is named and placed on the page."""

    def __init__(self) -> None:
        self.pages = people()
        self.countries = list_countries()

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


# --------------------------------------------------------------------------- sections

def lede(preset, teams: list[hub.TeamView]) -> str:
    places = sum(t.places for t in teams)
    os_places = sum(t.overseas_places for t in teams)
    purse = sum(t.purse for t in teams)
    rich, poor = teams[0], teams[-1]
    return (
        f'    <p class="ah-when"><b>16 December 2026</b><span class="ah-count" data-day="{AUCTION_DAY.isoformat()}"></span></p>\n'
        + para(f"The IPL 2027 auction is a mini auction: the ten franchises kept most of their squads, "
               f"and go in with {money(purse)} between them for {places} places, {os_places} of them "
               f"for overseas players.",
               "Every squad must reach at least 18 players and may hold 25, no more than eight of them "
               "from overseas.",
               f"{esc(rich.name)} have the most to spend, {money(rich.purse)}; {esc(poor.name)} the least, "
               f"{money(poor.purse)} for {plural(poor.places, 'place')}.")
        + '\n    <p class="actions ah-cta"><a class="go" href="/auction">Run the mock auction</a>'
          '<a class="go ghost" href="/rooms">Bid against friends</a>'
          '<a class="go ghost" href="/almanack/auctions/2026">How the 2026 auction went</a></p>\n'
        + '    <p class="hx-note">Predict the Auction opens with the official register on 3 December: '
          'call each team\'s biggest buy and the record price, and see how you rank once the hammer falls.</p>\n')


def team_card(t: hub.TeamView, who: Who, top_purse: int) -> str:
    key = crest_key(t.name) or t.short
    crest = crest_url(t.name)
    img = f'<img class="ah-crest" src="{crest}" alt="" width="64" height="64" loading="lazy">' if crest else ""
    pct = 100 * t.purse / top_purse if top_purse else 0
    gaps = "".join(f'<span class="ah-gap">{GAP_CHIP[g.what]} <b>{g.have}/{g.want}</b></span>' for g in t.gaps)
    need = (f"Short of {listing([GAP_LABEL[g.what] for g in t.gaps[:3]])}."
            if t.gaps else "No gaps against a full squad of 25.")
    must = (f" Must buy at least {plural(t.must_buy, 'player')} to reach 18." if t.must_buy else "")
    groups = []
    for b in hub.BANDS:
        rows = [c for c in t.squad if hub.band_of(c) == b]
        if not rows:
            continue
        lis = "".join(f'<li>{who.name(c)} <span class="ah-role">{ROLE.get(c.role or "", "")}</span>{marks(c)}</li>'
                      for c in rows)
        groups.append(f'<h4>{hub.BAND_LABEL[b]} <small>{len(rows)}</small></h4><ul>{lis}</ul>')
    return (
        f'<article class="ah-team crest-{key}" id="{t.short.lower()}">{img}'
        f'<header><em>{t.short}</em><h3>{esc(t.name)}</h3></header>'
        f'<div class="ah-purse"><b>{money(t.purse)}</b><span>left to spend</span></div>'
        f'<div class="ah-bar" aria-hidden="true"><i style="width:{pct:.1f}%"></i></div>'
        f'<dl class="ah-facts"><div><dt>Retained</dt><dd>{t.size}</dd></div>'
        f'<div><dt>Places</dt><dd>{t.places}</dd></div>'
        f'<div><dt>Overseas</dt><dd>{t.overseas_places}</dd></div>'
        f'<div><dt>Per place</dt><dd>{money(round(t.per_place))}</dd></div></dl>'
        f'<p class="ah-need">{need}{must}</p>'
        + (f'<div class="ah-gaps">{gaps}</div>' if gaps else "")
        + f'<details class="ah-squad"><summary>Retained squad ({t.size})</summary>{"".join(groups)}</details>'
        '</article>')


def teams_section(teams: list[hub.TeamView], who: Who) -> str:
    top = max(t.purse for t in teams)
    size = max(t.size + t.places for t in teams)
    cards = "".join(team_card(t, who, top) for t in teams)
    keeper = [t for t in teams if any(g.what == "keeper" for g in t.gaps)]
    intro = para(
        "Each team below with its money, its open places and the overseas places it has left, "
        "and where its retained squad falls short.",
        f"A team is short wherever it sits below the targets the computer teams in our mock auction "
        f"bid toward for a full squad of {size}: "
        + listing([f"{words(n)} {GAP_LABEL[k]}" for k, n in hub.targets(size).items()]) + ".",
        f"{words(len(keeper)).capitalize()} of the ten {'is' if len(keeper) == 1 else 'are'} short of a keeper"
        + (f": {listing([esc(t.name) for t in keeper])}." if keeper else "."))
    return section("The ten teams", intro + f'<div class="ah-teams">{cards}</div>')


def market_section(teams: list[hub.TeamView], lots) -> str:
    """Demand (places short, summed) beside supply (players on the list) for each band."""
    short = Counter()
    for t in teams:
        for g in t.gaps:
            short[g.what] += g.short
    supply = Counter(hub.band_of(c) for c, _ in lots)
    keepers = sum(c.keeper_eligible for c, _ in lots)
    bowlers = sum(c.has_bowl for c, _ in lots)
    os_lots = sum(c.overseas is True for c, _ in lots)
    os_places = sum(t.overseas_places for t in teams)
    rows = []
    for b in hub.BANDS:
        rows.append(f"<tr><td>{hub.BAND_LABEL[b]}</td><td class=n>{short[b]}</td><td class=n>{supply[b]}</td></tr>")
    rows.append(f"<tr><td>Wicketkeepers</td><td class=n>{short['keeper']}</td><td class=n>{keepers}</td></tr>")
    rows.append(f"<tr><td>Bowling options</td><td class=n>{short['bowling']}</td><td class=n>{bowlers}</td></tr>")
    rows.append(f"<tr><td>Overseas places</td><td class=n>{os_places}</td><td class=n>{os_lots}</td></tr>")
    table = ('<div class="hx-scroll"><table class="hx-table"><thead><tr><th>What</th>'
             '<th class=n>Teams short by</th><th class=n>On the list</th></tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table></div>')
    hottest = max(hub.BANDS, key=lambda b: (short[b] / supply[b]) if supply[b] else math.inf)
    return section("The market", para(
        "What the ten teams are short of between them, beside how many players on the list could fill it.",
        f"The tightest band is the {hub.BAND_LABEL[hottest].lower()}: {short[hottest]} places short across "
        f"the league against {supply[hottest]} players on the list who bat there.",
        "A player counts in one band, the one he has batted in most, so a bowler who bats low "
        "counts in the lower order.") + table)


def register_section(preset, lots, who: Who) -> str:
    rows = []
    for c, capped in lots:
        status = preset.status.get(c.person_id, "")
        age = preset.ages.get(c.person_id)
        b = hub.band_of(c) or ""
        note = preset.notes.get(c.person_id, "")
        line = season_line(c, status)
        detail = " ".join(x for x in (line, esc(note)) if x)
        rows.append(
            f'<tr data-name="{esc(c.name.lower())}" data-role="{esc(c.role or "")}" data-band="{b}" '
            f'data-os="{"os" if c.overseas else "in"}" data-status="{status}">'
            f'<td>{who.name(c)}{marks(c)}</td><td>{esc(who.country(c))}</td>'
            f'<td>{ROLE.get(c.role or "", "")}</td><td>{band_tag(c)}</td>'
            f'<td class="n ah-age">{int(age) if age else ""}</td>'
            f'<td><span class="ah-st st-{status}">{STATUS.get(status, "")}</span>{" · capped" if capped else ""}</td>'
            f'<td class="ah-note">{detail}</td></tr>')
    counts = Counter(preset.status.get(c.person_id, "") for c, _ in lots)
    os_count = sum(c.overseas is True for c, _ in lots)
    intro = para(
        f"<b>A provisional list.</b> The official register, with base prices and sets, comes out on "
        f"3 December; until then this is our own list of {plural(len(lots), 'player')}, the one the mock "
        f"auction plays: the {counts['played']} released after playing IPL 2026, the "
        f"{counts['unsold']} left unsold at the 2026 auction, and {counts['new']} new names from this "
        f"year's T20 leagues and the Syed Mushtaq Ali Trophy.",
        f"{os_count} are from overseas. Alphabetical; filter by role, batting band, passport or how "
        "each player comes in.")
    controls = (
        '<div class="ah-filters" id="ahFilters" hidden>'
        '<input class="room-input" id="ahSearch" type="search" placeholder="Search a name" aria-label="Search a name">'
        '<select id="ahRole" aria-label="Role"><option value="">Every role</option>'
        + "".join(f'<option value="{k}">{v}</option>' for k, v in ROLE.items()) + '</select>'
        '<select id="ahBand" aria-label="Batting band"><option value="">Any band</option>'
        + "".join(f'<option value="{b}">{hub.BAND_LABEL[b]}</option>' for b in hub.BANDS) + '</select>'
        '<select id="ahOs" aria-label="Passport"><option value="">Indian and overseas</option>'
        '<option value="in">Indian</option><option value="os">Overseas</option></select>'
        '<select id="ahStatus" aria-label="How he comes in"><option value="">Everyone</option>'
        + "".join(f'<option value="{k}">{v}</option>' for k, v in STATUS.items() if counts[k]) + '</select>'
        '<span class="ah-shown" id="ahShown"></span></div>')
    table = ('<div class="hx-scroll"><table class="hx-table ah-register" id="ahRegister"><thead><tr>'
             '<th>Player</th><th>Country</th><th>Role</th><th>Bats</th><th class=n>Age</th>'
             '<th>Going in</th><th>This year</th></tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table></div>')
    return section("The players", intro + controls + table)


def notes_section() -> str:
    return ('    <p class="hx-note">Retained squads from Wikipedia\'s 2027 personnel-changes page and the '
            'retention lists published on 17 November; money and places left from the JioHotstar '
            'Retention Special broadcast table of the same day, which every squad here matches for '
            'places and overseas places. Ages are given only where two sources agree on a birth date. '
            'Nothing on this page rates a player: the mock auction plays every one of them, but, like '
            'the auction floor itself, it shows no ratings.</p>\n')


def build() -> str:
    preset = load_preset()
    if preset is None:
        raise SystemExit("data/mini2027.json.gz is missing: run uv run python -m tools.snapshot_mini")
    teams = hub.teams(preset)
    lots = hub.lots(preset)
    who = Who()
    body = (lede(preset, teams) + teams_section(teams, who) + market_section(teams, lots)
            + register_section(preset, lots, who) + notes_section()
            + '    <script src="/static/auction_hub.js"></script>\n')
    return page(PATH, "IPL 2027 auction: squads, purses and every player",
                "Everything before the IPL 2027 auction on 16 December: each franchise's retained squad, "
                "money and places left, what it is short of, and every player on the list.",
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
