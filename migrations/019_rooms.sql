-- Live multiplayer rooms move from a process-lifetime dict (`web/rooms.py`'s original
-- "in-memory, v1" note) into Neon, so a room survives a serverless cold start and reads
-- the same on whichever instance answers the next request. This does not change what a
-- room IS -- the round-by-round, per-seat-deal, lazy-resolve design stands exactly as
-- documented in `web/rooms.py`'s own module docstring. Only where the state LIVES moves.
--
-- Two tables, not one wide one, because a room and its seats are genuinely different
-- grains -- one row per room, N rows per room's players -- the same reasoning A38 already
-- gave for `player_season_impact`.
--
-- `room_players.state` stores exactly what `web.session.encode`/`decode` already produce
-- for a solo draft ("384729-0:4.2:7"): a seed and a move list, in one string. No second
-- serialisation scheme -- a room seat's draft is read by the exact same function a solo
-- session's state string is. A CPU seat's row carries its seed and an empty move list
-- (`"384729-"`); its final twelve is never stored, because it is exactly reproducible
-- from (deck, seed) via `run_draft`'s rational policy and storing it would be a second
-- copy of a number `run_draft` already computes for free (A19's rule, applied here).

create table rooms (
    code             text primary key,
    format           text not null check (format in ('final', 'cup', 'league')),
    timer_seconds    smallint not null check (timer_seconds in (15, 30, 45)),
    seed             int not null,
    host_id          text not null,
    status           text not null default 'lobby'
                       check (status in ('lobby', 'drafting', 'complete', 'failed')),
    round            smallint not null default 0,
    -- Wall-clock epoch seconds (Python's time.time()), not a timestamptz -- the ONLY
    -- comparison ever made against this value is against another time.time() read by
    -- Python later, never by Postgres, so storing it as a plain float means the request
    -- handler's own clock is the only clock involved, with no cross-system skew to reason
    -- about. NULL until the room leaves "lobby".
    round_started_at double precision,
    failure_reason   text,
    created_at       timestamptz not null default now()
);

comment on column rooms.round_started_at is
    'time.time() epoch seconds, compared only against a later time.time() read -- never against Postgres now(). See migration 019''s header.';

create table room_players (
    room_code  text not null references rooms (code) on delete cascade,
    player_id  text not null,
    -- Join order. A plain dict preserved insertion order in the old in-memory version;
    -- this column is that same fact, made explicit rather than left to depend on a SELECT
    -- returning rows in insertion order, which SQL never promises.
    seat_order smallint not null,
    name       text not null,
    is_cpu     boolean not null default false,
    state      text not null,
    primary key (room_code, player_id)
);

create index room_players_room_idx on room_players (room_code);

comment on table rooms is
    'Live multiplayer draft rooms (web/rooms.py). Ephemeral by design -- create_room sweeps rooms older than ROOM_TTL_HOURS lazily, there is no scheduled job (A62''s "resolve on read" stance, extended to cleanup).';

comment on column room_players.state is
    'web.session.encode(seed, moves) verbatim -- decode() recovers both. A CPU seat''s moves are always empty; its order/impact are recomputed from (deck, seed) on every read, never stored (A19).';
