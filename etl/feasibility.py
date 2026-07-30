"""SPEC 1.1. Can a draft always complete against real coverage?

The slot template was written before coverage was known. A39 measured coverage and found
`tail` empty, `finisher` rated in 43 of 166 franchise-seasons and `death` in 8 of 66, so
the deal-time guarantee's unstated assumption -- that every slot type is fillable from
enough of the deck that a draft can always finish -- is no longer safe to assume.

This module does not check per-slot counts. It runs the draft.
"""

from __future__ import annotations

import argparse
import os
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import psycopg
from dotenv import load_dotenv

# SPEC 1.1, with A8's interim collapse of pace 3 + spin 2 into a generic bowler 5.
TEMPLATE: dict[str, int] = {
    "keeper": 1,
    "opener": 2,
    "top_order": 2,
    "middle": 2,
    "finisher": 1,
    "bowler": 5,
    "open": 2,
}
SQUAD_SIZE = sum(TEMPLATE.values())

BAND_SLOTS = {"opener", "top_order", "middle", "finisher"}

# The guarantee re-draws silently. It cannot re-draw forever, and a cap that is hit is
# itself the finding, so it is generous rather than tight.
REDRAW_CAP = 2_000


@dataclass(frozen=True)
class Card:
    """One draftable player-season: a person, in a franchise-season, and what it can fill."""

    fs_id: int
    person_id: str
    name: str
    slots: frozenset[str]
    rating: float
    band: str | None = None
    role: str | None = None
    career_keeper: bool = False


@dataclass
class Deck:
    cards_by_fs: dict[int, list[Card]]
    fs_ids: list[int]

    def slot_supply(self) -> Counter:
        """Franchise-seasons offering at least one card for each slot."""
        supply = Counter()
        for fs, cards in self.cards_by_fs.items():
            for slot in {s for c in cards for s in c.slots}:
                supply[slot] += 1
        return supply


def load_deck(conn) -> Deck:
    """Every rated player-season, and the slots it is eligible for.

    A player-season with no rated discipline is not draftable at all (A33), so it is not
    a card. Batting-band slots need a *batting* rating; the bowler slot needs a *bowling*
    one. `open` takes anything draftable, which is what makes it the buffer.
    """
    rows = conn.execute(
        """
        select s.franchise_season_id, s.person_id, p.primary_name,
               s.role, s.batting_band, coalesce(p.is_keeper, false),
               max(r.shrunk_per_ball) filter (where r.discipline = 'batting')  as bat,
               max(r.shrunk_per_ball) filter (where r.discipline = 'bowling')  as bowl
          from squad_members s
          join people p on p.person_id = s.person_id
          join player_season_rating r
            on r.franchise_season_id = s.franchise_season_id
           and r.person_id = s.person_id
         group by 1, 2, 3, 4, 5, 6
        """
    ).fetchall()

    cards_by_fs: dict[int, list[Card]] = defaultdict(list)
    for fs_id, person_id, name, role, band, career_keeper, bat, bowl in rows:
        slots = {"open"}
        if role == "keeper":
            slots.add("keeper")
        if bat is not None and band in BAND_SLOTS:
            slots.add(band)
        if bowl is not None:
            slots.add("bowler")
        best = max(v for v in (bat, bowl) if v is not None)
        cards_by_fs[fs_id].append(
            Card(fs_id, person_id, name, frozenset(slots), best, band, role, career_keeper)
        )

    all_fs = [r[0] for r in conn.execute("select franchise_season_id from franchise_seasons")]
    return Deck(dict(cards_by_fs), sorted(all_fs))


# --- template variants ----------------------------------------------------------------
#
# Each variant re-labels which slots a card can fill and/or reshapes the template. They are
# the candidate repairs named in the A39 discussion, measured rather than argued about.

def variant_spec(card: Card) -> frozenset[str]:
    return card.slots


def variant_finisher_or_middle(card: Card) -> frozenset[str]:
    """A rated middle-order batter may fill the finisher slot.

    Bands 5-6 and 7-8 are adjacent and the distinction is partly opportunity, so this is
    the mildest repair that touches only the slot that strands.
    """
    if "middle" in card.slots:
        return card.slots | {"finisher"}
    return card.slots


def variant_keeper_hand_filled(card: Card) -> frozenset[str]:
    """Upper bound on what finishing `keepers_by_season.csv` could buy the keeper slot.

    Uses the career keeper flag, which A24 showed is wrong for a per-season question, so
    this is deliberately optimistic. If the slot still strands here it cannot be fixed by
    the hand-fill.
    """
    if card.career_keeper:
        return card.slots | {"keeper"}
    return card.slots


def variant_both(card: Card) -> frozenset[str]:
    return variant_finisher_or_middle(card) | variant_keeper_hand_filled(card)


VARIANTS = {
    "spec": (variant_spec, TEMPLATE),
    "finisher<-middle": (variant_finisher_or_middle, TEMPLATE),
    "keeper hand-filled": (variant_keeper_hand_filled, TEMPLATE),
    "both": (variant_both, TEMPLATE),
    "finisher->open": (variant_spec, {**TEMPLATE, "finisher": 0, "open": 3}),
}


def apply_variant(deck: Deck, relabel) -> Deck:
    return Deck(
        {
            fs: [Card(c.fs_id, c.person_id, c.name, relabel(c), c.rating,
                      c.band, c.role, c.career_keeper)
                 for c in cards]
            for fs, cards in deck.cards_by_fs.items()
        },
        deck.fs_ids,
    )


# --- the drafter ---------------------------------------------------------------------

def eligible_pairs(cards, unfilled: dict[str, int], taken: set[str]):
    for card in cards:
        if card.person_id in taken:
            continue
        for slot in card.slots:
            if unfilled.get(slot, 0) > 0:
                yield card, slot


def pick_rational(pairs, scarcity: dict[str, int]):
    """Fill the globally scarcest slot this card can fill, and hoard `open` to the end.

    A drafter who spends an open slot on a player who had a specific slot available has
    thrown away the buffer, which is the single easiest way to strand yourself. This is
    the drafter the deal-time guarantee is entitled to assume.
    """
    return min(pairs, key=lambda p: (scarcity.get(p[1], 0), -p[0].rating))


def pick_naive(pairs, scarcity: dict[str, int]):
    """Take the best player on the card and put them wherever they fit, open included.

    Not a straw man: with ratings visible this is what "draft the best available" means,
    and the memory board mode hides the ratings that would tell you otherwise.
    """
    return max(pairs, key=lambda p: (p[0].rating, p[1] == "open"))


def pick_random(pairs, scarcity: dict[str, int]):
    return random.choice(list(pairs))


POLICIES = {"rational": pick_rational, "naive": pick_naive, "random": pick_random}


@dataclass
class Result:
    completed: bool
    stranded_on: tuple[str, ...] = ()
    redraws: list[int] = field(default_factory=list)
    fs_served: list[int] = field(default_factory=list)


def run_draft(deck: Deck, policy, rng: random.Random, guarantee: bool = True,
              template: dict[str, int] | None = None) -> Result:
    """With `guarantee` off, an unservable deal strands the drafter instead of re-drawing.

    That ablation is the point of the exercise: a template that only completes because the
    guarantee rescues it has moved the guarantee from safety net to load-bearing beam, and
    the SPEC calls it a safety net.
    """
    unfilled = {s: n for s, n in (template or TEMPLATE).items() if n > 0}
    taken: set[str] = set()
    result = Result(completed=False)

    for _ in range(sum(unfilled.values())):
        # Global scarcity, recomputed each pick: how many franchise-seasons could still
        # serve each unfilled slot given who is already drafted.
        scarcity = {
            slot: sum(
                any(slot in c.slots and c.person_id not in taken for c in cards)
                for cards in deck.cards_by_fs.values()
            )
            for slot, n in unfilled.items()
            if n > 0
        }

        served = None
        for attempt in range(REDRAW_CAP if guarantee else 1):
            fs_id = rng.choice(deck.fs_ids)
            pairs = list(eligible_pairs(deck.cards_by_fs.get(fs_id, ()), unfilled, taken))
            if pairs:
                served = (fs_id, pairs, attempt)
                break
        if served is None:
            result.stranded_on = tuple(sorted(s for s, n in unfilled.items() if n > 0))
            return result

        fs_id, pairs, attempt = served
        card, slot = policy(pairs, scarcity)
        taken.add(card.person_id)
        unfilled[slot] -= 1
        result.redraws.append(attempt)
        result.fs_served.append(fs_id)

    result.completed = True
    return result


def simulate(deck: Deck, policy_name: str, trials: int, seed: int,
             guarantee: bool = True, template: dict[str, int] | None = None) -> list[Result]:
    rng = random.Random(seed)
    policy = POLICIES[policy_name]
    return [run_draft(deck, policy, rng, guarantee, template) for _ in range(trials)]


def report(deck: Deck, trials: int, seed: int) -> None:
    supply = deck.slot_supply()
    print(f"=== SPEC 1.1 draft feasibility: {SQUAD_SIZE} slots over "
          f"{len(deck.fs_ids)} franchise-seasons ===\n")
    # Dedupe is on person_id (SPEC 1.1), so the deck's real depth for a slot is the number
    # of distinct PEOPLE who can fill it, not the number of cards or franchise-seasons.
    people = defaultdict(set)
    for cards in deck.cards_by_fs.values():
        for card in cards:
            for slot in card.slots:
                people[slot].add(card.person_id)

    print(f"    {'slot':12} {'need':>4} {'fs able':>8} {'distinct people':>16}")
    for slot in TEMPLATE:
        n = supply.get(slot, 0)
        flag = "  <-- scarce" if n < len(deck.fs_ids) // 2 else ""
        print(f"      {slot:12} x{TEMPLATE[slot]}   {n:4} of {len(deck.fs_ids)}"
              f"   {len(people[slot]):8}{flag}")

    print(f"\n    {trials} drafts per policy, uniform deck, seed {seed}")
    print("    'guarantee on' is SPEC 1.1 as written; 'off' is the same draft with the\n"
          "    silent re-draw removed, which is how much work the guarantee is doing.\n")
    print(f"    {'policy':10} {'guarantee':>10} {'completed':>10} {'failed':>8} "
          f"{'fail rate':>10}   {'redraws':>8} {'worst':>6}   stranded on")
    for name in ("rational", "naive", "random"):
        for guarantee in (True, False):
            results = simulate(deck, name, trials, seed, guarantee)
            failed = [r for r in results if not r.completed]
            redraws = [sum(r.redraws) for r in results if r.completed]
            worst = max((max(r.redraws) for r in results if r.redraws), default=0)
            strand = Counter(r.stranded_on for r in failed)
            summary = ", ".join(f"{'+'.join(k)} x{v}"
                                for k, v in strand.most_common(3)) or "-"
            mean = sum(redraws) / len(redraws) if redraws else 0.0
            print(f"    {name:10} {'on' if guarantee else 'off':>10} "
                  f"{len(results) - len(failed):10} {len(failed):8} "
                  f"{len(failed) / len(results):9.1%}   {mean:8.1f} {worst:6}   {summary}")

    # The scarce slots, named. A slot the whole 19-year archive can fill from 29 people is
    # a slot whose pick is nearly predetermined however well the ratings behind it work.
    for slot in ("finisher", "keeper"):
        names = sorted({(c.name, c.person_id) for cards in deck.cards_by_fs.values()
                        for c in cards if slot in c.slots})
        print(f"\n    every person who can fill '{slot}' in nineteen seasons ({len(names)})")
        for i in range(0, len(names), 4):
            print("      " + "  ".join(f"{n:22}" for n, _ in names[i:i + 4]))

    print(f"\n=== template variants, {trials} drafts each, guarantee OFF ===")
    print("    guarantee-off is the honest measure: it asks whether the template is\n"
          "    feasible on its own rather than whether the safety net can rescue it.\n")
    print(f"    {'variant':20} {'rational':>10} {'naive':>10} {'random':>10}   strands on")
    for label, (relabel, template) in VARIANTS.items():
        v_deck = apply_variant(deck, relabel)
        rates, strands = [], Counter()
        for name in ("rational", "naive", "random"):
            results = simulate(v_deck, name, trials, seed, guarantee=False, template=template)
            failed = [r for r in results if not r.completed]
            rates.append(len(failed) / len(results))
            strands.update(s for r in failed for s in r.stranded_on)
        top = ", ".join(f"{s} x{n}" for s, n in strands.most_common(2)) or "-"
        print(f"    {label:20} {rates[0]:9.1%} {rates[1]:9.1%} {rates[2]:9.1%}   {top}")

    # Where the re-draws land is the finding the completion rate hides.
    print("\n    re-draws burned by the deal-time guarantee, by pick number (rational)")
    results = simulate(deck, "rational", trials, seed)
    by_pick = defaultdict(list)
    for r in results:
        for i, n in enumerate(r.redraws, start=1):
            by_pick[i].append(n)
    print(f"      {'pick':>4} {'mean':>8} {'p95':>8} {'max':>8}   "
          f"distinct franchise-seasons actually served")
    for pick in range(1, SQUAD_SIZE + 1):
        vals = sorted(by_pick.get(pick, [0]))
        p95 = vals[int(len(vals) * 0.95) - 1] if vals else 0
        distinct = len({r.fs_served[pick - 1] for r in results if len(r.fs_served) >= pick})
        print(f"      {pick:4} {sum(vals) / len(vals):8.1f} {p95:8} {max(vals):8}   {distinct:4}")


def main() -> None:
    ap = argparse.ArgumentParser(description="SPEC 1.1 draft feasibility")
    ap.add_argument("--trials", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    load_dotenv()
    with psycopg.connect(os.environ["DIRECT_URL"]) as conn:
        deck = load_deck(conn)
    report(deck, args.trials, args.seed)


if __name__ == "__main__":
    main()
