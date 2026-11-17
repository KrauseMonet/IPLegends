"""The 2027 mini auction's starting point: projected 2027 cards, the retained squads, what
each team has left, and the list of players going under the hammer.

Pure: everything here is a function of the deck and the override files' rows, so the
snapshot tool can freeze it and a test can build it from hand-made rows.

**The 2027 card [A296/A297].** No season 2027 exists to rate, so a card is a PROJECTION,
and the projection was backtested before it was chosen (2,533 player-seasons, each
predicted from earlier seasons only): the last season alone predicts a season's card WORSE
than the league mean (MSE 36.2 against 30.6); a volume-weighted mean with each earlier
season worth 0.7 of the next, shrunk by 600 pseudo-balls toward the mean, does best (25.3).
The engine's halves were backtested the same way (decay 0.5-0.85 and ~300 pseudo-balls
best; the prior is 0, since they are season-centred). An honest projection COMPRESSES --
the spread of projected cards is about a third of a real season's -- so the number SHOWN
on the card is stretched back to a real season's spread, rank for rank and gap for gap
(a linear map, A44's argument against percentiles), while the engine plays the projection
itself. Ratified by the user, 2026-11-17.

**Newcomers.** A player with no IPL record has nothing to project. He is carded at the
POOLED level of real IPL debut seasons for his role and passport -- A48's rule (an unrated
player plays at his group's pooled level, never at a fabricated individual one) -- and
nothing about him is invented. Cross-league ratings are a later phase.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import date

from etl.feasibility import Card, Deck

DECAY_DISPLAY, K_DISPLAY = 0.7, 600      # the backtest's best rule for the card
DECAY_HALF, K_HALF = 0.7, 300            # and for the engine's halves
AUCTION_DAY = date(2026, 12, 16)
SEASON = 2027
DISPLAY_FLOOR, DISPLAY_CAP = 72, 99      # A267's scale
FIRST_SEASON = 2008
SPLIT_MIN = 20                           # pooled debut levels split by passport only above this

LIST_ROLE = {"Batter": "batter", "Bowler": "bowler", "All-Rounder": "allrounder",
             "Wicketkeeper": "keeper"}


def history(deck: Deck) -> dict[str, dict[int, Card]]:
    """person -> year -> his card; the larger-volume card where a season has two."""
    out: dict[str, dict[int, Card]] = defaultdict(dict)
    for fs in sorted(deck.cards_by_fs):
        for c in deck.cards_by_fs[fs]:
            old = out[c.person_id].get(c.season_year)
            if old is None or _volume(c) > _volume(old):
                out[c.person_id][c.season_year] = c
    return out


def _volume(c: Card) -> int:
    return (c.bat_balls or 0) + (c.bowl_balls or 0)


def deck_mean(deck: Deck) -> float:
    shown = [c.display for cs in deck.cards_by_fs.values() for c in cs if c.display is not None]
    return sum(shown) / len(shown)


def project_display(hist: dict[int, Card], mean: float) -> float:
    num = den = 0.0
    for y, c in hist.items():
        if c.display is None:
            continue
        w = DECAY_DISPLAY ** (SEASON - 1 - y) * (_volume(c) + 1)
        num += w * c.display
        den += w
    return (num + K_DISPLAY * mean) / (den + K_DISPLAY)


def project_half(hist: dict[int, Card], value: str, balls: str, log: bool = False) -> float | None:
    """A volume-weighted, decayed mean of one engine half, shrunk toward 0 (season-centred)
    -- or toward a multiplier of 1 on the log scale. None if he never had the discipline."""
    num = den = 0.0
    for y, c in hist.items():
        v = getattr(c, value)
        if v is None or not getattr(c, balls):
            continue
        w = DECAY_HALF ** (SEASON - 1 - y) * getattr(c, balls)
        num += w * (math.log(v) if log else v)
        den += w
    if den == 0:
        return None
    mean = num / (den + K_HALF)
    return math.exp(mean) if log else mean


def stretch(raw: dict[str, float], ref_mean: float, ref_sd: float) -> dict[str, int]:
    """The projected cards mapped linearly onto a real season's mean and spread, kept to the
    72-99 scale. Rank and every gap survive in proportion."""
    vals = list(raw.values())
    mu, sd = statistics.fmean(vals), statistics.pstdev(vals) or 1.0
    return {k: max(DISPLAY_FLOOR, min(DISPLAY_CAP, round(ref_mean + (v - mu) * ref_sd / sd)))
            for k, v in raw.items()}


def projected_card(hist: dict[int, Card], display: int, franchise: str | None) -> Card:
    """His latest card, carrying the projected engine halves and the stretched display. Where
    he bats, his role, passport, keeping and style come from the latest season."""
    last = hist[max(hist)]
    halves = dict(
        bat_scoring=project_half(hist, "bat_scoring", "bat_balls"),
        bat_dismissal=project_half(hist, "bat_dismissal", "bat_balls", log=True),
        bowl_scoring=project_half(hist, "bowl_scoring", "bowl_balls"),
        bowl_dismissal=project_half(hist, "bowl_dismissal", "bowl_balls", log=True),
        bat=project_half(hist, "bat", "bat_balls"),
        bowl=project_half(hist, "bowl", "bowl_balls"),
    )
    # A discipline he had last season stays; one he never had stays None.
    for half in ("bat", "bowl"):
        if getattr(last, half) is None:
            halves[half] = None
            halves[f"{half}_scoring"] = halves[f"{half}_dismissal"] = None
    # The box score (runs, balls, wickets) stays his last real season's, which is what the
    # card shows beside the projection; nothing is made up for 2027.
    return replace(last, display=display, season_year=SEASON, franchise=franchise, **halves)


# ---------------------------------------------------------------- newcomers

@dataclass(frozen=True)
class Pooled:
    display: int
    positions: frozenset[int]
    bat: float | None
    bowl: float | None
    bat_scoring: float | None
    bat_dismissal: float | None
    bowl_scoring: float | None
    bowl_dismissal: float | None
    bat_balls: int | None
    bowl_balls: int | None
    n: int


def pooled_debuts(deck: Deck, weaker_half: bool = False) -> dict[tuple[str, bool | None], Pooled]:
    """The pooled level of real IPL debut seasons, by role and -- where each side has at
    least SPLIT_MIN debuts -- by passport (key overseas True/False), else pooled (None).

    `weaker_half` pools only the lower half of each group by card. Real debut seasons are
    players a team WANTED and played; last year's unsold players with no IPL record are
    the ones nobody did, so they are carded at the weaker half -- a declared rule, since
    the archive cannot say how good a player who never played is."""
    groups: dict[tuple[str, bool | None], list[Card]] = defaultdict(list)
    for hist in history(deck).values():
        first = hist[min(hist)]
        if first.season_year == FIRST_SEASON:
            continue          # the founding season's "debuts" were established players
        groups[(first.role, first.overseas)].append(first)
        groups[(first.role, None)].append(first)
    out = {}
    for key, cards in groups.items():
        if weaker_half:
            cards = sorted(cards, key=lambda c: (c.display, c.person_id))[:max(1, len(cards) // 2)]
        out[key] = _pool(cards)
    return out


def _pool(cards: list[Card]) -> Pooled:
    def wmean(value, balls, log=False):
        pairs = [(getattr(c, value), getattr(c, balls)) for c in cards
                 if getattr(c, value) is not None and getattr(c, balls)]
        if len(pairs) * 2 < len(cards):        # most of the group never had it
            return None
        tot = sum(b for _, b in pairs)
        m = sum((math.log(v) if log else v) * b for v, b in pairs) / tot
        return math.exp(m) if log else m

    positions = Counter(c.positions for c in cards).most_common(1)[0][0]
    bat = wmean("bat", "bat_balls")
    bowl = wmean("bowl", "bowl_balls")
    return Pooled(
        display=round(statistics.fmean(c.display for c in cards)), positions=positions,
        bat=bat, bowl=bowl,
        bat_scoring=wmean("bat_scoring", "bat_balls") if bat is not None else None,
        bat_dismissal=wmean("bat_dismissal", "bat_balls", log=True) if bat is not None else None,
        bowl_scoring=wmean("bowl_scoring", "bowl_balls") if bowl is not None else None,
        bowl_dismissal=wmean("bowl_dismissal", "bowl_balls", log=True) if bowl is not None else None,
        bat_balls=round(statistics.median(c.bat_balls or 0 for c in cards)) if bat is not None else None,
        bowl_balls=round(statistics.median(c.bowl_balls or 0 for c in cards)) if bowl is not None else None,
        n=len(cards))


def pooled_for(pools: dict, role: str, overseas: bool) -> Pooled:
    split = pools.get((role, overseas))
    return split if split is not None and split.n >= SPLIT_MIN else pools[(role, None)]


def newcomer_card(person_id: str, name: str, role: str, overseas: bool, pools: dict,
                  franchise: str | None = None) -> Card:
    p = pooled_for(pools, role, overseas)
    return Card(fs_id=-1, person_id=person_id, name=name, bat=p.bat, bowl=p.bowl,
                role=role, keeper_eligible=role == "keeper", season_year=SEASON,
                franchise=franchise, overseas=overseas, bowling_style=None, display=p.display,
                positions=p.positions,
                bat_scoring=p.bat_scoring, bat_dismissal=p.bat_dismissal,
                bowl_scoring=p.bowl_scoring, bowl_dismissal=p.bowl_dismissal)


# ---------------------------------------------------------------- the preset

@dataclass
class Preset:
    squads: dict[str, list[Card]]          # retained, per team short code
    purse: dict[str, int]                  # lakh left
    squad_size: dict[str, int]             # retained + places left (25 at most)
    overseas_cap: dict[str, int]           # retained overseas + overseas places left (8 at most)
    lots: list[tuple[Card, int, bool]]     # (card, base price in lakh, capped) in list order
    ages: dict[str, float] = field(default_factory=dict)   # person -> age on auction day
    debutants: frozenset[str] = frozenset()                # no IPL record
    notes: dict[str, str] = field(default_factory=dict)    # person -> what his card rests on


def age_on(birth: str) -> float:
    y, m, d = map(int, birth.split("-"))
    return (AUCTION_DAY - date(y, m, d)).days / 365.25


def newcomer_id(name: str, cricsheet: str = "") -> str:
    """A newcomer's person id: his Cricsheet registry id where known (the same id space as
    every IPL player's), else a name key that cannot collide with a registry id."""
    if cricsheet:
        return cricsheet
    return "new:" + "".join(ch for ch in name.lower() if ch.isalpha())


def build_preset(deck: Deck, retained_rows: list[dict], list_rows: list[dict],
                 purse_rows: list[dict], birth_rows: list[dict], base_price) -> Preset:
    """`base_price(card)` is the auction's own tier rule, for a player with no real base."""
    hist = history(deck)
    mean = deck_mean(deck)
    ref = [c.display for cs in deck.cards_by_fs.values() for c in cs if c.season_year == SEASON - 1]
    ref_mean, ref_sd = statistics.fmean(ref), statistics.pstdev(ref)
    pools = pooled_debuts(deck)
    weaker = pooled_debuts(deck, weaker_half=True)

    def pid_of(row: dict) -> str:
        p = (row.get("person_id_checked") or row.get("person_id") or "").strip()
        return "" if p == "debutant" else p

    with_record = {pid_of(r) for r in retained_rows + list_rows if pid_of(r) in hist}
    shown = stretch({p: project_display(hist[p], mean) for p in sorted(with_record)}, ref_mean, ref_sd)

    notes: dict[str, str] = {}

    def card_for(row: dict, franchise: str | None) -> Card:
        pid = pid_of(row)
        if pid in hist:
            h = hist[pid]
            last = h[max(h)]
            n = len(h)
            notes[pid] = (f"A 2027 projection from {n} IPL season{'s' if n != 1 else ''}. "
                          f"Figures shown: {last.season_year} with {last.franchise}.")
            return projected_card(h, shown[pid], franchise)
        role = LIST_ROLE[(row.get("role_checked") or row.get("role") or "").strip()]
        # chosen for this season's form, or retained by a franchise: the full debut level;
        # last year's unsold with no record: the weaker half
        level = weaker if row.get("source") == "unsold" else pools
        card = newcomer_card(newcomer_id(row["name"], row.get("cricsheet_id", "")), row["name"], role,
                             row["overseas"] == "yes", level, franchise)
        why = ("Unsold last year: carded at the weaker half of IPL debut seasons for his role."
               if row.get("source") == "unsold" else
               "Carded at the average IPL debut season for his role.")
        seen = (f" This season: {row['evidence']}." if row.get("evidence") and row.get("source") == "newcomer"
                else "")
        notes[card.person_id] = f"No IPL record. {why}{seen}"
        return card

    squads: dict[str, list[Card]] = defaultdict(list)
    for r in retained_rows:
        squads[r["short"]].append(card_for(r, r["team"]))
    purse, size, cap = {}, {}, {}
    for r in purse_rows:
        s = r["short"]
        purse[s] = int(r["purse_left_lakh"])
        size[s] = len(squads[s]) + int(r["slots"])
        cap[s] = sum(c.overseas is True for c in squads[s]) + int(r["overseas_slots"])
    taken = {c.person_id for cs in squads.values() for c in cs}
    lots = []
    for r in list_rows:
        if (r.get("include") or "").strip().lower() == "n":
            continue
        card = card_for(r, None)
        if card.person_id in taken:
            continue
        taken.add(card.person_id)
        base = int(r["base_2026_lakh"]) if r.get("base_2026_lakh") else base_price(card)
        capped = r.get("capped") == "yes"
        lots.append((card, base, capped))
    births = {b["person_id"]: b["birth_date"] for b in birth_rows if b.get("birth_date")}
    ages = {p: round(age_on(births[p]), 2) for p in taken if p in births}
    debutants = frozenset(p for p in taken if p not in hist)
    return Preset(dict(squads), purse, size, cap, lots, ages, debutants,
                  {p: t for p, t in notes.items() if p in taken})
