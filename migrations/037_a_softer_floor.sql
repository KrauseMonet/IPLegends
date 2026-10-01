-- SPEC 7.8-7.12, A159. A VIEW REPLACEMENT AND NOTHING ELSE, as 010-016, 018 and 029 were.
--
-- ---------------------------------------------------------------------------------------
-- [A159] The reputation floor softens from 0.85*career + 0.15*merit to 0.5*career +
-- 0.5*merit. Every other line is 029's, verbatim.
--
-- The floor was measured as the main source of ratings that contradict the scorecard.
-- Over 1,276 same-season pairs of batters where one has at least 30 more runs, a higher
-- strike rate and no more dismissals, the better batter rated LOWER in 70 (5.5%). On the
-- season's own merit, before any career term, that is 12 (0.9%): the floor accounted for
-- most of the rest, because it lifted 54% of all seasons and a good season by a player
-- with no strong career got no lift, so it read low by comparison -- Jurel 2026 (515 at
-- 155) rated 74 against Buttler 2026 (526 at 152) at 83 while the model scored Jurel's
-- season higher. At 0.5 the count is 23 (1.8%); removing the floor gives 12.
--
-- 0.5 rather than removal is a GAME-DESIGN choice, the user's, in the same category as
-- A57's REPUTATION: it keeps A67's point (a great player's poor season does not read as
-- broken) at half the strength. Bumrah's worst full season goes 83 -> 78; his median and
-- Kohli's barely move, because the anchors re-derive from the new distribution.
--
-- Still convex (A69): 0.5 + 0.5 = 1, so a rating can never leave the interval its own two
-- inputs span, which check 9 asserts. Re-run tools.snapshot_deck after applying (A107).

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
    'SPEC 7.8-7.12 rating. EVERY squad member is rated (A65 rates every player-season that faced or bowled a ball; A71 covers the 4 of 3,337 who did neither, from reputation alone or the scale floor if they have none). Runs above par per match (A54), disciplines added (A55), Player of the Match priced in (A56), a continuous all-rounder term (A59), shrunk on MATCHES for the per-match quantity (A66), with the career acting as a FLOOR rather than a blend (A67/A69, convex). Integer 70-99, anchored at the 2nd and 99.2ND percentiles of blended merit (A74 lowered the high anchor from the 99.8th to the 99th, A118 from the 99th to the 99.2nd -- see each file header). k = 100 balls, K_MATCHES = 6, POTM_RUNS = 12, ALLROUNDER_RUNS = 5, CAREER_FLOOR = 0.5 (convex, A69; was 0.85 until A159), CAREER_N = 2 and the anchors live here and nowhere else. Season means and cohort offsets are estimated on gate-passing seasons and applied to all (A68). `not_rateable_reason = ''no_evidence''` and `prior_source = ''reputation_floor''` mark the A71 branch.';
