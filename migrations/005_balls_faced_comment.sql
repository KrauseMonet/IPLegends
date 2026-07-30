-- Corrects the `legal_ball` comment from migration 003, which asserted the wrong rule.
--
-- 003 said balls faced filters on legal_ball. It does not: a no-ball IS a ball faced,
-- because the batter can and does score off it, while a wide is not. Filtering balls
-- faced on legal_ball undercounts by the number of no-balls a batter received and makes
-- every strike rate slightly too high. Found by SPEC 8 check 7 against published
-- scorecards -- Kohli's 2016 season came out at 637 balls against a published 640.
--
-- 003 is applied and therefore immutable, so the correction lands here. No column
-- changes: per A19 the right predicate is already derivable from the extras split.

comment on column deliveries.legal_ball is
    'False for wides and no-balls. OVERS BOWLED and the bowling economy denominator filter on this. BALLS FACED and batting strike rate do NOT - they filter on extra_wides = 0, because a no-ball is a ball faced. See SPEC 4.7/A22.';

comment on column deliveries.extra_noballs is
    'Charged to the bowler, and counts as a ball faced by the batter, but is not a legal ball.';

comment on column deliveries.extra_wides is
    'Charged to the bowler. Not a legal ball and not a ball faced - the only extra that is neither. Balls faced is exactly extra_wides = 0.';
