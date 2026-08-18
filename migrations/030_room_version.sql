-- A monotonic per-room state counter, so a CLIENT can tell a fresh response from a stale
-- one. Every room response is served to several seats polling on their own timers, and
-- until now nothing in the payload said WHICH state it represented -- so a client applied
-- whatever arrived last, regardless of when the server actually read it. Two responses in
-- flight at once (routine: the poll interval is 2s and a slow request exceeds that easily)
-- could land out of order and roll the UI backwards onto state the room had already left.
-- The visible symptoms were a pick that appeared and then vanished, and a seat that could
-- no longer pick because the screen had been rolled back underneath it.
--
-- Incremented by _save_room on EVERY write, in the same statement, rather than derived
-- from anything already stored. Deriving it was tried first and does not work: len(moves)
-- is monotonic during a draft but not across `play_again` (A84 clears both move logs on
-- the same room row), and any count including players goes DOWN on a leave or a kick,
-- which would make the lobby reject its own updates. A counter that only ever increments,
-- whatever changed and whatever the status, is the one thing a staleness test can rely on
-- without a special case per mutation.
--
-- bigint, not int: it costs nothing here and this is a value nothing ever resets, so the
-- one thing it must never do is wrap.

alter table rooms add column version bigint not null default 0;

comment on column rooms.version is
    'Monotonic state counter, incremented by web/rooms.py''s _save_room on every write. Never reset -- play_again increments it like any other mutation. Clients discard any response carrying a version below the highest they have already applied.';
