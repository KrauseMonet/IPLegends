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


def check_18_batting_order_is_complete(conn) -> Result:
    """SPEC 6.4/A4. An innings that lost ten wickets used exactly eleven batters.

    The wicket count comes from `player_out_id`, which the batting-position rule never
    reads, so this predicts the size of the order from a column outside the rule
    rather than restating it. Dropping the `non_striker` scan, or letting an Impact
    Player entrant take a twelfth position, both break it.
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


CHECKS = (
    check_01_runs_total,
    check_02_participants_are_recorded,
    check_03_legal_balls_per_over,
    check_04_two_franchise_seasons,
    check_05_no_squad_member_without_appearances,
    check_06_super_overs_excluded,
    check_15_actual_delivery_is_reproducible,
    check_16_scheduled_length_is_null_only_where_specified,
    check_17_balls_faced_convention,
    check_18_batting_order_is_complete,
    check_19_every_squad_can_field_a_keeper,
)
