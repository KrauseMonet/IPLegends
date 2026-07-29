"""Team-name string -> stable franchise, from etl/overrides/franchises.csv.

SPEC 4.3: franchise identity is never inferred at runtime. Any team name not present
in the override file is a hard error, so a new or renamed side surfaces immediately
instead of being silently split into its own franchise.
"""

from __future__ import annotations

import csv
from functools import cache

from etl.db import REPO_ROOT

OVERRIDES = REPO_ROOT / "etl" / "overrides"
FRANCHISES_CSV = OVERRIDES / "franchises.csv"


def read_override_csv(path) -> list[dict[str, str]]:
    """Read an override CSV, skipping '#' comment lines above the header."""
    with path.open(newline="") as handle:
        lines = [line for line in handle if not line.lstrip().startswith("#")]
    return list(csv.DictReader(lines))


@cache
def franchise_map() -> dict[str, str]:
    if not FRANCHISES_CSV.exists():
        raise RuntimeError(f"{FRANCHISES_CSV} not found.")
    rows = read_override_csv(FRANCHISES_CSV)
    return {r["team_name"]: r["canonical_franchise"] for r in rows}


def canonical(team_name: str) -> str:
    try:
        return franchise_map()[team_name]
    except KeyError:
        raise RuntimeError(
            f"Team name {team_name!r} is not in {FRANCHISES_CSV.name}. Add it by hand "
            f"after confirming which franchise it belongs to. Do not guess."
        ) from None
