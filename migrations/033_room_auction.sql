-- An auction room [A139]: the same room -- lobby, shared move log, resolve on read, the
-- league season afterwards -- filled by an auction instead of a snake draft.
--
-- `game` says which. It is a separate column rather than a new `format` value on purpose:
-- an auction room plays the ordinary ten-team LEAGUE season afterwards, and the match code
-- branches on `format = 'league'` in some twenty-five places. A new format value would
-- have had to be taught to every one of them; a new column needs none of them to change.
--
-- `franchise` is the seat's franchise short name (CSK, MI, ...) in an auction room, and
-- NULL in a draft room, where seats are not franchises at all. Chosen by a human in the
-- lobby; assigned at start to anybody who did not choose, and to every computer seat.

alter table rooms add column game text not null default 'draft'
    check (game in ('draft', 'auction'));

comment on column rooms.game is
    '''draft'' (the snake draft) or ''auction'' (web/room_auction.py). Set once at creation, never updated, like format. An auction room is always format = ''league''.';

alter table room_players add column franchise text;

comment on column room_players.franchise is
    'Auction rooms only: the seat''s franchise short name (game.auction.FRANCHISES). NULL in a draft room, and for a human who has not chosen yet.';

create unique index room_players_franchise_idx on room_players (room_code, franchise)
    where franchise is not null;

comment on index room_players_franchise_idx is
    'Two seats in one room can never hold the same franchise. Enforced here, not only in web/rooms.py, because two people can choose in the same instant.';
