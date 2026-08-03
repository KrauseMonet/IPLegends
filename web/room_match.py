"""Live multiplayer rooms, match phase: the same per-match toss + break-time Impact
engine A78 gave solo play, brought into rooms once every seat's twelve is drafted.

Confirmed with the user: three narrow jobs, not one decision routed to whichever seat
happens to own it. The TOSS WINNER calls bat/bowl for their own side and nobody else's
-- a CPU-owned winner auto-resolves to `game.season.TOSS_DEFAULT_ELECTS`, same as every
other automatic move source in this codebase. The HOST decides every Impact Player
choice, for either side, in every match -- simulation decisions are the host's job, not
the owning player's, which is what keeps a room from ever needing to work out who
currently controls "the other side" mid-match. And only the HOST advances from one
match's result to the next, so the pacing of reveals is one knob, not a race between
however many seats are watching.

`game.season.play_open` already generalises the toss + break-time Impact mechanism to
two PEER sides (either can win the toss, either can have a break-time decision) --
this module's only job is authorisation and persistence: mapping `play_open`'s pause
exceptions (carried by `Side` identity) back to player_ids, and replaying
`rooms.match_moves` from scratch on every call, exactly `web.rooms.replay_room`'s own
"resolve on read" contract (A62) one phase later.

Fixtures are built incrementally, not precomputed, because a cup's final and a
league's playoffs both depend on results the earlier fixtures haven't produced yet
when replay starts -- the same dependency `game.season.run_playoffs` already has on
`run_league`'s own table, just interleaved here with the live toss/Impact pause
instead of a single non-interactive `play()` call per fixture.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace

from game.season import (
    TOSS_DEFAULT_ELECTS, ImpactPick, Result, Side, Standing, _OpenMatchNeedsImpact,
    _OpenMatchNeedsToss, _credit, fixtures as league_fixtures, play_open,
)
from game.simulator import Model
from web import rooms
from web.rooms import Room, RoomError


class _RoomMatchCursor:
    """A read-only view over the tail of `room.match_moves`, bound to ONE fixture --
    `play_open`'s own move protocol, generalised from `web/season_session.py`'s
    `MoveCursor` to identity-keyed toss/impact instead of seat-indexed. `consumed`
    tracks exactly how many entries this fixture used, so the caller can advance the
    outer flat position by that many once the fixture resolves without pausing --
    mirrors `MoveCursor.emitted`'s own bookkeeping role.
    """

    def __init__(self, moves: list, is_cpu_side=lambda side: False):
        self._moves = moves
        self._i = 0
        self._is_cpu_side = is_cpu_side

    @property
    def consumed(self) -> int:
        return self._i

    def _next(self, expected_kind: str) -> dict | None:
        if self._i >= len(self._moves):
            return None
        mv = self._moves[self._i]
        if not isinstance(mv, dict) or mv.get("kind") != expected_kind:
            raise RoomError(
                f"expected a {expected_kind!r} move at match-move position {self._i}, "
                f"got {mv!r}")
        self._i += 1
        return mv

    def next_toss(self, winner: Side) -> str | None:
        # A CPU-owned winner auto-resolves to TOSS_DEFAULT_ELECTS -- there is no seat to
        # ask, and the shared move log has nothing to say about it either (a CPU's toss
        # call is never recorded, exactly A19: nobody's decision, nothing to store).
        if self._is_cpu_side(winner):
            return TOSS_DEFAULT_ELECTS
        mv = self._next("toss")
        return None if mv is None else mv["elects"]

    def next_impact(self, side: Side) -> ImpactPick | None:
        mv = self._next("impact")
        return None if mv is None else ImpactPick(mv["slot"])


@dataclass
class RoomResultEntry:
    stage: str
    result: Result
    a_pid: str
    b_pid: str


@dataclass
class RoomStandingRow:
    pid: str
    standing: Standing


@dataclass
class RoomMatchReplay:
    """Everything `web/app.py` needs to build either a paused or a completed match-phase
    response -- rebuilt from scratch on every call, exactly `web.rooms.RoomReplay` one
    phase later."""

    results: list[RoomResultEntry] = field(default_factory=list)
    complete: bool = False
    table: list[RoomStandingRow] | None = None        # league only, progressive
    champion_pid: str | None = None                   # cup/league only, complete only
    pending_kind: str | None = None                   # "toss" | "impact" | "advance" | None
    pending_stage: str | None = None
    pending_a_pid: str | None = None                  # the two contesting THIS fixture
    pending_b_pid: str | None = None
    pending_toss_winner_pid: str | None = None        # toss only
    pending_home_pid: str | None = None               # impact/advance only (known post-toss)
    pending_away_pid: str | None = None               # impact/advance only
    pending_impact_side_pid: str | None = None        # impact only
    pending_impact_discipline: str | None = None       # impact only
    pending_impact_first: object | None = None         # impact only, an Innings


def _sides_with_pid(room: Room, deck) -> list[tuple[str, Side]]:
    out = []
    for pid, p, order, impact in rooms.room_sides(room, deck):
        short = "".join(w[0] for w in p.name.split())[:4].upper() or pid[:4].upper()
        out.append((pid, Side(name=p.name, short=short, xi=order, impact=impact)))
    return out


def _pid_of(side: Side, pairs: list[tuple[str, Side]]) -> str:
    return next(pid for pid, s in pairs if s is side)


class _Driver:
    """One pass over `room.match_moves`, replaying every fixture the room's format has
    produced so far and stopping at the first pause or the natural end. Holding this as
    an object rather than a long function keeps `_play_fixture`'s pause-handling in one
    place regardless of which format's fixture-building calls it.

    `interactive=False` (the "league" format's own round-robin, and ONLY that) skips
    the move log entirely and plays every fixture through automatically, exactly A78's
    own algorithmic `play()` -- never `_OpenMatchNeedsToss`/`_OpenMatchNeedsImpact`,
    because `play_open(moves=None)` never raises them. This is a measured, not assumed,
    scope decision: `replay_room_matches` replays every already-resolved fixture from
    scratch on every call (A62's own cost, same profile solo's `replay_season` already
    accepts), and a seventy-fixture round-robin genuinely paced match by match was
    timed at multiple SECONDS per step by fixture 20 and climbing -- a real, measured
    architectural ceiling, not a guess. `final` (one fixture) and `cup` (three) stay
    fully interactive; `league`'s own PLAYOFFS (at most four fixtures, the dramatic
    part) still pause normally, only the bulk round-robin is exempted."""

    def __init__(self, room: Room, model: Model, pairs: list[tuple[str, Side]]):
        self.room = room
        self.model = model
        self.pairs = pairs
        self.rng = random.Random(room.seed)
        self.pos = 0
        self.entries: list[RoomResultEntry] = []
        self.paused: RoomMatchReplay | None = None

    def _pid(self, side: Side) -> str:
        return _pid_of(side, self.pairs)

    def _is_cpu(self, side: Side) -> bool:
        return self.room.players[self._pid(side)].is_cpu

    def play_fixture(self, side_a: Side, side_b: Side, stage: str,
                      interactive: bool = True) -> Result | None:
        """Returns the `Result` if this fixture resolved AND an advance move was found
        (or it is the very last fixture, handled by the caller), or `None` if replay
        should stop here -- `self.paused` is set to the reason in that case."""
        if self.paused is not None:
            return None
        a_pid, b_pid = self._pid(side_a), self._pid(side_b)
        if not interactive:
            r = play_open(self.model, side_a, side_b, self.rng, stage=stage, moves=None)
            self.entries.append(RoomResultEntry(stage=stage, result=r,
                                                a_pid=a_pid, b_pid=b_pid))
            return r
        cursor = _RoomMatchCursor(self.room.match_moves[self.pos:], is_cpu_side=self._is_cpu)
        try:
            r = play_open(self.model, side_a, side_b, self.rng, stage=stage, moves=cursor)
        except _OpenMatchNeedsToss as exc:
            self.paused = RoomMatchReplay(
                results=list(self.entries), complete=False,
                pending_kind="toss", pending_stage=stage,
                pending_a_pid=a_pid, pending_b_pid=b_pid,
                pending_toss_winner_pid=self._pid(exc.winner))
            return None
        except _OpenMatchNeedsImpact as exc:
            side_pid, opp_pid = self._pid(exc.side), self._pid(exc.opponent)
            home_pid, away_pid = (
                (side_pid, opp_pid) if exc.discipline == "bowl" else (opp_pid, side_pid))
            self.paused = RoomMatchReplay(
                results=list(self.entries), complete=False,
                pending_kind="impact", pending_stage=stage,
                pending_a_pid=a_pid, pending_b_pid=b_pid,
                pending_home_pid=home_pid, pending_away_pid=away_pid,
                pending_impact_side_pid=side_pid,
                pending_impact_discipline=exc.discipline, pending_impact_first=exc.first)
            return None

        self.entries.append(RoomResultEntry(stage=stage, result=r, a_pid=a_pid, b_pid=b_pid))
        self.pos += cursor.consumed
        return r

    def gate_on_advance(self, home_pid: str, away_pid: str, stage: str) -> bool:
        """Call after a fixture that is NOT the last one in the format resolves. Only
        the host may advance -- this consumes exactly one `{"kind": "advance"}` move
        (host-only, enforced by `web.rooms.advance_match`, not here) before the caller
        may build the NEXT fixture at all. Returns True if the room may proceed."""
        if self.paused is not None:
            return False
        if self.pos >= len(self.room.match_moves):
            self.paused = RoomMatchReplay(
                results=list(self.entries), complete=False,
                pending_kind="advance", pending_stage=stage,
                pending_home_pid=home_pid, pending_away_pid=away_pid)
            return False
        mv = self.room.match_moves[self.pos]
        if not isinstance(mv, dict) or mv.get("kind") != "advance":
            raise RoomError(
                f"expected an 'advance' move at match-move position {self.pos}, "
                f"got {mv!r}")
        self.pos += 1
        return True


def _standings_table(pairs: list[tuple[str, Side]],
                      entries: list[RoomResultEntry]) -> list[RoomStandingRow]:
    standings = {pid: Standing(side=side) for pid, side in pairs}
    for e in entries:
        if e.stage != "league":
            continue
        r = e.result
        h, a = standings[_pid_of(r.home, pairs)], standings[_pid_of(r.away, pairs)]
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

    if room.format == "final":
        side_a, side_b = pairs[0][1], pairs[1][1]
        r = d.play_fixture(side_a, side_b, "Final")
        if d.paused is not None:
            return d.paused
        return RoomMatchReplay(results=list(d.entries), complete=True)

    if room.format == "cup":
        # 1v4, 2v3 by JOIN order -- a room has no league table to seed a bracket from,
        # matching game.season.run_cup's own convention exactly.
        semi1 = d.play_fixture(pairs[0][1], pairs[3][1], "Semi-final 1")
        if d.paused is not None:
            return d.paused
        if not d.gate_on_advance(d._pid(semi1.home), d._pid(semi1.away), "Semi-final 1"):
            return d.paused

        semi2 = d.play_fixture(pairs[1][1], pairs[2][1], "Semi-final 2")
        if d.paused is not None:
            return d.paused
        if not d.gate_on_advance(d._pid(semi2.home), d._pid(semi2.away), "Semi-final 2"):
            return d.paused

        finalist1 = semi1.winner or pairs[0][1]     # a tied semi falls to the higher seed
        finalist2 = semi2.winner or pairs[1][1]
        final = d.play_fixture(finalist1, finalist2, "Final")
        if d.paused is not None:
            return d.paused
        return RoomMatchReplay(results=list(d.entries), complete=True,
                                champion_pid=d._pid(final.winner or finalist1))

    # "league": TEAMS seats exactly (ROOM_FORMATS["league"] == game.season.TEAMS), the
    # full round-robin plus the IPL's own four-team playoff finish -- game.season.
    # run_league/run_playoffs' own fixture list and bracket, reproduced here fixture by
    # fixture instead of called once each.
    #
    # The round-robin plays through NON-interactively (`interactive=False` -- A78's
    # automatic engine decides every toss and every Impact call for every one of the
    # seventy fixtures, nobody is ever asked live). This is a measured, not assumed,
    # scope decision: `replay_room_matches` replays every already-resolved fixture from
    # scratch on every call (A62's own "resolve on read"), and pacing all seventy
    # round-robin fixtures the way `final`/`cup` pace every match of theirs was timed at
    # multiple SECONDS per step by fixture 20 and climbing -- a real ceiling, not a
    # guess. `final` (one fixture) and `cup` (three) stay fully interactive throughout.
    # `league`'s own PLAYOFFS -- at most four fixtures, the dramatic part -- still pause
    # normally; only the bulk round-robin is exempted, so the host's pacing control is
    # reserved for where the volume actually allows it.
    side_by_index = [side for _, side in pairs]
    league_fx = league_fixtures(len(pairs))
    for i, j in league_fx:
        d.play_fixture(side_by_index[i], side_by_index[j], "league", interactive=False)

    table = _standings_table(pairs, d.entries)
    if not d.gate_on_advance(None, None, "league"):
        return replace(d.paused, table=table)
    first, second, third, fourth = (row.standing.side for row in table[:4])

    q1 = d.play_fixture(first, second, "Qualifier 1")
    if d.paused is not None:
        return replace(d.paused, table=table)
    if not d.gate_on_advance(d._pid(q1.home), d._pid(q1.away), "Qualifier 1"):
        return replace(d.paused, table=table)

    elim = d.play_fixture(third, fourth, "Eliminator")
    if d.paused is not None:
        return replace(d.paused, table=table)
    if not d.gate_on_advance(d._pid(elim.home), d._pid(elim.away), "Eliminator"):
        return replace(d.paused, table=table)

    q1_loser = second if q1.winner is first else first
    elim_winner = elim.winner or third      # a tied eliminator falls to the higher seed
    q2 = d.play_fixture(q1_loser, elim_winner, "Qualifier 2")
    if d.paused is not None:
        return replace(d.paused, table=table)
    if not d.gate_on_advance(d._pid(q2.home), d._pid(q2.away), "Qualifier 2"):
        return replace(d.paused, table=table)

    finalist = q1.winner or first
    other = q2.winner or q1_loser
    final = d.play_fixture(finalist, other, "Final")
    if d.paused is not None:
        return replace(d.paused, table=table)

    return RoomMatchReplay(results=list(d.entries), complete=True, table=table,
                            champion_pid=d._pid(final.winner or finalist))


def room_match_state(conn, code: str, deck, model) -> tuple[Room, RoomMatchReplay]:
    """Read-only poll: reuses `rooms.room_state`'s own loader (a no-op resolve once the
    draft is complete, matching every other room route's "load, maybe advance, save"
    shape) and replays the match phase on top."""
    room = rooms.room_state(conn, code, deck)
    return room, replay_room_matches(room, deck, model)


# --- mutators: authorisation lives here, not in game.season ------------------------------

def submit_toss(conn, code: str, player_id: str, elects: str, deck, model) -> Room:
    if elects not in ("bat", "bowl"):
        raise RoomError(f"elects must be 'bat' or 'bowl', got {elects!r}")
    room = rooms._load_room(conn, code)
    replay = replay_room_matches(room, deck, model)
    if replay.pending_kind != "toss":
        raise RoomError("no toss is pending in this room right now")
    if replay.pending_toss_winner_pid != player_id:
        raise RoomError("you did not win this toss")
    room.match_moves = room.match_moves + [{"kind": "toss", "elects": elects}]
    rooms._save_room(conn, room)
    return room


def submit_impact(conn, code: str, player_id: str, slot: int | None, deck, model) -> Room:
    room = rooms._load_room(conn, code)
    if player_id != room.host_id:
        raise RoomError("only the host decides Impact Player substitutions")
    replay = replay_room_matches(room, deck, model)
    if replay.pending_kind != "impact":
        raise RoomError("no Impact decision is pending in this room right now")
    room.match_moves = room.match_moves + [{"kind": "impact", "slot": slot}]
    rooms._save_room(conn, room)
    return room


def advance_match(conn, code: str, player_id: str, deck, model) -> Room:
    room = rooms._load_room(conn, code)
    if player_id != room.host_id:
        raise RoomError("only the host can move on to the next match")
    replay = replay_room_matches(room, deck, model)
    if replay.pending_kind != "advance":
        raise RoomError("this room is not waiting to advance right now")
    room.match_moves = room.match_moves + [{"kind": "advance"}]
    rooms._save_room(conn, room)
    return room
