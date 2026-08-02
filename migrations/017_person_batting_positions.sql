-- SPEC 1.2 / A72. Career-wide batting-position experience, one row per position a
-- person has actually batted at, with how many innings.
--
-- This is a genuinely new derivation, not an aggregate over an existing column.
-- `squad_members.batting_position_min/max` (migration 004) are per FRANCHISE-SEASON
-- envelopes, and MIN/MAX of those across a career was tried first -- it needs no new
-- ETL -- and it does not hold up: SV Samson comes out 1-8 and RK Singh (Rinku) comes
-- out 3-8, both far looser than reputation, because a single outlier innings widens a
-- MIN/MAX envelope forever and can never be forgotten. The fix is to count, not
-- envelope: a position is real evidence about a player only if he has batted there
-- often enough that one emergency knock cannot manufacture it.
--
-- So this table holds an exact count, per (person, position), across every innings in
-- the archive -- derived in `etl/career_positions.py` by re-running the same
-- first-appearance-wins scan `etl/positions.py.batting_order()` already established
-- (A4), just accumulated across ALL of a person's innings rather than reset each
-- franchise-season. Reusing `etl.derive_squads.batting_positions()`'s per-innings scan
-- rather than re-deriving it a second way.
--
-- No threshold and no fallback live here. This table states what was observed and
-- nothing more: a person with zero rows never faced a ball or was never a non-striker
-- at the start of an innings; a person whose highest count at any position is 1 has
-- exactly one recorded innings there. The MIN_INNINGS_AT_POSITION = 5 bar that decides
-- what counts as "eligible to bat there" is a GAME rule, not an archive fact, and per
-- A70/A71's precedent it is applied in exactly one place downstream
-- (`etl.feasibility.Card.positions`), never here and never in a view over this table.
-- A count column, once written, does not need a fallback -- an absent row already means
-- zero, which is exactly what a fallback would otherwise have to invent.

create table person_batting_positions (
    person_id text     not null references people (person_id),
    position  smallint not null,
    innings   smallint not null,

    primary key (person_id, position),

    constraint person_batting_positions_position_ck
        check (position between 1 and 11),
    constraint person_batting_positions_innings_ck
        check (innings >= 1)
);

comment on table person_batting_positions is
    'A72. Career-wide count of innings a person has batted at each position, 1-11, '
    'across every match in the archive (super overs excluded, matching A4/A17). No row '
    'means zero innings there. The eligibility bar (>= 5 innings) and the tail-only '
    'fallback for a player with no qualifying position are both GAME rules applied once '
    'in etl.feasibility.Card.positions, never here -- this table is the archive''s own '
    'answer, undecorated (A19, A70).';

create index person_batting_positions_person_idx on person_batting_positions (person_id);
