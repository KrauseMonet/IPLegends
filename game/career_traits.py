"""Who your player becomes in Build a Career: four traits your choices build (A245).

Pure: nothing here plays a match or reads the deck. The career loop moves the traits after
each season and hands them to the season; the season reads them through `styled` (every
match) and `big_match` (key moments only).

  temper  calm (-) .. aggressive (+). A STYLE, not a value: the measured trade-off a key
          moment's call already uses (A204's TRADE_SLOPE), so an aggressive batter scores
          faster and gets out more often, an aggressive bowler takes more wickets and costs
          more. It does not move the Rating.
  nerve   how you play when it matters: must-wins, make-or-breaks and playoffs only.
  work    small and for good, and the one trait that can take you PAST the potential shown:
          it adds to your ability after the career's arc is capped, `WORK_RATING` Rating
          points a step.
  lead    no change to how you bat or bowl. It changes the club: how far it stands by you,
          whether the dressing room asks you to lead.

Each is a number in [-TRAIT_MAX, TRAIT_MAX], moved by whole and part steps. A move is
certain -- the choice decides it, never a coin flip -- and is said on the season card in
words. Every number here is declared game design; the guardrail is the calibration
(`tools.career_calibration`, policies "virtue" and "vice"), not any one constant."""

from __future__ import annotations

import dataclasses
import math

TRAITS = ("temper", "nerve", "work", "lead")
TRAIT_MAX = 3.0

# A step of temper is a third of a key moment's call (ATTACK_SHIFT = 0.15 runs a ball for one
# match), so a fully aggressive player plays every match as one attacking call.
TEMPER_STEP = 0.05
TRADE_SLOPE = 1.25                   # A204's measured d ln(dismissal odds) per run a ball
# A step of nerve, in big matches only: under one level of a mid-season form swing (A230's
# FORM_STEP 0.05, FORM_ODDS 0.15), since it lands in the matches that matter most.
NERVE_STEP = 0.025
NERVE_ODDS = 0.075
# A step of work, in Rating points for good.
WORK_RATING = 1.5
CALL_STEP = 1 / 3                    # a key-moment call: three the same way make one step

# The words a trait reads as at each level, by kind of player. "" is unremarkable.
WORDS = {
    "temper": {-3: "Unflappable at the crease", -2: "A patient player", -1: "Calm",
               1: "Likes to take it on", 2: "An aggressive player", 3: "All-out attack"},
    "nerve": {-3: "Freezes on the big stage", -2: "Nervy in big games", -1: "",
              1: "", 2: "Steely in big games", 3: "A big-match player"},
    "work": {-3: "Coasting", -2: "Cuts corners", -1: "",
             1: "", 2: "A hard worker", 3: "The first in the nets, the last out"},
    "lead": {-3: "A loner in the dressing room", -2: "Keeps to himself", -1: "",
             1: "", 2: "A leader in the dressing room", 3: "The heart of the side"},
}
BOWLER_TEMPER = {-3: "A miser with the ball", -2: "A patient bowler", -1: "Calm",
                 1: "Bowls to take wickets", 2: "An attacking bowler", 3: "All-out attack"}

# What a move says on the season card, by trait and direction.
MOVED = {
    ("temper", 1): "You are playing with more aggression.",
    ("temper", -1): "You are playing with more patience.",
    ("nerve", 1): "You are growing into the big occasions.",
    ("nerve", -1): "The big occasions are getting to you.",
    ("work", 1): "Your work ethic is making you a better player.",
    ("work", -1): "You are cutting corners, and it shows.",
    ("lead", 1): "The dressing room looks to you more and more.",
    ("lead", -1): "You are drifting away from the dressing room.",
}


def empty() -> dict[str, float]:
    return {t: 0.0 for t in TRAITS}


def nudge(traits: dict, moves) -> dict[str, float]:
    """`traits` moved by `moves` (pairs or a dict), each clamped to [-TRAIT_MAX, TRAIT_MAX].
    A new dict: the caller's is never changed."""
    out = {t: float(traits.get(t, 0.0)) for t in TRAITS}
    for t, d in (moves.items() if isinstance(moves, dict) else moves):
        if t not in out:
            raise ValueError(f"no trait {t!r}")
        out[t] = max(-TRAIT_MAX, min(TRAIT_MAX, round(out[t] + d, 6)))
    return out


def level(x: float) -> int:
    """A trait's whole level, for its words: rounded toward zero, so a third of a step is not
    yet a change of character."""
    return int(x) if x >= 0 else -int(-x)


def _shift(card, d: float):
    """Scoring up and dismissal odds up together by TRADE_SLOPE (batting); for bowling, runs
    saved down and wicket odds up. `d = 0` is the card exactly."""
    if not d:
        return card
    mult = math.exp(TRADE_SLOPE * d)
    return dataclasses.replace(
        card,
        bat_scoring=None if card.bat_scoring is None else card.bat_scoring + d,
        bat_dismissal=None if card.bat_dismissal is None else card.bat_dismissal * mult,
        bowl_scoring=None if card.bowl_scoring is None else card.bowl_scoring - d,
        bowl_dismissal=None if card.bowl_dismissal is None else card.bowl_dismissal * mult)


def styled(card, traits: dict | None):
    """Your card for every match: your temper applied. Identity at temper 0."""
    if not traits:
        return card
    return _shift(card, traits.get("temper", 0.0) * TEMPER_STEP)


def big_match(card, traits: dict | None):
    """Your card in a key moment: your nerve applied, better (or worse) at everything a
    little. Identity at nerve 0."""
    n = (traits or {}).get("nerve", 0.0)
    if not n:
        return card
    d, mult = n * NERVE_STEP, math.exp(-NERVE_ODDS * n)
    return dataclasses.replace(
        card,
        bat_scoring=None if card.bat_scoring is None else card.bat_scoring + d,
        bat_dismissal=None if card.bat_dismissal is None else card.bat_dismissal * mult,
        bowl_scoring=None if card.bowl_scoring is None else card.bowl_scoring + d,
        bowl_dismissal=None if card.bowl_dismissal is None else card.bowl_dismissal / mult)


def work_points(traits: dict | None) -> float:
    return (traits or {}).get("work", 0.0) * WORK_RATING


def lean(scoring: float | None, dismissal: float | None, bowling: bool) -> float:
    """Where a real player's numbers sit along the trade-off, in runs a ball: positive is
    aggressive (a batter who scores fast and gets out; a bowler who takes wickets and leaks),
    negative patient. His numbers projected onto the direction `_shift` moves a card."""
    if scoring is None or dismissal is None or dismissal <= 0:
        return 0.0
    s = -scoring if bowling else scoring
    return (s + TRADE_SLOPE * math.log(dismissal)) / (1 + TRADE_SLOPE ** 2)


LEAN_CLEAR = 0.02                    # runs a ball along the trade: a style clear enough to pass on


def style_from(scoring, dismissal, bowling: bool) -> int:
    """+1, -1 or 0: the temper a mentor passes on, from his own real numbers."""
    x = lean(scoring, dismissal, bowling)
    return 1 if x >= LEAN_CLEAR else -1 if x <= -LEAN_CLEAR else 0


def words(traits: dict | None, bowler: bool = False) -> list[str]:
    """The traits as the player card shows them: words only, never numbers."""
    out = []
    for t in TRAITS:
        lv = level((traits or {}).get(t, 0.0))
        table = BOWLER_TEMPER if t == "temper" and bowler else WORDS[t]
        w = table.get(lv, "")
        if w:
            out.append(w)
    return out


def changes(before: dict, after: dict) -> list[tuple[str, str]]:
    """What moved a whole level between two trait sets, as (sentence, tone) for the season
    card. Temper is a style: its tone is even."""
    out = []
    for t in TRAITS:
        a, b = level(before.get(t, 0.0)), level(after.get(t, 0.0))
        if a == b:
            continue
        d = 1 if b > a else -1
        out.append((MOVED[(t, d)], "" if t == "temper" else "up" if d > 0 else "down"))
    return out


def key(traits: dict | None) -> tuple:
    """Traits as part of a season's cache key."""
    return tuple(round((traits or {}).get(t, 0.0), 6) for t in TRAITS)
