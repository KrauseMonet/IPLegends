-- Adds the column SPEC 7.1's wicket-cost rule needs and 007 did not carry.
--
-- 007 stored `runs_remaining_total` and nothing else, and in the same breath SPEC 7.1 was
-- corrected to price a wicket by the drop in expected FINAL total, because differencing
-- runs remaining is confounded and comes out negative. Final total is
-- `runs_so_far + runs_remaining`, so the ratified rule could not be computed from the
-- table that had just been applied. A19 does not help here: the missing quantity is not
-- derivable from anything stored.
--
-- 007 is applied and therefore immutable, so the column lands here rather than in the
-- statement that should have created it.
--
-- Truncate first so `not null` needs no default. A default would be a fabricated value in
-- the one part of this project most careful to avoid them, and there is nothing to
-- preserve: the fit has to be re-run either way. Until `etl.state_model --write` re-runs,
-- check 20 fails on the empty table, so the intermediate state cannot be mistaken for a
-- finished one.

truncate state_runs_remaining;

alter table state_runs_remaining
    add column runs_so_far_total bigint not null;

comment on column state_runs_remaining.runs_so_far_total is
    'Runs scored in the innings BEFORE this delivery, summed over observations. Exists so expected final total - (runs_so_far_total + runs_remaining_total) / observations - is derivable, which is what SPEC 7.1 prices a wicket with. Differencing runs_remaining_total alone is confounded by runs already scored and can come out negative.';
