"""The Almanack's pictures: charts and cards drawn into the page when it is built.

Every function here returns plain HTML or inline SVG, written into the static page, so a
reader with no script still sees the picture, and every figure a picture shows is also in
the page as text (a table, a sentence or the picture's own label) for a crawler and a
screen reader [A318]. `web/static/almanack.js` only animates what is already drawn.

Colours come from the crest classes (`crest-KEY`, A145): an element carrying one has
`--team`, `--team-deep` and `--team-ink` set, so a bar, a card or a dot takes its club's
colours with nothing chosen by hand. An element with no club falls back to the site gold.

Pure: no database, no file reads, so each picture is tested on hand-built numbers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from tools.build_history import esc

CARD_LO, CARD_HI = 72, 99          # the card scale (A267)
KIND_COLOUR = {"caught": "#d8b95a", "bowled": "#e5484d", "lbw": "#3e8ef0", "run out": "#2bb3a3",
               "stumped": "#a86ef0", "caught and bowled": "#f08a3e"}
OTHER_COLOUR = "#7d8699"


def crest_cls(key: str | None) -> str:
    return f"crest-{key}" if key else ""


def nice_max(v: float) -> float:
    """The smallest 1, 2 or 5 times a power of ten at or above `v`, for a chart's top line."""
    if v <= 0:
        return 1
    p = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 5, 10):
        if m * p >= v:
            return m * p
    return 10 * p


def _fmt(v: float) -> str:
    return f"{int(v):,}" if float(v).is_integer() else f"{v:,.1f}"


# --------------------------------------------------------------------------- a career, season by season

@dataclass
class SeasonPoint:
    year: int
    key: str | None        # crest key of the club that season
    team: str
    runs: int
    wickets: int
    card: int | None


def career_chart(points: list[SeasonPoint], metrics: list[str]) -> str:
    """One bar a season in the club's colours, for each metric in `metrics` ("runs",
    "wickets"), the first shown and any second behind a switch; the season's card as a dot
    on its own 72-99 scale. Empty for fewer than two seasons, where a chart says nothing a
    tile does not."""
    if len(points) < 2 or not metrics:
        return ""
    slot, left, right, top, ph, bottom = 34, 40, 34, 22, 150, 28
    width = max(380, left + slot * len(points) + right)
    height = top + ph + bottom
    base = top + ph
    groups = []
    for i, metric in enumerate(metrics):
        vals = [p.runs if metric == "runs" else p.wickets for p in points]
        top_v = nice_max(max(vals))
        parts = []
        for j, (p, v) in enumerate(zip(points, vals)):
            x = left + slot * j + 6
            h = ph * v / top_v
            unit = "runs" if metric == "runs" else ("wicket" if v == 1 else "wickets")
            card = f", card {p.card}" if p.card else ""
            parts.append(
                f'<g class="al-b {crest_cls(p.key)}"><title>{p.year} · {esc(p.team)} · {v:,} {unit}{card}</title>'
                f'<rect x="{x}" y="{base - h:.1f}" width="{slot - 12}" height="{max(h, 0.5):.1f}" rx="3"/>'
                + (f'<text class="al-bv" x="{x + (slot - 12) / 2:.1f}" y="{base - h - 4:.1f}">{v:,}</text>' if v else "")
                + "</g>")
        grid = "".join(f'<line class="al-grid" x1="{left}" x2="{width - right}" y1="{base - ph * f:.1f}" '
                       f'y2="{base - ph * f:.1f}"/><text class="al-ax" x="{left - 6}" y="{base - ph * f + 3:.1f}">'
                       f'{_fmt(top_v * f)}</text>' for f in (0, 0.5, 1))
        hide = "" if i == 0 else ' style="display:none"'
        groups.append(f'<g class="al-series" data-metric="{metric}"{hide}>{grid}{"".join(parts)}</g>')
    years = "".join(f'<text class="al-yr" x="{left + slot * j + slot / 2:.1f}" y="{base + 16}">'
                    f"&#8217;{p.year % 100:02d}</text>" for j, p in enumerate(points))
    dots, line = [], []
    for j, p in enumerate(points):
        if not p.card:
            continue
        x = left + slot * j + slot / 2
        y = top + ph * (1 - (p.card - CARD_LO) / (CARD_HI - CARD_LO))
        line.append(f"{x:.1f},{y:.1f}")
        dots.append(f'<circle class="al-dot" cx="{x:.1f}" cy="{y:.1f}" r="4.5"><title>{p.year}: card {p.card}</title></circle>')
    cards = ""
    if dots:
        cards = (f'<polyline class="al-cline" points="{" ".join(line)}"/>{"".join(dots)}'
                 f'<text class="al-ax al-ax-r" x="{width - right + 6}" y="{top + 3}">{CARD_HI}</text>'
                 f'<text class="al-ax al-ax-r" x="{width - right + 6}" y="{base + 3}">{CARD_LO}</text>')
    label = {"runs": "Runs", "wickets": "Wickets"}
    tabs = ""
    if len(metrics) > 1:
        tabs = ('<div class="al-switch" role="group" aria-label="Show">'
                + "".join(f'<button type="button" data-metric="{m}" aria-pressed="{"true" if i == 0 else "false"}">'
                          f"{label[m]}</button>" for i, m in enumerate(metrics)) + "</div>")
    first = label[metrics[0]].lower()
    return (f'<figure class="al-chart">{tabs}'
            f'<div class="al-chart-scroll"><svg class="al-career" viewBox="0 0 {width} {height}" width="{width}" '
            f'height="{height}" role="img" aria-label="{first.capitalize()} each season, with the season\'s card">'
            f'{"".join(groups)}{years}{cards}</svg></div>'
            f'<figcaption><span class="al-key al-key-bar"></span>{"Bars" if len(metrics) == 1 else "Bars"}: '
            f'{" or ".join(label[m].lower() for m in metrics)} each season, in that season\'s club colours. '
            + ('<span class="al-key al-key-dot"></span>Dots: the season\'s card, 72-99.' if dots else "")
            + "</figcaption></figure>")


# --------------------------------------------------------------------------- above or below par

def par_bars(rows: list[tuple[str, float]], unit: str) -> str:
    """One bar per (label, value) either side of a par line: right and green for better
    than the league, left and red for worse. `value` is already signed so that positive is
    good. The scale is the largest value shown, never less than 10, so a tiny edge does
    not fill the bar."""
    rows = [(k, v) for k, v in rows if v is not None]
    if not rows:
        return ""
    top = max(10.0, max(abs(v) for _, v in rows))
    out = []
    for k, v in rows:
        side = "up" if v >= 0 else "down"
        w = 50 * abs(v) / top
        out.append(f'<div class="al-par-row"><span class="al-par-k">{esc(k)}</span>'
                   f'<span class="al-par-t"><i class="{side}" style="width:{w:.1f}%"></i></span>'
                   f'<span class="al-par-v {side}">{"+" if v >= 0 else "−"}{abs(v):.1f}</span></div>')
    return (f'<div class="al-par" role="img" aria-label="{esc(unit)}"><div class="al-par-head">'
            f'<span>Worse than the league</span><span>Par</span><span>Better</span></div>{"".join(out)}</div>')


# --------------------------------------------------------------------------- shares as a ring

def donut(items: list[tuple[str, int]], centre: str, centre_label: str) -> str:
    """A ring of shares with its legend, which carries every number the ring shows."""
    total = sum(n for _, n in items)
    if not total:
        return ""
    arcs, legend, at = [], [], 0.0
    for name, n in items:
        share = 100 * n / total
        colour = KIND_COLOUR.get(name.lower(), OTHER_COLOUR)
        arcs.append(f'<circle r="15.9155" cx="21" cy="21" fill="none" stroke="{colour}" stroke-width="5.2" '
                    f'stroke-dasharray="{share:.3f} {100 - share:.3f}" stroke-dashoffset="{25 - at:.3f}">'
                    f"<title>{esc(name)}: {n} ({share:.0f}%)</title></circle>")
        legend.append(f'<li><i style="background:{colour}"></i><span>{esc(name)}</span><b>{n}</b>'
                      f'<em>{share:.0f}%</em></li>')
        at += share
    return (f'<div class="al-donut"><svg viewBox="0 0 42 42" role="img" aria-label="{esc(centre_label)}">'
            f'<circle r="15.9155" cx="21" cy="21" fill="none" stroke="var(--line)" stroke-width="5.2"/>'
            f'{"".join(arcs)}<text x="21" y="21.5" class="al-donut-n">{esc(centre)}</text>'
            f'<text x="21" y="27" class="al-donut-l">{esc(centre_label)}</text></svg>'
            f'<ul>{"".join(legend)}</ul></div>')


# --------------------------------------------------------------------------- cards

@dataclass
class RecordCard:
    label: str
    value: str             # the big figure, e.g. "175*"
    who: str               # plain text, escaped here
    detail: str            # plain text, escaped here
    key: str | None        # crest key
    href: str | None
    crest: str | None = None   # crest image URL


def record_wall(cards: list[RecordCard]) -> str:
    """Each card is one link to the holder's page, in the colours of the club it was done
    for: the figure large, the holder and the occasion under it."""
    out = []
    for c in cards:
        tag = "a" if c.href else "div"
        href = f' href="{c.href}"' if c.href else ""
        img = (f'<img class="al-rec-crest" src="{c.crest}" alt="" width="64" height="64" loading="lazy">'
               if c.crest else "")
        out.append(f'<{tag} class="al-rec {crest_cls(c.key)}"{href}>{img}<em>{esc(c.label)}</em>'
                   f'<b class="al-num">{esc(c.value)}</b><span>{esc(c.who)}</span><small>{esc(c.detail)}</small></{tag}>')
    return f'<div class="al-wall">{"".join(out)}</div>'


@dataclass
class BoardRow:
    name_html: str         # already a link, built by the caller
    value: int
    shown: str             # the value as printed
    detail: str            # plain text
    key: str | None
    crest: str | None


def board(rows: list[BoardRow], what: str) -> str:
    """A leaderboard as bars: each row's bar is its share of the leader's figure."""
    if not rows:
        return ""
    top = max(r.value for r in rows) or 1
    out = []
    for i, r in enumerate(rows, 1):
        img = f'<img src="{r.crest}" alt="" width="26" height="26" loading="lazy">' if r.crest else "<span></span>"
        out.append(f'<li class="{crest_cls(r.key)}"><span class="al-rank">{i}</span>{img}'
                   f'<span class="al-who">{r.name_html}<small>{esc(r.detail)}</small></span>'
                   f'<span class="al-bd-t"><i style="width:{100 * r.value / top:.1f}%"></i></span>'
                   f'<b>{esc(r.shown)}</b></li>')
    return f'<ol class="al-board" aria-label="{esc(what)}">{"".join(out)}</ol>'


@dataclass
class RivalCard:
    label: str
    name_html: str
    line: str              # plain text
    tone: str              # "bad" | "good"


def rivals(cards: list[RivalCard]) -> str:
    if not cards:
        return ""
    return '<div class="al-rivals">' + "".join(
        f'<div class="al-rival {c.tone}"><em>{esc(c.label)}</em><b>{c.name_html}</b><span>{esc(c.line)}</span></div>'
        for c in cards) + "</div>"


def meter(label: str, shown: str, frac: float) -> str:
    """A labelled bar 0-1, clamped; `shown` is the figure in words or digits."""
    f = min(1.0, max(0.0, frac))
    return (f'<div class="al-meter"><span>{esc(label)}</span><b>{esc(shown)}</b>'
            f'<span class="al-meter-t"><i style="width:{100 * f:.0f}%"></i></span></div>')


# --------------------------------------------------------------------------- an auction, price against card

@dataclass
class Buy:
    price: float           # in the auction's own unit
    card: int
    key: str | None
    label: str             # "Name · Team · price · card", plain text
    mark: str = ""         # "dear" | "value" | ""
    short: str = ""        # a short name to print beside a marked dot


def log_ticks(lo: float, hi: float) -> list[float]:
    """1, 2 and 5 times powers of ten between `lo` and `hi`, at most six of them."""
    out = []
    p = 10 ** math.floor(math.log10(lo))
    while p <= hi * 10:
        for m in (1, 2, 5):
            v = m * p
            if lo <= v <= hi:
                out.append(v)
        p *= 10
    while len(out) > 6:
        out = out[::2]
    return out


def scatter(buys: list[Buy], money) -> str:
    """Every buy that played: price along the bottom on a log scale (the dearest cost a
    hundred times the cheapest), the season's card up the side. `money` prints a price."""
    buys = [b for b in buys if b.price > 0 and b.card]
    if len(buys) < 5:
        return ""
    w, h, left, right, top, bottom = 720, 300, 40, 18, 16, 34
    lo, hi = min(b.price for b in buys), max(b.price for b in buys)
    lo, hi = lo / 1.15, hi * 1.15
    lc, hc = min(b.card for b in buys) - 1, CARD_HI + 1

    def x(v):
        return left + (w - left - right) * (math.log(v) - math.log(lo)) / (math.log(hi) - math.log(lo))

    def y(c):
        return top + (h - top - bottom) * (1 - (c - lc) / (hc - lc))

    grid = "".join(f'<line class="al-grid" x1="{x(t):.1f}" x2="{x(t):.1f}" y1="{top}" y2="{h - bottom}"/>'
                   f'<text class="al-ax al-ax-x" x="{x(t):.1f}" y="{h - bottom + 16}">{esc(money(t))}</text>'
                   for t in log_ticks(lo, hi))
    for c in range(math.ceil(lc / 5) * 5, hc + 1, 5):
        grid += (f'<line class="al-grid" x1="{left}" x2="{w - right}" y1="{y(c):.1f}" y2="{y(c):.1f}"/>'
                 f'<text class="al-ax" x="{left - 6}" y="{y(c) + 3:.1f}">{c}</text>')
    dots = []
    for b in sorted(buys, key=lambda b: b.mark != ""):
        r = 6 if b.mark else 4.2
        dots.append(f'<circle class="al-sdot {crest_cls(b.key)} {b.mark}" cx="{x(b.price):.1f}" cy="{y(b.card):.1f}" '
                    f'r="{r}"><title>{esc(b.label)}</title></circle>')
        if b.mark and b.short:
            anchor = "end" if x(b.price) > w - 120 else "start"
            dx = -9 if anchor == "end" else 9
            dots.append(f'<text class="al-slabel {b.mark}" x="{x(b.price) + dx:.1f}" y="{y(b.card) + 3:.1f}" '
                        f'text-anchor="{anchor}">{esc(b.short)}</text>')
    return (f'<figure class="al-chart"><div class="al-chart-scroll"><svg class="al-scatter" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}" role="img" aria-label="Price paid against the season\'s card">'
            f'{grid}{"".join(dots)}</svg></div><figcaption>Each dot is a buy who played that season: price along the '
            "bottom, card up the side, in the buying club's colours. Gold rings mark the dearest buys, green the best "
            "value. Tap or hover a dot for the player.</figcaption></figure>")
