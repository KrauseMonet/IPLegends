-- Puzzle results: what each signed-in player did in each daily puzzle, and which answers
-- everybody picked [A188]. The first table the puzzle games have ever had -- Bingo, Guess the
-- Player, Common Ground and Name the XI were stateless and anonymous (A182-A185) -- and it
-- exists because a leaderboard, a rarity score and a badge each need a result that outlives
-- the request that made it.
--
-- Operational, like accounts and game_results: it comes out of the puzzle engines and never
-- reads the archive, so check 6 classes it with them.
--
-- ONE attempt per account per game per day, enforced by the primary key rather than by the
-- route that writes it (the daily challenge's rule, migration 032): a leaderboard whose "one
-- try" lives only in application code eventually holds somebody's fourth.
--
-- What is stored is what cannot be re-derived or what ranking must read without a replay:
--   moves    the guesses, so a result can be re-verified from the row alone (A62). For the one
--            timed game (Common Ground) each move carries the SERVER's clock, never the page's.
--   score    the deterministic score the game's own rules give those moves. Rarity (A188) is
--            NOT folded into it: that depends on what everybody else picked and is derived
--            when read, so a stored score can never go stale against a later pick.
--   detail   the summary the badges and the board read (picks, wrong guesses, guesses used).
-- The score is NOT a second copy of the moves: it is checked against a replay by the route that
-- writes it, and it is stored so a leaderboard need not replay every row.

create table puzzle_results (
    account_id   int not null references accounts (account_id) on delete cascade,
    game         text not null check (game in ('bingo', 'guess', 'xi', 'common')),
    day          date not null,
    -- When the attempt began. For Common Ground this is the server's clock at the moment the
    -- player pressed Start, and the 4 minutes are measured from it. For the untimed games it
    -- is simply when the result was submitted.
    started_at   timestamptz not null default now(),
    finished_at  timestamptz,
    moves        jsonb not null default '[]'::jsonb check (jsonb_typeof(moves) = 'array'),
    -- Why it ended: the player stopped ('gave_up') or the clock ran out ('time'). NULL for a
    -- puzzle that ended by being completed, and for one still open.
    ended        text check (ended in ('gave_up', 'time')),
    score        integer check (score >= 0),
    solved       boolean,
    elapsed_ms   integer check (elapsed_ms >= 0),
    detail       jsonb not null default '{}'::jsonb check (jsonb_typeof(detail) = 'object'),
    primary key (account_id, game, day),
    -- A finished row has a score and a verdict; an open one has neither. A timed attempt is
    -- open between Start and its last move, so the table has to be able to say so.
    constraint puzzle_results_finished_ck check (
        (finished_at is null) = (score is null) and (finished_at is null) = (solved is null))
);

comment on table puzzle_results is
    'One row per account, game and day (A188). moves are replayable; score is the deterministic score without rarity; an open row (finished_at NULL) is a timed attempt in progress.';
comment on column puzzle_results.moves is
    'The guesses in order. Bingo: [{"cell", "id"}]; Guess the Player and Name the XI: [id]; Common Ground: [{"id", "t"}] with t the server''s milliseconds since Start.';

-- The board's read: one game, one day, finished rows only.
create index puzzle_results_board_idx on puzzle_results (game, day) where finished_at is not null;
-- The sweep that finishes timed attempts whose clock ran out with nobody to notice.
create index puzzle_results_open_idx on puzzle_results (game, day) where finished_at is null;
-- "Everything this account has done", for a profile and its badges.
create index puzzle_results_account_idx on puzzle_results (account_id, day);

-- Which answer each voter put in each slot on each day -- the denominator and numerator of a
-- rarity score. A voter is an account ('a:12') or a browser ('d:<32 hex>'): rarity is
-- worth having only with a large enough sample, and signed-out players are most of the sample,
-- so their picks count -- but they are never ranked, so nothing here can be gamed into a place
-- on a board.
--
-- slot: Bingo, the cell (0-8); Name the XI, the batting position; Common Ground, the player
-- named (there is no choice to make, so the answer IS the slot and the question is how many
-- found him).
create table puzzle_picks (
    game       text not null check (game in ('bingo', 'xi', 'common')),
    day        date not null,
    slot       text not null,
    voter      text not null check (voter ~ '^(a:[0-9]+|d:[0-9a-f]{32})$'),
    person_id  text not null,
    picked_at  timestamptz not null default now(),
    primary key (game, day, slot, voter)
);

comment on table puzzle_picks is
    'Correct picks in a daily puzzle, one per voter per slot (A188). Counts, never rates: a share is derived when read.';

create index puzzle_picks_slot_idx on puzzle_picks (game, day, slot, person_id);
