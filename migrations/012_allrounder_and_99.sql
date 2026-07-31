-- SPEC 7.9. Versatility is worth runs, and the scale tops out at 99.
--
-- A VIEW REPLACEMENT AND NOTHING ELSE, as 010 and 011 were.

-- ---------------------------------------------------------------------------------------
-- [A59] An all-rounder is worth more than the sum of his two impacts, and the reason is
-- the DRAFT rather than the cricket.
--
-- A55 made batting and bowling add, which was the missing half. It is still not the whole
-- of what a dual contributor is worth, because the template is what a drafter is actually
-- solving: 15 cards into `keeper 1 / opener 2 / top_order 2 / middle_or_finisher 3 /
-- bowler 5 / open 2`. A card that fills a batting band AND the bowler slot relieves two
-- constraints with one pick. Narine 2024 can be slotted at `opener`, `bowler` or `open`;
-- a pure opener of the same merit can only ever be one of those. That flexibility has real
-- value to a drafter and none of it appears in a sum of per-ball impacts.
--
-- Priced at ALLROUNDER_RUNS = 5.0 runs per match at a FULL double share, scaled
-- continuously by how much of each job the player actually did:
--
--     share = least(bat_balls_per_match / 18, 1) * least(bowl_balls_per_match / 24, 1)
--
-- CONTINUOUS, not a threshold, and that is the point. A cut at "3 overs bowled and batting
-- enough" qualified 38 of the 123 player-seasons rated in both disciplines and put a cliff
-- through a continuum: Watson 2015 missed by 0.6 balls a match while Watson 2011 cleared
-- it. A33's floors and A43's cohort threshold are defensible because each sits in a wide
-- empty gap in the evidence; this quantity has no gap, so a threshold here would be an
-- arbitrary line rather than a rule about evidence.
--
-- The references are the two full shares: 18 balls faced is three overs and is also the
-- league's mean batting exposure (18.7), and 24 balls bowled is four overs, which is the
-- hard maximum a bowler may bowl. So a player doing both jobs completely scores 1.0 and a
-- specialist scores 0.
--
-- This is a declared game-design term, exactly as A57 is, and it is written down as one.

-- ---------------------------------------------------------------------------------------
-- [A60] The scale tops out at 99, not 100.
--
-- 100 reads as a perfect card, and nothing in nineteen seasons is perfect. Purely a
-- display decision: the map stays linear on blended merit and only the upper anchor moves,
-- so every gap survives in proportion exactly as A58 requires. Four seasons reach 99.
--
-- A54's per-match rebasing also has a structural consequence worth recording here, because
-- it is a property of the denominator rather than a defect to correct: a bowler may bowl at
-- most 24 balls in a match, while a top-order batter faces up to 43.6. Bowling exposure is
-- therefore capped at about 55% of batting's ceiling, which is true of the game itself and
-- is not adjusted for. A59 partly offsets it for players who do both.

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
        -- [A35/A46] k = 100, ratified, and still the only place it appears.
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

-- A2/A43. Pooled across seasons, zero below 20 observations.
cohort_level as (
    select
        discipline,
        cohort,
        count(*) as cohort_n,
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
    left join cohort_level cl using (discipline, cohort)
),

-- [A54] Runs above par per match, per discipline.
per_match as (
    select
        n.*,
        n.normalised_per_ball * n.balls::numeric / n.matches as impact_per_match,
        n.balls::numeric / n.matches                         as balls_per_match
    from normalised n
),

-- [A55/A59] The player-season grain: disciplines added, and each one's exposure kept so
-- A59 can see how much of both jobs the player actually did.
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

-- [A56] Awards attributed to the squad the player appeared for in that match.
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

-- [A57] Leave-one-season-out, career estimate itself shrunk toward the league.
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
    select c.*, 0.55 * c.merit + 0.45 * c.career_merit as blended_merit
    from career c
),

-- [A58] Percentile anchors, not extremes.
scale as (
    select
        percentile_cont(0.02)  within group (order by blended_merit) as lo,
        percentile_cont(0.998) within group (order by blended_merit) as hi
    from blended
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
    n.normalised_per_ball,

    b.potm_awards,
    b.allrounder_share,
    b.impact_per_match  as season_impact_per_match,
    b.merit,
    b.career_merit,
    b.blended_merit,

    -- [A57] What the ENGINE reads, carrying the same blend as the card so the scoreboard
    -- cannot disagree with the rating. Check 22 enforces the identity.
    n.normalised_per_ball
        + (b.blended_merit - b.impact_per_match) / b.balls_per_match
                                                            as rated_per_ball,

    -- [A58/A60] 70-99, linear on the blended merit, integer.
    round(
        70.0 + 29.0 * greatest(0.0, least(1.0,
            (b.blended_merit - s.lo) / nullif(s.hi - s.lo, 0)))
    )::int                                                  as display_rating,

    n.prior_per_ball,
    n.prior_source
from per_match n
join blended b using (franchise_season_id, person_id)
cross join scale s;

comment on view player_season_rating is
    'SPEC 7.8-7.9 rating. Runs above par per MATCH (A54), batting and bowling added (A55), Player of the Match priced in (A56), a continuous all-rounder term for filling two slots (A59), blended toward the player career leave-one-out (A57 - a declared game-design term, not a measurement), displayed on an integer 70-99 percentile-anchored scale (A58/A60). rated_per_ball is what the simulator consumes and carries the same blend as the card; display_rating is the UI number. k = 100, POTM_RUNS = 12, ALLROUNDER_RUNS = 5, REPUTATION = 0.45, CAREER_N = 2 and the percentile anchors live here and nowhere else.';
