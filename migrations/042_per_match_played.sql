-- SPEC 7.8-7.12, A165. A VIEW REPLACEMENT AND NOTHING ELSE: 041's view with two changes to
-- what a card judges.
--
-- ---------------------------------------------------------------------------------------
-- [A165] (1) A part-timer's bowling is not on his card. Ratified by the user: "don't
-- consider players who bowl under a certain amount of overs as allrounders and judge them
-- solely on batting numbers." The line is A26's own calibrated one -- a season is a
-- `batter` or `keeper` season when it bowled under BOWL_MIN = 6 legal balls a match -- so
-- the card and the squad's role cannot disagree about who is an all-rounder. For those
-- seasons the bowling counts for nothing: no statistics, no impact, no all-rounder share,
-- and it is excluded from every bowling peer group. The ENGINE still plays whatever
-- bowling he did, and `rated_per_ball` still integrates over every discipline, so check 22
-- holds.
--
-- (2) Every per-match quantity is divided by the matches PLAYED that season, not the
-- matches in that discipline. A batter who bowled two overs in one of fourteen matches
-- read as bowling twelve balls a match; for a genuine all-rounder who bowled in fewer
-- matches than he batted, the same error inflated his bowling exposure. Shashank Singh
-- 2025 (350 runs, average 50) read 76 off one wicketless two-over spell.

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
        i.outs_expected,
        -- [A165] A26's calibrated season role. A `batter` or `keeper` season bowled under
        -- an over a match (BOWL_MIN = 6 legal balls), and its bowling does not count on
        -- the card at all.
        s.role,
        not (i.discipline = 'bowling' and s.role in ('batter', 'keeper')) as on_card
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

-- [A163] The card's STATISTICAL half: each season's headline numbers against its season
-- and its role. Counted straight from the scoring set (A19: derived, not stored), on the
-- same deliveries the impact numbers are scored on, so the two halves of the blend speak
-- about one universe.
-- [A164] EVERY match, rain-shortened ones included -- balls, runs, wickets and matches
-- alike -- so a card's statistics are the season's public record (and the figures the card
-- itself displays, which `load_deck` has always counted this way). Only super overs are
-- left out, as they are from every season total. The impact half keeps the scoring set:
-- its match model needs a full-length innings to price a ball against.
raw_stats as (
    select batting_fs_id as franchise_season_id, batter_id as person_id,
           'batting'::text as discipline,
           sum(runs_batter)::float8 as runs,
           count(*) filter (where player_out_id = batter_id
                            and wicket_kind is distinct from 'retired hurt')::float8 as outs,
           count(*) filter (where extra_wides = 0)::float8 as balls,
           count(distinct match_id) filter (where extra_wides = 0)::float8 as matches
    from deliveries
    where not is_super_over
    group by 1, 2
    union all
    select bowling_fs_id, bowler_id, 'bowling',
           sum(runs_batter + extra_wides + extra_noballs)::float8,
           count(*) filter (where credited_to_bowler)::float8,
           count(*) filter (where legal_ball)::float8,
           count(distinct match_id) filter (where legal_ball)::float8
    from deliveries
    where not is_super_over
    group by 1, 2
),

stat_inputs as (
    select n.franchise_season_id, n.person_id, n.discipline, n.season_year, n.cohort,
           n.not_rateable_reason,
           coalesce(nullif(r.matches, 0), n.matches::float8) as matches,
           coalesce(nullif(r.balls, 0), n.balls::float8) as balls,
           coalesce(r.runs, 0) as runs, coalesce(r.outs, 0) as outs,
           -- [A165] the matches he PLAYED this season, in either discipline
           max(coalesce(nullif(r.matches, 0), n.matches::float8))
               over (partition by n.franchise_season_id, n.person_id) as season_matches
    from normalised n
    left join raw_stats r using (franchise_season_id, person_id, discipline)
    -- [A165] a part-timer's bowling is not a bowling season, so it is nobody's peer either
    where n.on_card
),

-- The season's pooled rates among gate-passing seasons: what a thin season is shrunk toward.
season_rates as (
    select discipline, season_year,
           sum(runs) / sum(balls)            as per_ball,
           sum(runs) / nullif(sum(outs), 0)  as per_out
    from stat_inputs
    where not_rateable_reason is null
    group by 1, 2
),

-- Three statistics per discipline, each oriented so higher is better.
--   batting: runs per match, strike rate (runs per ball, shrunk by 100 balls), average
--            (runs per dismissal, shrunk by 5 dismissals)
--   bowling: wickets per match, economy (runs conceded per legal ball, shrunk by 100 balls,
--            negated), wickets in the season
stat_vals as (
    select s.*,
           case when s.discipline = 'batting' then s.runs / s.matches
                else s.outs / s.matches end                                   as v1,
           case when s.discipline = 'batting'
                then (s.runs + 100 * sr.per_ball) / (s.balls + 100)
                else -(s.runs + 100 * sr.per_ball) / (s.balls + 100) end      as v2,
           case when s.discipline = 'batting'
                then (s.runs + 5 * sr.per_out) / (s.outs + 5)
                else s.outs end                                               as v3
    from stat_inputs s
    join season_rates sr using (discipline, season_year)
),

-- Centred within season, scaled by the POOLED spread across all seasons (A42's rule: a
-- season's own spread is estimated off ~50 players and dividing by it reorders on noise).
stat_season as (
    select discipline, season_year, avg(v1) as m1, avg(v2) as m2, avg(v3) as m3
    from stat_vals where not_rateable_reason is null
    group by 1, 2
),
stat_centred as (
    select v.*, v.v1 - ss.m1 as c1, v.v2 - ss.m2 as c2, v.v3 - ss.m3 as c3
    from stat_vals v join stat_season ss using (discipline, season_year)
),
stat_spread as (
    select discipline, stddev(c1) as s1, stddev(c2) as s2, stddev(c3) as s3
    from stat_centred where not_rateable_reason is null
    group by 1
),
stat_z as (
    select c.*, c.c1 / sp.s1 as z1, c.c2 / sp.s2 as z2, c.c3 / sp.s3 as z3
    from stat_centred c join stat_spread sp using (discipline)
),

-- Then WITHIN ROLE: against the role's own pooled mean and spread, so a death bowler's
-- economy is judged against death bowlers and a finisher's average against finishers.
-- The role's peers are EVERY season in that role, not only gate-passing ones: no tailender
-- has ever faced 100 balls (A39), so a gate-passing peer group for `tail` is empty, and
-- without one every bowler's batting was judged against top-order batters -- a penalty on
-- nearly every bowler that pure batters, with no bowling row, never paid. Found on the
-- dry run (bowlers' 99s fell 8 -> 2). A role still needs 20 seasons (A43).
role_level as (
    select discipline, cohort, count(*) as n,
           avg(z1) as rm1, stddev(z1) as rs1, avg(z2) as rm2, stddev(z2) as rs2,
           avg(z3) as rm3, stddev(z3) as rs3
    from stat_z
    group by 1, 2
),
stat_role as (
    select z.*,
           case when rl.n >= 20 then (z.z1 - rl.rm1) / rl.rs1 else z.z1 end as r1,
           case when rl.n >= 20 then (z.z2 - rl.rm2) / rl.rs2 else z.z2 end as r2,
           case when rl.n >= 20 then (z.z3 - rl.rm3) / rl.rs3 else z.z3 end as r3
    from stat_z z left join role_level rl using (discipline, cohort)
),

-- Weighted: batting 40% runs per match / 35% strike rate / 25% average; bowling 45%
-- wickets per match / 15% season wickets (wicket-taking 60%) / 40% economy.
stat_comp as (
    select s.*,
           case when s.discipline = 'batting' then 0.40 * s.r1 + 0.35 * s.r2 + 0.25 * s.r3
                else 0.45 * s.r1 + 0.40 * s.r2 + 0.15 * s.r3 end as comp
    from stat_role s
),

-- Expressed in RUNS PER MATCH on the ROLE's own spread of per-match impact, so the blend
-- adds like with like and everything downstream (Player of the Match, the all-rounder
-- term, the career floor, the scale) is unchanged. The role's spread, not the
-- discipline's: being the best tailender is worth what a tailender's batting can be worth
-- in a match. On the discipline's spread the dry run sent bowlers who can bat a little to
-- 99 -- Harbhajan 2010, Morris 2016, Maharoof 2008 -- because a good cameo is several
-- spreads above other tailenders and was then paid as if it were an opener's season.
-- A role thinner than 20 seasons takes the discipline's spread, as its z did (A43).
stat_scale as (
    select discipline, stddev(comp) as comp_sd
    from stat_comp where not_rateable_reason is null
    group by discipline
),
impact_spread as (
    select discipline, cohort, count(*) as n,
           stddev(normalised_per_ball * balls::float8 / matches) as sd
    from normalised
    group by 1, 2
),
discipline_spread as (
    select discipline, stddev(normalised_per_ball * balls::float8 / matches) as sd
    from normalised where not_rateable_reason is null
    group by 1
),
-- And in proportion to how much of the job was done: a full share is A59's reference
-- exposure (18 balls a match batting, 24 bowling). Without it a batter who bowled one
-- expensive over was judged "per match" against full-time bowlers' wicket rates and lost
-- up to nine points on the dry run (Dube 2022, Venkatesh Iyer 2024) for an over nobody
-- remembers.
stat_pm as (
    select c.franchise_season_id, c.person_id, c.discipline,
           c.comp / sc.comp_sd
             * case when isp.n >= 20 then isp.sd else ds.sd end
             * least(c.balls / c.season_matches
                     / case when c.discipline = 'batting' then 18.0 else 24.0 end,
                     1.0) as stat_per_match
    from stat_comp c
    join stat_scale sc using (discipline)
    join discipline_spread ds using (discipline)
    left join impact_spread isp using (discipline, cohort)
),

per_match as (
    select
        n.*,
        -- [A165] Per match PLAYED, not per match in this discipline: a batter who bowled
        -- two overs in one of his fourteen matches bowled 0.9 balls a match, not 12.
        n.normalised_per_ball * n.balls::numeric
            / max(n.matches) over (partition by n.franchise_season_id, n.person_id)
                                                             as impact_per_match,
        n.balls::numeric
            / max(n.matches) over (partition by n.franchise_season_id, n.person_id)
                                                             as balls_per_match,
        coalesce(sp.stat_per_match, 0) as stat_per_match
    from normalised n
    left join stat_pm sp using (franchise_season_id, person_id, discipline)
),

player_season as (
    select
        franchise_season_id,
        person_id,
        max(matches)          as matches,
        -- Every discipline, so `rated_per_ball` still integrates to the card (check 22)...
        sum(impact_per_match) as impact_per_match,
        -- ...but only the disciplines ON THE CARD are judged (A165).
        sum(impact_per_match) filter (where on_card) as card_impact_per_match,
        sum(stat_per_match)   as stat_per_match,
        sum(balls_per_match)  as balls_per_match,
        coalesce(max(balls_per_match) filter (where discipline = 'batting'), 0) as bat_bpm,
        coalesce(max(balls_per_match) filter (where discipline = 'bowling' and on_card), 0)
                                                                              as bowl_bpm
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
        -- [A163] 70% the season's statistics against its season and role, 30% its impact.
        0.7 * ps.stat_per_match + 0.3 * coalesce(ps.card_impact_per_match, 0)
          + 12.0 * coalesce(p.potm_awards, 0) / ps.matches
          + 2.5 * least(ps.bat_bpm / 18.0, 1.0) * least(ps.bowl_bpm / 24.0, 1.0)  -- [A163] was 5.0 (A59)
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
            -- [A161] A159's 0.5/0.5 floor, CAPPED AT THE CAREER. Below the career it is
            -- A159's floor exactly; above it, it is the career, which the evidence blend
            -- on the line above already exceeds -- so the floor can only ever lift a season
            -- toward the career, never past it toward one hot game. Convex (A69).
            least(0.5 * c.career_merit + 0.5 * c.merit, c.career_merit)
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
    'SPEC 7.8-7.12 rating. EVERY squad member is rated (A65 rates every player-season that faced or bowled a ball; A71 covers the 4 of 3,337 who did neither, from reputation alone or the scale floor if they have none). Runs above par per match (A54), disciplines added (A55), Player of the Match priced in (A56), a continuous all-rounder term (A59), shrunk on MATCHES for the per-match quantity (A66), with the career acting as a FLOOR rather than a blend (A67/A69, convex). [A163/A164] Merit is 70% season-relative statistics (counted over every match, rain-shortened included, A164) compared within role (batting: runs per match 40%, strike rate 35%, average 25%; bowling: wickets per match 45%, season wickets 15%, economy 40%) and 30% per-match impact. Integer 70-99, anchored at the 2nd and 99.2ND percentiles of blended merit (A74 lowered the high anchor from the 99.8th to the 99th, A118 from the 99th to the 99.2nd -- see each file header). k = 100 balls, K_MATCHES = 6, POTM_RUNS = 12, ALLROUNDER_RUNS = 2.5 (A163; 5 until then), CAREER_FLOOR = 0.5, capped at the career itself (convex, A69; 0.85/0.15 until A159, uncapped until A161), CAREER_N = 2 and the anchors live here and nowhere else. Season means and cohort offsets are estimated on gate-passing seasons and applied to all (A68). [A160] `scoring_per_ball` and `dismissal_multiplier` are what the ENGINE plays: the two halves of a season, season-centred and NOT cohort-adjusted, with no Player-of-the-Match, all-rounder or reputation term -- the engine replays the season as it happened, and `rated_per_ball`/`display_rating` are the card''s. `not_rateable_reason = ''no_evidence''` and `prior_source = ''reputation_floor''` mark the A71 branch.';
