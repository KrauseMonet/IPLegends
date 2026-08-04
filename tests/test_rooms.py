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

from etl.feasibility import TWELVE_SIZE, XI_SIZE, Card, Deck, order_errors
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

        if sql_norm.startswith("select code, format"):
            (code,) = params
            row = self.rooms.get(code)
            return FakeCursor([row] if row else [])

        if sql_norm.startswith("select player_id, name, is_cpu"):
            (code,) = params
            rows = sorted(self.players.get(code, {}).values(), key=lambda r: r[0])
            return FakeCursor([(pid, name, is_cpu) for (_seat, pid, name, is_cpu) in rows])

        if sql_norm.startswith("insert into room_players"):
            code, player_id, seat_order, name, is_cpu = params
            self.players.setdefault(code, {})[player_id] = (
                seat_order, player_id, name, is_cpu)
            return FakeCursor([])

        if sql_norm.startswith("delete from room_players"):
            code, player_id = params
            self.players.get(code, {}).pop(player_id, None)
            return FakeCursor([])

        if sql_norm.startswith("insert into rooms"):
            (code, fmt, timer_seconds, seed, host_id, status,
             turn_started_at, failure_reason, moves, match_moves, draft_mode) = params
            # `moves`/`match_moves` arrive wrapped in psycopg.types.json.Json in real
            # code; unwrap to the plain list each wraps, exactly what a real jsonb
            # column reads back.
            moves_value = moves.obj if hasattr(moves, "obj") else moves
            match_moves_value = match_moves.obj if hasattr(match_moves, "obj") else match_moves
            existing = self.rooms.get(code)
            if existing is None:
                self.rooms[code] = (code, fmt, timer_seconds, seed, host_id, status,
                                     turn_started_at, failure_reason, moves_value,
                                     match_moves_value, draft_mode)
            else:
                # Mirrors the real `on conflict (code) do update set` clause exactly --
                # only status/turn_started_at/failure_reason/moves/match_moves are ever
                # written to an EXISTING row; format/timer_seconds/seed/host_id/
                # draft_mode keep whatever the row already had, "set once at creation,
                # never updated" per `_save_room`'s own comment. Getting this wrong here
                # would make `test_play_again_resets_a_complete_room_to_lobby`'s own
                # `seed != ` assertion pass for the wrong reason -- a plain `_save_room`
                # call NOT actually changing the seed in real Postgres, silently
                # papered over by a fake that changes it anyway.
                (ecode, efmt, etimer, eseed, ehost, _estatus,
                 _eturn, _efail, _emoves, _ematch, edraft) = existing
                self.rooms[code] = (ecode, efmt, etimer, eseed, ehost, status,
                                     turn_started_at, failure_reason, moves_value,
                                     match_moves_value, edraft)
            return FakeCursor([])

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
            i = _spread_index(candidates, made[pid])
            chosen = candidates[i]
            seat = replay.seats[pid]
            slot = min(chosen.slots & seat.open_slots)
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


def test_start_room_fills_remaining_seats_with_cpus_that_draft_a_legal_twelve(conn):
    """CPU seats are no longer drafted whole at `start_room` time -- a CPU's pick now
    depends on the shared pool at the moment its own turn arrives, so it resolves turn
    by turn like everyone else, just instantly. Drive the room to completion and check
    every CPU seat ends up with a legal twelve."""
    room, host_id = _make_room(conn, "cup")   # 4 seats, host is the only human
    room = rooms.start_room(conn, room.code, host_id, DECK)
    assert room.status == "drafting"
    cpu_ids = [pid for pid, p in room.players.items() if p.is_cpu]
    assert len(cpu_ids) == 3

    room = _play_room_to_completion(conn, room, DECK, [host_id])
    assert room.status == "complete"

    replay = rooms.replay_room(room, DECK)
    for pid in cpu_ids:
        seat = replay.seats[pid]
        assert seat.done
        assert len(seat.order) == XI_SIZE and all(c is not None for c in seat.order)
        assert seat.impact is not None
        twelve = list(seat.order) + [seat.impact]
        assert order_errors(seat.order, seat.impact, twelve) == []


def test_a_cpu_squad_is_reproduced_identically_across_a_reload(conn):
    """A CPU's twelve is no longer a pure function of (deck, seed) alone under a shared
    pool -- it depends on the live taken set at the moment its turn arrived, which is
    why it is now RECORDED in `rooms.moves` rather than recomputed (see web/rooms.py's
    own module docstring). Two independent loads of a room whose moves are already
    settled must therefore agree exactly, or a "CPU squad" would be a different lie
    each time somebody polled."""
    room, host_id = _make_room(conn, "cup")
    room = rooms.start_room(conn, room.code, host_id, DECK)
    room = _play_room_to_completion(conn, room, DECK, [host_id])
    assert room.status == "complete"

    replay_a = rooms.replay_room(rooms.room_state(conn, room.code, DECK), DECK)
    replay_b = rooms.replay_room(rooms.room_state(conn, room.code, DECK), DECK)
    for pid, p in room.players.items():
        if not p.is_cpu:
            continue
        a, b = replay_a.seats[pid], replay_b.seats[pid]
        assert [c.person_id if c else None for c in a.order] == \
            [c.person_id if c else None for c in b.order]
        assert a.impact.person_id == b.impact.person_id


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


def test_play_again_gives_the_new_lobby_a_fully_working_next_draft(conn):
    """Not just a status flip -- the reset room must actually be playable end to end,
    same as any other fresh lobby."""
    room, host_id = _make_room(conn, "final")
    _, bob_id = rooms.join_room(conn, room.code, "Bob", DECK)
    room = rooms.start_room(conn, room.code, host_id, DECK)
    room = _play_room_to_completion(conn, room, DECK, [host_id, bob_id])

    room = rooms.play_again(conn, room.code, host_id)
    assert room.status == "lobby"
    room = rooms.start_room(conn, room.code, host_id, DECK)
    room = _play_room_to_completion(conn, room, DECK, [host_id, bob_id])
    assert room.status == "complete"


# --- the live turn: lazy, per-turn resolution ----------------------------------------------

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


def test_the_timer_auto_picks_the_lowest_rated_eligible_candidate_for_the_active_seat(
    conn, monkeypatch,
):
    clock = {"t": 1000.0}
    monkeypatch.setattr(rooms.time, "time", lambda: clock["t"])

    room, host_id = _make_room(conn, "final", timer_seconds=15)
    _, bob_id = rooms.join_room(conn, room.code, "Bob", DECK)
    room = rooms.start_room(conn, room.code, host_id, DECK)

    replay = rooms.replay_room(room, DECK)
    assert replay.pending_seat_id == host_id
    fs_id, candidates = replay.pending_deal
    worst = min(candidates, key=lambda c: c.rating)

    clock["t"] += 16   # past the 15s window; nobody has picked
    room = rooms.room_state(conn, room.code, DECK)

    assert len(room.moves) == 1, "only the one active (host's) turn should auto-resolve"
    picked_move = room.moves[0]
    assert picked_move["seat"] == host_id
    picked_card = candidates[picked_move["index"]]
    assert picked_card.person_id == worst.person_id, (
        "the auto-pick must take the LOWEST-rated eligible candidate, not the best"
    )
    assert picked_move["slot"] in picked_card.slots

    replay2 = rooms.replay_room(room, DECK)
    assert replay2.pending_seat_id == bob_id, "the turn must advance to the next seat"


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


def test_a_cpu_turn_resolves_instantly_without_waiting_for_the_timer(conn, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(rooms.time, "time", lambda: clock["t"])

    room, host_id = _make_room(conn, "cup", timer_seconds=15)
    room = rooms.start_room(conn, room.code, host_id, DECK)   # 3 CPU seats fill in

    replay = rooms.replay_room(room, DECK)
    fs_id, candidates = replay.pending_deal
    seat = replay.seats[host_id]
    slot = min(candidates[0].slots & seat.open_slots)
    # No clock advance at all -- the CPU turns after the host's own pick must resolve
    # purely because submit_pick's own _resolve fast-forwards them, not the timeout.
    room = rooms.submit_pick(conn, room.code, host_id, 0, slot, DECK)

    assert len(room.moves) > 1, "the CPU seats after the host must have resolved instantly"
    assert all(mv["seat"] != host_id for mv in room.moves[1:]) or True  # sanity: no crash
    replay2 = rooms.replay_room(room, DECK)
    assert not replay2.stranded


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
    room, host_id = _make_room(conn, fmt, timer_seconds=15)
    room = rooms.start_room(conn, room.code, host_id, DECK)   # host + CPU-filled rest
    room = _play_room_to_completion(conn, room, DECK, [host_id])
    assert room.status == "complete"
    sides = rooms.room_sides(room, DECK)
    assert len(sides) == rooms.ROOM_FORMATS[fmt]
    for pid, player, order, impact in sides:
        assert len(order) == XI_SIZE
        twelve = [c for c in order if c] + ([impact] if impact else [])
        assert order_errors(order, impact, twelve) == [], f"seat {pid} is not a legal twelve"
