-- The name a fan knows each player by [A252].
--
-- `primary_name` is Cricsheet's register `unique_name`, an initials form that is
-- unreadable for much of the league: PWH de Silva is Wanindu Hasaranga, PHKD Mendis is
-- Kamindu Mendis. `display_name` is the player's English Wikipedia article title (a
-- disambiguator such as "(cricketer)" stripped), decided by hand in
-- etl/overrides/full_names.csv and written by `etl.derive_people --full-names`.
--
-- `primary_name` is left alone: the ETL, the validation checks and every override CSV
-- read it, and the archive's own spelling is what they are checked against. Readers that
-- show a name use coalesce(display_name, primary_name), so a blank row in the CSV shows
-- the archive's name rather than nothing.
--
-- Checked against the code BEFORE this was applied (the CLAUDE.md standing rule): the
-- loader writes this column and nothing else, and every reader that needs it names it.

alter table people add column display_name text;

alter table people add constraint people_display_name_ck
    check (display_name is null
           or (length(btrim(display_name)) between 1 and 40
               and display_name !~ '[<>]'));

create unique index people_display_name_uq on people (display_name)
    where display_name is not null;

comment on column people.display_name is
    'The name a fan knows him by: his English Wikipedia article title, from etl/overrides/full_names.csv (A252). NULL = undecided, show primary_name. Display only; never a key.';
