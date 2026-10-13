-- Three to Make, the fifth daily puzzle [A196]: name three players whose figures for a stat add
-- up as close to a target as you can, in ninety seconds on the server's clock.
--
-- Nothing new to store: a result is a row of puzzle_results like any other, and its picks are
-- rows of puzzle_picks (each of the three players is a slot of its own, as in Common Ground).
-- What it needs is for both tables' game CHECKs to admit it.
--
-- detail carries the distance from the target, the trio's sum, the target and the stat, because
-- the board ranks on the distance (then rarity, then time) without replaying anything. The
-- stored score is a 0-1000 closeness the overall board puts beside the other games.

alter table puzzle_results drop constraint puzzle_results_game_check;
alter table puzzle_results add constraint puzzle_results_game_check
    check (game in ('bingo', 'guess', 'xi', 'common', 'trio'));

alter table puzzle_picks drop constraint puzzle_picks_game_check;
alter table puzzle_picks add constraint puzzle_picks_game_check
    check (game in ('bingo', 'xi', 'common', 'trio'));

comment on column puzzle_results.moves is
    'The guesses in order. Bingo: [{"cell", "id"}]; Guess the Player and Name the XI: [id]; Common Ground: [{"id", "t"}] with t the server''s milliseconds since Start; Three to Make: [id, id, id], the trio held (and, once finished, locked in).';
