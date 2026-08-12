-- Email+password accounts. Confirmed directly with the user: no OAuth, no email-sending
-- service -- both would add a real external dependency this project doesn't have.
-- Password hashing (web/auth.py) is stdlib-only (hashlib.pbkdf2_hmac + secrets), matching
-- this project's own minimal-dependency stance (SPEC.md; pyproject.toml carries nothing
-- auth-related today). Session identity is a signed cookie (web/auth.py), never a
-- server-side session-store table -- there is nothing here to expire or sweep.
--
-- This closes SPEC.md 11.5's own deferral: "accounts only become necessary when a result
-- has to outlive the request that produced it. A `results` table arrives with the
-- leaderboard and not before." migration 027 is that results table.
--
-- Case-insensitive uniqueness on both username and email is enforced as functional
-- unique indexes rather than a citext column -- no Postgres extension is available on
-- Neon's free tier (CLAUDE.md's own rule, and citext is one).

create table accounts (
    account_id     serial primary key,
    username       text not null,
    email          text not null,
    password_hash  text not null,
    created_at     timestamptz not null default now(),

    constraint accounts_username_len_ck check (char_length(username) between 3 and 24),
    constraint accounts_username_charset_ck check (username ~ '^[A-Za-z0-9_]+$')
);

create unique index accounts_username_lower_idx on accounts (lower(username));
create unique index accounts_email_lower_idx on accounts (lower(email));

comment on table accounts is
    'Email+password sign-in (web/auth.py, web/accounts.py). No email verification and no password-reset flow in v1 -- both need an email-sending service this project deliberately does not have. Session identity is a signed cookie (web/auth.py), not a row in this or any other table.';

comment on column accounts.password_hash is
    'pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex> -- stdlib hashlib.pbkdf2_hmac + secrets only, never bcrypt/argon2/passlib. See web/auth.py.';

comment on column accounts.username is
    'Display name shown on the profile page and, when logged in, pre-filled (not forced) as a room seat''s own name. 3-24 chars, [A-Za-z0-9_] only, case-insensitively unique.';
