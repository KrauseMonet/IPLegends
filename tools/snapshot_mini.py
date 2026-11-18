"""Freeze the 2027 mini auction's starting point to a committed file [A297].

    uv run python -m tools.snapshot_mini           # write data/mini2027.json.gz
    uv run python -m tools.snapshot_mini --check   # exit 1 if it disagrees with its sources

The preset (`game.mini2027.build_preset`) is a pure function of the committed deck snapshot
and five override files -- the retained squads, the purses, our auction list, the birth
dates and where each player has batted outside the IPL (A302) -- so this needs no database and no network, and the file is byte-stable (A107: gzip
written with mtime 0, keys sorted). The app loads it at boot; `--check` is a deploy gate,
since a list edited without regenerating it would otherwise ship the old list silently.
"""
from __future__ import annotations

import gzip
import json
import pathlib
import sys

from etl.derive_people import OVERRIDES, read_override
from game import auction as au
from game.mini2027 import Preset, build_preset
from tools.snapshot_deck import _card_doc, _card_from, deck_from, read_document

SNAPSHOT = pathlib.Path(__file__).resolve().parent.parent / "data" / "mini2027.json.gz"
FORMAT_VERSION = 2      # [A298] adds each player's status


def build() -> Preset:
    return build_preset(deck_from(read_document()),
                        read_override(OVERRIDES / "retained_2027.csv"),
                        read_override(OVERRIDES / "auction_2027_list.csv"),
                        read_override(OVERRIDES / "purse_2027.csv"),
                        read_override(OVERRIDES / "birth_dates.csv"),
                        au.base_price,
                        read_override(OVERRIDES / "positions_2027.csv"))


def to_document(p: Preset) -> dict:
    return {
        "format_version": FORMAT_VERSION,
        "squads": {s: [_card_doc(c) for c in cs] for s, cs in sorted(p.squads.items())},
        "purse": p.purse, "squad_size": p.squad_size, "overseas_cap": p.overseas_cap,
        "lots": [[_card_doc(c), base, capped] for c, base, capped in p.lots],
        "ages": p.ages, "debutants": sorted(p.debutants), "notes": p.notes,
        "status": p.status,
    }


def from_document(d: dict) -> Preset:
    return Preset(
        squads={s: [_card_from(c) for c in cs] for s, cs in d["squads"].items()},
        purse=d["purse"], squad_size=d["squad_size"], overseas_cap=d["overseas_cap"],
        lots=[(_card_from(c), base, capped) for c, base, capped in d["lots"]],
        ages=d["ages"], debutants=frozenset(d["debutants"]), notes=d["notes"],
        status=d["status"],
    )


def encode(doc: dict) -> bytes:
    raw = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
    return gzip.compress(raw, mtime=0)


_CACHE: Preset | None = None


def load_preset() -> Preset | None:
    """The committed preset, or None when the file is missing or of another format."""
    global _CACHE
    if _CACHE is None and SNAPSHOT.exists():
        doc = json.loads(gzip.decompress(SNAPSHOT.read_bytes()))
        if doc.get("format_version") == FORMAT_VERSION:
            _CACHE = from_document(doc)
    return _CACHE


def main(argv: list[str]) -> int:
    data = encode(to_document(build()))
    if "--check" in argv:
        if not SNAPSHOT.exists() or SNAPSHOT.read_bytes() != data:
            print(f"{SNAPSHOT.name} is stale: run uv run python -m tools.snapshot_mini")
            return 1
        print(f"{SNAPSHOT.name} matches its sources")
        return 0
    SNAPSHOT.write_bytes(data)
    print(f"wrote {SNAPSHOT} ({len(data) / 1024:.0f} kB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
