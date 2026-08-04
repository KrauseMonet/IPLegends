-- Comment-only correction. Migration 022 is immutable (already applied) so the fix
-- lands here rather than in place -- the same shape as migration 005 correcting 003's
-- wrong column comment (A22).
--
-- 022's comment described a flat, positionally-consumed move log where "which KIND is
-- expected at a given position" was implicit, and Impact Player substitutions were a
-- third kind of move the host decided. Both are now wrong: independent fixtures within
-- the same round (a cup's two semis; a league's Qualifier 1 + Eliminator) resolve in
-- PARALLEL rather than one at a time, so position no longer identifies which fixture a
-- move belongs to -- a `"stage"` tag does, and `"advance"` moves carry the round's own
-- label instead. And Impact is never a move at all any more: `game.season.play_open`
-- resolves it via `decide_impact` unconditionally for both sides, exactly like every
-- other fully-automatic match in this codebase, so `{"kind": "impact", ...}` is retired.
--
-- The shape now: `{"kind": "toss", "stage": "Semi-final 1", "elects": "bat"|"bowl"}`
-- or `{"kind": "advance", "round": "Semi-finals"}`. See `web/room_match.py`'s own
-- module docstring for the full design.

comment on column rooms.match_moves is
    'The room''s shared, ordered match-phase move log: [{"kind": "toss", "stage": <fixture name>, "elects": "bat"|"bowl"}, {"kind": "advance", "round": <round label>}, ...], replayed from scratch by web.room_match.replay_room_matches. Stage/round tags identify which fixture or round a move belongs to, since independent fixtures within one round resolve in parallel and can be answered in either order. See migration 024''s header for what changed from 022''s original (flat-positional, Impact-included) design.';
