-- Matches and per-match squad listings.

create table matches (
    match_id           text primary key,   -- cricsheet filename stem
    match_date         date not null,
    season_year        int  not null,      -- derived from match_date, NOT from info.season
    raw_season_label   text,               -- the source string, e.g. '2007/08', kept for reference
    venue              text,
    city               text,
    team_a_fs_id       int references franchise_seasons (franchise_season_id),
    team_b_fs_id       int references franchise_seasons (franchise_season_id),
    toss_winner_fs_id  int references franchise_seasons (franchise_season_id),
    toss_decision      text,
    winner_fs_id       int references franchise_seasons (franchise_season_id),
    result_type        text,
    result_margin      int,

    -- What actually settled the match, so the row is self-describing.
    -- result_type = 'tie' with a populated winner_fs_id is correct but reads as a
    -- contradiction; decided_by = 'eliminator' says why. 'dls' is the case that
    -- otherwise hides completely: a D/L result carries an ordinary runs or wickets
    -- margin and is indistinguishable from a normal win without this column.
    decided_by         text,
    scheduled_overs    int,                -- always 20 in this archive; kept for completeness
    had_super_over     boolean not null default false,

    -- True when any innings was scheduled for fewer than 120 balls (rain/DLS).
    -- SPEC 7.1: these matches are excluded from rating computation entirely, but
    -- still count toward display statistics - a player's card shows their real runs.
    was_reduced        boolean not null default false,
    player_of_match_id text references people (person_id),

    constraint matches_result_type_ck
        check (result_type in ('runs', 'wickets', 'tie', 'no result')),
    constraint matches_decided_by_ck
        check (decided_by in ('runs', 'wickets', 'eliminator', 'dls')),

    -- A tie is always settled in the super over in this archive, and a match with
    -- no result is settled by nothing at all.
    constraint matches_tie_decided_by_eliminator_ck
        check (result_type <> 'tie' or decided_by = 'eliminator'),
    constraint matches_no_result_undecided_ck
        check (result_type <> 'no result' or decided_by is null),
    constraint matches_toss_decision_ck
        check (toss_decision in ('bat', 'field')),
    constraint matches_distinct_teams_ck
        check (team_a_fs_id is distinct from team_b_fs_id)
);

comment on column matches.decided_by is
    'runs | wickets | eliminator | dls. Null only for a no-result match. Takes dls in preference to the margin type, because the margin type is already in result_type and the D/L involvement is not recorded anywhere else.';

comment on column matches.season_year is
    'Derived from match_date. info.season is inconsistent for the IPL (single year vs 2007/08 vs 2020/21) and must never be used directly.';

create index matches_season_year_idx on matches (season_year);
create index matches_date_idx on matches (match_date);


create table appearances (
    match_id            text not null references matches (match_id) on delete cascade,
    person_id           text not null references people (person_id),
    franchise_season_id int  not null references franchise_seasons (franchise_season_id),

    -- Cricsheet's info.players assertion, stored as-is. From 2023 the Impact Player
    -- rule means this can list 12 per side, including players who never took the field.
    named_in_squad      boolean not null default true,

    -- Derived: did this person actually bat, bowl, field a dismissal, or get out?
    -- Kept separate from named_in_squad so Impact Player era counts stay honest.
    participated        boolean not null default false,

    primary key (match_id, person_id)
);

create index appearances_person_idx on appearances (person_id);
create index appearances_fs_idx on appearances (franchise_season_id);
