"""Freeze the puzzle facts to a file, so the puzzle games need no database. [A182]

    uv run python -m tools.snapshot_facts            # write data/puzzle_facts.json.gz
    uv run python -m tools.snapshot_facts --check    # exit 1 if it disagrees with the DB

The same treatment, for the same reasons, as `tools.snapshot_deck` (A107): a request for a
puzzle touches no database, and a snapshot that loads perfectly yet disagrees with the
archive is the failure `--check` exists for. Run it LAST in the refresh chain, after
`tools.snapshot_deck`, since it reads the deck that step freezes.
"""

from __future__ import annotations

import gzip
import json
import os
import sys

import psycopg
from dotenv import load_dotenv

from etl.feasibility import load_deck
from game import puzzle_facts


def _connect():
    load_dotenv()
    return psycopg.connect(os.environ["DIRECT_URL"])


def main(argv: list[str]) -> int:
    check = "--check" in argv
    with _connect() as conn:
        deck = load_deck(conn)
        if check:
            doc = puzzle_facts.read_document()
            if doc is None:
                print(f"no readable snapshot at {puzzle_facts.SNAPSHOT}")
                print("run: uv run python -m tools.snapshot_facts")
                return 1
            problems = puzzle_facts.compare(conn, deck, doc)
            if problems:
                print("the puzzle facts snapshot disagrees with the database:")
                for p in problems[:20]:
                    print(f"  {p}")
                if len(problems) > 20:
                    print(f"  ... and {len(problems) - 20} more")
                print("run: uv run python -m tools.snapshot_facts")
                return 1
            print(f"puzzle facts current: {len(doc['players'])} players")
            return 0
        doc = puzzle_facts.build_document(conn, deck)

    path = puzzle_facts.SNAPSHOT
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(doc, sort_keys=True, indent=1).encode()
    # mtime=0 so regenerating an unchanged snapshot is byte-identical (A107).
    with open(path, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0) as fh:
            fh.write(body)
    print(f"wrote {path.name}: {len(doc['players'])} players, "
          f"{path.stat().st_size / 1024:.0f} kB gzipped")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
