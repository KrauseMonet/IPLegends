"""A real IPL match, rebuilt as the engine's own `Result` from the archive's deliveries [A319].

The match replay page plays a real match on the same scoreboard every simulated match uses,
so the real match has to arrive in the same shape: an `Innings` per side with its scorecard,
its fall-of-wicket lines, its over log and its milestones. This module builds exactly that
from the ball-by-ball rows `tools.snapshot_matches` froze, with the project's own counting
rules, so a replay's scorecard agrees with the Almanack's pages:

  - a ball faced is any delivery that was not a wide (A22);
  - a bowler is charged runs off the bat, wides and no-balls, never byes or leg-byes (A1),
    and credited only the wickets that are his (A36);
  - "retired hurt" is not a dismissal; every other way out is;
  - the batting order is the order players first reached the crease at EITHER end (A4);
  - a hat-trick counts every ball the bowler sent down, wides included, so a wide between
    two wickets breaks one and a wicket off a wide counts (A316's rule, the record's own).

Over-log rules are `OverSnapshot`'s: only a completed over is logged, and the last over of
an innings that ended mid-over is not. An over the umpire miscounted (A14) is logged when
the innings carried on past it, whatever its count.

Pure: no database, no deck, no rng.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from game.season import Result, Side, SuperOver, _abbrev
from game.simulator import (BatterCard, BowlerCard, CreaseBatter, Innings, Milestone,
                            OverBatter, OverSnapshot, Player)

NOT_OUT = {"retired hurt"}
NOT_BOWLERS = {"run out", "retired hurt", "retired out", "obstructing the field"}

# Ball row positions, as `tools.snapshot_matches` writes them.
OVER, STRIKER, NON_STRIKER, BOWLER, RUNS, WIDES, NOBALLS, BYES, LEGBYES, PENALTY, OUT, KIND = range(12)


@dataclass
class Doc:
    """The frozen archive: `matches` by id, plus the shared look-up tables."""
    kinds: list[str]
    fs: dict[str, list]           # fs id -> [franchise, year, display name]
    people: dict[str, str]        # person id -> name
    matches: dict[str, dict]

    @classmethod
    def of(cls, doc: dict) -> "Doc":
        return cls(doc["kinds"], doc["fs"], doc["people"], {m["id"]: m for m in doc["matches"]})


def side(doc: Doc, fs: int) -> Side:
    franchise, year, display = doc.fs[str(fs)]
    return Side(name=display, short=_abbrev(display, year), xi=[], franchise=display, year=year)


def innings(doc: Doc, raw: dict, dnb: list[str] | None = None) -> Innings:
    """One innings. `dnb` lists the side's named players in the order they should follow the
    batters who came in, so the scorecard carries all eleven ("did not bat")."""
    pids = raw["p"]
    cards: dict[str, BatterCard] = {}
    order: list[BatterCard] = []
    bowlers: dict[str, BowlerCard] = {}
    inn = Innings(batting=order, bowling=[])

    def batter(pid: str) -> BatterCard:
        if pid not in cards:
            cards[pid] = BatterCard(Player(doc.people.get(pid, pid), 0.0, person_id=pid))
            order.append(cards[pid])
        return cards[pid]

    def bowler(pid: str) -> BowlerCard:
        if pid not in bowlers:
            bowlers[pid] = BowlerCard(Player(doc.people.get(pid, pid), 0.0, person_id=pid))
            inn.bowling.append(bowlers[pid])
        return bowlers[pid]

    balls = raw["b"]
    streak: dict[str, int] = {}
    in_over: dict[tuple[int, str], int] = {}
    over_start: dict[int, tuple] = {}
    over_begin = (0, 0, 0, 0)          # runs, wickets, fours, sixes at the start of the over
    over_legal = 0
    for i, b in enumerate(balls):
        over = b[OVER]
        if i == 0 or balls[i - 1][OVER] != over:
            over_begin = (inn.runs, inn.wickets, inn.fours, inn.sixes)
            over_legal = 0
            over_start = {id(c): (c.runs, c.balls, c.fours, c.sixes, c.out) for c in order}
        st, ns = batter(pids[b[STRIKER]]), batter(pids[b[NON_STRIKER]])
        bw = bowler(pids[b[BOWLER]])
        legal = b[WIDES] == 0 and b[NOBALLS] == 0
        if b[WIDES] == 0:
            st.balls += 1
            st.faced_any = True
        before = st.runs
        st.runs += b[RUNS]
        if b[RUNS] == 4:
            st.fours += 1
            bw.fours += 1
            inn.fours += 1
        elif b[RUNS] == 6:
            st.sixes += 1
            bw.sixes += 1
            inn.sixes += 1
        bw.runs += b[RUNS] + b[WIDES] + b[NOBALLS]
        bw.off_bat += b[RUNS]
        extras = b[WIDES] + b[NOBALLS] + b[BYES] + b[LEGBYES] + b[PENALTY]
        inn.runs += b[RUNS] + extras
        inn.extras += extras
        if legal:
            bw.balls += 1
            inn.balls += 1
            over_legal += 1
        for mark, kind in ((50, "fifty"), (100, "hundred")):
            if before < mark <= st.runs:
                inn.milestones.append(Milestone(kind, st.player.name, st.player.person_id, inn.balls,
                                                st.runs, 0, st.balls))
        kind = doc.kinds[b[KIND]] if b[KIND] >= 0 else None
        credited = kind is not None and kind not in NOT_BOWLERS
        streak[bw.player.person_id] = streak.get(bw.player.person_id, 0) + 1 if credited else 0
        if kind is not None and kind not in NOT_OUT:
            gone = batter(pids[b[OUT]])
            gone.out = True
            gone.faced_any = True
            inn.wickets += 1
            if credited:
                bw.wickets += 1
                key = (over, bw.player.person_id)
                in_over[key] = in_over.get(key, 0) + 1
                _milestones(inn, bw, streak[bw.player.person_id], in_over[key])
            how = f"b {bw.player.name}" if credited else kind
            inn.commentary.append(f"  {inn.overs:>5}  {gone.player.name} out {gone.runs} ({gone.balls})  {how}  "
                                  f"-- {inn.runs}/{inn.wickets}")
        last_of_over = i + 1 == len(balls) or balls[i + 1][OVER] != over
        if last_of_over and (i + 1 < len(balls) or over_legal >= 6):
            if i + 1 < len(balls):
                nxt = balls[i + 1]
                on, off = batter(pids[nxt[STRIKER]]), batter(pids[nxt[NON_STRIKER]])
            else:
                on, off = ns, st
            inn.over_log.append(OverSnapshot(
                over=over, bowler=bw.player.name, bowler_id=bw.player.person_id,
                runs=inn.runs, wickets=inn.wickets, balls=inn.balls,
                over_runs=inn.runs - over_begin[0], over_wickets=inn.wickets - over_begin[1],
                over_fours=inn.fours - over_begin[2], over_sixes=inn.sixes - over_begin[3],
                striker=_crease(on), non_striker=_crease(off),
                bowler_balls=bw.balls, bowler_runs=bw.runs, bowler_wickets=bw.wickets,
                batters=_over_batters(order, over_start)))
    for pid in dnb or ():
        batter(pid)
    return inn


def _crease(c: BatterCard) -> CreaseBatter:
    return CreaseBatter(c.player.name, c.player.person_id, c.runs, c.balls, c.fours, c.sixes)


def _over_batters(order: list[BatterCard], start: dict) -> tuple[OverBatter, ...]:
    out = []
    for i, c in enumerate(order):
        r0, b0, f0, s0, out0 = start.get(id(c), (0, 0, 0, 0, False))
        if c.balls == b0 and not (c.out and not out0):      # faced nothing and was not run out in it
            continue
        out.append(OverBatter(i, c.player.name, c.player.person_id, c.runs - r0, c.balls - b0,
                              c.fours - f0, c.sixes - s0, c.out))
    return tuple(out)


def _milestones(inn: Innings, bw: BowlerCard, streak: int, in_over: int) -> None:
    def mark(kind: str) -> None:
        inn.milestones.append(Milestone(kind, bw.player.name, bw.player.person_id, inn.balls,
                                        bw.runs, bw.wickets, bw.balls))
    if streak == 3:
        mark("hattrick")
    elif in_over == 3:
        mark("three_in_over")
    if bw.wickets == 5:
        mark("five_for")


def stage_label(m: dict) -> str:
    if m.get("stage"):
        return m["stage"]
    return f"League · match {m['number']}" if m.get("number") else "League"


def result(doc: Doc, match_id: str, dnb_key: Callable[[int, str], object] | None = None) -> Result:
    """The match as a `Result`: `home` is the side that batted first, as everywhere in the
    engine. `dnb_key(fs, person)` orders the players who did not bat; by name if omitted."""
    m = doc.matches[match_id]
    main = [i for i in m["inns"] if not i["so"]]
    supers = [i for i in m["inns"] if i["so"]]
    first_fs = main[0]["fs"] if main else m["a"]
    other_fs = m["b"] if first_fs == m["a"] else m["a"]
    home, away = side(doc, first_fs), side(doc, other_fs)
    sides = {first_fs: home, other_fs: away}

    def dnb(fs: int, raw: dict) -> list[str]:
        came = set(raw["p"][b[STRIKER]] for b in raw["b"]) | set(raw["p"][b[NON_STRIKER]] for b in raw["b"])
        rest = [p for p in m["xi"].get(str(fs), []) if p not in came]
        key = (lambda p: dnb_key(fs, p)) if dnb_key else (lambda p: doc.people.get(p, p))
        return sorted(rest, key=key)

    built = [innings(doc, raw, dnb(raw["fs"], raw)) for raw in main]
    r = Result(home=home, away=away, stage=stage_label(m))
    for raw, inn in zip(main, built):
        if raw["fs"] == first_fs:
            r.home_innings, r.home_runs, r.home_wickets, r.home_balls = inn, inn.runs, inn.wickets, inn.balls
        else:
            r.away_innings, r.away_runs, r.away_wickets, r.away_balls = inn, inn.runs, inn.wickets, inn.balls
    if r.away_innings is not None and m["winner"] == other_fs and m["result"] == "wickets":
        r.away_innings.chased = True
    r.winner = sides.get(m["winner"])
    r.margin = margin(m, r.winner)
    for k in range(0, len(supers) - 1, 2):
        a, b = innings(doc, supers[k]), innings(doc, supers[k + 1])
        first, second = sides[supers[k]["fs"]], sides[supers[k + 1]["fs"]]
        win = first if a.runs > b.runs else second if b.runs > a.runs else None
        r.super_overs.append(SuperOver(k // 2 + 1, first, second, a, b, win))
    if r.super_overs and r.super_overs[-1].winner is None and r.winner is not None:
        # Before 2020 a tied super over went to the side with more boundaries (729315, 2014).
        r.margin = f"{r.winner.short} won on boundaries after a tied super over"
    return r


def margin(m: dict, winner: Side | None) -> str:
    if m["result"] == "no result" or winner is None:
        return "No result"
    if m["result"] == "tie":
        return f"{winner.short} won the super over"
    n = m["margin"] or 0
    unit = ("run" if n == 1 else "runs") if m["result"] == "runs" else ("wicket" if n == 1 else "wickets")
    return f"{winner.short} by {n} {unit}" + (" (DLS method)" if m["by"] == "dls" else "")
