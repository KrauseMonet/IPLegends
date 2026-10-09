"""Career facts about every player, for the puzzle games (Bingo and what follows). [A182]

A puzzle asks "has this PERSON ever done X", where the draft and the auction ask about one
player-SEASON. So this is a different grain from the deck: one row per person, built from
the deck's cards plus four counts the deck does not carry (hundreds, fifties, four-wicket
hauls, sixes), which need a pass over `deliveries`.

**Frozen to `data/puzzle_facts.json.gz`, for A107's reason.** Reading it needs no database,
so a puzzle request touches none. `tools.snapshot_facts` writes it and `--check` compares it
with the archive; run that last in the refresh chain, after `tools.snapshot_deck`.

**Franchises are the 15 CANONICAL ones** (`etl.franchise_map.canonical`), never the era's
display name. Counting Delhi Daredevils and Delhi Capitals as two clubs, and Kings XI and
Punjab Kings as two more, was measured to cost 15 points of playable grids (68% against
83%) and, worse, would ask a player to name someone "who played for Daredevils" and
refuse a man who played for the same club a year later.

**"Played" means batted or bowled.** The deck holds only squad members who did (A27), so a
substitute fielder who took one catch is not a player here. For a puzzle that is the right
reading, and it is stated so that nobody is surprised by it.

**Every count is the IPL's alone**, super overs excluded -- the same scoping a season total
has on the public record, and the same as `load_deck`.
"""

from __future__ import annotations

import gzip
import json
import pathlib
from collections import defaultdict
from dataclasses import dataclass

from etl.franchise_map import canonical

SNAPSHOT = pathlib.Path(__file__).resolve().parent.parent / "data" / "puzzle_facts.json.gz"

# Bumped only when the FILE FORMAT changes, not when the data does (A107's rule).
FORMAT_VERSION = 2   # [A183] role, country and bowling style were added for Guess the Player


@dataclass(frozen=True)
class PlayerFacts:
    person_id: str
    name: str
    franchises: tuple[str, ...]      # canonical, sorted
    seasons: tuple[int, ...]         # calendar years played, sorted
    overseas: bool | None            # NULL stays unknown (A23); nobody is unknown today (A51)
    keeper: bool                     # keeper in any season of his career (A77)
    runs: int                        # IPL career
    wickets: int
    best_season_runs: int            # most runs in one calendar year, across franchises
    best_season_wickets: int
    hundreds: int                    # innings of 100+
    fifties: int                     # innings of 50+ (includes the hundreds)
    four_wicket_hauls: int           # matches with 4+ wickets
    sixes: int
    # What the person IS, as the archive and the hand-filled CSVs record it. NULL stays NULL
    # (A23): a player nobody has classified is unknown, never a default, and the games that
    # show these fields say "unknown" rather than guess.
    role: str | None = None          # the role of most of his seasons (A26): batter, bowler,
                                     # allrounder or keeper
    country: str | None = None       # the nation he played for DURING his IPL career (A51)
    bowling_style: str | None = None # "pace" or "spin"; NULL for a man who never bowled

    @property
    def prominence(self) -> int:
        """A rough 'how well known' ordering, for choosing which answers to show first.
        Not a rating and never read by the engine: career volume only."""
        return self.runs + 20 * self.wickets


def _doc_row(p: PlayerFacts) -> dict:
    return {
        "id": p.person_id, "name": p.name, "fr": list(p.franchises), "yrs": list(p.seasons),
        "over": p.overseas, "keeper": p.keeper, "runs": p.runs, "wk": p.wickets,
        "mr": p.best_season_runs, "mw": p.best_season_wickets, "h": p.hundreds,
        "f": p.fifties, "w4": p.four_wicket_hauls, "s6": p.sixes,
        "role": p.role, "ctry": p.country, "bs": p.bowling_style,
    }


def _from_row(d: dict) -> PlayerFacts:
    return PlayerFacts(
        person_id=d["id"], name=d["name"], franchises=tuple(d["fr"]), seasons=tuple(d["yrs"]),
        overseas=d["over"], keeper=d["keeper"], runs=d["runs"], wickets=d["wk"],
        best_season_runs=d["mr"], best_season_wickets=d["mw"], hundreds=d["h"],
        fifties=d["f"], four_wicket_hauls=d["w4"], sixes=d["s6"],
        role=d["role"], country=d["ctry"], bowling_style=d["bs"])


# When two roles tie across a career, the more specific wins: a man who is half all-rounder
# and half batter is an all-rounder, and one half keeper is a keeper. Fixed so the answer
# never depends on the order the database returns rows in.
_ROLE_PRIORITY = ("allrounder", "keeper", "batter", "bowler")


def _career_roles(conn) -> dict[str, str]:
    """The role of most of a person's seasons (`squad_members.role`, A26/A52). Per-season
    roles are the archive's own calibrated answer; a career has no role of its own, so this
    is the most common one."""
    counts: dict[str, dict[str, int]] = defaultdict(dict)
    for pid, role, n in conn.execute(
            "select person_id, role, count(*) from squad_members group by 1, 2"):
        counts[pid][role] = n
    return {pid: max(by_role, key=lambda r: (by_role[r], -_ROLE_PRIORITY.index(r)
                                              if r in _ROLE_PRIORITY else -99))
            for pid, by_role in counts.items()}


def build_players(conn, deck) -> list[PlayerFacts]:
    """Every person in the deck, with the career facts a puzzle can ask about."""
    franchises: dict[str, set[str]] = defaultdict(set)
    runs_by_year: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    wkts_by_year: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    name: dict[str, str] = {}
    overseas: dict[str, bool | None] = {}
    keeper: dict[str, bool] = defaultdict(bool)
    style: dict[str, str | None] = {}
    for cards in deck.cards_by_fs.values():
        for c in cards:
            pid = c.person_id
            name[pid] = c.name
            overseas.setdefault(pid, c.overseas)
            style.setdefault(pid, c.bowling_style)
            keeper[pid] = keeper[pid] or c.keeper_eligible
            franchises[pid].add(canonical(c.franchise))
            # Summed by CALENDAR YEAR across franchises: a man traded mid-season has two
            # cards for one season, and his season total is the sum, not the larger half.
            runs_by_year[pid][c.season_year] += c.bat_runs or 0
            wkts_by_year[pid][c.season_year] += c.bowl_wickets or 0

    def counts(sql: str) -> dict[str, int]:
        return {pid: n for pid, n in conn.execute(sql)}

    hundreds = counts("""
        select batter_id, count(*) from (
          select batter_id, match_id from deliveries where not is_super_over
           group by 1, 2 having sum(runs_batter) >= 100) x group by 1""")
    fifties = counts("""
        select batter_id, count(*) from (
          select batter_id, match_id from deliveries where not is_super_over
           group by 1, 2 having sum(runs_batter) >= 50) x group by 1""")
    hauls = counts("""
        select bowler_id, count(*) from (
          select bowler_id, match_id from deliveries where not is_super_over
           group by 1, 2 having count(*) filter (where credited_to_bowler) >= 4) x group by 1""")
    sixes = counts("""
        select batter_id, count(*) from deliveries
         where runs_batter = 6 and not is_super_over group by 1""")

    role_of = _career_roles(conn)
    country = {pid: ctry for pid, ctry in conn.execute(
        "select person_id, nationality from people where nationality is not null")}

    out = []
    for pid in sorted(name):
        years = runs_by_year[pid].keys()
        out.append(PlayerFacts(
            person_id=pid, name=name[pid],
            franchises=tuple(sorted(franchises[pid])), seasons=tuple(sorted(years)),
            overseas=overseas[pid], keeper=keeper[pid],
            runs=sum(runs_by_year[pid].values()), wickets=sum(wkts_by_year[pid].values()),
            best_season_runs=max(runs_by_year[pid].values()),
            best_season_wickets=max(wkts_by_year[pid].values()),
            hundreds=hundreds.get(pid, 0), fifties=fifties.get(pid, 0),
            four_wicket_hauls=hauls.get(pid, 0), sixes=sixes.get(pid, 0),
            role=role_of.get(pid), country=country.get(pid), bowling_style=style.get(pid)))
    return out


def build_document(conn, deck) -> dict:
    return {"version": FORMAT_VERSION,
            "players": [_doc_row(p) for p in build_players(conn, deck)]}


def read_document() -> dict | None:
    """The committed snapshot, or None if it is missing, unreadable or a different format.
    None is a normal answer: the routes that need it say so instead of guessing."""
    try:
        with gzip.open(SNAPSHOT, "rt") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if doc.get("version") == FORMAT_VERSION else None


def players_from(doc: dict) -> list[PlayerFacts]:
    return [_from_row(d) for d in doc["players"]]


def compare(conn, deck, doc: dict) -> list[str]:
    """Differences between a snapshot and the archive, empty if they agree. The one
    comparison `--check` and any validation check should share, as A107 did."""
    fresh = {d["id"]: d for d in build_document(conn, deck)["players"]}
    have = {d["id"]: d for d in doc["players"]}
    problems = []
    for pid in sorted(fresh.keys() - have.keys()):
        problems.append(f"{fresh[pid]['name']} ({pid}) is in the archive, not the snapshot")
    for pid in sorted(have.keys() - fresh.keys()):
        problems.append(f"{have[pid]['name']} ({pid}) is in the snapshot, not the archive")
    for pid in sorted(fresh.keys() & have.keys()):
        if fresh[pid] != have[pid]:
            keys = [k for k in fresh[pid] if fresh[pid][k] != have[pid].get(k)]
            problems.append(f"{fresh[pid]['name']} ({pid}) differs in {', '.join(keys)}")
    return problems
