"""Playing for your country in Build a Career [A229].

After each IPL season the selectors look at it: an uncapped player may be called up, a player
in the side may keep his place or be dropped, and a dropped one may be recalled. Every rate
here is MEASURED, not chosen: each real IPL season of a player in the deck was set against
the international archives (Test, ODI and T20I), with the selection window running from June
after that IPL season to the next May, and banded by the season's card (70-79, 80-84, 85+).

Indian players (seasons up to 2024; counts in brackets):
  first call-up   7% (60/811)   27% (23/84)   49% (19/39)
  keeps place     65% (205/313) 86% (120/139) 97% (108/111)
  recalled        9% (23/249)   25% (13/53)   46% (13/28)

Overseas players are picked mainly on what they do at home, so IPL form moves their odds far
less, and the samples are small: first call-up 31% (17/55) at 70-79 and 8 of 16 above,
pooled to 50%; keeps place 87-92%; recalled 31 of 141 (22%) pooled across bands.

Internationals in a window when in the side, as deciles: Indian 3-35 (median 14, 433
windows), overseas 5-33 (median 18, 793); a debut window is shorter, median 3 and 7.

Pure: no database, and every draw comes from a stream of its own, `f"{seed}:nt:{k}"`, so
nothing here can move a match (A181's rule) or an offer.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

INDIA = "India"
# The countries an overseas career may play for: the nations that have sent players to the
# IPL. Afghanistan is offered although the archives hold none of its matches (A30): the
# rates are pooled across overseas players, so it needs no matches of its own.
OVERSEAS_NATIONS = ("Australia", "England", "South Africa", "New Zealand", "West Indies",
                    "Sri Lanka", "Afghanistan", "Bangladesh")

#                    70-79  80-84  85+
FIRST_CALL = {"indian": (0.07, 0.27, 0.49), "overseas": (0.31, 0.50, 0.50)}
KEEP = {"indian": (0.65, 0.86, 0.97), "overseas": (0.87, 0.88, 0.92)}
RECALL = {"indian": (0.09, 0.25, 0.46), "overseas": (0.22, 0.22, 0.22)}
CAPS_DECILES = {"indian": (3, 5, 8, 11, 14, 18, 23, 27, 35),
                "overseas": (5, 9, 12, 16, 18, 21, 25, 29, 33)}
DEBUT_MEDIAN = {"indian": 3, "overseas": 7}
CAP_MARK = 50                        # every fifty internationals is a milestone


def band(grade: int) -> int:
    return 0 if grade < 80 else 1 if grade < 85 else 2


def nation(overseas: bool, country: str) -> str:
    """Who you can play for: India for an Indian career, the chosen country for an overseas
    one, and nobody ("") for an overseas career with none (one from before A229)."""
    return INDIA if not overseas else country


@dataclass
class Selection:
    event: str                       # "debut" | "kept" | "dropped" | "recalled" | "" (not picked)
    caps: int                        # internationals played in the window after this season
    total: int                       # career internationals after it
    in_side: bool
    capped: bool


def select(state: dict, grade: int, seed: int, k: int, overseas: bool) -> Selection:
    """The selectors' verdict on the season graded `grade`, and the internationals of the
    window that follows. `state` is the career's national record so far: {"capped",
    "in_side", "caps"}; an empty dict is an uncapped player."""
    kind = "overseas" if overseas else "indian"
    rng = random.Random(f"{seed}:nt:{k}")
    b = band(grade)
    capped, in_side, total = bool(state.get("capped")), bool(state.get("in_side")), int(state.get("caps", 0))
    roll = rng.random()
    if not capped:
        picked = roll < FIRST_CALL[kind][b]
        event = "debut" if picked else ""
    elif in_side:
        picked = roll < KEEP[kind][b]
        event = "kept" if picked else "dropped"
    else:
        picked = roll < RECALL[kind][b]
        event = "recalled" if picked else ""
    caps = 0
    if picked:
        if event == "debut":
            # a debut window is the debut tour: between one match and twice the median less one
            caps = rng.randint(1, 2 * DEBUT_MEDIAN[kind] - 1)
        else:
            caps = rng.choice(CAPS_DECILES[kind])
    return Selection(event, caps, total + caps, picked, capped or picked)


def words(sel: Selection, country: str) -> str:
    """The season card's line, or "" when nothing happened."""
    if sel.event == "debut":
        return (f"Called up by {country}: {sel.caps} international{'s' if sel.caps != 1 else ''} "
                f"after the season.")
    if sel.event == "recalled":
        return (f"Recalled by {country}: {sel.caps} international{'s' if sel.caps != 1 else ''} "
                f"after the season.")
    if sel.event == "dropped":
        return f"Dropped by {country} after the season."
    if sel.event == "kept":
        return f"Kept your place with {country}: {sel.caps} more internationals ({sel.total} in all)."
    return ""
