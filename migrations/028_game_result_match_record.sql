-- 028: a saved game's own match record, not just whether it was won outright.
--
-- 027 stored `champion` alone, which answers "did you take the title" and nothing else.
-- That is enough for tournament-level counts but cannot answer the ordinary question a
-- career page is asked -- how many MATCHES have you won -- because a season is fourteen
-- league games plus playoffs and a cup is two or three, all of which collapsed into one
-- boolean.
--
-- Nothing here is newly computed. Both save paths already build the figures and then threw
-- them away: solo's `SeasonReplay.journey` (game.season.JourneyStats) and a room's
-- `room_journey` (web.room_match.RoomJourney) each carry played/won/lost/tied for the
-- seat being saved. This migration is purely about persisting what was already in hand.
--
-- NULLABLE on purpose, and the nulls are meaningful rather than lazy. Rows written before
-- this migration genuinely have no match record -- it was never captured, and a saved game
-- is not replayable in general (a room's own row can age out from under its natural_key,
-- ROOM_TTL_HOURS). Defaulting them to 0 would be the A23 mistake in a new place: an
-- unobserved value taking a plausible-looking number and then being summed as if it were
-- measured. SUM() skips nulls, so an old row contributes nothing instead of contributing a
-- confident zero.
--
-- `format` is deliberately NOT added. The two format-shaped questions the profile actually
-- asks -- tournaments won solo, tournaments won with friends -- are already answered by
-- `source`, and a room's "friend leagues won" is defined as every room title regardless of
-- whether it was a final, a cup or a league. Storing a column no query needs would be
-- storing it for a use nobody has yet.

alter table game_results add column matches_played smallint;
alter table game_results add column matches_won smallint;

comment on column game_results.matches_played is
    'Matches this seat played in that saved game -- 14 league games plus any playoffs for a
     solo season, 1-3 for a room depending on format. NULL for rows saved before migration
     028, which never captured it; never 0-as-unknown.';
comment on column game_results.matches_won is
    'Matches this seat won in that saved game. NULL for pre-028 rows (see matches_played).
     Distinct from `champion`, which is about the tournament rather than its matches.';
