-- An auction room's trade window, on or off [A174].
--
-- After the fill round and before the twelves, the PEOPLE in an auction room may swap
-- players one for one -- never with a computer franchise, ratified by the user. Whether a
-- room has the window at all is the host's choice, made in the lobby and locked when the
-- auction starts, so unlike format/game/is_open it IS updated after creation: `_save_room`
-- writes it on conflict. Nothing after the lobby changes it, and the code refuses a change
-- once the room has left the lobby.
--
-- Checked against the code BEFORE this was applied (the CLAUDE.md standing rule):
-- tests/test_room_schema.py now fails if `_save_room` or `_load_room` names a rooms column
-- no migration creates.

alter table rooms add column trades boolean not null default false;

comment on column rooms.trades is
    'Auction rooms only: true opens a trade window between the people in the room after the fill round (web/room_auction.py, A174). Chosen by the host in the lobby; locked once the auction starts. Always false in a draft room.';
