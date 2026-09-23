"""Flashback -- a guess-the-season trivia round, built from real archive facts only.

Not a rating, not a draft: three real facts about one real franchise-season -- its
leading run-scorer, its leading wicket-taker, and its real match record that year
(`matches.winner_fs_id` is a genuine per-match fact, already stored, A20) -- and the
player guesses which of that SAME real franchise's other seasons produced them.

What this deliberately does NOT claim: there is no captaincy data anywhere in this
schema, and no per-real-season "who won the tournament" concept (the archive tracks
individual match results, not a historical points table) -- both real trivia-game
staples elsewhere, neither fabricated here.

Decoy years are always the same franchise (`franchises.canonical_name`, stable across a
rename -- Delhi Daredevils/Capitals is one row, while Deccan Chargers/Sunrisers Hyderabad
and the two Gujarat franchises are correctly kept SEPARATE, per that table's own comment),
so a guess has to use the clues rather than recognise a kit change. A franchise needs at
least `FLASHBACK_MIN_SEASONS` real seasons to supply three distinct decoys plus the
answer; below that (Kochi's one season, the two 2016/17 replacement franchises' two
apiece, Pune Warriors' three) it is left out of the pool rather than padded with a made-up
option.

No new ETL, no new table -- three plain queries against tables that already exist and are
already indexed on the columns used here.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

FLASHBACK_MIN_SEASONS = 4
CANDIDATE_COUNT = 4


@dataclass(frozen=True)
class FlashbackRound:
    franchise: str
    correct_year: int
    candidates: list[int]
    top_scorer: str | None
    top_scorer_runs: int | None
    top_wicket_taker: str | None
    top_wicket_taker_wickets: int | None
    matches_played: int
    matches_won: int


def _group_eligible(rows: list[tuple[str, int, int]],
                     min_seasons: int = FLASHBACK_MIN_SEASONS) -> dict[str, list[tuple[int, int]]]:
    """`rows` is (canonical_name, season_year, franchise_season_id). Groups by franchise
    and drops any with fewer than `min_seasons` -- pure, so the threshold is testable
    without a database."""
    by_name: dict[str, list[tuple[int, int]]] = {}
    for name, year, fs_id in rows:
        by_name.setdefault(name, []).append((year, fs_id))
    return {name: seasons for name, seasons in by_name.items() if len(seasons) >= min_seasons}


def _pick_candidates(rng: random.Random, seasons: list[tuple[int, int]],
                      correct_year: int, count: int = CANDIDATE_COUNT) -> list[int]:
    """The correct year plus up to `count - 1` distinct decoy years from the SAME
    franchise, shuffled together. Never invents a year outside `seasons`."""
    others = [y for y, _ in seasons if y != correct_year]
    decoys = rng.sample(others, min(count - 1, len(others)))
    candidates = decoys + [correct_year]
    rng.shuffle(candidates)
    return candidates


def _eligible_franchises(conn) -> dict[str, list[tuple[int, int]]]:
    rows = conn.execute(
        """
        select f.canonical_name, fs.season_year, fs.franchise_season_id
          from franchises f join franchise_seasons fs using (franchise_id)
         order by f.canonical_name, fs.season_year
        """
    ).fetchall()
    return _group_eligible([tuple(r) for r in rows])


def _facts(conn, fs_id: int) -> tuple:
    scorer = conn.execute(
        """
        select p.primary_name, sum(d.runs_batter) as runs
          from deliveries d join people p on p.person_id = d.batter_id
         where d.batting_fs_id = %s and not d.is_super_over
         group by d.batter_id, p.primary_name
         order by runs desc limit 1
        """,
        (fs_id,),
    ).fetchone()
    wickets = conn.execute(
        """
        select p.primary_name, count(*) filter (where d.credited_to_bowler) as wkts
          from deliveries d join people p on p.person_id = d.bowler_id
         where d.bowling_fs_id = %s and not d.is_super_over
         group by d.bowler_id, p.primary_name
        having count(*) filter (where d.credited_to_bowler) > 0
         order by wkts desc limit 1
        """,
        (fs_id,),
    ).fetchone()
    played, won = conn.execute(
        """
        select count(*), count(*) filter (where winner_fs_id = %s)
          from matches
         where team_a_fs_id = %s or team_b_fs_id = %s
        """,
        (fs_id, fs_id, fs_id),
    ).fetchone()
    return scorer, wickets, played, won


def random_round(conn, rng: random.Random | None = None) -> FlashbackRound:
    rng = rng or random.Random()
    pool = _eligible_franchises(conn)
    if not pool:
        raise RuntimeError("no franchise has enough seasons for a Flashback round")
    name = rng.choice(list(pool))
    seasons = pool[name]
    correct_year, fs_id = rng.choice(seasons)
    candidates = _pick_candidates(rng, seasons, correct_year)
    scorer, wickets, played, won = _facts(conn, fs_id)
    return FlashbackRound(
        franchise=name,
        correct_year=correct_year,
        candidates=candidates,
        top_scorer=scorer[0] if scorer else None,
        top_scorer_runs=int(scorer[1]) if scorer else None,
        top_wicket_taker=wickets[0] if wickets else None,
        top_wicket_taker_wickets=int(wickets[1]) if wickets else None,
        matches_played=int(played),
        matches_won=int(won),
    )
