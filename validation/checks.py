"""SPEC 8 checks 1-6.

Check 1 is the only one here that reads the archive. That is the point of it: every
other check in this file compares the database against itself, so it can only catch
an inconsistency, never a misreading. Check 1 and check 15 are the two that can tell
us we read the source wrong.

Check 2 is not the check SPEC 8 lists. The listed one - every person in `deliveries`
and `appearances` exists in `people` - is enforced by five foreign keys and cannot
fail, so it is replaced here by the two things the schema does NOT enforce: that a
delivery's participants are recorded as appearing in that match, and that every
franchise-season reference on a match's rows is one of that match's own two teams.

Checks 5 and 6 target tables that SPEC 6 and 7 build. They skip, loudly.
"""

from __future__ import annotations

import json
import zipfile
from collections import Counter

from etl.db import data_dir
from etl.feasibility import load_deck, order_errors, simulate
# Imported rather than restated. The fitting filter and the bucket labels live in one
# place, so check 20 cannot drift into validating the state model against a copy of the
# rule the state model no longer uses. A19's habit applied to code instead of columns.
from etl.derive_people import HOME_NATION, OVERSEAS_PER_XI
from etl.impact import SCORING_SET
from etl.state_model import BUCKETS, FITTING_SET
from validation.harness import Result, bad, skipped, verdict

WICKET_BUCKETS = tuple(label for label, _ in BUCKETS)

ARCHIVE = data_dir() / "ipl_json.zip"

# Overs the source itself flags as miscounted, from CLAUDE.md. Check 3 asserts the
# database reproduces exactly this set - the whitelist is only trustworthy if the
# thing it exempts is the thing we think it is.
MISCOUNTED = {
    ("1136564", 2, 11), ("335987", 1, 7), ("335994", 2, 10), ("336015", 2, 14),
    ("392198", 2, 10), ("419155", 1, 18), ("501202", 1, 5), ("501255", 2, 9),
}

# Published season aggregates, entered by hand from the public record - the only
# external reference in the suite. (runs, balls faced). The balls figures are what
# caught the balls-faced bug: the legal-ball-only predicate gives 637/558/405.
PUBLISHED_SEASONS = (
    ("V Kohli", 2016, 973, 640),
    ("DA Warner", 2016, 848, 560),
    ("AB de Villiers", 2016, 687, 407),
)

# Tables that exist as of migration 004. Check 6 needs a derived stat table to
# examine, and this is how it tells whether one has appeared yet.
BASE_TABLES = {
    "appearances", "deliveries", "franchise_seasons", "franchises", "matches",
    "people", "schema_migrations", "squad_members",
}

# Derived stat tables check 6 knows how to examine. A table appearing in neither this
# set nor BASE_TABLES still fails check 6, which is the trap working rather than a gap.
STATE_TABLES = {
    "state_ball_outcomes", "state_runs_remaining",
    "player_season_impact", "player_season_rating",
    "person_batting_positions", "person_season_batting_positions",
}

# [Migration 019] Live multiplayer room state -- not a category check 6 can even ask
# about. "Super over deliveries excluded from derived stat tables" presumes a table built
# FROM `deliveries`; a room holds seat assignments and move lists, nothing aggregated from
# a ball bowled. Excluded from `derived` entirely (same treatment as BASE_TABLES) rather
# than added to STATE_TABLES, which would misstate that check 6 examines them.
#
# [Migrations 026/027, classified 2026-08-16] The accounts feature is the same category and
# was NOT classified when it landed, so check 6 failed on all three tables -- the trap
# working, several commits after the fact, and CLAUDE.md's "21 pass, 0 fail" was stale for
# that whole stretch. `accounts` is an identity; `game_results`/`game_result_players` hold
# what a PLAYER's own simulated game produced, which comes out of the match engine and
# never out of `deliveries`. No archive delivery, super over or otherwise, can reach them.
#
# Excluding is right HERE and writing an assertion was right for `player_season_impact`,
# and the difference is not a matter of convenience: check 6 asks "did super over
# deliveries leak into this aggregate", which is a real question of a table built FROM the
# archive and a meaningless one of a table that never reads it. Answering a meaningless
# question would be the vacuous check this project has already had to delete twice.
OPERATIONAL_TABLES = {"rooms", "room_players",
                      "accounts", "game_results", "game_result_players"}

# Who the bowler gets a wicket for. Check 8 asserts these two sets between them account
# for every kind in the archive, so a kind added later cannot fall silently between them.
# `retired hurt` is not a dismissal at all - the batter may return - but it carries a
# player_out_id, so it has to be named somewhere or it reads as an unclassified kind.
BOWLER_CREDITED = ("caught", "bowled", "lbw", "caught and bowled", "stumped",
                   "hit wicket")
NOT_BOWLER_CREDITED = ("run out", "retired hurt", "retired out",
                       "obstructing the field")

# The one external anchor on the career leaderboards. Deliberately an ordering and not a
# total: the margin over second place is more than 2,000 runs, so no revision to a recent
# season can reorder it, whereas any exact figure would go stale the next time Cricsheet
# revises 2026. A22's lesson is that the claim has to be the thing actually verified.
LEADING_RUN_SCORER = "V Kohli"

TOP_RUN_SCORERS = """
    select p.primary_name, sum(d.runs_batter) as runs,
           count(*) filter (where d.extra_wides = 0) as balls
    from deliveries d join people p on p.person_id = d.batter_id
    where not d.is_super_over
    group by p.person_id, p.primary_name
    order by runs desc, p.primary_name
    limit %s
"""

TOP_WICKET_TAKERS = """
    select p.primary_name, count(*) as wickets
    from deliveries d join people p on p.person_id = d.bowler_id
    where not d.is_super_over and d.wicket_kind is not null
      and d.wicket_kind <> all(%s)
    group by p.person_id, p.primary_name
    order by wickets desc, p.primary_name
    limit %s
"""


def _rows(conn, sql: str, params=None) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _tables(conn) -> set[str]:
    return {t for (t,) in _rows(
        conn,
        "select table_name from information_schema.tables where table_schema = 'public'",
    )}


def _table_exists(conn, name: str) -> bool:
    return name in _tables(conn)


def check_01_runs_total(conn) -> Result:
    """Every delivery's runs_batter + runs_extras equals the source runs.total."""
    title = "delivery runs reproduce the source runs.total"
    if not ARCHIVE.exists():
        return skipped(1, title, f"{ARCHIVE} absent; run: uv run python -m etl.download")

    loaded = {m for (m,) in _rows(conn, "select match_id from matches")}
    if not loaded:
        return skipped(1, title, "no matches loaded")

    # Keyed on the natural key the schema declares unique, rebuilt from the archive
    # independently of the parser. A key that fails to line up is itself a finding.
    source: dict[tuple, int] = {}
    with zipfile.ZipFile(ARCHIVE) as zf:
        for name in zf.namelist():
            if not name.endswith(".json"):
                continue
            match_id = name[:-5]
            if match_id not in loaded:
                continue
            raw = json.loads(zf.read(name))
            for innings_no, innings in enumerate(raw.get("innings", []), start=1):
                is_super = bool(innings.get("super_over"))
                for over in innings.get("overs", []):
                    for ball_no, d in enumerate(over["deliveries"], start=1):
                        key = (match_id, innings_no, is_super, over["over"], ball_no)
                        source[key] = d["runs"]["total"]

    offenders: list[str] = []
    compared = 0
    with conn.cursor(name="check_01") as cur:
        cur.itersize = 20_000
        cur.execute(
            """
            select match_id, innings_no, is_super_over, over_no, ball_no,
                   runs_batter, runs_extras
            from deliveries
            """
        )
        for match_id, innings_no, is_super, over_no, ball_no, batter, extras in cur:
            compared += 1
            key = (match_id, innings_no, is_super, over_no, ball_no)
            expected = source.pop(key, None)
            where = f"{match_id} inn{innings_no} ov{over_no} ball{ball_no}"
            if expected is None:
                offenders.append(f"{where}: not present in the source")
            elif batter + extras != expected:
                offenders.append(
                    f"{where}: db {batter}+{extras}={batter + extras} "
                    f"vs source {expected}"
                )

    for match_id, innings_no, _, over_no, ball_no in sorted(source):
        offenders.append(
            f"{match_id} inn{innings_no} ov{over_no} ball{ball_no}: in the source, "
            f"not loaded"
        )

    return verdict(
        1, title, f"{compared} deliveries compared against the archive", offenders
    )


def check_02_participants_are_recorded(conn) -> Result:
    """Replaces the listed check 2, which five foreign keys already enforce."""
    title = "delivery participants and team references are consistent"
    offenders: list[str] = []

    missing = _rows(
        conn,
        """
        select distinct d.match_id, x.person_id
        from deliveries d,
             unnest(array[d.batter_id, d.bowler_id, d.non_striker_id, d.player_out_id])
                 as x(person_id)
        where x.person_id is not null
          and not exists (
              select 1 from appearances a
              where a.match_id = d.match_id and a.person_id = x.person_id
          )
        order by 1, 2
        """,
    )
    offenders += [
        f"{match_id}: {person_id} took part but has no appearances row"
        for match_id, person_id in missing
    ]

    # `is distinct from` rather than `not in`, because team_a_fs_id is nullable and a
    # null would make `not in` return null, which reads as a pass.
    stray = _rows(
        conn,
        """
        select d.match_id, count(*)
        from deliveries d join matches m using (match_id)
        where (d.batting_fs_id is distinct from m.team_a_fs_id
               and d.batting_fs_id is distinct from m.team_b_fs_id)
           or (d.bowling_fs_id is distinct from m.team_a_fs_id
               and d.bowling_fs_id is distinct from m.team_b_fs_id)
           or d.batting_fs_id = d.bowling_fs_id
        group by 1 order by 1
        """,
    )
    offenders += [
        f"{match_id}: {n} deliveries reference a franchise-season not playing this match"
        for match_id, n in stray
    ]

    stray_appearances = _rows(
        conn,
        """
        select a.match_id, a.person_id
        from appearances a join matches m using (match_id)
        where a.franchise_season_id is distinct from m.team_a_fs_id
          and a.franchise_season_id is distinct from m.team_b_fs_id
        order by 1, 2
        """,
    )
    offenders += [
        f"{match_id}: {person_id} appears for a franchise-season not playing this match"
        for match_id, person_id in stray_appearances
    ]

    (participants,), = _rows(
        conn,
        """
        select count(*) from (
            select distinct d.match_id, x.person_id
            from deliveries d,
                 unnest(array[d.batter_id, d.bowler_id, d.non_striker_id,
                              d.player_out_id]) as x(person_id)
            where x.person_id is not null
        ) t
        """,
    )
    return verdict(
        2, title, f"{participants} match-participant pairs cross-checked", offenders
    )


def check_03_legal_balls_per_over(conn) -> Result:
    """Six legal balls per over, except the last over of an innings and the
    miscounted overs the source itself declares."""
    title = "completed overs hold six legal balls"
    offenders: list[str] = []

    short = _rows(
        conn,
        """
        with per_over as (
            select match_id, innings_no, is_super_over, over_no,
                   count(*) filter (where legal_ball) as legal,
                   bool_or(over_miscounted) as miscounted
            from deliveries
            group by 1, 2, 3, 4
        ), bounded as (
            select *, max(over_no) over (
                partition by match_id, innings_no, is_super_over
            ) as final_over
            from per_over
        )
        select match_id, innings_no, over_no, legal
        from bounded
        where over_no < final_over and not miscounted and legal <> 6
        order by 1, 2, 3
        """,
    )
    offenders += [
        f"{match_id} inn{innings_no} ov{over_no}: {legal} legal balls in a completed over"
        for match_id, innings_no, over_no, legal in short
    ]

    # The exemption above is only as good as the set it exempts.
    flagged = _rows(
        conn,
        """
        select match_id, innings_no, over_no, count(*) filter (where legal_ball)
        from deliveries where over_miscounted
        group by 1, 2, 3 order by 1, 2, 3
        """,
    )
    found = {(m, i, o) for m, i, o, _ in flagged}
    for key in sorted(found - MISCOUNTED):
        offenders.append(f"{key[0]} inn{key[1]} ov{key[2]}: miscounted, not in the whitelist")
    for key in sorted(MISCOUNTED - found):
        offenders.append(f"{key[0]} inn{key[1]} ov{key[2]}: whitelisted, not flagged in the database")
    for m, i, o, legal in flagged:
        if legal == 6:
            offenders.append(f"{m} inn{i} ov{o}: flagged miscounted but holds six legal balls")

    (total_overs,), = _rows(
        conn, "select count(*) from (select distinct match_id, innings_no, is_super_over, over_no from deliveries) t"
    )
    return verdict(
        3, title,
        f"{total_overs} overs checked, {len(flagged)} miscounted overs matched the whitelist",
        offenders,
    )


def check_04_two_franchise_seasons(conn) -> Result:
    """Exactly two distinct franchise-seasons per match, both from its own season."""
    title = "each match maps to two franchise-seasons of its own season"
    offenders: list[str] = []

    bad_teams = _rows(
        conn,
        """
        select m.match_id, m.season_year, a.season_year, b.season_year
        from matches m
        left join franchise_seasons a on a.franchise_season_id = m.team_a_fs_id
        left join franchise_seasons b on b.franchise_season_id = m.team_b_fs_id
        where m.team_a_fs_id is null or m.team_b_fs_id is null
           or m.team_a_fs_id = m.team_b_fs_id
           or a.season_year is distinct from m.season_year
           or b.season_year is distinct from m.season_year
        order by 1
        """,
    )
    offenders += [
        f"{match_id}: season {season}, teams from {a} and {b}"
        for match_id, season, a, b in bad_teams
    ]

    # A winner or toss winner that is not one of the two teams is not FK-preventable.
    outsiders = _rows(
        conn,
        """
        select match_id, 'winner' from matches
        where winner_fs_id is not null
          and winner_fs_id is distinct from team_a_fs_id
          and winner_fs_id is distinct from team_b_fs_id
        union all
        select match_id, 'toss winner' from matches
        where toss_winner_fs_id is not null
          and toss_winner_fs_id is distinct from team_a_fs_id
          and toss_winner_fs_id is distinct from team_b_fs_id
        order by 1
        """,
    )
    offenders += [f"{match_id}: {role} is not one of the two teams" for match_id, role in outsiders]

    (matches,), = _rows(conn, "select count(*) from matches")
    (fs,), = _rows(conn, "select count(*) from franchise_seasons")
    return verdict(4, title, f"{matches} matches over {fs} franchise-seasons", offenders)


def check_05_no_squad_member_without_appearances(conn) -> Result:
    title = "no franchise-season holds a player with zero appearances"
    (n,), = _rows(conn, "select count(*) from squad_members")
    if n == 0:
        return skipped(
            5, title,
            "squad_members is empty; SPEC 6 builds it. Will assert every "
            "squad_members row has a matching appearances row for the same "
            "franchise-season.",
        )
    offenders = [
        f"fs {fs_id}: {person_id} has no appearance for this franchise-season"
        for fs_id, person_id in _rows(
            conn,
            """
            select s.franchise_season_id, s.person_id
            from squad_members s
            where not exists (
                select 1 from appearances a
                where a.person_id = s.person_id
                  and a.franchise_season_id = s.franchise_season_id
            )
            order by 1, 2
            """,
        )
    ]
    return verdict(5, title, f"{n} squad_members rows checked", offenders)


def check_06_super_overs_excluded(conn) -> Result:
    """Super over deliveries reach no derived stat table.

    The state model is the first derived table to exist, and this check fired as a FAIL
    the moment migration 007 created it - which is what it was built to do. It had been
    skipping with a reason, and the skip turned into a failure rather than a pass so that
    an unexamined derived table could not be mistaken for a verified one.

    Aggregation destroys provenance: no column on `state_ball_outcomes` can say which
    deliveries built a cell, so the exclusion has to be checked where it happens. The
    assertion is that **no delivery is both a super over and inside the fitting set**, and
    that each clause of the filter would exclude them on its own.

    The redundancy is the finding here and it is worth stating rather than trimming. A
    super over carries `innings_no >= 3` and a null `innings_scheduled_balls`, so
    `innings_no = 1` and `innings_scheduled_balls = 120` each exclude every super over
    delivery without help - the explicit `not is_super_over` removes nothing. That is A21
    working exactly as intended: a positive test for a known value excludes nulls by
    construction instead of relying on anyone remembering the negative clause. The clause
    stays because it states the intent, and this check reports that it is currently
    carrying no weight, so nobody mistakes it for the thing doing the work.
    """
    title = "super over deliveries are excluded from derived stat tables"
    derived = sorted(_tables(conn) - BASE_TABLES - OPERATIONAL_TABLES)
    if not derived:
        (n,), = _rows(conn, "select count(*) from deliveries where is_super_over")
        return skipped(
            6, title,
            f"no derived stat table exists yet; SPEC 7 builds them. "
            f"{n} super over deliveries are flagged and waiting to be excluded.",
        )
    unexamined = [t for t in derived if t not in STATE_TABLES]
    if unexamined:
        return bad(
            6, title, "a derived stat table exists but this check has not been written",
            [f"unexamined table: {t}" for t in unexamined],
        )

    offenders: list[str] = []
    (leaked,), = _rows(
        conn, f"select count(*) from deliveries where is_super_over and ({FITTING_SET})")
    if leaked:
        offenders.append(f"{leaked} super over deliveries satisfy the SPEC 7.1 fitting set")

    # Each clause on its own, so the exclusion does not rest on a single predicate.
    (supers,), = _rows(conn, "select count(*) from deliveries where is_super_over")
    for clause in ("innings_no = 1", "innings_scheduled_balls = 120"):
        (survives,), = _rows(
            conn, f"select count(*) from deliveries where is_super_over and {clause}")
        if survives:
            offenders.append(f"`{clause}` alone lets {survives} super over deliveries through")

    # Migration 009's ratings table is fed by the SCORING set, not the fitting set: it
    # scores second innings and miscounted overs too. So it needs its own assertion rather
    # than inheriting the one above, and the same redundancy holds for the same reason -
    # a super over's `innings_scheduled_balls` is null, so `= 120` excludes it unaided.
    (leaked,), = _rows(
        conn, f"select count(*) from deliveries where is_super_over and ({SCORING_SET})")
    if leaked:
        offenders.append(f"{leaked} super over deliveries satisfy the SPEC 7.2 scoring set")
    (survives,), = _rows(
        conn,
        "select count(*) from deliveries "
        "where is_super_over and innings_scheduled_balls = 120",
    )
    if survives:
        offenders.append(
            f"`innings_scheduled_balls = 120` alone lets {survives} super over deliveries "
            "into the scoring set"
        )

    # `player_season_rating` has no delivery provenance of its own - it reads only
    # `player_season_impact` and `squad_members`, so the exclusion above already covers it.
    # What it CAN get wrong independently is WHO it offers.
    #
    # [A65] The rule this asserts changed once already. Until 013 the view offered exactly
    # the seasons passing A33's floors; A65 reversed that, so it must offer every season
    # that faced or bowled a ball. `balls > 0` is arithmetic rather than a floor - without a
    # ball there is no per-ball figure to shrink and no balls-per-match to multiply by.
    #
    # [A71] It changed again. Four of 3,337 squad_members face or bowl NO balls at all that
    # season (a role was assigned, but nothing happened behind it) and A65's `balls > 0`
    # correctly does not admit them - there is no per-ball evidence to shrink. A71 adds a
    # second branch, from reputation alone, so `playable` is no longer the whole answer:
    # the right total is `playable` plus every squad_member with no `player_season_impact`
    # row in either discipline. Both counts are taken independently of the view, the same
    # discipline check 6 has always applied - a view that simply unioned in every
    # squad_member, rated or not, would still pass a count taken from its own SELECT.
    #
    # The assertion is kept EXACT in both directions. Too few rows means a gate (old or new)
    # has crept back and some slice of every squad has silently lost its cards; too many
    # means a season with no evidence at all has acquired a rating out of nothing.
    #
    # `balls > 0` is INERT today - no `player_season_impact` row has zero balls, so the
    # clause changes nothing and breaking it does not fail this check. Kept and named for
    # the same reason check 6 keeps its inert super-over clause: it states the rule. What
    # DOES fail here is reverting to A33's gate, which is the regression worth catching.
    (playable,), = _rows(
        conn, "select count(*) from player_season_impact where balls > 0")
    (zero_evidence,), = _rows(
        conn,
        """
        select count(*) from squad_members s
        where not exists (
            select 1 from player_season_impact i
            where i.franchise_season_id = s.franchise_season_id
              and i.person_id = s.person_id
        )
        """,
    )
    (offered,), = _rows(conn, "select count(*) from player_season_rating")
    expected = playable + zero_evidence
    if offered != expected:
        offenders.append(
            f"player_season_rating offers {offered:,} rows against {expected:,} expected "
            f"({playable:,} that faced or bowled a ball, A65, plus {zero_evidence:,} rated "
            "from reputation alone, A71)"
        )

    # [A71] The property the fix exists for: no squad member, however thin his contribution,
    # is left with no rating at all. This is the direct check, independent of the row count
    # above - a bug that dropped four rated rows and added four different unrated ones would
    # pass the count and fail this.
    (uncovered,), = _rows(
        conn,
        """
        select count(*) from squad_members s
        where not exists (
            select 1 from player_season_rating r
            where r.franchise_season_id = s.franchise_season_id
              and r.person_id = s.person_id
        )
        """,
    )
    if uncovered:
        offenders.append(
            f"{uncovered:,} squad_members have no player_season_rating row at all (A71)"
        )

    # [A72] `person_batting_positions` excludes super overs at the ETL's own scan
    # (`etl.derive_squads.batting_positions` filters `not is_super_over` before any
    # per-innings buffering happens), so there is no redundant second clause to test
    # independently here the way the fitting/scoring sets have -- one predicate, applied
    # once. What IS checkable is that the exclusion is not vacuous: recomputing the same
    # aggregate WITHOUT the filter must change at least one count, or "excluded" would be
    # true only because super overs happened to contribute nothing anyway. Check 23 is
    # what confirms the CORRECTLY-excluding version is what is actually stored; this is
    # the narrower claim that excluding matters at all.
    (with_supers,), = _rows(
        conn,
        """
        with events as (
            select match_id, innings_no, over_no, ball_no, batter_id as person_id,
                   0 as role_rank
            from deliveries
            union all
            select match_id, innings_no, over_no, ball_no, non_striker_id, 1
            from deliveries
        ),
        first_seen as (
            select distinct on (match_id, innings_no, person_id)
                   match_id, innings_no, person_id, over_no, ball_no, role_rank
            from events
            order by match_id, innings_no, person_id, over_no, ball_no, role_rank
        )
        select count(*) from first_seen
        """,
    )
    (without_supers,), = _rows(conn, "select sum(innings) from person_batting_positions")
    if with_supers == without_supers:
        offenders.append(
            "including super overs in the career position scan changes nothing -- the "
            "exclusion clause may not be doing anything"
        )

    # [A76] `person_season_batting_positions` is written from the exact same scan as
    # `person_batting_positions` (`etl.career_positions` runs `batting_positions()` once
    # and re-aggregates it two ways), so their grand totals must be identical -- summing
    # a person's innings across every season must equal summing them across his career,
    # by definition. This is the one relationship that could drift if the season write
    # ever excluded super overs differently from the career write (or excluded a
    # different filter entirely), which neither table's own row-count alone would catch.
    (season_total,), = _rows(
        conn, "select sum(innings) from person_season_batting_positions")
    if season_total != without_supers:
        offenders.append(
            f"person_season_batting_positions totals {season_total:,} innings against "
            f"person_batting_positions' {without_supers:,} -- the two aggregates of the "
            "same scan have drifted apart"
        )

    (stored,), = _rows(conn, "select sum(faced) from state_ball_outcomes")
    (rated,), = _rows(conn, "select count(*) from player_season_impact")
    return verdict(
        6, title,
        f"{len(derived)} derived table(s) examined; all {supers:,} super over deliveries "
        f"excluded from both the fitting and scoring sets by each clause independently; "
        f"state model holds {stored:,} balls; ratings hold {rated:,} rows of which "
        f"{playable:,} faced or bowled a ball, {zero_evidence:,} are rated from reputation "
        f"alone (A71), and every one of 3,337 squad_members is covered; excluding super "
        f"overs from the career position scan changes {with_supers - without_supers:,} "
        f"innings; the season-grain table totals the same {season_total:,} innings as "
        "the career-grain one",
        offenders,
    )


def check_17_balls_faced_convention(conn) -> Result:
    """Balls faced excludes wides only, so it reproduces published strike rates.

    Hand-entered reference figures, not scraped. Three season aggregates are enough
    because the two candidate predicates differ by the number of no-balls a batter
    faced, which is small but never zero over a season - so a wrong predicate cannot
    coincidentally agree with all three.
    """
    title = "balls faced reproduces published strike rates"
    offenders: list[str] = []
    for name, season, runs, balls in PUBLISHED_SEASONS:
        rows = _rows(
            conn,
            """
            select sum(d.runs_batter),
                   count(*) filter (where d.extra_wides = 0),
                   count(*) filter (where d.legal_ball)
            from deliveries d
            join people p on p.person_id = d.batter_id
            join matches m using (match_id)
            where m.season_year = %s and not d.is_super_over and p.primary_name = %s
            """,
            (season, name),
        )
        got_runs, faced, legal_only = rows[0]
        if got_runs is None:
            offenders.append(f"{name} {season}: no deliveries found")
            continue
        if got_runs != runs:
            offenders.append(f"{name} {season}: {got_runs} runs, published {runs}")
        if faced != balls:
            offenders.append(
                f"{name} {season}: {faced} balls faced, published {balls}"
                f" (legal-ball-only would give {legal_only})"
            )
    return verdict(
        17, title, f"{len(PUBLISHED_SEASONS)} published season aggregates reproduced",
        offenders,
    )


def check_18_batting_order_is_complete(conn) -> Result:
    """SPEC 6.4/A4. An innings that lost ten wickets used exactly eleven batters.

    The wicket count comes from `player_out_id`, which the batting-position rule never
    reads, so this predicts the size of the order from a column outside the rule
    rather than restating it. Dropping the `non_striker` scan, or letting an Impact
    Player entrant take a twelfth position, both break it.

    [A72] Untouched by the draft redesign, and more load-bearing than before:
    `person_batting_positions` is a full-archive aggregate of exactly the per-innings scan
    this check guards, so a regression here would silently corrupt every player's
    career-wide position eligibility, not just one season's `squad_members` row.
    """
    title = "an all-out innings used exactly eleven batters"
    (all_out,), = _rows(
        conn,
        """
        select count(*) from (
            select match_id, innings_no
            from deliveries where not is_super_over
            group by 1, 2
            having count(*) filter (where player_out_id is not null) = 10
        ) t
        """,
    )
    if not all_out:
        return skipped(18, title, "no innings in the database lost ten wickets")

    offenders = [
        f"{match_id} innings {innings_no}: 10 wickets but {used} batters"
        for match_id, innings_no, used in _rows(
            conn,
            """
            with batters as (
                select match_id, innings_no, batter_id as person_id
                from deliveries where not is_super_over
                union
                select match_id, innings_no, non_striker_id
                from deliveries where not is_super_over
            ),
            used as (
                select match_id, innings_no, count(*) as n from batters group by 1, 2
            ),
            wickets as (
                select match_id, innings_no,
                       count(*) filter (where player_out_id is not null) as w
                from deliveries where not is_super_over group by 1, 2
            )
            select match_id, innings_no, used.n
            from wickets join used using (match_id, innings_no)
            where wickets.w = 10 and used.n <> 11
            """,
        )
    ]
    offenders += [
        f"franchise-season {fs_id} {person_id}: position {low}-{high}"
        for fs_id, person_id, low, high in _rows(
            conn,
            """
            select franchise_season_id, person_id,
                   batting_position_min, batting_position_max
            from squad_members
            where batting_position_max > 11 or batting_position_min < 1
            """,
        )
    ]
    return verdict(18, title, f"{all_out} all-out innings checked", offenders)


def check_19_every_squad_can_field_a_keeper(conn) -> Result:
    """A24. A franchise-season with no keeper cannot be drafted legally.

    This is a check on the deck, not on the parse. The keeper slot in the XI has to
    be filled from somewhere, so a squad that offers nobody to fill it is a card that
    breaks the game rather than a statistic that is slightly off. It fails while
    `keepers_by_season.csv` is unfilled, and that is the correct reading: the failure
    is real and it blocks the deck.

    [A72] More load-bearing than before, not less. Measured directly against the current
    deck with the forward check switched off: a naive or random drafter fails to field a
    legal twelve mostly for want of a keeper (75 of 127, and 101 of 164, illegal squads
    respectively) -- a rational drafter's own need-tracking largely avoids it on its own,
    which is exactly why the forward check exists for the cases that do not.
    """
    title = "every franchise-season offers at least one keeper"
    offenders = [
        f"{season} {name} ({squad} players, none kept)"
        for season, name, squad in _rows(
            conn,
            """
            select fs.season_year, fs.display_name, count(sm.person_id)
            from franchise_seasons fs
            left join squad_members sm using (franchise_season_id)
            group by fs.franchise_season_id, fs.season_year, fs.display_name
            having count(*) filter (where sm.role = 'keeper') = 0
            order by fs.season_year, fs.display_name
            """,
        )
    ]
    (total,), = _rows(conn, "select count(*) from franchise_seasons")
    return verdict(
        19, title, f"{total - len(offenders)} of {total} franchise-seasons covered",
        offenders,
    )


def check_12_the_draft_can_always_complete(conn) -> Result:
    """SPEC 1.1/1.2/8.12. Run the draft. Do not count positions and infer that it would work.

    [A72/A73] The question changed shape three times now. It was never "does every
    franchise-season hold enough draftable players for every slot type" (a per-slot count)
    -- a drafter is served franchise-seasons one at a time, and dedupe on `person_id`
    removes a player from every other card they appear on, so feasibility is a property of
    the whole sequence and the only honest test is to play it out. The slot template was
    then retired for a fifteen-then-arrange model, and A73 retired THAT for a direct
    twelve-pick draft where placement is atomic: every pick already occupies a slot the
    moment it is made.

    That atomicity is exactly what removes the separate "completed but cannot field a legal
    twelve" case this check used to test via `best_twelve` -- there is no longer a second
    solve to disagree with the one the forward check already did. What is still checked,
    independently, is that the CONSTRUCTION's guarantee actually holds: every completed
    draft's own `order`/`impact` passes `order_errors` -- a totally different code path
    (rule-by-rule, from scratch) from the incremental forward check that built it, so a bug
    in the forward check that let an illegal arrangement through would still be caught here.

    Asserted with the deal-time guarantee *on* and the forward check (`require_legal`) *on*,
    for `rational` and `naive` -- 0 of 300 each, both stranded and order_errors-illegal.
    **`random` is measured, not asserted, and is expected to be non-zero: 14 of 2,000 at a
    higher trial count.** This is a real, found consequence of `could_still_complete`'s own
    optimism, not a bug in translating it into code: with exactly one pick left, the
    Hall's-theorem argument only proves POSITION is always reachable, and the count check
    assumes -- as the wildcard model always has (A31/A61) -- that the one remaining pick can
    be a bowling keeper if that is what is still needed. Real archive data does not always
    supply one: one specific stranding (reproduced against seed 7) needed a keeper who ALSO
    bowls, eligible at position 10, with 11 of 166 franchise-seasons offering an eligible
    keeper there and precisely 0 offering one who bowls too. No amount of re-drawing finds a
    player who does not exist. `rational` avoids this because it actively prioritises a
    missing keeper or bowling depth over rating (see `pick_rational`); a policy that ignores
    both for eleven straight picks is not a policy any real drafter -- human or automated --
    plays, and the live legality panel a human draft reads from is built from the same
    counts `rational` reads from. `require_legal=False` is reported for all three policies,
    unchanged in spirit from before -- it shows how much work the forward check alone is
    buying, now measured on a tighter twelve-pick draft where every pick is load-bearing
    rather than three of fifteen being slack.
    """
    title = "a draft can always complete against real coverage"
    deck = load_deck(conn)
    trials, seed = 300, 7

    offenders, notes = [], []
    for policy in ("rational", "naive", "random"):
        results = simulate(deck, policy, trials, seed, guarantee=True, require_legal=True)
        stranded = [r for r in results if not r.completed]
        illegal = [r for r in results if r.completed
                   and order_errors(r.order, r.impact, r.picks)]
        if illegal or (stranded and policy != "random"):
            offenders.append(
                f"{policy}: {len(stranded)} of {trials} drafts stranded, "
                f"{len(illegal)} completed but order_errors calls the result illegal"
            )
        elif stranded:
            notes.append(f"{policy} stranded {len(stranded)} of {trials} (expected, "
                          f"see docstring)")
        blind = [r for r in simulate(deck, policy, trials, seed, guarantee=True,
                                      require_legal=False)
                 if not r.completed or order_errors(r.order, r.impact, r.picks)]
        notes.append(f"{policy} {len(blind) / trials:.0%}")

    supply = deck.position_supply()
    thin = min(supply.items(), key=lambda kv: kv[1])
    detail = (f"{trials} drafts x 3 policies field a legal twelve with the forward check "
              f"on; without it (guarantee still on) {', '.join(notes)} cannot; thinnest "
              f"position {thin[0]} servable from {thin[1]} of {len(deck.fs_ids)} "
              f"franchise-seasons")
    return verdict(12, title, detail, offenders)


def check_08_career_leaderboards(conn) -> Result:
    """SPEC 8.8. The career leaderboards, and the three ways they break quietly.

    SPEC asks for these to be printed for plausibility, and `--leaderboards` does that.
    Printing alone cannot fail, so the automated half asserts the things a human
    skimming ten names would not notice:

    * **Kohli leads the run scorers.** The only external anchor available here. It is
      robust to a 2026 revision in a way an exact total is not: the margin is over 2,000
      runs, so no plausible season reorders it. Per A22's lesson the claim is the
      ordering, which is what has been checked, not a figure standing next to it.
    * **No name appears twice.** A leaderboard is where a `person_id` split surfaces as
      one player wearing two rows, and no foreign key can see it.
    * **Every dismissal kind is classified.** Bowler-credited plus not-credited must
      account for all of them. A kind Cricsheet adds later - `handled the ball`, say -
      would otherwise fall between the two and vanish from the wicket column silently,
      which is the same failure shape as an unstorable outcome in the state model.
    """
    title = "career leaderboards are plausible and completely classified"
    offenders: list[str] = []

    batters = _rows(conn, TOP_RUN_SCORERS, (10,))
    bowlers = _rows(conn, TOP_WICKET_TAKERS, (list(NOT_BOWLER_CREDITED), 10))

    if not batters or batters[0][0] != LEADING_RUN_SCORER:
        got = batters[0][0] if batters else "nobody"
        offenders.append(f"leading run scorer is {got}, expected {LEADING_RUN_SCORER}")

    for label, board in (("run scorers", batters), ("wicket takers", bowlers)):
        names = [row[0] for row in board]
        repeated = {n for n in names if names.count(n) > 1}
        offenders += [f"{label}: {n} appears twice, so a person_id is split"
                      for n in sorted(repeated)]

    kinds = dict(_rows(conn, """
        select wicket_kind, count(*) from deliveries
        where not is_super_over and wicket_kind is not null group by 1
    """))
    unclassified = sorted(set(kinds) - set(BOWLER_CREDITED) - set(NOT_BOWLER_CREDITED))
    offenders += [f"dismissal kind '{k}' ({kinds[k]}) is credited to nobody" for k in unclassified]

    runs = batters[0][1] if batters else 0
    return verdict(
        8, title,
        f"{len(kinds)} dismissal kinds all classified; "
        f"{LEADING_RUN_SCORER} {runs:,} runs, "
        f"{bowlers[0][0] if bowlers else '-'} {bowlers[0][1] if bowlers else 0} wickets",
        offenders,
    )


def print_leaderboards(conn, top: int = 10) -> None:
    """SPEC 8.8's other half: the lists a human reads for a name that looks wrong."""
    print(f"\ntop {top} career run scorers (super overs excluded)")
    print(f"    {'#':<4}{'player':<26}{'runs':>7}{'balls':>8}{'per ball':>10}")
    for i, (name, runs, balls) in enumerate(_rows(conn, TOP_RUN_SCORERS, (top,)), 1):
        print(f"    {i:<4}{name:<26}{runs:>7,}{balls:>8,}{runs / balls:>10.3f}")

    print(f"\ntop {top} career wicket takers (bowler-credited dismissals only)")
    print(f"    {'#':<4}{'player':<26}{'wickets':>8}")
    rows = _rows(conn, TOP_WICKET_TAKERS, (list(NOT_BOWLER_CREDITED), top))
    for i, (name, wickets) in enumerate(rows, 1):
        print(f"    {i:<4}{name:<26}{wickets:>8,}")

    print(f"\n    credited to the bowler: {', '.join(BOWLER_CREDITED)}")
    print(f"    not credited:           {', '.join(NOT_BOWLER_CREDITED)}")


def check_20_state_model_covers_every_state(conn) -> Result:
    """SPEC 7.1. All 80 states present, and the fit reconciles with a fresh recount.

    Two distinct claims. The first is about legibility: a state the archive has never
    seen must be stored as `faced = 0`, not left out. An absent row and a zero row mean
    different things to the simulator - "this table does not cover that" against "cricket
    has never produced that" - and only one of them is true, so the table has to say
    which rather than leaving it to be inferred from a missing key.

    The second re-derives the fitting set straight from `deliveries` and demands the
    stored totals match. That is what makes this more than a restatement of the loader:
    it fails if the state model is ever left stale behind a reload, or refitted through a
    filter that has drifted from A28's `innings_scheduled_balls = 120`.
    """
    title = "state model covers all 80 states and reconciles with the fitting set"
    if not _table_exists(conn, "state_ball_outcomes"):
        return skipped(20, title, "state_ball_outcomes absent - run etl.state_model --write")
    offenders: list[str] = []

    stored = {(over, bucket): faced for over, bucket, faced in _rows(
        conn, "select over_no, wicket_bucket, faced from state_ball_outcomes")}
    for over in range(20):
        for bucket in WICKET_BUCKETS:
            if (over, bucket) not in stored:
                offenders.append(f"state (ov{over}, {bucket}) has no row at all")

    (faced, runs, outs), = _rows(
        conn, "select sum(faced), sum(runs_off_bat), sum(dismissals) from state_ball_outcomes")
    (obs, remaining), = _rows(
        conn, "select sum(observations), count(*) from state_runs_remaining")
    # A31/008: the wicket cost is the drop in expected FINAL total, so both halves of it
    # have to be present. An empty table after migration 008 lands here as a mismatch.
    (final_at_start,), = _rows(conn, """
        select (runs_so_far_total + runs_remaining_total)::float / observations
        from state_runs_remaining where over_no = 0 and wickets = 0
    """) or [(None,)]

    # Recounted from deliveries through A28's filter, not read back from the fit.
    (fit_faced, fit_runs, fit_all), = _rows(conn, f"""
        select count(*) filter (where extra_wides = 0),
               sum(runs_batter) filter (where extra_wides = 0),
               count(*)
        from deliveries where {FITTING_SET}
    """)
    # `sum` over an empty table is null, which is the state right after a migration that
    # truncated one. That has to read as "not fitted yet", not crash the runner: a check
    # that raises is a check whose verdict nobody gets.
    for label, got, want in (("balls faced", faced, fit_faced),
                             ("runs off the bat", runs, fit_runs),
                             ("runs-remaining observations", obs, fit_all)):
        if got is None:
            offenders.append(
                f"{label}: nothing stored — refit with `etl.state_model --write`")
        elif got != want:
            offenders.append(f"{label}: state model holds {got:,}, deliveries gives {want:,}")

    zeroes = sum(1 for n in stored.values() if n == 0)
    anchor = f"{final_at_start:.1f}" if final_at_start else "MISSING"
    return verdict(
        20, title,
        f"{len(stored)} of 80 states stored ({zeroes} never observed, kept as zeroes); "
        f"{faced:,} balls faced, {outs:,} dismissals, {remaining} runs-remaining states; "
        f"expected first-innings total {anchor}",
        offenders,
    )


def check_21_no_real_xi_fielded_five_overseas(conn) -> Result:
    """A51. Our nationality data replayed against the IPL's own four-overseas cap.

    The third check in this file that can tell us we are WRONG rather than merely
    inconsistent, and the only one that tests SPEC 6.1. Checks 1 and 15 read the archive
    for facts it states outright; this one reads a rule the archive never states and the
    IPL has enforced in every season since 2008, then replays 2,486 real team sheets
    against it. An XI our data says fielded five overseas players is our data being wrong
    about somebody in that XI, because the match was played and the XI was legal.

    It earns its keep by having already found things nothing else could. Three override
    rows claimed a nationality the seasons made impossible, and the archive-resolved half
    turned out to answer a subtly different question from the one the draft asks -- which
    nation a player EVER represented, not the one he held that season. A player who
    emigrated after his IPL career reads as overseas for seasons in which he was not.

    Deliberately counts the KNOWN overseas only, exactly as the engine does (A49). An
    unknown nationality cannot make this fail, which is right: unknown is a gap and this
    check is for contradictions. The gaps are check 19's business and the CSV's.

    [A72] Untouched by the draft redesign -- it already counts exactly the object the
    redesign fields, the named twelve (A51), and needs no code change to keep meaning that.
    """
    title = "no real XI fielded more than four overseas players"
    nationality = dict(_rows(
        conn, "select person_id, nationality from people where nationality is not null"))
    if not nationality:
        return skipped(21, title, "no nationalities derived - run etl.derive_people")

    # The named XI, not everyone who took the field: a substitute fielder never counted
    # against the cap. See etl.derive_people.real_xis.
    xis = _rows(conn, """
        select f.season_year, m.match_id, a.franchise_season_id, array_agg(a.person_id)
        from appearances a
        join franchise_seasons f on f.franchise_season_id = a.franchise_season_id
        join matches m on m.match_id = a.match_id
        where a.named_in_squad
        group by 1, 2, 3
    """)
    names = dict(_rows(conn, "select person_id, primary_name from people"))

    culprits: Counter = Counter()
    illegal = 0
    for year, match_id, _fs, people in xis:
        overseas = [p for p in people
                    if nationality.get(p) not in (None, HOME_NATION)]
        if len(overseas) > OVERSEAS_PER_XI:
            illegal += 1
            for p in overseas:
                culprits[p] += 1

    # Named by player rather than by match. A single wrong nationality shows up in every
    # XI that player appeared in, so a list of matches would report one error dozens of
    # times and bury how few distinct facts are actually in dispute.
    offenders = [
        f"{names.get(p, p)} ({nationality[p]}) sits in {n} XI(s) that would hold "
        f"{OVERSEAS_PER_XI + 1}+ overseas players"
        for p, n in culprits.most_common(15)
    ]
    if len(culprits) > 15:
        offenders.append(f"... {len(culprits) - 15} more player(s) implicated")
    return verdict(
        21, title,
        f"{illegal} of {len(xis)} real XIs would be illegal under our nationality data; "
        f"{len(culprits)} player(s) implicated",
        offenders,
    )


def check_22_the_card_and_the_engine_agree(conn) -> Result:
    """A57. What the drafter reads and what the engine plays are the same claim.

    The reputation blend is the one term in the project that is not a measurement, and the
    only thing that keeps it honest is that it reaches BOTH numbers. If it lifted the card
    without lifting `rated_per_ball`, a drafter would pick a 97 and watch it bowl like a 78
    -- and would be right to trust neither number afterwards.

    So this recomputes the identity the view is built on: spread over a player-season's own
    balls, `rated_per_ball` has to integrate back to exactly `blended_merit`. It is not a
    restatement of the view's arithmetic, because it re-derives the left side from the
    per-discipline columns a consumer actually reads, in the grain a consumer reads them.
    A blend applied to the display alone fails here on every row that has one.

    Also pins the scale to A58's 70-100. A rating outside it means the percentile anchors
    stopped bracketing the data, which is what a revised archive would do quietly.

    [A71] The identity has a second form now. A71's four reputation-only rows have no
    in-season balls to integrate `rated_per_ball` over - that is exactly why they needed
    the branch - so their `rated_per_ball` is `blended_merit` divided by the SAME reference
    exposure A59 already declares (18 balls a match for batting, 24 for bowling), and the
    identity to check is the inverse of that division, not the general per-ball integral.
    Using the general formula on these rows would multiply by `balls = 0` and always
    integrate to zero regardless of what the card claims, which is a check that cannot fail
    - the exact failure mode this project's own standing rule refuses.
    """
    title = "the card's rating and the engine's per-ball value are the same claim"
    if not _table_exists(conn, "player_season_rating"):
        return skipped(22, title, "player_season_rating absent - apply migration 011")

    REF_BALLS_PER_MATCH = {"batting": 18.0, "bowling": 24.0}

    offenders: list[str] = []
    rows = _rows(conn, """
        select p.primary_name, r.season_year, r.franchise_season_id, r.person_id,
               r.discipline, r.rated_per_ball, r.balls, r.matches, r.prior_source,
               r.blended_merit, r.display_rating
        from player_season_rating r
        join people p on p.person_id = r.person_id
        order by r.franchise_season_id, r.person_id
    """)

    groups: dict[tuple, list[tuple]] = {}
    for row in rows:
        groups.setdefault((row[2], row[3]), []).append(row)

    both = 0
    for (fs_id, person_id), grp in groups.items():
        name, year = grp[0][0], grp[0][1]
        blended = float(grp[0][9])
        display = grp[0][10]
        integrated = 0.0
        for (_, _, _, _, discipline, rated_per_ball, balls, matches,
             prior_source, _, _) in grp:
            if prior_source == "reputation_floor":
                # [A71] The inverse of how `rated_per_ball` was built for this row: no
                # season balls to spread over, so the reference exposure stands in.
                integrated += float(rated_per_ball) * REF_BALLS_PER_MATCH[discipline]
            else:
                integrated += float(rated_per_ball) * float(balls) / float(matches)
        if abs(integrated - blended) > 1e-6:
            offenders.append(
                f"{name} {year}: engine integrates to {integrated:.4f} but the "
                f"card claims {blended:.4f}")
        if not 70 <= display <= 100:
            offenders.append(f"{name} {year}: display_rating {display} is outside 70-100")
        if len(grp) == 2:
            both += 1

    return verdict(
        22, title,
        f"{len(groups):,} player-seasons reconciled, {both:,} of them all-rounders scoring "
        f"in both disciplines",
        offenders,
    )


def check_09_no_rating_leaks_across_seasons(conn) -> Result:
    """SPEC 8.9. A season's rating is built from that season, and priors are the exception.

    The original wording - no rating uses data from any other season "except as a shrinkage
    prior" - was written before there were two shrinkage stages. There now are: the band
    prior inside `player_season_impact` (A46) and the career blend on top of it (A57). So
    the check has to police the exception rather than merely restate it, and it does that
    in two ways that fail differently.

    First, the EVIDENCE. `balls` and `matches` are recounted from `deliveries` restricted to
    that franchise-season alone. If a career aggregate ever leaked into the impact totals
    rather than into the prior, the denominators are where it would surface, because they
    are the one part of the rating that a prior cannot legitimately touch.

    Second, the BOUND. A57's blend is a convex combination of `merit` and `career_merit`, so
    the blended value must lie between them. That is what makes the career term shrinkage
    rather than leakage: it can pull a season toward the player's other seasons and can
    never carry it past them, so no season can be rated on evidence it does not have. A
    blend that escaped its own endpoints - a bonus applied to careers above some bar, say -
    would pass every count-based check ever written and fail this one on the row it moved.

    [A71] A71's four reputation-only rows are a THIRD kind of prior, not an exception to
    either rule above: they recount to exactly zero balls (there is genuinely no delivery
    behind them, so the LEFT JOIN recount is coalesced to 0 rather than compared against a
    NULL it would trivially satisfy), and `merit` is NULL for them - there is no in-season
    evidence to convex-combine with - so the BOUND check's `least`/`greatest` would silently
    pass every one of these rows on NULL propagation alone without ever inspecting them. That
    silent pass is exactly the failure this project's standing rule on vacuous checks warns
    against, so these rows get their own explicit assertion instead of an accidental one:
    `blended_merit` must equal `career_merit` when the player has any other rated season, and
    every zero-career row (no rated season anywhere) must land on the identical scale floor,
    not a different number each - the falsifiable proxy for "one shared arbitrary floor"
    rather than four independently fabricated ones.
    """
    title = "no rating leaks across seasons except through a declared prior"
    if not _table_exists(conn, "player_season_rating"):
        return skipped(9, title, "player_season_rating absent - apply migration 012")

    offenders: list[str] = []

    # Recounted from deliveries, per franchise-season, never per person's career.
    # coalesce(c.balls, 0): a player-discipline with truly no deliveries (A71) recounts to
    # zero, not NULL, so it is compared honestly against the view's own `balls = 0` rather
    # than passing by `0 is distinct from NULL` being true for the wrong reason.
    mismatches = _rows(conn, f"""
        with counted as (
            select d.batting_fs_id as fs, d.batter_id as pid, 'batting' as discipline,
                   count(*) filter (where d.extra_wides = 0) as balls
            from deliveries d where {SCORING_SET} group by 1, 2
            union all
            select d.bowling_fs_id, d.bowler_id, 'bowling',
                   count(*) filter (where d.legal_ball)
            from deliveries d where {SCORING_SET} group by 1, 2
        )
        select p.primary_name, r.season_year, r.discipline, r.balls, coalesce(c.balls, 0)
        from player_season_rating r
        join people p on p.person_id = r.person_id
        left join counted c
          on c.fs = r.franchise_season_id and c.pid = r.person_id
         and c.discipline = r.discipline
        where coalesce(c.balls, 0) is distinct from r.balls
    """)
    for name, year, discipline, stored, recounted in mismatches:
        offenders.append(
            f"{name} {year} {discipline}: rating holds {stored} balls, this "
            f"franchise-season alone yields {recounted}")

    # A57's blend must stay inside its own endpoints - only where there is a `merit` to
    # bound it. A71's reputation-only rows have no in-season merit at all (NULL), so this
    # bound does not apply to them and must not silently wave them through either; they are
    # checked explicitly below instead.
    escaped = _rows(conn, """
        select distinct p.primary_name, r.season_year,
               r.merit, r.career_merit, r.blended_merit
        from player_season_rating r
        join people p on p.person_id = r.person_id
        where r.prior_source is distinct from 'reputation_floor'
          and (r.blended_merit < least(r.merit, r.career_merit) - 1e-9
           or r.blended_merit > greatest(r.merit, r.career_merit) + 1e-9)
    """)
    for name, year, merit, career, blended in escaped:
        offenders.append(
            f"{name} {year}: blended {float(blended):.3f} is outside "
            f"[{float(min(merit, career)):.3f}, {float(max(merit, career)):.3f}] - the "
            f"career term is no longer shrinkage")

    # [A71] The reputation-only branch, checked on its own terms instead of by omission.
    reputation_rows = _rows(conn, """
        select p.primary_name, r.season_year, r.career_merit, r.blended_merit
        from player_season_rating r
        join people p on p.person_id = r.person_id
        where r.prior_source = 'reputation_floor'
    """)
    zero_career_values = set()
    for name, year, career_merit, blended in reputation_rows:
        if career_merit is not None:
            if abs(float(blended) - float(career_merit)) > 1e-9:
                offenders.append(
                    f"{name} {year}: reputation-only blended {float(blended):.4f} does "
                    f"not equal his own career merit {float(career_merit):.4f}")
        else:
            zero_career_values.add(round(float(blended), 9))
    if len(zero_career_values) > 1:
        offenders.append(
            f"players with no career anywhere landed on {len(zero_career_values)} "
            f"different floor values instead of one shared scale floor: "
            f"{sorted(zero_career_values)}"
        )

    (rated,), = _rows(conn, "select count(*) from player_season_rating")
    return verdict(
        9, title,
        f"{rated:,} rating rows recounted from their own franchise-season; every blend "
        f"inside its own endpoints or the declared A71 prior",
        offenders,
    )


# Eras to test A2's pooling assumption against. The 2023 boundary is the Impact Player
# rule, which SPEC 8.13 names as the specific thing that might have broken "finisher"
# meaning the same in 2010 and 2024; the earlier split is there so a slow drift with no
# rule change behind it is visible too.
ERAS = (("2008-2014", 2008, 2014), ("2015-2022", 2015, 2022), ("2023-2026", 2023, 2026))

# A cohort's era drift has to clear BOTH bars to fail. Real: three sampling standard
# errors, so noise off a thin cell cannot trip it. Material: 0.05 runs/ball, which is
# roughly the ENTIRE cohort spread A41 measured (0.049), so a drift smaller than that
# cannot justify splitting an offset it is already the same size as.
#
# MEASURED 2026-07-31: the material bar is currently INERT and the significance bar is
# what does the work. Every cohort sits below 3 SE - the closest are batting/opener at
# 0.89 of the bar (0.048 against 0.054) and batting/middle at 0.78 (0.055 against 0.071) -
# and lowering DRIFT_RUNS to 0.01 changes no verdict, while lowering DRIFT_SES to 0.5
# fails the check. Kept anyway, and named here rather than deleted, for the same reason
# check 6 keeps its inert `not is_super_over` clause: it states the second half of the
# rule, and the day a thick cohort drifts by a hair it is what stops a split being made
# on a difference too small to matter. Note batting/middle EXCEEDS the material bar
# already, so the two are not far from swapping which one binds.
DRIFT_SES = 3.0
DRIFT_RUNS = 0.05


def check_13_cohort_offsets_do_not_drift_by_era(conn) -> Result:
    """SPEC 8.13 / A2. Does a cohort mean the same thing in 2010 as in 2024?

    A43 pools each cohort offset across all nineteen seasons, which assumes `finisher` is
    one job throughout. The Impact Player rule is the obvious reason that might be false.

    Not a tautology, and worth saying why, because A2 deleted two checks for being one:
    `centred_per_ball` is centred within SEASON, so it is forced flat across seasons taken
    as a whole and is NOT forced flat within a cohort. A cohort drifting while the league
    it sits in does not is exactly the signal, and it survives the centring that makes the
    aggregate uninformative.

    Fails only when drift is both real and material (see DRIFT_SES, DRIFT_RUNS). The
    response to a failure is in the spec and is not to widen the bars: split that cohort's
    offset by era.
    """
    title = "pooled cohort offsets do not drift across eras"
    if not _table_exists(conn, "player_season_rating"):
        return skipped(13, title, "player_season_rating absent - apply migration 012")

    # [A68] Measured on the seasons the offsets are ESTIMATED from, not on every rated
    # row. Since A65 the view also carries thin seasons, whose band is derived from a
    # handful of innings; including them showed 0.090 runs/ball of "era drift" in
    # batting/finisher that vanished on the gate-passing seasons alone (worst ratio 1.65
    # against 0.90). That was thin seasons being unevenly spread across eras - more players
    # per season since the Impact Player rule - and splitting an offset by era on the
    # strength of it would have been fitting noise, which A2 refused twice.
    cells: dict[tuple, list[float]] = {}
    for discipline, cohort, year, centred in _rows(conn, """
        select discipline, cohort, season_year, centred_per_ball
        from player_season_rating
        where cohort is not null and not_rateable_reason is null
    """):
        for label, lo, hi in ERAS:
            if lo <= year <= hi:
                cells.setdefault((discipline, cohort, label), []).append(float(centred))

    offenders: list[str] = []
    worst = ("", 0.0)
    by_cohort: dict[tuple, list] = {}
    for (discipline, cohort, era), vals in cells.items():
        by_cohort.setdefault((discipline, cohort), []).append((era, vals))

    for (discipline, cohort), eras in sorted(by_cohort.items()):
        # A43's own evidence bar: a cohort too thin to earn an offset is too thin to
        # be tested for drift in it.
        usable = [(era, v) for era, v in eras if len(v) >= 20]
        if len(usable) < 2:
            continue
        means = {era: sum(v) / len(v) for era, v in usable}
        spread = max(means.values()) - min(means.values())
        pooled = [x for _, v in usable for x in v]
        sd = (sum((x - sum(pooled) / len(pooled)) ** 2 for x in pooled)
              / (len(pooled) - 1)) ** 0.5
        se = max(
            (sd * (1 / len(v)) ** 0.5 for _, v in usable), default=0.0)
        if spread > worst[1]:
            worst = (f"{discipline}/{cohort}", spread)
        if spread > DRIFT_SES * se and spread > DRIFT_RUNS:
            detail = ", ".join(f"{era} {m:+.3f}" for era, m in sorted(means.items()))
            offenders.append(
                f"{discipline}/{cohort}: era means drift {spread:.3f} runs/ball "
                f"({detail}); {DRIFT_SES:.0f} SE = {DRIFT_SES * se:.3f}. Split this "
                f"cohort's offset by era.")

    return verdict(
        13, title,
        f"{len(by_cohort)} cohorts tested over {len(ERAS)} eras; largest drift "
        f"{worst[1]:.3f} runs/ball ({worst[0]})",
        offenders,
    )


def check_15_actual_delivery_is_reproducible(conn) -> Result:
    """A19. `legal_ball` regenerates the source's own scorecard reference.

    The second of the two checks that can tell us we misread the source rather than
    merely contradicted ourselves. `actual_delivery` is the over.legal-ball label a
    scorecard prints, and A19 declines to store it precisely so it can be used this
    way: reproducing it exactly is a statement that our wide and no-ball
    classification agrees with Cricsheet's, tested against 295,732 independent
    assertions rather than against our own parser.

    The label is the *next* legal-ball index, which is why it repeats after a wide -
    the wide does not advance the count, so the following legal ball reuses the label.
    Those repeats are the informative part: get wides wrong and the duplicates land in
    the wrong places even though the count of them stays plausible.
    """
    title = "legal_ball reproduces the source's actual_delivery"
    if not ARCHIVE.exists():
        return skipped(15, title, f"{ARCHIVE} absent; run: uv run python -m etl.download")

    loaded = {m for (m,) in _rows(conn, "select match_id from matches")}
    if not loaded:
        return skipped(15, title, "no matches loaded")

    source: dict[tuple, str] = {}
    with zipfile.ZipFile(ARCHIVE) as zf:
        for name in zf.namelist():
            if not name.endswith(".json"):
                continue
            match_id = name[:-5]
            if match_id not in loaded:
                continue
            raw = json.loads(zf.read(name))
            for innings_no, innings in enumerate(raw.get("innings", []), start=1):
                is_super = bool(innings.get("super_over"))
                for over in innings.get("overs", []):
                    for ball_no, d in enumerate(over["deliveries"], start=1):
                        label = d.get("actual_delivery")
                        if label is not None:
                            key = (match_id, innings_no, is_super, over["over"], ball_no)
                            source[key] = label

    offenders: list[str] = []
    compared = 0
    legal_so_far = 0
    previous = None
    with conn.cursor(name="check_15") as cur:
        cur.itersize = 20_000
        cur.execute(
            """
            select match_id, innings_no, is_super_over, over_no, ball_no, legal_ball
            from deliveries
            order by match_id, innings_no, is_super_over, over_no, ball_no
            """
        )
        for match_id, innings_no, is_super, over_no, ball_no, legal in cur:
            over_key = (match_id, innings_no, is_super, over_no)
            if over_key != previous:
                legal_so_far = 0
                previous = over_key
            # The label always names the ball this one would be if it counted, so it
            # is read before the increment, not after.
            derived = f"{over_no}.{legal_so_far + 1}"
            if legal:
                legal_so_far += 1

            expected = source.pop((*over_key, ball_no), None)
            if expected is None:
                continue  # the source omits the field on this delivery
            compared += 1
            if derived != expected:
                offenders.append(
                    f"{match_id} inn{innings_no} ov{over_no} ball{ball_no}: "
                    f"derived {derived} vs source {expected}"
                )

    if not compared:
        return skipped(15, title, "the archive carries no actual_delivery field")
    return verdict(
        15, title, f"{compared} scorecard references regenerated from legal_ball",
        offenders,
    )


def check_16_scheduled_length_is_null_only_where_specified(conn) -> Result:
    """A17. Exactly six innings may have an unknown scheduled length.

    A null here is load-bearing: §7.1 filters on `innings_scheduled_balls = 120`, and
    a null fails that equality by construction, so an innings of unknown length falls
    out of the fitting set without anyone remembering to exclude it. That protection
    is only as good as the null being rare and accounted for. A seventh null would
    mean the A17 rule had quietly stopped matching the archive, and it would remove
    deliveries from the fit silently rather than loudly.

    Super overs are excluded from the count rather than listed as offenders: they have
    no scheduled length by nature, and 34 of them are null for that reason.
    """
    title = "scheduled length is null only for the six A17 innings"
    expected = {
        ("1136566", 1), ("1136592", 1), ("980989", 1),   # shortened, abandoned mid-over
        ("1473495", 1), ("1527685", 1), ("501265", 1),   # abandoned, no second innings
    }
    found = {
        (match_id, innings_no)
        for match_id, innings_no in _rows(
            conn,
            """
            select distinct match_id, innings_no from deliveries
            where innings_scheduled_balls is null and not is_super_over
            """,
        )
    }
    offenders = [f"{m} inn{i}: unknown length, not one of the six" for m, i in sorted(found - expected)]
    offenders += [f"{m} inn{i}: expected unknown, now has a length" for m, i in sorted(expected - found)]
    return verdict(
        16, title, f"{len(found)} innings of unknown scheduled length", offenders
    )


def check_23_career_positions_match_an_independent_aggregate(conn) -> Result:
    """A72. `person_batting_positions` is the aggregate it claims to be.

    Recomputed here in SQL, independent of `etl.career_positions`'s Python re-use of
    `etl.derive_squads.batting_positions()`: a first-appearance-wins scan of the union of
    `batter_id`/`non_striker_id` per innings, ordered by `(over_no, ball_no)`, super overs
    excluded (matching A4/A17). Two different code paths computing the same first-innings
    scan is what makes this a check rather than a restatement of the loader's own logic --
    a bug shared by both would still slip through, but a bug in only one of them (a
    plausible failure: the ETL's row-buffering, or a SQL window-function edge case) would
    not.
    """
    title = "person_batting_positions matches an independently recomputed aggregate"
    rows = _rows(
        conn,
        """
        -- [A4] On a tied first ball (ball 1: striker and non-striker both appear at
        -- once) the striker is position 1. `role_rank` (0 = batter, 1 = non-striker)
        -- carries that fact through the UNION, which plain (over_no, ball_no) loses --
        -- this is exactly the bug this independent recompute is supposed to catch.
        with events as (
            select match_id, innings_no, over_no, ball_no, batter_id as person_id,
                   0 as role_rank
            from deliveries where not is_super_over
            union all
            select match_id, innings_no, over_no, ball_no, non_striker_id, 1
            from deliveries where not is_super_over
        ),
        first_seen as (
            select distinct on (match_id, innings_no, person_id)
                   match_id, innings_no, person_id, over_no, ball_no, role_rank
            from events
            order by match_id, innings_no, person_id, over_no, ball_no, role_rank
        ),
        ranked as (
            select match_id, innings_no, person_id,
                   row_number() over (partition by match_id, innings_no
                                       order by over_no, ball_no, role_rank) as position
            from first_seen
        )
        select person_id, position, count(*) as innings
        from ranked
        group by 1, 2
        """,
    )
    recomputed = {(person_id, position): innings for person_id, position, innings in rows}
    stored = {
        (person_id, position): innings
        for person_id, position, innings in _rows(
            conn, "select person_id, position, innings from person_batting_positions",
        )
    }

    offenders = [
        f"{key}: stored {stored.get(key)}, recomputed {recomputed.get(key)}"
        for key in set(stored) | set(recomputed)
        if stored.get(key) != recomputed.get(key)
    ]

    # Named spot-checks: the archive facts A72's design was measured against, pinned
    # exactly rather than only trusted as an aggregate row count.
    spot_checks = {
        "SV Samson": {1: 27, 2: 18, 3: 94, 4: 36, 5: 3, 6: 6, 7: 1, 8: 1},
        "V Suryavanshi": {2: 23},
        "JJ Bumrah": {9: 3, 10: 18, 11: 12},
    }
    names = dict(_rows(conn, "select person_id, primary_name from people"))
    name_to_id = {name: pid for pid, name in names.items()}
    for name, expected in spot_checks.items():
        pid = name_to_id.get(name)
        if pid is None:
            offenders.append(f"{name}: not found in people")
            continue
        actual = {pos: n for (p, pos), n in stored.items() if p == pid}
        if actual != expected:
            offenders.append(f"{name}: expected {expected}, stored {actual}")

    (total,), = _rows(conn, "select count(*) from person_batting_positions")
    return verdict(
        23, title,
        f"{total:,} (person, position) rows checked against an independent recount, "
        f"{len(spot_checks)} named players pinned exactly",
        offenders,
    )


def check_24_batting_role_cascade_is_correctly_wired(conn) -> Result:
    """A76, supersedes A72/A73's widening check (same number, the mechanism it pinned no
    longer exists). `etl.feasibility.load_deck` computes each card's `positions` by
    calling `etl.batting_roles.batting_role` against three inputs pulled from the
    database. This checks that the WIRING -- which rows go in, which set comes out -- is
    right; the algorithm itself (the aggregate-band comparison, the boundary tie-break,
    the four-tier cascade) is `tests/test_batting_roles.py`'s job, independently and with
    no database at all, exactly as A26's `role_for` is owned by `tests/test_squads.py`
    rather than by a check here.

    Three claims, checked separately so a fix to one is never mistaken for a fix to the
    rest. First: every card's `positions` is exactly one of the four fixed
    `BATTING_ROLE_SLOTS` ranges -- nothing else can legally come out of the cascade.
    Second: recomputing `batting_role` fresh from `person_batting_positions`,
    `person_season_batting_positions` and `squad_members.role` -- pulled by separate
    queries from load_deck's own -- reproduces every card's `positions` exactly, which is
    what makes this an independence check rather than a restatement of load_deck's own
    logic. Third: a card with literally zero recorded career batting innings is always a
    bowler -- A26's `role_for` already requires having batted at least once to be tagged
    batter/allrounder/keeper, so if this ever fires it means that guarantee broke, not
    that this check found a new legitimate case.
    """
    from etl.batting_roles import MIN_INNINGS_FOR_ROLE, batting_role
    from etl.feasibility import BATTING_ROLE_SLOTS, load_deck

    title = "the batting-role cascade (A76) is wired correctly end to end"
    offenders = []

    career: dict[str, dict[int, int]] = {}
    for person_id, position, innings in _rows(
        conn, "select person_id, position, innings from person_batting_positions",
    ):
        career.setdefault(person_id, {})[position] = innings

    season: dict[tuple[int, str], dict[int, int]] = {}
    for fs_id, person_id, position, innings in _rows(
        conn,
        "select franchise_season_id, person_id, position, innings "
        "from person_season_batting_positions",
    ):
        season.setdefault((fs_id, person_id), {})[position] = innings

    squad_role = {
        (fs_id, person_id): role
        for fs_id, person_id, role in _rows(
            conn, "select franchise_season_id, person_id, role from squad_members",
        )
    }

    deck = load_deck(conn)
    all_cards = [c for cards in deck.cards_by_fs.values() for c in cards]

    valid_ranges = set(BATTING_ROLE_SLOTS.values())
    wrong_shape = [c for c in all_cards if c.positions not in valid_ranges]

    mismatches = []
    tier_counts = Counter()
    for c in all_cards:
        key = (c.fs_id, c.person_id)
        season_counts = season.get(key, {})
        career_counts = career.get(c.person_id, {})
        expected_band = batting_role(season_counts, career_counts, squad_role.get(key))
        if c.positions != BATTING_ROLE_SLOTS[expected_band]:
            mismatches.append(
                f"{c.name} {c.season_year}: got {sorted(c.positions)}, "
                f"expected {sorted(BATTING_ROLE_SLOTS[expected_band])}"
            )
        if sum(season_counts.values()) >= MIN_INNINGS_FOR_ROLE:
            tier_counts["season"] += 1
        elif sum(career_counts.values()) >= MIN_INNINGS_FOR_ROLE:
            tier_counts["career"] += 1
        elif squad_role.get(key) != "bowler" and career_counts:
            tier_counts["thin, not a bowler"] += 1
        else:
            tier_counts["tailender default"] += 1

    zero_career_evidence = [c for c in all_cards if not career.get(c.person_id)]
    zero_evidence_non_bowlers = [c for c in zero_career_evidence if c.role != "bowler"]

    if wrong_shape:
        offenders.append(
            f"{len(wrong_shape)} card(s) have a positions set outside the four fixed bands"
        )
    if mismatches:
        offenders.append(
            f"{len(mismatches)} card(s) disagree with an independent recompute: "
            + "; ".join(mismatches[:5])
        )
    if zero_evidence_non_bowlers:
        offenders.append(
            f"{len(zero_evidence_non_bowlers)} card(s) have zero career batting evidence "
            "but are not bowlers -- A26's role_for guarantee broke"
        )

    tier_summary = ", ".join(f"{n:,} via {tier}" for tier, n in tier_counts.most_common())
    return verdict(
        24, title,
        f"{len(all_cards):,} cards checked, every one reproduced from an independent "
        f"recompute of the raw tables ({tier_summary}); {len(zero_career_evidence)} "
        "cards have zero career batting evidence, all of them bowlers",
        offenders,
    )


def check_25_order_errors_agrees_with_the_forward_check(conn) -> Result:
    """A73. `order_errors` is the one independent, from-scratch verifier of a final
    twelve; the forward check inside `run_draft` builds one incrementally, pick by pick,
    committing each to a slot as it goes -- and the two must never disagree about what
    counts as legal.

    Drafts real squads via `run_draft` (the exact construction the game plays) and asserts
    `order_errors` reports NOTHING wrong with the `order`/`impact` it produced -- a forward
    check that quietly let an illegal arrangement through would be caught here, since
    `order_errors` shares no code with it. Then perturbs one concrete, real violation --
    swapping the position-1 and position-11 batters, which real career ranges almost never
    both permit -- and asserts the perturbed arrangement is named illegal for a position
    reason. The unit-level tests for `Card.positions`'s widening rule and
    `could_still_complete`'s count arithmetic live in `tests/test_twelve.py`; this check is
    deliberately the lighter, integration-flavoured half, against real archive data.
    """
    import random as _random

    from etl.feasibility import POLICIES, load_deck, order_errors, run_draft

    title = "order_errors agrees with the forward check's own construction"
    deck = load_deck(conn)
    offenders = []
    swap_tested = 0

    rng = _random.Random(11)
    for _ in range(30):
        result = run_draft(deck, POLICIES["rational"], rng, guarantee=True)
        if not result.completed:
            offenders.append("a draft failed to complete against the real deck")
            continue

        errors = order_errors(result.order, result.impact, result.picks)
        if errors:
            offenders.append(f"run_draft's own construction was called illegal: {errors}")

        order = list(result.order)
        order[0], order[10] = order[10], order[0]
        swapped_errors = order_errors(order, result.impact, result.picks)
        if order[0] is not None and order[10] is not None:
            swap_tested += 1
            if not any("cannot bat at" in e for e in swapped_errors):
                offenders.append(
                    f"swapping positions 1 and 11 ({order[10].name} <-> {order[0].name}) "
                    "was not caught as a position violation"
                )

    return verdict(
        25, title,
        f"30 real drafts checked; every run_draft construction independently confirmed "
        f"legal; {swap_tested} position-1/11 swaps tested for the perturbation",
        offenders,
    )


CHECKS = (
    check_01_runs_total,
    check_02_participants_are_recorded,
    check_03_legal_balls_per_over,
    check_04_two_franchise_seasons,
    check_05_no_squad_member_without_appearances,
    check_06_super_overs_excluded,
    check_08_career_leaderboards,
    check_12_the_draft_can_always_complete,
    check_15_actual_delivery_is_reproducible,
    check_16_scheduled_length_is_null_only_where_specified,
    check_17_balls_faced_convention,
    check_18_batting_order_is_complete,
    check_19_every_squad_can_field_a_keeper,
    check_20_state_model_covers_every_state,
    check_21_no_real_xi_fielded_five_overseas,
    check_22_the_card_and_the_engine_agree,
    check_09_no_rating_leaks_across_seasons,
    check_13_cohort_offsets_do_not_drift_by_era,
    check_23_career_positions_match_an_independent_aggregate,
    check_24_batting_role_cascade_is_correctly_wired,
    check_25_order_errors_agrees_with_the_forward_check,
)
