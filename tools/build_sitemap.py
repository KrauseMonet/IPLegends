"""Write `web/static/sitemap.xml` and `robots.txt`: every page worth a search engine's time.

    uv run python -m tools.build_sitemap           # write both
    uv run python -m tools.build_sitemap --check   # exit 1 if either is stale

Listed: the home page, the game and puzzle pages, the guides, the articles, the history pages
(`tools.build_history`) and the about/legal pages. Left out on purpose: a page that is only
someone's own state (profile, the Hall of Fame, admin, a room, a season in progress) and the offline card.
`/api/` is NOT disallowed in robots.txt: a crawler rendering a game page needs it.
Run it after `tools.build_history` and `tools.build_almanack`, which decide which pages exist.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "web" / "static"
ORIGIN = "https://fineleg.app"

PAGES = ["/", "/draft", "/auction", "/career", "/rooms", "/daily", "/puzzles", "/bingo", "/guess",
         "/common", "/xi", "/trio", "/flashback", "/records", "/almanack", "/almanack/players", "/almanack/records", "/almanack/grounds",
         "/almanack/head-to-head", "/almanack/auctions", "/seasons", "/teams", "/guides", "/articles",
         "/about", "/faq", "/contact", "/terms", "/privacy"]


def urls() -> list[str]:
    out = list(PAGES)
    for folder in ("guides", "articles", "seasons", "teams"):
        out += [f"/{folder}/{p.stem}" for p in sorted((STATIC / folder).glob("*.html"))]
    # [A315] only the player pages that are indexed: a noindex page has no place in a sitemap.
    for folder in ("players", "grounds", "auctions"):
        for p in sorted((STATIC / "almanack" / folder).glob("*.html")):
            if 'name="robots" content="noindex' not in p.read_text():
                out.append(f"/almanack/{folder}/{p.stem}")
    return out


def sitemap() -> str:
    rows = "".join(f"  <url><loc>{ORIGIN}{u}</loc></url>\n" for u in urls())
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + rows + "</urlset>\n")


def robots() -> str:
    lines = ["User-agent: *", "Allow: /"] + [f"Disallow: {p}" for p in ("/admin", "/profile", "/hall")]
    return "\n".join(lines) + f"\n\nSitemap: {ORIGIN}/sitemap.xml\n"


def main(check: bool = False) -> int:
    want = {STATIC / "sitemap.xml": sitemap(), STATIC / "robots.txt": robots()}
    stale = [p.name for p, t in want.items() if not p.exists() or p.read_text() != t]
    if check:
        print("stale: " + ", ".join(stale) if stale else f"sitemap current ({len(urls())} pages)")
        return 1 if stale else 0
    for p, t in want.items():
        p.write_text(t)
    print(f"sitemap written ({len(urls())} pages)")
    return 0


if __name__ == "__main__":
    sys.exit(main(check="--check" in sys.argv))
