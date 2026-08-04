-- A room-wide draft mode, chosen once by the host at creation and binding on every seat.
--
-- 'stat' shows a rating badge on every card and lets a name be clicked for his season
-- stats; 'memory' shows neither -- a pure display choice with no effect on the draft's
-- own legality or the deck dealt, exactly the same distinction the SOLO draft's own
-- DRAFT_MODE already makes (web/static/index.html).
--
-- Ratified with the user directly: this was previously each PLAYER'S OWN client-side
-- preference, set once on their own home screen before ever entering a room, with
-- nothing to keep two seats in the same room looking at the same mode -- one friend on
-- 'memory' from an earlier solo game could join a friend's 'stat' room and see ratings
-- the host never intended to be visible. A room-wide setting the host declares once at
-- creation, applying to every seat, is the fix; the join flow gets no mode choice of its
-- own, matching the existing pattern where the host alone decides format and the timer.

alter table rooms add column draft_mode text not null default 'stat'
    check (draft_mode in ('stat', 'memory'));

comment on column rooms.draft_mode is
    'stat | memory -- chosen once by the host at creation (web/rooms.py create_room), binding on every seat in the room. A pure display choice, same distinction as the solo draft''s own client-side DRAFT_MODE.';
