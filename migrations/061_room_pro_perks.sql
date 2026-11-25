-- Pro rooms and pass perks in friends' rooms [A311, A312].
--
-- `pro`: the host holds a pass and has made this a Pro room; EVERYONE in it then gets a
-- second legend per seat, three trades instead of two, and "Run it back" (a fresh season
-- with the same squads). Chosen in the lobby, by a host the server has checked holds a
-- pass, and locked when the room starts, like `trades` and `legends`.
--
-- `perks`: the host's switch for pass perks, ON by default (ratified by the user): pass
-- holders may bring one extra legend, and a legend from a career played with pass boosts
-- (A310). Visible to everyone in the lobby; the host may turn it off.
--
-- `legends` (migration 050) now holds a LIST per seat once a room allows more than one; a
-- single object, as every room before this stored it, still reads as one legend.
--
-- Checked against the code BEFORE this was applied (the CLAUDE.md standing rule):
-- tests/test_room_schema.py fails if `_save_room` or `_load_room` names a rooms column no
-- migration creates.

alter table rooms add column pro boolean not null default false;
alter table rooms add column perks boolean not null default true;

comment on column rooms.pro is
    'A Pro room (A311): a pass-holding host made it one; everyone gets two legends per seat, three trades and Run it back. Set in the lobby, locked once the room starts.';
comment on column rooms.perks is
    'Pass perks on (A312): pass holders bring one extra legend, and legends from boosted careers may come in. On by default; the host may turn it off in the lobby.';
comment on column rooms.legends is
    'Auction rooms only: NULL = career legends off; otherwise {player_id: legend or [legend, ...]} for the seats that chose any (web/room_auction.py, A215, A311). Set in the lobby, locked once the auction starts.';
