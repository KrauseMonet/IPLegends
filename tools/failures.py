"""Why players' requests failed, from client_failure_reports [A180].

    uv run python -m tools.failures             # the last 24 hours, local database
    tools/on-neon.sh uv run python -m tools.failures --hours 168   # production, a week

Reads a few hundred rows at most, so it is safe to run against Neon (unlike validation,
which reads `deliveries`).
"""

from __future__ import annotations

import argparse
import os

import psycopg
from dotenv import load_dotenv

from web.client_failures import summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()
    load_dotenv()
    with psycopg.connect(os.environ["DIRECT_URL"]) as conn:
        s = summary(conn, args.hours)
    print(f"last {s['hours']}h: {s['failures']} failed requests, {s['ok']} succeeded "
          f"({s['rate']:.2%} failed), from {s['reports']} reports")
    for title, key in (("by kind", "by_kind"), ("by request", "by_path"),
                       ("by page", "by_page"), ("browser's own network flag", "offline_flag")):
        if s[key]:
            print(f"\n{title}:")
            for k, n, avg_ms in s[key]:
                print(f"  {n:6}  avg wait {avg_ms or 0:>6} ms  {k}")


if __name__ == "__main__":
    main()
