"""One playable draft and one simulated match, end to end.

    uv run python -m game                 # draft two squads, play the match
    uv run python -m game --seed 42
    uv run python -m game --validate      # the engine against the archive it was fitted on

The draft is not reimplemented here. It is `etl.feasibility`'s -- the same deck, the same
uniform sampling over 166 franchise-seasons, the same dedupe, the same deal-time guarantee,
and above all the same TEMPLATE, so a template retune moves the game and the feasibility
check together instead of leaving them describing two different games.
"""

from __future__ import annotations

import argparse
import os
import random

import psycopg
from dotenv import load_dotenv

from etl.feasibility import Card, POLICIES, SQUAD_SIZE, load_deck, run_draft
from etl.state_model import FITTING_SET, NOT_A_WICKET
from game.simulator import (
    BALLS_PER_OVER, MAX_OVERS_PER_BOWLER, OVERS, Innings, Model, Player,
    load_model, play_innings,
)

# Where each band bats. `tail` last, and a player with no band at all beside them -- see
# `bat_delta` for why those two are not the same case.
BAND_ORDER = {"opener": 0, "top_order": 1, "middle": 2, "finisher": 3, "tail": 4}

BOWLERS_NEEDED = OVERS // MAX_OVERS_PER_BOWLER


def bat_delta(card: Card, model: Model) -> tuple[float, bool]:
    """The batting delta for a drafted card, and whether it is a real rating.

    A34's floor means most of a squad has no batting rating at all -- five bowlers and a
    couple of the middle order -- and every one of them still has to bat. Three ways to
    handle that, two of them wrong:

    - Give them zero. Zero is league average after centring, so a number 10 would bat like a
      rated middle-order player. This is the A23 failure exactly: defaulting an unobserved
      value to something plausible, where the plausible thing is systematically wrong for a
      whole class.
    - Rate them anyway with a small `k`. A33 measured that the list does not stabilise below
      100 balls, so this manufactures an individual number the evidence cannot support.
    - Use the band's pooled below-floor level. **This one.** No individual is rated, and none
      needs to be: 707 tail seasons and 7,144 balls pin the population level at -0.636 runs
      per ball even though not one of those seasons could be rated alone. It is A43's rule
      about evidence, applied to the other side of the same floor.

    A card with no band never batted in that season, so it has no below-floor level either.
    It takes `tail`'s, which is a bound rather than an estimate -- a bowler who did not bat
    at all is unlikely to be better than the pooled tail -- and it is the one number in the
    engine chosen by argument instead of measurement, so it is flagged in the scorecard.
    """
    if card.bat is not None:
        return card.bat, True
    band = card.band if card.band in model.unrated_bat else "tail"
    return model.unrated_bat[band] - model.season_mean[("batting", card.season_year)], False


def to_player(card: Card, model: Model) -> Player:
    delta, rated = bat_delta(card, model)
    return Player(name=card.name, bat=delta, bowl=card.bowl, rated_bat=rated)


OVERSEAS_LIMIT = 4


def viable(xi: list[Card]) -> bool:
    """An XI has to keep wicket and get through twenty overs."""
    return (any(c.role == "keeper" for c in xi)
            and sum(c.has_bowl for c in xi) >= BOWLERS_NEEDED)


def choose_xi(squad: list[Card]) -> list[Card]:
    """Eleven from fifteen: a keeper, five bowlers, the best bats left, then made legal.

    Five bowlers because twenty overs at four each is exactly five, so the count is the
    rule rather than a preference. The keeper is taken first and only then are the bowlers
    counted, because a keeper who also bowls would otherwise be picked twice and leave the
    attack an over short.

    The overseas limit is applied as a repair rather than as a constraint on the search.
    Greedy-then-repair gets the same answer here as a full search would -- swapping out the
    cheapest overseas player for the best home replacement is exactly the optimisation, on a
    squad of fifteen -- and it keeps the two rules readable separately, which matters
    because only one of them can be enforced honestly (see `overseas_status`).
    """
    keeper = max((c for c in squad if c.role == "keeper"), key=lambda c: c.rating)
    rest = [c for c in squad if c is not keeper]

    bowlers = sorted((c for c in rest if c.has_bowl), key=lambda c: -c.bowl)[:BOWLERS_NEEDED]
    remaining = [c for c in rest if c not in bowlers]
    batters = sorted(remaining, key=lambda c: -(c.bat if c.has_bat else -9.9))

    xi = [keeper] + bowlers + batters[:11 - 1 - len(bowlers)]
    return enforce_overseas(xi, squad)


def enforce_overseas(xi: list[Card], squad: list[Card]) -> list[Card]:
    """Swap overseas players out until at most four KNOWN overseas players remain.

    Known, not actual. A23 left `is_overseas` NULL for 314 people rather than defaulting it
    to Indian, so this enforces a lower bound and `overseas_status` reports how far the true
    count could be above it. Enforcing against the bound is the only move available that
    cannot be wrong in the dangerous direction -- the alternative, treating unknown as
    domestic, is the precise error A23 was written to stop, and it would field an illegal
    XI while printing that the rule was satisfied.

    The same reading applies to the replacement: only a KNOWN domestic player can repair
    the breach. Swapping an overseas player out for an unknown one lowers the count this
    function can see without lowering the count that matters, which is the whole shape of
    the A23 error running in the other direction.

    Returns the XI unchanged if no legal one exists, so the caller reports the failure
    rather than this silently returning ten players.
    """
    while sum(c.overseas is True for c in xi) > OVERSEAS_LIMIT:
        bench = [c for c in squad if c not in xi and c.overseas is False]
        swaps = [
            (out.rating - into.rating, out, into)
            for out in xi if out.overseas is True
            for into in bench
            if viable([c for c in xi if c is not out] + [into])
        ]
        if not swaps:
            return xi
        _, out, into = min(swaps, key=lambda s: s[0])
        xi = [c for c in xi if c is not out] + [into]
    return xi


def batting_order(xi: list[Card], model: Model) -> list[Player]:
    """Bands first, rating within a band, unbanded last. Not a captain's order, a band's."""
    ordered = sorted(
        xi,
        key=lambda c: (BAND_ORDER.get(c.band, len(BAND_ORDER)),
                       -(c.bat if c.has_bat else -9.9)),
    )
    return [to_player(c, model) for c in ordered]


def attack(xi: list[Card], model: Model) -> list[Player]:
    """The five who bowl, best first -- not everyone in the XI who can.

    Capped at five deliberately. `choose_bowler` hands the next over to whoever has bowled
    least, so an XI containing seven bowlers would give all seven three overs each: the two
    worst get a full share and the best gets a quarter of their four. That is not how a T20
    attack works, and it quietly flattens exactly the rating differences the draft is about.
    """
    return [to_player(c, model)
            for c in sorted((c for c in xi if c.has_bowl),
                            key=lambda c: -c.bowl)[:BOWLERS_NEEDED]]


def overseas_status(xi: list[Card]) -> str:
    """SPEC 1.1's four-overseas rule, reported honestly rather than enforced falsely.

    `is_overseas` is NULL for 314 of 816 people because A23 refused to default an unobserved
    nationality to Indian, and that refusal is exactly why this cannot be enforced yet: an
    XI with four known overseas players and three unknowns might be legal or might be seven
    deep. The honest report is the bound and the size of the gap, not a verdict.
    """
    known = sum(1 for c in xi if c.overseas is True)
    unknown = sum(1 for c in xi if c.overseas is None)
    if known > OVERSEAS_LIMIT:
        return (f"{known} known overseas and NO LEGAL XI EXISTS in this squad -- the draft "
                "has no nationality constraint, so it can deal one that cannot be fielded")
    if unknown == 0:
        return f"{known} overseas, 0 unknown -- legal"
    return (f"{known} overseas known, {unknown} unknown -- legal on what is known, but "
            f"CANNOT BE CERTIFIED: the true count is between {known} and "
            f"{known + unknown}. Needs nationality.csv, which is 314 rows unfilled.")


def print_draft(picks: list[tuple[Card, str]]) -> None:
    print(f"    {'#':>2}  {'dealt':38} {'pick':24} {'slot':18} rating")
    for i, (card, slot) in enumerate(picks, 1):
        dealt = f"{card.franchise} {card.season_year}"
        print(f"    {i:>2}  {dealt:38} {card.name:24} {slot:18} {card.rating:+.3f}")


def print_scorecard(innings: Innings, side: str, chasing: bool) -> None:
    print(f"\n  --- {side} ---")
    for card in innings.batting:
        if not card.faced_any:
            continue
        how = "" if card.out else "*"
        flag = "" if card.player.rated_bat else "  (unrated bat)"
        print(f"      {card.player.name:24} {card.runs:>4}{how} ({card.balls}){flag}")
    print(f"      {'extras':24} {innings.extras:>4}")
    print(f"      {'TOTAL':24} {innings.runs:>4}/{innings.wickets}  ({innings.overs} overs)")
    print("      bowling:")
    for b in sorted(innings.bowling, key=lambda b: -b.balls):
        if b.balls:
            print(f"        {b.player.name:22} {b.overs:>5}  {b.runs:>3}-{b.wickets}")
    if chasing and not innings.chased:
        print("      target not reached")


def archive_innings(conn) -> dict[str, float]:
    """What a real full-length first innings looks like, over the SPEC 7.1 fitting set.

    Queried rather than written down (A19). A figure pasted into a print statement stops
    being a measurement the moment the archive is reloaded, and this one is the whole
    yardstick.
    """
    n, total, extras, wickets, all_out, sd = conn.execute(
        f"""
        with fit as (
            select match_id, runs_batter + runs_extras as runs, runs_extras,
                   (wicket_kind is not null and wicket_kind <> all(%s)) as is_wicket
            from deliveries where {FITTING_SET}
        ), per_innings as (
            select match_id, sum(runs) as total, sum(runs_extras) as extras,
                   sum(case when is_wicket then 1 else 0 end) as w
            from fit group by 1
        )
        select count(*), avg(total), avg(extras), avg(w),
               avg(case when w >= 10 then 1.0 else 0.0 end), stddev_pop(total)
        from per_innings
        """,
        (list(NOT_A_WICKET),),
    ).fetchone()
    return {"innings": n, "total": float(total), "extras": float(extras),
            "wickets": float(wickets), "all_out": float(all_out), "sd": float(sd)}


def validate(conn, model: Model, trials: int, seed: int) -> None:
    """Does the engine reproduce the archive it was fitted on?

    Eleven league-average batters against five league-average bowlers -- every delta zero,
    so the tilt is the identity and the engine is doing nothing but walking the state model.
    The mean first innings that comes out has to be the mean first innings that went in.

    This is the one check here that cannot pass by agreeing with itself. The target is a
    property of the archive, computed by a different query over a population the engine
    never sees, and **the engine has no free parameter that could be moved to hit it** --
    there is nothing to tune, so a match is the state model composing correctly across an
    innings rather than a fit. Runs alone would be weak, because extras were calibrated to
    the same archive and would drag the total most of the way there on their own. Wickets,
    the all-out rate and the spread were not calibrated to anything, which is why they are
    in the table.
    """
    average = [Player(f"batter {i}", 0.0, 0.0) for i in range(11)]
    rng = random.Random(seed)
    innings = [play_innings(model, average, average[:BOWLERS_NEEDED], rng)
               for _ in range(trials)]
    archive = archive_innings(conn)

    totals = [i.runs for i in innings]
    mean = sum(totals) / len(totals)
    variance = sum((t - mean) ** 2 for t in totals) / len(totals)

    rows = [
        ("mean total", mean, archive["total"]),
        ("  of which extras", sum(i.extras for i in innings) / trials, archive["extras"]),
        ("mean wickets", sum(i.wickets for i in innings) / trials, archive["wickets"]),
        ("SD of totals", variance ** 0.5, archive["sd"]),
    ]

    print(f"=== engine against the archive, {trials} league-average innings, seed {seed} ===\n")
    print(f"    {'':28}{'simulated':>12}{'archive':>12}")
    for label, got, want in rows:
        print(f"    {label:28}{got:12.1f}{want:12.1f}")
    print(f"    {'all out':28}"
          f"{sum(i.wickets == 10 for i in innings) / trials:11.1%}"
          f"{archive['all_out']:11.1%}")
    print(f"\n    simulated spread {min(totals)} to {max(totals)}, over "
          f"{archive['innings']:,} real innings for the archive column")


def main() -> None:
    ap = argparse.ArgumentParser(description="SPEC 1.1 draft and match")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--policy", default="rational", choices=sorted(POLICIES))
    ap.add_argument("--validate", action="store_true",
                    help="play league-average innings and compare with the archive")
    ap.add_argument("--trials", type=int, default=2000)
    args = ap.parse_args()

    load_dotenv()
    with psycopg.connect(os.environ["DIRECT_URL"]) as conn:
        model = load_model(conn)
        if args.validate:
            validate(conn, model, args.trials, args.seed)
            return
        deck = load_deck(conn)

    rng = random.Random(args.seed)
    squads = []
    for side in ("YOU", "OPPONENT"):
        result = run_draft(deck, POLICIES[args.policy], rng)
        if not result.completed:
            raise SystemExit(f"{side} stranded on {result.stranded_on} -- check 12 lies")
        squads.append((side, result.picks))

    for side, picks in squads:
        print(f"\n=== {side}: {SQUAD_SIZE} picks, uniform deck, "
              f"{args.policy} drafter, seed {args.seed} ===\n")
        print_draft(picks)

    elevens = []
    for side, picks in squads:
        xi = choose_xi([c for c, _ in picks])
        print(f"\n=== {side}: playing XI ===\n")
        for c in sorted(xi, key=lambda c: (BAND_ORDER.get(c.band, 9),
                                           -(c.bat if c.has_bat else -9.9))):
            bat = f"{c.bat:+.3f}" if c.has_bat else "  --  "
            bowl = f"{c.bowl:+.3f}" if c.has_bowl else "  --  "
            print(f"    {c.name:24} {c.franchise} {c.season_year}   "
                  f"{str(c.band or '-'):12} bat {bat}  bowl {bowl}")
        print(f"\n    four-overseas rule: {overseas_status(xi)}")
        elevens.append((side, xi))

    (side_a, xi_a), (side_b, xi_b) = elevens
    print(f"\n\n=== the match: {side_a} bat first ===")

    first = play_innings(model, batting_order(xi_a, model), attack(xi_b, model), rng)
    print_scorecard(first, f"{side_a} innings", chasing=False)

    second = play_innings(model, batting_order(xi_b, model), attack(xi_a, model), rng,
                          target=first.runs)
    print_scorecard(second, f"{side_b} innings, chasing {first.runs + 1}", chasing=True)

    print()
    if second.runs > first.runs:
        print(f"  {side_b} win by {10 - second.wickets} wickets "
              f"with {OVERS * BALLS_PER_OVER - second.balls} balls to spare")
    elif second.runs == first.runs:
        print("  tied")
    else:
        print(f"  {side_a} win by {first.runs - second.runs} runs")


if __name__ == "__main__":
    main()
