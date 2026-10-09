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
FORMAT_VERSION = 4   # [A185] matches were added for Name the XI (v3: squads, v2: role, country, style)


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
    # Every franchise-season he batted or bowled in, as "Franchise|year" (canonical
    # franchise). Two players are TEAMMATES if they share one -- the same club in the same
    # season, which is how a fan remembers a dressing room. Not "played in the same match".
    squads: tuple[str, ...] = ()

    @property
    def prominence(self) -> int:
        """A rough 'how well known' ordering, for choosing which answers to show first.
        Not a rating and never read by the engine: career volume only."""
        return self.runs + 20 * self.wickets


@dataclass(frozen=True)
class XiPlayer:
    """One man in a side's XI, with what he did that day -- the clue each blank carries."""
    person_id: str
    position: int | None          # batting order (1-11), None if he did not bat
    runs: int | None
    balls: int | None
    out: bool | None              # None if he did not bat
    wickets: int | None           # None if he did not bowl
    conceded: int | None
    bowl_balls: int | None        # legal balls


@dataclass(frozen=True)
class XiSide:
    """A side of a famous match, to be named from its scorecard. [A185]"""
    match_id: str
    fs_id: int
    date: str
    season: int
    kinds: tuple[str, ...]        # final | century | super_over
    venue: str
    city: str
    team: str                     # display name that season
    team_franchise: str           # canonical
    opponent: str
    opponent_franchise: str
    result: str
    players: tuple[XiPlayer, ...]
    opposition: tuple[str, ...]   # the other side's XI, only to tell a miss from "wrong side"

    @property
    def key(self) -> str:
        return f"{self.match_id}:{self.fs_id}"


def _xi_row(x: XiSide) -> dict:
    return {"m": x.match_id, "fs": x.fs_id, "d": x.date, "yr": x.season, "k": list(x.kinds),
            "v": x.venue, "c": x.city, "t": x.team, "tf": x.team_franchise, "o": x.opponent,
            "of": x.opponent_franchise, "r": x.result, "opp": list(x.opposition),
            "p": [[q.person_id, q.position, q.runs, q.balls, q.out, q.wickets, q.conceded,
                   q.bowl_balls] for q in x.players]}


def _xi_from(d: dict) -> XiSide:
    return XiSide(match_id=d["m"], fs_id=d["fs"], date=d["d"], season=d["yr"],
                  kinds=tuple(d["k"]), venue=d["v"], city=d["c"], team=d["t"],
                  team_franchise=d["tf"], opponent=d["o"], opponent_franchise=d["of"],
                  result=d["r"], opposition=tuple(d["opp"]),
                  players=tuple(XiPlayer(*row) for row in d["p"]))


def _doc_row(p: PlayerFacts) -> dict:
    return {
        "id": p.person_id, "name": p.name, "fr": list(p.franchises), "yrs": list(p.seasons),
        "over": p.overseas, "keeper": p.keeper, "runs": p.runs, "wk": p.wickets,
        "mr": p.best_season_runs, "mw": p.best_season_wickets, "h": p.hundreds,
        "f": p.fifties, "w4": p.four_wicket_hauls, "s6": p.sixes,
        "role": p.role, "ctry": p.country, "bs": p.bowling_style, "sq": list(p.squads),
    }


def _from_row(d: dict) -> PlayerFacts:
    return PlayerFacts(
        person_id=d["id"], name=d["name"], franchises=tuple(d["fr"]), seasons=tuple(d["yrs"]),
        overseas=d["over"], keeper=d["keeper"], runs=d["runs"], wickets=d["wk"],
        best_season_runs=d["mr"], best_season_wickets=d["mw"], hundreds=d["h"],
        fifties=d["f"], four_wicket_hauls=d["w4"], sixes=d["s6"],
        role=d["role"], country=d["ctry"], bowling_style=d["bs"], squads=tuple(d["sq"]))


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
    squad_of: dict[str, set[str]] = defaultdict(set)
    for cards in deck.cards_by_fs.values():
        for c in cards:
            pid = c.person_id
            name[pid] = c.name
            overseas.setdefault(pid, c.overseas)
            style.setdefault(pid, c.bowling_style)
            squad_of[pid].add(f"{canonical(c.franchise)}|{c.season_year}")
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
            role=role_of.get(pid), country=country.get(pid), bowling_style=style.get(pid),
            squads=tuple(sorted(squad_of[pid]))))
    return out


# --- Name the XI: sides of famous matches ----------------------------------------------------

FIRST_IMPACT_SEASON = 2023


def _result_text(winner: str | None, result_type: str | None, margin: int | None,
                 decided_by: str | None, super_over: bool) -> str:
    if winner is None:
        return "Tied" if result_type == "tie" else "No result"
    if result_type == "tie" or super_over and not margin:
        return f"Tied; {winner} won the super over"
    unit = result_type or ""
    if margin == 1 and unit.endswith("s"):
        unit = unit[:-1]
    dls = " (D/L)" if decided_by == "dls" else ""
    return f"{winner} won by {margin} {unit}{dls}".replace("  ", " ")


def clean_side(season: int, named: set[str], participated: set[str], known: set[str]) -> bool:
    """Whether a side's XI can be used as a puzzle -- the rule `build_sides` documents.

    Before the Impact Player (2023) the archive names exactly the 11 who played. From 2023 it
    names a twelfth who may or may not have taken the field, so only a side where every named
    man took part is used. And every man must be a person the facts hold, or he could never be
    typed and the side could not be completed."""
    if season < FIRST_IMPACT_SEASON:
        ok = len(named) == 11
    else:
        ok = bool(named) and named == participated
    return ok and named <= known


def _famous_matches(conn) -> dict[str, set[str]]:
    """match_id -> why it is famous. Finals are the LAST match of each season by date (checked
    against the 18 champions the record gives: all agree); centuries are any match with an
    innings of 100+; super overs are matches that went to one."""
    kinds: dict[str, set[str]] = defaultdict(set)
    for (mid,) in conn.execute("""
            select match_id from matches
             where (season_year, match_date) in
                   (select season_year, max(match_date) from matches group by 1)"""):
        kinds[mid].add("final")
    for (mid,) in conn.execute("""
            select distinct match_id from (
              select match_id from deliveries where not is_super_over
               group by match_id, batter_id having sum(runs_batter) >= 100) x"""):
        kinds[mid].add("century")
    for (mid,) in conn.execute("select match_id from matches where had_super_over"):
        kinds[mid].add("super_over")
    return kinds


def scan_deliveries(rows):
    """One pass over deliveries (in match, innings, ball order) to the three things the XI
    clues need: who came in when, what each batter made, what each bowler conceded.

    Pure, so every rule is testable on hand-built deliveries:
      - the batting order is the order men first appear at the crease as STRIKER OR NON-STRIKER
        (A4): a man waiting at the other end is already in, whether or not he has faced;
      - a ball faced is a delivery that is not a wide (A22) -- a no-ball is faced;
      - a retirement is not a dismissal, so a man who retired is not out;
      - a bowler is charged runs off the bat, wides and no-balls, never byes or leg-byes (A1),
        credited only the wickets that are his (A36), and bowls only LEGAL balls.
    """
    order: dict[tuple[str, int], list[str]] = defaultdict(list)
    seen: dict[tuple[str, int], set[str]] = defaultdict(set)
    batting: dict[tuple[str, str], list] = {}
    bowling: dict[tuple[str, str], list] = {}
    for (mid, _inn, fs, batter, non, bowler, runs, wides, nb, _byes, _lb, legal, credited,
         out, kind) in rows:
        for pid in (batter, non):
            if pid not in seen[(mid, fs)]:
                seen[(mid, fs)].add(pid)
                order[(mid, fs)].append(pid)
        b = batting.setdefault((mid, batter), [0, 0, False])
        b[0] += runs
        b[1] += 1 if wides == 0 else 0
        if out and kind not in ("retired hurt", "retired not out"):
            batting.setdefault((mid, out), [0, 0, False])[2] = True
        w = bowling.setdefault((mid, bowler), [0, 0, 0])
        w[0] += 1 if credited else 0
        w[1] += runs + wides + nb
        w[2] += 1 if legal else 0
    return order, batting, bowling


def build_sides(conn, known: set[str]) -> list[XiSide]:
    """Every side of a famous match whose XI is UNAMBIGUOUS and guessable.

    Unambiguous means: before the Impact Player (2023) the XI is exactly the 11 players the
    archive names; from 2023 it names a twelfth, who may or may not have played, so only a
    side where everyone named took part is used -- a named man who did not play would
    otherwise be marked a wrong answer for a player who was never on the field. Guessable
    means every man is a person the facts hold (anybody who batted or bowled in some IPL
    match is; one who never did is not, and could not be typed). Both rules drop a few sides
    rather than guess at them.
    """
    kinds = _famous_matches(conn)
    ids = sorted(kinds)
    head = {r[0]: r for r in conn.execute("""
        select m.match_id, m.match_date, m.season_year, m.venue, m.city,
               m.team_a_fs_id, m.team_b_fs_id, fa.display_name, fb.display_name,
               fw.display_name, m.result_type, m.result_margin, m.decided_by, m.had_super_over
          from matches m
          join franchise_seasons fa on fa.franchise_season_id = m.team_a_fs_id
          join franchise_seasons fb on fb.franchise_season_id = m.team_b_fs_id
          left join franchise_seasons fw on fw.franchise_season_id = m.winner_fs_id
         where m.match_id = any(%s)""", (ids,))}
    squad: dict[tuple[str, int], tuple[set[str], set[str]]] = {}
    for mid, fs, named, part in conn.execute("""
            select match_id, franchise_season_id,
                   array_agg(person_id) filter (where named_in_squad),
                   array_agg(person_id) filter (where participated)
              from appearances where match_id = any(%s) group by 1, 2""", (ids,)):
        squad[(mid, fs)] = (set(named or []), set(part or []))

    order, batting, bowling = scan_deliveries(conn.execute("""
            select match_id, innings_no, batting_fs_id, batter_id, non_striker_id, bowler_id,
                   runs_batter, extra_wides, extra_noballs, extra_byes, extra_legbyes,
                   legal_ball, credited_to_bowler, player_out_id, wicket_kind
              from deliveries where match_id = any(%s) and not is_super_over
             order by match_id, innings_no, delivery_id""", (ids,)))

    sides: list[XiSide] = []
    for mid in ids:
        h = head.get(mid)
        if not h:
            continue
        (_, date, season, venue, city, fs_a, fs_b, name_a, name_b, winner, rtype, margin,
         decided, had_so) = h
        result = _result_text(winner, rtype, margin, decided, had_so)
        for fs, name, opp_fs, opp_name in ((fs_a, name_a, fs_b, name_b), (fs_b, name_b, fs_a, name_a)):
            named, part = squad.get((mid, fs), (set(), set()))
            if not clean_side(season, named, part, known):
                continue
            batters = order[(mid, fs)]
            players = []
            for pid in named:
                bat = batting.get((mid, pid))
                bowl = bowling.get((mid, pid))
                players.append(XiPlayer(
                    person_id=pid,
                    position=batters.index(pid) + 1 if pid in batters else None,
                    runs=bat[0] if bat and pid in batters else None,
                    balls=bat[1] if bat and pid in batters else None,
                    out=bat[2] if bat and pid in batters else None,
                    wickets=bowl[0] if bowl else None, conceded=bowl[1] if bowl else None,
                    bowl_balls=bowl[2] if bowl else None))
            players.sort(key=lambda q: (q.position is None, q.position or 0,
                                        -(q.wickets or 0), q.person_id))
            sides.append(XiSide(
                match_id=mid, fs_id=fs, date=date.isoformat(), season=season,
                kinds=tuple(sorted(kinds[mid])), venue=venue or "", city=city or "",
                team=name, team_franchise=canonical(name), opponent=opp_name,
                opponent_franchise=canonical(opp_name), result=result, players=tuple(players),
                opposition=tuple(sorted(squad.get((mid, opp_fs), (set(), set()))[0]))))
    sides.sort(key=lambda x: (x.date, x.match_id, x.fs_id))
    return sides


def build_document(conn, deck) -> dict:
    players = build_players(conn, deck)
    sides = build_sides(conn, {p.person_id for p in players})
    return {"version": FORMAT_VERSION, "players": [_doc_row(p) for p in players],
            "xi": [_xi_row(x) for x in sides]}


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


def sides_from(doc: dict) -> list[XiSide]:
    return [_xi_from(d) for d in doc["xi"]]


def compare(conn, deck, doc: dict) -> list[str]:
    """Differences between a snapshot and the archive, empty if they agree. The one
    comparison `--check` and any validation check should share, as A107 did."""
    built = build_document(conn, deck)
    fresh = {d["id"]: d for d in built["players"]}
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
    fresh_xi = {f"{d['m']}:{d['fs']}": d for d in built["xi"]}
    have_xi = {f"{d['m']}:{d['fs']}": d for d in doc.get("xi", [])}
    for k in sorted(fresh_xi.keys() - have_xi.keys()):
        problems.append(f"XI side {k} is in the archive, not the snapshot")
    for k in sorted(have_xi.keys() - fresh_xi.keys()):
        problems.append(f"XI side {k} is in the snapshot, not the archive")
    for k in sorted(fresh_xi.keys() & have_xi.keys()):
        if fresh_xi[k] != have_xi[k]:
            problems.append(f"XI side {k} differs")
    return problems
