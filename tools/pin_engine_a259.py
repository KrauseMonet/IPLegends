"""Freeze the engine's numbers as they were before A267, for the daily challenge. [A267]

    uv run python -m tools.pin_engine_a259          # write data/engine_a259.json.gz, once

A267 replays each season again (A160's rule) and so changes every card's engine numbers back.
Days played on A259's two-level numbers (from 2026-11-07 until A267 ships) keep them, from
this file. Everything below is `tools.pin_engine_a160`'s, word for word but the names.

A daily day is scored once and replayed from its stored state whenever its page is shown,
so it must keep the engine it was scored with (A203). A259 changed every card's four
engine numbers (`bat_scoring`, `bat_dismissal`, `bowl_scoring`, `bowl_dismissal`) and the
season average an unrated batter is centred on (`season_mean_scoring`). This writes them,
read from the COMMITTED deck snapshot as it stood before A259, to a file of their own.

A separate file on purpose, never part of the deck snapshot: `tools.snapshot_deck` rebuilds
that one from the database, which no longer knows the old numbers, so a pin carried inside it
would vanish on the next rebuild with nothing to say so. This file is written ONCE and the
tool refuses to overwrite it.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

from tools.snapshot_deck import read_document

PIN = Path(__file__).resolve().parent.parent / "data" / "engine_a259.json.gz"
FIELDS = ("bat_scoring", "bat_dismissal", "bowl_scoring", "bowl_dismissal")


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
    body = json.dumps({"format": 1, "fields": list(FIELDS), "cards": cards,
                       "season_mean_scoring": doc["model_inputs"]["season_mean_scoring"]},
                      sort_keys=True, separators=(",", ":")).encode()
    with open(PIN, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0) as fh:
            fh.write(body)
    n = sum(len(v) for v in cards.values())
    print(f"wrote {PIN.name}: {n} cards, {PIN.stat().st_size / 1024:.0f} kB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
