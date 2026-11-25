"""Cloud career slots for pass holders [A313].

Plain functions over a bare psycopg connection, the shape `web/premium.py` uses;
`web/career_routes.py` decides who may call them.

A career is its signed token (A199). A slot keeps that token, compressed, plus the few
facts the "Your careers" list shows -- read off the token's own checkpoint, so the list is
never something the page told the server. Saving needs a pass (`web.premium.has_premium`,
asked by the route); listing, opening and deleting need only the account, so a lapsed pass
never locks anybody out of careers they already kept.
"""

from __future__ import annotations

import dataclasses
import datetime
import zlib

SLOTS = 5                     # migration 059's CHECK, held equal by a test


class SlotError(ValueError):
    """Refused with a 400."""


@dataclasses.dataclass(frozen=True)
class Summary:
    name: str
    role: str
    year: int
    seasons: int
    finished: bool
    rating: int | None


@dataclasses.dataclass(frozen=True)
class SlotRow:
    slot: int
    summary: Summary
    updated_at: datetime.datetime


def summary_of(setup, checkpoint, role_label: str) -> Summary:
    """What the list shows, from the career itself: its name, its role, the year it has
    reached, how many seasons it holds, whether it is over, and its peak Rating (the Hall of
    Fame's measure: a last season's Rating reads wrong beside the card's next one)."""
    seasons = checkpoint.seasons
    last = seasons[-1] if seasons else None
    return Summary(name=setup.name, role=role_label,
                   year=last.year if last else setup.debut, seasons=len(seasons),
                   finished=checkpoint.ended is not None,
                   rating=max((s.rating for s in seasons), default=0) or None)


def pack(token: str) -> bytes:
    return zlib.compress(token.encode(), 9)


def unpack(blob: bytes) -> str:
    return zlib.decompress(bytes(blob)).decode()


def check_slot(slot: int) -> int:
    if not 1 <= slot <= SLOTS:
        raise SlotError(f"a career slot is 1 to {SLOTS}")
    return slot


def list_slots(conn, account_id: int) -> list[SlotRow]:
    return [SlotRow(r[0], Summary(*r[1:7]), r[7]) for r in conn.execute(
        "select slot, name, role, year, seasons, finished, rating, updated_at "
        "from career_slots where account_id = %s order by slot", (account_id,))]


def save(conn, account_id: int, slot: int, token: str, s: Summary) -> None:
    """Keep a career in a slot, replacing whatever the slot held."""
    check_slot(slot)
    conn.execute(
        "insert into career_slots (account_id, slot, token_gz, name, role, year, seasons, "
        "finished, rating) values (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "on conflict (account_id, slot) do update set token_gz = excluded.token_gz, "
        "name = excluded.name, role = excluded.role, year = excluded.year, "
        "seasons = excluded.seasons, finished = excluded.finished, rating = excluded.rating, "
        "updated_at = now()",
        (account_id, slot, pack(token), s.name, s.role, s.year, s.seasons, s.finished,
         s.rating))


def token_in(conn, account_id: int, slot: int) -> str | None:
    check_slot(slot)
    r = conn.execute("select token_gz from career_slots where account_id = %s and slot = %s",
                     (account_id, slot)).fetchone()
    return unpack(r[0]) if r else None


def delete(conn, account_id: int, slot: int) -> bool:
    check_slot(slot)
    r = conn.execute("delete from career_slots where account_id = %s and slot = %s "
                     "returning slot", (account_id, slot)).fetchone()
    return r is not None
