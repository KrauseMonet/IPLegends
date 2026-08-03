-- The per-SEASON twin of migration 017's person_batting_positions. Same shape, one
-- more key column: one row per (franchise_season_id, person_id, position), how many
-- innings that person batted at that position IN THAT SEASON.
--
-- Not a new derivation either. `etl.derive_squads.batting_positions()` already builds
-- exactly this dict, keyed by (franchise_season_id, person_id) -> list of positions per
-- innings, as the input to BOTH squad_members' per-season modal/min/max (migration 004)
-- and `etl.career_positions`'s career-wide aggregate (migration 017) -- the season key
-- was simply thrown away on the way to the career table. This persists it instead of
-- discarding it, because the four-role classification (top order / middle / finisher /
-- tailender, A76) needs to try a player's CURRENT season's own evidence first and only
-- fall back to his career when that season alone is too thin -- exactly the cascade
-- A57/A67/A71 already use for ratings, applied here to batting position instead.
--
-- Same undecorated-archive-fact discipline as 017: no threshold, no fallback, no game
-- rule lives here. The innings floor that decides whether a SEASON's own evidence is
-- trustworthy is a GAME rule and is applied once, downstream, in `etl.batting_roles`
-- (A76) -- never here.

create table person_season_batting_positions (
    franchise_season_id int      not null references franchise_seasons (franchise_season_id),
    person_id           text     not null references people (person_id),
    position             smallint not null,
    innings              smallint not null,

    primary key (franchise_season_id, person_id, position),

    constraint person_season_batting_positions_position_ck
        check (position between 1 and 11),
    constraint person_season_batting_positions_innings_ck
        check (innings >= 1)
);

comment on table person_season_batting_positions is
    'A76. Per-FRANCHISE-SEASON count of innings a person batted at each position, 1-11. '
    'No row means zero innings there that season. The floor for trusting a season''s own '
    'evidence, and every fallback rule beyond it, are GAME rules applied once in '
    'etl.batting_roles, never here (A19/A70).';

create index person_season_batting_positions_person_idx
    on person_season_batting_positions (person_id);
