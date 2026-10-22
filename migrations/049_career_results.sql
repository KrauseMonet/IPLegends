-- [A210] Build a Career's saved careers: the profile's Hall of Fame.
--
-- A career is not a game_results row. That table's grain is one tournament (champion,
-- matches won, the twelve's figures), and a career is up to nineteen seasons of one player
-- with nothing of the kind. Its own table.
--
-- Saving needs no replay. A career is a SIGNED checkpoint (A199): the server issued the
-- token and nothing else can, so a finished career's token is its own proof. The natural key
-- is a hash of the signed payload, so saving the same finished career twice is a no-op while
-- two different careers from one seed are two rows.
--
-- The columns are what the Hall of Fame ranks and filters on; `card` is the rest of the
-- career card as the page draws it (milestones, clubs, the season timeline). A career's
-- place on the real all-time lists is NOT stored: it is derived when read from `runs` and
-- `wickets` against the puzzle facts, so a refreshed archive re-places every career (A19).

create table career_results (
    career_result_id  serial primary key,
    account_id        int not null references accounts (account_id) on delete cascade,
    natural_key       text not null check (natural_key ~ '^[0-9a-f]{64}$'),
    player_name       text not null check (length(player_name) between 1 and 30),
    role              text not null,
    debut             smallint not null,
    overseas          boolean not null,
    seasons           smallint not null check (seasons >= 1),
    matches           smallint not null,
    runs              int not null,
    wickets           int not null,
    titles            smallint not null,
    peak_card         smallint not null,
    peak_year         smallint not null,
    ending            text not null check (ending in ('retired', 'released', 'archive')),
    card              jsonb not null,
    completed_at      timestamptz not null default now(),

    unique (account_id, natural_key)
);

create index career_results_account_idx on career_results (account_id, completed_at desc);
