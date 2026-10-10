"""Rarity -- how few other players picked the same answer [A191].

A correct pick that few people made is worth more than one nearly everybody made. It is the thing
Immaculate Grid made famous, and it needs a crowd: "4% of players picked him" means nothing with
nine players, so **a slot earns no rarity until `MIN_VOTERS` have been counted in it**, and until
then a result is worth exactly its base score. A crowd is also what makes it honest to count
signed-out players, who are most of any sample (a pick never makes a leaderboard row, so counting
one cannot buy anybody a place).

**Rarity is a BONUS on top of the base score, not a replacement for it** (`RARITY_MAX` = 50 for a
pick nobody else made, down to 0 for one everybody did). A replacement would put two scales in one
board -- a cell scored by how many players FIT it before the sample is big enough and by how many
CHOSE it after -- so a player's score would jump the moment the 20th person finished. An additive
bonus that is 0 until then moves nobody backwards.

What a "share" is depends on the game, because what a pick means does:

* **Bingo**: a cell has many correct answers and a player chooses one. The share is the part of
  the players who filled THAT CELL that chose THIS player -- so the denominator is per cell.
* **Name the XI** and **Common Ground**: the answers are fixed (a slot has one man in it; a pair
  has its common teammates). A player does not choose them; he finds them or not. The share is the
  part of the day's players who FOUND this one, so the denominator is everybody who found anything.

Everything here is integer arithmetic on counts: a share is derived when read and never stored
(A19), and an integer round-half-up means no float and no banker's half can move a rank.
Pure: no database and no clock. `web/puzzle_results.py` supplies the counts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MIN_VOTERS = 20
RARITY_MAX = 50


@dataclass
class Counts:
    """What the day's picks add up to. `by_pick[(slot, person)]` is how many voters made that
    pick; `by_slot[slot]` how many picked anything in that slot; `voters` how many voters there
    were in the game that day."""
    by_pick: dict[tuple[str, str], int] = field(default_factory=dict)
    by_slot: dict[str, int] = field(default_factory=dict)
    voters: int = 0


def denominator(game: str, slot: str, counts: Counts) -> int:
    """Whom a pick's share is a share OF: the others who filled the same Bingo cell, or every
    player of the day for the games whose answers are fixed."""
    return counts.by_slot.get(slot, 0) if game == "bingo" else counts.voters


def bonus_for(game: str, slot: str, person_id: str, counts: Counts) -> int:
    """The points this one pick earns for being rare: 0 without a big enough sample, up to
    `RARITY_MAX` for an answer nobody else chose. Round-half-up in integers."""
    den = denominator(game, slot, counts)
    if den < MIN_VOTERS:
        return 0
    chose = min(counts.by_pick.get((slot, person_id), 0), den)
    return (2 * RARITY_MAX * (den - chose) + den) // (2 * den)


def share(game: str, slot: str, person_id: str, counts: Counts) -> float | None:
    """The fraction of the denominator that made this pick, or None while the sample is too small
    to say. For display only: scoring never reads a float."""
    den = denominator(game, slot, counts)
    if den < MIN_VOTERS:
        return None
    return min(counts.by_pick.get((slot, person_id), 0), den) / den


def total_bonus(game: str, picks, counts: Counts) -> int:
    """A result's whole rarity bonus: the sum over its correct picks, `picks` being the
    `[slot, person_id, base]` triples a result stores."""
    return sum(bonus_for(game, slot, pid, counts) for slot, pid, *_ in picks)
