-- SPEC 7.1. The fitted state model, in two tables because the grains genuinely differ.
--
-- Per-ball outcomes get the A5 bucketed wicket state: one delivery is a very noisy
-- observation and 0-1/2-3/4-5/6+ is the grain that survives it. Expected runs remaining
-- gets EXACT wickets, because it aggregates the whole rest of the innings and is far
-- smoother per observation -- and because it has to. The cost of a wicket is the
-- difference one wicket makes, and inside a two-wide bucket one more wicket usually
-- stays in the same bucket. On the bucketed grid half the states would price a wicket
-- at exactly zero.
--
-- Both tables store COUNTS, never rates. Per A19 every rate is derivable, and two copies
-- of the same fact drift; what is really lost is the check. `runs_per_ball` is
-- runs_off_bat / faced and belongs in the query that needs it.

-- One row per (over, wicket bucket): all 80 logically possible, not just the 75 observed.
-- A row that is absent and a row with faced = 0 mean different things to the simulator --
-- "this table does not cover that state" versus "the archive has never seen it" -- and
-- the difference must be legible rather than inferred from a missing key. Nobody has ever
-- been 4 down in over 0, and that is a fact worth storing rather than a gap.
create table state_ball_outcomes (
    over_no       smallint not null,
    wicket_bucket text     not null,   -- '0-1' | '2-3' | '4-5' | '6+'

    -- [A22] Balls FACED: extra_wides = 0. A no-ball is a ball faced, a wide is not.
    -- This is not the same denominator as overs bowled and must never be confused with it.
    faced         int      not null,
    runs_off_bat  int      not null,
    dismissals    int      not null,

    -- The distribution, not the mean. The simulator draws from it; a mean cannot be
    -- drawn from. Seven columns cover 100% of the fitting set: 5s happen (32 of them,
    -- off overthrows), 7s do not. See the sum constraint below.
    runs_0        int      not null,
    runs_1        int      not null,
    runs_2        int      not null,
    runs_3        int      not null,
    runs_4        int      not null,
    runs_5        int      not null,
    runs_6        int      not null,

    primary key (over_no, wicket_bucket),

    constraint state_ball_outcomes_over_ck
        check (over_no between 0 and 19),
    constraint state_ball_outcomes_bucket_ck
        check (wicket_bucket in ('0-1', '2-3', '4-5', '6+')),

    -- Not a restatement of the loader: it is the guard against a future archive refresh
    -- producing an eighth outcome. An 8 off an overthrow would have nowhere to go, and
    -- this is what stops it going nowhere QUIETLY. Holds exactly on the current archive.
    constraint state_ball_outcomes_distribution_ck
        check (runs_0 + runs_1 + runs_2 + runs_3 + runs_4 + runs_5 + runs_6 = faced),

    -- A dismissal is a delivery, so it cannot outnumber the deliveries.
    constraint state_ball_outcomes_dismissals_ck
        check (dismissals <= faced)
);

comment on table state_ball_outcomes is
    'SPEC 7.1 per-ball state model, fitted on first innings scheduled for 120 balls only (A28). Counts only - rates are derived. A row with faced = 0 is a state the archive has never observed, not a missing row.';

comment on column state_ball_outcomes.faced is
    'Balls faced, extra_wides = 0 (A22). NOT legal_ball, which is the overs-bowled denominator.';


-- One row per (over, exact wickets down). Wickets is the count BEFORE the delivery, so
-- 0-9: a tenth wicket ends the innings and no delivery follows it.
create table state_runs_remaining (
    over_no              smallint not null,
    wickets              smallint not null,

    -- Every delivery in the state, wides included -- unlike the table above. This is not
    -- an inconsistency: runs remaining is a property of the innings position, and a wide
    -- occupies a position in the innings even though no batter faced it.
    observations         int      not null,
    runs_remaining_total bigint   not null,

    primary key (over_no, wickets),

    constraint state_runs_remaining_over_ck
        check (over_no between 0 and 19),
    constraint state_runs_remaining_wickets_ck
        check (wickets between 0 and 9),
    constraint state_runs_remaining_observations_ck
        check (observations > 0)
);

comment on table state_runs_remaining is
    'SPEC 7.1 expected runs remaining, at EXACT wickets. Mean is runs_remaining_total / observations. Wicket cost is the drop in expected FINAL total between adjacent wicket states at the same over - see SPEC 7.1: differencing runs remaining alone is confounded and can come out negative.';

comment on column state_runs_remaining.wickets is
    'Wickets down BEFORE this delivery. retired hurt is excluded - the batter may return and the fielding side has taken no wicket. retired out is a dismissal and counts.';
