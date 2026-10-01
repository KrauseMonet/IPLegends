-- SPEC 7.1/7.2/10, A160. The engine plays two numbers per discipline instead of one.
--
-- ---------------------------------------------------------------------------------------
-- [A160] A single per-ball rating can only make a good batter BOTH score faster and get
-- out less: the engine's tilt moves one mean. Real players are two-dimensional --
-- Abhishek Sharma 2024 struck 53 per 100 balls above his season AND was dismissed more
-- often, and the one-number engine played him as +10 and almost never out. So each ball's
-- impact is now also stored as its two halves:
--
--   runs_total         sum of (runs - E[runs | state])            runs, bowling: SAVED
--   outs_excess_total  sum of (out - P[out | state])              outs, bowling: wickets
--   outs_expected      sum of P[out | state]                      the multiplier's base
--
-- each with a prior of its own, so the view shrinks each half exactly as it shrinks the
-- total (k = 100). The total stays what the CARD rates; the halves are what the engine
-- plays. Measured on 536 batter-seasons and 588 bowler-seasons, the engine reproducing
-- each season's real excess: scoring 0.70 -> 0.99, dismissals 0.53 -> 0.98, economy
-- 0.66 -> 0.99, wickets 0.66 -> 0.99 (correlations, in-sample by design -- the question
-- is whether the engine can PLAY the season, not predict the next one).
--
-- The new columns are added NULLABLE with a NOT VALID check: existing rows predate the
-- split and are replaced wholesale by the next `etl.impact --write`, which truncates and
-- rewrites; every row written from now on must carry them.
--
-- impact_total is NOT made derivable from the halves (A19 would ask for that), because
-- the card's wicket half is priced in RUNS and the stored half is in OUTS -- the price is
-- per state and lives in etl.impact.Costs, so the total is the one place the priced sum
-- exists. A CHECK could not recompute it without a second copy of the price table.

alter table player_season_impact
    add column runs_total          double precision,
    add column runs_prior_per_ball double precision,
    add column outs_excess_total   double precision,
    add column outs_prior_per_ball double precision,
    add column outs_expected       double precision;

alter table player_season_impact
    add constraint player_season_impact_halves_present check (
        runs_total is not null and runs_prior_per_ball is not null
        and outs_excess_total is not null and outs_prior_per_ball is not null
        and outs_expected is not null and outs_expected >= 0
    ) not valid;

comment on column player_season_impact.runs_total is
    'A160. Sum over the season of (runs - E[runs | state]); for bowling, runs SAVED. The runs half of impact_total.';
comment on column player_season_impact.outs_excess_total is
    'A160. Sum of (out - P[out | state]); batting: dismissals above expected (bad), bowling: wickets above expected (good). In outs, not runs.';
comment on column player_season_impact.outs_expected is
    'A160. Sum of P[out | state] over the season; outs_excess_total / outs_expected is the dismissal multiplier before shrinkage.';

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
                                                            as shrunk_per_ball,
        -- [A160] The two halves, shrunk with the same k toward priors of their own.
        (i.runs_total + 100 * i.runs_prior_per_ball) / (i.balls + 100)
                                                            as shrunk_runs_per_ball,
        (i.outs_excess_total + 100 * i.outs_prior_per_ball) / (i.balls + 100)
                                                            as shrunk_outs_per_ball,
        i.outs_expected
    from player_season_impact i
    join franchise_seasons f using (franchise_season_id)
    join squad_members     s using (franchise_season_id, person_id)
    where i.balls > 0
),

season_level as (
    select discipline, season_year, avg(shrunk_per_ball) as season_mean,
           avg(shrunk_runs_per_ball) as season_mean_runs,
           avg(shrunk_outs_per_ball) as season_mean_outs
    from rateable
    where not_rateable_reason is null
    group by discipline, season_year
),

season_centred as (
    select r.*, r.shrunk_per_ball - sl.season_mean as centred_per_ball,
           -- [A160] What the ENGINE plays: season-centred, and deliberately NOT
           -- cohort-adjusted. The cohort offset is a fairness correction for the CARD
           -- (an opener compared with openers); the engine replays real states, and an
           -- opener's real dismissal rate against those states is what it has to reproduce.
           r.shrunk_runs_per_ball - sl.season_mean_runs as scoring_per_ball,
           greatest(0.1, 1 + (r.shrunk_outs_per_ball - sl.season_mean_outs)
                             / nullif(r.outs_expected / r.balls, 0)) as dismissal_multiplier
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
            -- [A159] 0.5/0.5, was 0.85/0.15 (A67/A69). Still convex, still lift-only.
            0.5 * c.career_merit + 0.5 * c.merit
        ) as blended_merit
    from career c
),

-- [A118] hi is the 99.2nd percentile; lo is untouched. A74 moved it from p99.8 to p99
-- and A118 from p99 to p99.2 -- see the file header.
scale as (
    select
        percentile_cont(0.02) within group (order by blended_merit) as lo,
        percentile_cont(0.992) within group (order by blended_merit) as hi
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
    n.prior_source,
    n.shrunk_runs_per_ball,
    n.scoring_per_ball,
    n.dismissal_multiplier
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
    'reputation_floor'                 as prior_source,
    null::double precision             as shrunk_runs_per_ball,
    null::double precision             as scoring_per_ball,
    null::double precision             as dismissal_multiplier
from zero_evidence_rated z;

comment on view player_season_rating is
    'SPEC 7.8-7.12 rating. EVERY squad member is rated (A65 rates every player-season that faced or bowled a ball; A71 covers the 4 of 3,337 who did neither, from reputation alone or the scale floor if they have none). Runs above par per match (A54), disciplines added (A55), Player of the Match priced in (A56), a continuous all-rounder term (A59), shrunk on MATCHES for the per-match quantity (A66), with the career acting as a FLOOR rather than a blend (A67/A69, convex). Integer 70-99, anchored at the 2nd and 99.2ND percentiles of blended merit (A74 lowered the high anchor from the 99.8th to the 99th, A118 from the 99th to the 99.2nd -- see each file header). k = 100 balls, K_MATCHES = 6, POTM_RUNS = 12, ALLROUNDER_RUNS = 5, CAREER_FLOOR = 0.5 (convex, A69; was 0.85 until A159), CAREER_N = 2 and the anchors live here and nowhere else. Season means and cohort offsets are estimated on gate-passing seasons and applied to all (A68). [A160] `scoring_per_ball` and `dismissal_multiplier` are what the ENGINE plays: the two halves of a season, season-centred and NOT cohort-adjusted, with no Player-of-the-Match, all-rounder or reputation term -- the engine replays the season as it happened, and `rated_per_ball`/`display_rating` are the card''s. `not_rateable_reason = ''no_evidence''` and `prior_source = ''reputation_floor''` mark the A71 branch.';
