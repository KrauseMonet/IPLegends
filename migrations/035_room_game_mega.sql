-- A mega auction room: retentions and Right to Match on top of the live auction [A140].
--
-- A new `game` value rather than a separate flag, because the two auction games differ in
-- exactly one thing (whether the retention phase and the cards exist) and every other place
-- a room asks "is this an auction?" already reads `game`. Checked against the code BEFORE
-- this was applied, which is the step 033 skipped: tests/test_room_schema.py now fails if
-- `web.rooms.GAMES` names a value this constraint does not allow.

alter table rooms drop constraint rooms_game_check;

alter table rooms add constraint rooms_game_check
    check (game in ('draft', 'auction', 'mega'));

comment on column rooms.game is
    '''draft'' (the snake draft), ''auction'' (web/room_auction.py) or ''mega'' (an auction with retentions and Right to Match). Set once at creation, never updated. An auction room is always format = ''league''.';
