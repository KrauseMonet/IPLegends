"""Tune the auction's computer bidders against measured targets, not by feel. [A136]

    uv run python -m tools.auction_calibration --trials 200
    uv run python -m tools.auction_calibration --trials 200 --sweep

Reads the committed deck snapshot (A107), so it needs no database. Nothing here is
imported by the app.

What a good setting has to do, in order of importance:
  1. every team finishes with eighteen and a legal twelve -- 100%, not "nearly";
  2. the fill round is a net, almost never used;
  3. purses are spent (a team leaving ₹40 cr on the table is a bidder that is too timid);
  4. price follows quality (rank correlation of rating against price), with a spread --
     a handful of expensive stars, most players cheap.
"""

from __future__ import annotations

import argparse
import random
import statistics
import time
from collections import Counter

import game.auction as au
from etl.feasibility import order_errors
from tools import snapshot_deck


class Passive(au.Human):
    """Never bids; everything arrives through the fill round. The hardest test of the
    net, since a whole squad of eighteen has to come out of it."""


class Greedy(au.Human):
    """Bids everything it has on stars until it cannot. Tests the purse reserve rule."""

    def ceiling(self, auction, team, lot, round_no):
        return team.max_bid() if (lot.card.display or 0) >= 85 else 0


class Random(au.Human):
    """A careless human: interested in a random 40% of lots, at a random multiple."""

    def __init__(self, seed: int):
        self.rng = random.Random(seed)

    def ceiling(self, auction, team, lot, round_no):
        if self.rng.random() > 0.4:
            return 0
        return int(lot.base * self.rng.uniform(1, 12))


def _spearman(xs: list[float], ys: list[float]) -> float:
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0.0


def measure(deck, trials: int, seed: int, policy: str = "none", mega: bool = False) -> dict:
    stranded = illegal = fills = fill_auctions = unsold = 0
    spend, tops, medians, at_base, corr, spread, secs = [], [], [], [], [], [], []
    top_share, human_spend, human_legal = [], [], 0
    kept, rtm_used, rtm_matched, cards_left = [], 0, 0, []
    for t in range(trials):
        s = seed * 1_000_003 + t
        human = {"none": None, "passive": Passive(), "greedy": Greedy(),
                 "random": Random(s)}[policy]
        start = time.perf_counter()
        a = au.run_auction(deck, s, human_short="KKR" if human else None, human=human,
                           mega=mega)
        kept.extend(t.retained for t in a.teams)
        cards_left.extend(t.rtm for t in a.teams)
        events = [x.rtm for x in a.sales if x.rtm]
        rtm_used += len(events)
        rtm_matched += sum(e.matched for e in events)
        secs.append(time.perf_counter() - start)

        stranded += len(a.stranded)
        fills += a.fills
        fill_auctions += a.fills > 0
        unsold += sum(1 for lot in a.unsold)
        strengths = []
        for team in a.teams:
            spend.append((au.PURSE - team.purse) / au.PURSE)
            arranged = a.twelve(team)
            if arranged is None or len(team.squad) != au.SQUAD_SIZE \
                    or order_errors(arranged[0], arranged[1], team.squad):
                illegal += 1
                continue
            strengths.append(statistics.mean(c.display for c in arranged[0] + [arranged[1]]))
            if team.human:
                human_legal += 1
                human_spend.append((au.PURSE - team.purse) / au.PURSE)
        if strengths:
            spread.append(max(strengths) - min(strengths))

        sold = [x for x in a.sales if x.winner is not None and x.round != "fill"]
        prices = sorted((x.price for x in sold), reverse=True)
        tops.append(prices[0])
        medians.append(statistics.median(prices))
        at_base.append(sum(x.price == x.lot.base for x in sold) / len(sold))
        corr.append(_spearman([x.lot.card.display for x in sold], [x.price for x in sold]))
        top_share.append(sum(prices[:10]) / sum(prices))

    teams = trials * au.TEAMS
    return {
        "teams_stranded": f"{stranded}/{teams}",
        "illegal_twelves": f"{illegal}/{teams}",
        "fills_per_auction": round(fills / trials, 2),
        "auctions_with_fill": f"{fill_auctions}/{trials}",
        "unsold_per_auction": round(unsold / trials, 1),
        "spend_mean": f"{statistics.mean(spend):.0%}",
        "spend_p10": f"{sorted(spend)[len(spend) // 10]:.0%}",
        "top_price": au.crore(int(statistics.mean(tops))),
        "top_price_max": au.crore(max(tops)),
        "median_price": au.crore(int(statistics.mean(medians))),
        "sold_at_base": f"{statistics.mean(at_base):.0%}",
        "top10_share": f"{statistics.mean(top_share):.0%}",
        "rating_price_rho": round(statistics.mean(corr), 2),
        "strength_spread": round(statistics.mean(spread), 1),
        "ms_per_auction": round(1000 * statistics.mean(secs)),
        **({"retained_per_team": round(statistics.mean(kept), 2),
            "rtm_played_per_auction": round(rtm_used / trials, 1),
            "rtm_matched_share": f"{rtm_matched / max(1, rtm_used):.0%}",
            "rtm_cards_unused_per_team": round(statistics.mean(cards_left), 2)}
           if mega else {}),
        **({"human_legal": f"{human_legal}/{trials}",
            "human_spend": f"{statistics.mean(human_spend):.0%}" if human_spend else "-"}
           if policy != "none" else {}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trials", type=int, default=100)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--mega", action="store_true",
                        help="retentions and Right to Match (A138)")
    parser.add_argument("--sweep", action="store_true",
                        help="grid over VALUE_AT_70 x VALUE_GROWTH")
    args = parser.parse_args()
    deck = snapshot_deck.deck_from(snapshot_deck.read_document())

    if args.sweep:
        for a70 in (80, 110, 140, 180):
            for k in (0.08, 0.09, 0.10, 0.11):
                au.VALUE_AT_70, au.VALUE_GROWTH = a70, k
                m = measure(deck, args.trials, args.seed)
                print(f"A={a70:>3} K={k:.2f}  " + "  ".join(
                    f"{key}={m[key]}" for key in ("teams_stranded", "fills_per_auction",
                                                  "spend_mean", "spend_p10", "top_price",
                                                  "median_price", "rating_price_rho")))
        return

    for policy in ("none", "passive", "greedy", "random"):
        print(f"\n== human: {policy}")
        for key, value in measure(deck, args.trials, args.seed, policy, args.mega).items():
            print(f"  {key:<20} {value}")


if __name__ == "__main__":
    main()
