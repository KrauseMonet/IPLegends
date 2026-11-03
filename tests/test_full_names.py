"""A player is shown by the name a fan knows, not Cricsheet's initials (A252).

`full_names.csv` holds each player's English Wikipedia article title, found by cricinfo id
(Wikidata P2697), never by name. These tests pin what the file must be -- complete, unique,
sourced -- that the deck and the puzzles actually show it, and the two page helpers that
use it: the short form for tight spots and a search that finds either name.
"""

import csv
import gzip
import json
import pathlib
import re
import shutil
import subprocess

import pytest

from etl import derive_people

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSV = ROOT / "etl" / "overrides" / "full_names.csv"
DECK = ROOT / "data" / "deck_snapshot.json.gz"
FACTS = ROOT / "data" / "puzzle_facts.json.gz"
SOURCES = {"web", "web-search", "wikidata-label", "archive"}


def rows():
    with CSV.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def deck_cards():
    doc = json.load(gzip.open(DECK))
    return [c for cards in doc["deck"]["cards_by_fs"].values() for c in cards]


def test_every_player_has_one_full_name_and_no_two_share_it():
    by = {r["person_id"]: r for r in rows()}
    names = [r["full_name"].strip() for r in by.values()]
    assert all(names), [r["name"] for r in by.values() if not r["full_name"].strip()]
    dupes = sorted(n for n in set(names) if names.count(n) > 1)
    assert not dupes
    # Everybody in the deck is in the file.
    assert {c["person_id"] for c in deck_cards()} <= set(by)


def test_every_name_says_how_it_was_decided():
    for r in rows():
        assert r["source"] in SOURCES, r
        assert r["cricinfo_id"], r
        if r["source"] == "web":
            # The article title, with only a "(cricketer)"-style tag removed and dotted
            # initials closed up ("R. P. Singh" -> "RP Singh").
            title = re.sub(r"\s*\([^)]*\)$", "", r["wikipedia"])
            assert r["full_name"].replace(" ", "") == title.replace(".", "").replace(" ", ""), r
        if r["source"] == "archive":
            # Kept as the archive wrote it, which is only right where that is not initials.
            assert r["full_name"] == r["name"] and not re.match(r"^[A-Z]{1,4} ", r["name"]), r


def test_the_names_the_overhaul_was_for():
    by = {r["name"]: r["full_name"] for r in rows()}
    assert by["PWH de Silva"] == "Wanindu Hasaranga"
    assert by["PHKD Mendis"] == "Kamindu Mendis"
    assert by["SL Malinga"] == "Lasith Malinga"
    assert by["V Kohli"] == "Virat Kohli"
    # Where Wikipedia itself uses initials, so does the site.
    assert by["MS Dhoni"] == "MS Dhoni" and by["AB de Villiers"] == "AB de Villiers"
    # A112/A114's two Harmeet Singhs, still told apart, and Kuldeep is not Kuldip.
    assert by["Harmeet Singh"] != by["Harmeet Singh (2)"]
    assert by["Kuldeep Yadav"] != by["K Yadav"]


def test_the_deck_shows_the_full_name():
    full = {r["person_id"]: r["full_name"] for r in rows()}
    wrong = [(c["name"], full[c["person_id"]]) for c in deck_cards()
             if c["name"] != full[c["person_id"]]]
    assert not wrong, wrong[:5]


def test_the_puzzles_show_the_full_name_and_keep_the_archive_one_to_search():
    by = {r["person_id"]: r for r in rows()}
    players = json.load(gzip.open(FACTS))["players"]
    for p in players:
        r = by[p["id"]]
        assert p["name"] == r["full_name"]
        assert p["aka"] == ("" if r["name"] == r["full_name"] else r["name"])


def test_a_name_given_to_two_players_is_refused(monkeypatch):
    monkeypatch.setattr(derive_people, "ipl_people", lambda conn: {"a": "X Smith", "b": "Y Smith"})
    monkeypatch.setattr(derive_people, "matches_played", lambda conn: {})
    monkeypatch.setattr(derive_people, "read_override", lambda path: [
        {"person_id": "a", "full_name": "John Smith"},
        {"person_id": "b", "full_name": "John Smith"}])

    class NoDb:
        def cursor(self):
            raise AssertionError("wrote to the database before refusing")

    with pytest.raises(SystemExit, match="John Smith"):
        derive_people.do_full_names(NoDb())


# --- the page helpers, run under node -------------------------------------------------

node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _fn(path, name):
    src = (ROOT / "web" / "static" / path).read_text()
    return re.search(rf"function {name}\([\s\S]*?\n}}\n", src).group(0)


def run_js(code: str):
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@node
def test_the_short_form():
    code = _fn("common.js", "esc") + _fn("common.js", "shortName") + _fn("common.js", "nameSwap") + """
    console.log(JSON.stringify({
      cases: ['Wanindu Hasaranga', 'Quinton de Kock', 'MS Dhoni', 'AB de Villiers', 'Noor',
              'Lhuan-dre Pretorius'].map(shortName),
      same: nameSwap('MS Dhoni'), swap: nameSwap('Wanindu Hasaranga'),
      escaped: nameSwap('<b>Bad Name</b>')}))"""
    out = run_js(code)
    assert out["cases"] == ["W Hasaranga", "Q de Kock", "MS Dhoni", "AB de Villiers", "Noor",
                            "L Pretorius"]
    assert out["same"] == "MS Dhoni"
    assert out["swap"] == ('<span class="nm-full">Wanindu Hasaranga</span>'
                           '<span class="nm-short">W Hasaranga</span>')
    assert "<b>" not in out["escaped"]


@node
def test_search_finds_a_player_by_either_name():
    players = [{"id": "h", "name": "Wanindu Hasaranga", "aka": "PWH de Silva"},
               {"id": "p", "name": "Hardik Pandya", "aka": "HH Pandya"},
               {"id": "k", "name": "Krunal Pandya", "aka": "KH Pandya"},
               {"id": "d", "name": "MS Dhoni"},
               {"id": "s", "name": "Kumar Sangakkara", "aka": "KC Sangakkara"},
               {"id": "q", "name": "Praveen Kumar", "aka": "P Kumar"},
               {"id": "x", "name": "Kumar Dharmasena", "aka": "HDPK Kumar"}]
    src = (ROOT / "web" / "static" / "puzzle.js").read_text()
    init = re.search(r"PZ_PLAYERS = list\.map\([\s\S]*?\);", src).group(0)
    code = (_fn("puzzle.js", "pzNorm") + "let PZ_PLAYERS = null;\n" + _fn("puzzle.js", "pzMatches")
            + f"const list = {json.dumps(players)};\n{init}\n" + """
    const ids = q => pzMatches(q).list.map(x => x.p.id);
    console.log(JSON.stringify({
      first: ids('hardik'), surname: ids('pandya'), archive: ids('de silva'),
      initials: ids('pwh'), both: ids('hasaranga'), dhoni: ids('dhoni'), none: ids('kohli'),
      kumar: ids('kumar')}))""")
    out = run_js(code)
    assert out["first"] == ["p"]
    assert sorted(out["surname"]) == ["k", "p"]
    assert out["archive"] == ["h"] and out["initials"] == ["h"] and out["both"] == ["h"]
    assert out["dhoni"] == ["d"] and out["none"] == []
    # A surname match ranks first -- ahead of the alphabet, which would put Sangakkara
    # before Praveen -- and a player is ranked on whichever of his names matched best:
    # Dharmasena's shown name starts with Kumar, his archive name ends with it.
    assert out["kumar"] == ["x", "q", "s"]


def test_every_reader_that_shows_a_name_reads_the_full_one():
    """The deck snapshot is patched, but the database fallback (A107), the profile's
    leaderboards and the display-only unrated list build names from SQL. A bare
    `p.primary_name` there would show initials again with nothing else noticing."""
    bare = []
    for path in [ROOT / "etl" / "feasibility.py", *sorted((ROOT / "web").glob("*.py")),
                 *sorted((ROOT / "game").glob("*.py"))]:
        for line in path.read_text().splitlines():
            if "primary_name" in line and "coalesce(p.display_name, p.primary_name)" not in line:
                bare.append(f"{path.relative_to(ROOT)}: {line.strip()}")
    # The one deliberate reader: the archive name kept for search (puzzle_facts `aka`).
    assert bare == ['game/puzzle_facts.py: archive_name = {pid: n for pid, n in '
                    'conn.execute("select person_id, primary_name from people")}'], bare
