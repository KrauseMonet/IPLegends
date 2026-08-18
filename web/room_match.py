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
import time
from dataclasses import dataclass, field
from itertools import zip_longest

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

    `league_progress`/`league_next` are set ONLY while a league room's round-robin is
    still being revealed (`league_progress[0] < league_progress[1]`) -- `(revealed,
    total)` and the single fixture waiting at the `revealed` position, respectively.
    Both `None` the rest of the time (every other format, and a league room once its
    group stage has been fully revealed). This is a distinct concept from
    `current_round`/`advance_ready`, which describe a round of KNOCKOUT fixtures a real
    seat might still need to call a toss for -- a round-robin fixture is always already
    resolved (`resolve_auto` never pauses), so there is nothing to wait ON here beyond
    the host's own pacing.
    """

    results: list[RoomResultEntry] = field(default_factory=list)
    complete: bool = False
    table: list[RoomStandingRow] | None = None        # league only, progressive
    champion_pid: str | None = None                   # complete only
    current_round: list[RoomFixtureState] | None = None
    round_label: str | None = None
    advance_ready: bool = False
    eliminated_pids: frozenset[str] = field(default_factory=frozenset)
    league_progress: tuple[int, int] | None = None
    league_next: RoomResultEntry | None = None
    # True only in the window between the draft finishing and the host recording a
    # "start" move -- every other field is left at its default (an empty `results`,
    # `complete=False`) since nothing has been resolved yet, not even a first toss.
    awaiting_start: bool = False


def _short_name(name: str, pid: str) -> str:
    """The scoreboard abbreviation for a seat.

    The old rule was initials-of-every-word capped at four, which produced two visibly
    wrong things on the live scorecard. A filler seat's name ends in its season, so
    "Chennai Super Kings 2010" took the YEAR's leading digit as a word initial and came
    out "CSK2"; and a one-word player name came out as a single letter, so "Krause"
    batted as "K". Solo's own `game.season._abbrev` already formats a historical squad
    correctly ("CSK 2010") -- a room printing the same squad differently was the bug.

    So: hold a trailing four-digit season aside, take initials from the words that are
    actually words, and fall back to the first three characters when there is only one
    of them.
    """
    words = name.split()
    year = words.pop() if words and words[-1].isdigit() and len(words[-1]) == 4 else None
    letters = "".join(w[0] for w in words if w[:1].isalpha())
    if len(letters) < 2 and words:
        letters = words[0][:3]        # "Krause" -> "Kra", never "K"
    short = letters.upper()[:4] or pid[:4].upper()
    return f"{short} {year}" if year else short


def _sides_with_pid(room: Room, deck) -> list[tuple[str, Side]]:
    out = []
    for pid, p, order, impact in rooms.room_sides(room, deck):
        out.append((pid, Side(name=p.name, short=_short_name(p.name, pid),
                              xi=order, impact=impact)))
    return out


def _pid_of(side: Side, pairs: list[tuple[str, Side]]) -> str:
    for pid, s in pairs:
        if s is side:
            return pid
    raise RoomError(f"internal error: {side.name!r} is not one of this room's own sides")


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
    matches) stays fully interactive.

    `resolve_auto`'s own cost is now paid at most ONCE per room, not once per poll --
    see `_round_robin_results`'s docstring for why replaying the whole round-robin on
    every call was the dominant cost behind a league room feeling slow deep into a
    tournament, and how caching it is made safe."""

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
        # A fixture of its own -- NOT self.rng -- because self.rng is one continuous
        # stream shared across every fixture in a round, and this one can PAUSE
        # (_OpenMatchNeedsToss) while a sibling in the same round does not. A round-
        # robin fixture (resolve_auto) never pauses, so it draws the same amount on
        # every replay and sharing a stream there is safe; this one doesn't have that
        # guarantee -- consuming 1 draw (the coin flip) while pending, then thousands
        # more once its toss is finally answered, would shift every LATER fixture's own
        # draws out from under it the instant that happens, silently re-simulating an
        # already-shown result into a different one. Seeding per (room.seed, stage)
        # instead makes each named fixture's own outcome depend only on its own moves,
        # never on a sibling's resolution timing -- what this module's own docstring
        # already claims ("attempted independently every replay pass") but the shared
        # stream did not actually deliver. `str` seeds hash deterministically
        # (unaffected by PYTHONHASHSEED), unlike a bare tuple.
        fixture_rng = random.Random(f"{self.room.seed}:{stage}")
        try:
            r = play_open(self.model, side_a, side_b, fixture_rng, stage=stage, moves=cursor)
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


def _start_recorded(room: Room) -> bool:
    return any(isinstance(mv, dict) and mv.get("kind") == "start" for mv in room.match_moves)


def _league_revealed_count(room: Room) -> int:
    """How many of the round-robin's fixtures the host has revealed so far -- the
    highest `through` value recorded, or 0. Monotonic by construction: a write only ever
    raises it (see `advance_league_reveal`, which refuses to record a `through` at or
    below what's already there)."""
    throughs = [mv.get("through", 0) for mv in room.match_moves
                if isinstance(mv, dict) and mv.get("kind") == "league-reveal"]
    return max(throughs, default=0)


# How long a match-phase step may sit waiting on a human before it resolves itself.
#
# The MATCH phase had no timeout of any kind -- `web/rooms.py` carries 19 references to
# `turn_started_at`/`pick_afk` for the draft and this module carried none -- so two
# unbounded waits could freeze a room permanently: a host who closed their tab left every
# other seat on "Waiting for the host to continue…" forever, and a toss winner who left
# stranded that fixture, since `_FixtureToss.next_toss` returns None for a human whose
# move never arrives and nothing else ever filled it in.
#
# Deliberately GENEROUS, because this is a safety net rather than the pacing mechanism.
# The reveal is paced client-side -- the host's own screen advances a few seconds after
# its animation ends -- and a full match at the slowest speed setting is about 48 seconds
# of animation, so a timeout short enough to feel responsive would advance the round out
# from under people still watching it. Ninety seconds clears the slowest reveal, leaves
# room for a host who has deliberately hit Pause to actually pause, and is far short of
# "nobody is coming back".
#
# It does bound that Pause, and that is the intended trade rather than an oversight: an
# unbounded pause is exactly the freeze this constant exists to make impossible, so a
# host who steps away mid-pause still cannot strand nine other people.
MATCH_STEP_TIMEOUT_S = 90


def _last_step_at(room: Room) -> float:
    """When the current step began, as an epoch second on the same clock `time.time()`
    reads -- the timestamp of the most recent match move, or the end of the draft if the
    match phase has not recorded one yet.

    Reading the LAST move rather than storing a separate "step started" field is what
    keeps this migration-free: every match move is recorded at the moment a step ends,
    which is the same moment the next one begins. A move from before this was introduced
    carries no `at` at all and is treated as arbitrarily old, so a room frozen under the
    old code resolves itself on the next request rather than staying stuck forever."""
    stamps = [mv["at"] for mv in room.match_moves
              if isinstance(mv, dict) and isinstance(mv.get("at"), (int, float))]
    if stamps:
        return max(stamps)
    if room.match_moves:
        return 0.0     # pre-timestamp moves: treat as ancient, so a frozen room recovers
    return room.turn_started_at or 0.0


def _stamped(move: dict) -> dict:
    """Every match move carries when it happened. Nothing reads an individual move's
    timestamp -- only the maximum matters (`_last_step_at`) -- but stamping them all is
    what makes that maximum mean "when did the current step begin" rather than "when did
    whichever kind of move I remembered to stamp happen"."""
    return {**move, "at": time.time()}


def _resolve_one_step(room: Room, replay: RoomMatchReplay, *, league_to: int | None = None) -> bool:
    """Record the ONE move the room is currently waiting on, and report whether there was
    one. The single implementation of "what does this room need next", shared by the
    inactivity failsafe and by the deliberate skips -- a second copy of this would be a
    second place for "what counts as the current step" to drift from the engine.

    Ordering matches `replay_room_matches`'s own: the start gate, then the league reveal
    cursor, then a pending toss, then the round advance. A room can only ever be waiting on
    one of them at a time, but checking in the engine's own order means this can never
    resolve a step the engine does not consider current.

    `league_to` is how a skip collapses the group stage in a single move instead of
    seventy: the reveal cursor is a TARGET (A86), so one move can carry it all the way to
    the total. The failsafe leaves it None and advances the cursor by one, because it is
    rescuing a stalled room rather than fast-forwarding it."""
    if replay.awaiting_start:
        room.match_moves = room.match_moves + [_stamped({"kind": "start"})]
        return True

    if replay.league_progress is not None:
        revealed, total = replay.league_progress
        if revealed < total:
            through = total if league_to is None else min(league_to, total)
            if through > revealed:
                room.match_moves = room.match_moves + [
                    _stamped({"kind": "league-reveal", "through": through})]
                return True

    for fixture in (replay.current_round or []):
        if fixture.result is None and fixture.pending_toss_winner_pid is not None:
            # The same default a CPU-owned winner already gets, for the same reason: it is
            # a decision nobody is present to make, not one being taken away. This is also
            # what lets a skip get PAST a pending toss -- the old "Skip ahead" looped on
            # `advance_ready` alone, which a pending toss makes false, so it could never
            # reach the end of a tournament that still had a real toss outstanding.
            room.match_moves = room.match_moves + [_stamped(
                {"kind": "toss", "stage": fixture.stage, "elects": TOSS_DEFAULT_ELECTS})]
            return True

    if replay.advance_ready:
        room.match_moves = room.match_moves + [
            _stamped({"kind": "advance", "round": replay.round_label})]
        return True

    return False


def _auto_resolve(room: Room, deck, model) -> bool:
    """Resolve ONE step that has been waiting past the timeout, and report whether it did.

    Resolve-on-read (A62), the same shape `rooms._resolve` uses for the draft: no
    scheduler, no background job -- whichever request arrives next does the work. One step
    per call rather than a loop, deliberately: the move this records is stamped with the
    current time, so an abandoned room drains one step per timeout period instead of
    fast-forwarding to the end the instant anybody looks at it. That keeps a room that
    everyone briefly left in a state they can rejoin and still watch.

    Advances the league cursor by ONE (`league_to=revealed + 1`) rather than to the total:
    this is rescuing a room nobody is watching, not skipping one on purpose, and collapsing
    seventy fixtures because a host stepped away for two minutes would destroy the reveal
    they came back for."""
    if time.time() - _last_step_at(room) <= MATCH_STEP_TIMEOUT_S:
        return False
    replay = replay_room_matches(room, deck, model)
    league_to = None
    if replay.league_progress is not None:
        league_to = replay.league_progress[0] + 1
    return _resolve_one_step(room, replay, league_to=league_to)


def _paused(entries: list[RoomResultEntry], states: list[RoomFixtureState], label: str,
            eliminated: set[str], *, advance_ready: bool = False,
            table: list[RoomStandingRow] | None = None,
            league_progress: tuple[int, int] | None = None,
            league_next: RoomResultEntry | None = None) -> RoomMatchReplay:
    return RoomMatchReplay(
        results=list(entries), complete=False, table=table,
        current_round=states, round_label=label,
        advance_ready=advance_ready, eliminated_pids=frozenset(eliminated),
        league_progress=league_progress, league_next=league_next)


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


# The round-robin depends on nothing but (room.seed, pairs) -- never on room.match_moves,
# since every one of its fixtures auto-resolves -- so unlike every other fixture it is
# cacheable for the room's whole lifetime rather than replayed on every single poll. A
# league room's 70-fixture round-robin was the measured, dominant cost behind
# `replay_room_matches` being slow deep into a tournament (on the order of 10 million
# `tilt()` evaluations, per game.simulator's own bisection); every fixture AFTER it is
# cheap by comparison (at most 4 further matches). Keyed on (code, seed) rather than seed
# alone as a trivial extra guard against a theoretical future code reuse, at no real cost.
#
# The one subtlety that makes this a correctness-sensitive cache, not just a memoisation:
# `_Driver.rng` is a SINGLE stream, and everything simulated after the round-robin needs
# to draw from exactly where that stream would sit had the 70 matches genuinely just been
# simulated in this call -- `random.Random` has no way to "skip ahead," only to actually
# draw. `rng.getstate()`/`setstate()` is what lets a cache hit reproduce that exactly
# without paying for it twice: restoring the RNG's own internal state, not just the
# entries it produced. Verified by test, not just reasoned about: see
# `test_round_robin_cache_does_not_change_playoff_outcomes`.
#
# Stores the raw fixture list, not a precomputed table -- the table now depends on how
# much of the round-robin has been REVEALED (see `replay_room_matches`'s own league
# branch), which is unrelated to whether the underlying simulation is cached. Computing
# a table fresh from whatever slice is relevant is cheap (70 rows at most) and needs no
# caching of its own.
_ROUND_ROBIN_CACHE: dict[tuple[str, int], tuple[list[RoomResultEntry], tuple]] = {}


def _round_robin_results(d: "_Driver", room: Room, pairs: list[tuple[str, Side]]
                          ) -> list[RoomResultEntry]:
    key = (room.code, room.seed)
    cached = _ROUND_ROBIN_CACHE.get(key)
    if cached is not None:
        entries, rng_state = cached
        d.entries.extend(entries)
        d.rng.setstate(rng_state)
        return entries

    side_by_index = [side for _, side in pairs]
    for i, j in league_fixtures(len(pairs)):
        d.resolve_auto(side_by_index[i], side_by_index[j], "league")
    entries = list(d.entries)
    _ROUND_ROBIN_CACHE[key] = (entries, d.rng.getstate())
    return entries


def _reveal_order(entries: list[RoomResultEntry], n_teams: int) -> list[RoomResultEntry]:
    """Reorders ALREADY-SIMULATED round-robin entries for REVEAL ONLY -- diagnosed
    from a real report ("only the host's own matches are shown") that turned out to be
    literally true for the first fourteen of seventy reveals, every time.

    `league_fixtures`'s own nested loop (`for i in range(n): for j in range(i+1, n)`)
    generates one team's WHOLE season consecutively before the next team's remaining
    fixtures even begin -- correct and necessary for the SIMULATION (`_round_robin_
    results` draws from one sequential `d.rng` stream, so this exact order is what a
    cached round-robin's `rng.getstate()` reproduces, and every later playoff result
    depends on the stream sitting where it would after simulating in THIS order --
    reordering the simulation itself would silently change downstream results for the
    same seed). But `_sides_with_pid` always places the host at index 0 (join order,
    host first), so team 0's fourteen fixtures are always, deterministically, the
    host's own -- and the room's reveal cursor (`advance_league_reveal`) walks this
    list in a straight line, one `through` at a time, so a room genuinely showed
    nothing but host-vs-X for the first 20% of the tournament, then never again.

    Fixed by bucketing the FINISHED entries by the LOWER of their two team indices
    (recovered positionally from `league_fixtures(n_teams)`, which entries[k] always
    corresponds to 1:1 -- this never touches `_round_robin_results`'s own output or
    cache, only a local copy) and interleaving the buckets round-robin style, so any
    run of `n_teams` consecutive REVEALED fixtures spans a spread of different teams
    instead of one team's whole season. Every fixture home team's OWN bucket holds
    ALL its games regardless of forward/reverse order, because `min(i, j)` catches a
    DOUBLE_AT reverse fixture `(j, i)` the same as the forward `(i, j)`. Purely
    cosmetic: the SET of 70 results, the final table, and every playoff outcome are
    completely unaffected, since nothing here touches simulation, only which order the
    finished results are exposed through the reveal cursor."""
    pairs_seq = league_fixtures(n_teams)
    buckets: list[list[RoomResultEntry]] = [[] for _ in range(n_teams)]
    for (i, j), entry in zip(pairs_seq, entries):
        buckets[min(i, j)].append(entry)
    return [e for row in zip_longest(*buckets) for e in row if e is not None]


def replay_room_matches(room: Room, deck, model: Model) -> RoomMatchReplay:
    if room.status != "complete":
        raise RoomError(f"room is not complete yet (status: {room.status})")

    # Every seat reviews their own finished twelve -- and the three ratings that come
    # with it -- before a single ball is bowled. Nothing below this point runs (not
    # even a first toss) until the host records a "start" move, mirroring the
    # "advance" move's own shape (_advance_recorded/advance_match) rather than
    # inventing a new kind of gate.
    if not _start_recorded(room):
        return RoomMatchReplay(awaiting_start=True)

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
        # Each semi's own loser is eliminated the INSTANT that semi resolves, never
        # gated on its sibling's -- a fixture whose own result is already known must
        # not wait on an unrelated match before its loser can move on.
        for (_, side_a, side_b), fs in zip(semi_fixtures, semis):
            if fs.result is not None:
                eliminated.add(d._pid(_loser(fs.result, side_a, side_b)))
        if not _round_complete(semis):
            return _paused(d.entries, semis, "Semi-finals", eliminated)
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
    # round instead of called once each. The round-robin itself is cached (see
    # `_round_robin_results`'s own docstring) -- it is the dominant cost of replaying a
    # league room and depends on nothing this room's own moves could change.
    all_entries = _reveal_order(_round_robin_results(d, room, pairs), len(pairs))
    total = len(all_entries)
    revealed = min(_league_revealed_count(room), total)

    if revealed < total:
        # The round-robin's OUTCOME is already fully decided by the cache above --
        # revealing it is pure presentation, paced by the host, never a re-simulation.
        # Nobody is marked eliminated from a table that hasn't finished being SHOWN yet,
        # even though the true final table is already sitting in `all_entries`: leaking
        # who's really out before the host gets there would spoil the one thing this
        # pacing exists to protect.
        d.entries[:] = all_entries[:revealed]
        partial_table = _standings_table(pairs, d.entries) if revealed else None
        # `league_next` is the fixture THIS response is delivering an animation for --
        # the one just newly included in `d.entries` by advancing to `revealed`, i.e.
        # index `revealed - 1`, NOT `all_entries[revealed]` (the next one still
        # waiting). `revealed` counts fixtures already shown; the one a "Continue" call
        # just unlocked is the LAST of those, not the one after. None only at
        # revealed == 0, before the host's first Continue -- nothing has been unlocked
        # yet to animate.
        league_next = all_entries[revealed - 1] if revealed > 0 else None
        return RoomMatchReplay(
            results=list(d.entries), complete=False, table=partial_table,
            current_round=[], round_label="League",
            league_progress=(revealed, total), league_next=league_next,
            advance_ready=False, eliminated_pids=frozenset())

    d.entries[:] = all_entries
    table = _standings_table(pairs, d.entries)
    top4_pids = {row.pid for row in table[:4]}
    # The non-top-4 are out the INSTANT the table settles -- well before the playoffs
    # even open, let alone finish. Nothing about their own tournament changes from here.
    eliminated.update(pid for pid, _ in pairs if pid not in top4_pids)
    if not _advance_recorded(room, "League"):
        # The round-robin's own LAST fixture never gets a paused, per-match stopover
        # the way an ordinary in-progress one does -- the moment `revealed` reaches
        # `total` this branch takes over, and the plain version of this return carried
        # no `league_next` at all, so the final fixture's own animation silently never
        # played (the same shape of gap A83 already fixed for a room's LAST knockout
        # fixture). Still offering it here, alongside `advance_ready`, lets the
        # existing frontend reveal-check (which always runs first) animate it before
        # ever acting on `advance_ready` -- by the time a host can actually record the
        # "advance" move that leaves this screen, they must already have seen it.
        return _paused(d.entries, [], "League", eliminated, advance_ready=True, table=table,
                       league_progress=(total, total), league_next=all_entries[-1])

    # Resolved via the CURRENT call's own `pairs`, never via `row.standing.side` -- once
    # the round-robin can be served from `_ROUND_ROBIN_CACHE`, `table`'s own Side objects
    # belong to whichever call first populated it, not this one, and `d._pid()` below
    # needs Side objects it can find by IDENTITY in its own `self.pairs` (the exact
    # cross-call Side-identity trap this module's docstrings already warn about
    # elsewhere -- found here by the test suite, not assumed safe).
    pairs_by_pid = dict(pairs)
    first, second, third, fourth = (pairs_by_pid[row.pid] for row in table[:4])
    qual_fixtures = [("Qualifier 1", first, second), ("Eliminator", third, fourth)]
    quals = _resolve_round(d, qual_fixtures)
    q1_state, elim_state = quals
    # The real IPL asymmetry, applied the instant EACH fixture's own result is known,
    # never gated on its sibling: the Eliminator's loser is out immediately, but
    # Qualifier 1's loser gets a second life via Qualifier 2 rather than being
    # eliminated here -- so only the Eliminator's own result feeds `eliminated` at
    # this point.
    if elim_state.result is not None:
        eliminated.add(d._pid(_loser(elim_state.result, third, fourth)))
    if not _round_complete(quals):
        return _paused(d.entries, quals, "Qualifiers", eliminated, table=table)
    q1_r, elim_r = q1_state.result, elim_state.result
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
    """Poll: reuses `rooms.room_state`'s own loader (a no-op resolve once the draft is
    complete) and replays the match phase on top -- then, if this room has been sitting
    on a human who never came back, resolves ONE step itself and saves.

    Not read-only any more, and the discipline that makes that safe is A119's: the
    overwhelmingly common case does not write at all. `_auto_resolve` returns False
    without touching anything whenever the current step is still inside its timeout,
    which is every poll of a room anybody is actually watching -- so a write happens only
    on the rare poll that genuinely rescues a stalled room, not on the ~0.5 polls/second
    per seat that arrive the rest of the time.

    The re-load under a lock before writing matters for the same reason it does in
    `rooms.room_state`: several seats poll on their own timers, so two can decide to
    rescue the same step at once. Re-checking under the lock means the second one finds
    the step already resolved and writes nothing."""
    room = rooms.room_state(conn, code, deck)
    if not _auto_resolve(room, deck, model):
        return room, replay_room_matches(room, deck, model)
    room = rooms._load_room(conn, code, lock=True)
    if _auto_resolve(room, deck, model):
        rooms._save_room(conn, room)
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
    room.match_moves = room.match_moves + [_stamped(
        {"kind": "toss", "stage": stage, "elects": elects})]
    rooms._save_room(conn, room)
    return room


def advance_match(conn, code: str, player_id: str, deck, model) -> Room:
    room = rooms._load_room(conn, code)
    if player_id != room.host_id:
        raise RoomError("only the host can move on to the next round")
    replay = replay_room_matches(room, deck, model)
    if not replay.advance_ready:
        raise RoomError("this room is not waiting to advance right now")
    room.match_moves = room.match_moves + [_stamped(
        {"kind": "advance", "round": replay.round_label})]
    rooms._save_room(conn, room)
    return room


def start_matches(conn, code: str, player_id: str, deck, model) -> Room:
    """Host-only, mirroring `advance_match`'s own authorisation and shape exactly.
    Idempotent rather than an error on a repeat call -- the same convergence
    philosophy as `rooms.play_again`/`advance_league_reveal`: a double-click or a
    network retry sending this twice is a real, expected case, not a bug."""
    room = rooms._load_room(conn, code)
    if player_id != room.host_id:
        raise RoomError("only the host can start the matches")
    if _start_recorded(room):
        return room
    replay = replay_room_matches(room, deck, model)
    if not replay.awaiting_start:
        raise RoomError("this room is not waiting to start right now")
    room.match_moves = room.match_moves + [_stamped({"kind": "start"})]
    rooms._save_room(conn, room)
    return room


SKIP_TARGETS = ("group_stage", "tournament")

# A tournament cannot need more steps than this, so a loop that hits it is a bug rather
# than a big room: a ten-seat league is one start gate + one league-reveal move + four
# playoff rounds with at most one toss each, well under twenty. Bounded because this is
# the one place that loops step resolution, and an unbounded loop over replayed state is
# how a single request would hang a room instead of failing it.
_SKIP_STEP_CAP = 40


def skip_ahead(conn, code: str, player_id: str, target: str, deck, model) -> Room:
    """Resolve every remaining step up to `target` in one request, using the same declared
    defaults every automatic move source in this codebase already uses.

    `'group_stage'` stops the moment a league room's round-robin is fully revealed, leaving
    the playoffs to be watched normally -- the point of it is to get past seventy fixtures,
    not to skip the part worth seeing. `'tournament'` runs to `complete`.

    Host-only, matching `advance_match`/`advance_league_reveal`, because this advances
    SHARED state: it is not "stop showing ME animations" but "move this room on for
    everybody", and a non-host firing it would take the tournament away from the other
    seats. A viewer who has merely seen enough already has per-match skips
    (`roomSkipThisMatch`, `skipOverStepper`) that touch nothing but their own screen.

    Loops `_resolve_one_step` rather than reimplementing the step ladder, which is what
    lets it get past a pending toss -- the old client-side "Skip ahead" looped on
    `advance_ready` alone and stalled on exactly that. Idempotent in the same sense the
    other mutators are: a room already at the target records nothing and returns."""
    if target not in SKIP_TARGETS:
        raise RoomError(f"target must be one of {SKIP_TARGETS}, got {target!r}")
    room = rooms._load_room(conn, code)
    if player_id != room.host_id:
        raise RoomError("only the host can skip ahead")

    changed = False
    for _ in range(_SKIP_STEP_CAP):
        replay = replay_room_matches(room, deck, model)
        if replay.complete:
            break
        if target == "group_stage" and replay.league_progress is None:
            # Either the group stage is fully revealed, or this format never had one.
            break
        if not _resolve_one_step(room, replay):
            break
        changed = True
    else:
        raise RoomError("could not skip ahead: too many steps remained")

    if changed:
        rooms._save_room(conn, room)
    return room


def advance_league_reveal(conn, code: str, player_id: str, through: int, deck, model) -> Room:
    """Host-only, mirroring `advance_match`'s own authorisation. `through` is the
    TARGET cursor the caller wants revealed, not an increment -- "Continue" sends
    `revealed + 1`, "Skip ahead" sends the group stage's own total, so both are just
    different values of the same call. Idempotent on a stale request rather than an
    error (matching `rooms.play_again`'s own convergence philosophy): a double-click or
    a network retry sending the same `through` twice is a real, expected case here, not
    a bug to reject."""
    room = rooms._load_room(conn, code)
    if player_id != room.host_id:
        raise RoomError("only the host can reveal the next league match")
    replay = replay_room_matches(room, deck, model)
    if replay.league_progress is None:
        raise RoomError("the group stage is not being revealed right now")
    revealed, total = replay.league_progress
    if through > total:
        raise RoomError(f"only {total} group-stage fixtures exist")
    if through <= revealed:
        return room
    room.match_moves = room.match_moves + [_stamped(
        {"kind": "league-reveal", "through": through})]
    rooms._save_room(conn, room)
    return room
