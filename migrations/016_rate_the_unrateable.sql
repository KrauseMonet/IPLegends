-- SPEC 7.12. A71: reputation is the fallback for zero evidence, not just a floor on top of it.
--
-- A VIEW REPLACEMENT AND NOTHING ELSE, as 010-015 were.

-- ---------------------------------------------------------------------------------------
-- [A71] A65 rated every player-season that faced or bowled a ball. It left one population
-- untouched: squad members who did neither, in either discipline, all season.
--
-- Measured at 4 of 3,337 squad_members -- JP Faulkner 2011, SS Mundhe 2011, VY Mahesh 2009,
-- MS Bhandage 2025. These are not A27's excluded 47 (A27 never gives them a `squad_members`
-- row at all, because nothing about their appearance suggested a role). These four DO have
-- a role and a band or usage -- the squad selection process named them a bowler or a
-- batter -- but that season they were run out without facing, or never bowled an over that
-- survived, or sat out every match they were part of. `player_season_impact` has no row
-- for them at all, in either discipline, so `balls > 0` (A65's own test) does not admit
-- them: there is no per-ball figure to shrink, because there were no balls.
--
-- The fix is not a fifth per-ball evidence tier. There is no per-ball evidence at zero
-- balls, and manufacturing one would be the A33/A48 mistake in a new place: an individual
-- number the evidence cannot support. What these four DO have, sometimes, is a career --
-- Faulkner has six other rated seasons, Mahesh has three -- and A67 already established
-- that reputation may answer for a player when the season's own evidence cannot. So the
-- rule is the same one, run with no season term at all rather than a thin one:
--
--     blended_merit = reputation_merit,           if the player has ANY other rated season
--                    = scale.lo,                  otherwise
--
-- `reputation_merit` is exactly the CAREER_N = 2 shrunk-toward-league average A57 already
-- computes for everyone else, just evaluated with nothing to leave out (there is no current
-- season in `merit` to subtract, because this season contributed no evidence to it).
--
-- `scale.lo` -- the same 2nd-percentile anchor A58 already uses to fix the bottom of the
-- 70-99 scale -- is the fallback for a player with no rated season anywhere in nineteen
-- years (Mundhe, Bhandage). It is a deliberate arbitrary floor rather than a fabricated
-- merit number: it says plainly "the lowest a card can honestly read," not "here is what he
-- was worth." Zero would be wrong here for the same reason A23 and A48 already ruled it
-- out elsewhere -- it is the league AVERAGE, and there is no reason to believe an unrated
-- nobody is average rather than replacement-level.
--
-- `rated_per_ball` -- the number the engine actually plays, not only the card's face value
-- (A45/A57) -- is `blended_merit` converted through the SAME two reference exposures A59
-- already declares: 18 balls a match for batting, 24 for bowling. Nothing new is invented;
-- a runs-per-match reputation number has no per-ball equivalent of its own, so it borrows
-- the references the rest of the view already uses to move between the two units.
--
-- These four never enter `season_level` or `cohort_level` (built before this branch is
-- unioned in): a player with no evidence must not vote on where the season mean or the
-- cohort offset sits, for the same reason A68 keeps thin seasons out of that vote.

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
        i.not_rateable_reason,
        case when i.discipline = 'batting' then s.batting_band
             else s.bowling_usage end                       as cohort,
        (i.impact_total + 100 * i.prior_per_ball) / (i.balls + 100)
                                                            as shrunk_per_ball
    from player_season_impact i
    join franchise_seasons f using (franchise_season_id)
    join squad_members     s using (franchise_season_id, person_id)
    where i.balls > 0
),

season_level as (
    select discipline, season_year, avg(shrunk_per_ball) as season_mean
    from rateable
    where not_rateable_reason is null
    group by discipline, season_year
),

season_centred as (
    select r.*, r.shrunk_per_ball - sl.season_mean as centred_per_ball
    from rateable r
    join season_level sl using (discipline, season_year)
),

cohort_level as (
    select
        discipline,
        cohort,
        count(*) as cohort_n,
        case when count(*) >= 20 then avg(centred_per_ball) else 0.0 end as cohort_offset
    from season_centred
    where not_rateable_reason is null
    group by discipline, cohort
),

normalised as (
    select
        c.*,
        coalesce(cl.cohort_offset, 0.0) as cohort_offset,
        cl.cohort_n,
        c.centred_per_ball - coalesce(cl.cohort_offset, 0.0) as normalised_per_ball
    from season_centred c
    left join cohort_level cl using (discipline, cohort)
),

per_match as (
    select
        n.*,
        n.normalised_per_ball * n.balls::numeric / n.matches as impact_per_match,
        n.balls::numeric / n.matches                         as balls_per_match
    from normalised n
),

player_season as (
    select
        franchise_season_id,
        person_id,
        max(matches)          as matches,
        sum(impact_per_match) as impact_per_match,
        sum(balls_per_match)  as balls_per_match,
        coalesce(max(balls_per_match) filter (where discipline = 'batting'), 0) as bat_bpm,
        coalesce(max(balls_per_match) filter (where discipline = 'bowling'), 0) as bowl_bpm
    from per_match
    group by franchise_season_id, person_id
),

potm as (
    select
        a.franchise_season_id,
        m.player_of_match_id as person_id,
        count(*)::numeric    as potm_awards
    from matches m
    join appearances a
      on a.match_id = m.match_id and a.person_id = m.player_of_match_id
    where m.player_of_match_id is not null
    group by a.franchise_season_id, m.player_of_match_id
),

merit as (
    select
        ps.*,
        coalesce(p.potm_awards, 0) as potm_awards,
        least(ps.bat_bpm / 18.0, 1.0) * least(ps.bowl_bpm / 24.0, 1.0) as allrounder_share,
        ps.impact_per_match
          + 12.0 * coalesce(p.potm_awards, 0) / ps.matches
          + 5.0 * least(ps.bat_bpm / 18.0, 1.0) * least(ps.bowl_bpm / 24.0, 1.0)
                                                            as merit
    from player_season ps
    left join potm p using (franchise_season_id, person_id)
),

league as (select avg(merit) as league_merit from merit),

career as (
    select
        m.*,
        (sum(m.merit) over w - m.merit + 2.0 * l.league_merit)
            / (count(*) over w - 1 + 2.0) as career_merit
    from merit m
    cross join league l
    window w as (partition by m.person_id)
),

blended as (
    select
        c.*,
        greatest(
            (c.matches::numeric / (c.matches + 6)) * c.merit
              + (6 / (c.matches + 6.0)) * c.career_merit,
            0.85 * c.career_merit + 0.15 * c.merit
        ) as blended_merit
    from career c
),

scale as (
    select
        percentile_cont(0.02)  within group (order by blended_merit) as lo,
        percentile_cont(0.998) within group (order by blended_merit) as hi
    from blended
),

-- [A71] Every person's own reputation, evaluated with no current season to leave out: the
-- same CAREER_N = 2 shrink toward the league that `career` above computes for everyone
-- else, just without the leave-one-out subtraction, because there is nothing here to
-- subtract.
reputation as (
    select
        m.person_id,
        (sum(m.merit) + 2.0 * max(l.league_merit)) / (count(*) + 2.0) as reputation_merit
    from merit m
    cross join league l
    group by m.person_id
),

-- [A71] Squad members with no `player_season_impact` row in either discipline this season:
-- zero evidence, not thin evidence. Excluded from `rateable` from the start, so they never
-- touch `season_level` or `cohort_level` -- a player with nothing to show must not vote on
-- where the season mean or the cohort offset sits (A68's rule, extended to the case A68
-- did not anticipate).
zero_evidence as (
    select
        s.franchise_season_id,
        s.person_id,
        f.season_year,
        case when s.batting_band is not null then 'batting' else 'bowling' end as discipline,
        coalesce(s.batting_band, s.bowling_usage) as cohort,
        s.matches_played
    from squad_members s
    join franchise_seasons f using (franchise_season_id)
    where not exists (
        select 1 from player_season_impact i
        where i.franchise_season_id = s.franchise_season_id
          and i.person_id = s.person_id
    )
),

zero_evidence_rated as (
    select
        z.*,
        r.reputation_merit,
        -- [A71] Reputation if he has any; otherwise the scale's own floor -- an honest
        -- "lowest a card can read," never a fabricated merit number (A23/A48's rule).
        coalesce(r.reputation_merit, sc.lo) as blended_merit,
        case when z.discipline = 'batting' then 18.0 else 24.0 end as ref_balls_per_match,
        sc.lo,
        sc.hi
    from zero_evidence z
    left join reputation r using (person_id)
    cross join scale sc
)

select
    n.franchise_season_id,
    n.person_id,
    n.discipline,
    n.season_year,
    n.cohort,
    n.balls,
    n.matches,
    n.not_rateable_reason,

    n.shrunk_per_ball,
    n.centred_per_ball,
    n.cohort_offset,
    n.normalised_per_ball,

    b.potm_awards,
    b.allrounder_share,
    b.impact_per_match  as season_impact_per_match,
    b.merit,
    b.career_merit,
    b.blended_merit,

    n.normalised_per_ball
        + (b.blended_merit - b.impact_per_match) / b.balls_per_match
                                                            as rated_per_ball,

    round(
        70.0 + 29.0 * greatest(0.0, least(1.0,
            (b.blended_merit - s.lo) / nullif(s.hi - s.lo, 0)))
    )::int                                                  as display_rating,

    n.prior_per_ball,
    n.prior_source
from per_match n
join blended b using (franchise_season_id, person_id)
cross join scale s

union all

-- [A71] The zero-evidence branch. Same column shapes as above; the columns that describe
-- per-ball evidence (`shrunk_per_ball`, `centred_per_ball`, `cohort_offset`,
-- `normalised_per_ball`, `merit`, `season_impact_per_match`, `prior_per_ball`) are NULL
-- because there is no delivery behind them to compute from, not because they are zero.
select
    z.franchise_season_id,
    z.person_id,
    z.discipline,
    z.season_year,
    z.cohort,
    0                                  as balls,
    z.matches_played::smallint         as matches,
    'no_evidence'                      as not_rateable_reason,

    null::double precision             as shrunk_per_ball,
    null::double precision             as centred_per_ball,
    null::double precision             as cohort_offset,
    null::double precision             as normalised_per_ball,

    0::numeric                         as potm_awards,
    0::numeric                         as allrounder_share,
    null::double precision             as season_impact_per_match,
    null::double precision             as merit,
    z.reputation_merit                 as career_merit,
    z.blended_merit,

    z.blended_merit / z.ref_balls_per_match                as rated_per_ball,

    round(
        70.0 + 29.0 * greatest(0.0, least(1.0,
            (z.blended_merit - z.lo) / nullif(z.hi - z.lo, 0)))
    )::int                                                  as display_rating,

    null::double precision             as prior_per_ball,
    'reputation_floor'                 as prior_source
from zero_evidence_rated z;

comment on view player_season_rating is
    'SPEC 7.8-7.12 rating. EVERY squad member is rated (A65 rates every player-season that faced or bowled a ball; A71 covers the 4 of 3,337 who did neither, from reputation alone or the scale floor if they have none). Runs above par per match (A54), disciplines added (A55), Player of the Match priced in (A56), a continuous all-rounder term (A59), shrunk on MATCHES for the per-match quantity (A66), with the career acting as a FLOOR rather than a blend (A67/A69, convex). Integer 70-99, percentile-anchored (A58/A60). k = 100 balls, K_MATCHES = 6, POTM_RUNS = 12, ALLROUNDER_RUNS = 5, CAREER_FLOOR = 0.85 (convex, A69), CAREER_N = 2 and the anchors live here and nowhere else. Season means and cohort offsets are estimated on gate-passing seasons and applied to all (A68). `not_rateable_reason = ''no_evidence''` and `prior_source = ''reputation_floor''` mark the A71 branch.';
