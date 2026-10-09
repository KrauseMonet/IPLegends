"""Request failures as each player's browser saw them [A180] -- migration 045.

The page records a failed request (a timeout, a dropped connection, or a 5xx) on the
device and sends a batch here the next time a request succeeds, because a request that
never arrived leaves no trace on the server and Vercel keeps its logs for about an hour.
`summary` is what `tools/failures.py` prints.
"""

from __future__ import annotations

import json

from psycopg.types.json import Json

KINDS = ("timeout", "network", "server")
MAX_PER_REPORT = 50
# Spam guard for an anonymous endpoint: past this many reports in the last hour, new ones
# are dropped. Far above anything real -- a full ten-seat room failing constantly would
# send a report per seat per successful request at most, and reports are batched.
MAX_REPORTS_PER_HOUR = 2000
KEEP_DAYS = 30


def record(conn, page: str, ok_count: int, failures: list[dict]) -> bool:
    """One row per report, in ONE round trip: the prune, the rate check and the insert are
    a single statement. True if stored, False if dropped by the spam guard."""
    row = conn.execute(
        """
        with pruned as (
            delete from client_failure_reports
             where received_at < now() - make_interval(days => %s)
        )
        insert into client_failure_reports (page, ok_count, failures)
        select %s, %s, %s
         where (select count(*) from client_failure_reports
                 where received_at > now() - interval '1 hour') < %s
        returning report_id
        """,
        (KEEP_DAYS, page[:100], max(0, ok_count), Json(failures[:MAX_PER_REPORT]),
         MAX_REPORTS_PER_HOUR),
    ).fetchone()
    return row is not None


def summary(conn, hours: int = 24) -> dict:
    """Failures over the last `hours`: totals, the rate against successful requests, and
    breakdowns by kind, by request path and by page."""
    reports, ok, n = conn.execute(
        """select count(*), coalesce(sum(ok_count), 0),
                  coalesce(sum(jsonb_array_length(failures)), 0)
             from client_failure_reports
            where received_at > now() - make_interval(hours => %s)""", (hours,)).fetchone()

    def breakdown(expr: str) -> list[tuple]:
        return conn.execute(
            f"""select {expr} as k, count(*), round(avg((f->>'ms')::numeric))
                  from client_failure_reports r, jsonb_array_elements(r.failures) f
                 where r.received_at > now() - make_interval(hours => %s)
                 group by 1 order by 2 desc limit 15""", (hours,)).fetchall()

    return {
        "hours": hours, "reports": reports, "ok": ok, "failures": n,
        "rate": n / (n + ok) if n + ok else 0.0,
        "by_kind": breakdown("f->>'kind'"),
        "by_path": breakdown("(f->>'method') || ' ' || (f->>'path')"),
        "by_page": breakdown("r.page"),
        "offline_flag": breakdown("case when (f->>'online')::boolean then 'online' "
                                  "else 'browser said offline' end"),
    }


def as_json(rows: list[tuple]) -> str:
    return json.dumps(rows, default=str)
