"""Freeze each player's phase lean to a file, for Build a Career. [A248]

    uv run python -m tools.snapshot_phase            # write data/phase_skill.json.gz
    uv run python -m tools.snapshot_phase --check    # exit 1 if it disagrees with the DB

The same treatment as `tools.snapshot_facts` (A182): no database at runtime, and a gate
for a snapshot that loads perfectly yet disagrees with the archive.
"""

from __future__ import annotations

import gzip
import json
import os
import sys

import psycopg
from dotenv import load_dotenv

from etl import phase_skill
from game.phase_skill import FORMAT, SNAPSHOT, read_document


def main(argv: list[str]) -> int:
    load_dotenv()
    with psycopg.connect(os.environ["DIRECT_URL"]) as conn:
        players = phase_skill.fetch(conn)
    if "--check" in argv:
        doc = read_document()
        if doc is None:
            print(f"no readable snapshot at {SNAPSHOT}; run: uv run python -m tools.snapshot_phase")
            return 1
        bad = [p for p in set(players) | set(doc["players"])
               if players.get(p) != doc["players"].get(p)]
        if bad:
            print(f"the phase snapshot disagrees with the database for {len(bad)} players, "
                  f"e.g. {sorted(bad)[:5]}; run: uv run python -m tools.snapshot_phase")
            return 1
        print(f"phase skill current: {len(players)} players")
        return 0
    body = json.dumps({"format": FORMAT, "players": players}, sort_keys=True).encode()
    with open(SNAPSHOT, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0) as fh:
            fh.write(body)
    print(f"wrote {SNAPSHOT.name}: {len(players)} players, {SNAPSHOT.stat().st_size / 1024:.0f} kB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
