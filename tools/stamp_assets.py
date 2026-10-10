"""Stamp a content hash onto every CSS/JS reference in the HTML pages.

WHY THIS EXISTS, from the bug that forced it. The pages are served with
`must-revalidate` so a deploy is picked up immediately, but CSS and JS were cached for
minutes. A returning visitor therefore got the NEW html and the OLD script, and on
2026-08-16 that combination shipped: fresh `index.html` called `renderDeckStats`, which
only existed in the fresh `common.js`, and every returning visitor's home page died with
`ReferenceError: renderDeckStats is not defined` until their cache expired. Nothing about
that was specific to one function -- ANY deploy that changes a script can break any page
that already assumed the change, so the fix has to be structural rather than a rule about
what may be edited together.

Versioning the URL makes the pairing impossible to break: the html carries the hash, so a
changed script is a changed URL and the browser cannot serve the old one for a new page.
That also lets `vercel.json` mark css/js `immutable` for a year instead of re-checking
every ten minutes -- strictly faster AND strictly safer, which is the rare case where the
cautious option is also the quick one.

The stamped html is COMMITTED rather than generated at deploy time, because this project
has no build pipeline and adding one to solve a caching bug would be the larger change.
Same treatment as `web/static/upi-qr.png`: a build artefact that lives in git, regenerated
by a command.

    uv run python -m tools.stamp_assets          # rewrite, print what changed
    uv run python -m tools.stamp_assets --check  # exit 1 if any stamp is stale

`--check` is the guard: run it before deploying and a forgotten stamp fails loudly instead
of shipping a page pinned to a script that no longer exists.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import sys

STATIC = pathlib.Path(__file__).resolve().parent.parent / "web" / "static"

# Matches src="/static/x.js" and href="/static/x.css", with or without an existing ?v=,
# so re-running is idempotent rather than accumulating query strings.
REF = re.compile(r'((?:src|href)="/static/([A-Za-z0-9_.\-/]+\.(?:js|css)))(\?v=[0-9a-f]+)?"')


def _hash(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:10]


def stamp(check: bool = False) -> int:
    stale: list[str] = []
    changed: list[str] = []

    for page in sorted(STATIC.glob("*.html")):
        text = page.read_text()

        def rewrite(m: re.Match) -> str:
            head, rel = m.group(1), m.group(2)
            target = STATIC / rel
            if not target.exists():
                # A reference to a file that is not there is a real error, not something
                # to paper over with a hash of nothing.
                raise SystemExit(f"{page.name}: references missing asset /static/{rel}")
            return f'{head}?v={_hash(target)}"'

        new = REF.sub(rewrite, text)
        if new != text:
            (stale if check else changed).append(page.name)
            if not check:
                page.write_text(new)

    if check:
        if stale:
            print("stale asset stamps in: " + ", ".join(stale))
            print("run: uv run python -m tools.stamp_assets")
            return 1
        print(f"all asset stamps current ({len(list(STATIC.glob('*.html')))} pages)")
        return 0

    print(f"stamped {len(changed)} page(s): {', '.join(changed) or '(already current)'}")
    return 0


if __name__ == "__main__":
    sys.exit(stamp(check="--check" in sys.argv))
