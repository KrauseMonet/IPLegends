"""What each franchise takes into the IPL 2027 auction, read off the mini-auction preset [A324].

Pure: a function of `game.mini2027.Preset` alone, so the hub page (`tools.build_auction_hub`)
needs no database and a test can build it from a hand-made preset.

**A team's gaps are the auction engine's own.** `game.auction.need` is what the computer
teams in the mock auction bid on: a target per batting band, keepers and bowling options,
each scaled from a squad of eighteen to the team's real squad limit (25 in a mini auction).
The hub reports a team as short wherever its retained squad sits below one of those
targets, so the page and the mock auction can never disagree about what a team is after.

**No rating appears here.** The mini auction shows none (A298), and the hub is the same
event, so a player is described by his job -- role, batting band, bowling style, passport --
and never by a number that ranks him.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from etl.feasibility import Card
from game import auction as au

BANDS = ("top", "middle", "finisher", "tail")
BAND_LABEL = {"top": "Top order", "middle": "Middle order", "finisher": "Finisher", "tail": "Lower order"}
BAND_SHORT = {"top": "Top", "middle": "Middle", "finisher": "Finisher", "tail": "Lower"}
STYLES = ("pace", "spin")
# What a team card shows and what a released player is matched against [A326]: three batting
# bands and keepers by the mock auction's own targets (`targets`), and pace and spin bowlers
# against the LEAGUE: a team is short of a style when it holds fewer specialist bowlers of it
# than the median retained squad (`style_wants`). The engine's own bowling target counts any
# bowling option, part-timers included, and so says almost nobody is short of bowling.
NEEDS = ("top", "middle", "finisher", "keeper", "pace", "spin")


def band_of(card: Card) -> str | None:
    """The band a card bats in (A76), as the auction pages name it; None with no positions."""
    return BANDS[au.band(card)] if card.positions else None


def pos_range(card: Card) -> str:
    if not card.positions:
        return ""
    lo, hi = min(card.positions), max(card.positions)
    return f"{lo}" if lo == hi else f"{lo}–{hi}"


@dataclass
class Gap:
    what: str          # 'top', 'middle', 'finisher', 'tail', 'keeper', 'bowling'
    have: int
    want: int

    @property
    def short(self) -> int:
        return self.want - self.have


@dataclass
class TeamView:
    short: str
    name: str
    squad: list[Card]           # retained, ordered by band then name
    purse: int                  # lakh left
    places: int                 # open places, up to the squad limit
    must_buy: int               # places that MUST be filled to reach the squad minimum
    overseas: int               # overseas players retained
    overseas_places: int        # overseas places left
    bands: dict[str, int]
    keepers: int
    bowlers: int
    pace: int
    spin: int
    gaps: list[Gap]                      # the mock auction's own targets, every one
    meters: list[Gap] = field(default_factory=list)   # NEEDS, in order, short or not

    @property
    def short_of(self) -> set[str]:
        return {m.what for m in self.meters if m.have < m.want}

    @property
    def size(self) -> int:
        return len(self.squad)

    @property
    def per_place(self) -> float:
        """Lakh per open place: the purse shared over every place the team may still fill."""
        return self.purse / self.places if self.places else 0.0


def targets(squad_size: int) -> dict[str, int]:
    """`game.auction.need`'s targets at this squad size, the same rounding it uses."""
    scale = squad_size / au.SQUAD_SIZE
    out = {b: round(au.BAND_TARGET[i] * scale) for i, b in enumerate(BANDS)}
    out["keeper"] = round(au.KEEPERS_WANTED * scale)
    out["bowling"] = round(au.BOWLERS_WANTED * scale)
    return out


def team_view(preset, short: str, name: str) -> TeamView:
    squad = list(preset.squads.get(short, []))
    size = preset.squad_size[short]
    bands = {b: 0 for b in BANDS}
    for c in squad:
        b = band_of(c)
        if b:
            bands[b] += 1
    keepers = sum(c.keeper_eligible for c in squad)
    bowlers = sum(c.has_bowl for c in squad)
    have = dict(bands, keeper=keepers, bowling=bowlers)
    gaps = [Gap(k, have[k], want) for k, want in targets(size).items() if have[k] < want]
    gaps.sort(key=lambda g: (-g.short, list(have).index(g.what)))
    overseas = sum(c.overseas is True for c in squad)
    order = {b: i for i, b in enumerate(BANDS)}
    squad.sort(key=lambda c: (order.get(band_of(c) or "tail", 3), c.name))
    return TeamView(
        short=short, name=name, squad=squad, purse=preset.purse[short],
        places=size - len(squad), must_buy=max(0, au.MINI_SQUAD_MIN - len(squad)),
        overseas=overseas, overseas_places=preset.overseas_cap[short] - overseas,
        bands=bands, keepers=keepers, bowlers=bowlers,
        pace=sum(is_specialist(c, "pace") for c in squad),
        spin=sum(is_specialist(c, "spin") for c in squad),
        gaps=gaps)


def is_specialist(card: Card, style: str) -> bool:
    """A bowler or all-rounder of this style -- not a batter who bowls the odd over."""
    return card.role in ("bowler", "allrounder") and card.bowling_style == style


def style_wants(views: list[TeamView]) -> dict[str, int]:
    """The league's median retained count of each style, rounded up: fewer is short."""
    return {s: math.ceil(statistics.median(getattr(v, s) for v in views)) for s in STYLES}


def teams(preset) -> list[TeamView]:
    """All ten, most money left first (ties: fewer players retained first, then name), each
    with its six NEEDS meters filled in."""
    out = [team_view(preset, s, n) for s, n in au.FRANCHISES if s in preset.purse]
    wants = style_wants(out)
    for v in out:
        want = targets(v.size + v.places)
        have = dict(v.bands, keeper=v.keepers, pace=v.pace, spin=v.spin)
        v.meters = [Gap(k, have[k], wants[k] if k in STYLES else want[k]) for k in NEEDS]
    return sorted(out, key=lambda t: (-t.purse, t.size, t.short))


def fills(card: Card) -> set[str]:
    """Which NEEDS a player could fill: his batting band if he bats in the top seven, the
    gloves if he keeps, his bowling style if he is a bowler or all-rounder."""
    out = set()
    b = band_of(card)
    if b in ("top", "middle", "finisher") and card.role in ("batter", "keeper", "allrounder"):
        out.add(b)
    if card.keeper_eligible:
        out.add("keeper")
    for s in STYLES:
        if is_specialist(card, s):
            out.add(s)
    return out


def released(preset) -> list[Card]:
    """Players released after playing IPL 2026 (status 'played' on the list, A298)."""
    return [c for c, _b, _cap in preset.lots if preset.status.get(c.person_id) == "played"]
