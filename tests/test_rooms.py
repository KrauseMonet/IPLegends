"""Live multiplayer rooms (`web/rooms.py`). No live Postgres, no HTTP layer -- like
`tests/test_web.py`, these exercise the session/room logic directly; the FastAPI routes in
`web/app.py` are verified by hand against a running server, the same split `test_web.py`'s
own docstring already draws for the solo draft.

`FakeConn`/`FakeCursor` below are a minimal in-memory stand-in for the two tables
migrations 019/020 created, matched on the handful of distinct SQL statements
`web/rooms.py` issues. Tests call the REAL `create_room`/`join_room`/`start_room`/
`submit_pick`/`room_state`/`replay_room` -- the load/save layer included, not just the
pure logic underneath it -- so a bug in the SQL or the JSONB round-trip would be caught
here too, not only by hand against a real Neon connection.

A room reuses `etl.feasibility.eligible`/`could_still_complete`/`choose_slot` verbatim
(A62's standing rule) for the shared deal-time guarantee, so these tests are about what
a room adds on top: seat limits, snake turn order, the per-turn timer, the AFK auto-pick
fallback, instant CPU-turn resolution, and the shared taken-person-id pool itself -- a
card one seat drafts must vanish from every other seat's own dealt candidates, and one
seat's earlier pick can now strand a DIFFERENT seat, not just itself.
"""

from __future__ import annotations

import pytest

import random
import time

from etl.feasibility import (
    TWELVE_SIZE, XI_SIZE, Card, Deck, eligible, order_errors, pick_rational,
)
from game.__main__ import viable
from web import rooms


class FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class FakeConn:
    """Two dicts standing in for `rooms` and `room_players` (migrations 019/020).
    Matched on the same distinguishing keywords a human skimming `web/rooms.py`'s SQL
    would use -- if that SQL's shape changes, this is the file to update alongside it."""

    def __init__(self):
        self.rooms: dict[str, tuple] = {}                  # code -> row tuple
        self.players: dict[str, dict[str, tuple]] = {}      # code -> {player_id: row}

    def execute(self, sql: str, params: tuple = ()) -> FakeCursor:
        sql_norm = " ".join(sql.split()).lower()

        if sql_norm.startswith("delete from rooms"):
            return FakeCursor([])   # tests never age a room out mid-run

        if sql_norm.startswith("select 1 from rooms"):
            (code,) = params
            return FakeCursor([(1,)] if code in self.rooms else [])

        # Matched on the JOIN, not on a prefix: `_load_room` and `list_open_rooms` now
        # BOTH begin "select r.code, r.format", so a prefix test would route one to the
        # other's branch. This one is checked first and is the only query joining the two
        # tables.
        if "left join room_players" in sql_norm:
            (code,) = params
            row = self.rooms.get(code)
            if row is None:
                return FakeCursor([])
            seats = sorted(self.players.get(code, {}).values(), key=lambda r: r[0])
            if not seats:
                # A room with no seats still returns exactly one row, with every column
                # of the right-hand side NULL -- that is what an outer join does, and
                # `_load_room` has an explicit branch for it.
                return FakeCursor([tuple(row) + (None, None, None)])
            return FakeCursor([
                tuple(row) + (pid, name, is_cpu)
                for (_seat, pid, name, is_cpu) in seats
            ])

        if sql_norm.startswith("set local lock_timeout"):
            # Real Postgres bounds how long the locking read waits; there is no lock to
            # wait on here, so this only has to be accepted rather than simulated.
            return FakeCursor([])

        if sql_norm.startswith("select r.code, r.format"):
            (limit,) = params
            out = []
            # Newest first -- self.rooms is a plain dict, insertion-ordered oldest
            # first, mirroring the real query's `order by r.created_at desc`.
            for row in reversed(list(self.rooms.values())):
                # Indexed, not unpacked with a trailing *_rest: `version` is now the
                # last column, so "the last two are draft_mode and is_open" silently
                # became false -- is_open read the version (always truthy) and every
                # closed room started listing itself as open.
                (code, fmt, _timer, _seed, host_id, status) = row[:6]
                draft_mode, is_open = row[10], row[11]
                if not (is_open and status == "lobby"):
                    continue
                timer_seconds = row[2]
                seats = self.players.get(code, {})
                host_row = seats.get(host_id)
                host_name = host_row[2] if host_row else None
                out.append((code, fmt, timer_seconds, draft_mode, host_name, len(seats)))
            return FakeCursor(out[:limit])

        if sql_norm.startswith("insert into room_players"):
            # One multi-row insert per call now (`_save_room`'s own docstring on why),
            # so `params` is N*5 flat values, not always exactly 5 -- chunk back into
            # per-row tuples the same way psycopg binds them against the repeated
            # `(%s, %s, %s, %s, %s)` VALUES clause.
            for k in range(0, len(params), 5):
                code, player_id, seat_order, name, is_cpu = params[k:k + 5]
                self.players.setdefault(code, {})[player_id] = (
                    seat_order, player_id, name, is_cpu)
            return FakeCursor([])

        if sql_norm.startswith("delete from room_players"):
            code, player_id = params
            self.players.get(code, {}).pop(player_id, None)
            return FakeCursor([])

        if sql_norm.startswith("insert into rooms"):
            (code, fmt, timer_seconds, seed, host_id, status,
             turn_started_at, failure_reason, moves, match_moves, draft_mode,
             is_open) = params
            # `moves`/`match_moves` arrive wrapped in psycopg.types.json.Json in real
            # code; unwrap to the plain list each wraps, exactly what a real jsonb
            # column reads back.
            moves_value = moves.obj if hasattr(moves, "obj") else moves
            match_moves_value = match_moves.obj if hasattr(match_moves, "obj") else match_moves
            existing = self.rooms.get(code)
            if existing is None:
                # version starts at 1, matching the real INSERT's own literal -- a room
                # that has been written once is at version 1, not 0.
                self.rooms[code] = (code, fmt, timer_seconds, seed, host_id, status,
                                     turn_started_at, failure_reason, moves_value,
                                     match_moves_value, draft_mode, is_open, 1)
            else:
                # Mirrors the real `on conflict (code) do update set` clause exactly --
                # only status/turn_started_at/failure_reason/moves/match_moves are ever
                # written to an EXISTING row; format/timer_seconds/seed/host_id/
                # draft_mode/is_open keep whatever the row already had, "set once at
                # creation, never updated" per `_save_room`'s own comment. Getting this
                # wrong here would make `test_play_again_resets_a_complete_room_to_lobby`'s
                # own `seed != ` assertion pass for the wrong reason -- a plain
                # `_save_room` call NOT actually changing the seed in real Postgres,
                # silently papered over by a fake that changes it anyway.
                (ecode, efmt, etimer, eseed, ehost, _estatus,
                 _eturn, _efail, _emoves, _ematch, edraft, eopen, eversion) = existing
                # `version = rooms.version + 1` in the real ON CONFLICT clause: it
                # increments on EVERY write, whatever changed, and is never reset --
                # including by play_again, which resets everything else about the room.
                self.rooms[code] = (ecode, efmt, etimer, eseed, ehost, status,
                                     turn_started_at, failure_reason, moves_value,
                                     match_moves_value, edraft, eopen, eversion + 1)
            # RETURNING version -- `_save_room` reads this back onto the Room object so
            # the response it serves carries the version its own write produced.
            return FakeCursor([(self.rooms[code][12],)])

        if sql_norm.startswith("update rooms set seed"):
            seed, code = params
            existing = self.rooms.get(code)
            if existing is not None:
                row = list(existing)
                row[3] = seed
                self.rooms[code] = tuple(row)
            return FakeCursor([])

        raise AssertionError(f"FakeConn does not know this query: {sql_norm[:80]!r}")


@pytest.fixture
def conn():
    return FakeConn()


def card(n: int, positions: frozenset[int], *, fs: int = 1, role: str = "batter",
         bowl: float | None = None, overseas: bool = False) -> Card:
    return Card(fs_id=fs, person_id=f"c{n}", name=f"c{n}", bat=0.5, bowl=bowl,
                role=role, overseas=overseas, positions=positions,
                keeper_eligible=(role == "keeper"))


def deck_of(n_fs: int = 24, per_fs: int = 16) -> Deck:
    """Every franchise-season offers a keeper, several bowlers and a full position
    spread -- the same proven shape `tests/test_web.py` uses, so the deal-time guarantee
    completes quickly and reliably for a human's own replay and a CPU's alike."""
    by_fs: dict[int, list[Card]] = {}
    n = 0
    for fs in range(1, n_fs + 1):
        cards = []
        for i in range(per_fs):
            start = i % XI_SIZE
            pos = frozenset(((start + k) % XI_SIZE) + 1 for k in range(4))
            role = "keeper" if i == 0 else "batter"
            bowl = 0.1 if i % 2 == 0 else None
            overseas = i % 6 == 0
            cards.append(card(n, pos, fs=fs, role=role, bowl=bowl, overseas=overseas))
            n += 1
        by_fs[fs] = cards
    return Deck(by_fs, sorted(by_fs))


def deck_of_no_keeper(n_fs: int = 6, per_fs: int = 16) -> Deck:
    """The universal stranding fixture: identical shape to `deck_of` -- full position
    spread, plenty of bowlers -- except NO card anywhere is a keeper. `eligible`'s
    forward check (A73) only tests keeper coverage on the very LAST pick
    (`remaining_after == 0`), so this deck completes any seat's first eleven picks
    completely normally and only reveals it is unplayable on the twelfth -- for EVERY
    seat, since none of them can ever have a keeper. Distinct from
    `deck_of_one_keeper` below, which isolates a stranding caused by another seat."""
    by_fs: dict[int, list[Card]] = {}
    n = 0
    for fs in range(1, n_fs + 1):
        cards = []
        for i in range(per_fs):
            start = i % XI_SIZE
            pos = frozenset(((start + k) % XI_SIZE) + 1 for k in range(4))
            bowl = 0.1 if i % 2 == 0 else None
            cards.append(card(n, pos, fs=fs, role="batter", bowl=bowl, overseas=False))
            n += 1
        by_fs[fs] = cards
    return Deck(by_fs, sorted(by_fs))


def deck_of_one_keeper(n_fs: int = 12, per_fs: int = 16) -> Deck:
    """Exactly ONE keeper-eligible card in the whole deck -- unlike `deck_of_no_keeper`'s
    zero (which strands every seat universally, regardless of order), this isolates a
    stranding caused SPECIFICALLY by one seat taking the only keeper before another
    seat's own last pick needs one. Same shape as `deck_of`, just one card's role
    changed -- giving the keeper a WIDER position window than normal was tried and
    made things worse, not better: a universally-eligible card is exactly the one most
    likely to be the sole survivor once a seat's own open slots have narrowed late in
    the draft, which is the opposite of what a seat trying to AVOID it needs."""
    by_fs: dict[int, list[Card]] = {}
    n = 0
    keeper_given = False
    for fs in range(1, n_fs + 1):
        cards = []
        for i in range(per_fs):
            start = i % XI_SIZE
            pos = frozenset(((start + k) % XI_SIZE) + 1 for k in range(4))
            bowl = 0.1 if i % 2 == 0 else None
            role = "batter"
            if not keeper_given and fs == 1 and i == 0:
                role = "keeper"
                keeper_given = True
            cards.append(card(n, pos, fs=fs, role=role, bowl=bowl, overseas=False))
            n += 1
        by_fs[fs] = cards
    return Deck(by_fs, sorted(by_fs))


DECK = deck_of()


def _make_room(conn, fmt: str = "final", timer_seconds: int = 15) -> tuple[rooms.Room, str]:
    return rooms.create_room(conn, fmt, timer_seconds, "Host")


def _spread_index(candidates, made: int) -> int:
    """Cycles through options rather than always taking index 0 (which would draft
    nothing but a fixture's one keeper) -- the same shape `tests/test_web.py`'s own
    `_spread` uses."""
    return (made * 7) % len(candidates)


def _play_room_to_completion(conn, room: rooms.Room, deck: Deck,
                              human_ids: list[str]) -> rooms.Room:
    """Drive every human seat's own turns via `submit_pick` (spreading picks across
    candidates), until the room completes or fails. CPU turns resolve on their own,
    inside `submit_pick`/`room_state`'s own lazy `_resolve` -- this loop just keeps
    calling one or the other depending on whose turn it currently is."""
    made = {pid: 0 for pid in human_ids}
    room = rooms.room_state(conn, room.code, deck)
    guard = 0
    while room.status == "drafting" and guard < 2000:
        guard += 1
        replay = rooms.replay_room(room, deck)
        if replay.stranded or replay.complete:
            room = rooms.room_state(conn, room.code, deck)
            continue
        pid = replay.pending_seat_id
        if pid in human_ids:
            fs_id, candidates = replay.pending_deal
            seat = replay.seats[pid]
            # `pick_rational` rather than a spread/naive index. The naive one strands
            # roughly 7% of rooms (measured, 4 of 60) -- not a bug, but A73's documented
            # wildcard-optimism gap: the forward check is an OPTIMISTIC bound, and under a
            # shared pool one seat's picks can strand another. That made every test
            # asserting `status == "complete"` intermittently red for reasons having
            # nothing to do with what it was testing. A73 measures `rational` at 0 of
            # 2,000, and a helper standing in for a player should draft like one anyway.
            chosen, slot = pick_rational(candidates, rooms._draft_state(seat), random.Random(0))
            i = next(k for k, c in enumerate(candidates) if c.person_id == chosen.person_id)
            room = rooms.submit_pick(conn, room.code, pid, i, slot, deck)
            made[pid] += 1
        else:
            room = rooms.room_state(conn, room.code, deck)
    return room


# --- lobby: creation, joining, seat limits -----------------------------------------------

def test_create_room_seats_the_host_alone(conn):
    room, host_id = _make_room(conn, "cup")
    assert room.seats == 4
    assert list(room.players) == [host_id]
    assert not room.full
    assert room.status == "lobby"


def test_create_room_refuses_an_unknown_format_or_timer(conn):
    with pytest.raises(rooms.RoomError):
        rooms.create_room(conn, "triangular", 15, "Host")
    with pytest.raises(rooms.RoomError):
        rooms.create_room(conn, "final", 20, "Host")


def test_create_room_defaults_to_stat_mode(conn):
    room, _ = rooms.create_room(conn, "final", 15, "Host")
    assert room.draft_mode == "stat"


def test_create_room_honours_an_explicit_memory_mode(conn):
    room, _ = rooms.create_room(conn, "final", 15, "Host", draft_mode="memory")
    assert room.draft_mode == "memory"
    # Binding on every seat, not just the host's own choice at the moment of joining --
    # a later reload of the SAME room must still report it, proving it round-trips
    # through the DB rather than being a value only the creating call ever saw.
    reloaded = rooms._load_room(conn, room.code)
    assert reloaded.draft_mode == "memory"


def test_create_room_refuses_an_unknown_draft_mode(conn):
    with pytest.raises(rooms.RoomError):
        rooms.create_room(conn, "final", 15, "Host", draft_mode="illegible")


def test_join_room_fills_seats_until_full_then_refuses(conn):
    room, host_id = _make_room(conn, "final")
    room2, bob_id = rooms.join_room(conn, room.code, "Bob", DECK)
    assert room2.full
    with pytest.raises(rooms.RoomError):
        rooms.join_room(conn, room.code, "Carol", DECK)


def test_join_room_refuses_once_the_draft_has_started(conn):
    room, host_id = _make_room(conn, "final")
    rooms.join_room(conn, room.code, "Bob", DECK)
    rooms.start_room(conn, room.code, host_id, DECK)
    with pytest.raises(rooms.RoomError):
        rooms.join_room(conn, room.code, "Carol", DECK)


# --- leave: a lobby seat is actually freed, not just abandoned client-side --------------

def test_leave_room_frees_the_seat_for_a_real_rejoin(conn):
    """The bug this closes: without a real removal, a player who left and rejoined
    under the same room code got a second, duplicate seat rather than reclaiming their
    first one, as long as the room wasn't already full."""
    room, host_id = _make_room(conn, "cup")
    _, bob_id = rooms.join_room(conn, room.code, "Bob", DECK)
    room2 = rooms.leave_room(conn, room.code, bob_id)
    assert bob_id not in room2.players
    assert len(room2.players) == 1

    room3, bob_id_2 = rooms.join_room(conn, room.code, "Bob", DECK)
    assert bob_id_2 != bob_id
    assert len(room3.players) == 2
    assert sum(1 for p in room3.players.values() if p.name == "Bob") == 1


def test_leave_room_refuses_the_host(conn):
    room, host_id = _make_room(conn, "final")
    rooms.join_room(conn, room.code, "Bob", DECK)
    with pytest.raises(rooms.RoomError):
        rooms.leave_room(conn, room.code, host_id)


def test_leave_room_refuses_a_seat_not_in_the_room(conn):
    room, host_id = _make_room(conn, "final")
    with pytest.raises(rooms.RoomError):
        rooms.leave_room(conn, room.code, "not-a-real-seat")


def test_leave_room_refuses_once_the_draft_has_started(conn):
    room, host_id = _make_room(conn, "final")
    _, bob_id = rooms.join_room(conn, room.code, "Bob", DECK)
    rooms.start_room(conn, room.code, host_id, DECK)
    with pytest.raises(rooms.RoomError):
        rooms.leave_room(conn, room.code, bob_id)


# --- kick: the host's mirror of leave, against someone ELSE's seat ----------------------

def test_kick_player_frees_the_seat_for_a_real_rejoin(conn):
    room, host_id = _make_room(conn, "cup")
    _, bob_id = rooms.join_room(conn, room.code, "Bob", DECK)
    room2 = rooms.kick_player(conn, room.code, host_id, bob_id)
    assert bob_id not in room2.players
    assert len(room2.players) == 1

    room3, bob_id_2 = rooms.join_room(conn, room.code, "Bob", DECK)
    assert bob_id_2 != bob_id
    assert len(room3.players) == 2


def test_kick_player_is_host_only(conn):
    room, host_id = _make_room(conn, "cup")
    _, bob_id = rooms.join_room(conn, room.code, "Bob", DECK)
    _, carol_id = rooms.join_room(conn, room.code, "Carol", DECK)
    with pytest.raises(rooms.RoomError):
        rooms.kick_player(conn, room.code, bob_id, carol_id)


def test_kick_player_refuses_the_host_as_a_target(conn):
    room, host_id = _make_room(conn, "final")
    rooms.join_room(conn, room.code, "Bob", DECK)
    with pytest.raises(rooms.RoomError):
        rooms.kick_player(conn, room.code, host_id, host_id)


def test_kick_player_refuses_a_seat_not_in_the_room(conn):
    room, host_id = _make_room(conn, "final")
    with pytest.raises(rooms.RoomError):
        rooms.kick_player(conn, room.code, host_id, "not-a-real-seat")


def test_kick_player_refuses_once_the_draft_has_started(conn):
    room, host_id = _make_room(conn, "final")
    _, bob_id = rooms.join_room(conn, room.code, "Bob", DECK)
    rooms.start_room(conn, room.code, host_id, DECK)
    with pytest.raises(rooms.RoomError):
        rooms.kick_player(conn, room.code, host_id, bob_id)


# --- turn order: the snake, tested in isolation ------------------------------------------

def test_turn_seat_index_snake_order_4_seats():
    n = 4
    for move_no in range(n * TWELVE_SIZE):
        round_no, pos = divmod(move_no, n)
        expected = pos if round_no % 2 == 0 else n - 1 - pos
        assert rooms.turn_seat_index(move_no, n) == expected


def test_turn_seat_index_snake_order_10_seats():
    n = 10
    for move_no in range(n * TWELVE_SIZE):
        round_no, pos = divmod(move_no, n)
        expected = pos if round_no % 2 == 0 else n - 1 - pos
        assert rooms.turn_seat_index(move_no, n) == expected


# --- start: host-only, CPU seats fill the remaining seats ---------------------------------

def test_start_room_is_host_only(conn):
    room, host_id = _make_room(conn, "final")
    _, bob_id = rooms.join_room(conn, room.code, "Bob", DECK)
    with pytest.raises(rooms.RoomError):
        rooms.start_room(conn, room.code, bob_id, DECK)


def test_pending_deal_accounts_for_the_whole_squad(conn):
    """A64's own rule ("the deal shows the whole squad, greyed, not just the takeable
    part"), reused verbatim via `sess._blocked` rather than a second implementation --
    every card in the dealt franchise-season must appear as either a live candidate or
    a `pending_blocked` entry, and never both."""
    room, host_id = _make_room(conn, "final")
    rooms.join_room(conn, room.code, "Guest", DECK)
    room = rooms.start_room(conn, room.code, host_id, DECK)
    replay = rooms.replay_room(room, DECK)
    fs_id, candidates = replay.pending_deal
    assert replay.pending_blocked is not None

    whole_squad = {c.person_id for c in DECK.cards_by_fs[fs_id]}
    live = {c.person_id for c in candidates}
    blocked = {c.person_id for c, _ in replay.pending_blocked}
    assert live | blocked == whole_squad
    assert live.isdisjoint(blocked)


def test_start_room_fills_remaining_seats_with_historical_squads(conn):
    """A filler (`is_cpu`) seat no longer drafts a fantasy twelve at all -- it is handed
    a real historical franchise-season's own eleven the instant the room starts drafting
    (`game.season.historical_sides`, the same mechanism solo's own season opposition
    uses), with ZERO recorded moves of its own. Confirmed immediately after `start_room`,
    before the host has made a single pick. Checked against `viable` (keeper + 5
    bowlers), the rule the opposition path is actually built to satisfy -- not
    `order_errors`, which is A76's batting-position-eligibility rule for a DRAFTED
    twelve and does not apply here (see `historical_sides`'s own docstring)."""
    room, host_id = _make_room(conn, "cup")   # 4 seats, host is the only human
    room = rooms.start_room(conn, room.code, host_id, DECK)
    assert room.status == "drafting"
    filler_ids = [pid for pid, p in room.players.items() if p.is_cpu]
    assert len(filler_ids) == 3
    assert room.moves == []

    replay = rooms.replay_room(room, DECK)
    fs_ids = set()
    for pid in filler_ids:
        seat = replay.seats[pid]
        assert seat.done
        assert seat.historical_name is not None
        assert len(seat.order) == XI_SIZE and all(c is not None for c in seat.order)
        assert viable(seat.order)
        fs_ids.add(seat.order[0].fs_id)
    # Distinct franchise-seasons -- historical_sides' own dedup, confirmed at the room
    # level too, not just trusted from its docstring. Checked by fs_id, not the derived
    # display name -- this fixture's synthetic cards carry no franchise/season_year, so
    # every filler's name collapses to the same "None None" string regardless of dedup.
    assert len(fs_ids) == len(filler_ids)


def test_filler_squads_are_reproduced_identically_across_a_reload(conn):
    """A filler's eleven is a pure function of (room.seed, filler count), independent of
    `room.moves` entirely -- unlike the old turn-based CPU design, it needs no draft at
    all to be stable: two independent replays right after `start_room` must already
    agree, with no picks made by anyone yet."""
    room, host_id = _make_room(conn, "cup")
    room = rooms.start_room(conn, room.code, host_id, DECK)

    replay_a = rooms.replay_room(rooms.room_state(conn, room.code, DECK), DECK)
    replay_b = rooms.replay_room(rooms.room_state(conn, room.code, DECK), DECK)
    for pid, p in room.players.items():
        if not p.is_cpu:
            continue
        a, b = replay_a.seats[pid], replay_b.seats[pid]
        assert a.historical_name == b.historical_name
        assert [c.person_id if c else None for c in a.order] == \
            [c.person_id if c else None for c in b.order]
        assert (a.impact.person_id if a.impact else None) == \
            (b.impact.person_id if b.impact else None)


def test_a_filler_squad_is_unaffected_by_what_the_humans_draft(conn):
    """A filler seat's historical assignment reads only `room.seed` and the filler
    count -- never `room.moves`/the shared `taken` pool -- so drafting all the way to
    completion must not change which franchise-seasons the fillers ended up with."""
    room, host_id = _make_room(conn, "cup")
    room = rooms.start_room(conn, room.code, host_id, DECK)
    filler_ids = [pid for pid, p in room.players.items() if p.is_cpu]
    before = rooms.replay_room(room, DECK)
    before_names = {pid: before.seats[pid].historical_name for pid in filler_ids}

    room = _play_room_to_completion(conn, room, DECK, [host_id])
    assert room.status == "complete"
    after = rooms.replay_room(room, DECK)
    after_names = {pid: after.seats[pid].historical_name for pid in filler_ids}
    assert before_names == after_names


def test_a_human_may_draft_a_card_also_on_a_fillers_historical_squad(conn):
    """The whole point of the redesign: a filler's squad never enters the shared `taken`
    pool, so a human is never blocked from a card just because a filler's real
    historical eleven happens to include the same player. Checked directly against
    `eligible` (the exact predicate `replay_room`'s own `_deal_for` uses) rather than by
    hoping a real draft session happens to deal the filler's own franchise-season by
    chance -- deterministic, not a coin flip."""
    room, host_id = _make_room(conn, "final")   # 1 filler seat
    room = rooms.start_room(conn, room.code, host_id, DECK)
    filler_id = next(pid for pid, p in room.players.items() if p.is_cpu)
    replay = rooms.replay_room(room, DECK)
    filler_card = replay.seats[filler_id].order[0]
    assert filler_card is not None

    # No human move has been made yet, so the shared `taken` set is genuinely empty --
    # if the filler's own squad were (wrongly) added to it, this call would prove it by
    # excluding filler_card here.
    host_seat = replay.seats[host_id]
    candidates = list(eligible(
        DECK.cards_by_fs[filler_card.fs_id], set(), frozenset(host_seat.open_slots),
        host_seat.keeper_have, host_seat.bowl_have, host_seat.overseas_taken,
        TWELVE_SIZE,
    ))
    assert any(c.person_id == filler_card.person_id for c in candidates)


# --- play again: resets a finished room back to its own lobby, in place -------------------

def test_play_again_resets_a_complete_room_to_lobby(conn):
    room, host_id = _make_room(conn, "final")
    _, bob_id = rooms.join_room(conn, room.code, "Bob", DECK)
    room = rooms.start_room(conn, room.code, host_id, DECK)
    room = _play_room_to_completion(conn, room, DECK, [host_id, bob_id])
    assert room.status == "complete"

    room2 = rooms.play_again(conn, room.code, bob_id)
    assert room2.status == "lobby"
    assert room2.moves == []
    assert room2.match_moves == []
    assert room2.failure_reason is None
    # Same code, same real seats, same settings -- only the seed (and status/logs)
    # actually changes.
    assert room2.code == room.code
    assert room2.host_id == room.host_id
    assert room2.format == room.format
    assert room2.timer_seconds == room.timer_seconds
    assert room2.draft_mode == room.draft_mode
    assert set(room2.players) == {host_id, bob_id}

    # The seed check is against a FRESH reload, not `room2` itself: `_save_room`'s own
    # `on conflict` clause deliberately excludes `seed` (same as format/timer_seconds/
    # host_id/draft_mode -- "set once at creation, never updated"), so the seed change
    # needs its own dedicated write. Comparing `room2.seed` alone would still read the
    # new value even if that write were missing, since it's the same in-memory object
    # `play_again` set `.seed` on before ever touching the database -- only a reload
    # proves the new seed actually reached the row rather than living in Python only.
    reloaded = rooms._load_room(conn, room.code)
    assert reloaded.seed == room2.seed
    assert reloaded.seed != room.seed


def test_play_again_drops_cpu_seats(conn):
    room, host_id = _make_room(conn, "cup")   # 4 seats, host is the only human
    room = rooms.start_room(conn, room.code, host_id, DECK)
    cpu_ids = [pid for pid, p in room.players.items() if p.is_cpu]
    assert len(cpu_ids) == 3
    room = _play_room_to_completion(conn, room, DECK, [host_id])
    assert room.status == "complete"

    room2 = rooms.play_again(conn, room.code, host_id)
    assert list(room2.players) == [host_id]
    assert not any(p.is_cpu for p in room2.players.values())


def test_play_again_works_on_a_failed_room_too(conn):
    room, host_id = _make_room(conn, "final")
    room = rooms.start_room(conn, room.code, host_id, DECK)
    room = rooms._load_room(conn, room.code)
    room.status = "failed"
    room.failure_reason = "stranded on seat X's turn"
    rooms._save_room(conn, room)

    room2 = rooms.play_again(conn, room.code, host_id)
    assert room2.status == "lobby"
    assert room2.failure_reason is None


def test_play_again_is_a_no_op_once_the_room_is_already_back_in_lobby(conn):
    """Idempotent by design: a second player's own click landing after the first's
    reset already went through must not re-roll the seed or otherwise mutate the room
    a second time -- it just returns the already-reset room."""
    room, host_id = _make_room(conn, "final")
    _, bob_id = rooms.join_room(conn, room.code, "Bob", DECK)
    room = rooms.start_room(conn, room.code, host_id, DECK)
    room = _play_room_to_completion(conn, room, DECK, [host_id, bob_id])

    room2 = rooms.play_again(conn, room.code, host_id)
    room3 = rooms.play_again(conn, room.code, bob_id)
    assert room3.seed == room2.seed
    assert room3.status == "lobby"


def test_play_again_refuses_a_seat_not_in_the_room(conn):
    room, host_id = _make_room(conn, "final")
    room = rooms.start_room(conn, room.code, host_id, DECK)
    room = _play_room_to_completion(conn, room, DECK, [host_id])
    with pytest.raises(rooms.RoomError):
        rooms.play_again(conn, room.code, "not-a-real-seat")


def test_play_again_refuses_a_room_still_in_progress(conn):
    room, host_id = _make_room(conn, "final")
    _, bob_id = rooms.join_room(conn, room.code, "Bob", DECK)
    room = rooms.start_room(conn, room.code, host_id, DECK)
    with pytest.raises(rooms.RoomError):
        rooms.play_again(conn, room.code, host_id)


def test_play_again_gives_the_new_lobby_a_fully_working_next_draft(conn, monkeypatch):
    """Not just a status flip -- the reset room must actually be playable end to end,
    same as any other fresh lobby.

    Pinned to a fixed seed, because otherwise this asserts something luck controls. Both
    `create_room` and `play_again` mint a random seed, and a room drafted by a
    non-strategic policy strands outright some of the time -- measured at 4 of 60 fresh
    rooms here, which is A73's own documented wildcard-optimism gap (the forward check is
    an OPTIMISTIC bound, and under a shared pool one seat can strand another). That is a
    real property of the archive, not a defect to engineer away, so the fix is to stop
    this test depending on it rather than to soften the assertion: a stranded draft says
    nothing about whether `play_again` produced a working lobby, which is what is on
    trial here.
    """
    monkeypatch.setattr(rooms.sess, "new_seed", lambda: 3)
    room, host_id = _make_room(conn, "final")
    _, bob_id = rooms.join_room(conn, room.code, "Bob", DECK)
    room = rooms.start_room(conn, room.code, host_id, DECK)
    room = _play_room_to_completion(conn, room, DECK, [host_id, bob_id])

    room = rooms.play_again(conn, room.code, host_id)
    assert room.status == "lobby"
    room = rooms.start_room(conn, room.code, host_id, DECK)
    room = _play_room_to_completion(conn, room, DECK, [host_id, bob_id])
    assert room.status == "complete"


# --- open/closed rooms: the public browse list ---------------------------------------------

def test_a_room_defaults_to_closed(conn):
    room, host_id = _make_room(conn, "final")
    assert room.is_open is False


def test_is_open_round_trips_through_load_and_save(conn):
    room, host_id = rooms.create_room(conn, "final", 30, "Host", is_open=True)
    reloaded = rooms._load_room(conn, room.code)
    assert reloaded.is_open is True


def test_is_open_is_set_once_at_creation_never_updated(conn):
    """Joins format/timer_seconds/seed/host_id/draft_mode: `_save_room`'s own ON
    CONFLICT clause must never let a later write flip it."""
    room, host_id = rooms.create_room(conn, "final", 30, "Host", is_open=True)
    rooms.join_room(conn, room.code, "Guest", DECK)   # a second _save_room call
    reloaded = rooms._load_room(conn, room.code)
    assert reloaded.is_open is True


def test_list_open_rooms_excludes_closed_full_and_started_rooms(conn):
    open_room, open_host = rooms.create_room(conn, "final", 30, "Alice", is_open=True)

    closed_room, _ = rooms.create_room(conn, "final", 30, "Bob", is_open=False)

    full_room, full_host = rooms.create_room(conn, "final", 30, "Carl", is_open=True)
    rooms.join_room(conn, full_room.code, "Dave", DECK)   # final = 2 seats, now full

    started_room, started_host = rooms.create_room(conn, "final", 30, "Eve", is_open=True)
    rooms.join_room(conn, started_room.code, "Frank", DECK)
    rooms.start_room(conn, started_room.code, started_host, DECK)

    codes = {r.code for r in rooms.list_open_rooms(conn)}
    assert open_room.code in codes
    assert closed_room.code not in codes
    assert full_room.code not in codes
    assert started_room.code not in codes


def test_list_open_rooms_reports_the_right_shape_and_order(conn):
    room, host_id = rooms.create_room(conn, "cup", 15, "Alice", is_open=True)
    rooms.join_room(conn, room.code, "Bob", DECK)

    rows = rooms.list_open_rooms(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row.code == room.code
    assert row.format == "cup"
    assert row.timer_seconds == 15
    assert row.host_name == "Alice"
    assert row.seats_filled == 2

    # Newest first.
    later_room, _ = rooms.create_room(conn, "final", 30, "Carl", is_open=True)
    rows = rooms.list_open_rooms(conn)
    assert rows[0].code == later_room.code


def test_list_open_rooms_respects_the_limit(conn, monkeypatch):
    monkeypatch.setattr(rooms, "OPEN_ROOMS_LIMIT", 2)
    for name in ("Alice", "Bob", "Carl"):
        rooms.create_room(conn, "final", 30, name, is_open=True)
    assert len(rooms.list_open_rooms(conn)) == 2


# --- the live turn: lazy, per-turn resolution ----------------------------------------------

def test_load_room_lock_false_omits_the_row_lock(conn):
    """`room_state`'s fast path (own docstring: diagnosed as the cause of drafts
    freezing on a pick, since every 2-second poll used to take the same write lock a
    pick needs) depends on `_load_room(lock=False)` issuing a plain SELECT with no FOR
    UPDATE. Checked against the actual SQL text sent, not just that the call returns
    the right data -- a correct return value says nothing about whether the lock was
    really skipped, and `lock=True` (the default every mutating path still uses) must
    still take it."""
    room, _host_id = _make_room(conn, "final")

    seen: list[str] = []
    real_execute = conn.execute

    def spy(sql, params=()):
        seen.append(sql)
        return real_execute(sql, params)

    conn.execute = spy

    rooms._load_room(conn, room.code, lock=False)
    assert not any("for update" in s.lower() for s in seen), \
        "lock=False must not take the row lock"

    seen.clear()
    rooms._load_room(conn, room.code)
    assert any("for update" in s.lower() for s in seen), \
        "lock=True (the default) must still take the row lock"


def test_save_room_writes_every_players_row_in_one_round_trip(conn):
    """`_save_room` used to loop and call `conn.execute` once PER PLAYER for the
    room_players upsert -- for a full 10-seat league room, that's up to nine wasted
    round trips on EVERY single write (a pick, an auto-resolve, anything that touches
    `_save_room`), since every existing player's row is a guaranteed ON CONFLICT DO
    NOTHING no-op once created. Verified against the actual number of `conn.execute`
    calls, not just the resulting data -- the one-round-trip and nine-round-trip
    versions produce IDENTICAL end state, so only a call count can tell them apart."""
    room, host_id = _make_room(conn, "league")
    for name in ["P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9"]:
        rooms.join_room(conn, room.code, name, DECK)
    room = rooms._load_room(conn, room.code)
    assert len(room.players) == 9

    seen: list[str] = []
    real_execute = conn.execute

    def spy(sql, params=()):
        seen.append(sql)
        return real_execute(sql, params)

    conn.execute = spy

    rooms._save_room(conn, room)
    room_player_inserts = [s for s in seen if "insert into room_players" in s.lower()]
    assert len(room_player_inserts) == 1, (
        f"expected exactly one round trip for all nine players' rows, got "
        f"{len(room_player_inserts)}"
    )


def test_the_turn_does_not_advance_before_the_timer_expires(conn, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(rooms.time, "time", lambda: clock["t"])

    room, host_id = _make_room(conn, "final", timer_seconds=15)
    _, bob_id = rooms.join_room(conn, room.code, "Bob", DECK)
    room = rooms.start_room(conn, room.code, host_id, DECK)
    assert room.round == 0

    clock["t"] += 5   # well inside the 15s window
    room = rooms.room_state(conn, room.code, DECK)
    assert room.round == 0
    assert room.status == "drafting"
    replay = rooms.replay_room(room, DECK)
    assert replay.pending_seat_id == host_id, "the host picks first (join order, round 0)"


def test_the_timer_auto_picks_a_random_eligible_candidate_for_the_active_seat(
    conn, monkeypatch,
):
    """A103: the timeout used to take the LOWEST-rated candidate, and this test asserted
    exactly that. The rule is now a random eligible one, so what is left to pin is that the
    pick is LEGAL and that the turn advances -- which is the part that actually has to hold
    however the card is chosen."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(rooms.time, "time", lambda: clock["t"])

    room, host_id = _make_room(conn, "final", timer_seconds=15)
    _, bob_id = rooms.join_room(conn, room.code, "Bob", DECK)
    room = rooms.start_room(conn, room.code, host_id, DECK)

    replay = rooms.replay_room(room, DECK)
    assert replay.pending_seat_id == host_id
    fs_id, candidates = replay.pending_deal

    clock["t"] += 16   # past the 15s window; nobody has picked
    room = rooms.room_state(conn, room.code, DECK)

    assert len(room.moves) == 1, "only the one active (host's) turn should auto-resolve"
    picked_move = room.moves[0]
    assert picked_move["seat"] == host_id
    assert 0 <= picked_move["index"] < len(candidates), "auto-pick was not one of the deal"
    picked_card = candidates[picked_move["index"]]
    assert picked_move["slot"] in picked_card.slots, "auto-pick placed somewhere illegal"

    replay2 = rooms.replay_room(room, DECK)
    assert replay2.pending_seat_id == bob_id, "the turn must advance to the next seat"


def test_the_timer_does_not_always_hand_out_the_worst_card(conn, monkeypatch):
    """The behavioural half of A103, at the level a player actually experiences it. Run the
    same timeout across several rooms: under the old `min(candidates, key=rating)` rule the
    worst card came back EVERY time, so this fails outright against it rather than merely
    becoming unlikely."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(rooms.time, "time", lambda: clock["t"])

    was_worst = []
    for _ in range(12):
        room, host_id = _make_room(conn, "final", timer_seconds=15)
        rooms.join_room(conn, room.code, "Bob", DECK)
        room = rooms.start_room(conn, room.code, host_id, DECK)

        replay = rooms.replay_room(room, DECK)
        _, candidates = replay.pending_deal
        worst = min(candidates, key=lambda c: c.rating).person_id

        clock["t"] += 16
        room = rooms.room_state(conn, room.code, DECK)
        picked = candidates[room.moves[0]["index"]].person_id
        was_worst.append(picked == worst)
        clock["t"] += 1   # fresh window for the next room

    assert not all(was_worst), "every timeout still handed out the lowest-rated card"


def test_an_auto_pick_never_touches_a_seat_that_already_picked(conn, monkeypatch):
    """Host picks in time; Bob lets the clock run out on HIS OWN turn. Only Bob's turn
    should be auto-assigned -- the host's own on-time pick must survive untouched."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(rooms.time, "time", lambda: clock["t"])

    room, host_id = _make_room(conn, "final", timer_seconds=15)
    _, bob_id = rooms.join_room(conn, room.code, "Bob", DECK)
    room = rooms.start_room(conn, room.code, host_id, DECK)

    replay = rooms.replay_room(room, DECK)
    fs_id, candidates = replay.pending_deal
    seat = replay.seats[host_id]
    slot = min(candidates[0].slots & seat.open_slots)
    room = rooms.submit_pick(conn, room.code, host_id, 0, slot, DECK)
    moves_after_host_pick = list(room.moves)
    assert moves_after_host_pick[0]["seat"] == host_id

    clock["t"] += 16
    room = rooms.room_state(conn, room.code, DECK)

    assert room.moves[0] == moves_after_host_pick[0], (
        "a seat that already picked must not be auto-picked over"
    )
    assert len(room.moves) == 2 and room.moves[1]["seat"] == bob_id, (
        "the AFK seat (Bob, whose turn it was) must have been resolved"
    )


def test_filler_seats_never_take_a_turn(conn, monkeypatch):
    """Filler seats are never `pending_seat_id`, at any point in a full draft -- there is
    no CPU-turn-resolution mechanism left to skip waiting for (retires the old
    `test_a_cpu_turn_resolves_instantly_without_waiting_for_the_timer`, whose whole
    premise -- a CPU fast-forwarding through its own live turn -- no longer applies)."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(rooms.time, "time", lambda: clock["t"])

    room, host_id = _make_room(conn, "cup", timer_seconds=15)
    room = rooms.start_room(conn, room.code, host_id, DECK)   # 3 filler seats fill in
    filler_ids = {pid for pid, p in room.players.items() if p.is_cpu}

    replay = rooms.replay_room(room, DECK)
    assert replay.pending_seat_id == host_id   # only the human ever gets a turn

    room = _play_room_to_completion(conn, room, DECK, [host_id])
    assert room.status == "complete"
    assert all(mv["seat"] not in filler_ids for mv in room.moves)
    assert len(room.moves) == TWELVE_SIZE   # the lone human's own twelve, nothing more


# --- the regression: a stranded turn must fail the room, never crash it -------------------

def test_a_room_that_strands_is_marked_failed_not_raised(conn):
    """`deck_of_no_keeper` completes any seat's first eleven picks fine (the forward
    check's own optimism, A73) and only the twelfth, unavoidably-doomed pick reveals
    there is no keeper anywhere in the archive -- discovered lazily by `_resolve`,
    whichever entry point (`room_state` here) next touches the room."""
    deck = deck_of_no_keeper()
    room, host_id = _make_room(conn, "final", timer_seconds=15)
    _, bob_id = rooms.join_room(conn, room.code, "Bob", deck)
    room = rooms.start_room(conn, room.code, host_id, deck)

    room = _play_room_to_completion(conn, room, deck, [host_id, bob_id])
    assert room.status == "failed"
    assert room.failure_reason and "stranded" in room.failure_reason

    # And the room stays gracefully failed on every subsequent call, rather than being a
    # one-shot recovery that crashes again the next time round.
    room_again = rooms.room_state(conn, room.code, deck)
    assert room_again.status == "failed"


def test_submit_pick_never_records_a_move_once_the_draft_has_stranded(conn):
    """The same corner, hit from the OTHER entry point: `replay_room` alone (a pure,
    side-effect-free read) already reveals a doomed turn the instant enough moves are
    recorded to reach it -- so by the time a human's own `submit_pick` call would try
    that turn, `_resolve` (called at the top of `submit_pick` itself) has already
    marked the room "failed", and `submit_pick` refuses rather than half-recording
    anything. Drives picks via `submit_pick` exclusively (never `room_state` as a
    fallback), so this exercises submit_pick's own call path specifically."""
    deck = deck_of_no_keeper()
    room, host_id = _make_room(conn, "final", timer_seconds=15)
    _, bob_id = rooms.join_room(conn, room.code, "Bob", deck)
    room = rooms.start_room(conn, room.code, host_id, deck)

    made = {host_id: 0, bob_id: 0}
    guard = 0
    while guard < 4 * TWELVE_SIZE:
        guard += 1
        replay = rooms.replay_room(room, deck)
        pid = replay.pending_seat_id
        if replay.stranded:
            break
        fs_id, candidates = replay.pending_deal
        i = _spread_index(candidates, made[pid])
        chosen = candidates[i]
        seat = replay.seats[pid]
        slot = min(chosen.slots & seat.open_slots)
        try:
            room = rooms.submit_pick(conn, room.code, pid, i, slot, deck)
        except rooms.RoomError:
            break
        made[pid] += 1
    else:
        pytest.fail("expected the draft to strand within the guard budget")

    moves_before = list(rooms._load_room(conn, room.code).moves)
    with pytest.raises(rooms.RoomError):
        rooms.submit_pick(conn, room.code, host_id, 0, 1, deck)
    reloaded = rooms._load_room(conn, room.code)
    assert reloaded.status == "failed"
    assert reloaded.moves == moves_before, "no further move can ever be recorded once failed"


# --- the shared pool: a card taken by one seat vanishes for every other -------------------

def test_a_card_taken_by_one_seat_becomes_unavailable_to_another(conn):
    deck = deck_of(n_fs=1, per_fs=16)   # one fs -- every pick necessarily reveals it
    room, host_id = _make_room(conn, "final", timer_seconds=15)
    _, bob_id = rooms.join_room(conn, room.code, "Bob", deck)
    room = rooms.start_room(conn, room.code, host_id, deck)

    replay = rooms.replay_room(room, deck)
    assert replay.pending_seat_id == host_id
    fs_id, candidates = replay.pending_deal
    taken_card = candidates[0]
    seat = replay.seats[host_id]
    slot = min(taken_card.slots & seat.open_slots)
    room = rooms.submit_pick(conn, room.code, host_id, 0, slot, deck)

    replay2 = rooms.replay_room(room, deck)
    assert replay2.pending_seat_id == bob_id
    _, bob_candidates = replay2.pending_deal
    assert all(c.person_id != taken_card.person_id for c in bob_candidates), (
        "a card drafted by one seat must vanish from every other seat's own dealt "
        "candidates, even from the very same franchise-season"
    )


def test_a_shared_pool_stranding_is_caused_by_another_seats_earlier_pick(conn, monkeypatch):
    """Unlike `deck_of_no_keeper`'s universal stranding, `deck_of_one_keeper` has
    exactly one keeper-eligible card in the whole deck. Host deliberately grabs it the
    moment it's offered; Bob's own last pick must then strand SPECIFICALLY because the
    shared pool's only keeper is already gone -- not because of anything Bob himself did.

    Bob must never take the keeper even if it's dealt to him, and a genuinely random
    room seed turned out to make that occasionally impossible: late in the draft, with
    only a couple of open slots left, the keeper (or whichever lone card is left in
    whatever franchise-season gets dealt) can legitimately be the ONLY eligible
    candidate on offer, with nothing to avoid it in favour of. Rather than a flaky
    seat-of-the-pants retry, the room's seed is pinned to one verified by hand (a sweep
    of 200 candidate seeds found this scenario resolves cleanly on the great majority
    of them -- 7 is simply one, matching this project's own convention of pinning a
    reproducible seed rather than tolerating a flaky one)."""
    monkeypatch.setattr(rooms.sess, "new_seed", lambda *a, **k: 7)
    deck = deck_of_one_keeper()
    room, host_id = _make_room(conn, "final", timer_seconds=15)
    _, bob_id = rooms.join_room(conn, room.code, "Bob", deck)
    room = rooms.start_room(conn, room.code, host_id, deck)

    made = {host_id: 0, bob_id: 0}
    keeper_taken = False
    guard = 0
    while not keeper_taken and guard < 500:
        guard += 1
        replay = rooms.replay_room(room, deck)
        assert not replay.stranded and not replay.complete, "keeper must be reachable first"
        pid = replay.pending_seat_id
        fs_id, candidates = replay.pending_deal
        seat = replay.seats[pid]
        keeper_idx = next((i for i, c in enumerate(candidates) if c.role == "keeper"), None)
        if pid == host_id and keeper_idx is not None:
            i = keeper_idx
        else:
            i = _spread_index(candidates, made[pid])
            if candidates[i].role == "keeper":   # never let Bob take it, even by chance
                i = next(j for j, c in enumerate(candidates) if c.role != "keeper")
        chosen = candidates[i]
        slot = min(chosen.slots & seat.open_slots)
        room = rooms.submit_pick(conn, room.code, pid, i, slot, deck)
        made[pid] += 1
        if pid == host_id and chosen.role == "keeper":
            keeper_taken = True
    assert keeper_taken, "test setup failed to let the host reach the one keeper card"

    room = _play_room_to_completion(conn, room, deck, [host_id, bob_id])
    assert room.status == "failed"
    assert room.failure_reason and bob_id in room.failure_reason, (
        "the stranding must be attributed to Bob's turn, not the host's"
    )


def test_the_room_advances_per_turn_not_per_round(conn):
    """Under the old round-based design, ANY not-yet-done seat could submit a pick
    anytime during a shared round -- there was no such thing as "not your turn yet".
    Under the new design, Bob submitting before the host has picked (still the host's
    turn, round 0) must be flatly refused -- proving turns are strictly ordered, not
    a round everyone can act within simultaneously."""
    room, host_id = _make_room(conn, "cup", timer_seconds=15)   # host + bob + 2 CPU
    _, bob_id = rooms.join_room(conn, room.code, "Bob", DECK)
    room = rooms.start_room(conn, room.code, host_id, DECK)

    replay = rooms.replay_room(room, DECK)
    assert replay.pending_seat_id == host_id, "the host picks first (join order, round 0)"

    with pytest.raises(rooms.RoomError):
        rooms.submit_pick(conn, room.code, bob_id, 0, 1, DECK)

    # And once the host actually picks, the turn genuinely advances to Bob.
    fs_id, candidates = replay.pending_deal
    seat = replay.seats[host_id]
    slot = min(candidates[0].slots & seat.open_slots)
    room = rooms.submit_pick(conn, room.code, host_id, 0, slot, DECK)
    replay2 = rooms.replay_room(room, DECK)
    assert replay2.pending_seat_id == bob_id


# --- room_sides: what each format's match is built from ------------------------------------

@pytest.mark.parametrize("fmt", ["final", "cup", "league"])
def test_room_sides_gives_every_seat_a_legal_twelve(conn, fmt):
    """A drafting (human) seat's twelve is checked against `order_errors` -- the same
    A76 batting-position-eligibility rule the draft itself enforces. A filler seat's
    eleven is checked against `viable` instead -- the actual rule its OWN construction
    (`opposition_xi`/`opposition_order`) is built to satisfy (keeper + 5 bowlers), which
    deliberately does NOT reason about A76 bands at all (see `historical_sides`'s own
    docstring) -- `order_errors` is the wrong predicate for a squad that was never built
    against `card.positions` in the first place."""
    room, host_id = _make_room(conn, fmt, timer_seconds=15)
    room = rooms.start_room(conn, room.code, host_id, DECK)   # host + filler-filled rest
    room = _play_room_to_completion(conn, room, DECK, [host_id])
    assert room.status == "complete"
    sides = rooms.room_sides(room, DECK)
    assert len(sides) == rooms.ROOM_FORMATS[fmt]
    for pid, player, order, impact in sides:
        assert len(order) == XI_SIZE
        if player.is_cpu:
            assert viable([c for c in order if c]), f"filler seat {pid} is not viable"
        else:
            twelve = [c for c in order if c] + ([impact] if impact else [])
            assert order_errors(order, impact, twelve) == [], \
                f"seat {pid} is not a legal twelve"


# --- migration 030: the monotonic state counter ---------------------------------------
#
# The counter exists so a CLIENT can order responses that arrive out of order. Nothing in
# Python reads it, so these tests are the only thing holding it -- exactly the standing
# "a rule in behaviour, not in a CHECK, is unprotected without a test" case.

def test_every_write_increments_the_version(conn):
    """Whatever changed, and whether or not anything meaningful did. A version that only
    moved on 'interesting' writes would be useless for ordering, because the client has
    no way to know which writes the server considered interesting."""
    room, host_id = _make_room(conn, "final", timer_seconds=30)
    seen = [room.version]
    room, _bob = rooms.join_room(conn, room.code, "Bob", DECK)
    seen.append(room.version)
    room = rooms.start_room(conn, room.code, host_id, DECK)
    seen.append(room.version)
    replay = rooms.replay_room(room, DECK)
    _fs, candidates = replay.pending_deal
    seat = replay.seats[replay.pending_seat_id]
    slot = sorted(candidates[0].slots & seat.open_slots)[0]
    room = rooms.submit_pick(conn, room.code, replay.pending_seat_id, 0, slot, DECK)
    seen.append(room.version)
    assert seen == sorted(set(seen)), f"versions must strictly increase, got {seen}"


def test_version_keeps_climbing_across_play_again(conn):
    """`play_again` resets the room -- fresh seed, both move logs cleared, back to lobby.
    The version must NOT reset with it. This is the case that rules out deriving the
    counter from `len(moves)` (or anything else already stored), which was the first
    thing tried: a derived counter goes backwards here, and a client that has correctly
    been told to ignore anything older would then ignore the entire new game."""
    room, host_id = _make_room(conn, "final", timer_seconds=30)
    room, bob_id = rooms.join_room(conn, room.code, "Bob", DECK)
    room = rooms.start_room(conn, room.code, host_id, DECK)
    room = _play_room_to_completion(conn, room, DECK, [host_id, bob_id])
    assert room.status == "complete"
    before = room.version
    room = rooms.play_again(conn, room.code, host_id)
    assert room.status == "lobby"
    assert room.moves == [] and room.match_moves == []
    assert room.version > before, (
        f"version fell from {before} to {room.version} across play_again")


def test_a_poll_that_resolves_nothing_writes_nothing(conn, monkeypatch):
    """The write, not the lock, is what a redundant poll used to cost. Every connected
    seat escalates to the locked read when a turn's clock expires, but only the FIRST
    one through finds work -- the rest used to each write identical content, serialised
    one behind another, with any real pick submitted in that window queued behind the
    lot of them (and able to hit `lock_timeout` and fail outright).

    `_resolve` is stubbed to a no-op here, and that is the point rather than a shortcut:
    the branch under test only ever fires when ANOTHER caller resolved the turn between
    this one's lock-free read and its locked one, which single-threaded code cannot reach
    on its own -- `_resolve` otherwise always finds work on an expired clock, which is
    exactly why the first version of this test passed with the branch deleted. Stubbing
    it reproduces the lost race precisely: an escalated caller that finds nothing to do.

    Asserted on the version rather than by counting statements, because the version is
    what a write is DEFINED to move: if a save happens it moves, whatever the SQL was."""
    room, host_id = _make_room(conn, "final", timer_seconds=30)
    room, _bob = rooms.join_room(conn, room.code, "Bob", DECK)
    room = rooms.start_room(conn, room.code, host_id, DECK)

    # Clock still running: returns from the lock-free read, never escalates at all.
    live = rooms.room_state(conn, room.code, DECK)
    assert rooms.room_state(conn, room.code, DECK).version == live.version

    stored = rooms._load_room(conn, room.code, lock=False)
    stored.turn_started_at = time.time() - 999
    rooms._save_room(conn, stored)
    expired_at = rooms._load_room(conn, room.code, lock=False).version

    monkeypatch.setattr(rooms, "_resolve", lambda room, deck: None)
    for _ in range(5):
        again = rooms.room_state(conn, room.code, DECK)
        assert again.version == expired_at, (
            "a poll with nothing to resolve wrote anyway: "
            f"{expired_at} -> {again.version}")


def test_a_poll_that_does_resolve_still_writes(conn):
    """The other half, and the reason the test above cannot stand alone: skipping the
    write when nothing changed must not turn into skipping it when something did. An
    expired turn really does get auto-picked and really is persisted."""
    room, host_id = _make_room(conn, "final", timer_seconds=30)
    room, _bob = rooms.join_room(conn, room.code, "Bob", DECK)
    room = rooms.start_room(conn, room.code, host_id, DECK)
    stored = rooms._load_room(conn, room.code, lock=False)
    stored.turn_started_at = time.time() - 999
    rooms._save_room(conn, stored)
    before = rooms._load_room(conn, room.code, lock=False)

    after = rooms.room_state(conn, room.code, DECK)
    assert after.version > before.version, "the auto-pick was not persisted"
    assert len(after.moves) == len(before.moves) + 1
    assert rooms._load_room(conn, room.code, lock=False).moves == after.moves


def test_a_room_with_no_seats_still_loads(conn):
    """The outer join returns one all-NULL right-hand side rather than no rows at all,
    and `_load_room` has an explicit branch for it. Reachable for real: `leave_room`
    frees a seat, and the last one leaving empties the lobby."""
    room, host_id = _make_room(conn, "final", timer_seconds=30)
    conn.players[room.code] = {}
    loaded = rooms._load_room(conn, room.code, lock=False)
    assert loaded.players == {}
    assert loaded.code == room.code
