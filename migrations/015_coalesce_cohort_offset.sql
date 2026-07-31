-- SPEC 7.11. A latent NULL in the cohort join, exposed by A68.
--
-- A VIEW REPLACEMENT AND NOTHING ELSE.
--
-- ---------------------------------------------------------------------------------------
-- [A70] Migration 010 SAID the cohort offset was coalesced to zero and the SQL never did
-- it. The comment read:
--
--     "LEFT, and the offset coalesced to zero, so an unclassified cohort arrives
--      uncorrected rather than vanishing. An inner join here would silently drop the row,
--      which is the A37 failure exactly: a rating that disappears leaves no symptom."
--
-- The reasoning was right and the code was `c.centred_per_ball - cl.cohort_offset`, which
-- yields NULL rather than the uncorrected value. It never fired because every cohort had
-- rows to match, so the LEFT join always found one -- for four migrations the guard was
-- decorative and nobody could tell.
--
-- A68 broke it within minutes: estimating offsets on gate-passing seasons only leaves
-- `tail` with none (A39 -- no tail batter clears the 100-ball floor in nineteen years), so
-- every tail card came back with a NULL rating and the draft crashed on `max()` of an
-- empty sequence.
--
-- The symptom was loud, which is luck rather than design. A NULL that reached the display
-- would have shown a blank rating on a card and nothing else. **A comment asserting
-- behaviour the code does not have is worse than no comment**: it is a claim a reader will
-- trust, and it survived three migrations that each copied the block forward verbatim.

-- ---------------------------------------------------------------------------------------
-- [A68] A33's gate keeps its job as an EVIDENCE rule for estimating parameters, and loses
-- only its power to remove a card.
--
-- 013 removed the gate everywhere at once, and check 13 failed immediately: batting's
-- `finisher` offset drifted 0.090 runs/ball across eras against a 3-SE bar of 0.054, and
-- `middle` and `opener` went with it. The spec's own instruction for that failure is to
-- split the offset by era.
--
-- It was checked before it was obeyed, and the instruction would have been wrong here.
-- Recomputing the same drift on the THICK seasons alone: nothing clears 3 SE, the worst
-- ratio falling from 1.65 to 0.90. So the drift is not an era effect at all -- it is thin
-- seasons, whose `batting_band` is derived from a handful of innings and which are not
-- spread evenly across eras now that the Impact Player rule has teams using more players.
-- Splitting by era would have fitted noise, which is precisely what A2 refused twice and
-- what A43's threshold exists to prevent.
--
-- So the season means and cohort offsets are estimated from seasons that clear A33's
-- floors, and then APPLIED to everyone. One rule, stated once: a parameter is measured
-- where the evidence supports it, and a rating is produced for every player who played.
-- Check 13 goes back to passing on its own terms rather than on a widened bar.

-- ---------------------------------------------------------------------------------------
-- [A69] The reputation floor must be CONVEX, or it stops being shrinkage.
--
-- 013 wrote the floor as `0.85 * career`, which is sign-blind: for a below-average player
-- the career merit is negative, so 85% of it is LARGER, and the floor silently lifted
-- every poor player toward the league. Check 9 caught it on the row it moved -- A Mithun
-- 2009 blended to -0.591 from a merit of -0.695 and a career of -0.721, outside both.
--
-- The floor is now `0.85 * career + 0.15 * merit`: still a floor, still lift-only against
-- the evidence blend, but a genuine convex combination, so a rating can never leave the
-- interval its own two inputs span. That is what makes the career term shrinkage rather
-- than a bonus, and check 9 pins it.

-- ---------------------------------------------------------------------------------------
-- [A65] The A33 gate is REMOVED from the rating. It was never a statement about the player,
-- only about the estimate, and the game turned it into one.
--
-- A33's floors are still right about what they measured: below 100 balls faced or 150
-- bowled, a per-ball figure is noise. What was wrong was the CONSEQUENCE. A gated
-- player-season had no row in the view, so it was not draftable at all -- and a squad is
-- about twenty men of whom roughly half are gated, so half of every squad simply did not
-- exist. RCB 2025 won the title and offered nine of nineteen men with no card.
--
-- The fix is not to lower the floor (A33 measured it and it is where the raw lists stop
-- moving). It is to stop treating "we cannot estimate this precisely" as "this player
-- cannot be picked". A thin season now gets a rating dominated by its prior, which is the
-- honest answer to a thin season and is what shrinkage is for.
--
-- The gate survives where it belongs: `player_season_impact.not_rateable_reason` still
-- records it, so the reason a season is thin is still auditable, and the loader still
-- refuses to disagree with itself about the arithmetic.

-- ---------------------------------------------------------------------------------------
-- [A66] Shrinkage counts BALLS for a per-ball figure and MATCHES for a per-match one.
--
-- Removing the gate alone put Mike Hussey's THREE-MATCH 2008 at 99, top of the all-time
-- list, and Harshal Patel's ONE-MATCH 2017 at 97. That is exactly the failure A33 was
-- built to prevent, and it happened even though the per-ball value was already shrunk at
-- k = 100 balls -- because A54 then multiplies by balls per match, and balls per match is
-- itself estimated from the same tiny sample. The shrinkage was undone by the very step
-- that made the number a card.
--
-- So the per-match quantity gets its own shrinkage in its own unit:
--
--     w = matches / (matches + 6)
--
-- Three matches counts a third; fourteen counts seven tenths. Hussey 2008 goes 99 -> 87
-- and no season under six matches survives in the top fifteen, while a full season is
-- barely touched. The constant is in matches because the quantity is per match; using
-- balls here was the whole mistake.

-- ---------------------------------------------------------------------------------------
-- [A67] Reputation becomes a FLOOR, not a blend, and that fixes a drag nobody asked for.
--
-- A57 blended every season 55/45 toward the player's career. It was meant to stop a great
-- player's bad season reading as broken, and it did -- but a symmetric blend also drags a
-- GREAT season down toward the man's average years. Measured: the blend retained only
-- **59.5%** of the spread in merit, and Kohli's 2025 fell from a merit of 4.03 to 3.25 for
-- no reason except that he has also had lean years.
--
-- A floor does the job the blend was brought in for without the cost:
--
--     blended = greatest(evidence_blend, 0.85 * career)
--
-- A career can lift a poor or thin season and can never pull a good one down. Bumrah's
-- range stays 80-96 across thirteen seasons, and his 2026 -- four wickets in forty-nine
-- overs -- still reads 80 rather than 37, which was the whole point of A57.
--
-- Still a declared game-design term rather than a measurement, exactly as A57 was.

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
        -- [A35/A46] k = 100 BALLS, for a per-ball figure. A66 adds the per-match stage.
        (i.impact_total + 100 * i.prior_per_ball) / (i.balls + 100)
                                                            as shrunk_per_ball
    from player_season_impact i
    join franchise_seasons f using (franchise_season_id)
    join squad_members     s using (franchise_season_id, person_id)
    -- [A65] The gate is gone. `balls > 0` is not a floor, it is arithmetic: a man who
    -- never faced or bowled a ball has no per-ball figure to shrink and no balls-per-match
    -- to multiply by. Four squad members of 3,337 are in that position (A27).
    where i.balls > 0
),

-- [A68] Estimated on the seasons that clear A33's floors, applied to all of them.
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

-- [A68] Likewise: a thin season's band comes from a handful of innings, so it may not
-- vote on where its band sits. Measured on the thick, applied to all.
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
        -- [A70] The coalesce 010's comment always promised. `tail` has no gate-passing
        -- season to estimate an offset from (A39), so it arrives uncorrected rather than
        -- as a NULL that empties the card.
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
            -- [A66] Evidence, counted in matches because the quantity is per match.
            (c.matches::numeric / (c.matches + 6)) * c.merit
              + (6 / (c.matches + 6.0)) * c.career_merit,
            -- [A67/A69] Reputation as a floor, and CONVEX so it stays shrinkage: it may
            -- lift a thin or poor season toward the man's career and may never carry a
            -- rating outside the interval its own two inputs span.
            0.85 * c.career_merit + 0.15 * c.merit
        ) as blended_merit
    from career c
),

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
cross join scale s;

comment on view player_season_rating is
    'SPEC 7.8-7.11 rating. EVERY player-season that faced or bowled a ball is rated (A65 - the A33 gate no longer removes a card, only records that the estimate is thin). Runs above par per match (A54), disciplines added (A55), Player of the Match priced in (A56), a continuous all-rounder term (A59), shrunk on MATCHES for the per-match quantity (A66), with the career acting as a FLOOR rather than a blend (A67 - it may lift a season and may never drag one). Integer 70-99, percentile-anchored (A58/A60). k = 100 balls, K_MATCHES = 6, POTM_RUNS = 12, ALLROUNDER_RUNS = 5, CAREER_FLOOR = 0.85 (convex, A69) and the anchors live here and nowhere else. Season means and cohort offsets are estimated on gate-passing seasons and applied to all (A68).';
