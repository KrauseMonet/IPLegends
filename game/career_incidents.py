"""Mid-season incidents in Build a Career (A230): the things that happen to a player during a
season, set off by a match that really happened -- a hamstring that goes in the field, a
duck the papers will not let go, a hammering that starts a row -- and asked about before the
next match, the way a key moment is (A204).

Pure data and pure triggers: nothing here plays a match or reads the deck. The season loop
(`game.career.simulate_year`) asks `candidate` which incident this season may bring, checks
its trigger after each of your league matches, and applies the answer from the next match.

What an answer does is never shown as a number. It moves your form for a while, keeps you
out of matches, sets a call you keep for the rest of the season, changes how the club sees
you, or -- for an injury managed rather than mended -- costs something for good. The words
say what happened; the season says how it went."""

from __future__ import annotations

import random
from dataclasses import dataclass

# One level of form, as a change to your two engine numbers per discipline (A160): runs a
# ball scored (or saved), and the dismissal odds (or the wicket odds) as a multiplier.
# Declared game design, smaller than a key moment's call (ATTACK_SHIFT = 0.15 for one
# match), since a form level lasts for weeks rather than one evening.
FORM_STEP = 0.05
FORM_ODDS = 0.15

# [A230] A season's chance of an injury, by role. These keep A226's measured counts per
# career, which the pre-season injury cards produced (0.92 batters, 0.97 keepers, 1.01
# spinners, 1.04 all-rounders, 2.58 fast bowlers in twelve seasons), now as one chance a
# season instead of a share of a card draw. The order between roles is kept.
INJURY_RISK = {"pace": 0.215, "allrounder": 0.087, "spin": 0.084, "keeper": 0.081,
               "opener": 0.077, "middle": 0.077, "finisher": 0.077}
OTHER_CHANCE = 0.55            # a season with no injury may bring one of the others
SLUMP_CHANCE = 0.5             # ...and one with neither may bring a slump, if your form turns

HEAVY_RUNS = 40                # lost by this many runs or more...
HEAVY_WICKETS = 7              # ...or by this many wickets: a hammering
COLLAPSE_TOTAL = 130           # your side bowled out, or kept under this batting first
BLAME_TOTAL = 150              # a hammering with fewer than this on your board is the batters' fault
DEATH_OVER = 15                # 0-indexed: overs 16-20 are the death (A104)
DEATH_HIT = 20                 # an over at the death that went for this many
POOR_RUNS = 10                 # out for fewer than this is a failure
POOR_SPELL = 40                # wicketless for this many in three or four overs
FIRST_MATCH = {"poor_game": 3, "heavy_defeat": 2, "collapse": 2, "death_hit": 2}
# How often each can happen in one career; unlisted, no limit.
LIMIT = {"back": 1, "side_strain": 2, "hamstring": 2, "wedding": 1, "death": 1,
         "collapse": 2, "row": 3, "press": 3}


@dataclass(frozen=True)
class Turn:
    """One way an answer turns out, decided by the seed when you choose."""
    chance: float
    text: str = ""
    out: int = 0               # more matches out, after one more match
    form: int = 0              # form, from the next match to the end of the season
    permanent: int = 0         # Rating points for good, after the season
    standing: int = 0
    injury: str = ""


@dataclass(frozen=True)
class IncidentOption:
    label: str
    hint: str                  # what it means, in the voice of somebody in the dressing room
    story: str                 # what the season card says
    out: int = 0               # matches out from the next one; -1 is the rest of the season
    form: int = 0
    form_matches: int = 0      # how long the form lasts; 0 is the rest of the season
    approach: int | None = None   # a call (0 anchor, 2 attack) kept for the rest of the season
    death: bool = False        # you bowl at the death from now on
    standing: int = 0
    permanent: int = 0
    age: float = 0.0
    injury: str = ""
    thread: str = ""           # something a later season remembers
    tone: str = ""             # 'up' | 'down' | '' for the season card's mark
    turns: tuple[Turn, ...] = ()


@dataclass(frozen=True)
class Incident:
    key: str
    title: str
    text: str                  # "{opp}", "{what}" and the like filled in from the match
    trigger: str               # seeded | poor_game | heavy_defeat | collapse | death_hit | form
    kinds: frozenset           # who it can happen to: batter, keeper, pace, spin, allrounder
    options: tuple[IncidentOption, ...]
    injury: bool = False
    weight: int = 0            # among the non-injury incidents


ALL = frozenset({"batter", "keeper", "pace", "spin", "allrounder"})
BOWLING = frozenset({"pace", "spin", "allrounder"})
BATTING = frozenset({"batter", "keeper", "allrounder"})

INCIDENTS: dict[str, Incident] = {i.key: i for i in (
    Incident(
        "hamstring", "A tight hamstring",
        "Against {opp} you pulled up {what} with a tight hamstring. The physio says it is a "
        "strain, not a tear, if you look after it.", "seeded", ALL,
        (IncidentOption("Sit out and let it heal", "Two matches off, and you come back whole.",
                        "You sat out two matches and the hamstring healed.", out=2,
                        injury="Hamstring strain, rested (2 matches)"),
         IncidentOption("Strap it and play on", "It may hold. It may not.",
                        "You strapped the hamstring and played on, never quite at full stretch.",
                        form=-1, tone="down",
                        turns=(Turn(0.7, "The hamstring held."),
                               Turn(0.3, "It went again a match later, and you missed three more.",
                                    out=3, injury="Hamstring, torn after playing on (3 matches)")))),
        injury=True),
    Incident(
        "side_strain", "A side strain",
        "Bowling against {opp}, you felt something go in your side. The physio wants four weeks.",
        "seeded", BOWLING,
        (IncidentOption("Take the full four weeks", "Do it properly, once.",
                        "Four weeks out, and the side strain healed properly.", out=5,
                        injury="Side strain (5 matches)"),
         IncidentOption("Rush back", "Back in a fortnight, if the side holds.",
                        "You rushed back from the side strain.", out=2,
                        injury="Side strain, rushed back (2 matches)",
                        turns=(Turn(0.65, "The side held up."),
                               Turn(0.35, "The strain came back, and rushing it cost you.",
                                    out=3, permanent=-1, injury="Side strain, recurred (3 matches)")))),
        injury=True),
    Incident(
        "back", "A stress fracture",
        "Another full spell against {opp}, and your lower back will not loosen. The scan finds a "
        "stress fracture, the fast bowler's injury.", "seeded", frozenset({"pace"}),
        (IncidentOption("Surgery, and the rest of the season off", "Lose the season. Keep the career.",
                        "Surgery ended your season, but your action is intact.", out=-1,
                        injury="Back stress fracture, surgery (season over)"),
         IncidentOption("Manage it and keep bowling", "Stay on the field. The body will remember.",
                        "You kept bowling through the fracture and lost a yard of pace for good.",
                        form=-1, permanent=-3, age=0.5, tone="down",
                        injury="Back stress fracture, managed")),
        injury=True),
    Incident(
        "press", "The papers turn",
        "{what} against {opp}, and the papers have gone for you. One columnist says you are "
        "not worth the money.", "poor_game", ALL,
        (IncidentOption("Own it", "\"I have to be better.\" The dressing room will hear it.",
                        "You held your hand up, and the dressing room noticed.", standing=1,
                        tone="up"),
         IncidentOption("Say nothing", "Let it blow over.",
                        "You kept your head down and let the noise pass."),
         IncidentOption("Hit back at the press conference", "Give them a headline of your own.",
                        "You hit back at the press conference, and it made the back pages.",
                        thread="press_feud",
                        turns=(Turn(0.5, "The anger got you going.", form=1),
                               Turn(0.5, "The row followed you round the grounds.", form=-1)))),
        weight=3),
    Incident(
        "row", "A row in the dressing room",
        "After the hammering by {opp}, voices are raised in the dressing room: a senior player "
        "blames {what}.", "heavy_defeat", ALL,
        (IncidentOption("Back the captain", "The side needs to pull together, and be seen to.",
                        "You stood with the captain, and the dressing room settled.", standing=1,
                        tone="up"),
         IncidentOption("Have it out with him", "Clear the air, whatever it costs.",
                        "You had it out with him behind closed doors.",
                        turns=(Turn(0.5, "Clearing the air sharpened you.", form=1),
                               Turn(0.5, "Word of it got out, and the management were not pleased.",
                                    standing=-1))),
         IncidentOption("Stay out of it", "Not your fight.", "You kept out of the row.")),
        weight=2),
    Incident(
        "collapse", "A collapse",
        "{what} against {opp}. The captain asks you to drop anchor for the rest of the season "
        "and bat through.", "collapse", BATTING,
        (IncidentOption("Take the anchor's job", "Fewer fireworks. Someone has to be there at the end.",
                        "You took the anchor's job for the rest of the season.", approach=0,
                        standing=1, tone="up"),
         IncidentOption("Play your own game", "It is what got you here.",
                        "You kept playing your own game."),
         IncidentOption("Take the attack to them", "Somebody has to take the bowlers on.",
                        "You decided the answer was attack, for the rest of the season.",
                        approach=2)),
        weight=2),
    Incident(
        "death", "The death overs",
        "{what} against {opp}. The captain asks whether you will take the death overs from "
        "here on.", "death_hit", BOWLING,
        (IncidentOption("Take the death overs", "The hardest job in the side. He asked you.",
                        "You took the death overs for the rest of the season.", death=True,
                        standing=1, tone="up"),
         IncidentOption("Stick to your usual overs", "Do what you do best.",
                        "You stuck to your usual overs.")),
        weight=2),
    Incident(
        "wedding", "A family wedding",
        "Your sister is getting married back home in three days.", "seeded", ALL,
        (IncidentOption("Fly home for it", "Two matches missed. Some things matter more.",
                        "You flew home for the wedding and came back with a lighter heart.",
                        out=2, form=1, form_matches=3),
         IncidentOption("Stay with the team", "Watch it on a video call from the hotel.",
                        "You stayed with the team, though your mind was at home for a while.",
                        form=-1, form_matches=2)),
        weight=1),
    Incident(
        "slump", "Out of form",
        "Your last three: {what}. Nothing is coming off, and you can feel it.", "form", ALL,
        (IncidentOption("Grind it out in the nets", "Hit balls until it comes back.",
                        "You ground it out in the nets.",
                        turns=(Turn(0.5, "It clicked one evening.", form=1),
                               Turn(0.5, "Nothing changed."))),
         IncidentOption("Ask for a break", "Step away for two matches and clear your head.",
                        "You asked for a break and sat out two matches.", out=2,
                        turns=(Turn(0.6, "The break cleared your head.", form=1),
                               Turn(0.4, "You came back no different."))),
         IncidentOption("Call your old coach", "Go back to the person who knows your game.",
                        "You called your old coach.",
                        turns=(Turn(0.6, "He found it in one phone call.", form=1),
                               Turn(0.4, "The tinkering only confused you.", form=-1))))),
)}


def kind_of(card) -> str:
    """Who an incident can happen to, from your card: batter, keeper, pace, spin, allrounder."""
    if card.role == "bowler":
        return "pace" if card.bowling_style == "pace" else "spin"
    if card.role in ("allrounder", "keeper"):
        return card.role
    return "batter"


def candidate(seed: int, k: int, kind: str, league_matches: int) -> tuple[str | None, int]:
    """The incident this season may bring, and from which of your league matches a seeded
    one comes. Its own stream, so it never moves a match. An injury first, by role; otherwise
    one of the others, or nothing."""
    rng = random.Random(f"{seed}:incident:{k}")
    risk = INJURY_RISK["opener" if kind == "batter" else kind]
    target = rng.randrange(1, max(2, league_matches - 2))
    if rng.random() < risk:
        injuries = [i for i in INCIDENTS.values() if i.injury and kind in i.kinds]
        weights = [{"back": 3, "side_strain": 3, "hamstring": 4}[i.key] for i in injuries]
        return rng.choices(injuries, weights=weights)[0].key, target
    if rng.random() < OTHER_CHANCE:
        others = [i for i in INCIDENTS.values() if not i.injury and i.weight and kind in i.kinds]
        return rng.choices(others, weights=[i.weight for i in others])[0].key, target
    return None, target


def turn_for(seed: int, k: int, option: IncidentOption) -> Turn | None:
    if not option.turns:
        return None
    rng = random.Random(f"{seed}:turn:{k}")
    return rng.choices(option.turns, weights=[t.chance for t in option.turns])[0]


@dataclass(frozen=True)
class Match:
    """Your last match, as an incident's trigger needs it."""
    opp: str                   # "Kolkata Knight Riders"
    played: bool
    batted: bool
    runs: int
    out: bool
    bowl_balls: int
    bowl_runs: int
    wickets: int
    lost: bool
    heavy: str | None          # "the batters" | "the bowlers" when it was a hammering
    collapse: str | None       # "Bowled out for 112", when your side collapsed
    death_hit: tuple[str, int, int] | None   # (bowler, runs, over number) of a mate's bad over


def fires(incident: Incident, kind: str, m: Match, idx: int, target: int,
          recent_balls: list[int]) -> str | None:
    """Whether the incident is set off by your last match, and the detail its text needs.
    `idx` is the league match about to be played (0-based), `recent_balls` the balls you
    bowled in your last matches, oldest first."""
    if not m.played or idx < FIRST_MATCH.get(incident.trigger, 1):
        return None
    t = incident.trigger
    if t == "seeded":
        if idx < target:
            return None
        if incident.key == "back" and (len(recent_balls) < 2 or min(recent_balls[-2:]) < 24):
            return None
        if incident.key == "side_strain" and m.bowl_balls < 12:
            return None
        if incident.key == "hamstring":
            return "going for a second run" if kind in ("batter", "keeper") or not m.bowl_balls \
                else "in your delivery stride"
        return ""
    if t == "poor_game":
        if m.batted and m.out and m.runs < POOR_RUNS and kind in ("batter", "keeper", "allrounder"):
            return "Out for a duck" if m.runs == 0 else f"Out for {m.runs}"
        if m.bowl_balls >= 18 and not m.wickets and m.bowl_runs >= POOR_SPELL:
            return f"{m.bowl_balls // 6} overs for {m.bowl_runs} and no wicket"
        return None
    if t == "heavy_defeat":
        return m.heavy
    if t == "collapse":
        return m.collapse if m.collapse and m.batted else None
    if t == "death_hit":
        if m.death_hit is None:
            return None
        who, runs, over = m.death_hit
        return f"{who} went for {runs} in the {_ord(over)} over"
    return None


def _ord(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def formed(card, level: int):
    """Your card at a form level: better (or worse) at everything you do, a little."""
    import dataclasses
    import math

    if not level:
        return card
    d, mult = level * FORM_STEP, math.exp(-FORM_ODDS * level)
    return dataclasses.replace(
        card,
        bat_scoring=None if card.bat_scoring is None else card.bat_scoring + d,
        bat_dismissal=None if card.bat_dismissal is None else card.bat_dismissal * mult,
        bowl_scoring=None if card.bowl_scoring is None else card.bowl_scoring + d,
        bowl_dismissal=None if card.bowl_dismissal is None else card.bowl_dismissal / mult)
