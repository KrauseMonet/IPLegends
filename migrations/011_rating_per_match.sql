-- SPEC 7.8. The rating becomes runs per MATCH, disciplines add, and a career term blends in.
--
-- A VIEW REPLACEMENT AND NOTHING ELSE, exactly as 010 was. No table, no column, no stored
-- number. Everything below is a derived parameter and lives here so re-tuning stays a
-- migration rather than a silent edit to a stored value (A35).

-- ---------------------------------------------------------------------------------------
-- [A54] Per BALL was the wrong denominator for a card, and four measurements say so.
--
-- 010 shipped a per-ball rating. It is the right quantity for the engine, which tilts one
-- delivery at a time, and the wrong one for a drafter, who is buying a player for a match.
-- The gap is not cosmetic:
--
--   * Kohli 2016 (973 runs, the aggregate record) rated 75.4 while Russell 2019 rated
--     100.0. Per match they are +11.35 and +12.13 runs above par - an 11% difference
--     displayed as 25 points, because Kohli's value is spread over 36.9 balls a game and
--     Russell's over 19.2.
--   * Warner 2016 outrated Tanvir 2008 on impact per match (+12.61 to +9.46) and lost on
--     the card (81.5 to 84.7). The card had the better season second.
--   * Players who won four to six Player-of-the-Match awards in a season - Marsh 2008,
--     Hussey 2013, Tendulkar 2010, Rohit 2016 - sat in the 60s.
--   * Finishers held 4% of rated seasons and 20% of the top twenty, because a death-overs
--     ball carries 3.93 runs of variance against a powerplay ball's 2.12 and they face
--     roughly half as many. Per-ball rating reads that extra noise as extra merit.
--
-- Multiplying by balls per match fixes all four at once and needs no new correction: the
-- finisher tail collapses because fewer balls now means less, not more.

-- ---------------------------------------------------------------------------------------
-- [A55] Batting and bowling ADD. They are already the same unit.
--
-- 010 gave an all-rounder two independent rows and every consumer took the better one, so
-- Narine 2024 - 488 runs at a strike rate of 181 AND a 69.7 bowling season - was carded at
-- 69.7 and his batting was discarded. Both impacts are measured in runs above par, so they
-- need no weighting against each other, only addition. Nothing here decides how much a
-- wicket is worth relative to a run; A31 already did, from the archive.

-- ---------------------------------------------------------------------------------------
-- [A56] Player of the Match enters the rating, because it is evidence and we already had it.
--
-- Cricsheet carries `player_of_match` on 1,234 of 1,243 matches and migration 002 has
-- stored it since the first load. It is the one HUMAN judgement in the whole pipeline -
-- somebody who watched the game naming who decided it - and the ratings ignored it for
-- nineteen seasons of data.
--
-- Priced at POTM_RUNS runs per award per match played, which is a conversion and therefore
-- a choice; it is written once, here. It is deliberately not large enough to carry a
-- season on its own: at 12 runs an award, Marsh's five-award 2008 gains 5.5 runs a match
-- against a merit of 15.3, so the award is a third of his case and his batting is the rest.

-- ---------------------------------------------------------------------------------------
-- [A57] A REPUTATION term, and it is a game-design decision rather than a measurement.
--
-- This is the one parameter in the project that is not derived from evidence about the
-- thing it scores, and it is recorded plainly so that nobody later mistakes it for one.
-- The brief is a game that people want to play, and a card that rates Bumrah 37 for one
-- poor season reads as broken to a player who knows he is Bumrah - however well earned
-- that 37 is by 4 wickets in 49 overs at 8.37 an over.
--
-- Mechanically it is A7 reinstated as a SECOND shrinkage stage: each season is blended
-- toward the player's other seasons, leave-one-out. A46 removed A7 from the FIRST stage
-- and that removal stands - the band prior is still what `player_season_impact` shrinks
-- toward, and it is still the right answer for estimating a season. This is a different
-- claim, applied after the estimate is complete and declared as such: a card is "Bumrah,
-- as of 2026", not "Bumrah in 2026".
--
-- Two guards, both load-bearing:
--
--   * The career estimate is itself shrunk toward the league by CAREER_N pseudo-seasons.
--     Without it a one-season player is blended toward himself, i.e. not blended at all,
--     and Tanvir 2008 - a genuinely fine season, but ELEVEN matches with no career to
--     appeal to - ranked third of 1,689 ahead of every Kohli and Gayle season.
--   * The blend feeds `rated_per_ball` too, not only the display. If reputation lifted the
--     card without lifting what the engine plays, the match would contradict the rating
--     within one over and the drafter would be right not to trust either. The card and the
--     scoreboard have to be the same claim.

-- ---------------------------------------------------------------------------------------
-- [A58] The display scale is 70-100, INTEGER, anchored on percentiles rather than extremes.
--
-- A44's 0-100 with a mean of 50 is a statistician's scale. A game scale puts the floor
-- where a weak card is still a card and lets the field sit high, which is what every sports
-- game does, and it costs nothing in honesty because the map is linear - every gap survives
-- in proportion, which is the whole of A44's argument against percentile.
--
-- Anchored at the 2nd and 99.8th percentiles of the blended merit, NOT at min and max. Two
-- seasons (Gayle 2011 and 2012) sit far clear of the field, and anchoring on them compressed
-- 55% of all seasons into a five-point band. On percentiles the distribution spreads
-- 14/38/27/15/5/2 across the six bands and four seasons reach 100.
--
-- Rounded to an integer, which was asked for and which makes ties common - roughly 56
-- seasons per point. That is accepted: a drafter sees fifteen cards at a time, so ties
-- rarely appear side by side, and the ordering underneath is intact in `rated_per_ball`.

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

-- A42. Unweighted: avg() over player-seasons, never over balls. Unchanged by A54.
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

-- A2/A43. Pooled across seasons, zero below 20 observations. Unchanged by A54.
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

-- [A54] Runs above par per match, per discipline. `matches` is identical across a
-- player-season's two discipline rows by construction (009), so max() is a pick, not an
-- aggregate over differing values.
per_match as (
    select
        n.*,
        n.normalised_per_ball * n.balls::numeric / n.matches as impact_per_match,
        n.balls::numeric / n.matches                         as balls_per_match
    from normalised n
),

-- [A55] The player-season grain: disciplines added, not compared.
player_season as (
    select
        franchise_season_id,
        person_id,
        max(matches)          as matches,
        sum(impact_per_match) as impact_per_match,
        sum(balls_per_match)  as balls_per_match
    from per_match
    group by franchise_season_id, person_id
),

-- [A56] Awards are attributed to the squad the player appeared for in that match, so a
-- mid-season transfer credits the right franchise-season.
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
        ps.impact_per_match
          + 12.0 * coalesce(p.potm_awards, 0) / ps.matches as merit
    from player_season ps
    left join potm p using (franchise_season_id, person_id)
),

league as (select avg(merit) as league_merit from merit),

-- [A57] Leave-one-season-out, with the career estimate itself shrunk toward the league by
-- CAREER_N = 2 pseudo-seasons so a single-season player is not blended toward himself.
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
    b.impact_per_match  as season_impact_per_match,
    b.merit,
    b.career_merit,
    b.blended_merit,

    -- [A57] What the ENGINE reads. The blend is spread back over the player-season's balls
    -- and added to every discipline, so the per-match total of `rated_per_ball` is exactly
    -- `blended_merit` and the scoreboard cannot disagree with the card.
    n.normalised_per_ball
        + (b.blended_merit - b.impact_per_match) / b.balls_per_match
                                                            as rated_per_ball,

    -- [A58] 70-100, linear on the blended merit, integer.
    round(
        70.0 + 30.0 * greatest(0.0, least(1.0,
            (b.blended_merit - s.lo) / nullif(s.hi - s.lo, 0)))
    )::int                                                  as display_rating,

    n.prior_per_ball,
    n.prior_source
from per_match n
join blended b using (franchise_season_id, person_id)
cross join scale s;

comment on view player_season_rating is
    'SPEC 7.8 rating. Runs above par per MATCH (A54), batting and bowling added (A55), Player of the Match priced in (A56), blended toward the player career leave-one-out (A57 - a declared game-design term, not a measurement), displayed on an integer 70-100 percentile-anchored scale (A58). rated_per_ball is what the simulator consumes and it carries the same blend as the card; display_rating is the UI number. k = 100, POTM_RUNS = 12, REPUTATION = 0.45, CAREER_N = 2 and the 2nd/99.8th percentile anchors live here and nowhere else.';
