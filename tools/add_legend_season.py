"""[A272] Keep one more season of a saved career as a friends'-auction legend.

A career brings its best-graded season (A225). This adds another, by year, to the same saved
career (`card.extra_legends`), and the lobby picker always lists it.

[A273] A career saved from now on keeps every season's legend card (`card.season_legends`),
so name it by its key:

    tools/on-neon.sh uv run python -m tools.add_legend_season --account 7 --year 2014 --key <64 hex>

A career saved before that kept only its best season; the others exist only in its token,
copied from the career page's browser storage,

    copy(JSON.parse(localStorage.iplegends_career_v1).token)

and passed in a file (`--token-file`). The token is matched to the saved career by its hash
(the natural key, A210), which proves it is that career without the production signing
secret: a career is only ever saved from a token the server signed. Add `--write` to store.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from game import career as C
from web import career_results, db
from web.career_routes import decode_payload


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--account", type=int, required=True)
    ap.add_argument("--year", type=int, required=True)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--key", help="a saved career's natural key (careers saved since A273)")
    src.add_argument("--token-file", type=Path)
    ap.add_argument("--write", action="store_true", help="store it (otherwise only shows it)")
    a = ap.parse_args(argv)
    load_dotenv()
    from tools.snapshot_deck import _card_from

    if a.key:
        with db.connection() as conn:
            if a.write:
                leg = career_results.add_extra_legend(conn, a.account, a.key, a.year)
            else:
                (legs,), = conn.execute(
                    "select coalesce(card -> 'season_legends', '[]') from career_results"
                    " where account_id = %s and natural_key = %s", (a.account, a.key)).fetchall()
                leg = next((x for x in legs if x["year"] == a.year), None)
        if leg is None:
            print(f"{a.year}: no saved season for that career", file=sys.stderr)
            return 1
        bat, bowl = C.legend_values(_card_from(leg["card"]))
        print(f"{leg['name']} {a.year} · {leg['role_label']} · {leg['rating']} · "
              f"bat {bat:+.1f} bowl {'—' if bowl is None else f'{bowl:+.1f}'} runs a match")
        print("added" if a.write else "(not written; pass --write)")
        return 0

    token = a.token_file.read_text().strip().strip('"')
    key = career_results.natural_key(token)
    seed, setup, checkpoint, partial = decode_payload(token.rsplit(".", 1)[0])
    from tools import snapshot_deck
    doc = snapshot_deck.read_document()
    deck, model = snapshot_deck.deck_from(doc), snapshot_deck.model_from(doc)
    career = C.play_career(deck, model, seed, setup, partial, resume=checkpoint)
    leg = career_results.legend(career, a.year)
    if leg is None or leg["year"] != a.year:
        print(f"{a.year}: no measured season in this career", file=sys.stderr)
        return 1
    bat, bowl = C.legend_values(_card_from(leg["card"]))
    print(f"{setup.name} {a.year} · {leg['role_label']} · {leg['rating']} · "
          f"bat {bat:+.1f} bowl {'—' if bowl is None else f'{bowl:+.1f}'} runs a match")
    if not a.write:
        print("(not written; pass --write)")
        return 0
    with db.connection() as conn:
        career_results.add_extra_legend(conn, a.account, key, a.year, career)
    print("added")
    return 0


if __name__ == "__main__":
    sys.exit(main())
