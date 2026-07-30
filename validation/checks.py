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

from etl.db import data_dir
from validation.harness import Result, bad, skipped, verdict

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


def _rows(conn, sql: str, params=None) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


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
    title = "super over deliveries are excluded from derived stat tables"
    present = {t for (t,) in _rows(
        conn,
        "select table_name from information_schema.tables where table_schema = 'public'",
    )}
    derived = sorted(present - BASE_TABLES)
    if not derived:
        (n,), = _rows(conn, "select count(*) from deliveries where is_super_over")
        return skipped(
            6, title,
            f"no derived stat table exists yet; SPEC 7 builds them. "
            f"{n} super over deliveries are flagged and waiting to be excluded.",
        )
    # Not a pass. A derived table exists and this check does not yet look at it,
    # which is precisely when a silent green would do damage.
    return bad(
        6, title, "a derived stat table exists but this check has not been written",
        [f"unexamined table: {t}" for t in derived],
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


CHECKS = (
    check_01_runs_total,
    check_02_participants_are_recorded,
    check_03_legal_balls_per_over,
    check_04_two_franchise_seasons,
    check_05_no_squad_member_without_appearances,
    check_06_super_overs_excluded,
    check_17_balls_faced_convention,
)
