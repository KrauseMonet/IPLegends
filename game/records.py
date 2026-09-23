"""The archive-wide record book -- the deck's own extremes, not one draft or one season.

Pure function of the `Deck` (`etl.feasibility`) already loaded into memory at boot
(A107): no database call, no new ETL, nothing here that was not already sitting on a
`Card` the draft screen shows every day. Every figure is either a plain archive COUNT
(runs, wickets), which needs no volume floor at all -- A115's own argument for boundary
counts applies just as well here, twelve wickets are twelve wickets in four matches or
fourteen -- or a per-ball RATE gated by A33's own already-ratified floors (100 balls
faced for batting, 150 legal balls bowled for bowling), never a floor invented fresh for
this page.
"""

from __future__ import annotations

from dataclasses import dataclass

from etl.feasibility import Card, Deck

# A33's floors, reused rather than reinvented -- the same two numbers the draft's own
# rating gate uses, measured from where the raw top-5 lists stop moving.
BAT_BALLS_FLOOR = 100
BOWL_BALLS_FLOOR = 150

ROLES = ("batter", "bowler", "allrounder", "keeper")


@dataclass(frozen=True)
class RecordRow:
    name: str
    team: str | None
    value: float
    detail: str


def _all_cards(deck: Deck) -> list[Card]:
    return [c for cards in deck.cards_by_fs.values() for c in cards]


def _team(c: Card) -> str | None:
    if not c.franchise or not c.season_year:
        return None
    return f"{c.franchise} {c.season_year}"


def top_rated(deck: Deck, limit: int = 10, role: str | None = None) -> list[RecordRow]:
    """Highest `display` rating, optionally restricted to one role (A26/A76's role, not
    a discipline) -- `display` already blends both disciplines (A55) so a card's single
    face value is the fair thing to rank on, the same number every draft screen shows.
    """
    cards = [c for c in _all_cards(deck) if c.display is not None]
    if role is not None:
        cards = [c for c in cards if c.role == role]
    cards.sort(key=lambda c: (c.display, c.name), reverse=True)
    return [
        RecordRow(c.name, _team(c), float(c.display), f"{c.role or '?'} · {c.season_year}")
        for c in cards[:limit]
    ]


def most_runs(deck: Deck, limit: int = 10) -> list[RecordRow]:
    cards = [c for c in _all_cards(deck) if c.bat_runs is not None]
    cards.sort(key=lambda c: (c.bat_runs, c.name), reverse=True)
    return [
        RecordRow(c.name, _team(c), float(c.bat_runs), f"off {c.bat_balls} balls")
        for c in cards[:limit]
    ]


def most_wickets(deck: Deck, limit: int = 10) -> list[RecordRow]:
    cards = [c for c in _all_cards(deck) if c.bowl_wickets is not None]
    cards.sort(key=lambda c: (c.bowl_wickets, c.name), reverse=True)
    return [
        RecordRow(c.name, _team(c), float(c.bowl_wickets), f"off {c.bowl_balls} balls")
        for c in cards[:limit]
    ]


def best_strike_rate(deck: Deck, limit: int = 10) -> list[RecordRow]:
    cards = [
        c for c in _all_cards(deck)
        if c.bat_balls is not None and c.bat_balls >= BAT_BALLS_FLOOR
    ]
    cards.sort(key=lambda c: (c.bat_runs / c.bat_balls, c.name), reverse=True)
    return [
        RecordRow(
            c.name, _team(c), round(100 * c.bat_runs / c.bat_balls, 1),
            f"{c.bat_runs} off {c.bat_balls}",
        )
        for c in cards[:limit]
    ]


def best_economy(deck: Deck, limit: int = 10) -> list[RecordRow]:
    cards = [
        c for c in _all_cards(deck)
        if c.bowl_balls is not None and c.bowl_balls >= BOWL_BALLS_FLOOR
    ]
    cards.sort(key=lambda c: (c.bowl_runs / c.bowl_balls, c.name))
    return [
        RecordRow(
            c.name, _team(c), round(6 * c.bowl_runs / c.bowl_balls, 2),
            f"{c.bowl_wickets} wkts, {c.bowl_balls} balls",
        )
        for c in cards[:limit]
    ]
