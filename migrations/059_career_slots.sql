-- Cloud career slots for pass holders [A306].
--
-- A career exists only as its signed token (A199), held in one browser's storage, so a
-- player has one career and loses it with the browser. A pass holder may keep up to five
-- on their account and open any of them on any device.
--
-- The token is stored COMPRESSED: a career's token is about 90 kB by its fifteenth season
-- and compresses about fivefold. The page saves once per finished season, never per click,
-- because this database's monthly transfer allowance has run out once before (A173).
--
-- The summary columns are what the "Your careers" list shows, so listing slots never reads
-- a token. Saving needs a pass; listing, opening and deleting need only the account, so a
-- lapsed pass never locks anybody out of careers they already kept.

create table career_slots (
    account_id   int not null references accounts(account_id) on delete cascade,
    slot         smallint not null,
    token_gz     bytea not null,
    name         text not null,
    role         text not null,
    year         smallint not null,
    seasons      smallint not null,
    finished     boolean not null,
    rating       smallint,
    updated_at   timestamptz not null default now(),

    primary key (account_id, slot),
    -- web/career_slots.py SLOTS, held equal by a test
    constraint career_slots_slot_ck check (slot between 1 and 5),
    constraint career_slots_size_ck check (octet_length(token_gz) between 1 and 200000),
    constraint career_slots_seasons_ck check (seasons >= 0)
);

comment on table career_slots is
    '[A306] A pass holder''s saved careers: the signed career token, compressed, plus what '
    'the list shows. Deleted with the account.';
