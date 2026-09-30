"""Team colours from one hue: the derivation shared by franchise crests and team kits.

    colours_from(rgb) -> {"team": "#...", "deep": "#...", "ink": "#..."}

    --team       the colour itself, lightness clamped so a stripe or glow reads
    --team-deep  22% of it over the site's navy -- the base a crest or badge sits on. A
                 crest is NEVER put on its own main colour: RCB gold on gold vanished.
    --team-ink   the colour lifted until it clears 4.5:1 against --team-deep, for text

Lives here rather than in `tools.build_crests` because two things now need it: the crest
build, which finds a crest's hue from its pixels, and `web.kit`, which starts from a hue a
player chose [A146]. One derivation, so a kit and a crest of the same hue carry the same
three shades and the two can never look like they come from different systems.

Pure -- no image library -- so the web app can import it.
"""

from __future__ import annotations

import colorsys

NAVY = (6, 13, 31)           # --bg in style.css


def luminance(c) -> float:
    def ch(x):
        x /= 255
        return x / 12.92 if x <= .03928 else ((x + .055) / 1.055) ** 2.4
    return .2126 * ch(c[0]) + .7152 * ch(c[1]) + .0722 * ch(c[2])


def contrast(a, b) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + .05) / (lo + .05)


def with_lightness(c, lightness: float) -> tuple[int, int, int]:
    h, _, s = colorsys.rgb_to_hls(*(x / 255 for x in c))
    return tuple(round(x * 255) for x in colorsys.hls_to_rgb(h, lightness, s))


def hex_rgb(h: str) -> tuple[int, int, int]:
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def colours_from(hue: tuple[float, float, float]) -> dict[str, str]:
    """team / deep / ink, as hex, from one RGB colour. See the module docstring."""
    lightness = colorsys.rgb_to_hls(*(x / 255 for x in hue))[1]
    team = with_lightness(hue, min(max(lightness, .42), .6))
    deep = tuple(round(team[i] * .22 + NAVY[i] * .78) for i in range(3))
    ink, lightness = team, colorsys.rgb_to_hls(*(x / 255 for x in team))[1]
    while contrast(ink, deep) < 4.5 and lightness < .92:
        lightness += .02
        ink = with_lightness(team, lightness)
    hexed = lambda c: "#%02x%02x%02x" % c
    return {"team": hexed(team), "deep": hexed(deep), "ink": hexed(ink)}
