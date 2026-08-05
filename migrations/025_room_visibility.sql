-- A room can be marked OPEN at creation -- discoverable and joinable by anyone browsing
-- (GET /api/rooms/open, web/rooms.py's list_open_rooms), no code needed -- or CLOSED
-- (the only mode that existed before this), joinable only by whoever has the code.
-- Closed is the default: "play with friends" should default to private, with open as an
-- explicit opt-in for public matchmaking. This app has no accounts (A62), so "visible to
-- everyone" means exactly that -- a public, anonymous list, the same way the whole app
-- already has no login.

alter table rooms add column is_open boolean not null default false;

comment on column rooms.is_open is
    'true if this room should be listed for anyone to join without the code -- set once at creation, same as format/timer_seconds/draft_mode, never updated afterward.';
