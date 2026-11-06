"""Freeze every card's batting leans as A260 centred them, for the daily challenge. [A268]

    uv run python -m tools.pin_leans_a260          # write data/leans_a260.json.gz, once

A268 re-centres each card's batting leans (`phase_bat`, `style_bat`, `burn`) on the balls
faced at the positions the card bats, where A260 centred them on the balls the player really
faced. A day played on A260's leans (from `SITUATIONS_FROM` until A268 ships) keeps them, from
this file -- read from the COMMITTED deck snapshot as it stood before A268.

A separate file, never part of the deck snapshot, for `tools.pin_engine_a259`'s reason: the
snapshot is rebuilt from the database, which no longer produces these values, so a pin
carried inside it would vanish on the next rebuild. Written ONCE; the tool refuses to
overwrite it.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

from tools.snapshot_deck import read_document

PIN = Path(__file__).resolve().parent.parent / "data" / "leans_a260.json.gz"
FIELDS = ("phase_bat", "style_bat", "burn")


def main() -> int:
    if PIN.exists():
        print(f"{PIN.name} already exists and is a frozen record; not overwriting it")
        return 1
    doc = read_document()
    cards = {}
    for fs, cs in doc["deck"]["cards_by_fs"].items():
        for c in cs:
            vals = [c.get(f) for f in FIELDS]
            if any(v is not None for v in vals):
                cards.setdefault(fs, {})[c["person_id"]] = vals
    body = json.dumps({"format": 1, "fields": list(FIELDS), "cards": cards},
                      sort_keys=True, separators=(",", ":")).encode()
    with open(PIN, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0) as fh:
            fh.write(body)
    n = sum(len(v) for v in cards.values())
    print(f"wrote {PIN.name}: {n} cards, {PIN.stat().st_size / 1024:.0f} kB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
