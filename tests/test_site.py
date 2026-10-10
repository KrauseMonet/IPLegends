"""The site's identity (A187): its name, its address and its images are each in ONE place, and
nothing user-facing still carries the old name.

A rename is the kind of change that is finished in 95% of places and silently wrong in the rest:
a share line pointing at a dead address, a page title with the old name, an install icon whose
declared size is a lie. Each is a test.
"""

from __future__ import annotations

import json
import pathlib
import re
import struct

from game import site
from tools import build_brand

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "web" / "static"
PARTIALS = ROOT / "web" / "partials"
PAGES = sorted(STATIC.glob("*.html"))


def text(path) -> str:
    return pathlib.Path(path).read_text()


def test_the_name_and_address():
    assert site.SITE_NAME == "Fine Leg XI"
    assert site.SITE_HOST == "finelegxi.in"
    assert site.SITE_ORIGIN == "https://finelegxi.in"


def test_the_browser_and_python_agree_on_the_address():
    """The one copy of the address in the browser must equal the Python one."""
    js = re.search(r"const SITE_ORIGIN = '([^']+)'", text(STATIC / "common.js")).group(1)
    assert js == site.SITE_ORIGIN


def test_the_scripts_that_build_links_use_the_shared_origin_not_their_own():
    assert "const PZ_ORIGIN = SITE_ORIGIN;" in text(STATIC / "puzzle.js")
    assert "const QZ_SHARE_ORIGIN = SITE_ORIGIN;" in text(STATIC / "flashback.js")
    for js in STATIC.glob("*.js"):
        assert not re.search(r"https?://[a-z0-9.-]*vercel\.app", text(js)), js.name


def test_every_share_line_points_at_the_site_and_names_it():
    from game import bingo, ground, guess, xi
    from web import daily
    for module, path in ((bingo, "bingo"), (guess, "guess"), (ground, "common"), (xi, "xi")):
        assert module.SHARE_HOST == f"{site.SITE_HOST}/{path}"
    assert daily.SHARE_URL == f"{site.SITE_HOST}/daily"


def test_nothing_user_facing_still_carries_the_old_name():
    """The old name or address anywhere a visitor could meet it: pages, scripts, the manifest,
    the shared partials and the Python that builds share text. Internal names (the database,
    the `iplegends_*` storage keys) are deliberately not renamed and are not user-facing."""
    files = (list(STATIC.glob("*.html")) + list(STATIC.glob("*.js")) + [STATIC / "manifest.webmanifest"]
             + list(PARTIALS.glob("*.html")) + list((ROOT / "game").glob("*.py"))
             + list((ROOT / "web").glob("*.py")))
    for f in files:
        body = text(f)
        assert not re.search(r"almanack", body, re.I), f"{f.name} still says Almanack"
        assert "iplegends.vercel.app" not in body, f"{f.name} still names the old address"


def test_every_page_is_titled_with_the_new_name():
    for page in PAGES:
        title = re.search(r"<title>([^<]*)</title>", text(page)).group(1)
        assert title.endswith("Fine Leg XI") or title.startswith("Fine Leg XI"), (page.name, title)
        body = text(page)
        if page.name != "offline.html":
            assert '<meta property="og:title" content="Fine Leg XI">' in body, page.name
            assert 'apple-mobile-web-app-title" content="Fine Leg XI"' in body, page.name


def test_link_previews_name_the_image_by_its_absolute_address():
    """A relative og:image is ignored by most crawlers, so a shared link showed no picture."""
    for page in PAGES:
        for tag in re.findall(r'<meta (?:property|name)="(?:og|twitter):image" content="([^"]+)"', text(page)):
            assert tag == f"{site.SITE_ORIGIN}/static/og-image.png", (page.name, tag)


def test_the_masthead_and_the_installed_app_use_the_name():
    nav = text(PARTIALS / "nav.html")
    assert "<span>Fine Leg <b>XI</b></span>" in nav
    manifest = json.loads(text(STATIC / "manifest.webmanifest"))
    assert manifest["name"] == "Fine Leg XI"
    assert manifest["short_name"] == "Fine Leg XI" and len(manifest["short_name"]) <= 12


def png_size(path) -> tuple[int, int]:
    head = pathlib.Path(path).read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


def test_every_icon_the_manifest_declares_exists_at_the_size_it_declares():
    manifest = json.loads(text(STATIC / "manifest.webmanifest"))
    assert manifest["icons"]
    for icon in manifest["icons"]:
        w, h = (int(n) for n in icon["sizes"].split("x"))
        path = STATIC / icon["src"].removeprefix("/static/")
        assert png_size(path) == (w, h), f"{path.name} is not {icon['sizes']}"


def test_the_og_image_has_the_size_link_previews_expect():
    assert png_size(STATIC / "og-image.png") == (1200, 630)


def test_the_brand_images_match_the_master_logo():
    """Same deploy gate as the crests: a master replaced without a rebuild, or an image edited
    by hand, fails here."""
    assert build_brand.check() == 0


def test_the_logo_and_favicons_are_stamped_so_a_returning_visitor_cannot_keep_the_old_ones():
    """Images are cached for 30 days under their own name, so a logo replaced in place would
    show the old one to every returning visitor until it expired. Each reference carries the
    file's content hash, in the pages AND the shared partials that are copied into them."""
    import hashlib
    for f in list(PAGES) + list(PARTIALS.glob("*.html")):
        for m in re.finditer(r'(?:src|href)="/static/((?:logo-[a-z]+|favicon-\d+)\.(?:webp|png))(\?v=([0-9a-f]+))?"', text(f)):
            assert m.group(3), f"{f.name}: {m.group(1)} is not stamped"
            want = hashlib.sha256((STATIC / m.group(1)).read_bytes()).hexdigest()[:10]
            assert m.group(3) == want, f"{f.name}: {m.group(1)} carries a stale stamp"
    assert 'logo-emblem.webp?v=' in text(PARTIALS / "nav.html")
