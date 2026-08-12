-- What an account's profile page needs: games played, titles won, and per-real-player
-- totals across every game the account chose to save. Two tables, not one wide one, for
-- the same reason 019 split rooms/room_players and 009 the state-model grids -- one row
-- per completed game vs one row per that game's own squad member is a genuinely
-- different grain.
--
-- Nothing here is recomputed. `game_result_players` carries exactly the figures
-- `game/season.py`'s JourneyAccumulator / web/app.py's `_journey_entry()` already
-- compute at season/room-match completion time (A80/A96/A101's already-twice-debugged
-- per-side attribution) -- this table only PERSISTS them past the request, the way A62
-- always said a `results` table eventually would (SPEC.md 11.5).
--
-- Saving is an explicit, idempotent client action (POST .../save), never a side effect
-- of the existing read-only GET/replay routes -- A62's statelessness stands for a
-- logged-out player, and for every ordinary poll/reload of a logged-in one. The unique
-- constraint below is the real guard against a double-save, not a client-side flag: the
-- natural key is `state` (the season's own draft+moves string, already unique per
-- completed season) for source='solo', and `"{room code}:{room seed}:{player_id}"` for
-- source='room' -- a room can be replayed in place (A84's play_again mints a fresh
-- seed on the same code), so code alone is not unique per playthrough.

create table game_results (
    game_result_id  serial primary key,
    account_id      int not null references accounts (account_id) on delete cascade,
    source          text not null check (source in ('solo', 'room')),
    natural_key     text not null,
    champion        boolean not null,
    completed_at    timestamptz not null default now(),

    unique (account_id, source, natural_key)
);

create index game_results_account_idx on game_results (account_id);

create table game_result_players (
    game_result_id    int not null references game_results (game_result_id) on delete cascade,
    person_id         text not null references people (person_id),
    sim_bat_runs      smallint,
    sim_bat_balls     smallint,
    sim_bowl_wickets  smallint,
    sim_bowl_runs     smallint,
    sim_bowl_balls    smallint,

    primary key (game_result_id, person_id)
);

create index game_result_players_person_idx on game_result_players (person_id);

comment on table game_results is
    'One row per completed solo season or room match an ACCOUNT explicitly saved (web/app.py POST .../save routes). champion = this account''s own seat won that tournament -- feeds the profile page''s "titles won". Never written by an ordinary GET/replay (A62).';

comment on table game_result_players is
    'One row per one of the saving account''s own twelve drafted players, per saved game -- his SIMULATED figures in that one playthrough (JourneySquadEntryOut), not his real archive season. A player who neither batted nor bowled in that game gets no row at all, same "unobserved stays null, never a manufactured zero" convention as CardOut/JourneySquadEntryOut (A23/A71) -- there is nothing to sum for him anyway.';

comment on column game_results.natural_key is
    '`state` (draft+season moves string) for source=''solo''; "{room code}:{room seed}:{player_id}" for source=''room''. Unique per (account_id, source) -- see this file''s own header for why a room needs seed in the key and solo does not.';
