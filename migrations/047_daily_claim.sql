-- A daily attempt made signed out can be claimed by whoever signs in on that browser.
--
-- A signed-out player is dealt from a seed derived from the date and a random per-browser
-- id (web/daily.py anon_key), an account from the date and its own id. A claimed attempt
-- keeps the deal it was drafted under, so the row records WHICH player key that was:
-- without it the state could never be replayed again (the match, the scorecard and the
-- share line are all re-derived from it on every read).

alter table daily_results add column dealt_as text;

alter table daily_results add constraint daily_results_dealt_as_form
    check (dealt_as is null or dealt_as ~ '^anon:[0-9a-f]{32}$');

-- One browser's attempt can be claimed by ONE account, ever. Without this a shared
-- computer would let every account that signs in on it take the same result.
create unique index daily_results_dealt_as_uq
    on daily_results (challenge_date, dealt_as) where dealt_as is not null;

comment on column daily_results.dealt_as is
    'NULL for an attempt drafted under the account''s own seed. Otherwise the anonymous player key the attempt was drafted under before the account claimed it.';
