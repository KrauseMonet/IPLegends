"""Skills and roles on a card [A258].

Reads the committed snapshot `tools.snapshot_skills` writes from the archive
(`etl.skills`): the skills each player-SEASON earned, the measured rating blocks, and each
season's job. Pure and database-free at runtime, like the deck (A107).

A skill is earned by the season (style and special, shrunk toward his career by as much as
the archive says a season can differ) or by a career at the top of a phase plus a top
season in it (elite). How many a card may SHOW is set by its level -- the higher of the
season's card and the player's CAREER STANDING (his second-best season card), ratified by
the user -- against blocks measured from what cards actually earn (`measure_blocks`). A
thin career with one hot season can carry a high card and no skill at all: there is
nothing behind it to show.

A card shows one skill per tier, the clearest in that tier. Which discipline is read off the
season's role (A26): a batter or keeper shows batting skills, a bowler bowling skills, an
all-rounder either.
"""

from __future__ import annotations

import functools
import gzip
import json
from dataclasses import dataclass
from pathlib import Path

from etl.skills import SKILL_BY_KEY, Skill

SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "player_skills.json.gz"
FORMAT = 2
# [A267] The rating at which each tier opens is DECLARED by the user: 80 / 87 / 94 on the
# 72-99 scale. Until A267 they were measured (`measure_blocks`, A258) -- 84 / 88 / 96 the
# last time; the snapshot still records the measured values beside it, for the record.
TIER_BLOCKS = {1: 80, 2: 87, 3: 94}
BLOCK_WIDTH = 4                         # levels pooled per band, to steady thin bands
BLOCK_MIN_CARDS = 30                    # a band thinner than this is noise, not evidence
BLOCK_SHARE = 0.5                       # of the top band's earning rate
# A batter's or keeper's few overs are part-time (A165), so they show no bowling skill. A
# bowler shows batting skills he EARNED: the season floors already demand he really batted
# (100 balls, or 30 in the situation), and A26 calls some genuine hitters bowlers.
DISC_FOR_ROLE = {"batter": ("bat",), "keeper": ("bat",), "bowler": ("bat", "bowl"),
                 "allrounder": ("bat", "bowl")}


def read_document() -> dict | None:
    try:
        with gzip.open(SNAPSHOT, "rt") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if doc.get("format") == FORMAT else None


@functools.lru_cache(maxsize=1)
def document() -> dict:
    """The snapshot, or an empty one: a missing file means no skills and no roles for
    anybody, never a guessed one."""
    return read_document() or {"skills": {}, "roles": {}, "cuts": {}, "metrics": {},
                               "values": {}}


def standing(cards) -> dict[str, int]:
    """{person: career standing}: his second-best season card. One season alone has no
    standing (0), so it is judged by its own card and nothing else."""
    by: dict[str, list[int]] = {}
    for c in cards:
        if c.display is not None:
            by.setdefault(c.person_id, []).append(c.display)
    return {p: (sorted(v, reverse=True)[1] if len(v) > 1 else 0) for p, v in by.items()}


def blocks() -> dict[int, int]:
    return dict(TIER_BLOCKS)


def slots(display: int | None, career: int, at: dict[int, int] | None = None) -> int:
    level = max(display or 0, career)
    return sum(level >= v for v in (at or blocks()).values())


def earned_tiers(card, held: dict) -> set[int]:
    """The tiers this card's season EARNED, for its role's discipline, ignoring any gate."""
    discs = DISC_FOR_ROLE.get(card.role or "", ("bat", "bowl"))
    got = held.get(card.person_id, {}).get(str(card.season_year), [])
    return {t for k, t, _ in got if k in SKILL_BY_KEY and SKILL_BY_KEY[k].disc in discs}


def measure_blocks(cards, held: dict) -> dict[int, int]:
    """Where each tier opens, read off what cards actually EARN with no gate at all. A
    card's level is the higher of its season card and the player's career standing; levels
    are pooled in bands of BLOCK_WIDTH. A tier opens at the lowest band from which every
    band above it (with BLOCK_MIN_CARDS or more cards) earns that tier at least BLOCK_SHARE
    as often as the very top band does: from there up, holding the skill is the ordinary
    thing for a card of that level rather than the exception."""
    stand = standing(cards)
    bands: dict[int, list[set]] = {}
    for c in cards:
        lvl = max(c.display or 0, stand.get(c.person_id, 0))
        lo = 99 - ((99 - lvl) // BLOCK_WIDTH) * BLOCK_WIDTH - (BLOCK_WIDTH - 1)
        bands.setdefault(lo, []).append(earned_tiers(c, held))
    ordered = sorted(lo for lo, v in bands.items() if len(v) >= BLOCK_MIN_CARDS)
    out = {}
    for tier in (1, 2, 3):
        rate = {lo: sum(tier in t for t in bands[lo]) / len(bands[lo]) for lo in ordered}
        bar = BLOCK_SHARE * rate[ordered[-1]]
        opens = ordered[-1]
        for lo in reversed(ordered):
            if rate[lo] < bar:
                break
            opens = lo
        out[tier] = max(opens, out.get(tier - 1, 0))
    return out


@dataclass(frozen=True)
class Shown:
    key: str
    label: str
    tier: int
    disc: str
    blurb: str


def card_skills(person_id: str, season: int | None, role: str | None, display: int | None,
                career: int, held: dict | None = None,
                at: dict[int, int] | None = None) -> list[Shown]:
    """What one card shows, lowest tier first: the skills THIS SEASON earned (A258), one
    per open tier, the clearest in each. `held` and `at` default to the snapshot's (tests
    pass their own)."""
    held = document()["skills"] if held is None else held
    n = slots(display, career, at)
    discs = DISC_FOR_ROLE.get(role or "", ("bat", "bowl"))
    best: dict[int, tuple[float, Skill]] = {}
    for key, tier, strength in held.get(person_id, {}).get(str(season), []):
        s = SKILL_BY_KEY.get(key)
        if s is None or s.disc not in discs or tier > n:
            continue
        if tier not in best or strength > best[tier][0]:
            best[tier] = (strength, s)
    return [Shown(s.key, s.label, s.tier, s.disc, s.blurb)
            for _t, (_st, s) in sorted(best.items())]


def season_roles(person_id: str, season: int | None, role: str | None = None,
                 roles: dict | None = None) -> dict:
    """{"bat": job, "bowl": job} for that season, either left out where he had none -- or
    where the season's role says it was not his job: a batter's few overs are part-time
    (A165), so a batter shows no bowling job. A bowler's batting job shows only where he
    had one (the roles table needs five innings for that)."""
    roles = document()["roles"] if roles is None else roles
    r = roles.get(person_id, {})
    out = {}
    for disc in DISC_FOR_ROLE.get(role or "", ("bat", "bowl")):
        job = r.get(disc, {}).get(str(season))
        if job:
            out[disc] = job
    return out
