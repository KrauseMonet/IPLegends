"""Cricsheet match JSON -> typed rows. Pure: no database, no I/O, no network.

`parse_match(raw, match_id)` returns a ParsedMatch holding everything four tables
need, with franchises as canonical keys rather than surrogate ids. The loader is
what resolves those to franchise_season_id, which keeps this module testable
against a single JSON file with nothing else running.

Anything the source does not state is None and is recorded in `warnings`.
Nothing is guessed.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from etl.franchise_map import canonical

FULL_INNINGS_BALLS = 120

# SPEC 4.7 / migrations/003. Wides and no-balls do not count as balls faced or
# balls bowled; byes, legbyes and penalties are legal deliveries that happen to
# concede runs off the bat's absence.
ILLEGAL_EXTRAS = frozenset({"wides", "noballs"})

# SPEC: credited_to_bowler. The bowler earns the wicket for modes of dismissal
# that follow from the delivery, and does not for those that follow from what
# happened afterwards. Every kind below was observed in the archive; an unseen
# kind is a warning, not a guess.
BOWLER_CREDITED = {
    "bowled": True,
    "caught": True,
    "caught and bowled": True,
    "lbw": True,
    "stumped": True,
    "hit wicket": True,
    "run out": False,
    "obstructing the field": False,
    "retired hurt": False,
    "retired out": False,
}


class ParsedPerson(BaseModel):
    person_id: str
    name: str


class ParsedAppearance(BaseModel):
    person_id: str
    franchise: str
    named_in_squad: bool
    participated: bool


class ParsedDelivery(BaseModel):
    innings_no: int
    over_no: int
    ball_no: int
    batting_franchise: str
    bowling_franchise: str
    batter_id: str
    bowler_id: str
    non_striker_id: str
    runs_batter: int
    runs_extras: int
    extra_wides: int
    extra_noballs: int
    extra_byes: int
    extra_legbyes: int
    extra_penalty: int
    innings_scheduled_balls: int | None
    is_super_over: bool
    legal_ball: bool
    credited_to_bowler: bool | None
    over_miscounted: bool
    wicket_kind: str | None
    player_out_id: str | None
    phase: str | None


class ParsedMatch(BaseModel):
    match_id: str
    match_date: date
    season_year: int
    raw_season_label: str | None
    venue: str | None
    city: str | None
    team_a: str
    team_b: str
    toss_winner: str | None
    toss_decision: str | None
    winner: str | None
    result_type: str | None
    result_margin: int | None
    decided_by: str | None
    scheduled_overs: int | None
    had_super_over: bool
    was_reduced: bool
    player_of_match_id: str | None
    people: list[ParsedPerson]
    appearances: list[ParsedAppearance]
    deliveries: list[ParsedDelivery]
    warnings: list[str]


def target_overs_to_balls(overs: float) -> int:
    """`9.2` means nine overs and two balls, so 56 - not 9.2 overs.

    SPEC 4.5: this is the whole reason scheduled length is stored in balls.
    """
    whole = int(overs)
    return whole * 6 + round((overs - whole) * 10)


def _is_legal(delivery: dict) -> bool:
    return not (ILLEGAL_EXTRAS & set(delivery.get("extras") or {}))


def _wickets_fallen(innings: dict) -> int:
    """Dismissals that cost the side a batter. Retired hurt does not."""
    return sum(
        1
        for over in innings.get("overs", [])
        for d in over["deliveries"]
        for w in d.get("wickets", [])
        if w["kind"] != "retired hurt"
    )


def _scheduled_balls(
    innings: dict,
    innings_no: int,
    *,
    has_chase: bool,
    shortened: bool,
    warnings: list[str],
    match_id: str,
) -> int | None:
    """Balls this innings was scheduled to last, or None when unknowable.

    SPEC 4.5: `info.overs` is 20 in every match and carries no information.

    A chasing innings states its own length in `target.overs`, always. The first
    innings never states it, so it has to be read off what happened - and the
    reading needs the rest of the match, because an innings that stopped short
    of twenty overs did so for one of three reasons and only two of them say
    anything about the schedule.

      - twenty overs bowled -> 120.
      - no second innings at all -> the match was abandoned during this innings
        and nothing left in the file attests to its intended length -> None.
      - the chase was scheduled for a full twenty -> so was this one, and it
        ended early on wickets -> 120.
      - the match was shortened -> the innings closed when the schedule ran out,
        so a complete final over gives the length directly. An incomplete final
        over means rain stopped play mid-over, and an all-out side means the
        innings ended on wickets; neither is observable -> None.
    """
    if innings.get("super_over"):
        return None

    target = (innings.get("target") or {}).get("overs")
    if target is not None:
        return target_overs_to_balls(target)

    overs = innings.get("overs", [])
    if not overs:
        return None

    over_count = len(overs)
    if over_count >= 20:
        return FULL_INNINGS_BALLS

    if not has_chase:
        warnings.append(
            f"{match_id} innings {innings_no}: match abandoned after {over_count} "
            f"overs with no second innings; scheduled length not observable"
        )
        return None

    if not shortened:
        return FULL_INNINGS_BALLS

    if _wickets_fallen(innings) >= 10:
        warnings.append(
            f"{match_id} innings {innings_no}: all out in {over_count} overs of a "
            f"shortened match; scheduled length not observable"
        )
        return None

    last = overs[-1]
    expected_last = (
        (innings.get("miscounted_overs") or {})
        .get(str(last["over"]), {})
        .get("balls", 6)
    )
    if sum(1 for d in last["deliveries"] if _is_legal(d)) < expected_last:
        warnings.append(
            f"{match_id} innings {innings_no}: shortened match abandoned mid-over "
            f"after {over_count} overs; scheduled length not observable"
        )
        return None

    return over_count * 6


def _in_powerplay(powerplays: list[dict], over_no: int, ball_no: int) -> bool:
    """Powerplay bounds are `over.position`, counting illegal deliveries.

    A bound of `5.9` occurs in the archive, which no legal-ball index can reach,
    so the comparison is against ball_no rather than the scorecard ball number.
    """
    for block in powerplays:
        start_over, start_ball = _split_bound(block["from"])
        end_over, end_ball = _split_bound(block["to"])
        if (start_over, start_ball) <= (over_no, ball_no) <= (end_over, end_ball):
            return True
    return False


def _split_bound(value: float) -> tuple[int, int]:
    whole = int(value)
    return whole, round((value - whole) * 10)


def _phase(
    over_no: int,
    ball_no: int,
    powerplays: list[dict],
    scheduled_balls: int | None,
) -> str | None:
    """SPEC 4.8, display splits only. The state model does not use these."""
    if _in_powerplay(powerplays, over_no, ball_no):
        return "powerplay"
    if scheduled_balls is None:
        return None
    death_from_over = (scheduled_balls * 3 // 4) // 6
    return "death" if over_no >= death_from_over else "middle"


def parse_match(raw: dict, match_id: str) -> ParsedMatch:
    info = raw["info"]
    registry: dict[str, str] = info["registry"]["people"]
    warnings: list[str] = []

    def person(name: str, context: str) -> str | None:
        """SPEC 4.1: names are resolved through the registry, never keyed on."""
        person_id = registry.get(name)
        if person_id is None:
            warnings.append(f"{match_id}: {name!r} ({context}) absent from registry")
        return person_id

    dates = info["dates"]
    match_date = date.fromisoformat(dates[0])

    # SPEC 4.2: never info.season. It reads '2007/08' for 2008, '2009/10' for
    # 2010 and '2020/21' for 2020, while 2009 - played in South Africa, where a
    # split label would at least be defensible - reads plainly '2009'.
    season_year = match_date.year

    squads: dict[str, str] = {}
    for team_name in info["teams"]:
        squads[team_name] = canonical(team_name)

    team_names = list(info["teams"])
    team_a, team_b = (squads[team_names[0]], squads[team_names[1]])

    toss = info.get("toss") or {}
    toss_winner = squads.get(toss.get("winner")) if toss.get("winner") else None

    outcome = info.get("outcome") or {}
    winner_name = outcome.get("winner") or outcome.get("eliminator")
    winner = squads.get(winner_name) if winner_name else None
    result_type, result_margin, decided_by = _result(outcome, warnings, match_id)

    people: dict[str, str] = {}
    team_of: dict[str, str] = {}
    squad_named: set[str] = set()
    participated: set[str] = set()

    for team_name, roster in info["players"].items():
        franchise = squads.get(team_name)
        if franchise is None:
            raise ValueError(f"{match_id}: info.players lists unknown team {team_name!r}")
        for name in roster:
            person_id = person(name, "squad member")
            if person_id is None:
                continue
            people[person_id] = name
            team_of[person_id] = franchise
            squad_named.add(person_id)

    deliveries: list[ParsedDelivery] = []
    had_super_over = False
    scheduled_by_innings: list[int | None] = []

    # Read off the chase before parsing anything, because the first innings'
    # scheduled length can only be settled by looking at the rest of the match.
    chase_targets = [
        (i.get("target") or {}).get("overs")
        for i in raw.get("innings", [])
        if not i.get("super_over")
    ]
    has_chase = any(t is not None for t in chase_targets)
    shortened = any(t is not None and t < 20 for t in chase_targets)

    for index, innings in enumerate(raw.get("innings", []), start=1):
        is_super_over = bool(innings.get("super_over"))
        had_super_over = had_super_over or is_super_over

        batting_name = innings["team"]
        batting = squads.get(batting_name)
        if batting is None:
            raise ValueError(f"{match_id}: innings names unknown team {batting_name!r}")
        bowling = team_b if batting == team_a else team_a

        scheduled_balls = _scheduled_balls(
            innings,
            index,
            has_chase=has_chase,
            shortened=shortened,
            warnings=warnings,
            match_id=match_id,
        )
        if not is_super_over:
            scheduled_by_innings.append(scheduled_balls)

        powerplays = innings.get("powerplays", [])
        miscounted = {str(k) for k in (innings.get("miscounted_overs") or {})}

        for over in innings.get("overs", []):
            over_no = over["over"]
            over_miscounted = str(over_no) in miscounted

            for ball_no, delivery in enumerate(over["deliveries"], start=1):
                extras = delivery.get("extras") or {}
                runs = delivery["runs"]
                legal = _is_legal(delivery)

                batter_id = person(delivery["batter"], "batter")
                bowler_id = person(delivery["bowler"], "bowler")
                non_striker_id = person(delivery["non_striker"], "non-striker")
                if not (batter_id and bowler_id and non_striker_id):
                    raise ValueError(
                        f"{match_id} innings {index} over {over_no} ball {ball_no}: "
                        f"a named participant is absent from info.registry.people"
                    )

                wicket_kind = None
                player_out_id = None
                credited = None
                wickets = delivery.get("wickets", [])
                if wickets:
                    wicket = wickets[0]
                    wicket_kind = wicket["kind"]
                    player_out_id = person(wicket["player_out"], "dismissed batter")
                    if wicket_kind not in BOWLER_CREDITED:
                        warnings.append(
                            f"{match_id}: unrecognised dismissal {wicket_kind!r}; "
                            f"credited_to_bowler left null"
                        )
                    credited = BOWLER_CREDITED.get(wicket_kind)
                    for fielder in wicket.get("fielders", []):
                        fielder_id = person(fielder["name"], "fielder")
                        if fielder_id is None:
                            continue
                        people.setdefault(fielder_id, fielder["name"])
                        team_of.setdefault(fielder_id, bowling)
                        if not is_super_over:
                            participated.add(fielder_id)

                if not is_super_over:
                    participated.update(
                        {batter_id, bowler_id, non_striker_id}
                        | ({player_out_id} if player_out_id else set())
                    )

                # SPEC 5 A16. Both replacement kinds mean the player took the
                # field: `match` is the Impact Player actually brought on,
                # `role` a substitute taking over a bowling or fielding role.
                # The 12th name that never gets used produces no entry here,
                # which is exactly the distinction `participated` exists for.
                for kind, entries in (delivery.get("replacements") or {}).items():
                    for entry in entries:
                        incoming = person(entry["in"], f"{kind} replacement")
                        if incoming is None:
                            continue
                        people.setdefault(incoming, entry["in"])
                        # A `match` entry names its team. A `role` entry never
                        # does and never needs to: taking over a bowling or
                        # fielding role puts the player on the fielding side.
                        team_of.setdefault(
                            incoming,
                            squads[entry["team"]] if kind == "match" else bowling,
                        )
                        if not is_super_over:
                            participated.add(incoming)

                deliveries.append(
                    ParsedDelivery(
                        innings_no=index,
                        over_no=over_no,
                        ball_no=ball_no,
                        batting_franchise=batting,
                        bowling_franchise=bowling,
                        batter_id=batter_id,
                        bowler_id=bowler_id,
                        non_striker_id=non_striker_id,
                        runs_batter=runs["batter"],
                        runs_extras=runs["extras"],
                        extra_wides=extras.get("wides", 0),
                        extra_noballs=extras.get("noballs", 0),
                        extra_byes=extras.get("byes", 0),
                        extra_legbyes=extras.get("legbyes", 0),
                        extra_penalty=extras.get("penalty", 0),
                        innings_scheduled_balls=scheduled_balls,
                        is_super_over=is_super_over,
                        legal_ball=legal,
                        credited_to_bowler=credited,
                        over_miscounted=over_miscounted,
                        wicket_kind=wicket_kind,
                        player_out_id=player_out_id,
                        phase=None
                        if is_super_over
                        else _phase(over_no, ball_no, powerplays, scheduled_balls),
                    )
                )

    potm = (info.get("player_of_match") or [None])[0]
    potm_id = person(potm, "player of the match") if potm else None

    # SPEC 5 A7. Two flags, deliberately not one. `named_in_squad` is what
    # info.players asserts; a substitute fielder or an Impact Player brought on
    # gets an appearance row without it.
    appearances = [
        ParsedAppearance(
            person_id=person_id,
            franchise=franchise,
            named_in_squad=person_id in squad_named,
            participated=person_id in participated,
        )
        for person_id, franchise in sorted(team_of.items())
    ]

    return ParsedMatch(
        match_id=match_id,
        match_date=match_date,
        season_year=season_year,
        raw_season_label=str(info["season"]) if info.get("season") else None,
        venue=info.get("venue"),
        city=info.get("city"),
        team_a=team_a,
        team_b=team_b,
        toss_winner=toss_winner,
        toss_decision=toss.get("decision"),
        winner=winner,
        result_type=result_type,
        result_margin=result_margin,
        decided_by=decided_by,
        scheduled_overs=info.get("overs"),
        had_super_over=had_super_over,
        was_reduced=any(
            b is not None and b < FULL_INNINGS_BALLS for b in scheduled_by_innings
        ),
        player_of_match_id=potm_id,
        people=[ParsedPerson(person_id=p, name=n) for p, n in sorted(people.items())],
        appearances=appearances,
        deliveries=deliveries,
        warnings=warnings,
    )


def _result(
    outcome: dict, warnings: list[str], match_id: str
) -> tuple[str | None, int | None, str | None]:
    """Returns (result_type, result_margin, decided_by).

    A tie is stored as a tie even when a super over settled it. Every tie in the
    archive carries an `eliminator`, so `winner_fs_id` holds the side that won
    the super over while `result_type` still reads 'tie', and `decided_by` says
    which of the two the reader is looking at.

    `decided_by` prefers 'dls' over the margin type. The margin type is already
    in `result_type`; D/L involvement is recorded nowhere else, and a D/L result
    carries an ordinary runs or wickets margin that makes it invisible otherwise.
    """
    method = outcome.get("method")
    if method is not None and method != "D/L":
        warnings.append(f"{match_id}: unrecognised outcome.method {method!r}")

    if "result" in outcome:
        result = outcome["result"]
        if result not in ("tie", "no result"):
            warnings.append(f"{match_id}: unrecognised outcome.result {result!r}")
            return None, None, None
        if result == "tie":
            if "eliminator" not in outcome:
                warnings.append(f"{match_id}: tie with no eliminator")
                return result, None, None
            return result, None, "eliminator"
        return result, None, None

    by = outcome.get("by") or {}
    for key in ("runs", "wickets"):
        if key in by:
            return key, by[key], "dls" if method == "D/L" else key

    warnings.append(f"{match_id}: outcome has neither a result nor a margin")
    return None, None, None
