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
    in: since A225 his best season, carded like a real one (`C.legend_card`). Stored, not
    re-derived, because a later change to the career engine must not change a legend already
    saved. `rating` is the number on his card; `basis` says which rule made it."""
    from tools.snapshot_deck import _card_doc

    best = C.legend_card(career)
    if best is None:
        return None
    r, c = best
    return {"rating": round(c.display), "year": r.year, "role": career.setup.role,
            "basis": "season" if r.measured else "rating",
            "role_label": C.ROLES[career.setup.role]["label"], "name": career.setup.name,
            "card": _card_doc(c)}


# [A225] The Hall of Fame is for careers worth remembering, not every career you finish: two
# of these four honours, ratified by the user after measuring 84 careers (two of four admits
# 33% of careless careers and 55% of ones that always take the strongest offer; any one of
# four admitted about 63%). Read when the profile is read, never stored, so a career saved
# before the rule is placed by it at once and a change to the bar needs no migration.
INDUCTION_RATING = 90        # a peak Rating of 90 or more
INDUCTION_TITLES = 2         # two titles or more
INDUCTION_RANK = 25          # in the real all-time top 25 for runs or for wickets
INDUCTION_NEEDS = 2


def honours(peak: int, titles: int, caps: int, runs_rank: int | None,
            wickets_rank: int | None) -> list[str]:
    """Which of the four induction honours a career holds, in words."""
    out = []
    if peak >= INDUCTION_RATING:
        out.append(f"Peak Rating {peak}")
    if titles >= INDUCTION_TITLES:
        out.append(f"{titles} titles")
    if caps:
        out.append(f"{caps} cap{'s' if caps != 1 else ''}")
    ranks = [r for r in (runs_rank, wickets_rank) if r is not None and r <= INDUCTION_RANK]
    if ranks:
        out.append(f"All-time top {INDUCTION_RANK}")
    return out


def inducted(held: list[str]) -> bool:
    return len(held) >= INDUCTION_NEEDS


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
        # [A228] Player of the Match awards, where the player went after the game, and the
        # ambitions set on the first day with how each went.
        "poms": s.poms, "epilogue": s.epilogue, "number": career.setup.number,
        "country": career.setup.nation,
        "caps": int((career.checkpoint.national if career.checkpoint else {}).get("caps", 0)),
        "ambitions": [[k, *C.ambition_status(k, career.seasons)] for k in career.setup.ambitions],
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
