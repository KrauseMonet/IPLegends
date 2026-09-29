"""The room code and the rooms table must agree on what a room's status can be. [A139]

The room tests run on a fake connection that enforces no CHECK constraint, so a status the
database refuses passes every one of them -- which is how auction rooms shipped a status
('auctioning') that migration 019's constraint rejected on the first live start. This reads
the constraint straight out of the migrations and every status the code assigns straight
out of the source, and fails if the second is not inside the first.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
ROOM_CODE = [ROOT / "web" / f for f in ("rooms.py", "room_auction.py", "room_match.py")]


def allowed_statuses() -> set[str]:
    """The constraint as the LAST migration to define it left it."""
    found: set[str] | None = None
    for path in sorted((ROOT / "migrations").glob("*.sql")):
        text = path.read_text()
        for m in re.finditer(r"check\s*\(\s*status\s+in\s*\(([^)]*)\)\s*\)", text, re.I):
            found = set(re.findall(r"'([^']+)'", m.group(1)))
    assert found, "no status constraint found in the migrations"
    return found


def assigned_statuses() -> set[str]:
    """Every quoted word on a `.status = ...` line, so a conditional assignment
    (`room.status = "complete" if done else "auctioning"`) is read whole -- the first
    version matched only the literal straight after `=` and missed exactly that case."""
    out: set[str] = set()
    for path in ROOM_CODE:
        for line in path.read_text().splitlines():
            if re.search(r"\.status\s*=(?!=)", line):
                out |= set(re.findall(r"\"([a-z_]+)\"", line))
    return out


def test_every_status_the_room_code_sets_is_one_the_database_allows():
    assigned = assigned_statuses()
    assert {"lobby", "drafting", "auctioning", "complete", "failed"} <= assigned, \
        "the scan should find every status the rooms use; if not, the regex has drifted"
    assert assigned <= allowed_statuses()
