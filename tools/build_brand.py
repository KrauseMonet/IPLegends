"""Build the served brand images from the one master logo. [A187]

    uv run python -m tools.build_brand           # rebuild web/static/ brand images
    uv run python -m tools.build_brand --check   # exit 1 if the build is stale

The master is `assets/brand/finelegxi-logo.webp`: the shield and the "finelegXI" wordmark on a
TRANSPARENT background, the wordmark in near-white so it reads on the site's navy. Everything
the site shows of its own identity is cut from it, so changing the logo is one file and one
command rather than eight images edited by hand and a text label baked into a picture:

    logo-emblem.webp         the shield alone, for the nav and anywhere a mark is wanted
    logo-full.webp           the shield and wordmark together, for large placements
    favicon-32/180/512.png   the shield on transparent, padded
    icon-192/384.png         the same, for the install manifest
    icon-maskable-512.png    the shield on the site's navy, inside the 80% safe zone, because
                             Android crops a maskable icon to a squircle
    og-image.png             1200x630 on navy, the full logo, for link previews

The old og-image HAD THE NAME BAKED IN as pixels ("The Legends Almanack"), which is why renaming
a site is a design task and not a find-and-replace; this one carries only the logo.

`--check` needs no image library: it compares the master's hash and each output's hash against
what `web/brand_files.json` recorded, so a master replaced without a rebuild, or an output
edited by hand, fails it. Run it with the other deploy gates.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MASTER = ROOT / "assets" / "brand" / "finelegxi-logo.webp"
STATIC = ROOT / "web" / "static"
MANIFEST = ROOT / "web" / "brand_files.json"

NAVY = (5, 11, 26)               # --bg, the site's background and the manifest's theme colour
# Where the shield and the wordmark sit in the 2000x667 master (measured from its alpha
# channel: three runs of content, the shield, a gap, then the letters).
SHIELD = (117, 128, 475, 538)
FULL = (117, 128, 1885, 538)
OUTPUTS = ["logo-emblem.webp", "logo-full.webp", "favicon-32.png", "favicon-180.png",
           "favicon-512.png", "icon-192.png", "icon-384.png", "icon-maskable-512.png",
           "og-image.png"]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def build() -> None:
    from PIL import Image  # dev-only dependency; --check never needs it

    master = Image.open(MASTER).convert("RGBA")
    shield = master.crop(SHIELD)
    full = master.crop(FULL)

    def fit(img, box_w, box_h):
        scale = min(box_w / img.width, box_h / img.height)
        return img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                          Image.LANCZOS)

    def tile(size, fraction, background=None):
        """The shield centred in a square, filling `fraction` of it."""
        canvas = Image.new("RGBA", (size, size), background or (0, 0, 0, 0))
        mark = fit(shield, round(size * fraction), round(size * fraction))
        canvas.alpha_composite(mark, ((size - mark.width) // 2, (size - mark.height) // 2))
        return canvas

    out = {}
    emblem = fit(shield, 400, 240)
    out["logo-emblem.webp"] = ("WEBP", emblem, {"quality": 92, "method": 6})
    big = fit(full, 1000, 400)
    out["logo-full.webp"] = ("WEBP", big, {"quality": 92, "method": 6})
    for name, size in (("favicon-32.png", 32), ("favicon-180.png", 180), ("favicon-512.png", 512),
                       ("icon-192.png", 192), ("icon-384.png", 384)):
        # A palette with transparency, as the old icons were: four flat colours need no more,
        # and a 512px true-colour PNG of them was 112 kB against 16 kB.
        out[name] = ("PNG", tile(size, 0.9).quantize(colors=48, method=Image.FASTOCTREE),
                     {"optimize": True})
    out["icon-maskable-512.png"] = ("PNG", tile(512, 0.62, NAVY + (255,)), {"optimize": True})
    og = Image.new("RGBA", (1200, 630), NAVY + (255,))
    mark = fit(full, 900, 360)
    og.alpha_composite(mark, ((1200 - mark.width) // 2, (630 - mark.height) // 2))
    out["og-image.png"] = ("PNG", og.convert("RGB"), {"optimize": True})

    record = {"master": digest(MASTER.read_bytes()), "files": {}}
    for name, (fmt, img, opts) in out.items():
        path = STATIC / name
        img.save(path, fmt, **opts)
        record["files"][name] = digest(path.read_bytes())
        print(f"wrote {name:<24}{img.width}x{img.height}  {path.stat().st_size // 1024} kB")
    MANIFEST.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")


def check() -> int:
    if not MANIFEST.exists():
        print("no web/brand_files.json: run  uv run python -m tools.build_brand")
        return 1
    record = json.loads(MANIFEST.read_text())
    problems = []
    if digest(MASTER.read_bytes()) != record["master"]:
        problems.append("the master logo changed since the images were built")
    for name in OUTPUTS:
        path = STATIC / name
        if not path.exists():
            problems.append(f"{name} is missing")
        elif digest(path.read_bytes()) != record["files"].get(name):
            problems.append(f"{name} does not match the build")
    for p in problems:
        print(p)
    if problems:
        print("run: uv run python -m tools.build_brand")
        return 1
    print(f"brand images current ({len(OUTPUTS)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(check() if "--check" in sys.argv else (build() or 0))
