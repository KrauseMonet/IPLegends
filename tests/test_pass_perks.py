"""Pass perks in friends' rooms [A312]: on by default, the host may turn them off, and while
on a pass holder brings one extra legend and a boosted career's legend may come in. Every
legend perks let in is marked for the whole room to see."""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from tests.test_rooms import DECK, FakeConn
from web import app as app_mod
from web import rooms


@pytest.fixture
def conn():
    return FakeConn()


def _legend(name: str, career: str, **extra) -> dict:
    return {"name": name, "rating": 90, "year": 2015, "role_label": "Opening batter",
            "career": career, "skilled": True, "card": {}, **extra}


def _room(conn, pro=False):
    return rooms.create_room(conn, "league", 15, "Host", game="auction", legends=True, pro=pro)


def test_a_new_room_has_pass_perks_on(conn):
    room, _ = _room(conn)
    assert room.perks is True
    assert rooms._load_room(conn, room.code).perks is True


@pytest.mark.parametrize("pro,perks,holder,want", [
    (False, True, False, 1), (False, True, True, 2), (False, False, True, 1),
    (True, True, False, 2), (True, True, True, 3), (True, False, True, 2)])
def test_a_pass_holder_gets_one_more_legend_only_while_perks_are_on(conn, pro, perks, holder,
                                                                      want):
    room, host = _room(conn, pro=pro)
    rooms.set_perks(conn, room.code, host, perks)
    assert rooms.legend_slots(rooms._load_room(conn, room.code), holder) == want


def test_the_extra_legend_is_marked_as_a_pass_perk_and_the_shared_one_is_not(conn):
    room, host = _room(conn)
    rooms.set_legend(conn, room.code, host, _legend("A", "a" * 64), 0, holder=True)
    rooms.set_legend(conn, room.code, host, _legend("B", "b" * 64), 1, holder=True)
    got = rooms.seat_legends(rooms._load_room(conn, room.code), host)
    assert [rooms.is_perk(x) for x in got] == [False, True]
    with pytest.raises(rooms.RoomError):
        rooms.set_legend(conn, room.code, host, _legend("C", "c" * 64), 1, holder=False)


def test_a_boosted_legend_comes_in_only_while_perks_are_on(conn):
    room, host = _room(conn)
    boosted = _legend("Boosted", "a" * 64, boosted=True)
    rooms.set_legend(conn, room.code, host, boosted)
    assert rooms.is_perk(rooms.seat_legends(rooms._load_room(conn, room.code), host)[0])
    rooms.set_perks(conn, room.code, host, False)
    with pytest.raises(rooms.RoomError, match="perks off"):
        rooms.set_legend(conn, room.code, host, boosted)


def test_turning_perks_off_drops_every_legend_perks_let_in(conn):
    room, host = _room(conn)
    _, guest = rooms.join_room(conn, room.code, "Guest", DECK)
    rooms.set_legend(conn, room.code, host, _legend("A", "a" * 64), 0, holder=True)
    rooms.set_legend(conn, room.code, host, _legend("B", "b" * 64), 1, holder=True)
    rooms.set_legend(conn, room.code, guest, _legend("G", "c" * 64, boosted=True))
    rooms.set_perks(conn, room.code, host, False)
    r = rooms._load_room(conn, room.code)
    assert [x["name"] for x in rooms.seat_legends(r, host)] == ["A"]
    assert rooms.seat_legends(r, guest) == []


def test_only_the_host_changes_perks_and_only_in_the_lobby(conn):
    room, host = _room(conn)
    _, guest = rooms.join_room(conn, room.code, "Guest", DECK)
    with pytest.raises(rooms.RoomError, match="host"):
        rooms.set_perks(conn, room.code, guest, False)
    stored = conn.rooms[room.code]
    conn.rooms[room.code] = stored[:5] + ("auctioning",) + stored[6:]
    with pytest.raises(rooms.RoomError, match="before the room starts"):
        rooms.set_perks(conn, room.code, host, False)


# --- the routes -------------------------------------------------------------------------------

@pytest.fixture
def routes(conn, monkeypatch):
    @contextmanager
    def fake_db():
        yield conn
    monkeypatch.setattr(app_mod, "_db", fake_db)
    monkeypatch.setitem(app_mod.STATE, "deck", DECK)
    state = {"pass": False}
    monkeypatch.setattr(app_mod, "_holds_pass", lambda c, request: state["pass"])
    return state


def test_the_perks_switch_needs_no_pass(conn, routes):
    room, host = _room(conn)
    out = app_mod.room_perks(room.code, app_mod.RoomProIn(player_id=host, on=False))
    assert out.perks is False


def test_the_room_shows_every_seat_which_legends_are_perks(conn, routes):
    room, host = _room(conn)
    _, guest = rooms.join_room(conn, room.code, "Guest", DECK)
    rooms.set_legend(conn, room.code, host, _legend("A", "a" * 64), 0, holder=True)
    rooms.set_legend(conn, room.code, host, _legend("B", "b" * 64), 1, holder=True)
    out = app_mod._room_state_out(rooms._load_room(conn, room.code), DECK, guest)
    assert out.legends[host].perk is False
    assert [x.perk for x in out.more_legends[host]] == [True]
    assert out.perks is True


def test_the_legend_route_asks_the_pass_only_for_a_slot_past_the_first(conn, routes,
                                                                        monkeypatch):
    seen = []
    monkeypatch.setattr(app_mod.career_results, "legend_of",
                        lambda c, a, key: _legend("X", key.partition("@")[0]))
    monkeypatch.setattr(app_mod.auth, "verify_session_cookie", lambda t: 7)

    class Req:
        cookies = {app_mod.auth.COOKIE_NAME: "t"}

    real = rooms.set_legend
    monkeypatch.setattr(app_mod.rooms, "set_legend",
                        lambda *a, **k: seen.append(k.get("holder")) or real(*a, **k))
    room, host = _room(conn)
    app_mod.room_legend(room.code, app_mod.RoomLegendIn(player_id=host, career="a" * 64), Req())
    with pytest.raises(app_mod.HTTPException):
        app_mod.room_legend(room.code, app_mod.RoomLegendIn(player_id=host, career="b" * 64,
                                                            slot=1), Req())
    routes["pass"] = True
    app_mod.room_legend(room.code, app_mod.RoomLegendIn(player_id=host, career="b" * 64,
                                                        slot=1), Req())
    assert seen == [False, False, True]
