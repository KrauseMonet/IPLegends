-- Reconstructed squads, one row per person per franchise-season.
-- Roles and bands are derived per franchise-season, not per career: players change
-- role across a career and the draft entry is a season, not a person.

create table squad_members (
    franchise_season_id int  not null references franchise_seasons (franchise_season_id),
    person_id           text not null references people (person_id),
    matches_played      int  not null,

    role                text not null,   -- 'keeper' | 'batter' | 'bowler' | 'allrounder'

    -- SPEC 6.4: modal batting position for this squad, plus the spread so we can see
    -- who actually moved around the order.
    batting_position_modal smallint,
    batting_position_min   smallint,
    batting_position_max   smallint,
    batting_band           text,        -- 'opener' | 'top_order' | 'middle' | 'finisher' | 'tail'

    -- SPEC 6.5: where this bowler's legal deliveries actually came from.
    bowling_usage          text,        -- 'powerplay' | 'middle' | 'death' | 'mixed'

    primary key (franchise_season_id, person_id),

    constraint squad_members_role_ck
        check (role in ('keeper', 'batter', 'bowler', 'allrounder')),
    constraint squad_members_band_ck
        check (batting_band in ('opener', 'top_order', 'middle', 'finisher', 'tail')),
    constraint squad_members_usage_ck
        check (bowling_usage in ('powerplay', 'middle', 'death', 'mixed'))
);

comment on column squad_members.batting_band is
    'Canonical bands: opener 1-2, top_order 3-4, middle 5-6, finisher 7-8, tail 9-11.';

create index squad_members_person_idx on squad_members (person_id);
