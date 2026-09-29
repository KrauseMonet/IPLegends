-- An auction room's status while it is bidding: 'auctioning' [A139].
--
-- Migration 033 added auction rooms and did NOT widen this constraint, so the first live
-- start was refused by the database. Found by starting a real room, not by the tests: the
-- test suite runs rooms against a fake connection that does not enforce CHECK constraints,
-- which is exactly the gap CLAUDE.md's standing rule names -- when a rule and the schema
-- that serves it are decided together, check the schema carries every value the rule uses
-- BEFORE applying the migration. 033 is applied and immutable, so the fix is this one.
-- tests/test_room_schema.py now reads the migrations and fails if the room code assigns a
-- status this constraint does not allow.

alter table rooms drop constraint rooms_status_check;

alter table rooms add constraint rooms_status_check
    check (status in ('lobby', 'drafting', 'auctioning', 'complete', 'failed'));
