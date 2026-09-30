-- A drafted side's own identity: team name, monogram, colour [A146].
--
-- Stored twice, for two different owners: on `accounts`, so a signed-in player's kit
-- follows them between devices, and on `room_players`, so every seat in a room sees the
-- kit each side wears. Both nullable -- an account that never chose one, a seat that has
-- not chosen yet (it wears a default computed on read, web/kit.py `default_kit`), and
-- every auction and filler seat, which is a real franchise with a crest instead.
--
-- Three typed columns rather than one jsonb, so the shape rules below are CHECKs the
-- database enforces rather than behaviour only the loader remembers. What a CHECK cannot
-- hold is palette MEMBERSHIP -- listing the colours here would be a second copy of
-- web/kit.py's PALETTE -- so `kit_colour` is only checked for shape, and membership is
-- web/kit.py's job, pinned by tests/test_kit.py. That same test reads these constraints
-- back out of this file and fails if web/kit.py can produce a value they refuse: the
-- reconciliation CLAUDE.md's standing rule asks for BEFORE a migration is applied.

alter table accounts
    add column kit_name     text,
    add column kit_monogram text,
    add column kit_colour   text,
    add constraint accounts_kit_whole_ck check (
        (kit_name is null) = (kit_monogram is null)
        and (kit_name is null) = (kit_colour is null)),
    add constraint accounts_kit_name_ck check (char_length(kit_name) between 1 and 24),
    add constraint accounts_kit_monogram_ck check (kit_monogram ~ '^[A-Z0-9]{1,3}$'),
    add constraint accounts_kit_colour_ck check (kit_colour ~ '^[a-z]+$');

alter table room_players
    add column kit_name     text,
    add column kit_monogram text,
    add column kit_colour   text,
    add constraint room_players_kit_whole_ck check (
        (kit_name is null) = (kit_monogram is null)
        and (kit_name is null) = (kit_colour is null)),
    add constraint room_players_kit_name_ck check (char_length(kit_name) between 1 and 24),
    add constraint room_players_kit_monogram_ck check (kit_monogram ~ '^[A-Z0-9]{1,3}$'),
    add constraint room_players_kit_colour_ck check (kit_colour ~ '^[a-z]+$');

comment on column accounts.kit_name is
    'The player''s chosen team name for a drafted side (web/kit.py). NULL until they choose one; kit_monogram and kit_colour are NULL exactly when this is.';
comment on column room_players.kit_name is
    'The team name this seat''s drafted side wears in the room (web/kit.py). NULL until the player chooses one -- the seat then wears default_kit(), computed on read. Always NULL for auction and filler seats, which are real franchises. Locked once the room''s matches start.';
