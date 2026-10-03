"""Give a LOCAL database the same franchise_season_id values as Neon.

`franchise_season_id` is a serial, so its values record the order squads were first
loaded, not anything about the squad. Neon's came from loading 2016 first (stage 4c)
and the rest of the archive after, so a fresh local load numbers the same squads
differently. That matters well beyond tidiness: the deck deals by fs id, so a draft
seed replays to a different deal, the committed snapshot (A107) disagrees with the
database, and every override CSV keyed on fs id (keepers_by_season.csv) points at the
wrong squad -- check 19 drops to 7 of 166 covered.

Neon's numbering is recorded in keepers_by_season.csv itself, which lists every squad
as `franchise_season_id,squad` ("2008 Chennai Super Kings"). This reads that, empties
franchise_seasons (CASCADE: every table under it is rebuilt by the chain that follows),
and re-inserts the squads under Neon's ids. etl.load then upserts on
(franchise_id, season_year) and keeps them. Refuses to touch anything but localhost.

    uv run python -m tools.localdb_align_ids     # after one etl.load, before the next
"""

from __future__ import annotations

import csv
import sys
from urllib.parse import urlparse

from etl.db import REPO_ROOT, connect, direct_url

CSV = REPO_ROOT / "etl" / "overrides" / "keepers_by_season.csv"


def neon_ids() -> dict[int, tuple[int, str]]:
    """fs id -> (season_year, display_name), as Neon numbered them."""
    out: dict[int, tuple[int, str]] = {}
    with CSV.open(newline="") as f:
        for row in csv.DictReader(f):
            year, display = row["squad"].split(" ", 1)
            out[int(row["franchise_season_id"])] = (int(year), display)
    return out


def main() -> int:
    host = urlparse(direct_url()).hostname
    if host not in ("localhost", "127.0.0.1", "::1"):
        print(f"refusing: DIRECT_URL points at {host}, not a local database", file=sys.stderr)
        return 1
    wanted = neon_ids()
    with connect(direct=True) as conn, conn.cursor() as cur:
        cur.execute("select season_year, display_name, franchise_id from franchise_seasons")
        franchise_of = {(y, d): fid for y, d, fid in cur.fetchall()}
        missing = [v for v in wanted.values() if v not in franchise_of]
        if missing or len(franchise_of) != len(wanted):
            print(f"cannot align: {len(franchise_of)} local squads, {len(wanted)} in the "
                  f"CSV, unmatched {missing[:5]} -- run etl.load --all first", file=sys.stderr)
            return 1
        cur.execute("select franchise_season_id, season_year, display_name from franchise_seasons")
        if all(wanted.get(i) == (y, d) for i, y, d in cur.fetchall()):
            print("franchise_season ids already match Neon")
            return 0
        cur.execute("truncate franchise_seasons cascade")
        cur.executemany(
            "insert into franchise_seasons (franchise_season_id, franchise_id, season_year,"
            " display_name) values (%s, %s, %s, %s)",
            [(i, franchise_of[(y, d)], y, d) for i, (y, d) in sorted(wanted.items())],
        )
        cur.execute("select setval('franchise_seasons_franchise_season_id_seq', %s)",
                    (max(wanted),))
        conn.commit()
    print(f"re-numbered {len(wanted)} franchise_seasons to Neon's ids; reload to refill")
    return 0


if __name__ == "__main__":
    sys.exit(main())
