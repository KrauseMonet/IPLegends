"""Pass cosmetics [A314]: pass colours for a team kit, chosen only with a pass, kept when
a pass lapses, never handed out as a default, and drawn with a sheen whoever looks."""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest
from starlette.requests import Request

from tests.test_rooms import DECK, FakeConn
from web import app as app_mod
from web import kit, rooms
from web.kit import Kit, KitError, default_kit, parse_kit

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _raw(colour: str) -> dict:
    return {"name": "Bombay Blasters", "monogram": "BB", "colour": colour}


def test_pass_colours_are_refused_without_leave_and_accepted_with_it():
    for colour in kit.PREMIUM:
        with pytest.raises(KitError, match="pass"):
            parse_kit(_raw(colour))
        assert parse_kit(_raw(colour), premium_ok=True).colour == colour


def test_leave_to_choose_a_pass_colour_never_lets_an_unknown_colour_through():
    with pytest.raises(KitError, match="unknown"):
        parse_kit(_raw("chartreuse"), premium_ok=True)


def test_the_pass_colours_are_six_and_none_is_a_free_colour():
    assert len(kit.PREMIUM) == 6
    assert not set(kit.PREMIUM) & set(kit.PALETTE)


def test_the_palette_marks_exactly_the_pass_colours():
    marked = {p["key"] for p in kit.palette() if p["premium"]}
    assert marked == set(kit.PREMIUM)
    assert {p["key"] for p in kit.palette()} == set(kit.PALETTE) | set(kit.PREMIUM)


def test_a_room_default_is_never_a_pass_colour_even_with_every_free_colour_taken():
    taken = set(kit.PALETTE)
    for seat in range(20):
        assert default_kit("Seat", seat, set(), taken).colour in kit.PALETTE


# --- who may choose one ------------------------------------------------------------------

def _request() -> Request:
    headers: list = []
    return Request({"type": "http", "headers": headers})


@pytest.fixture
def account(monkeypatch):
    """A signed-in account 7 whose pass and kit the test sets."""
    state = {"premium": False, "kit": None, "db_reads": 0}
    monkeypatch.setattr(app_mod, "_current_account_id", lambda request: 7)

    def has_premium(conn, account_id, today):
        state["db_reads"] += 1
        return state["premium"]

    monkeypatch.setattr(app_mod.premium_lib, "has_premium", has_premium)
    monkeypatch.setattr(app_mod.accounts, "get_account",
                        lambda conn, aid: type("A", (), {"kit": state["kit"]})())
    return state


def test_a_pass_holder_may_choose_a_pass_colour(account):
    account["premium"] = True
    assert app_mod._kit_premium_ok(None, _request(), "rose")


def test_without_a_pass_a_pass_colour_is_refused(account):
    assert not app_mod._kit_premium_ok(None, _request(), "rose")


def test_a_lapsed_pass_keeps_the_pass_colour_the_account_already_wears(account):
    account["kit"] = Kit("Bombay Blasters", "BB", "rose")
    assert app_mod._kit_premium_ok(None, _request(), "rose")
    assert not app_mod._kit_premium_ok(None, _request(), "aurora")


def test_a_free_colour_never_asks_the_database(account):
    app_mod._kit_premium_ok(None, _request(), "gold")
    assert account["db_reads"] == 0


def test_signed_out_nobody_may_choose_a_pass_colour(monkeypatch):
    monkeypatch.setattr(app_mod, "_current_account_id", lambda request: None)
    assert not app_mod._kit_premium_ok(None, _request(), "rose")


# --- rooms -------------------------------------------------------------------------------

def test_a_room_seat_needs_leave_to_wear_a_pass_colour():
    conn = FakeConn()
    room, host = rooms.create_room(conn, "final", 15, "Host")
    with pytest.raises(rooms.RoomError, match="pass"):
        rooms.set_kit(conn, room.code, host, _raw("crimson"))
    rooms.set_kit(conn, room.code, host, _raw("crimson"), premium_ok=True)
    assert rooms._load_room(conn, room.code).players[host].kit.colour == "crimson"


def test_a_pass_colour_brought_into_a_room_without_leave_is_dropped_not_refused():
    conn = FakeConn()
    room, host = rooms.create_room(conn, "final", 15, "Host")
    _, guest = rooms.join_room(conn, room.code, "Guest", DECK, _raw("aurora"))
    assert rooms._load_room(conn, room.code).players[guest].kit is None
    room2, host2 = rooms.create_room(conn, "final", 15, "Host", kit=_raw("aurora"),
                                     kit_premium_ok=True)
    assert rooms._load_room(conn, room2.code).players[host2].kit.colour == "aurora"


# --- the page ----------------------------------------------------------------------------

COMMON = ROOT / "web" / "static" / "common.js"


def _fn(src: str, name: str) -> str:
    return re.search(rf"function {name}\([\s\S]*?\n}}\n", src).group(0)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_badge_shines_and_the_editor_locks_by_the_server_palette():
    src = COMMON.read_text()
    palette = [{"key": "gold", "premium": False, "team": "#1", "deep": "#2", "ink": "#3"},
               {"key": "rose", "premium": True, "team": "#4", "deep": "#5", "ink": "#6"}]
    body = "\n".join(_fn(src, n) for n in ("esc", "kitPalette", "kitColour", "kitStyle",
                                           "kitBadge", "kitColourOpen"))
    script = f"""
    let META = {{kits: {palette!r}}};
    let ME = null;
    {body}
    const out = {{
      plain: kitBadge({{name: 'A', monogram: 'A', colour: 'gold'}}).includes('kit-premium'),
      shiny: kitBadge({{name: 'A', monogram: 'A', colour: 'rose'}}).includes('kit-premium'),
      free: kitColourOpen(META.kits[0], 'gold'),
      lockedOut: kitColourOpen(META.kits[1], 'gold'),
      wearing: kitColourOpen(META.kits[1], 'rose'),
    }};
    ME = {{premium: {{name: 'Season Pass'}}}};
    out.holder = kitColourOpen(META.kits[1], 'gold');
    console.log(JSON.stringify(out));
    """.replace("True", "true").replace("False", "false")
    import json
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True,
                                    check=True).stdout)
    assert out == {"plain": False, "shiny": True, "free": True, "lockedOut": False,
                   "wearing": True, "holder": True}


def test_the_career_card_prints_the_pass_edition_only_for_a_pass_holder():
    src = (ROOT / "web" / "static" / "career.js").read_text()
    assert "ME.premium) crPassEdition(" in src
