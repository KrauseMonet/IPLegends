-- A 2027 mini auction room: the real retained squads and purses, and our provisional list
-- [A301]. A new `game` value, as 'mega' was (035): every place a room asks "is this an
-- auction?" already reads `game`, and a mini room differs from the others in where its
-- teams, list and bidding come from, not in anything a column would carry. Checked against
-- the code BEFORE applying, as 035 was: tests/test_room_schema.py fails if `web.rooms.GAMES`
-- names a value this constraint does not allow.

alter table rooms drop constraint rooms_game_check;

alter table rooms add constraint rooms_game_check
    check (game in ('draft', 'auction', 'mega', 'mini'));

comment on column rooms.game is
    '''draft'' (the snake draft), ''auction'' (web/room_auction.py), ''mega'' (an auction with retentions and Right to Match) or ''mini'' (the 2027 mini auction: real retained squads and purses, A301). Set once at creation, never updated. An auction room is always format = ''league''.';
