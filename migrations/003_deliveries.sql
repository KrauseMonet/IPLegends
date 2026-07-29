-- The hot path. Every derived statistic reads this table.
-- Column order is chosen for alignment: 8-byte, then 4-byte, then 2-byte, then 1-byte, then varlena.

create table deliveries (
    delivery_id      bigserial primary key,

    batting_fs_id    int not null references franchise_seasons (franchise_season_id),
    bowling_fs_id    int not null references franchise_seasons (franchise_season_id),

    innings_no       smallint not null,
    over_no          smallint not null,   -- 0-indexed, as in the source
    ball_no          smallint not null,   -- raw sequence within the over, INCLUDING illegal deliveries
    runs_batter      smallint not null,
    runs_extras      smallint not null,

    -- Five separate columns, not one extra_type. A delivery can be both a no-ball and
    -- concede byes. More importantly wides and no-balls are charged to the bowler's
    -- analysis while byes and legbyes are not, so economy rate is wrong without the split.
    extra_wides      smallint not null default 0,
    extra_noballs    smallint not null default 0,
    extra_byes       smallint not null default 0,
    extra_legbyes    smallint not null default 0,
    extra_penalty    smallint not null default 0,

    -- Scheduled length of THIS innings, not the match, in BALLS. Stored in balls
    -- because target.overs is not always an integer: one match carries 9.2, meaning
    -- nine overs and two balls, which no overs column can represent.
    -- info.overs is useless here - it is 20 in all 1243 matches and carries no
    -- information. Length comes from innings.target.overs and innings 1 truncation.
    innings_scheduled_balls smallint,

    is_super_over    boolean not null default false,
    legal_ball       boolean not null,
    credited_to_bowler boolean,

    -- Cricsheet flags overs the umpire miscounted, which really did have 5 or 7 legal
    -- balls. Taken from the source flag, never inferred. Validation whitelists these,
    -- and SPEC 7.1 drops them from the state-model fit rather than reasoning about
    -- what a 7-ball over does to the ball-index dimension.
    over_miscounted  boolean not null default false,

    match_id         text not null references matches (match_id) on delete cascade,
    batter_id        text not null references people (person_id),
    bowler_id        text not null references people (person_id),
    non_striker_id   text not null references people (person_id),
    wicket_kind      text,
    player_out_id    text references people (person_id),
    phase            text,

    constraint deliveries_phase_ck
        check (phase in ('powerplay', 'middle', 'death')),
    constraint deliveries_extras_sum_ck
        check (runs_extras = extra_wides + extra_noballs + extra_byes + extra_legbyes + extra_penalty),
    constraint deliveries_wicket_pair_ck
        check ((wicket_kind is null) = (player_out_id is null)),
    unique (match_id, innings_no, is_super_over, over_no, ball_no)
);

comment on column deliveries.legal_ball is
    'False for wides and no-balls. Balls faced, overs bowled and strike rate all filter on this.';

comment on column deliveries.credited_to_bowler is
    'True for bowled, caught, lbw, stumped, hit wicket, caught and bowled. False for run out, retired, obstructing the field, timed out.';

comment on column deliveries.ball_no is
    'Raw within-over sequence preserving wides and no-balls, so the true delivery order survives. Legal-ball counts come from legal_ball, not from this.';

create index deliveries_batter_idx on deliveries (batter_id);
create index deliveries_bowler_idx on deliveries (bowler_id);
create index deliveries_match_innings_idx on deliveries (match_id, innings_no);
create index deliveries_phase_idx on deliveries (phase);
