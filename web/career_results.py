"""Saved careers and the Hall of Fame [A210] (migration 049).

A finished career is saved from its signed token (A199): the server issued it, so it needs no
replay to be trusted, and a hash of its payload is the natural key that makes saving
idempotent. Where it sits on the real all-time lists is derived when read, never stored.
"""

from __future__ import annotations

import hashlib

from psycopg.types.json import Jsonb

from game import career as C
from game.auction import crore

ENDINGS = {"retired": "retired", "released": "released"}   # anything else: the archive ran out
HALL_LIMIT = 100


def natural_key(token: str) -> str:
    """The signed payload's hash: one row per finished career, whoever saves it how often."""
    return hashlib.sha256(token.rsplit(".", 1)[0].encode()).hexdigest()


def legacy(runs: int, wickets: int) -> int:
    """How the Hall of Fame orders careers: A183's prominence, runs plus twenty a wicket, so a
    batter and a bowler stand on one scale."""
    return runs + 20 * wickets


def card(career: C.Career) -> dict:
    """The career card beyond the ranked columns, as the page draws it."""
    s = career.summary
    return {
        "balls": s.balls, "outs": s.outs, "highest": s.highest, "fifties": s.fifties,
        "hundreds": s.hundreds, "bowl_balls": s.bowl_balls, "bowl_runs": s.bowl_runs,
        "five_fors": s.five_fors, "hat_tricks": s.hat_tricks, "finals": s.finals,
        "orange_caps": s.orange_caps, "purple_caps": s.purple_caps,
        "captaincies": s.captaincies, "regular_seasons": s.regular_seasons,
        "earnings": crore(s.earnings), "franchises": s.franchises,
        "milestones": [[y, t] for y, t in s.milestones],
        "timeline": [{"year": r.year, "franchise": r.franchise, "card": r.display,
                      "champion": r.champion, "squad": r.squad} for r in career.seasons],
    }


def save(conn, account_id: int, key: str, career: C.Career) -> bool:
    """True if newly saved; False if this account already holds this career."""
    s, st = career.summary, career.setup
    ending = "retired" if career.retired else "released" if career.released else "archive"
    row = conn.execute(
        """
        insert into career_results
            (account_id, natural_key, player_name, role, debut, overseas, seasons, matches,
             runs, wickets, titles, peak_card, peak_year, ending, card)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (account_id, natural_key) do nothing
        returning career_result_id
        """,
        (account_id, key, st.name, st.role, st.debut, st.overseas, s.seasons, s.matches,
         s.runs, s.wickets, s.titles, s.peak_display, s.peak_year, ending, Jsonb(card(career))),
    ).fetchone()
    return row is not None


def hall(conn, account_id: int) -> list[dict]:
    """This account's careers, the best first (`legacy`), then the newest."""
    rows = conn.execute(
        """
        select natural_key, player_name, role, debut, overseas, seasons, matches, runs,
               wickets, titles, peak_card, peak_year, ending, card, completed_at
          from career_results
         where account_id = %s
         order by completed_at desc
        """,
        (account_id,),
    ).fetchall()
    keys = ("key", "name", "role", "debut", "overseas", "seasons", "matches", "runs",
            "wickets", "titles", "peak_card", "peak_year", "ending", "card", "completed_at")
    out = [dict(zip(keys, r)) for r in rows]
    # Ordered here, by the one `legacy`, rather than by a second copy of it in SQL; the sort
    # is stable, so equal careers stay newest first.
    out.sort(key=lambda r: -legacy(r["runs"], r["wickets"]))
    return out[:HALL_LIMIT]


def count(conn, account_id: int) -> int:
    (n,), = conn.execute("select count(*) from career_results where account_id = %s",
                         (account_id,)).fetchall()
    return int(n)
