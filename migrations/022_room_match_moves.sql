-- Bringing the per-match toss + break-time Impact engine into rooms (A78 gave solo play
-- this; rooms have run a single, fully-automatic play()/run_cup()/run_league()+
-- run_playoffs() call ever since -- see web/rooms.py's own module docstring for the
-- shared-pool draft-phase precedent this mirrors one phase later).
--
-- Confirmed with the user: three narrow jobs, not one decision per seat per match. The
-- TOSS WINNER calls bat/bowl for their own side, if a real seat (a CPU-owned winner
-- auto-resolves to TOSS_DEFAULT_ELECTS, same as everywhere else in this codebase). The
-- HOST decides every Impact Player choice, for either side, in every match -- simulation
-- decisions are the host's job, not the owning player's, which is what keeps a room from
-- needing to work out who currently controls "the other side" mid-match. And only the
-- HOST advances from one match's result to the next, so the pacing of reveals is one knob.
--
-- `rooms.match_moves` is a SECOND shared, ordered move log, exactly `rooms.moves`'s own
-- shape and replay-from-scratch philosophy (migration 020, A62): a JSONB array of
-- `{"kind": "toss", "elects": "bat"|"bowl"}`, `{"kind": "impact", "slot": int|null}`, or
-- `{"kind": "advance"}`. Position in the array is the move number; which KIND is expected
-- at a given position is never stored, because it is always exactly what
-- `web.room_match.replay_room_matches` derives from the fixture list and how many moves
-- the CURRENT fixture has already consumed -- the same "position implies context" shape
-- `rooms.moves` already uses for whose turn a draft pick belongs to.

alter table rooms add column match_moves jsonb not null default '[]'::jsonb;

comment on column rooms.match_moves is
    'The room''s shared, ordered match-phase move log: [{"kind": "toss"|"impact"|"advance", ...}, ...], replayed from scratch by web.room_match.replay_room_matches. See migration 022''s header.';
