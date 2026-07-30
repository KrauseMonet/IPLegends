-- SPEC 7.2-7.3. Per-ball impact at the ratings grain, plus the striker-only attribution
-- the ratings actually consume.
--
-- Three things land together because the standing check in CLAUDE.md says a rule and the
-- schema serving it must be reconciled before the migration is applied, and all three were
-- ratified in one pass: A36 (striker-only dismissals), A33 (the two gates), and the grain.

-- ---------------------------------------------------------------------------------------
-- A36. The grid the ratings read.
--
-- `state_ball_outcomes.dismissals` counts EVERY dismissal in the state, including a
-- non-striker run out. That is the wrong number to price a batter's ball against: the
-- non-striker did not cause it. 319 of the fitting set's 7,458 dismissals (4.28%) belong to
-- somebody other than the striker. The aggregate effect is 0.001 runs/ball and the
-- per-player effect reaches 0.193, which is why the aggregate could not have settled it.
--
-- NOT a second table. A31 split the state model in two because the grains genuinely
-- differed - bucketed wickets for per-ball rates, exact wickets for runs remaining. Here
-- the grain is identical: the same 80 (over, bucket) states, the same rows, one more count
-- per row. A second table would duplicate the key to no purpose.
--
-- `state_runs_remaining` is deliberately untouched. A wicket there is a state TRANSITION,
-- w to w+1: a fact about the innings, not about who was dismissed. Attribution does not
-- reach it.
--
-- Truncate first, as 008 did and for the same reason: the column is NOT NULL and the
-- archive's answer for it is not derivable from the columns already stored, so any default
-- would be a fabricated value (hard rule). Emptying the table forces the refit that
-- validation check 20 already insists on.
truncate state_ball_outcomes;

alter table state_ball_outcomes
    add column striker_dismissals int not null;

comment on column state_ball_outcomes.striker_dismissals is
    'A36. Dismissals of the STRIKER only - what a batting rating is priced against. This is the decision column.';

comment on column state_ball_outcomes.dismissals is
    'A36 DIAGNOSTIC, not consumed by ratings. Every dismissal in the state including non-striker run outs. Kept so the striker-only choice stays re-checkable; use striker_dismissals for anything that scores a batter.';

-- A dismissal of the striker is a dismissal, so it cannot outnumber them.
alter table state_ball_outcomes
    add constraint state_ball_outcomes_striker_ck
        check (striker_dismissals <= dismissals);


-- ---------------------------------------------------------------------------------------
-- The ratings grain: (franchise_season, person, discipline). LONG, not wide.
--
-- Keyed on franchise_season_id rather than season because 0 of 3,333 player-seasons span
-- two franchise-seasons in this archive, so the two are 1:1 and the franchise-season key
-- costs nothing while matching the deck grain (A10) and squad_members' primary key exactly.
-- Measured, not assumed - and 0 of the 3,333 lack a squad_members row, which is what makes
-- the foreign key below safe.
--
-- Long rather than wide because batting and bowling are different MEASUREMENTS, not two
-- columns of one. Different denominators (A22: balls faced is extra_wides = 0, bowling
-- counts legal balls), different floors (100 vs 150), separate priors. A wide table would
-- put a NULL meaning "never bowled" in the same column as a NULL meaning "unknown", which
-- is the A23 failure. Here an absent row means the player never practised that discipline
-- and a present row with not_rateable_reason = 'balls' means he did and fell short - the
-- A31 absent-versus-zero distinction, applied one level up.
create table player_season_impact (
    franchise_season_id int  not null,
    person_id           text not null,
    discipline          text not null,       -- 'batting' | 'bowling'

    -- Inputs, never rates (A19). Every rating is derived in the view below, so there is
    -- exactly one copy of each fact and the shrinkage constant is not baked into storage.
    balls               int  not null,       -- this discipline's own denominator
    impact_total        double precision not null,   -- summed, not averaged

    -- A36's diagnostic at player grain: the same seasons scored against any-wicket
    -- attribution. NULL for bowling, where `credited_to_bowler` already excludes run outs
    -- and there is no second reading to record.
    impact_total_any_wicket double precision,

    -- [A33] The four-match gate, counted over the SCORING SET.
    --
    -- This column is the reason the migration was worth reconciling before applying.
    -- The gate used to read squad_members.matches_played, which counts the whole archive
    -- including super overs and reduced matches - deliveries the rating itself throws away.
    -- The two counts disagree on 890 of 3,333 player-seasons, by up to 7 matches, and 61
    -- pass one gate while failing the other. Pinning not_rateable_reason to a mix of the
    -- two would assert a relationship between quantities measured in different universes,
    -- which is the A22 failure exactly. Both gates now count the same cricket.
    --
    -- Matches in which the player batted OR bowled within the scoring set, so it is the
    -- same definition as matches_played over a narrower universe rather than a new one.
    -- Identical across a player-season's two discipline rows by construction.
    matches             smallint not null,

    -- [A7] The shrinkage prior, stored as what it is rather than as its effect.
    prior_per_ball      double precision not null,
    prior_balls         int  not null,       -- career balls outside this season
    prior_source        text not null,       -- 'loso' | 'band' | 'season'

    -- [A33/A35] The gate as it stood when this row was written. The floors are provisional
    -- and will be re-read after 7.4, so the row records the thresholds it was judged
    -- against; otherwise a re-tune silently changes what an old row meant.
    floor_balls         smallint not null,
    gate_matches        smallint not null,

    -- [A20 shape] NULL means rateable. A boolean would be derivable and A19 forbids it;
    -- storing nothing would lose the floor in force. Naming the reason makes the row
    -- self-describing, and the CHECK below turns what would be a duplicate into a
    -- constraint.
    not_rateable_reason text,                -- null | 'matches' | 'balls' | 'both'

    primary key (franchise_season_id, person_id, discipline),

    foreign key (franchise_season_id, person_id)
        references squad_members (franchise_season_id, person_id) on delete cascade,

    constraint player_season_impact_discipline_ck
        check (discipline in ('batting', 'bowling')),
    constraint player_season_impact_prior_source_ck
        check (prior_source in ('loso', 'band', 'season')),

    -- A row exists because the player practised the discipline, so the denominator is
    -- positive. A zero-ball row would be the absent row wearing a disguise.
    constraint player_season_impact_balls_ck
        check (balls > 0),
    constraint player_season_impact_matches_ck
        check (matches > 0),

    -- The any-wicket diagnostic exists for batting and only for batting.
    constraint player_season_impact_diagnostic_ck
        check ((impact_total_any_wicket is not null) = (discipline = 'batting')),

    -- The gate, pinned. Not a restatement of the loader: it is what stops the two gates
    -- drifting apart again, and it can only be written at all because both now count the
    -- same deliveries.
    constraint player_season_impact_reason_ck
        check (not_rateable_reason is not distinct from
            case
                when matches >= gate_matches and balls >= floor_balls then null
                when matches <  gate_matches and balls <  floor_balls then 'both'
                when matches <  gate_matches                          then 'matches'
                else 'balls'
            end)
);

comment on table player_season_impact is
    'SPEC 7.2-7.3 per-ball impact at (franchise-season, person, discipline). Inputs only - raw and shrunk ratings are derived by the player_season_rating view, which is the single place the shrinkage constant lives.';

create index player_season_impact_person_idx
    on player_season_impact (person_id, discipline);

-- Ratings are read a franchise-season at a time when the deck deals a squad.
create index player_season_impact_rateable_idx
    on player_season_impact (franchise_season_id, discipline)
    where not_rateable_reason is null;


-- ---------------------------------------------------------------------------------------
-- The rating. One view, and the ONLY place the shrinkage constant appears.
--
-- [A35] k = 100 is PROVISIONAL and resolves together with A34 after 7.4, against
-- within-season-normalised numbers. Keeping it here rather than in a column is what makes
-- that revision a `create or replace` in a later migration instead of a refit - and what
-- makes the change auditable, since it has to arrive as a migration.
--
-- 7.4 and 7.7 both read shrunk values. They read them THROUGH THIS VIEW. If the formula is
-- copied into a second query the two copies drift and nothing catches it.
--
-- The view returns rateable rows only, so consuming a rating and respecting the gate are
-- the same act. A gated season is not a low rating, it is no rating (A33).
create view player_season_rating as
select
    i.franchise_season_id,
    i.person_id,
    i.discipline,
    f.season_year,
    i.balls,
    i.matches,
    i.impact_total / i.balls                                as raw_per_ball,
    (i.impact_total + 100 * i.prior_per_ball) / (i.balls + 100)
                                                            as shrunk_per_ball,
    i.prior_per_ball,
    i.prior_source
from player_season_impact i
join franchise_seasons f using (franchise_season_id)
where i.not_rateable_reason is null;

comment on view player_season_rating is
    'SPEC 7.2 shrunk per-ball impact, rateable player-seasons only. k = 100 lives here and nowhere else (A35, provisional). Nothing may reimplement the shrinkage formula.';
