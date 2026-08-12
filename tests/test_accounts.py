"""Email+password accounts (`web/accounts.py`). No live Postgres -- like
`tests/test_rooms.py`, `FakeConn`/`FakeCursor` are a minimal in-memory stand-in for the
`accounts` table (migration 026), matched on the handful of distinct SQL statements
`web/accounts.py` issues. Tests call the REAL `create_account`/`authenticate`/
`get_account` -- the load/save layer included, not just the validation logic above it.
"""

from __future__ import annotations

import pytest

from web import accounts


class FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    """One dict standing in for `accounts` (migration 026): account_id -> row tuple."""

    def __init__(self):
        self.rows: dict[int, tuple] = {}
        self._next_id = 1

    def execute(self, sql: str, params: tuple = ()) -> FakeCursor:
        sql_norm = " ".join(sql.split()).lower()

        if sql_norm.startswith("select username, email from accounts"):
            username, email = params
            for _, (aid, u, e, ph) in self.rows.items():
                if u.lower() == username.lower() or e.lower() == email.lower():
                    return FakeCursor([(u, e)])
            return FakeCursor([])

        if sql_norm.startswith("insert into accounts"):
            username, email, password_hash = params
            account_id = self._next_id
            self._next_id += 1
            self.rows[account_id] = (account_id, username, email, password_hash)
            return FakeCursor([(account_id,)])

        if sql_norm.startswith("select account_id, username, email, password_hash"):
            identifier, _identifier_again = params
            for _, (aid, u, e, ph) in self.rows.items():
                if u.lower() == identifier.lower() or e.lower() == identifier.lower():
                    return FakeCursor([(aid, u, e, ph)])
            return FakeCursor([])

        if sql_norm.startswith("select account_id, username, email from accounts"):
            (account_id,) = params
            row = self.rows.get(account_id)
            return FakeCursor([(row[0], row[1], row[2])] if row else [])

        raise AssertionError(f"FakeConn does not know this query: {sql_norm[:80]!r}")


@pytest.fixture
def conn():
    return FakeConn()


def test_a_new_account_can_be_created(conn):
    account = accounts.create_account(conn, "krausemonet", "km@example.com", "correcthorse")
    assert account.username == "krausemonet"
    assert account.email == "km@example.com"
    assert account.account_id is not None


def test_a_duplicate_username_is_rejected_case_insensitively(conn):
    accounts.create_account(conn, "krausemonet", "a@example.com", "correcthorse")
    with pytest.raises(accounts.AccountError, match="username"):
        accounts.create_account(conn, "KrauseMonet", "b@example.com", "correcthorse")


def test_a_duplicate_email_is_rejected_case_insensitively(conn):
    accounts.create_account(conn, "alice", "shared@example.com", "correcthorse")
    with pytest.raises(accounts.AccountError, match="email"):
        accounts.create_account(conn, "bob", "Shared@Example.com", "correcthorse")


@pytest.mark.parametrize("username", ["ab", "a" * 25, "has space", "has-dash", ""])
def test_an_invalid_username_is_rejected(conn, username):
    with pytest.raises(accounts.AccountError):
        accounts.create_account(conn, username, "ok@example.com", "correcthorse")


def test_a_short_password_is_rejected(conn):
    with pytest.raises(accounts.AccountError, match="password"):
        accounts.create_account(conn, "alice", "alice@example.com", "short")


def test_an_invalid_email_is_rejected(conn):
    with pytest.raises(accounts.AccountError, match="email"):
        accounts.create_account(conn, "alice", "not-an-email", "correcthorse")


def test_authenticate_accepts_the_right_password_by_username(conn):
    accounts.create_account(conn, "alice", "alice@example.com", "correcthorse")
    account = accounts.authenticate(conn, "alice", "correcthorse")
    assert account is not None
    assert account.username == "alice"


def test_authenticate_accepts_the_right_password_by_email(conn):
    accounts.create_account(conn, "alice", "alice@example.com", "correcthorse")
    account = accounts.authenticate(conn, "alice@example.com", "correcthorse")
    assert account is not None


def test_authenticate_rejects_the_wrong_password(conn):
    accounts.create_account(conn, "alice", "alice@example.com", "correcthorse")
    assert accounts.authenticate(conn, "alice", "wrong password") is None


def test_authenticate_rejects_an_unknown_identifier(conn):
    assert accounts.authenticate(conn, "nobody", "whatever") is None


def test_get_account_returns_none_for_an_unknown_id(conn):
    assert accounts.get_account(conn, 999) is None


def test_get_account_returns_the_right_account(conn):
    created = accounts.create_account(conn, "alice", "alice@example.com", "correcthorse")
    fetched = accounts.get_account(conn, created.account_id)
    assert fetched == created
