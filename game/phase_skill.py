"""Phase specialisation for Build a Career's own player [A248].

Reads the committed snapshot `tools.snapshot_phase` writes from the archive
(`etl.phase_skill`): for each real player, the shrunk runs a ball he scored (or saved) in the
powerplay, middle overs and death above his own average. Only the career's player ever
carries a lean into a match; every other player, in every mode, plays as before.
"""

from __future__ import annotations

import functools
import gzip
import json
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "phase_skill.json.gz"
FORMAT = 1
NONE = (0.0, 0.0, 0.0)


def read_document() -> dict | None:
    try:
        with gzip.open(SNAPSHOT, "rt") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if doc.get("format") == FORMAT else None


@functools.lru_cache(maxsize=1)
def table() -> dict[str, dict[str, list[float]]]:
    """{person: {"bat": [pp, mid, death], "bowl": [...]}}; empty if the file is missing,
    which means no lean for anybody -- the engine as it was, never a guess."""
    doc = read_document()
    return doc["players"] if doc else {}


def lean(person_id: str | None, disc: str) -> tuple[float, float, float]:
    row = table().get(person_id or "", {}).get(disc)
    return tuple(row) if row else NONE
