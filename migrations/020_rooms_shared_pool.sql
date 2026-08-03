-- Rooms move from "round-based, per-seat independent deals" to "turn-based, one shared
-- competitive pool" -- ratified with the user directly: going first now genuinely means
-- first dibs, so once any seat drafts a real person, no other seat in the room can ever
-- draft that same person. 019's header called the round-by-round, per-seat-deal design
-- something a schema move to Neon would not touch; this migration is the one that does.
--
-- A shared pool means a seat's own progress now depends on every OTHER seat's moves too,
-- not just its own -- so the whole per-seat-independent (seed, moves) pair 019 stored on
-- `room_players.state` stops meaning anything on its own. What replaces it is ONE shared,
-- ordered, room-wide move log: `rooms.moves`, a JSON array of {"seat", "index", "slot"},
-- one entry per resolved turn. Position in the array IS the move number, so no separate
-- counter is needed, and the log is replayed as a whole from move 0 every time (the same
-- "resolve on read" pattern `web.session.replay` already used for a single seat, A62,
-- generalised here to all of them sharing one taken-person-id set and one rng stream).
--
-- JSONB, not text: unlike `web.session.encode`'s URL-safe string (a client-facing state
-- fragment that must round-trip through `decode()`'s strict parser), this is server-
-- internal only and never reaches a browser directly, so JSONB's native array handling
-- is the simpler fit.
--
-- `room_players.state` is dropped outright -- there is no more per-seat seed or move list
-- to store; a seat's own order/impact/deal are derived by replaying the ONE shared
-- `rooms.moves` log, the same way for a human or a CPU seat (A19, once more: a CPU's pick
-- now depends on the shared pool at the moment its turn arrives, so it is recorded in
-- `moves` like any other move rather than recomputed from (deck, seed) alone -- the one
-- respect in which this migration WIDENS what gets stored, called out explicitly since
-- A19's rule is "don't store what's derivable" and a CPU's pick genuinely stops being
-- derivable from (deck, seed) alone under a shared pool).
--
-- `round` is dropped -- now derivable as len(moves) // seat_count, the same A19 shape.
-- `round_started_at` is renamed to `turn_started_at`: same column, same contract (a plain
-- time.time() float, compared only against a later time.time(), never Postgres now() --
-- see 019's own comment on it), renamed only because exactly one seat is ever "live" at a
-- time now, not a whole round of seats simultaneously.
--
-- Rooms are ephemeral and 24h-TTL by design (ROOM_TTL_HOURS, web/rooms.py) -- nothing in
-- an existing room is worth carrying forward across this incompatible schema and code
-- cutover, so in-flight rooms are cleared explicitly rather than left half-broken.

alter table rooms add column moves jsonb not null default '[]'::jsonb;

comment on column rooms.moves is
    'The whole room''s shared, ordered turn log: [{"seat": player_id, "index": int, "slot": int}, ...]. Position in the array is the move number; replayed from scratch (migration 020''s header) to reconstruct every seat''s own progress and the one shared taken-person-id set.';

alter table rooms rename column round_started_at to turn_started_at;

comment on column rooms.turn_started_at is
    'time.time() epoch seconds for the CURRENTLY PENDING seat''s own turn, compared only against a later time.time() read -- never Postgres now(). Renamed from round_started_at: one seat''s turn at a time now, not a whole round together. See migration 020''s header.';

alter table rooms drop column round;

alter table room_players drop column state;

delete from rooms;
