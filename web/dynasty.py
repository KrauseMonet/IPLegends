"""Club dynasty over a bare psycopg connection [A307]: the club, and its finished careers.

The rules live in `game/dynasty.py`; this module only reads and writes the two tables of
migration 060. `web/career_routes.py` decides who may call it (a pass to choose a club or
start a dynasty career; the account alone to read the card).
"""

from __future__ import annotations

import json

from game import dynasty as D


def club_of(conn, account_id: int) -> str | None:
    r = conn.execute("select franchise from dynasties where account_id = %s",
                     (account_id,)).fetchone()
    return r[0] if r else None


def set_club(conn, account_id: int, franchise: str) -> None:
    """Choose the dynasty club. Changing it starts a new dynasty; the old club's history is
    kept, tagged with its club, and shows again if the club is chosen back."""
    conn.execute(
        "insert into dynasties (account_id, franchise) values (%s, %s) "
        "on conflict (account_id) do update set franchise = excluded.franchise, "
        "created_at = now()", (account_id, franchise))


def history(conn, account_id: int, franchise: str) -> list[dict]:
    """The club's finished dynasty careers, oldest first: the order the dynasty grew in."""
    return [r[0] for r in conn.execute(
        "select record from dynasty_careers where account_id = %s and franchise = %s "
        "order by joined_at, career_key", (account_id, franchise))]


def add(conn, account_id: int, key: str, franchise: str, record: dict) -> bool:
    """Keep a finished dynasty career. Adding it twice is a no-op; True if newly added."""
    r = conn.execute(
        "insert into dynasty_careers (account_id, career_key, franchise, name, record) "
        "values (%s, %s, %s, %s, %s) on conflict do nothing returning career_key",
        (account_id, key, franchise, record["name"], json.dumps(record))).fetchone()
    return r is not None


def bonus(conn, account_id: int, franchise: str) -> int:
    return D.start_bonus(history(conn, account_id, franchise), franchise)
