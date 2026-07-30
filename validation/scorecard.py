"""Check 7: reconstruct full scorecards for hand-checking against the public record.

This is the check that matters, because it is the only one a human can disagree with.
Everything is recomputed from `deliveries` alone - no stored totals - so a scorecard
that matches Cricsheet's own published card means the delivery table is right about
runs, balls faced, dismissals, extras and bowling analysis all at once.

The bowling column is where SPEC A1 earns its keep: byes and legbyes are excluded
from runs conceded, wides and no-balls are not. Collapsing extras into one column
would put the economy rates visibly wrong here.

Fielders are not stored, so a catch reads `c ? b Kumar`. The bowler is what makes a
dismissal checkable; the fielder is cosmetic and would be a column carrying no other
purpose.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# One per season SPEC 8.7 names. Record performances rather than finals, because a
# figure a reader already knows is one they can falsify at a glance.
SCORECARD_MATCHES = (
    "335982",   # 2008 opener, RCB v KKR: McCullum 158* off 73
    "598027",   # 2013, RCB v Pune Warriors: Gayle 175* off 66, the T20 record
    "980987",   # 2016 Qualifier 1, RCB v Gujarat Lions: Kohli 109 and de Villiers 129*
    "1216517",  # 2020, MI v Kings XI: tied, then two super overs
    "1426312",  # 2024 final, SRH v KKR: SRH 113 all out
)

# A retirement is not a wicket for the purposes of the team score. `retired out` is.
NOT_A_WICKET = frozenset({"retired hurt", "retired not out"})

SHORT_KIND = {
    "caught": "c ? b {bowler}",
    "bowled": "b {bowler}",
    "lbw": "lbw b {bowler}",
    "caught and bowled": "c & b {bowler}",
    "stumped": "st ? b {bowler}",
    "hit wicket": "hit wicket b {bowler}",
}


@dataclass
class Bat:
    runs: int = 0
    balls: int = 0
    fours: int = 0
    sixes: int = 0
    out_kind: str | None = None
    out_bowler: str | None = None


@dataclass
class Bowl:
    balls: int = 0
    runs: int = 0
    wickets: int = 0
    wides: int = 0
    noballs: int = 0


@dataclass
class Innings:
    batting_fs_id: int
    order: list[str] = field(default_factory=list)
    batting: dict[str, Bat] = field(default_factory=dict)
    bowling_order: list[str] = field(default_factory=list)
    bowling: dict[str, Bowl] = field(default_factory=dict)
    extras: dict[str, int] = field(default_factory=lambda: dict.fromkeys(
        ("wides", "noballs", "byes", "legbyes", "penalty"), 0
    ))
    total: int = 0
    legal_balls: int = 0
    wickets: int = 0
    scheduled_balls: int | None = None
    fall: list[tuple[int, int, str, int, int]] = field(default_factory=list)


def overs(balls: int) -> str:
    return f"{balls // 6}.{balls % 6}"


def _header(conn, match_id: str) -> tuple:
    with conn.cursor() as cur:
        cur.execute(
            """
            select m.match_date, m.season_year, m.raw_season_label, m.venue, m.city,
                   a.display_name, b.display_name, m.team_a_fs_id, m.team_b_fs_id,
                   t.display_name, m.toss_decision,
                   w.display_name, m.result_type, m.result_margin, m.decided_by,
                   m.had_super_over, m.was_reduced, p.primary_name
            from matches m
            join franchise_seasons a on a.franchise_season_id = m.team_a_fs_id
            join franchise_seasons b on b.franchise_season_id = m.team_b_fs_id
            left join franchise_seasons t on t.franchise_season_id = m.toss_winner_fs_id
            left join franchise_seasons w on w.franchise_season_id = m.winner_fs_id
            left join people p on p.person_id = m.player_of_match_id
            where m.match_id = %s
            """,
            (match_id,),
        )
        return cur.fetchone()


def _names(conn, match_id: str) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select p.person_id, p.primary_name
            from appearances a join people p using (person_id)
            where a.match_id = %s
            """,
            (match_id,),
        )
        return dict(cur.fetchall())


def _squads(conn, match_id: str) -> dict[int, list[str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select a.franchise_season_id, a.person_id
            from appearances a join people p using (person_id)
            where a.match_id = %s and a.named_in_squad
            order by a.franchise_season_id, p.primary_name
            """,
            (match_id,),
        )
        squads: dict[int, list[str]] = {}
        for fs_id, person_id in cur.fetchall():
            squads.setdefault(fs_id, []).append(person_id)
        return squads


def build_innings(conn, match_id: str) -> dict[int, Innings]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select innings_no, batting_fs_id, batter_id, bowler_id, non_striker_id,
                   runs_batter, runs_extras,
                   extra_wides, extra_noballs, extra_byes, extra_legbyes, extra_penalty,
                   legal_ball, credited_to_bowler, wicket_kind, player_out_id,
                   over_no, ball_no, innings_scheduled_balls
            from deliveries
            where match_id = %s
            order by innings_no, over_no, ball_no
            """,
            (match_id,),
        )
        rows = cur.fetchall()

    innings: dict[int, Innings] = {}
    for r in rows:
        (no, batting_fs, batter, bowler, non_striker, runs_batter, runs_extras,
         wides, noballs, byes, legbyes, penalty, legal, credited, kind, out,
         over_no, ball_no, scheduled) = r

        inn = innings.setdefault(no, Innings(batting_fs_id=batting_fs))
        inn.scheduled_balls = scheduled

        for pid in (batter, non_striker):
            if pid not in inn.batting:
                inn.batting[pid] = Bat()
                inn.order.append(pid)
        if bowler not in inn.bowling:
            inn.bowling[bowler] = Bowl()
            inn.bowling_order.append(bowler)

        bat = inn.batting[batter]
        bat.runs += runs_batter
        # Balls faced excludes wides ONLY. A no-ball is a ball faced - the batter can
        # and does score off it - but it is not a legal ball, so `legal_ball` is the
        # wrong predicate here and undercounts. See SPEC A22.
        bat.balls += wides == 0
        bat.fours += runs_batter == 4
        bat.sixes += runs_batter == 6

        # A1: the bowler is charged for wides and no-balls, not for byes and legbyes.
        bowl = inn.bowling[bowler]
        bowl.balls += legal
        bowl.runs += runs_batter + wides + noballs
        bowl.wides += wides
        bowl.noballs += noballs
        bowl.wickets += bool(credited)

        inn.extras["wides"] += wides
        inn.extras["noballs"] += noballs
        inn.extras["byes"] += byes
        inn.extras["legbyes"] += legbyes
        inn.extras["penalty"] += penalty
        inn.total += runs_batter + runs_extras
        inn.legal_balls += legal

        if kind is not None:
            if kind not in NOT_A_WICKET:
                inn.wickets += 1
            dismissed = inn.batting.setdefault(out, Bat())
            dismissed.out_kind = kind
            dismissed.out_bowler = bowler if kind in SHORT_KIND else None
            inn.fall.append((inn.wickets, inn.total, out, over_no, ball_no))
    return innings


def dismissal(bat: Bat, name) -> str:
    if bat.out_kind is None:
        return "not out"
    template = SHORT_KIND.get(bat.out_kind)
    if template is None:
        return bat.out_kind
    return template.format(bowler=name(bat.out_bowler))


def print_scorecard(conn, match_id: str) -> None:
    header = _header(conn, match_id)
    if header is None:
        print(f"\n  {match_id}: not loaded\n")
        return

    (date, season, raw_label, venue, city, team_a, team_b, fs_a, fs_b,
     toss, decision, winner, result_type, margin, decided_by,
     had_super_over, was_reduced, potm) = header

    names = _names(conn, match_id)
    squads = _squads(conn, match_id)
    display = {fs_a: team_a, fs_b: team_b}

    def name(pid: str) -> str:
        return names.get(pid, f"<{pid}>")

    rule = "=" * 78
    print(f"\n{rule}")
    print(f"  {team_a} v {team_b}")
    label = f" (source label {raw_label})" if raw_label and raw_label != str(season) else ""
    # Venue strings often already end in the city ("...Chepauk, Chennai").
    where = venue or city or "venue not recorded"
    if city and venue and city not in venue:
        where = f"{venue}, {city}"
    print(f"  {where} | {date:%d %B %Y} | IPL {season}{label}")
    if toss:
        print(f"  Toss: {toss}, chose to {decision}")

    if result_type == "no result":
        outcome = "No result"
    elif result_type == "tie":
        outcome = f"Match tied. {winner} won the eliminator"
    elif winner:
        outcome = f"{winner} won by {margin} {result_type}"
    else:
        outcome = "Result not recorded"
    if decided_by == "dls":
        outcome += " (D/L)"
    print(f"  {outcome}")

    flags = [f for f, on in (("super over", had_super_over), ("reduced", was_reduced)) if on]
    if flags:
        print(f"  Flags: {', '.join(flags)}")
    if potm:
        print(f"  Player of the match: {potm}")
    print(rule)

    innings = build_innings(conn, match_id)
    super_over_seen = 0
    for no in sorted(innings):
        inn = innings[no]
        is_super = no > 2 and had_super_over
        if is_super:
            super_over_seen += 1
            title = f"SUPER OVER {(super_over_seen + 1) // 2} - {display[inn.batting_fs_id]}"
        else:
            title = f"INNINGS {no} - {display[inn.batting_fs_id]}"

        if is_super:
            scheduled = ""
        elif inn.scheduled_balls is None:
            scheduled = ", scheduled length NOT KNOWN"
        else:
            scheduled = f" of {overs(inn.scheduled_balls)}"
        print(f"\n  {title}")
        print(f"  {inn.total}/{inn.wickets}  ({overs(inn.legal_balls)}{scheduled})")

        print(f"\n  {'Batting':<24}{'':<22}{'R':>4}{'B':>5}{'4s':>4}{'6s':>4}{'SR':>8}")
        for pid in inn.order:
            bat = inn.batting[pid]
            how = dismissal(bat, name)
            sr = f"{bat.runs / bat.balls * 100:.2f}" if bat.balls else "-"
            print(
                f"  {name(pid):<24}{how:<22}{bat.runs:>4}{bat.balls:>5}"
                f"{bat.fours:>4}{bat.sixes:>4}{sr:>8}"
            )

        e = inn.extras
        print(
            f"  {'Extras':<24}{'':<22}{sum(e.values()):>4}"
            f"   (b {e['byes']}, lb {e['legbyes']}, w {e['wides']}, "
            f"nb {e['noballs']}, p {e['penalty']})"
        )
        print(f"  {'TOTAL':<24}{'':<22}{inn.total:>4}   for {inn.wickets}")

        absent = [p for p in squads.get(inn.batting_fs_id, []) if p not in inn.batting]
        if absent:
            print(f"\n  Did not bat: {', '.join(name(p) for p in absent)}")
        if inn.fall:
            print("\n  Fall of wickets:")
            for line in _wrap(_fall(inn, name), 74):
                print(f"    {line}")

        print(f"\n  {'Bowling':<24}{'O':>6}{'R':>6}{'W':>4}{'Econ':>8}{'wd':>5}{'nb':>4}")
        for pid in inn.bowling_order:
            b = inn.bowling[pid]
            econ = f"{b.runs / b.balls * 6:.2f}" if b.balls else "-"
            print(
                f"  {name(pid):<24}{overs(b.balls):>6}{b.runs:>6}{b.wickets:>4}"
                f"{econ:>8}{b.wides:>5}{b.noballs:>4}"
            )
    print()


def _fall(inn: Innings, name) -> str:
    return "   ".join(
        f"{wicket}-{total} ({name(pid)}, {over_no}.{ball_no})"
        for wicket, total, pid, over_no, ball_no in inn.fall
    )


def _wrap(text: str, width: int) -> list[str]:
    lines, current = [], ""
    for part in text.split("   "):
        if current and len(current) + len(part) + 3 > width:
            lines.append(current)
            current = part
        else:
            current = f"{current}   {part}" if current else part
    if current:
        lines.append(current)
    return lines
