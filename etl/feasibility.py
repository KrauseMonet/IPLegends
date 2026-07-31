"""SPEC 1.1. Can a draft always complete against real coverage?

The slot template was written before coverage was known. A39 measured coverage and found
`tail` empty, `finisher` rated in 43 of 166 franchise-seasons and `death` in 8 of 66, so
the deal-time guarantee's unstated assumption -- that every slot type is fillable from
enough of the deck that a draft can always finish -- is no longer safe to assume.

This module does not check per-slot counts. It runs the draft.

A40 resolved the template against what it found here: `middle` and `finisher` are one band
covering positions 5-8, because the separate finisher slot mandated a scarcity the deal-time
guarantee then had to enforce by steering every drafter toward the same 29 people, which is
the opposite of what uniform sampling is for.
"""

from __future__ import annotations

import argparse
import os
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace

import psycopg
from dotenv import load_dotenv

# SPEC 1.1, with A8's interim collapse of pace 3 + spin 2 into a generic bowler 5, and
# A40's merge of middle and finisher into one band. The merge is a widening, not a
# reshuffle: the three slots that were 2 middle + 1 finisher are now 3 drawn from the union,
# so it cannot be tighter than what it replaced.
TEMPLATE: dict[str, int] = {
    "keeper": 1,
    "opener": 2,
    "top_order": 2,
    "middle_or_finisher": 3,
    "bowler": 5,
    "open": 2,
}
SQUAD_SIZE = sum(TEMPLATE.values())

# SPEC 6.4 band -> the slot it fills. `tail` is absent by design (A39): a tail batter has no
# rateable batting season and is drafted for bowling.
BAND_SLOT: dict[str, str] = {
    "opener": "opener",
    "top_order": "top_order",
    "middle": "middle_or_finisher",
    "finisher": "middle_or_finisher",
}

# Kept only so the A40 decision stays auditable: this is the template the merge replaced.
PRE_MERGE_TEMPLATE: dict[str, int] = {
    "keeper": 1, "opener": 2, "top_order": 2, "middle": 2, "finisher": 1,
    "bowler": 5, "open": 2,
}
PRE_MERGE_BAND_SLOT = {b: b for b in ("opener", "top_order", "middle", "finisher")}

# The guarantee re-draws silently. It cannot re-draw forever, and a cap that is hit is
# itself the finding, so it is generous rather than tight.
REDRAW_CAP = 2_000


@dataclass(frozen=True)
class Card:
    """One draftable player-season: a person, in a franchise-season, and what it can fill.

    `bat` and `bowl` are the two normalised per-ball ratings, either of which may be None
    when that discipline did not clear A33's floor. They are carried rather than a pair of
    has-a-rating booleans because the simulator needs the numbers themselves, and a boolean
    beside the number it was derived from is two copies of one fact (A19).
    """

    fs_id: int
    person_id: str
    name: str
    slots: frozenset[str]
    bat: float | None = None
    bowl: float | None = None
    band: str | None = None
    role: str | None = None
    career_keeper: bool = False
    season_year: int | None = None
    franchise: str | None = None
    # NULL is a real and common value here, never False (A23): nationality is unfilled for
    # 314 people, and an unknown passport must not read as an Indian one. A51 filled them
    # all, so it is NULL for nobody today; the type stays because a revised archive can
    # reintroduce one and A49/A61 both depend on being able to tell unknown from domestic.
    overseas: bool | None = None
    # A58/A60's integer 70-99. The card's face value, carried so the API can show the
    # drafter the number the rating actually is, rather than a per-ball figure nobody reads.
    display: int | None = None

    @property
    def has_bat(self) -> bool:
        return self.bat is not None

    @property
    def has_bowl(self) -> bool:
        return self.bowl is not None

    @property
    def rating(self) -> float:
        """What a drafter ranks on: the better of the two disciplines."""
        return max(v for v in (self.bat, self.bowl) if v is not None)


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
               f.season_year, f.display_name, p.is_overseas,
               -- `rated_per_ball`, which is the number the CARD shows and the engine
               -- plays (A57). Not `normalised_per_ball`, which is the pre-blend value and
               -- would make the drafter rank on one scale and the match play out on
               -- another; and not `shrunk_per_ball`, which carries the era drift 7.4
               -- removes and is a season bias dressed up as a preference.
               max(r.rated_per_ball) filter (where r.discipline = 'batting') as bat,
               max(r.rated_per_ball) filter (where r.discipline = 'bowling') as bowl,
               -- Identical on both discipline rows by construction: the card is the
               -- player-season, not the discipline (A55).
               max(r.display_rating) as display
          from squad_members s
          join people p on p.person_id = s.person_id
          join franchise_seasons f on f.franchise_season_id = s.franchise_season_id
          join player_season_rating r
            on r.franchise_season_id = s.franchise_season_id
           and r.person_id = s.person_id
         group by 1, 2, 3, 4, 5, 6, 7, 8, 9
        """
    ).fetchall()

    cards_by_fs: dict[int, list[Card]] = defaultdict(list)
    for (fs_id, person_id, name, role, band, career_keeper,
         season_year, franchise, overseas, bat, bowl, display) in rows:
        cards_by_fs[fs_id].append(
            Card(fs_id, person_id, name, frozenset(), bat, bowl, band, role,
                 career_keeper, season_year, franchise, overseas, display)
        )

    all_fs = [r[0] for r in conn.execute("select franchise_season_id from franchise_seasons")]
    return label(Deck(dict(cards_by_fs), sorted(all_fs)))


def label(deck: Deck, band_slot: dict[str, str] | None = None,
          keeper_hand_filled: bool = False) -> Deck:
    """Attach eligible slots to every card under a given band mapping.

    Separated from loading so a template change is a re-label rather than a re-query, and
    so the pre-merge template can still be evaluated against the same deck.
    """
    band_slot = BAND_SLOT if band_slot is None else band_slot

    def slots_for(card: Card) -> frozenset[str]:
        slots = {"open"}
        if card.role == "keeper" or (keeper_hand_filled and card.career_keeper):
            slots.add("keeper")
        if card.has_bat and card.band in band_slot:
            slots.add(band_slot[card.band])
        if card.has_bowl:
            slots.add("bowler")
        return frozenset(slots)

    return Deck(
        {fs: [replace(c, slots=slots_for(c)) for c in cards]
         for fs, cards in deck.cards_by_fs.items()},
        deck.fs_ids,
    )


# --- template variants ----------------------------------------------------------------
#
# The ratified template is the first row. The rest exist so A40 stays auditable: the merge
# was chosen against these numbers, and a future reader can re-run them rather than take the
# decision on trust. `keeper_hand_filled` is an optimistic upper bound on the CSV (A24 showed
# the career flag is wrong for a per-season question), so it brackets rather than predicts.

VARIANTS: dict[str, tuple[dict[str, str], bool, dict[str, int]]] = {
    "A40 ratified":            (BAND_SLOT,           False, TEMPLATE),
    "A40 + keeper CSV":        (BAND_SLOT,           True,  TEMPLATE),
    "pre-merge (superseded)":  (PRE_MERGE_BAND_SLOT, False, PRE_MERGE_TEMPLATE),
    "pre-merge + keeper CSV":  (PRE_MERGE_BAND_SLOT, True,  PRE_MERGE_TEMPLATE),
}


# --- the drafter ---------------------------------------------------------------------

# [A61] The four-overseas rule applied at DEAL time rather than only at XI time.
#
# The IPL caps the playing XI at four overseas players, and §1.1's template said nothing
# about nationality, so the deck could hand a drafter fifteen cards that cannot produce a
# legal eleven. Measured over 400 drafted XIs: 87 of them, better than one in five.
#
# Capping the SQUAD at four is stricter than the rule it enforces - a real franchise
# carries more overseas players than it fields - and it is chosen anyway because it makes
# the guarantee unconditional: with four or fewer in a squad of fifteen, EVERY eleven drawn
# from it is legal, so no XI-selection path has to be trusted to find the legal one. A
# higher cap would need the selector to prove a legal eleven exists in every case, which is
# the sequence-property mistake A40 already made once by counting slots.
OVERSEAS_CAP = 4


def eligible_pairs(cards, unfilled: dict[str, int], taken: set[str],
                   overseas_taken: int = 0, cap: int | None = OVERSEAS_CAP):
    """Cards that can still fill an unfilled slot, honouring the overseas cap.

    `is_overseas` is NULL for nobody as of A51, so the cap counts a known quantity. Were
    unknowns to return - a revised archive adds a player nobody has resolved - `is True`
    keeps them draftable rather than silently spending an overseas place on a guess, which
    is A23's rule and A49's, applied at the third and last place the count is taken.
    """
    full = cap is not None and overseas_taken >= cap
    for card in cards:
        if card.person_id in taken:
            continue
        if full and card.overseas is True:
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
    # The squad itself, in pick order. Feasibility only ever counted completions, but the
    # game has to hand the drafted fifteen to a match, and re-deriving them from `fs_served`
    # would mean re-running the policy and hoping it landed the same way.
    picks: list[tuple[Card, str]] = field(default_factory=list)


def run_draft(deck: Deck, policy, rng: random.Random, guarantee: bool = True,
              template: dict[str, int] | None = None,
              cap: int | None = OVERSEAS_CAP) -> Result:
    """With `guarantee` off, an unservable deal strands the drafter instead of re-drawing.

    That ablation is the point of the exercise: a template that only completes because the
    guarantee rescues it has moved the guarantee from safety net to load-bearing beam, and
    the SPEC calls it a safety net.
    """
    unfilled = {s: n for s, n in (template or TEMPLATE).items() if n > 0}
    taken: set[str] = set()
    overseas_taken = 0
    result = Result(completed=False)

    for _ in range(sum(unfilled.values())):
        # Global scarcity, recomputed each pick: how many franchise-seasons could still
        # serve each unfilled slot given who is already drafted.
        full = cap is not None and overseas_taken >= cap
        scarcity = {
            slot: sum(
                any(slot in c.slots and c.person_id not in taken
                    and not (full and c.overseas is True) for c in cards)
                for cards in deck.cards_by_fs.values()
            )
            for slot, n in unfilled.items()
            if n > 0
        }

        served = None
        for attempt in range(REDRAW_CAP if guarantee else 1):
            fs_id = rng.choice(deck.fs_ids)
            pairs = list(eligible_pairs(deck.cards_by_fs.get(fs_id, ()), unfilled, taken,
                                        overseas_taken, cap))
            if pairs:
                served = (fs_id, pairs, attempt)
                break
        if served is None:
            result.stranded_on = tuple(sorted(s for s, n in unfilled.items() if n > 0))
            return result

        fs_id, pairs, attempt = served
        card, slot = policy(pairs, scarcity)
        taken.add(card.person_id)
        if card.overseas is True:
            overseas_taken += 1
        unfilled[slot] -= 1
        result.redraws.append(attempt)
        result.fs_served.append(fs_id)
        result.picks.append((card, slot))

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

    # The slot that used to strand, named. Kept in the report after the merge resolved it,
    # because the merge is only defensible if you can see what it widened.
    for slot in ("middle_or_finisher", "keeper"):
        names = sorted({c.name for cards in deck.cards_by_fs.values()
                        for c in cards if slot in c.slots})
        print(f"\n    distinct people who can fill '{slot}' in nineteen seasons: {len(names)}")
        if len(names) <= 40:
            for i in range(0, len(names), 4):
                print("      " + "  ".join(f"{n:22}" for n in names[i:i + 4]))

    print(f"\n=== template variants, {trials} drafts each, guarantee OFF ===")
    print("    guarantee-off is the honest measure: it asks whether the template is\n"
          "    feasible on its own rather than whether the safety net can rescue it.\n")
    print(f"    {'variant':24} {'rational':>9} {'naive':>9} {'random':>9}   strands on")
    for name, (bands, keeper_csv, template) in VARIANTS.items():
        v_deck = label(deck, bands, keeper_csv)
        rates, strands = [], Counter()
        for policy in ("rational", "naive", "random"):
            results = simulate(v_deck, policy, trials, seed, guarantee=False,
                               template=template)
            failed = [r for r in results if not r.completed]
            rates.append(len(failed) / len(results))
            strands.update(s for r in failed for s in r.stranded_on)
        top = ", ".join(f"{s} x{n}" for s, n in strands.most_common(2)) or "-"
        print(f"    {name:24} {rates[0]:8.1%} {rates[1]:8.1%} {rates[2]:8.1%}   {top}")

    # SPEC 1.1 requires the guarantee to log every time it fires, so that real play can be
    # compared against this prediction. These are the numbers that log should reproduce.
    print(f"\n=== how often the guarantee fires (the net's real load) ===\n")
    print(f"    {'policy':10} {'drafts where it fired':>22} {'picks where it fired':>21} "
          f"{'worst single pick':>18}")
    for policy in ("rational", "naive", "random"):
        results = simulate(deck, policy, trials, seed, guarantee=True)
        picks = [n for r in results for n in r.redraws]
        fired_draft = sum(any(n > 0 for n in r.redraws) for r in results)
        fired_pick = sum(n > 0 for n in picks)
        print(f"    {policy:10} {fired_draft / len(results):21.1%} "
              f"{fired_pick / len(picks):20.2%} {max(picks, default=0):18}")


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
