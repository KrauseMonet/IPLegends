"""Franchise crests: which image a team shows, for a given season.

Keyed on the franchise-season's own DISPLAY NAME and year -- `franchise_seasons.display_name`,
the era-correct string the rest of the site already shows ("Delhi Daredevils" in 2012,
"Delhi Capitals" in 2020) -- because the crest is a fact about the same era the name is. A
name alone is not enough: Kings XI changed crest in 2020 while keeping its name until
2021, so an entry can carry a year range.

The images themselves are built by `tools.build_crests` into `web/static/crests/`, with a
content hash in every file name (A106: a changed image must be a changed URL). This module
reads the generated `crest_files.json` to learn those names, so the resolver and the files
cannot disagree about what exists.

    crest_url("Delhi Daredevils", 2012) -> "/static/crests/DD-1a2b3c4d5e.webp"
    crest_url("Delhi Capitals")          -> the franchise's CURRENT crest (auction teams)

Returns None for anything not in the table -- a user's own drafted side, say -- and the
page falls back to its initials badge. `tests/test_crests.py` pins that every one of the
166 franchise-seasons in the deck resolves, so None never means "a crest we forgot".
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from functools import cache

FILES_JSON = pathlib.Path(__file__).resolve().parent / "crest_files.json"


@dataclass(frozen=True)
class Crest:
    key: str                  # the source file's stem in assets/crests/
    names: tuple[str, ...]    # display names it serves
    first: int | None = None  # first season it serves, inclusive; None = from the start
    last: int | None = None   # last season, inclusive; None = to this day


# Sources and provenance are recorded in assets/crests/SOURCES.md.
CRESTS: tuple[Crest, ...] = (
    Crest("CSK", ("Chennai Super Kings",)),
    Crest("DCH", ("Deccan Chargers",)),
    Crest("DD", ("Delhi Daredevils",)),
    Crest("DC", ("Delhi Capitals",)),
    # Kings XI Punjab adopted the lion shield in 2020, a year before the name followed.
    Crest("KXIP", ("Kings XI Punjab",), last=2019),
    Crest("PBKS", ("Kings XI Punjab",), first=2020),
    Crest("PBKS", ("Punjab Kings",)),
    Crest("KKR", ("Kolkata Knight Riders",)),
    Crest("MI", ("Mumbai Indians",)),
    Crest("RR", ("Rajasthan Royals",)),
    # One RCB crest for every season under either spelling (A332): the replacement set has
    # no separate Bangalore-era crest, and keeping the official one would defeat the change.
    Crest("RCB", ("Royal Challengers Bangalore", "Royal Challengers Bengaluru")),
    Crest("KTK", ("Kochi Tuskers Kerala",)),
    Crest("PWI", ("Pune Warriors",)),
    Crest("SRH", ("Sunrisers Hyderabad",)),
    Crest("GL", ("Gujarat Lions",)),
    Crest("RPS", ("Rising Pune Supergiants", "Rising Pune Supergiant")),
    Crest("GT", ("Gujarat Titans",)),
    Crest("LSG", ("Lucknow Super Giants",)),
)


# Each franchise's colour, PICKED BY HAND [A333] -- reversing A145's rule that colours are
# derived from the crest. The replacement crests all wear gold frames, and the derivation
# put about ten teams in gold or orange, so KKR, RCB and GT read alike on one scoreboard.
# Picked for the franchise's own identity first and then for distance from every team it
# shared a season with: `tests/test_crests.py` holds every same-season pair at least
# MIN_SEASON_DELTA_E apart (CIE76, ~2 is just noticeable). Three compromises, because ten
# current teams cannot all have their own red or blue: Punjab is SILVER (Kings XI's old
# second colour; RCB keeps the red), Gujarat Titans ANTIQUE GOLD, and Delhi a LIGHT
# periwinkle so it stands apart from Mumbai's royal blue and Kolkata's purple. One
# franchise, one colour across a rename: Daredevils = Capitals, Kings XI = Punjab Kings.
COLOURS: dict[str, str] = {
    "CSK": "#fdd20a",   # lemon yellow
    "MI": "#1a62d6",    # royal blue
    "KKR": "#7b45c9",   # purple
    "RCB": "#da1b2b",   # red
    "RR": "#ec2f8f",    # pink
    "SRH": "#f26a1b",   # orange
    "DC": "#91a6ff",    # periwinkle
    "DD": "#91a6ff",
    "PBKS": "#c9cfd9",  # silver
    "KXIP": "#c9cfd9",
    "GT": "#c99d3e",    # antique gold
    "LSG": "#35c8ef",   # sky blue
    "DCH": "#5fb0e2",   # steel sky
    "PWI": "#22b8a6",   # turquoise
    "KTK": "#e8892b",   # saffron
    "GL": "#f2a11f",    # amber
    "RPS": "#c73b9c",   # magenta
}
MIN_SEASON_DELTA_E = 25

@cache
def _files() -> dict[str, str]:
    """key -> file name under /static/crests/. Empty if the crests were never built,
    so a missing build degrades to initials badges rather than a crash."""
    try:
        return json.loads(FILES_JSON.read_text())["files"]
    except (OSError, ValueError, KeyError):
        return {}


def crest_key(name: str | None, year: int | None = None) -> str | None:
    """The crest key for a display name in a season. With no year, the entry that runs
    to this day -- the franchise as it is now."""
    if not name:
        return None
    for c in CRESTS:
        if name not in c.names:
            continue
        if year is None:
            if c.last is None:
                return c.key
        elif (c.first is None or year >= c.first) and (c.last is None or year <= c.last):
            return c.key
    return None


def all_crests() -> dict[str, str]:
    """Every crest, key -> URL, in the table's order (current and defunct interleaved as
    the table lists them). For the home page's strip of every team there has been."""
    out = {}
    for c in CRESTS:
        file = _files().get(c.key)
        if file and c.key not in out:
            out[c.key] = f"/static/crests/{file}"
    return out


def unambiguous_crest(name: str | None) -> str | None:
    """A name's crest ONLY if every season under that name wore the same one. For a
    guessing game about the year: Kings XI Punjab wore two crests, and showing either
    would hint at, or mislead about, the very season being asked for."""
    keys = {c.key for c in CRESTS if name in c.names}
    return crest_url(name) if len(keys) == 1 else None


def crest_url(name: str | None, year: int | None = None) -> str | None:
    key = crest_key(name, year)
    file = _files().get(key) if key else None
    return f"/static/crests/{file}" if file else None


def franchise_crest(name: str) -> str | None:
    """A franchise's LATEST crest, by its canonical name -- for a header or a tile, where
    there is no single season to ask about. For a defunct club that is the last crest it
    wore; `crest_url` with no year only knows the clubs still playing. [A182]"""
    best = None
    for c in CRESTS:
        if name in c.names and (best is None or (c.last or 9999) > (best.last or 9999)):
            best = c
    file = _files().get(best.key) if best else None
    return f"/static/crests/{file}" if file else None
