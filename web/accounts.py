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
