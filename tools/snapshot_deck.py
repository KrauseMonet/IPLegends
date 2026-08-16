"""Freeze the deck and the fitted model to a file, so booting needs no database. [A107]

WHY. `web/app.py`'s `lifespan` read the deck and the model from Neon on every cold start,
and that was **measured at 7.3s** locally (connect 1.7 + `load_deck` 4.4 + `load_model`
1.4) and 3-4s on production. It cannot be optimised in place: the `deliveries` aggregate
inside `load_deck` **executes in 106 ms server-side** and costs 620-1510 ms wall clock, so
the money is round-trip latency, not the query, and an index or a materialised table would
buy almost nothing. The only remedy is not contacting the database at boot at all.

Reading this file instead takes about **21 ms**, and it is 158 kB gzipped.

WHAT IS STORED, and what deliberately is not. The model is stored as its **query results**
and rebuilt through `game.simulator.build_model`, never as a built `Model`. `Grid` and
`Costs` are constructed objects carrying their own A37 fallback caches; freezing those
would freeze a derived thing, and a later change to the walk would be silently ignored by
every reader of the file. Cards ARE stored directly, because a `Card` is flat and already
carries the final answer (`positions`) that `etl.batting_roles` decided for it.

JSON, not pickle, and NOT for speed -- pickle is 15 ms faster and that is nothing against
a 7,500 ms baseline. Three reasons, all about safety:

- **A schema change fails loudly.** `Card(**fields)` raises `TypeError` the moment a field
  is added or renamed. Pickle would rebuild an object silently MISSING the new attribute
  and fail later somewhere unrelated, which is the exact class of bug this codebase keeps
  writing rules against.
- **It diffs.** A committed artefact nobody can read is a poor citizen in a repo this
  careful about auditability. `data/manifest.json` already set the precedent.
- No code-execution semantics on a file that gets deployed.

TWO WAYS THIS GOES STALE, one guard each:

- **Against the DATABASE** -- somebody re-runs `etl.impact --write` and forgets this step.
  This is the dangerous one, because the site would serve OLD RATINGS silently. Guarded by
  `--check` and, identically, by validation check 26: both call `compare()` below, so there
  is one comparison rather than two that can drift.
- **Against the CODE** -- a `Card` field is added. Guarded by construction, above, and the
  loader falls back to the database rather than failing.

    uv run python -m tools.snapshot_deck            # write data/deck_snapshot.json.gz
    uv run python -m tools.snapshot_deck --check    # exit 1 if it disagrees with the DB

Run it LAST in the refresh chain, after `etl.impact --write` and `etl.career_positions
--write`, because it freezes what those produce.
"""

from __future__ import annotations

import gzip
import json
import os
import pathlib
import sys
from dataclasses import fields

import psycopg
from dotenv import load_dotenv

from etl.feasibility import Card, Deck, load_deck
from game.simulator import Model, build_model, model_inputs

SNAPSHOT = pathlib.Path(__file__).resolve().parent.parent / "data" / "deck_snapshot.json.gz"

# Bumped only when the FILE FORMAT changes -- not when the data does. A loader seeing an
# unknown version falls back to the database rather than guessing at the layout.
FORMAT_VERSION = 1


def _card_doc(c: Card) -> dict:
    d = {f.name: getattr(c, f.name) for f in fields(c)}
    d["positions"] = sorted(d["positions"])      # frozenset is not JSON
    return d


def _card_from(d: dict) -> Card:
    # `Card(**d)` on purpose: an unknown or missing field raises TypeError here, which is
    # the schema guard. Never `for k in d: setattr(...)`, which would swallow both.
    return Card(**{**d, "positions": frozenset(d["positions"])})


def build_document(conn) -> dict:
    deck = load_deck(conn)
    return {
        "format_version": FORMAT_VERSION,
        "deck": {
            "fs_ids": list(deck.fs_ids),
            "cards_by_fs": {str(k): [_card_doc(c) for c in v]
                            for k, v in deck.cards_by_fs.items()},
        },
        "model_inputs": _sorted_inputs(model_inputs(conn)),
        "unrated": _unrated_doc(conn),
    }


def _sorted_inputs(inputs: dict) -> dict:
    """Row order out of Postgres is arbitrary without an ORDER BY, and none of these
    queries has one. It is stable in practice -- three consecutive `load_deck` calls
    returned identical order for all 166 franchise-seasons -- but stable-in-practice is
    not a contract, and `compare()` does an equality test, so a reordering would report a
    difference that is not one. Sorting makes both the file and the comparison
    order-independent. `build_model` is unaffected either way: every one of these lists is
    loaded straight into a dict.

    Worth knowing about `season_mean` specifically: it returns **163 rows for 38
    (discipline, season) pairs**, because `distinct` cannot collapse float representations
    that differ in the last bit. Measured, the spread within a group is at most **8.3e-17**
    -- so which one wins is arbitrary and unobservable, and the query's own comment ("the
    centring constant is `shrunk - centred` for ANY row of that season") is correct.
    """
    ROW_LISTS = ("state_ball_outcomes", "state_runs_remaining", "unrated_bat", "season_mean")
    # Named explicitly rather than "sort every list", which is what the first version did
    # and which is WRONG: `wide_extras` is four POSITIONAL scalars, not rows, and sorting
    # them reassigns their meanings -- it put `wide_rate` at 29.1 instead of 0.034. Caught
    # by `test_the_model_rebuilds_with_its_grids_intact`'s range assertion, which is
    # exactly the kind of cheap sanity bound that earns its place.
    return {k: (sorted(v, key=repr) if k in ROW_LISTS else v) for k, v in inputs.items()}


def _unrated_doc(conn) -> dict:
    """`web.app._load_unrated`'s own rows. Zero of them against the current archive (A71
    rates everyone), and stored anyway because the point of that query is to be a net for a
    revised archive -- a snapshot that dropped it would remove the net silently."""
    from web.app import _load_unrated
    return {str(k): v for k, v in _load_unrated(conn).items()}


def read_document() -> dict | None:
    """The stored document, or None if it is absent or unreadable. Never raises: every
    caller's correct response to a bad snapshot is to use the database instead."""
    try:
        with gzip.open(SNAPSHOT, "rt", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if doc.get("format_version") == FORMAT_VERSION else None


def deck_from(doc: dict) -> Deck:
    d = doc["deck"]
    return Deck(cards_by_fs={int(k): [_card_from(c) for c in v]
                             for k, v in d["cards_by_fs"].items()},
                fs_ids=[int(x) for x in d["fs_ids"]])


def model_from(doc: dict) -> Model:
    return build_model(doc["model_inputs"])


def unrated_from(doc: dict) -> dict:
    return {int(k): v for k, v in doc.get("unrated", {}).items()}


def compare(conn, doc: dict) -> list[str]:
    """Every way the snapshot disagrees with the database, as readable lines.

    Shared verbatim by `--check` and validation check 26, so the deploy gate and the
    validation suite cannot come to different conclusions about the same file -- this
    project has been bitten more than once by two implementations of one rule.
    """
    problems: list[str] = []
    if doc.get("format_version") != FORMAT_VERSION:
        return [f"format_version {doc.get('format_version')} != {FORMAT_VERSION}"]

    live = build_document(conn)

    stored_ids, live_ids = doc["deck"]["fs_ids"], live["deck"]["fs_ids"]
    if stored_ids != live_ids:
        problems.append(f"fs_ids differ: {len(stored_ids)} stored, {len(live_ids)} live")

    stored_cards, live_cards = doc["deck"]["cards_by_fs"], live["deck"]["cards_by_fs"]
    if stored_cards.keys() != live_cards.keys():
        only_s = sorted(stored_cards.keys() - live_cards.keys())
        only_l = sorted(live_cards.keys() - stored_cards.keys())
        problems.append(f"franchise-seasons differ: stored-only {only_s}, live-only {only_l}")
    for fs in sorted(stored_cards.keys() & live_cards.keys(), key=int):
        if stored_cards[fs] != live_cards[fs]:
            names = {c["person_id"] for c in stored_cards[fs]} ^ {c["person_id"] for c in live_cards[fs]}
            problems.append(
                f"fs {fs}: cards differ ({len(stored_cards[fs])} stored, "
                f"{len(live_cards[fs])} live"
                + (f", membership changed for {len(names)} person(s)" if names
                   else ", same players, changed values") + ")")

    for key in ("model_inputs", "unrated"):
        if doc.get(key) != live[key]:
            problems.append(f"{key} differs from the database")
    return problems


def _connect():
    load_dotenv()
    return psycopg.connect(os.environ["DIRECT_URL"])


def main(argv: list[str]) -> int:
    check = "--check" in argv
    with _connect() as conn:
        if check:
            doc = read_document()
            if doc is None:
                print(f"no readable snapshot at {SNAPSHOT}")
                print("run: uv run python -m tools.snapshot_deck")
                return 1
            problems = compare(conn, doc)
            if problems:
                print("the deck snapshot disagrees with the database:")
                for p in problems[:20]:
                    print(f"  {p}")
                if len(problems) > 20:
                    print(f"  ... and {len(problems) - 20} more")
                print("run: uv run python -m tools.snapshot_deck")
                return 1
            cards = sum(len(v) for v in doc["deck"]["cards_by_fs"].values())
            print(f"snapshot current: {cards} cards, {len(doc['deck']['fs_ids'])} "
                  f"franchise-seasons, {len(doc['model_inputs']['state_ball_outcomes'])} states")
            return 0

        doc = build_document(conn)

    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    # Sorted and indented so a regeneration produces a readable diff rather than one
    # reordered line -- the whole argument for JSON over pickle.
    body = json.dumps(doc, sort_keys=True, indent=1).encode()
    # GzipFile rather than gzip.open, for `mtime=0`: the gzip header otherwise embeds the
    # current time, so regenerating an UNCHANGED snapshot would produce a different file
    # every run and show up in git as a change that isn't one.
    with open(SNAPSHOT, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0) as fh:
            fh.write(body)
    cards = sum(len(v) for v in doc["deck"]["cards_by_fs"].values())
    print(f"wrote {SNAPSHOT.relative_to(SNAPSHOT.parent.parent)}: {cards} cards, "
          f"{len(doc['deck']['fs_ids'])} franchise-seasons, "
          f"{SNAPSHOT.stat().st_size / 1024:.0f} kB gzipped")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
