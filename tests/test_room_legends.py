"""Career legends in a friends' auction room (A215): the best version of a saved career joins
its player's squad before the bidding, free, taking one of the eighteen places.

Runs on `tests/test_rooms.py`'s `FakeConn` and the committed deck snapshot, as the other
auction-room tests do.
"""

from __future__ import annotations

import dataclasses

import pytest

import game.auction as au
from etl.feasibility import order_errors
from game import career as C
from tests.test_room_auction import Clock, load, run_to_the_end
from tests.test_rooms import FakeConn
from tools import snapshot_deck
from web import career_results
from web import room_auction as ra
from web import rooms

DOC = snapshot_deck.read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="no deck snapshot committed")


@pytest.fixture(scope="module")
def deck():
    return snapshot_deck.deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return snapshot_deck.model_from(DOC)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(ra.time, "time", c)
    monkeypatch.setattr(rooms.time, "time", c)
    return c


@pytest.fixture(scope="module")
def career(deck, model):
    car, _ = C.auto_career(deck, model, 5, C.Setup("Legend Kid", "opener", 2015, False), "strength")
    return car


@pytest.fixture(scope="module")
def legend(career):
    return career_results.legend(career)


def test_the_legend_is_the_season_of_your_best_rating(career, legend):
    best = max(career.seasons, key=lambda r: (r.rating, -r.year))
    assert legend["rating"] == best.rating and legend["year"] == best.year
    card = snapshot_deck._card_from(legend["card"])
    assert card.display == best.rating
    # the numbers the engine played that season, not the ceiling's
    played = C.ability(career.start, career.ceiling, best.ability, setup=career.setup,
                       fs_id=0, year=best.year, franchise=None)
    assert card.bat_scoring == played.bat_scoring and card.bat_dismissal == played.bat_dismissal
    assert card.positions == career.start.positions


def _legend_room(conn, deck, legend, game="auction", a="MI", b="CSK", start=True):
    room, host = rooms.create_room(conn, "league", 30, "Asha", game=game, legends=True)
    room, guest = rooms.join_room(conn, room.code, "Ben", deck)
    rooms.choose_franchise(conn, room.code, host, a)
    rooms.choose_franchise(conn, room.code, guest, b)
    rooms.set_legend(conn, room.code, host, legend)
    if start:
        rooms.start_room(conn, room.code, host, deck)
    return room.code, host, guest


def test_a_legend_joins_his_players_squad_free_before_the_bidding(deck, clock, legend):
    conn = FakeConn()
    code, host, guest = _legend_room(conn, deck, legend)
    r = ra.replay(load(conn, code), deck)
    assert r.phase == "bid"
    mine, theirs = r.auction.teams[r.team_of[host]], r.auction.teams[r.team_of[guest]]
    assert [c.name for c in mine.squad] == ["Legend Kid"] and mine.paid == [0]
    assert mine.purse == au.PURSE and mine.open_places == au.SQUAD_SIZE - 1
    assert mine.legend is mine.squad[0] and ra.is_legend(mine.squad[0])
    assert theirs.squad == [] and theirs.legend is None
    # a person id of his own, never the career's shared one
    assert mine.squad[0].person_id == ra.LEGEND_PREFIX + host


def test_a_room_with_a_legend_completes_with_legal_twelves(deck, clock, legend):
    conn = FakeConn()
    code, host, _ = _legend_room(conn, deck, legend)
    room = run_to_the_end(conn, code, deck, clock)
    assert room.status == "complete"
    r = ra.replay(room, deck)
    team = r.auction.teams[r.team_of[host]]
    assert len(team.squad) == au.SQUAD_SIZE and team.legend in team.squad
    for pid, player, order, impact in rooms.room_sides(room, deck):
        t = next(x for x in r.auction.teams if x.short == player.franchise)
        assert order_errors(order, impact, t.squad) == []


def test_two_legends_in_one_room_stay_two_people(deck, clock, legend):
    conn = FakeConn()
    code, host, guest = _legend_room(conn, deck, legend, start=False)
    rooms.set_legend(conn, code, guest, {**legend, "name": "Other Kid"})
    rooms.start_room(conn, code, host, deck)
    r = ra.replay(load(conn, code), deck)
    ids = {r.auction.teams[r.team_of[p]].legend.person_id for p in (host, guest)}
    assert len(ids) == 2


def test_without_the_setting_nobody_brings_anyone(deck, clock, legend):
    conn = FakeConn()
    room, host = rooms.create_room(conn, "league", 30, "Asha", game="auction")
    with pytest.raises(rooms.RoomError, match="off"):
        rooms.set_legend(conn, room.code, host, legend)
    draft, h = rooms.create_room(conn, "league", 30, "Asha")
    with pytest.raises(rooms.RoomError, match="only an auction room"):
        rooms.set_legends(conn, draft.code, h, True)


def test_only_the_host_turns_legends_on_and_only_in_the_lobby(deck, clock, legend):
    conn = FakeConn()
    room, host = rooms.create_room(conn, "league", 30, "Asha", game="auction")
    room, guest = rooms.join_room(conn, room.code, "Ben", deck)
    with pytest.raises(rooms.RoomError, match="host"):
        rooms.set_legends(conn, room.code, guest, True)
    rooms.set_legends(conn, room.code, host, True)
    assert load(conn, room.code).legends == {}
    rooms.set_legend(conn, room.code, guest, legend)
    rooms.set_legends(conn, room.code, host, False)
    assert load(conn, room.code).legends is None          # off forgets every choice
    rooms.set_legends(conn, room.code, host, True)
    rooms.choose_franchise(conn, room.code, host, "MI")
    rooms.choose_franchise(conn, room.code, guest, "CSK")
    rooms.start_room(conn, room.code, host, deck)
    with pytest.raises(rooms.RoomError, match="before the auction"):
        rooms.set_legend(conn, room.code, host, legend)
    with pytest.raises(rooms.RoomError, match="before the auction"):
        rooms.set_legends(conn, room.code, host, False)


def test_a_legend_is_never_traded(deck, clock, legend):
    conn = FakeConn()
    code, host, guest = _legend_room(conn, deck, legend)
    r = ra.replay(load(conn, code), deck)
    a, b = r.team_of[host], r.team_of[guest]
    legend_card = r.auction.teams[a].legend
    someone = dataclasses.replace(legend_card, person_id="someone-else", name="Someone")
    assert any("legend" in e for e in r.offer_errors(a, b, legend_card, someone))
    assert any("legend" in e for e in r.offer_errors(b, a, someone, legend_card))
    assert not any("legend" in e for e in r.offer_errors(a, b, someone, someone))


def test_a_legends_name_carries_no_markup(deck, clock, legend):
    conn = FakeConn()
    code, host, _ = _legend_room(conn, deck, {**legend, "name": "<img src=x onerror=1>Kid"})
    r = ra.replay(load(conn, code), deck)
    name = r.auction.teams[r.team_of[host]].legend.name
    assert "<" not in name and ">" not in name and name.endswith("Kid")
    with pytest.raises(C.CareerError):
        C.Setup("<b>x</b>", "opener", 2012, False).check()


# --- mega rooms: retentions must leave room for him -----------------------------------------


def _overseas_legend(legend):
    card = dict(legend["card"], overseas=True)
    return {**legend, "card": card}


def test_the_legend_check_counts_him_among_the_overseas_and_the_places(deck, legend):
    """A guard more than a rule today: with at most five retentions an overseas legend makes
    six, which is the cap, so it cannot bind -- but it holds if either constant moves."""
    legend_card = ra.legend_cards(type("R", (), {
        "legends": {"p": _overseas_legend(legend)},
        "players": {"p": rooms.RoomPlayer("p", "Asha", False)}})())["p"]
    team = au.Team(0, "MI", "Mumbai Indians", human=True, legend=legend_card)
    overseas = []
    for cs in deck.cards_by_fs.values():
        for c in cs:
            if c.overseas is True and all(c.person_id != o.person_id for o in overseas):
                overseas.append(c)
    assert not any("overseas" in e for e in ra.legend_errors(team, overseas[:au.SQUAD_OVERSEAS_CAP - 1]))
    assert any("overseas" in e for e in ra.legend_errors(team, overseas[:au.SQUAD_OVERSEAS_CAP]))
    assert ra.legend_errors(dataclasses.replace(team, legend=None), overseas[:au.SQUAD_OVERSEAS_CAP]) == []


def test_the_retention_timeout_leaves_room_for_the_legend(deck, clock, legend):
    conn = FakeConn()
    code, host, guest = _legend_room(conn, deck, _overseas_legend(legend), game="mega",
                                     a="RCB", b="KKR")
    r = ra.replay(load(conn, code), deck)
    team = r.auction.teams[r.team_of[host]]
    picks = ra.retention_suggestion(r, team.index)
    chosen = [r.pools[team.index][i] for i in picks]
    assert ra.legend_errors(team, chosen) == []
    # and the room carries on past retentions with him in the squad
    clock.now = load(conn, code).turn_started_at + rooms.CLOCK_GRACE_S + 1
    rooms.room_state(conn, code, deck)
    room = run_to_the_end(conn, code, deck, clock)
    rr = ra.replay(room, deck)
    t = rr.auction.teams[rr.team_of[host]]
    assert room.status == "complete" and t.legend in t.squad and len(t.squad) == au.SQUAD_SIZE
    assert t.overseas <= au.SQUAD_OVERSEAS_CAP


def test_with_six_retentions_allowed_the_legend_guard_binds(deck, clock, legend, monkeypatch):
    """The guard above cannot bind with five retentions, so it is exercised here with six
    allowed: six overseas retained plus an overseas legend is seven, over the cap. Refused
    when submitted, and trimmed when the clock answers for an absent player."""
    monkeypatch.setattr(au, "RETENTION_SLABS", (1800,) * 6)
    monkeypatch.setattr(au, "MAX_RETENTIONS", 6)
    conn = FakeConn()
    code, host, guest = _legend_room(conn, deck, _overseas_legend(legend), game="mega",
                                     a="DC", b="KKR")
    r = ra.replay(load(conn, code), deck)
    team = r.auction.teams[r.team_of[host]]
    pool = r.pools[team.index]
    picks, seen = [], set()
    for i, c in enumerate(pool):
        if c.overseas is True and c.person_id not in seen:
            seen.add(c.person_id)
            picks.append(i)
        if len(picks) == 6:
            break
    chosen = [pool[i] for i in picks]
    assert len(picks) == 6 and au.retention_errors(team, chosen) == [], \
        "the fixture must be a legal six without the legend"
    with pytest.raises(rooms.RoomError, match="legend"):
        ra.submit(conn, code, deck, ra.retain, host, picks)
    monkeypatch.setattr(au, "cpu_retain", lambda t, p, taken: list(chosen))
    trimmed = [pool[i] for i in ra.retention_suggestion(r, team.index)]
    assert len(trimmed) == 5 and ra.legend_errors(team, trimmed) == []
