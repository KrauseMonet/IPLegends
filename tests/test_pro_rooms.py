"""Pro rooms [A311]: a pass-holding host makes the room Pro, and everyone in it gets a second
legend each, three trades instead of two, and Run it back -- a fresh tournament with the
same squads. The pass is the route's business (a seat has no account); the rules are here."""

from __future__ import annotations

import dataclasses
from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from tests.test_room_match import (DECK, MODEL, _drive_room_to_completion, _make_room,
                                   _pinned_seed)  # noqa: F401  (the seed fixture)
from tests.test_rooms import FakeConn, _play_room_to_completion
from web import app as app_mod
from web import room_auction as RA
from web import room_match, rooms


@pytest.fixture
def conn():
    return FakeConn()


@pytest.fixture(scope="module")
def real_deck():
    from tools.snapshot_deck import deck_from, read_document
    doc = read_document()
    if doc is None:
        pytest.skip("no deck snapshot")
    return deck_from(doc)


def _legend(name: str, career: str = "a" * 64) -> dict:
    return {"name": name, "rating": 90, "year": 2015, "role_label": "Opening batter",
            "career": career, "skilled": True, "card": {}}


def _auction_room(conn, pro=False):
    room, host = rooms.create_room(conn, "league", 15, "Host", game="auction", legends=True,
                                   pro=pro)
    return room, host


# --- the setting ----------------------------------------------------------------------------

def test_only_the_host_makes_a_pro_room_and_only_in_the_lobby(conn):
    room, host = _auction_room(conn)
    _, guest = rooms.join_room(conn, room.code, "Guest", DECK)
    with pytest.raises(rooms.RoomError, match="host"):
        rooms.set_pro(conn, room.code, guest, True)
    assert rooms.set_pro(conn, room.code, host, True).pro is True
    assert rooms._load_room(conn, room.code).pro is True
    stored = conn.rooms[room.code]
    stored = stored[:5] + ("auctioning",) + stored[6:]
    conn.rooms[room.code] = stored
    with pytest.raises(rooms.RoomError, match="before the room starts"):
        rooms.set_pro(conn, room.code, host, False)


def test_a_pro_room_lets_each_seat_bring_two_legends_and_a_plain_room_one(conn):
    plain, host = _auction_room(conn)
    assert rooms.legend_slots(rooms._load_room(conn, plain.code)) == 1
    rooms.set_legend(conn, plain.code, host, _legend("A", "a" * 64))
    with pytest.raises(rooms.RoomError, match="1 legend"):
        rooms.set_legend(conn, plain.code, host, _legend("B", "b" * 64), slot=1)

    pro, phost = _auction_room(conn, pro=True)
    assert rooms.legend_slots(rooms._load_room(conn, pro.code)) == 2
    rooms.set_legend(conn, pro.code, phost, _legend("A", "a" * 64))
    rooms.set_legend(conn, pro.code, phost, _legend("B", "b" * 64), slot=1)
    got = rooms.seat_legends(rooms._load_room(conn, pro.code), phost)
    assert [x["name"] for x in got] == ["A", "B"]
    with pytest.raises(rooms.RoomError, match="2 legends"):
        rooms.set_legend(conn, pro.code, phost, _legend("C", "c" * 64), slot=2)


def test_the_same_career_cannot_be_two_of_your_legends(conn):
    room, host = _auction_room(conn, pro=True)
    rooms.set_legend(conn, room.code, host, _legend("A", "a" * 64))
    with pytest.raises(rooms.RoomError, match="already"):
        rooms.set_legend(conn, room.code, host, _legend("A again", "a" * 64 + "@2014"), slot=1)


def test_removing_your_first_legend_moves_the_second_up(conn):
    room, host = _auction_room(conn, pro=True)
    rooms.set_legend(conn, room.code, host, _legend("A", "a" * 64))
    rooms.set_legend(conn, room.code, host, _legend("B", "b" * 64), slot=1)
    rooms.set_legend(conn, room.code, host, None, slot=0)
    assert [x["name"] for x in rooms.seat_legends(rooms._load_room(conn, room.code), host)] == ["B"]


def test_turning_pro_off_keeps_only_each_seats_first_legend(conn):
    room, host = _auction_room(conn, pro=True)
    rooms.set_legend(conn, room.code, host, _legend("A", "a" * 64))
    rooms.set_legend(conn, room.code, host, _legend("B", "b" * 64), slot=1)
    rooms.set_pro(conn, room.code, host, False)
    assert [x["name"] for x in rooms.seat_legends(rooms._load_room(conn, room.code), host)] == ["A"]


def test_a_room_from_before_reads_its_single_legend_as_a_list_of_one():
    r = NS(legends={"p": {"name": "Old"}})
    assert rooms.seat_legends(r, "p") == [{"name": "Old"}]
    assert rooms.seat_legends(r, "q") == []


# --- legends in the auction -----------------------------------------------------------------

def test_both_legends_join_the_squad_free_with_ids_of_their_own(real_deck):
    card = next(iter(real_deck.cards_by_fs.values()))[0]
    from tools.snapshot_deck import _card_doc
    doc = _card_doc(card)
    room = NS(players={"p": NS(is_cpu=False)},
              legends={"p": [dict(_legend("A"), card=doc), dict(_legend("B"), card=doc)]})
    cards = RA.legend_cards(room)["p"]
    assert [c.person_id for c in cards] == ["legend:p", "legend:p:1"]
    assert [c.name for c in cards] == ["A", "B"]
    r = RA._replay(7, {"p": "MI"}, [], real_deck, legends={"p": cards})
    team = r.auction.teams[r.team_of["p"]]
    assert [c.person_id for c in team.squad] == ["legend:p", "legend:p:1"]
    assert team.paid == [0, 0]


def test_a_single_legend_keeps_the_id_every_room_before_gave_it():
    card = next(iter(DECK.cards_by_fs.values()))[0]
    from tools.snapshot_deck import _card_doc
    room = NS(players={"p": NS(is_cpu=False)},
              legends={"p": dict(_legend("A"), card=_card_doc(card))})
    assert [c.person_id for c in RA.legend_cards(room)["p"]] == ["legend:p"]


# --- three trades ---------------------------------------------------------------------------

def test_a_pro_room_trades_three_times_and_a_plain_room_twice(monkeypatch):
    assert RA.trades_limit(NS(pro=True)) == 3
    assert RA.trades_limit(NS(pro=False)) == 2
    monkeypatch.setattr(RA.au, "trade_errors", lambda *a: [])   # the squad half: not under test
    card = next(iter(DECK.cards_by_fs.values()))[0]
    two = [NS(a=0, b=1, a_gave=card, b_gave=card)] * 2
    plain = RA.RoomAuctionReplay(NS(teams=[NS(short="MI"), NS(short="CSK")]),
                                 {"x": 0, "y": 1}, "trade", trades=two)
    pro = dataclasses.replace(plain, trades_per_team=3)
    used = "used your 2 trades"
    assert any(used in e for e in plain.offer_errors(0, 1, card, card))
    assert not any("used your" in e for e in pro.offer_errors(0, 1, card, card))
    assert RA._window_over(plain, {0, 1}) and not RA._window_over(pro, {0, 1})


def test_the_room_reads_its_own_trade_limit_into_the_replay(monkeypatch):
    seen = {}

    def fake(*args, **kw):
        seen["limit"] = args[8] if len(args) > 8 else kw.get("trades_per_team")
        return NS()
    monkeypatch.setattr(RA, "_replay", fake)
    RA._CACHE.clear()
    for pro, want in ((True, 3), (False, 2)):
        room = NS(code=f"R{pro}", seed=1, moves=[], game="auction", trades=True, pro=pro,
                  legends=None, players={"p": NS(is_cpu=False, franchise="MI")})
        RA.replay(room, DECK)
        assert seen["limit"] == want


# --- Run it back ----------------------------------------------------------------------------

def _finished_final(conn, pro: bool):
    room, host = _make_room(conn, "final")
    if pro:
        rooms.set_pro(conn, room.code, host, True)
    _, guest = rooms.join_room(conn, room.code, "Guest", DECK)
    room = rooms.start_room(conn, room.code, host, DECK)
    room = _play_room_to_completion(conn, room, DECK, [host, guest])
    assert room.status == "complete", room.failure_reason
    room_match.start_matches(conn, room.code, host, DECK, MODEL)
    room = rooms._load_room(conn, room.code)
    replay = _drive_room_to_completion(conn, room, host)
    return room, host, guest, replay


def _scores(replay):
    return [(e.stage, e.result.home_innings.runs, e.result.away_innings.runs)
            for e in replay.results]


def test_run_it_back_is_for_a_pro_rooms_host_once_the_tournament_is_over(conn):
    room, host, guest, _ = _finished_final(conn, pro=False)
    with pytest.raises(rooms.RoomError, match="Pro"):
        room_match.run_it_back(conn, room.code, host, DECK, MODEL)

    conn2 = FakeConn()
    room, host, guest, first = _finished_final(conn2, pro=True)
    with pytest.raises(rooms.RoomError, match="host"):
        room_match.run_it_back(conn2, room.code, guest, DECK, MODEL)
    room = room_match.run_it_back(conn2, room.code, host, DECK, MODEL)
    assert room_match.run_number(room) == 1
    assert room.moves == rooms._load_room(conn2, room.code).moves      # the same squads
    _, replay = room_match.room_match_state(conn2, room.code, DECK, MODEL)
    assert replay.awaiting_start and replay.results == []
    # a repeat click while the new tournament is under way changes nothing
    again = room_match.run_it_back(conn2, room.code, host, DECK, MODEL)
    assert room_match.run_number(again) == 1


def test_a_run_is_played_on_its_own_seed_and_the_first_on_the_rooms(conn):
    room, host, guest, first = _finished_final(conn, pro=True)
    assert room_match.match_seed(rooms._load_room(conn, room.code)) == room.seed
    room_match.run_it_back(conn, room.code, host, DECK, MODEL)
    room = rooms._load_room(conn, room.code)
    assert room_match.match_seed(room) == f"{room.seed}:run1"
    room_match.start_matches(conn, room.code, host, DECK, MODEL)
    second = _drive_room_to_completion(conn, rooms._load_room(conn, room.code), host)
    assert second.complete
    assert room_match.run_number(rooms._load_room(conn, room.code)) == 1


def test_a_run_saves_as_its_own_game(conn, monkeypatch):
    """The saved-game key carries the run, so each Run it back is a game of its own."""
    import inspect
    src = inspect.getsource(app_mod)
    assert 'natural_key = f"{code}:{room.seed}:{body.player_id}" + (f":run{run}" if run else "")' in src


# --- the pass, at the routes ----------------------------------------------------------------

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


def test_making_a_room_pro_needs_a_pass_and_turning_it_off_does_not(conn, routes):
    room, host = _auction_room(conn)
    with pytest.raises(HTTPException) as e:
        app_mod.room_pro(room.code, app_mod.RoomProIn(player_id=host, on=True), None)
    assert e.value.status_code == 403
    routes["pass"] = True
    assert app_mod.room_pro(room.code, app_mod.RoomProIn(player_id=host, on=True), None).pro
    routes["pass"] = False
    assert not app_mod.room_pro(room.code, app_mod.RoomProIn(player_id=host, on=False), None).pro


def test_creating_a_pro_room_needs_a_pass(conn, routes):
    body = app_mod.CreateRoomIn(format="league", timer_seconds=15, host_name="Host",
                                game="auction", pro=True)
    with pytest.raises(HTTPException) as e:
        app_mod.create_room(body, None)
    assert e.value.status_code == 403
    routes["pass"] = True
    assert app_mod.create_room(body, None).room.pro is True
