"""The admin console's queries and actions [A154]. Plain functions over a bare psycopg
connection, the shape `web/accounts.py` and `web/rooms.py` already use; `web/app.py`
decides WHO may call them and this module decides WHAT they may touch.

**Who is an admin is an environment variable, not a column.** This repository is public,
so a hardcoded id would publish which account carries the privilege -- the reason
`DAILY_RESET_ACCOUNT_ID` is a variable too (web/app.py) -- and a column would need a
migration to grant or revoke it. `ADMIN_ACCOUNT_IDS` is a comma-separated list of account
ids. **When it is unset or empty, `DAILY_RESET_ACCOUNT_ID` stands in**, because that is the
one account the site's owner has already named as privileged, so an existing deployment
gets its owner as admin with no new setting. Unset both and nobody is an admin: a fork, a
preview or a local checkout fails CLOSED, which is the direction a privilege check should
fail in.

**Nothing here needs a migration.** Every figure is read from tables that already exist,
and every action is an UPDATE or a DELETE whose cascades migrations 026/027/032 already
declare. That also bounds what the console can do: there is no ban (it would need a
column), and no "last seen" (nothing records a sign-in) -- "last active" is the latest
saved game or daily attempt, which is the only activity an account leaves behind.

**Guards live here, not only in the route**, so a mistake in `web/app.py` cannot widen
them: an admin can never delete their own account (that would lock the site's only admin
out with no way back in), and no action touches ANOTHER admin's account at all. Every
action is logged to stderr, which Vercel keeps in its function logs; a table for an audit
trail would be the one migration worth adding if the console grows.
"""

from __future__ import annotations

import datetime
import os
import sys

from web import accounts, auth
from web import daily as daily_lib

ADMIN_ACCOUNT_IDS = "ADMIN_ACCOUNT_IDS"
FALLBACK_ADMIN_VAR = "DAILY_RESET_ACCOUNT_ID"

ACCOUNTS_PAGE = 50
ROOMS_LIMIT = 100
TREND_DAYS = 14


class AdminError(ValueError):
    """An admin action refused with a 400, never a 500."""


def admin_ids() -> frozenset[str]:
    """Read at CALL time, never captured at import: `load_dotenv()` runs in the app's
    lifespan, after this module is imported, so an import-time read would see Vercel's
    environment and miss a local `.env` entirely (the trap `_may_reset_daily` records).

    Each entry must be all digits. Compared as whole strings, never as a substring --
    `"7" in "17"` is true, and an account whose id merely appears inside an admin's is a
    different account."""
    raw = os.environ.get(ADMIN_ACCOUNT_IDS, "")
    if not raw.strip():
        raw = os.environ.get(FALLBACK_ADMIN_VAR, "")
    return frozenset(p.strip() for p in raw.split(",") if p.strip().isdigit())


def is_admin(account_id: int | None) -> bool:
    return account_id is not None and str(account_id) in admin_ids()


def _log(actor: int, action: str, detail: str) -> None:
    print(f"[admin] account {actor} {action}: {detail}", file=sys.stderr, flush=True)


def _guard_target(actor: int, target: int, *, allow_self: bool) -> None:
    if target == actor:
        if not allow_self:
            raise AdminError("you cannot do that to your own account")
        return
    if is_admin(target):
        raise AdminError("that account is an admin; change it from its own session")


def _require_account(conn, account_id: int) -> None:
    if conn.execute("select 1 from accounts where account_id = %s",
                    (account_id,)).fetchone() is None:
        raise AdminError(f"no account {account_id}")


# --- overview --------------------------------------------------------------------------

def overview(conn, today: datetime.date) -> dict:
    """The dashboard's numbers, in two round trips. Every count is its own scalar
    subquery, so no join can multiply one table's rows into another's figure."""
    row = conn.execute(
        """
        select
            (select count(*) from accounts),
            (select count(*) from accounts where created_at > now() - interval '1 day'),
            (select count(*) from accounts where created_at > now() - interval '7 days'),
            (select count(*) from game_results),
            (select count(*) from game_results
              where completed_at > now() - interval '7 days'),
            (select count(distinct account_id) from game_results
              where completed_at > now() - interval '7 days'),
            (select count(*) from daily_results where challenge_date = %(today)s),
            (select count(*) from daily_results),
            (select count(*) from rooms where is_open and status = 'lobby')
        """, {"today": today}).fetchone()
    rooms_by_status = {s: n for s, n in conn.execute(
        "select status, count(*) from rooms group by status order by status")}
    signups = _per_day(conn, "accounts", "created_at")
    games = _per_day(conn, "game_results", "completed_at")
    return {
        "accounts": row[0], "accounts_1d": row[1], "accounts_7d": row[2],
        "games_saved": row[3], "games_7d": row[4], "active_accounts_7d": row[5],
        "daily_today": row[6], "daily_all_time": row[7],
        "open_lobbies": row[8], "rooms_by_status": rooms_by_status,
        "signups_by_day": signups, "games_by_day": games,
    }


def _per_day(conn, table: str, column: str) -> list[dict]:
    """The last TREND_DAYS UTC days, oldest first, with a ZERO for a day nothing
    happened rather than a gap -- a missing day would shift every bar after it."""
    counts = {d: n for d, n in conn.execute(
        f"""
        select (({column}) at time zone 'utc')::date, count(*) from {table}
         where {column} > now() - make_interval(days => %s)
         group by 1
        """, (TREND_DAYS,))}
    end = datetime.datetime.now(datetime.timezone.utc).date()
    days = [end - datetime.timedelta(days=i) for i in range(TREND_DAYS - 1, -1, -1)]
    return [{"date": d.isoformat(), "count": counts.get(d, 0)} for d in days]


# --- accounts --------------------------------------------------------------------------

def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def list_accounts(conn, q: str = "", offset: int = 0,
                  limit: int = ACCOUNTS_PAGE) -> tuple[int, list[dict]]:
    """Newest first. `q` matches a username or email anywhere (ILIKE, with the user's
    own `%` and `_` escaped so they search for themselves), or an exact account id."""
    q = (q or "").strip()
    params = {"q": q, "pat": _like(q), "id": int(q) if q.isdigit() else -1,
              "limit": limit, "offset": max(0, offset)}
    where = ("%(q)s = '' or a.account_id = %(id)s "
             "or a.username ilike %(pat)s or a.email ilike %(pat)s")
    (total,) = conn.execute(f"select count(*) from accounts a where {where}",
                            params).fetchone()
    rows = conn.execute(
        f"""
        select a.account_id, a.username, a.email, a.created_at, a.kit_name,
               (select count(*) from game_results g where g.account_id = a.account_id),
               (select count(*) from game_results g
                 where g.account_id = a.account_id and g.champion),
               (select count(*) from daily_results d where d.account_id = a.account_id),
               greatest(
                 (select max(completed_at) from game_results g
                   where g.account_id = a.account_id),
                 (select max(completed_at) from daily_results d
                   where d.account_id = a.account_id))
          from accounts a
         where {where}
         order by a.created_at desc, a.account_id desc
         limit %(limit)s offset %(offset)s
        """, params).fetchall()
    return total, [
        {"account_id": r[0], "username": r[1], "email": r[2],
         "created_at": r[3].isoformat(), "kit_name": r[4], "games_saved": r[5],
         "titles": r[6], "daily_played": r[7],
         "last_active": r[8].isoformat() if r[8] else None,
         "is_admin": is_admin(r[0])}
        for r in rows]


def account_detail(conn, account_id: int) -> dict:
    """One account's card for the console: its career figures (the player's own profile
    query, reused rather than copied) and its most recent saved games and dailies."""
    row = conn.execute(
        "select username, email, created_at, kit_name, kit_monogram, kit_colour "
        "from accounts where account_id = %s", (account_id,)).fetchone()
    if row is None:
        raise AdminError(f"no account {account_id}")
    stats = accounts.profile_stats(conn, account_id)
    games = [
        {"source": s, "champion": c, "matches_played": mp, "matches_won": mw,
         "completed_at": at.isoformat()}
        for s, c, mp, mw, at in conn.execute(
            """
            select source, champion, matches_played, matches_won, completed_at
              from game_results where account_id = %s
             order by completed_at desc limit 10
            """, (account_id,))]
    dailies = [
        {"challenge_date": d.isoformat(), "objective_met": met, "margin": m,
         "bonus_points": b, "completed_at": at.isoformat()}
        for d, met, m, b, at in conn.execute(
            """
            select challenge_date, objective_met, margin, bonus_points, completed_at
              from daily_results where account_id = %s
             order by challenge_date desc limit 10
            """, (account_id,))]
    return {
        "account_id": account_id, "username": row[0], "email": row[1],
        "created_at": row[2].isoformat(),
        "kit": ({"name": row[3], "monogram": row[4], "colour": row[5]}
                if row[3] is not None else None),
        "is_admin": is_admin(account_id),
        "games_played": stats.games_played, "titles_won": stats.titles_won,
        "total_runs": stats.total_runs, "total_wickets": stats.total_wickets,
        "top_batters": [{"name": b.name, "total": b.total} for b in stats.top_batters],
        "top_bowlers": [{"name": b.name, "total": b.total} for b in stats.top_bowlers],
        "recent_games": games, "recent_dailies": dailies,
    }


def rename_account(conn, actor: int, target: int, username: str) -> str:
    _guard_target(actor, target, allow_self=True)
    try:
        username = accounts.validate_username(username)
    except accounts.AccountError as exc:
        raise AdminError(str(exc)) from exc
    _require_account(conn, target)
    taken = conn.execute(
        "select 1 from accounts where lower(username) = lower(%s) and account_id <> %s",
        (username, target)).fetchone()
    if taken is not None:
        raise AdminError("that username is already taken")
    conn.execute("update accounts set username = %s where account_id = %s",
                 (username, target))
    _log(actor, "renamed", f"account {target} -> {username}")
    return username


def set_password(conn, actor: int, target: int, password: str) -> None:
    """Sets a new password outright. The site has no reset-by-email flow (no mail
    service, A102), so this is how a locked-out player gets back in: the admin sets a
    temporary one and tells them. Existing sign-ins stay valid until they expire, since a
    session is a signed cookie with nothing server-side to revoke."""
    _guard_target(actor, target, allow_self=True)
    try:
        accounts.validate_password(password)
    except accounts.AccountError as exc:
        raise AdminError(str(exc)) from exc
    _require_account(conn, target)
    conn.execute("update accounts set password_hash = %s where account_id = %s",
                 (auth.hash_password(password), target))
    _log(actor, "set the password of", f"account {target}")


def clear_kit(conn, actor: int, target: int) -> None:
    """A kit name is free text shown to strangers in rooms, so it is the one piece of an
    account an admin most often needs to take down. All three columns together: 036's
    CHECK requires a kit to be whole or absent."""
    _guard_target(actor, target, allow_self=True)
    _require_account(conn, target)
    conn.execute(
        "update accounts set kit_name = null, kit_monogram = null, kit_colour = null "
        "where account_id = %s", (target,))
    _log(actor, "cleared the kit of", f"account {target}")


def delete_account(conn, actor: int, target: int) -> None:
    """Deletes the account and, through 027's, 032's and 046's cascades, every game it saved,
    every daily attempt it made and every puzzle result. Its puzzle PICKS (046) hold the voter
    as plain text, not a foreign key -- they are shared counts, and a signed-out player's are
    there too -- so they are deleted here by name. Room seats hold no account id, so live rooms
    are untouched."""
    _guard_target(actor, target, allow_self=False)
    row = conn.execute("delete from accounts where account_id = %s returning username",
                       (target,)).fetchone()
    if row is None:
        raise AdminError(f"no account {target}")
    conn.execute("delete from puzzle_picks where voter = %s", (f"a:{target}",))
    _log(actor, "deleted", f"account {target} ({row[0]})")


# --- the daily --------------------------------------------------------------------------

def daily_view(conn, challenge_date: datetime.date) -> dict:
    """One day's challenge and its WHOLE board, with account ids so a row can be acted
    on. Reads only: a day nobody opened is reported as absent rather than generated, so
    looking at a future date cannot fix its challenge early."""
    row = conn.execute(
        "select scenario_kind, scenario from daily_challenges where challenge_date = %s",
        (challenge_date,)).fetchone()
    scenario = (daily_lib._scenario_from_row(row[0], row[1]).describe()
                if row is not None else None)
    board = [
        {"account_id": aid, "username": u, "objective_met": met, "margin": m,
         "bonus_points": b, "completed_at": at.isoformat()}
        for aid, u, met, m, b, at in conn.execute(
            f"""
            select r.account_id, a.username, r.objective_met, r.margin, r.bonus_points,
                   r.completed_at
              from daily_results r join accounts a using (account_id)
             where r.challenge_date = %s
             order by {daily_lib._BOARD_ORDER}
            """, (challenge_date,))]
    return {"challenge_date": challenge_date.isoformat(), "scenario": scenario,
            "board": board}


def remove_daily_result(conn, actor: int, challenge_date: datetime.date,
                        target: int) -> None:
    """Takes one attempt off one day's board, which also hands that player the day back
    to play again. Allowed on the admin's own row -- that is the daily reset, generalised
    -- and on anybody else's except another admin's. The challenge row is never touched:
    deleting it would cascade into every other player's result for the day."""
    _guard_target(actor, target, allow_self=True)
    row = conn.execute(
        "delete from daily_results where challenge_date = %s and account_id = %s "
        "returning 1", (challenge_date, target)).fetchone()
    if row is None:
        raise AdminError("no such result")
    _log(actor, "removed the daily result of", f"account {target} on {challenge_date}")


# --- rooms ------------------------------------------------------------------------------

def list_rooms(conn, limit: int = ROOMS_LIMIT) -> list[dict]:
    """Every room still alive (rooms age out on their own, `rooms._sweep_stale_rooms`),
    most recently active first, with its seats. The names are free text typed by
    strangers, so the page escapes them."""
    rows = conn.execute(
        """
        select r.code, r.game, r.format, r.status, r.is_open, r.created_at, r.updated_at,
               coalesce(json_agg(json_build_object('name', p.name, 'cpu', p.is_cpu)
                                 order by p.seat_order)
                        filter (where p.player_id is not null), '[]'::json)
          from rooms r left join room_players p on p.room_code = r.code
         group by r.code
         order by r.updated_at desc
         limit %s
        """, (limit,)).fetchall()
    return [
        {"code": c, "game": g, "format": f, "status": s, "is_open": o,
         "created_at": ca.isoformat(), "updated_at": ua.isoformat(),
         "players": [p["name"] for p in seats if not p["cpu"]],
         "cpu_seats": sum(1 for p in seats if p["cpu"])}
        for c, g, f, s, o, ca, ua, seats in rows]


def delete_room(conn, actor: int, code: str) -> None:
    """Closes a room for everybody in it -- their next poll finds nothing and the page
    sends them home. The seats go with it (019's cascade)."""
    row = conn.execute("delete from rooms where code = %s returning status",
                       (code.upper(),)).fetchone()
    if row is None:
        raise AdminError(f"no room {code}")
    _log(actor, "deleted", f"room {code.upper()} ({row[0]})")
