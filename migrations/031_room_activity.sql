-- When a room was last ACTIVE, so an abandoned one can be told from a busy one.
--
-- `created_at` cannot answer this: it is when the room was made, and the rooms causing
-- the problem are precisely the ones created and then never touched again. Measured on
-- the live database before writing this, the public Join list was advertising three dead
-- rooms -- one idle 80 minutes, two idle around 22 hours -- each a single seat that
-- somebody would join and then sit alone in.
--
-- Maintained by web/rooms.py's _save_room on every write, plus a THROTTLED heartbeat from
-- the poll path. The heartbeat is not redundant and leaving it out would have made this
-- actively harmful: polls deliberately do not write (A119 stopped every seat's 2-second
-- poll from taking the row lock and writing), so a lobby with three people sitting in it
-- waiting for a fourth produces no writes at all. Without a heartbeat those three would
-- look exactly like an abandoned room and be deleted out from under themselves.
--
-- Existing rows take now() rather than created_at, deliberately: that gives every room
-- already in flight at deploy time one full idle window before it can be swept, instead
-- of deleting a live room the moment this ships.

alter table rooms add column updated_at timestamptz not null default now();

comment on column rooms.updated_at is
    'Last activity: every _save_room write, plus a throttled heartbeat from the poll path (polls otherwise never write, so an occupied lobby would look idle). Drives the idle sweep of open lobby rooms.';

create index rooms_open_lobby_idx on rooms (updated_at) where is_open and status = 'lobby';

comment on index rooms_open_lobby_idx is
    'Serves list_open_rooms and the idle sweep, both of which ask for open lobby rooms by recency. Partial because every other room is irrelevant to both.';
