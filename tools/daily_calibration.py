"""Calibrate the daily challenge's floors against the engine, rather than picking numbers
that look plausible.

A day asks for a margin -- more than N runs, at least N wickets in hand, inside N overs --
and a floor is only worth asking for if a competently drafted twelve meets it often enough
to be worth trying and rarely enough to be worth ranking. That is a measurement, and this
is where it is taken. Same posture as A33's balls floors: the number lands where the
evidence puts it, and it only looks round because of the sweep grid.

    uv run python -m tools.daily_calibration --trials 300

Reads the committed deck snapshot (A107), so it needs no database. Nothing here is
imported by the app -- it exists to produce the constants in `game.scenarios` and to be
re-run when the ratings or the engine move.
"""

from __future__ import annotations

import argparse
import random
from collections import Counter

from game.__main__ import attack, lineup
from etl.feasibility import POLICIES, run_draft
from game.scenarios import (
    BALLS_PER_INNINGS, BONUS_ORDER, BONUS_TESTS, MINE, OVERS_LIMITS, RUN_MARGINS,
    WICKET_FLOORS, WICKETS_PER_INNINGS,
)
from game.simulator import BALLS_PER_OVER, play_innings
from tools import snapshot_deck
from game.season import Side
from web.daily import side_for_fs, with_bowling_depth


def _twelve(deck, rng, policy="rational"):
    """One drafted twelve, from the same `run_draft` a real player goes through."""
    while True:
        result = run_draft(deck, POLICIES[policy], rng)
        if result.completed:
            return result


def _match(model, deck, rng, policy):
    """One full match: a drafted twelve against a real historical side, both attacks real.

    Returns the two innings in BOWLING order for each of the two orientations a day can
    take, so one drafted squad measures both `win_by_runs` and the two chase kinds."""
    drafted = _twelve(deck, rng, policy)
    fs_id = rng.choice(deck.fs_ids)
    opposition = side_for_fs(deck, fs_id)
    if opposition is None:
        return None

    # Through the same `with_bowling_depth` a real day plays, or this measures a match the
    # engine would refuse -- a legal eleven can be a bowler short until the Impact Player
    # is locked in, and `choose_bowler` runs out partway through the innings if he is not.
    mine = with_bowling_depth(Side(name="YOU", short="YOU", xi=list(drafted.order),
                                   impact=drafted.impact))
    opposition = with_bowling_depth(opposition)

    my_batting = lineup(list(mine.xi), model, mine.impact)
    my_attack = attack(list(mine.xi), model, mine.impact)
    their_batting = lineup(list(opposition.xi), model, opposition.impact)
    their_attack = attack(list(opposition.xi), model, opposition.impact)

    # Batting first: the win_by_runs orientation.
    bat_first = play_innings(model, my_batting, their_attack, rng)
    their_reply = play_innings(model, their_batting, my_attack, rng, target=bat_first.runs)

    # Bowling first: the win_by_wickets / chase_in_overs orientation.
    they_set = play_innings(model, their_batting, my_attack, rng)
    my_chase = play_innings(model, my_batting, their_attack, rng, target=they_set.runs)
    return bat_first, their_reply, they_set, my_chase


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--policy", default="rational", choices=sorted(POLICIES))
    args = ap.parse_args()

    doc = snapshot_deck.read_document()
    deck, model = snapshot_deck.deck_from(doc), snapshot_deck.model_from(doc)
    rng = random.Random(args.seed)

    run_margins, in_hand, spare, chased = [], [], [], 0
    bonuses = Counter()
    played = 0
    for _ in range(args.trials):
        got = _match(model, deck, rng, args.policy)
        if got is None:
            continue
        played += 1
        bat_first, their_reply, _they_set, my_chase = got
        run_margins.append(bat_first.runs - their_reply.runs)
        if my_chase.chased:
            chased += 1
            in_hand.append(WICKETS_PER_INNINGS - my_chase.wickets)
            spare.append(BALLS_PER_INNINGS - my_chase.balls)
        # Bonuses are read off the bowl-first orientation, which is the one where both
        # innings are the player's own doing.
        for name in BONUS_ORDER:
            reads, test = BONUS_TESTS[name]
            innings = my_chase if reads is MINE else _they_set
            if test(innings):
                bonuses[name] += 1

    print(f"{played} matches, {args.policy} drafter, seed {args.seed}\n")

    print("win_by_runs -- share of matches won by MORE than N runs")
    for n in RUN_MARGINS:
        hit = sum(1 for m in run_margins if m > n)
        print(f"  {n:>3} runs   {hit / played:6.1%}")
    print(f"  (won at all {sum(1 for m in run_margins if m > 0) / played:.1%})\n")

    print(f"win_by_wickets -- chased {chased / played:.1%} of the time; "
          "share of ALL matches chased with at least N in hand")
    for n in WICKET_FLOORS:
        print(f"  {n:>3} wkts   {sum(1 for w in in_hand if w >= n) / played:6.1%}")
    print()

    print("chase_in_overs -- share of ALL matches chased inside N overs")
    for n in OVERS_LIMITS:
        hit = sum(1 for b in spare if BALLS_PER_INNINGS - b <= n * BALLS_PER_OVER)
        print(f"  {n:>3} overs  {hit / played:6.1%}")
    print()

    print("bonus hit rate (bowl-first orientation)")
    for name in BONUS_ORDER:
        print(f"  {name:<22} {bonuses[name] / played:6.1%}")


if __name__ == "__main__":
    main()
