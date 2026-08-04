"""Live multiplayer rooms, match phase: the same real-toss engine A78 gave solo play,
brought into rooms once every seat's twelve is drafted, now running its independent
fixtures in true parallel rather than one at a time.

Two narrow jobs, not one decision routed to whichever seat happens to own it. The TOSS
WINNER calls bat/bowl for their own side and nobody else's -- a CPU-owned winner
auto-resolves to `game.season.TOSS_DEFAULT_ELECTS`, same as every other automatic move
source in this codebase. And only the HOST advances the room from one ROUND of matches
to the next, so the pacing of reveals is one knob, not a race between however many
seats are watching.

The Impact Player decision that used to be a third job here -- the host picking a
substitution for either side -- is gone, not merely hidden: `game.season.play_open` now
resolves it via `decide_impact` unconditionally, exactly like every fully-automatic
match in this codebase already did. It was never a meaningful choice for a side that
wasn't the host's own team, and A78's algorithm is trusted the same way here as it is
for the league's own round-robin (which has never paused for it).

A ROUND, not a fixture, is the pacing unit a room advances through. Two fixtures that
don't depend on each other -- a cup's two semis, a league's Qualifier 1 and Eliminator
-- open at the same time and are attempted independently every replay pass; one
fixture's still-pending toss never blocks its sibling's. `room.match_moves` entries
carry a `"stage"` tag for exactly this reason: two fixtures' toss calls can now arrive
in either order, so a flat position no longer identifies which fixture a move belongs
to. `"advance"` moves carry the ROUND's own label instead of a per-fixture stage, since
a round only opens the next one once every fixture inside it has resolved.

Fixtures are still built incrementally, not precomputed, because a cup's final and a
league's playoffs both depend on results the earlier rounds haven't produced yet when
replay starts -- the same dependency `game.season.run_playoffs` already has on
`run_league`'s own table, just interleaved here with the live toss pause instead of a
single non-interactive `play()` call per fixture.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from game.season import (
    TOSS_DEFAULT_ELECTS, JourneyAccumulator, Result, Side, Standing,
    _leader, _OpenMatchNeedsToss, _credit,
    fixtures as league_fixtures, play_open,
)
from game.simulator import Model
from web import rooms
from web.rooms import Room, RoomError


class _FixtureToss:
    """`play_open`'s move protocol, reduced to just the toss -- Impact is always
    automatic now, so `play_open` never calls anything else here -- and keyed by STAGE
    rather than position. Two fixtures open in the same round can have their toss calls
    answered in either order, so "next in the flat log" no longer identifies which
    fixture a move belongs to; its own `"stage"` tag does."""

    def __init__(self, moves: list, stage: str, is_cpu_side=lambda side: False):
        self._moves = moves
        self._stage = stage
        self._is_cpu_side = is_cpu_side

    def next_toss(self, winner: Side) -> str | None:
        # A CPU-owned winner auto-resolves to TOSS_DEFAULT_ELECTS -- there is no seat to
        # ask, and the shared move log has nothing to say about it either (a CPU's toss
        # call is never recorded, exactly A19: nobody's decision, nothing to store).
        if self._is_cpu_side(winner):
            return TOSS_DEFAULT_ELECTS
        for mv in self._moves:
            if isinstance(mv, dict) and mv.get("kind") == "toss" and mv.get("stage") == self._stage:
                return mv["elects"]
        return None


@dataclass
class RoomResultEntry:
    stage: str
    result: Result
    a_pid: str
    b_pid: str
    # Resolved ONCE, at the moment this fixture's own `pairs` (a single `_Driver`'s own
    # consistent list) is in scope -- never re-derived later via `_pid_of(r.home, ...)`
    # against a FRESH `_sides_with_pid` call, which would rebuild brand new `Side`
    # objects and make every `is` identity check fail silently (a real bug this
    # project's own dataclasses are otherwise careful about: `Side` has no `eq=False`,
    # and two structurally-identical-looking `Side`s from two different calls are
    # still different objects). `a_pid`/`b_pid` are "the two contesting this fixture,"
    # not necessarily home/away (the toss decides that); these two are.
    home_pid: str
    away_pid: str


@dataclass
class RoomStandingRow:
    pid: str
    standing: Standing


@dataclass
class RoomFixtureState:
    """One fixture in the CURRENTLY OPEN round -- pending on its own toss, or already
    resolved. Several of these can be open at once (a cup's two semis, a league's
    Qualifier 1 and Eliminator): each is attempted independently every replay pass, so
    one fixture's still-pending toss never blocks its sibling's -- this is the actual
    mechanism behind the room flowing its independent matches in parallel rather than
    forcing them through one at a time."""

    stage: str
    a_pid: str
    b_pid: str
    pending_toss_winner_pid: str | None = None
    home_pid: str | None = None      # known only once resolved (the toss decides it)
    away_pid: str | None = None
    result: Result | None = None


@dataclass
class RoomMatchReplay:
    """Everything `web/app.py` needs to build either an in-progress or a completed
    match-phase response -- rebuilt from scratch on every call, exactly
    `web.rooms.RoomReplay` one phase later.

    `current_round` is `None` only once `complete` is true -- there is nothing left
    open. Otherwise it holds every fixture in the room's currently-open round, mixed
    pending and resolved: a round only advances (`advance_ready`) once every one of
    them has a `result`, but a resolved fixture appears here immediately so its own
    players (and any spectator) see the score without waiting on their round-mates.

    `eliminated_pids` grows monotonically round by round -- see `replay_room_matches`'s
    per-format elimination logic -- so a player's own tournament can be known to be
    over well before the room as a whole reaches `complete`.
    """

    results: list[RoomResultEntry] = field(default_factory=list)
    complete: bool = False
    table: list[RoomStandingRow] | None = None        # league only, progressive
    champion_pid: str | None = None                   # complete only
    current_round: list[RoomFixtureState] | None = None
    round_label: str | None = None
    advance_ready: bool = False
    eliminated_pids: frozenset[str] = field(default_factory=frozenset)


def _sides_with_pid(room: Room, deck) -> list[tuple[str, Side]]:
    out = []
    for pid, p, order, impact in rooms.room_sides(room, deck):
        short = "".join(w[0] for w in p.name.split())[:4].upper() or pid[:4].upper()
        out.append((pid, Side(name=p.name, short=short, xi=order, impact=impact)))
    return out


def _pid_of(side: Side, pairs: list[tuple[str, Side]]) -> str:
    return next(pid for pid, s in pairs if s is side)


def _loser(result: Result, side_a: Side, side_b: Side) -> Side:
    """Whichever of the two named sides did NOT advance -- a tie falls to `side_a`,
    the same "higher (first-named) seed wins a tie" convention `game.season.run_cup`/
    `run_playoffs` already use, so a knockout fixture always has exactly one loser."""
    winner = result.winner or side_a
    return side_b if winner is side_a else side_a


class _Driver:
    """One pass over `room.match_moves`, resolving every fixture the room's format has
    opened so far. `resolve_fixture` never raises past its own call -- a fixture with no
    toss move yet returns a pending `RoomFixtureState` instead of aborting the round, so
    a sibling fixture already toss-answered still resolves on the same pass. This is
    the mechanism behind rooms flowing independent matches in parallel.

    `resolve_auto` (the league's own round-robin, and ONLY that) skips the move log
    entirely and plays every fixture through automatically, exactly A78's own
    algorithmic `play()`. This is a measured, not assumed, scope decision:
    `replay_room_matches` replays every already-resolved fixture from scratch on every
    call (A62's own cost, same profile solo's `replay_season` already accepts), and a
    seventy-fixture round-robin genuinely paced match by match was timed at multiple
    SECONDS per step by fixture 20 and climbing -- a real, measured architectural
    ceiling, not a guess. Every knockout fixture (cup's three; a league's four playoff
    matches) stays fully interactive."""

    def __init__(self, room: Room, model: Model, pairs: list[tuple[str, Side]]):
        self.room = room
        self.model = model
        self.pairs = pairs
        self.rng = random.Random(room.seed)
        self.entries: list[RoomResultEntry] = []

    def _pid(self, side: Side) -> str:
        return _pid_of(side, self.pairs)

    def _is_cpu(self, side: Side) -> bool:
        return self.room.players[self._pid(side)].is_cpu

    def _record(self, side_a: Side, side_b: Side, stage: str, r: Result) -> RoomResultEntry:
        a_pid, b_pid = self._pid(side_a), self._pid(side_b)
        entry = RoomResultEntry(
            stage=stage, result=r, a_pid=a_pid, b_pid=b_pid,
            home_pid=a_pid if r.home is side_a else b_pid,
            away_pid=a_pid if r.away is side_a else b_pid)
        self.entries.append(entry)
        return entry

    def resolve_auto(self, side_a: Side, side_b: Side, stage: str) -> RoomResultEntry:
        r = play_open(self.model, side_a, side_b, self.rng, stage=stage, moves=None)
        return self._record(side_a, side_b, stage, r)

    def resolve_fixture(self, side_a: Side, side_b: Side, stage: str) -> RoomFixtureState:
        a_pid, b_pid = self._pid(side_a), self._pid(side_b)
        cursor = _FixtureToss(self.room.match_moves, stage, is_cpu_side=self._is_cpu)
        try:
            r = play_open(self.model, side_a, side_b, self.rng, stage=stage, moves=cursor)
        except _OpenMatchNeedsToss as exc:
            return RoomFixtureState(
                stage=stage, a_pid=a_pid, b_pid=b_pid,
                pending_toss_winner_pid=self._pid(exc.winner))
        entry = self._record(side_a, side_b, stage, r)
        return RoomFixtureState(
            stage=stage, a_pid=a_pid, b_pid=b_pid,
            home_pid=entry.home_pid, away_pid=entry.away_pid, result=r)


def _resolve_round(d: _Driver, fixtures: list[tuple[str, Side, Side]]) -> list[RoomFixtureState]:
    return [d.resolve_fixture(side_a, side_b, stage) for stage, side_a, side_b in fixtures]


def _round_complete(states: list[RoomFixtureState]) -> bool:
    return all(s.result is not None for s in states)


def _advance_recorded(room: Room, round_label: str) -> bool:
    return any(isinstance(mv, dict) and mv.get("kind") == "advance" and mv.get("round") == round_label
               for mv in room.match_moves)


def _paused(entries: list[RoomResultEntry], states: list[RoomFixtureState], label: str,
            eliminated: set[str], *, advance_ready: bool = False,
            table: list[RoomStandingRow] | None = None) -> RoomMatchReplay:
    return RoomMatchReplay(
        results=list(entries), complete=False, table=table,
        current_round=states, round_label=label,
        advance_ready=advance_ready, eliminated_pids=frozenset(eliminated))


def _standings_table(pairs: list[tuple[str, Side]],
                      entries: list[RoomResultEntry]) -> list[RoomStandingRow]:
    standings = {pid: Standing(side=side) for pid, side in pairs}
    for e in entries:
        if e.stage != "league":
            continue
        r = e.result
        h, a = standings[e.home_pid], standings[e.away_pid]
        h.played += 1
        a.played += 1
        _credit(h, r.home_runs, r.home_balls, r.home_wickets,
                r.away_runs, r.away_balls, r.away_wickets)
        _credit(a, r.away_runs, r.away_balls, r.away_wickets,
                r.home_runs, r.home_balls, r.home_wickets)
        if r.winner is None:
            h.tied += 1
            a.tied += 1
        elif r.winner is r.home:
            h.won += 1
            a.lost += 1
        else:
            a.won += 1
            h.lost += 1
    ordered = sorted(standings.items(), key=lambda kv: (-kv[1].points, -kv[1].nrr, kv[1].side.name))
    return [RoomStandingRow(pid=pid, standing=s) for pid, s in ordered]


def replay_room_matches(room: Room, deck, model: Model) -> RoomMatchReplay:
    if room.status != "complete":
        raise RoomError(f"room is not complete yet (status: {room.status})")

    pairs = _sides_with_pid(room, deck)
    d = _Driver(room, model, pairs)
    eliminated: set[str] = set()

    if room.format == "final":
        states = _resolve_round(d, [("Final", pairs[0][1], pairs[1][1])])
        if not _round_complete(states):
            return _paused(d.entries, states, "Final", eliminated)
        r = states[0].result
        # A one-match format's "champion" is just its winner -- the same field cup/
        # league already use, so the journey card's own you_champion check needs no
        # per-format special case. None (not a fabricated winner) on a tie, and nobody
        # is marked eliminated either: a tie means both sides' runs simply end here
        # together, same as a decided result would, just with no champion to exclude.
        winner_pid = d._pid(r.winner) if r.winner else None
        if winner_pid is not None:
            eliminated.update(pid for pid, _ in pairs if pid != winner_pid)
        return RoomMatchReplay(results=list(d.entries), complete=True,
                                champion_pid=winner_pid, eliminated_pids=frozenset(eliminated))

    if room.format == "cup":
        # 1v4, 2v3 by JOIN order -- a room has no league table to seed a bracket from,
        # matching game.season.run_cup's own convention exactly.
        semi_fixtures = [
            ("Semi-final 1", pairs[0][1], pairs[3][1]),
            ("Semi-final 2", pairs[1][1], pairs[2][1]),
        ]
        semis = _resolve_round(d, semi_fixtures)
        if not _round_complete(semis):
            return _paused(d.entries, semis, "Semi-finals", eliminated)
        for (_, side_a, side_b), fs in zip(semi_fixtures, semis):
            eliminated.add(d._pid(_loser(fs.result, side_a, side_b)))
        if not _advance_recorded(room, "Semi-finals"):
            return _paused(d.entries, semis, "Semi-finals", eliminated, advance_ready=True)

        finalist1 = semis[0].result.winner or pairs[0][1]   # a tied semi falls to the higher seed
        finalist2 = semis[1].result.winner or pairs[1][1]
        final_states = _resolve_round(d, [("Final", finalist1, finalist2)])
        if not _round_complete(final_states):
            return _paused(d.entries, final_states, "Final", eliminated)
        final_r = final_states[0].result
        eliminated.add(d._pid(_loser(final_r, finalist1, finalist2)))
        winner_side = final_r.winner or finalist1
        return RoomMatchReplay(results=list(d.entries), complete=True,
                                champion_pid=d._pid(winner_side), eliminated_pids=frozenset(eliminated))

    # "league": TEAMS seats exactly (ROOM_FORMATS["league"] == game.season.TEAMS), the
    # full round-robin plus the IPL's own four-team playoff finish -- game.season.
    # run_league/run_playoffs' own fixture list and bracket, reproduced here round by
    # round instead of called once each.
    side_by_index = [side for _, side in pairs]
    for i, j in league_fixtures(len(pairs)):
        d.resolve_auto(side_by_index[i], side_by_index[j], "league")

    table = _standings_table(pairs, d.entries)
    top4_pids = {row.pid for row in table[:4]}
    # The non-top-4 are out the INSTANT the table settles -- well before the playoffs
    # even open, let alone finish. Nothing about their own tournament changes from here.
    eliminated.update(pid for pid, _ in pairs if pid not in top4_pids)
    if not _advance_recorded(room, "League"):
        return _paused(d.entries, [], "League", eliminated, advance_ready=True, table=table)

    first, second, third, fourth = (row.standing.side for row in table[:4])
    qual_fixtures = [("Qualifier 1", first, second), ("Eliminator", third, fourth)]
    quals = _resolve_round(d, qual_fixtures)
    if not _round_complete(quals):
        return _paused(d.entries, quals, "Qualifiers", eliminated, table=table)
    q1_r, elim_r = quals[0].result, quals[1].result
    # The real IPL asymmetry: the Eliminator's loser is out immediately, but Qualifier
    # 1's loser gets a second life via Qualifier 2 rather than being eliminated here.
    eliminated.add(d._pid(_loser(elim_r, third, fourth)))
    if not _advance_recorded(room, "Qualifiers"):
        return _paused(d.entries, quals, "Qualifiers", eliminated, advance_ready=True, table=table)

    q1_loser = second if q1_r.winner is first else first
    elim_winner = elim_r.winner or third      # a tied eliminator falls to the higher seed
    q2_states = _resolve_round(d, [("Qualifier 2", q1_loser, elim_winner)])
    if not _round_complete(q2_states):
        return _paused(d.entries, q2_states, "Qualifier 2", eliminated, table=table)
    q2_r = q2_states[0].result
    eliminated.add(d._pid(_loser(q2_r, q1_loser, elim_winner)))
    if not _advance_recorded(room, "Qualifier 2"):
        return _paused(d.entries, q2_states, "Qualifier 2", eliminated, advance_ready=True, table=table)

    finalist = q1_r.winner or first
    other = q2_r.winner or q1_loser
    final_states = _resolve_round(d, [("Final", finalist, other)])
    if not _round_complete(final_states):
        return _paused(d.entries, final_states, "Final", eliminated, table=table)
    final_r = final_states[0].result
    eliminated.add(d._pid(_loser(final_r, finalist, other)))
    winner_side = final_r.winner or finalist
    return RoomMatchReplay(results=list(d.entries), complete=True, table=table,
                            champion_pid=d._pid(winner_side), eliminated_pids=frozenset(eliminated))


@dataclass
class RoomJourney:
    """One seat's own tournament, for the journey card -- the room-match analogue of
    `game.season.JourneyStats`, which cannot be reused directly (A79 note: it hard-
    requires a `Season` object's `.table`/`.playoffs`/`.champion`, none of which a room
    has -- every fixture here, round-robin or knockout, lives in one flat `results`
    list keyed by `.stage`). `acc` is exposed alongside the totals it was built from so
    the caller can map the seat's own twelve through it card by card, exactly
    `web/app.py`'s existing `_journey_entry(card, acc)` already does for solo play."""

    runs: int
    wickets: int
    played: int
    won: int
    lost: int
    tied: int
    champion: bool
    top_scorer: tuple[str, int]
    top_wicket_taker: tuple[str, int]
    acc: JourneyAccumulator


def room_journey(room: Room, replay: RoomMatchReplay, pid: str) -> RoomJourney | None:
    """`None` if `pid`'s own run in the tournament hasn't ended yet (still alive, not
    champion), or `pid` is not a real seat. This is deliberately NOT gated on
    `replay.complete` -- a player eliminated in a cup semi, or one of a league's
    non-top-4, has their own stats fully determined the moment their own last fixture
    resolves, and `replay.eliminated_pids` already tracks exactly that.

    Played/won/lost/tied are counted by scanning `replay.results` directly rather than
    reading a `Standing` row, because `final`/`cup` never build one at all (A79: no
    league stage) and a scan is correct for every format uniformly, including league.
    Reads `e.home_pid`/`e.away_pid` -- resolved ONCE inside `_Driver._record` against
    its own consistent `pairs` -- rather than re-deriving them here via a fresh
    `_sides_with_pid(room, deck)` call, which would rebuild brand new `Side` objects
    and make every `is` identity check against `replay.results[i].result.home/away`
    fail silently (found exactly this way, not assumed away: see A79's test coverage
    note). No `deck` parameter needed as a result.
    """
    if pid not in room.players:
        return None
    if pid != replay.champion_pid and pid not in replay.eliminated_pids:
        return None
    acc = JourneyAccumulator()
    played = won = lost = tied = 0
    for e in replay.results:
        if pid not in (e.home_pid, e.away_pid):
            continue
        played += 1
        is_home = pid == e.home_pid
        if is_home:
            acc.add_batting(e.result.home_innings)
            acc.add_bowling(e.result.away_innings)
        else:
            acc.add_batting(e.result.away_innings)
            acc.add_bowling(e.result.home_innings)
        if e.result.winner is None:
            tied += 1
        elif (e.result.winner is e.result.home) == is_home:
            won += 1
        else:
            lost += 1
    return RoomJourney(
        runs=acc.total_runs, wickets=acc.total_wickets,
        played=played, won=won, lost=lost, tied=tied,
        champion=replay.champion_pid == pid,
        top_scorer=_leader(acc.runs, acc.names),
        top_wicket_taker=_leader(acc.wickets, acc.names),
        acc=acc,
    )


def room_match_state(conn, code: str, deck, model) -> tuple[Room, RoomMatchReplay]:
    """Read-only poll: reuses `rooms.room_state`'s own loader (a no-op resolve once the
    draft is complete, matching every other room route's "load, maybe advance, save"
    shape) and replays the match phase on top."""
    room = rooms.room_state(conn, code, deck)
    return room, replay_room_matches(room, deck, model)


# --- mutators: authorisation lives here, not in game.season ------------------------------

def submit_toss(conn, code: str, player_id: str, stage: str, elects: str, deck, model) -> Room:
    if elects not in ("bat", "bowl"):
        raise RoomError(f"elects must be 'bat' or 'bowl', got {elects!r}")
    room = rooms._load_room(conn, code)
    replay = replay_room_matches(room, deck, model)
    if replay.current_round is None:
        raise RoomError("no toss is pending in this room right now")
    fixture = next((f for f in replay.current_round if f.stage == stage), None)
    if fixture is None:
        raise RoomError(f"no fixture named {stage!r} is open right now")
    if fixture.result is not None:
        raise RoomError(f"the toss for {stage!r} has already been answered")
    if fixture.pending_toss_winner_pid != player_id:
        raise RoomError("you did not win this toss")
    room.match_moves = room.match_moves + [{"kind": "toss", "stage": stage, "elects": elects}]
    rooms._save_room(conn, room)
    return room


def advance_match(conn, code: str, player_id: str, deck, model) -> Room:
    room = rooms._load_room(conn, code)
    if player_id != room.host_id:
        raise RoomError("only the host can move on to the next round")
    replay = replay_room_matches(room, deck, model)
    if not replay.advance_ready:
        raise RoomError("this room is not waiting to advance right now")
    room.match_moves = room.match_moves + [{"kind": "advance", "round": replay.round_label}]
    rooms._save_room(conn, room)
    return room
