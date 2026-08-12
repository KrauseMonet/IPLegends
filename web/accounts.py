"""Email+password accounts and per-account career stats. Mirrors `web/rooms.py`'s own
shape: plain dataclasses, a `ValueError` subclass for failures the client should see as
a clean 4xx rather than a raw 500, load/query functions that take a bare psycopg
connection and nothing else.

Confirmed with the user directly: email+password only (no OAuth, no magic link), and no
login wall anywhere -- every route here is reached only when a player has actively chosen
to sign in or register, never as a gate in front of ordinary play.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from web import auth

_USERNAME_RE = re.compile(r"[A-Za-z0-9_]+")


class AccountError(ValueError):
    """A registration/login failure the client should see as a 400/401, never a 500."""


@dataclass(frozen=True)
class Account:
    account_id: int
    username: str
    email: str


def create_account(conn, username: str, email: str, password: str) -> Account:
    username = username.strip()
    email = email.strip()
    if not (3 <= len(username) <= 24):
        raise AccountError("username must be 3-24 characters")
    if not _USERNAME_RE.fullmatch(username):
        raise AccountError("username may only contain letters, numbers and underscores")
    if "@" not in email or len(email) < 3:
        raise AccountError("enter a valid email address")
    if len(password) < 8:
        raise AccountError("password must be at least 8 characters")

    # Checked in Python, not left to the database's own unique index, so a conflict
    # reads as a clean "that username is already taken" rather than a raw constraint-
    # violation error -- matches web/rooms.py's own style of explicit checks before a
    # write, not exception-driven control flow. The database's own unique indexes
    # (migration 026) remain the real guarantee against a genuine concurrent-registration
    # race; that residual, low-probability case surfaces as an ordinary 500 today, the
    # same as any other unhandled database error elsewhere in this file.
    existing = conn.execute(
        "select username, email from accounts "
        "where lower(username) = lower(%s) or lower(email) = lower(%s)",
        (username, email),
    ).fetchone()
    if existing is not None:
        existing_username, existing_email = existing
        if existing_username.lower() == username.lower():
            raise AccountError("that username is already taken")
        raise AccountError("that email is already registered")

    password_hash = auth.hash_password(password)
    (account_id,) = conn.execute(
        "insert into accounts (username, email, password_hash) values (%s, %s, %s) "
        "returning account_id",
        (username, email, password_hash),
    ).fetchone()
    return Account(account_id=account_id, username=username, email=email)


def authenticate(conn, identifier: str, password: str) -> Account | None:
    """`identifier` is a username or an email -- the sign-in form doesn't ask which."""
    row = conn.execute(
        "select account_id, username, email, password_hash from accounts "
        "where lower(username) = lower(%s) or lower(email) = lower(%s)",
        (identifier, identifier),
    ).fetchone()
    if row is None:
        return None
    account_id, username, email, password_hash = row
    if not auth.verify_password(password, password_hash):
        return None
    return Account(account_id=account_id, username=username, email=email)


def get_account(conn, account_id: int) -> Account | None:
    row = conn.execute(
        "select account_id, username, email from accounts where account_id = %s",
        (account_id,),
    ).fetchone()
    return None if row is None else Account(*row)


@dataclass(frozen=True)
class LeaderRow:
    person_id: str
    name: str
    total: int


@dataclass(frozen=True)
class ProfileStats:
    username: str
    games_played: int
    titles_won: int
    top_batters: list[LeaderRow]
    top_bowlers: list[LeaderRow]


def profile_stats(conn, account_id: int) -> ProfileStats:
    """Games played, titles won, and the top 5 batters / top 4 bowlers by total runs/
    wickets across every game this account has saved. Reads only `game_results`/
    `game_result_players` (migration 027) -- nothing here re-simulates anything.

    The `is not null` filters matter: a pure bowler who never batted in any saved game
    must never appear in `top_batters` with a manufactured 0 (A23/A71's rule, carried
    into this table by migration 027's own comment)."""
    account = get_account(conn, account_id)
    if account is None:
        raise AccountError(f"no account {account_id!r}")

    games_played, titles_won = conn.execute(
        "select count(*), count(*) filter (where champion) "
        "from game_results where account_id = %s",
        (account_id,),
    ).fetchone()

    top_batters = [
        LeaderRow(person_id=pid, name=name, total=total)
        for pid, name, total in conn.execute(
            """
            select p.person_id, p.primary_name, sum(grp.sim_bat_runs) as total_runs
              from game_result_players grp
              join game_results gr on gr.game_result_id = grp.game_result_id
              join people p on p.person_id = grp.person_id
             where gr.account_id = %s and grp.sim_bat_runs is not null
             group by p.person_id, p.primary_name
             order by total_runs desc, p.primary_name
             limit 5
            """,
            (account_id,),
        )
    ]

    top_bowlers = [
        LeaderRow(person_id=pid, name=name, total=total)
        for pid, name, total in conn.execute(
            """
            select p.person_id, p.primary_name, sum(grp.sim_bowl_wickets) as total_wickets
              from game_result_players grp
              join game_results gr on gr.game_result_id = grp.game_result_id
              join people p on p.person_id = grp.person_id
             where gr.account_id = %s and grp.sim_bowl_wickets is not null
             group by p.person_id, p.primary_name
             order by total_wickets desc, p.primary_name
             limit 4
            """,
            (account_id,),
        )
    ]

    return ProfileStats(
        username=account.username, games_played=games_played, titles_won=titles_won,
        top_batters=top_batters, top_bowlers=top_bowlers,
    )


def save_game_result(conn, account_id: int, source: str, natural_key: str,
                      champion: bool, squad) -> bool:
    """True if this call newly saved the game; False if `(account_id, source,
    natural_key)` already existed (idempotent no-op, not an error -- see migration 027's
    own header for why each source's natural_key is what it is).

    One `insert ... on conflict do nothing returning` for the parent row; only if that
    returns a row (i.e. this really is new), ONE multi-row insert for the child rows --
    mirrors `web/rooms.py`'s own `_save_room` batched-insert fix, never one round trip
    per squad member. `squad` is a list of `JourneySquadEntryOut`-shaped objects (solo
    and room both already build these at completion time -- see web/app.py's two save
    routes); a member who neither batted nor bowled contributes no row at all."""
    row = conn.execute(
        """
        insert into game_results (account_id, source, natural_key, champion)
        values (%s, %s, %s, %s)
        on conflict (account_id, source, natural_key) do nothing
        returning game_result_id
        """,
        (account_id, source, natural_key, champion),
    ).fetchone()
    if row is None:
        return False
    (game_result_id,) = row

    rows = [
        (game_result_id, c.person_id, c.sim_bat_runs, c.sim_bat_balls,
         c.sim_bowl_wickets, c.sim_bowl_runs, c.sim_bowl_balls)
        for c in squad
        if c.sim_bat_runs is not None or c.sim_bowl_wickets is not None
    ]
    if rows:
        values_sql = ", ".join(["(%s, %s, %s, %s, %s, %s, %s)"] * len(rows))
        params = [v for row in rows for v in row]
        conn.execute(
            f"""
            insert into game_result_players
                (game_result_id, person_id, sim_bat_runs, sim_bat_balls,
                 sim_bowl_wickets, sim_bowl_runs, sim_bowl_balls)
            values {values_sql}
            on conflict (game_result_id, person_id) do nothing
            """,
            params,
        )
    return True
