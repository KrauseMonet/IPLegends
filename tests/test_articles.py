"""The articles at /articles: hand-written prose whose every number is the data's.

An article quotes a figure as `<span data-fig="key">text</span>`, and its tables sit between
`<!-- fig:key -->` markers. `tools.article_figures` recomputes both from the archive and writes
`tools/article_figures.json`; these tests read the COMMITTED pages and that file, so they need
no database. The way an article goes wrong quietly is a number edited by hand, a key that no
longer exists, or a table pasted rather than generated -- each fails here. (Whether the file
itself is current is `tools.article_figures --check`, a deploy gate that needs the database.)

Routing, the sitemap and the word floor are `tests/test_history.py`'s, which covers these pages
with the other written pages.
"""

from __future__ import annotations

import html
import json
import pathlib
import re

from tools import article_figures as af

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "web" / "static"
DOC = json.loads((ROOT / "tools" / "article_figures.json").read_text())
ARTICLES = sorted((STATIC / "articles").glob("*.html"))
SPAN = re.compile(r'<span data-fig="([a-z0-9_]+)">(.*?)</span>', re.S)


def test_there_are_articles():
    assert len(ARTICLES) >= 6


def test_every_quoted_figure_is_the_value_the_data_gives():
    seen = 0
    for p in ARTICLES:
        for key, text in SPAN.findall(p.read_text()):
            assert key in DOC["figures"], (p.name, key)
            assert text == DOC["figures"][key], (p.name, key, text, DOC["figures"][key])
            seen += 1
    assert seen >= 100, seen      # the articles really are built on figures


def test_every_table_is_the_one_the_tool_writes():
    found = set()
    for p in ARTICLES:
        t = p.read_text()
        for m in af.FIG_TABLE.finditer(t):
            key = m.group(2)
            assert key in DOC["tables"], (p.name, key)
            assert m.group(3) == DOC["tables"][key], (p.name, key)
            found.add(key)
        # a marker left unfilled, or opened and never closed, is a table that silently vanished
        assert t.count("<!-- fig:") == t.count("<!-- /fig:"), p.name
    assert found == set(DOC["tables"]), set(DOC["tables"]) ^ found


def test_no_placeholder_survives_into_a_page():
    for p in ARTICLES + [STATIC / "articles.html"]:
        assert "{{" not in p.read_text(), p.name


def test_the_hub_lists_every_article_and_nothing_else():
    hub = (STATIC / "articles.html").read_text()
    linked = set(re.findall(r'class="hx-card" href="/articles/([a-z-]+)"', hub))
    assert linked == {p.stem for p in ARTICLES}


def test_each_article_is_dated_and_carries_its_own_structured_data():
    for p in ARTICLES:
        t = p.read_text()
        ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', t, re.S).group(1))
        title = html.unescape(re.search(r"<title>(.*?) · Fine Leg XI</title>", t).group(1))
        assert ld["@type"] == "Article" and ld["datePublished"], p.name
        assert ld["mainEntityOfPage"] == f"https://fineleg.app/articles/{p.stem}", p.name
        assert f'<link rel="canonical" href="https://fineleg.app/articles/{p.stem}">' in t, p.name
        assert 'class="hx-byline"' in t, p.name
        assert ld["headline"] == title, (p.name, title)


def test_the_home_page_and_the_footer_point_to_the_articles():
    assert 'href="/articles"' in (STATIC / "index.html").read_text()
    assert 'href="/articles"' in (ROOT / "web" / "partials" / "footer.html").read_text()


def test_a_broken_claim_stops_regeneration():
    """The orderings and comparisons the prose states in words are checked when the figures
    are made; one that no longer holds must stop the tool, not pass quietly."""
    import pytest
    with pytest.raises(SystemExit, match="Narine"):
        af.check_claims({"Narine is the cheapest": False, "something true": True})
    af.check_claims({"something true": True})


def test_the_figure_formatting_helpers():
    assert af.signed(-46.8) == "−47" and af.signed(4.2) == "+4"
    assert af.pct(0.074) == "7%" and af.n(3337) == "3,337"
    assert af.phase_of(5) == "pp" and af.phase_of(6) == "mid" and af.phase_of(15) == "death"
