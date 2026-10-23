-- Career legends in a friends' auction room [A214].
--
-- With the setting on, each signed-in player may bring the best version of a career they
-- saved (Build a Career's Hall of Fame, career_results.card -> 'legend'): his best-Rating
-- season joins that player's squad before the auction, free and outside the purse,
-- taking one of the eighteen places and counting toward the overseas limit.
--
-- One column, not a column per seat: NULL means the setting is off; an object means it is
-- on, keyed by player_id, each value the legend that seat chose (a COPY of the saved card,
-- so deleting or re-saving a career cannot change a room already playing). Chosen in the
-- lobby and locked when the auction starts, like `trades`, so `_save_room` writes it on
-- conflict.
--
-- Checked against the code BEFORE this was applied (the CLAUDE.md standing rule):
-- tests/test_room_schema.py fails if `_save_room` or `_load_room` names a rooms column no
-- migration creates.

alter table rooms add column legends jsonb;

comment on column rooms.legends is
    'Auction rooms only: NULL = career legends off; otherwise {player_id: legend} for the seats that chose one (web/room_auction.py, A214). Set in the lobby, locked once the auction starts.';
