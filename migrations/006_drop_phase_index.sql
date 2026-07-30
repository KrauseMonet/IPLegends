-- Drops deliveries_phase_idx, which 003 created and nothing can use.
--
-- `phase` holds three distinct values plus NULL across 295,732 rows, so any predicate
-- on it alone selects roughly a third of the table and the planner will take a
-- sequential scan every time. The index costs ~10 MB and a write on every delivery
-- inserted, to serve a query plan that will not be chosen. Queries that do filter by
-- phase also filter by match or franchise-season, and deliveries_match_innings_idx
-- already leads on that.
--
-- 003 is applied and therefore immutable, so this is a new migration rather than an
-- edit to the line that created it. That is the immutability rule working, not a
-- friction to route around: the index existed on a real database and its removal is
-- part of that database's history.

drop index if exists deliveries_phase_idx;
