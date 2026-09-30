"""Team kits [A146]: what web/kit.py accepts, what it hands out by default, and whether
migration 036's constraints accept everything it can produce."""

from __future__ import annotations

import pathlib
import re

import pytest

from tests.test_crests import _independent_contrast
from web import kit
from web.kit import KitError, default_kit, parse_kit

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _ok(**over):
    raw = {"name": "Mumbai Mavericks", "monogram": "mm", "colour": "royal"}
    raw.update(over)
    return raw


def test_a_valid_kit_is_accepted_and_its_monogram_upper_cased():
    k = parse_kit(_ok())
    assert (k.name, k.monogram, k.colour) == ("Mumbai Mavericks", "MM", "royal")


def test_whitespace_in_a_name_collapses():
    assert parse_kit(_ok(name="  Chennai   Chargers ")).name == "Chennai Chargers"


@pytest.mark.parametrize("bad", [
    {"name": ""}, {"name": "   "}, {"name": "x" * 25}, {"name": "Bad\u0000Name"},
    {"name": "Right‮to left"},              # a format character, not a letter
    {"monogram": ""}, {"monogram": "ABCD"}, {"monogram": "A B"}, {"monogram": "<b>"},
    {"colour": "chartreuse"}, {"colour": None},
])
def test_a_bad_kit_is_refused(bad):
    with pytest.raises(KitError):
        parse_kit(_ok(**bad))


def test_a_kit_that_is_not_an_object_is_refused():
    with pytest.raises(KitError):
        parse_kit("gold")


def test_every_palette_colour_has_readable_ink_on_its_own_base():
    """The badge draws its monogram in `ink` on `deep`, so this is the pairing that
    has to read -- checked with the test suite's own WCAG code, not web.colours'."""
    for p in kit.palette():
        assert _independent_contrast(p["ink"], p["deep"]) >= 4.5, p["key"]


def test_the_palette_colours_are_all_different():
    teams = [p["team"] for p in kit.palette()]
    assert len(set(teams)) == len(teams)


def test_initials_come_from_words_and_a_single_word_gives_three_letters():
    assert kit.initials("Koustav Mishra") == "KM"
    assert kit.initials("Krause") == "KRA"
    assert kit.initials("Rahul Kumar Singh Rao") == "RKS"
    assert kit.initials("!!!") == "XI"


def test_a_default_monogram_never_repeats_one_already_in_the_room():
    k = default_kit("Koustav Mishra", 1, {"KM", "K2"}, set())
    assert k.monogram not in {"KM", "K2"}
    assert re.fullmatch(r"[A-Z0-9]{1,3}", k.monogram)


def test_default_colours_spread_across_the_palette_and_skip_taken_ones():
    keys = list(kit.PALETTE)
    assert default_kit("A", 0, set(), set()).colour == keys[0]
    assert default_kit("A", 2, set(), set()).colour == keys[2]
    assert default_kit("A", 2, set(), {keys[2], keys[3]}).colour == keys[4]


def test_a_default_kit_from_a_long_seat_name_is_itself_a_valid_kit():
    k = default_kit("A seat name that runs to all of forty ch", 0, set(), set())
    assert parse_kit(k.to_dict()) == k


def test_the_solo_default_is_a_valid_kit():
    parse_kit(kit.SOLO_DEFAULT)


# --- migration 036 against web/kit.py ---------------------------------------------------

def _migration() -> str:
    return (ROOT / "migrations" / "036_kits.sql").read_text()


def _check(table: str, column: str) -> str:
    m = re.search(rf"{table}_kit_{column}_ck check \((.*?)\)[,;]?\n", _migration())
    assert m, f"no {table}.kit_{column} constraint"
    return m.group(1)


@pytest.mark.parametrize("table", ["accounts", "room_players"])
def test_the_database_accepts_every_colour_the_palette_has(table):
    pattern = re.search(r"~ '([^']+)'", _check(table, "colour")).group(1)
    for key in kit.PALETTE:
        assert re.fullmatch(pattern, key), key


@pytest.mark.parametrize("table", ["accounts", "room_players"])
def test_the_database_name_bound_is_the_code_bound(table):
    lo, hi = map(int, re.search(r"between (\d+) and (\d+)", _check(table, "name")).groups())
    assert (lo, hi) == (1, kit.NAME_MAX)


@pytest.mark.parametrize("table", ["accounts", "room_players"])
def test_the_database_monogram_rule_is_the_code_rule(table):
    pattern = re.search(r"~ '([^']+)'", _check(table, "monogram")).group(1)
    assert pattern == kit._MONOGRAM.pattern
