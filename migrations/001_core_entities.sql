-- Core identity tables: people, franchises, franchise-seasons.
-- Everything downstream keys on person_id and franchise_season_id, never on name strings.

create table people (
    person_id          text primary key,   -- cricsheet registry identifier
    primary_name       text not null,      -- cricsheet register `unique_name`
    nationality        text,               -- derived, SPEC 6.1
    nationality_source text,               -- 'international' | 'default' | 'override'
    is_overseas        boolean,            -- derived from nationality; hard rule in the XI
    bowling_style      text,               -- SPEC 6.3, override CSV only. NULL until filled.
    batting_hand       text,
    is_keeper          boolean not null default false,

    constraint people_nationality_source_ck
        check (nationality_source in ('international', 'default', 'override')),
    constraint people_bowling_style_ck
        check (bowling_style in ('pace', 'spin'))
);

comment on column people.nationality_source is
    'international = found in a Test/ODI/T20I archive; default = not found, provisionally Indian, MUST be audited; override = set by hand in etl/overrides/nationality.csv';

comment on column people.bowling_style is
    'Not derivable from Cricsheet. Populated solely from etl/overrides/bowling_style.csv. NULL means unknown - pace/spin splits must return NULL, never a guess.';


create table franchises (
    franchise_id   serial primary key,
    canonical_name text not null unique
);

comment on table franchises is
    'Stable across renames. Deccan Chargers/Sunrisers Hyderabad and Gujarat Lions/Gujarat Titans are SEPARATE franchises.';


create table franchise_seasons (
    franchise_season_id serial primary key,
    franchise_id        int  not null references franchises (franchise_id),
    season_year         int  not null,
    display_name        text not null,   -- era-correct: 'Delhi Daredevils' for 2012

    unique (franchise_id, season_year)
);

comment on column franchise_seasons.display_name is
    'The name the franchise actually carried that season. The game shows "Delhi Daredevils 2012", not "Delhi Capitals 2012".';

create index franchise_seasons_season_year_idx on franchise_seasons (season_year);
