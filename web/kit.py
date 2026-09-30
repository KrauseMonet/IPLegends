"""A drafted side's own identity: a team name, a monogram and a colour [A151].

A drafted twelve is a mix of franchises and so has no crest; a kit is what it wears in
their place -- on the scoreboard, in the table, on results rows and the journey card.
Purely cosmetic: nothing in the engine reads a kit, and a kit never enters a draft state,
because that string is the replay contract and a saved game's natural key (A102) -- the
same season saved under two team names would otherwise count as two games.

Where a kit lives:
    solo and the daily   the player's browser, and their account when signed in
    a draft room         `room_players.kit_*` (migration 036), so every seat sees it
    an auction           nowhere -- the side IS a real franchise, with its own crest

The palette is served to the page through `/api/meta` rather than copied into
JavaScript, and each colour's three shades come from `web.colours`, the same derivation
the franchise crests use -- so a scarlet kit and a scarlet crest carry identical shades.
The BASE hues are hand-picked, which is the one difference from the crests (A145 derives
those from the images): a player's choice has no image to derive from.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass

from web.colours import colours_from, hex_rgb

NAME_MAX = 24
MONOGRAM_MAX = 3
_MONOGRAM = re.compile(r"^[A-Z0-9]{1,3}$")

# key -> (label, base hue). Order is the order the picker shows, and the order default
# colours are handed out to seats in a room.
PALETTE: dict[str, tuple[str, str]] = {
    "gold": ("Gold", "#f5b83d"),
    "scarlet": ("Scarlet", "#d7263d"),
    "royal": ("Royal blue", "#2f5bd6"),
    "emerald": ("Emerald", "#1fa463"),
    "orange": ("Orange", "#f26b21"),
    "violet": ("Violet", "#9a4fd6"),
    "sky": ("Sky", "#3aa6e8"),
    "magenta": ("Magenta", "#e0379a"),
    "teal": ("Teal", "#14a3a3"),
    "lime": ("Lime", "#8cc63f"),
    "indigo": ("Indigo", "#5b4bd1"),
    "silver": ("Silver", "#9aa6b8"),
}

# What a solo player's side is called before they have chosen anything -- the name the
# site has always used, so nothing reads differently to someone who never opens the
# picker.
SOLO_DEFAULT = {"name": "Your eleven", "monogram": "YOU", "colour": "gold"}


class KitError(ValueError):
    """A kit that cannot be accepted -- refused with a 4xx, never a 500."""


@dataclass(frozen=True)
class Kit:
    name: str
    monogram: str
    colour: str

    def to_dict(self) -> dict:
        return asdict(self)


def palette() -> list[dict]:
    """The palette as the page reads it: every colour with its three derived shades."""
    return [{"key": key, "label": label, **colours_from(hex_rgb(base))}
            for key, (label, base) in PALETTE.items()]


def clean_name(raw: str) -> str:
    # Whitespace runs collapse, and every control or format character is refused rather
    # than stripped: a name that arrives carrying one was not typed into a text box.
    name = " ".join(str(raw).split())
    if any(unicodedata.category(ch).startswith("C") for ch in name):
        raise KitError("a team name cannot contain control characters")
    # Same rule as a room seat's name (web/rooms.py `clean_player_name`): the page escapes
    # every name it draws, and this keeps markup out of storage behind that.
    if "<" in name or ">" in name:
        raise KitError("a team name cannot contain < or >")
    if not name:
        raise KitError("give your team a name")
    if len(name) > NAME_MAX:
        raise KitError(f"a team name is at most {NAME_MAX} characters")
    return name


def parse_kit(raw) -> Kit:
    """A kit from untrusted input, or KitError. The monogram is upper-cased, since a
    badge is always drawn in capitals and "rcb" and "RCB" are the same monogram."""
    if not isinstance(raw, dict):
        raise KitError("a kit is a name, a monogram and a colour")
    name = clean_name(raw.get("name", ""))
    monogram = str(raw.get("monogram", "")).strip().upper()
    if not _MONOGRAM.match(monogram):
        raise KitError(f"a monogram is 1 to {MONOGRAM_MAX} letters or digits")
    colour = raw.get("colour")
    if colour not in PALETTE:
        raise KitError(f"unknown colour {colour!r}")
    return Kit(name, monogram, colour)


def initials(name: str) -> str:
    """A monogram suggested by a name: the initials of its words, or a one-word name's
    first three letters ("Krause" -> "KRA", never a lone "K"). Letters and digits only."""
    words = ["".join(ch for ch in w if ch.isascii() and ch.isalnum()) for w in name.split()]
    words = [w for w in words if w]
    if not words:
        return "XI"
    letters = "".join(w[0] for w in words) if len(words) > 1 else words[0]
    return letters.upper()[:MONOGRAM_MAX]


def default_kit(name: str, seat: int, taken_monograms: set[str],
                taken_colours: set[str]) -> Kit:
    """The kit a room seat wears until its player chooses one: the seat's own name, its
    initials, and the first palette colour nobody else in the room has -- starting from
    the seat's own place in the palette, so a room's defaults spread across it rather than
    every seat that never chose landing on gold.

    A monogram another seat already wears gets a digit ("KM" -> "KM2"), since two identical
    badges on one scoreboard would be the one thing a monogram exists to prevent."""
    base = initials(name)
    monogram = base
    n = 2
    while monogram in taken_monograms:
        monogram = (base[:MONOGRAM_MAX - 1] + str(n))[:MONOGRAM_MAX]
        n += 1
        if n > 9:
            break
    keys = list(PALETTE)
    colour = next((keys[(seat + i) % len(keys)] for i in range(len(keys))
                   if keys[(seat + i) % len(keys)] not in taken_colours),
                  keys[seat % len(keys)])
    try:
        # A seat name may run to 40 characters; a team name stops at NAME_MAX.
        clean = clean_name(" ".join(name.split())[:NAME_MAX])
    except KitError:
        clean = "Team " + monogram
    return Kit(clean, monogram, colour)
