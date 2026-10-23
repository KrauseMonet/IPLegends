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


def legend(career: C.Career) -> dict | None:
    """[A215] The best version of the player, kept so a friends' auction room can bring him
    in: his best-Rating season as the engine played it. Stored, not re-derived, because a
    later change to the career engine must not change a legend already saved."""
    from tools.snapshot_deck import _card_doc

    best = C.legend_card(career)
    if best is None:
        return None
    r, c = best
    return {"rating": r.rating, "year": r.year, "role": career.setup.role,
            "role_label": C.ROLES[career.setup.role]["label"], "name": career.setup.name,
            "card": _card_doc(c)}


def card(career: C.Career) -> dict:
    """The career card beyond the ranked columns, as the page draws it."""
    s = career.summary
    return {
        "legend": legend(career),
        "balls": s.balls, "outs": s.outs, "highest": s.highest, "fifties": s.fifties,
        "hundreds": s.hundreds, "bowl_balls": s.bowl_balls, "bowl_runs": s.bowl_runs,
        "five_fors": s.five_fors, "hat_tricks": s.hat_tricks, "finals": s.finals,
        "orange_caps": s.orange_caps, "purple_caps": s.purple_caps,
        "captaincies": s.captaincies, "regular_seasons": s.regular_seasons,
        "earnings": crore(s.earnings), "franchises": s.franchises,
        "milestones": [[y, t] for y, t in s.milestones],
        "peak_rating": s.peak_rating, "peak_rating_year": s.peak_rating_year,
        "injuries": [list(i) for i in (career.checkpoint.injuries if career.checkpoint else [])],
        "timeline": [{"year": r.year, "franchise": r.franchise, "card": r.display, "rating": r.rating,
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
         s.runs, s.wickets, s.titles, s.peak_rating or s.peak_display,
         s.peak_rating_year or s.peak_year, ending, Jsonb(card(career))),
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


def legend_of(conn, account_id: int, key: str) -> dict | None:
    """[A215] The legend of one of this account's own saved careers, or None."""
    row = conn.execute(
        "select card -> 'legend' from career_results where account_id = %s and natural_key = %s",
        (account_id, key)).fetchone()
    return row[0] if row and row[0] else None


def count(conn, account_id: int) -> int:
    (n,), = conn.execute("select count(*) from career_results where account_id = %s",
                         (account_id,)).fetchall()
    return int(n)
