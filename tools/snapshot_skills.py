"""Freeze career skills and season roles to a file. [A258]

    uv run python -m tools.snapshot_skills            # write data/player_skills.json.gz
    uv run python -m tools.snapshot_skills --check    # exit 1 if it disagrees with the DB

Same treatment as `tools.snapshot_phase` (A248): no database at runtime, and a gate for a
snapshot that loads perfectly yet disagrees with the archive. Local database only (A173):
it reads every delivery into Python.
"""

from __future__ import annotations

import gzip
import json
import sys

from etl import skills
from etl.db import connect
from game.skills import FORMAT, SNAPSHOT, measure_blocks, read_document
from tools.snapshot_deck import deck_from
from tools.snapshot_deck import read_document as read_deck


def main(argv: list[str]) -> int:
    with connect() as conn:
        doc = {"format": FORMAT, **skills.build(skills.load(conn))}
    # The blocks are read off the COMMITTED deck, the cards players actually see (A107).
    deck = deck_from(read_deck())
    cards = [c for cs in deck.cards_by_fs.values() for c in cs]
    doc["blocks"] = {str(k): v for k, v in measure_blocks(cards, doc["skills"]).items()}
    body = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    if "--check" in argv:
        old = read_document()
        if old is None:
            print(f"no readable snapshot at {SNAPSHOT}; run: uv run python -m tools.snapshot_skills")
            return 1
        if json.dumps(old, sort_keys=True, separators=(",", ":")) != body:
            bad = [k for k in doc if doc[k] != old.get(k)]
            print(f"the skills snapshot disagrees with the database in {bad}; "
                  "run: uv run python -m tools.snapshot_skills")
            return 1
        print(f"skills current: {len(doc['skills'])} players with a skill, "
              f"{len(doc['roles'])} with a role")
        return 0
    with open(SNAPSHOT, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0) as fh:
            fh.write(body.encode())
    print(f"rating blocks: {doc['blocks']}")
    print(f"wrote {SNAPSHOT.name}: {len(doc['skills'])} players with a skill, "
          f"{SNAPSHOT.stat().st_size / 1024:.0f} kB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
