"""Puzzle results: recording a signed-in player's daily puzzle, and the timed attempt that the
server (not the page) clocks [A188]. The tables are migration 046's.

Two kinds of result land in the same table:

* **Untimed** (Bingo, Guess the Player, Name the XI): the page sends the moves it made when it
  finishes, `game.puzzle_results` replays them, and the replayed outcome is recorded. Nothing
  the page says about how it did is believed.
* **Timed** (Common Ground): the four minutes have to be the SERVER's, because a page can claim
  any time it likes. So pressing Start inserts an open row stamped with the database's clock;
  every guess is stamped on arrival with the database's clock too; and the row is finished when
  the set is complete, when the player stops, or when the clock runs out. A row whose clock ran
  out with nobody there to notice is finished by the sweep that every board read begins with.

Everything takes the connection from the caller, so a route can make the write and its follow-up
reads one transaction, and so a test can hand it a real scratch database.
"""

from __future__ import annotations

import datetime
import json

from game import ground, puzzle_results as pr

CLOCK_GRACE_MS = 2000       # a guess typed on the last tick, in flight when the clock ran out


class OpenAttempt(pr.PuzzleResultError):
    """A move that needs a Start first, or an attempt that is already over."""


# --- writing ---------------------------------------------------------------------------------

def _insert_picks(conn, game: str, day: datetime.date, voter: str, picks) -> None:
    """The correct picks, one per voter per slot. A voter's first pick in a slot is the one that
    counts, so a repeat is ignored rather than moved."""
    if game not in pr.PICK_GAMES or not picks:
        return
    rows = [(game, day, p.slot, voter, p.person_id) for p in picks]
    values = ", ".join(["(%s, %s, %s, %s, %s)"] * len(rows))
    conn.execute(f"insert into puzzle_picks (game, day, slot, voter, person_id) values {values} "
                 "on conflict (game, day, slot, voter) do nothing",
                 [x for row in rows for x in row])


def record(conn, account_id: int, day: datetime.date, outcome: pr.Outcome, moves: list) -> bool:
    """Record one finished untimed attempt. Returns whether it was new: a second submission of
    the same game and day is ignored, so a reload or a double click cannot rewrite a result."""
    written = conn.execute(
        """
        insert into puzzle_results (account_id, game, day, started_at, finished_at, moves, ended,
                                    score, solved, elapsed_ms, detail)
        values (%s, %s, %s, now(), now(), %s::jsonb, %s, %s, %s, %s, %s::jsonb)
        on conflict (account_id, game, day) do nothing
        returning 1
        """,
        (account_id, outcome.game, day, json.dumps(moves), outcome.ended, outcome.score,
         outcome.solved, outcome.elapsed_ms, json.dumps(outcome.detail()))).fetchone()
    if written is not None:
        _insert_picks(conn, outcome.game, day, f"a:{account_id}", outcome.picks)
    return written is not None


def result_for(conn, account_id: int, game: str, day: datetime.date) -> dict | None:
    """This player's own finished attempt, if they have made one."""
    row = conn.execute(
        "select score, solved, elapsed_ms, ended, detail, finished_at from puzzle_results "
        "where account_id = %s and game = %s and day = %s and finished_at is not null",
        (account_id, game, day)).fetchone()
    if row is None:
        return None
    score, solved, elapsed, ended, detail, finished = row
    return {"score": score, "solved": solved, "elapsed_ms": elapsed, "ended": ended,
            "detail": detail, "finished_at": finished}


# --- the timed attempt -----------------------------------------------------------------------

_OPEN_ROW = ("select moves, finished_at, ended, detail, "
             "(extract(epoch from (now() - started_at)) * 1000)::bigint "
             "from puzzle_results where account_id = %s and game = 'common' and day = %s")


def _moves(raw: list) -> list[ground.Move]:
    return [ground.Move(m["id"], int(m["t"])) for m in raw]


def _store_moves(moves: list[ground.Move]) -> str:
    return json.dumps([{"id": m.id, "t": m.t} for m in moves])


def _finish(conn, account_id: int, day: datetime.date, g, puzzle, moves: list[ground.Move],
            end_t: int | None) -> pr.Outcome:
    """Close an attempt: replay it for its outcome and write the result, once."""
    outcome = pr.verify_common(g, puzzle, moves, end_t=end_t)
    detail = outcome.detail()
    if end_t is not None and outcome.ended is not None:
        detail["end_t"] = min(end_t, ground.LIMIT_MS)       # so the page can be rebuilt exactly
    conn.execute(
        "update puzzle_results set finished_at = now(), moves = %s::jsonb, ended = %s, score = %s, "
        "solved = %s, elapsed_ms = %s, detail = %s::jsonb "
        "where account_id = %s and game = 'common' and day = %s and finished_at is null",
        (_store_moves(moves), outcome.ended, outcome.score, outcome.solved, outcome.elapsed_ms,
         json.dumps(detail), account_id, day))
    _insert_picks(conn, "common", day, f"a:{account_id}", outcome.picks)
    return outcome


def ranked_view(conn, account_id: int, day: datetime.date) -> dict | None:
    """Where this player's attempt at today's pair stands, or None if they have not started.
    `elapsed_ms` is the SERVER's reading, which is what the page's countdown is built from."""
    row = conn.execute(_OPEN_ROW, (account_id, day)).fetchone()
    if row is None:
        return None
    raw, finished_at, ended, detail, elapsed = row
    return {"moves": raw, "finished": finished_at is not None, "ended": ended,
            "detail": detail, "elapsed_ms": int(elapsed)}


def ranked_start(conn, account_id: int, day: datetime.date) -> dict:
    """Press Start: stamp the database's clock on a new open row. Idempotent -- a second press
    (or a reload) does not restart the clock, it reports the one already running."""
    conn.execute(
        "insert into puzzle_results (account_id, game, day, started_at) values (%s, 'common', %s, now()) "
        "on conflict (account_id, game, day) do nothing", (account_id, day))
    view = ranked_view(conn, account_id, day)
    assert view is not None
    return view


def ranked_play(conn, g: ground.Ground, puzzle: ground.Puzzle, account_id: int, day: datetime.date,
                guess_id: str | None = None, end: bool = False) -> tuple[ground.State, dict, bool]:
    """Apply one guess and/or stop, on the server's clock. Returns the attempt as it now stands,
    its row's view, and whether the guess was APPLIED (it is not when the attempt was already
    over or the clock had run out). Raises OpenAttempt if the player never pressed Start, and
    PuzzleResultError for a move the rules refuse (which changes nothing)."""
    row = conn.execute(_OPEN_ROW + " for update", (account_id, day)).fetchone()
    if row is None:
        raise OpenAttempt("Press Start first.")
    raw, finished_at, ended, detail, elapsed = row
    elapsed = int(elapsed)
    moves = _moves(raw)
    if finished_at is not None:
        end_t = (detail or {}).get("end_t") if ended else None
        return (ground.replay(g, puzzle, moves, end_t=end_t),
                {"finished": True, "ended": ended, "elapsed_ms": elapsed, "detail": detail}, False)
    state = ground.replay(g, puzzle, moves)
    if elapsed >= ground.LIMIT_MS + CLOCK_GRACE_MS:
        # Nobody was looking when the clock ran out (a closed tab): it ended at the limit, and the
        # guess that arrives now is too late to count. RETURNED rather than raised: an exception
        # would roll back the very write that records the finish.
        _finish(conn, account_id, day, g, puzzle, moves, ground.LIMIT_MS)
        late = ground.replay(g, puzzle, moves, end_t=ground.LIMIT_MS)
        return late, {"finished": True, "late": True, "ended": late.ended, "elapsed_ms": elapsed}, False
    t = max(state.last_t, min(elapsed, ground.LIMIT_MS))
    try:
        if guess_id is not None:
            ground.guess(g, state, guess_id, t)
        if end or (elapsed >= ground.LIMIT_MS and not state.finished):
            ground.end(state, ground.LIMIT_MS if elapsed >= ground.LIMIT_MS else t)
    except ground.GroundError as exc:
        raise pr.PuzzleResultError(str(exc)) from exc
    if state.finished:
        _finish(conn, account_id, day, g, puzzle, state.moves, state.ended_t if state.ended else None)
    else:
        conn.execute("update puzzle_results set moves = %s::jsonb where account_id = %s and "
                     "game = 'common' and day = %s and finished_at is null",
                     (_store_moves(state.moves), account_id, day))
    view = ranked_view(conn, account_id, day)
    return state, view or {}, guess_id is not None


def sweep_expired(conn, g: ground.Ground, day: datetime.date) -> int:
    """Finish every timed attempt of this day whose clock ran out unobserved, at the limit.
    Called before a board is read, so an abandoned attempt still takes its place."""
    rows = conn.execute(
        "select account_id, moves from puzzle_results where game = 'common' and day = %s "
        "and finished_at is null and now() > started_at + (%s * interval '1 millisecond') "
        "for update", (day, ground.LIMIT_MS + CLOCK_GRACE_MS)).fetchall()
    if not rows:
        return 0
    puzzle = g.puzzle(_seed_for(day))
    for account_id, raw in rows:
        _finish(conn, account_id, day, g, puzzle, _moves(raw), ground.LIMIT_MS)
    return len(rows)


def _seed_for(day: datetime.date) -> int:
    return ground.daily_seed(day)


# --- the board -------------------------------------------------------------------------------

def rank_key(row: dict) -> tuple:
    """Best first: more points, then the quicker (a timed game's elapsed; the untimed games have
    none), then whoever finished first -- so the order is total and stable. The same rule
    everywhere a rank is shown."""
    elapsed = row["elapsed_ms"] if row["elapsed_ms"] is not None else 0
    return (-row["points"], elapsed, row["finished_at"], row["account_id"])


def board(conn, game: str, day: datetime.date, g: ground.Ground | None = None,
          limit: int = 50) -> list[dict]:
    """The day's finished results for one game, best first, each with its rank."""
    if game == "common" and g is not None:
        sweep_expired(conn, g, day)
    rows = conn.execute(
        """
        select r.account_id, a.username, a.kit_name, a.kit_monogram, a.kit_colour, r.score,
               r.solved, r.elapsed_ms, r.finished_at, r.detail
          from puzzle_results r join accounts a using (account_id)
         where r.game = %s and r.day = %s and r.finished_at is not null
        """, (game, day)).fetchall()
    out = [{"account_id": acc, "username": u,
            "kit": {"name": kn, "monogram": km, "colour": kc} if kn is not None else None,
            "score": score, "points": score, "solved": solved, "elapsed_ms": elapsed,
            "finished_at": finished, "detail": detail}
           for acc, u, kn, km, kc, score, solved, elapsed, finished, detail in rows]
    out.sort(key=rank_key)
    for i, row in enumerate(out, 1):
        row["rank"] = i
    return out[:limit] if limit else out


def rank_of(conn, game: str, day: datetime.date, account_id: int,
            g: ground.Ground | None = None) -> tuple[int | None, int]:
    """This player's place today and how many finished -- 'third of twelve'."""
    rows = board(conn, game, day, g, limit=0)
    mine = next((r["rank"] for r in rows if r["account_id"] == account_id), None)
    return mine, len(rows)
