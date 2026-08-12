"""Password hashing and session-cookie signing (web/auth.py), tested without a database
or a running server -- both are pure functions of their own inputs plus SESSION_SECRET.

`SESSION_SECRET` is set per-test via `monkeypatch`, never read from a real `.env` --
these tests must pass in an environment that has never seen a real secret.
"""

from __future__ import annotations

import time

import pytest

from web import auth


@pytest.fixture(autouse=True)
def _session_secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-only-secret-do-not-use-in-real-life")


def test_a_correct_password_verifies():
    stored = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("correct horse battery staple", stored)


def test_the_wrong_password_is_rejected():
    stored = auth.hash_password("correct horse battery staple")
    assert not auth.verify_password("wrong password", stored)


def test_two_hashes_of_the_same_password_differ():
    # A fresh random salt every time -- otherwise two accounts sharing a password would
    # have identical stored hashes, leaking that fact to anyone with read access to the
    # table.
    a = auth.hash_password("shared password")
    b = auth.hash_password("shared password")
    assert a != b
    assert auth.verify_password("shared password", a)
    assert auth.verify_password("shared password", b)


@pytest.mark.parametrize("malformed", [
    "not-the-right-shape",
    "pbkdf2_sha256$not-a-number$abcd$abcd",
    "bcrypt$600000$abcd$abcd",   # a scheme this module never wrote
    "pbkdf2_sha256$600000$not-hex$abcd",
    "",
])
def test_a_malformed_stored_hash_is_a_mismatch_not_a_crash(malformed):
    assert auth.verify_password("anything", malformed) is False


def test_a_session_cookie_round_trips():
    token = auth.make_session_cookie(42)
    assert auth.verify_session_cookie(token) == 42


def test_a_tampered_signature_is_rejected():
    token = auth.make_session_cookie(42)
    payload, _, sig = token.rpartition(".")
    tampered = f"{payload}.{'0' * len(sig)}"
    assert tampered != token   # the fixture actually changed something
    assert auth.verify_session_cookie(tampered) is None


def test_a_cookie_for_a_different_account_is_rejected():
    # Not just "any 42 works" -- swapping the account_id field must break the signature,
    # since the signature covers the payload including account_id.
    token = auth.make_session_cookie(42)
    _, expiry, sig = token.split(".")
    forged = f"99.{expiry}.{sig}"
    assert auth.verify_session_cookie(forged) is None


def test_an_expired_cookie_is_rejected(monkeypatch):
    monkeypatch.setattr(auth.time, "time", lambda: 1_000_000)
    token = auth.make_session_cookie(42)
    monkeypatch.setattr(auth.time, "time", lambda: 1_000_000 + auth.SESSION_MAX_AGE_S + 1)
    assert auth.verify_session_cookie(token) is None


@pytest.mark.parametrize("malformed", [
    "not-three-parts",
    "42.not-a-number.deadbeef",
    "not-a-number.99999999999.deadbeef",
    "",
    "42.99999999999",   # missing the signature entirely
])
def test_a_malformed_cookie_is_rejected_not_a_crash(malformed):
    assert auth.verify_session_cookie(malformed) is None


def test_a_cookie_signed_with_a_different_secret_is_rejected(monkeypatch):
    token = auth.make_session_cookie(42)
    monkeypatch.setenv("SESSION_SECRET", "a-completely-different-secret")
    assert auth.verify_session_cookie(token) is None
