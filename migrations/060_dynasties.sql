-- Club dynasty, stage one [A307].
--
-- A pass holder picks ONE club as their dynasty (`dynasties`, one row per account). A Build a
-- Career career started while that club is the dynasty is a dynasty career, and when it
-- finishes it joins the dynasty's history (`dynasty_careers`) and leaves its slot, so five
-- slots never cap a dynasty. A history row keeps only what the dynasty card reads -- each
-- season's year, club, figures and honours -- about a kilobyte or two, never the token.
--
-- Every figure on the dynasty card is worked out from these rows when it is read
-- (game/dynasty.py), never stored apart from them. Both tables go with the account.

create table dynasties (
    account_id   int primary key references accounts(account_id) on delete cascade,
    franchise    text not null,                 -- canonical (etl.franchise_map)
    created_at   timestamptz not null default now(),
    constraint dynasties_franchise_ck check (length(franchise) between 1 and 60)
);

create table dynasty_careers (
    account_id   int not null references accounts(account_id) on delete cascade,
    career_key   text not null,                 -- web.career_results.natural_key of its token
    franchise    text not null,                 -- the dynasty club it was played for
    name         text not null,
    record       jsonb not null,                -- game.dynasty.record_of
    joined_at    timestamptz not null default now(),
    primary key (account_id, career_key),
    constraint dynasty_careers_key_ck check (career_key ~ '^[0-9a-f]{64}$'),
    constraint dynasty_careers_size_ck check (octet_length(record::text) <= 100000)
);

create index dynasty_careers_club_idx on dynasty_careers (account_id, franchise, joined_at);

comment on table dynasties is '[A307] A pass holder''s one dynasty club.';
comment on table dynasty_careers is
    '[A307] Finished dynasty careers, as the dynasty card reads them. Deleted with the account.';
