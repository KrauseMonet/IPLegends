"""Password hashing and session-cookie signing. Deliberately stdlib-only (hashlib, hmac,
secrets) -- no bcrypt/argon2/passlib/authlib/python-jose. This project's own dependency
list (pyproject.toml) is already minimal by design (SPEC.md), and neither primitive here
needs anything heavier: PBKDF2-HMAC-SHA256 for passwords, plain HMAC-SHA256 for the
cookie. No server-side session-store table -- the cookie is the whole session.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

PBKDF2_ITERATIONS = 600_000  # OWASP's current PBKDF2-HMAC-SHA256 minimum (2023)

COOKIE_NAME = "ipl_auth"
SESSION_MAX_AGE_S = 60 * 60 * 24 * 30  # 30 days


def _session_secret() -> str:
    # Read lazily, not at import time -- importing this module (e.g. from a test that
    # never touches a cookie) must not require SESSION_SECRET to be set. Every function
    # that actually signs or verifies a cookie calls this, so the failure still surfaces
    # loudly the moment a real cookie operation is attempted, same fail-loud shape as
    # etl/db.py's DATABASE_URL/DIRECT_URL checks -- just deferred to first use instead of
    # import time, since unlike a DB connection, this module has legitimate import-time
    # use (password hashing) that shares nothing with signing.
    value = os.environ.get("SESSION_SECRET")
    if not value:
        raise RuntimeError(
            "SESSION_SECRET is not set. Copy .env.example to .env and fill it in "
            "(python -c \"import secrets;print(secrets.token_hex(32))\" generates one)."
        )
    return value


def hash_password(password: str) -> str:
    """`pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>` -- self-describing so a future
    scheme or iteration-count bump never breaks an already-stored hash."""
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """A malformed `stored` value (corrupt row, wrong scheme) is a mismatch, never a
    crash -- this is called on every login attempt with attacker-controlled input on one
    side."""
    try:
        scheme, iterations_s, salt_hex, hash_hex = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        derived = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations_s)
        )
        return hmac.compare_digest(derived, bytes.fromhex(hash_hex))
    except (ValueError, TypeError):
        return False


def _sign(payload: str) -> str:
    return hmac.new(_session_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_session_cookie(account_id: int) -> str:
    """`<account_id>.<expiry_epoch>.<hmac_sha256_hex>` -- signed over the first two
    fields. No server-side session-store table: verifying is pure computation from the
    cookie plus SESSION_SECRET, same reasoning A62 gives for the (unsigned) draft/season
    state string, except this one IS signed -- unlike a draft's state, an account_id is
    not itself re-validated against anything the server can replay, so there is real
    privilege here (which account you act as) worth protecting from forgery."""
    expiry = int(time.time()) + SESSION_MAX_AGE_S
    payload = f"{account_id}.{expiry}"
    return f"{payload}.{_sign(payload)}"


def verify_session_cookie(token: str) -> int | None:
    """`None` for anything malformed, tampered, or expired -- callers treat that
    identically to "no cookie at all" (GET /api/auth/me never 401s)."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    account_id_s, expiry_s, sig = parts
    if not account_id_s.isdigit() or not expiry_s.isdigit():
        return None
    payload = f"{account_id_s}.{expiry_s}"
    if not hmac.compare_digest(_sign(payload), sig):
        return None
    if int(expiry_s) < int(time.time()):
        return None
    return int(account_id_s)
