"""Run the SPEC 8 checks.

    uv run python -m validation                 every check that exists
    uv run python -m validation --check 17      one check, by its SPEC 8 number
    uv run python -m validation --scorecards    check 7, for reading by hand
"""

from __future__ import annotations

import argparse
import sys

from etl.db import connect
from validation.checks import CHECKS
from validation.harness import report
from validation.scorecard import SCORECARD_MATCHES, print_scorecard


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", type=int, action="append", dest="only")
    parser.add_argument("--scorecards", action="store_true")
    parser.add_argument("--match", action="append", dest="matches")
    args = parser.parse_args(argv)

    with connect(direct=True) as conn:
        if args.scorecards or args.matches:
            for match_id in args.matches or SCORECARD_MATCHES:
                print_scorecard(conn, match_id)
            return 0

        # Selected by SPEC 8 number, taken from the function name, not by position in
        # CHECKS. The two stopped agreeing the moment the suite skipped from 6 to 17.
        available = {int(check.__name__.split("_")[1]): check for check in CHECKS}
        chosen = [
            check for number, check in sorted(available.items())
            if args.only is None or number in args.only
        ]
        if not chosen:
            raise SystemExit(
                f"No such check. Available: "
                f"{', '.join(str(n) for n in sorted(available))}."
            )
        return report([check(conn) for check in chosen])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
