-- SPEC 7.4-7.6. Within-season normalisation, cohort offsets, and the 0-100 display scale.
--
-- A VIEW REPLACEMENT AND NOTHING ELSE. No table, no column, no stored number.
--
-- Season means and cohort offsets are derived parameters, exactly as k is. A35 settled the
-- principle: a stored parameter beside a stored result is two copies of one fact, and the
-- day the parameter is re-tuned the two disagree with nothing to catch it. So they live
-- here as CTEs, the raw totals stay the single source, and re-tuning stays a
-- `create or replace` that arrives as a migration and is therefore auditable.

-- ---------------------------------------------------------------------------------------
-- [A41] What 7.4 actually had to correct, and what it did not.
--
-- SPEC 7.4 justified cohort offsets by saying a great finisher would otherwise "be
-- compressed to 61 for facing fewer balls than an opener". That reasoning describes raw
-- strike rates, where a finisher genuinely does look worse than an opener, and it is
-- WRONG for this rating. Impact is scored per ball against the exact (over, wickets)
-- state, so the state model has already made the situational correction before cohorts
-- are reached. Measured, after within-season centring:
--
--     batting   opener -0.016  top_order -0.008  middle +0.033  finisher +0.019
--     bowling   middle +0.011  mixed     -0.011  powerplay -0.004  death +0.002
--
-- Finishers score ABOVE average per ball, not below, and the whole cohort spread is 0.049
-- runs/ball against a within-cohort SD of 0.185. The season drift it sits beside is 0.25 -
-- five times the entire cohort effect. The offsets are kept because they are cheap and
-- still move 27 ranks of 1,025, but they are a SMALL RESIDUAL correction after the state
-- model has done the situational work. The old rationale would justify a correction five
-- times too large, so it is deleted rather than softened.

-- ---------------------------------------------------------------------------------------
-- [A42] Centre within season. Do NOT divide by the season's standard deviation.
--
-- The level drift is real and large: batting runs +0.007 (2008) to +0.257 (2026), bowling
-- mirrors it from +0.083 to -0.111. Without centring, nobody would ever want a 2009 squad.
--
-- The SPREAD drift is not real. Season SDs range 0.136 to 0.216, but only 1 of 19 seasons
-- in each discipline sits more than two sampling standard errors from the pooled SD, so
-- that variation is estimation noise off ~50 observations. Dividing by it reorders 564 of
-- 1,025 batting seasons by more than ten places on nothing at all. This is A2's argument
-- against per-cell z-scoring one level up, and it holds at both levels: do not normalise
-- by a quantity that cannot be estimated stably.
--
-- The mean is UNWEIGHTED - one entry per player-season, not per ball. The rating is a
-- statement about players, so the reference population is players. Ball-weighting would
-- let a handful of high-volume players define the baseline everyone else is measured
-- against, tilting the reference toward exactly the players who least need correcting.
-- The two differ by about 0.03; unweighted is the principled choice and not merely the
-- close one, and it is recorded here so it is not "corrected" later.

-- ---------------------------------------------------------------------------------------
-- [A43] A cohort below the evidence threshold gets ZERO, and zero-by-insufficient-evidence
-- is not the same statement as zero-by-measurement.
--
-- `death` holds 8 rateable player-seasons over 7 seasons at +0.002 +/- 0.125. That is not
-- an estimate, it is a coin flip near zero, and fitting it would be fitting noise. `tail`
-- holds none at all (A39) - 0 of 707 clear A33's 100-ball floor - so it has nothing to fit
-- from and must not be given something.
--
-- Expressed as a THRESHOLD rather than a hardcoded list of two cohort names, because a
-- name list silently goes stale: a revised archive that gave `death` forty seasons would
-- leave the hardcoded zero standing and wrong. The threshold sits in a wide empty gap -
-- `death` has 8 and the next smallest cohort, `finisher`, has 45 - so its exact value is
-- not load-bearing and any number in (8, 45] behaves identically today. It is a rule about
-- evidence, the same shape as A33's floors, not a tuned constant.

-- DROP then CREATE, not CREATE OR REPLACE. Postgres only lets a replacement APPEND columns
-- to the end of a view's output list; this one reorders it and removes `raw_per_ball`, so
-- a replace would fail outright. `raw_per_ball` goes because it is the pre-normalisation
-- number and nothing may consume it now that a normalised one exists - leaving it would be
-- offering two ratings and inviting a caller to read the wrong one. It was verified unread
-- outside this file before removal.
drop view player_season_rating;

create view player_season_rating as
with rateable as (
    select
        i.franchise_season_id,
        i.person_id,
        i.discipline,
        f.season_year,
        i.balls,
        i.matches,
        i.prior_per_ball,
        i.prior_source,
        case when i.discipline = 'batting' then s.batting_band
             else s.bowling_usage end                       as cohort,
        -- [A35] k = 100, PROVISIONAL, and still the only place it appears. It resolves
        -- with A34 in the pass after this one, against the normalised numbers this view
        -- now produces - which is precisely why it was never allowed to become a column.
        (i.impact_total + 100 * i.prior_per_ball) / (i.balls + 100)
                                                            as shrunk_per_ball
    from player_season_impact i
    join franchise_seasons f using (franchise_season_id)
    join squad_members     s using (franchise_season_id, person_id)
    where i.not_rateable_reason is null
),

-- A42. Unweighted: avg() over player-seasons, never over balls.
season_level as (
    select discipline, season_year, avg(shrunk_per_ball) as season_mean
    from rateable
    group by discipline, season_year
),

season_centred as (
    select r.*, r.shrunk_per_ball - sl.season_mean as centred_per_ball
    from rateable r
    join season_level sl using (discipline, season_year)
),

-- A2/A43. Pooled across all seasons - one offset per (discipline, cohort), never per
-- (season x cohort). Those cells run to n = 1 and 20 of the 75 batting cells hold fewer
-- than ten players, which is the noise A2 refused.
cohort_level as (
    select
        discipline,
        cohort,
        count(*)                       as cohort_n,
        count(distinct season_year)    as cohort_seasons,
        case when count(*) >= 20 then avg(centred_per_ball) else 0.0 end as cohort_offset
    from season_centred
    group by discipline, cohort
),

normalised as (
    select
        c.*,
        cl.cohort_offset,
        cl.cohort_n,
        c.centred_per_ball - cl.cohort_offset as normalised_per_ball
    from season_centred c
    -- LEFT, and the offset coalesced to zero, so an unclassified cohort arrives
    -- uncorrected rather than vanishing. An inner join here would silently drop the row,
    -- which is the A37 failure exactly: a rating that disappears leaves no symptom.
    left join cohort_level cl using (discipline, cohort)
),

-- [A44] One anchor for the whole deck: a single SD across both disciplines and every
-- season and cohort. Per-cohort or per-season rescaling would break the only thing the
-- display number is for - a 92 has to mean the same thing on every card, or cross-squad
-- comparison is meaningless. Batting's SD is 0.1838 and bowling's 0.1766, a 4% difference,
-- so sharing costs almost nothing and buys the comparison a drafter actually makes: a
-- batting 92 and a bowling 92 are the same distance in runs per ball.
--
-- Derived rather than frozen as a literal (A19). A revised archive therefore rescales, and
-- that is correct: the scale is defined relative to the league it describes.
anchor as (
    select stddev_pop(normalised_per_ball) as sd from normalised
)

select
    n.franchise_season_id,
    n.person_id,
    n.discipline,
    n.season_year,
    n.cohort,
    n.balls,
    n.matches,

    n.shrunk_per_ball,
    n.centred_per_ball,
    n.cohort_offset,

    -- [A45] 7.6. The simulator reads THIS column and never the display rating.
    n.normalised_per_ball,

    -- [A44] 7.4's display scale: LINEAR on the normalised value, not percentile.
    --
    -- Percentile is more legible one number at a time and destroys the only thing that
    -- makes the ratings worth building - the gaps. Bumrah 2020 standing clear of the field
    -- is real information and is the whole reason a knowledgeable drafter reaches for him;
    -- percentile flattens that into "near the top, like several others". Worse, under
    -- percentile two players a hair apart and two players a chasm apart show the same gap,
    -- so the drafter cannot see which pick is the real edge. Linear shows the edge.
    --
    -- S = 3.5. The clip is a GUARD, not a transform: the observed range is -3.12 to +3.60
    -- SD, near symmetric with no fat tail, because shrinkage already pulled every extreme
    -- season (all of them 120-360 balls) toward its prior before it could run away. At 3.5
    -- the clip touches exactly ONE of 1,812 rows. It is set here rather than lower because
    -- clipping is percentile's sin applied locally - two clipped seasons both read 100 and
    -- the gap between them is gone - and S = 3.0 would destroy seven such gaps to buy back
    -- eleven points of scale.
    50.0 + 50.0 * greatest(-1.0, least(1.0, n.normalised_per_ball / (3.5 * a.sd)))
                                                            as display_rating,

    n.prior_per_ball,
    n.prior_source
from normalised n
cross join anchor a;

comment on view player_season_rating is
    'SPEC 7.4-7.6 normalised rating, rateable player-seasons only. Within-season centring (unweighted, no SD division - A42) then pooled cohort offsets (A43, zero below 20 observations). normalised_per_ball is what the simulator consumes; display_rating is the UI 0-100 and the simulator must not read it (A45). k = 100 and the S = 3.5 display anchor live here and nowhere else.';
