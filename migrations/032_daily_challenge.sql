-- The daily challenge: one scenario a day, the same deck for everybody, one attempt each.
--
-- Two tables, and the first of them deliberately STORES what this codebase would normally
-- derive (A19). The scenario and the day's deck are both pure functions of the date's seed
-- today -- but a leaderboard has to stay stable when the generator changes, and a derived
-- one would silently re-score every past day the moment anybody tuned a scenario. That is
-- A107's argument for the committed deck snapshot applied to a different artefact: the
-- cheap thing to store is the ANSWER, and what it buys is that the past stops moving.

create table daily_challenges (
    challenge_date  date primary key,
    seed            int not null,
    -- game/scenarios.py's SCENARIO_KINDS. Not a CHECK against a hardcoded list: the kinds
    -- are a game-design set expected to grow, and a CHECK here would mean a migration
    -- every time one is added, for a value only that module ever writes.
    scenario_kind   text not null,
    -- The resolved parameters -- target, opposition franchise-season, stage, and whatever
    -- floor the kind requires. jsonb rather than columns because the fields genuinely
    -- differ by kind (a chase has a target, a defence has a runs floor) and columns would
    -- mean a NULL half on every row, which is the shape A38 rejected for the same reason.
    scenario        jsonb not null,
    -- The day's shared deck: the franchise-seasons everyone drafts from, as an ordered
    -- array. Every player sees these same squads, shuffled into their own sequence.
    deck_fs_ids     jsonb not null,
    created_at      timestamptz not null default now()
);

comment on table daily_challenges is
    'One row per day. The scenario and deck are stored rather than derived so a past day''s leaderboard cannot be re-scored by a later change to the generator.';

create table daily_results (
    challenge_date  date not null references daily_challenges (challenge_date) on delete cascade,
    account_id      int not null references accounts (account_id) on delete cascade,
    -- The full draft+match state, replayed server-side before this row is written. Kept so
    -- a result can be re-verified, or a scorecard shown, without trusting anything the
    -- client said about it.
    state           text not null,
    objective_met   boolean not null,
    -- In the unit the scenario fixes (runs for a defence, wickets in hand for a chase), so
    -- it is homogeneous within a day and never compared across two units.
    margin          int not null,
    bonus_points    int not null default 0,
    bonuses         jsonb not null default '[]'::jsonb,
    completed_at    timestamptz not null default now(),
    -- One attempt per day, enforced by the database rather than by the route that writes
    -- it. A leaderboard whose "one try" rule lives only in application code is a
    -- leaderboard that will eventually hold somebody's fourth attempt.
    primary key (challenge_date, account_id)
);

comment on column daily_results.margin is
    'In the unit game/scenarios.py''s Scenario.margin_unit names for that day''s kind -- runs for defend_by, wickets in hand for either chase.';

-- The leaderboard's own read: one day, ranked. Mirrors scenarios.rank_key exactly --
-- objective first, then margin, then bonuses.
create index daily_results_board_idx
    on daily_results (challenge_date, objective_met desc, margin desc, bonus_points desc);
