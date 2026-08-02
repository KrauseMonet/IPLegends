"""SPEC 1.1/1.2. Can a draft always complete against real coverage?

A39-A40 answered this for a slot template: keeper 1, opener 2, top_order 2,
middle_or_finisher 3, bowler 5, open 2. A72 replaced the template with something more
skill-driven -- fifteen picks from an open pool, no slot categories, arranged by the
drafter into a batting order and judged on a final twelve (eleven who bat, one Impact
Player) -- and eligibility for each numbered position is a career fact about the player,
not a category his card belongs to.

A73 simplified the mechanic once more: the drafter now picks a playing TWELVE directly,
one slot at a time, rather than fifteen followed by a separate arranging phase. There is
no bench and no rearranging once a pick is made -- final the moment it lands. That turns
out to simplify more than the UI: with exactly as many future picks as future open slots,
position-matching can never be the thing that fails a forward check (see
`could_still_complete`), so the whole bipartite-matching/wildcard-exclusion machinery
this module used to need is gone.

This module still does not check per-position counts on faith. It runs the draft, now
against an exact forward check rather than a slot count, and measures whether the
deal-time guarantee is still a net or has become a beam (A40's question, asked again).
"""

from __future__ import annotations

import argparse
import os
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import psycopg
from dotenv import load_dotenv

# [A72] Career-wide, but counted rather than enveloped. The obvious mechanism -- career
# MIN(batting_position_min)/MAX(batting_position_max), both already on `squad_members`
# since migration 004 -- needs no new ETL and was tried first. It does not hold up:
# measured against the archive, SV Samson comes out 1-8 and RK Singh (Rinku) comes out
# 3-8, both far looser than reputation, because a single outlier innings widens a MIN/MAX
# envelope forever. So eligibility counts instead: a position is real evidence about a
# player only if he has batted there in at least this many separate career innings.
# Declared by the user directly, not calibrated -- a game-design constant like A57's
# REPUTATION or A59's ALLROUNDER_RUNS, not a threshold swept against a list.
MIN_INNINGS_AT_POSITION = 5

# [A72, widened by A73] The band a player defaults into when his own measured evidence
# doesn't pin him to the top or middle of the order. Two populations land here: a player
# with NO qualifying position anywhere (zero batting evidence, or evidence too thin to
# concentrate anywhere), and a player whose evidence IS real but confined entirely to the
# lower order already (a specialist finisher, a bowler who has batted enough to qualify
# but only at 7/8) -- both are "a lower-order hitter," and binding either one to the
# narrow (9, 11) tail this band used to be was tighter than the user plays: a number 7/8
# finisher and a part-time bowler batting at 9 are the same kind of flexible lower-order
# resource in practice, not two different roles. "Below 6" per the user -- confirmed to
# exclude 6 itself, matching how the earlier Rinku Singh example worked out (measured
# eligible at 5, 6, 7 for "only below 4", i.e. strictly after 4, not from 4).
LOWER_ORDER_BAND = (7, 11)

XI_SIZE = 11
TWELVE_SIZE = XI_SIZE + 1                 # eleven who bat, one Impact Player
IMPACT_SLOT = XI_SIZE + 1                 # a pseudo-position every player is eligible for
KEEPERS_IN_TWELVE = 1

# [A50] Twenty overs at a maximum of four each is exactly five. Re-declared here (not
# imported from game.simulator) because etl must not import game; test_twelve.py pins
# BOWLERS_IN_TWELVE == game.simulator.OVERS // game.simulator.MAX_OVERS_PER_BOWLER so the
# two can never quietly drift apart.
BOWLERS_IN_TWELVE = 5

# [A61] Unchanged: capping the SQUAD at four known-overseas makes every twelve drawn from
# it legal by construction -- and now the squad IS the twelve (A73), so this cap is
# enforced directly on the final twelve rather than as a squad-level buffer over a spare.
OVERSEAS_CAP = 4

# The guarantee re-draws silently. It cannot re-draw forever, and a cap that is hit is
# itself the finding, so it is generous rather than tight.
REDRAW_CAP = 2_000

# A reroll is the opposite of the guarantee: the drafter REJECTING a deal that already has
# eligible candidates, not the deck failing to offer one. Budget is for the whole draft,
# not per pick (original SPEC 1.1), and enforcing the count is the session layer's job
# (whichever policy raises `RerollRequested` knows how many it has already spent) --
# `run_draft` only needs to know a reroll CAN happen and how to retry when it does.
REROLLS_ALLOWED = 3

# A reroll asks for a different deal, but "different" has two meanings a drafter cares
# about separately: an unrelated team entirely, or the same team a different year. Both
# spend the same budget; only the redraw POOL for the very next attempt differs.
REROLL_KINDS = ("team", "season")

ALL_SLOTS = frozenset(range(1, XI_SIZE + 1)) | {IMPACT_SLOT}


class RerollRequested(Exception):
    """Raised by a policy to reject the deal just offered and try a different
    franchise-season for the same pick, without spending it. Caught inside `run_draft`'s
    own attempt loop -- unlike `web.session`'s `_NeedChoice`, this never propagates out of
    a single pick, since the drafter is still mid-decision on the SAME pick, not paused
    waiting for a new one.

    `kind` is one of `REROLL_KINDS`: "team" draws from the whole deck, same pool an
    ordinary redraw uses; "season" restricts the very next draw to a franchise-season
    sharing the SAME franchise as the one just rejected, so the drafter sees a different
    year of the same team rather than an unrelated one. Defaults to "team" so existing
    callers that raise it bare (e.g. a test policy) keep their old behaviour.
    """

    def __init__(self, kind: str = "team"):
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class Card:
    """One draftable player-season: a person, in a franchise-season, and where he may bat.

    `bat`/`bowl` may be None -- not because a discipline fell below a floor (A65/A71 rate
    those too), but because the player never had that discipline at all this season.
    `career_positions` is the ALREADY-thresholded set of batting numbers this person has
    at least MIN_INNINGS_AT_POSITION career innings at (possibly empty); `positions`
    applies the one widening rule on top of it (A73), so the threshold and the widening
    each live in exactly one place.
    """

    fs_id: int
    person_id: str
    name: str
    bat: float | None = None
    bowl: float | None = None
    band: str | None = None
    role: str | None = None
    career_keeper: bool = False
    season_year: int | None = None
    franchise: str | None = None
    # NULL is a real and common value here, never False (A23): an unknown passport must
    # not read as an Indian one. A51 filled them all, so it is NULL for nobody today; the
    # type stays because a revised archive can reintroduce one.
    overseas: bool | None = None
    # A58/A60's integer 70-99, the card's face value.
    display: int | None = None
    # [A72] The qualifying positions, threshold already applied at load time. Empty
    # frozenset means "no position has enough evidence," not "no evidence at all" --
    # those two cases are deliberately not distinguished past this point.
    career_positions: frozenset[int] = frozenset()

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

    @property
    def positions(self) -> frozenset[int]:
        """Batting numbers this player may occupy.

        [A73] Two cases widen to the full LOWER_ORDER_BAND rather than staying pinned to
        an exact measured set: no qualifying position at all, or every qualifying
        position already lower-order. Any player with real evidence above the band (a
        genuine opener/top/middle-order showing) keeps his exact measured positions
        untouched -- the widening only ever loosens where the evidence itself is silent
        or already agrees it's a lower-order player, never where it says otherwise.
        """
        lo, hi = LOWER_ORDER_BAND
        if not self.career_positions or min(self.career_positions) >= lo:
            return frozenset(range(lo, hi + 1))
        return self.career_positions

    @property
    def slots(self) -> frozenset[int]:
        """`positions`, plus the Impact pseudo-slot every player may occupy."""
        return self.positions | {IMPACT_SLOT}


@dataclass
class Deck:
    cards_by_fs: dict[int, list[Card]]
    fs_ids: list[int]

    def position_supply(self) -> dict[int, int]:
        """Franchise-seasons offering at least one card eligible for each position 1-11."""
        supply: Counter[int] = Counter()
        for cards in self.cards_by_fs.values():
            here = {p for c in cards for p in c.positions}
            for p in here:
                supply[p] += 1
        return dict(supply)


def load_deck(conn) -> Deck:
    """Every rated player-season, and the career-wide positions it may bat at.

    A player-season with no rated discipline is not draftable at all (A33/A65/A71 rate
    everyone who faced or bowled a ball, or has a career elsewhere, or a shared floor --
    see player_season_rating). Position eligibility is a PERSON fact (A72), read from
    `person_batting_positions` and thresholded by MIN_INNINGS_AT_POSITION here, in the
    one place that threshold is applied.
    """
    rows = conn.execute(
        """
        select s.franchise_season_id, s.person_id, p.primary_name,
               s.role, s.batting_band, coalesce(p.is_keeper, false),
               f.season_year, f.display_name, p.is_overseas,
               max(r.rated_per_ball) filter (where r.discipline = 'batting') as bat,
               max(r.rated_per_ball) filter (where r.discipline = 'bowling') as bowl,
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

    positions_by_person: dict[str, set[int]] = defaultdict(set)
    for person_id, position, innings in conn.execute(
        "select person_id, position, innings from person_batting_positions"
    ):
        if innings >= MIN_INNINGS_AT_POSITION:
            positions_by_person[person_id].add(position)

    cards_by_fs: dict[int, list[Card]] = defaultdict(list)
    for (fs_id, person_id, name, role, band, career_keeper,
         season_year, franchise, overseas, bat, bowl, display) in rows:
        cards_by_fs[fs_id].append(
            Card(fs_id, person_id, name, bat, bowl, band, role, career_keeper,
                 season_year, franchise, overseas, display,
                 frozenset(positions_by_person.get(person_id, ())))
        )

    all_fs = [r[0] for r in conn.execute("select franchise_season_id from franchise_seasons")]
    return Deck(dict(cards_by_fs), sorted(all_fs))


# --- the legality algebra --------------------------------------------------------------
#
# [A73] The final twelve is no longer a "does some arrangement of a bigger squad work"
# problem -- the squad IS the twelve, and every pick commits to exactly one open slot the
# moment it is made. `order_errors` stays as the one independent, from-scratch verifier
# (never shares code with the incremental forward check below), which is what makes it
# worth keeping even though a correctly-run draft should never actually trip it.


def order_errors(order: list[Card | None], impact: Card | None,
                  squad: list[Card]) -> list[str]:
    """Every way a drafter's own arrangement could be illegal, named rather than just
    rejected. Empty means playable."""
    errors: list[str] = []
    placed = [c for c in order if c is not None]
    twelve = placed + ([impact] if impact is not None else [])

    seen: set[str] = set()
    for c in twelve:
        if c.person_id in seen:
            errors.append(f"{c.name} is placed twice")
        seen.add(c.person_id)
    squad_ids = {c.person_id for c in squad}
    for c in twelve:
        if c.person_id not in squad_ids:
            errors.append(f"{c.name} was not drafted")

    if len(placed) < XI_SIZE:
        errors.append(f"only {len(placed)} of {XI_SIZE} batting positions are filled")
    if impact is None:
        errors.append("no Impact Player chosen")

    for i, c in enumerate(order):
        if c is not None and (i + 1) not in c.positions:
            errors.append(f"{c.name} cannot bat at {i + 1}")

    if not any(c.role == "keeper" for c in twelve):
        errors.append("no wicketkeeper in the final twelve")

    bowl_count = sum(c.has_bowl for c in twelve)
    if bowl_count < BOWLERS_IN_TWELVE:
        errors.append(f"only {bowl_count} of {BOWLERS_IN_TWELVE} bowling options")

    overseas_count = sum(c.overseas is True for c in twelve)
    if overseas_count > OVERSEAS_CAP:
        errors.append(f"{overseas_count} overseas players, more than {OVERSEAS_CAP}")

    return errors


def could_still_complete(open_slots_after: frozenset[int], keeper_have: bool,
                          bowl_have: int, remaining_after: int) -> bool:
    """Whether a legal twelve is still reachable after a hypothetical pick, assuming the
    best case for every pick not yet made: eligible anywhere, bowls, keeps, domestic.

    [A73] `remaining_after` future picks will fill exactly `remaining_after` open slots --
    an invariant "final at pick time" guarantees, since every pick commits to one open
    slot and none is ever freed again. With as many maximally-flexible wildcard picks as
    open slots, and a wildcard eligible at every one of them, a perfect matching always
    exists (Hall's theorem on a fully-connected bipartite graph): position eligibility can
    therefore never be the thing that fails here. Only the keeper and bowler COUNTS can
    still be unreachable, which is exactly what this checks -- no matching, no wildcard
    objects, no combinatorial exclusion needed at all.
    """
    assert remaining_after == len(open_slots_after), (
        "remaining picks and open slots must stay in lockstep: every pick fills exactly "
        "one open slot and none is ever freed"
    )
    if remaining_after == 0:
        return keeper_have and bowl_have >= BOWLERS_IN_TWELVE
    return bowl_have + remaining_after >= BOWLERS_IN_TWELVE


# --- the drafter ------------------------------------------------------------------------


def eligible(cards, taken: set[str], open_slots: frozenset[int], keeper_have: bool,
             bowl_have: int, overseas_taken: int, remaining: int,
             cap: int | None = OVERSEAS_CAP):
    """Cards that may be taken right now: not already drafted, doesn't bust the overseas
    cap, has somewhere open to bat, and leaves a legal twelve still reachable through at
    least one of his open eligible slots."""
    full = cap is not None and overseas_taken >= cap
    for card in cards:
        if card.person_id in taken:
            continue
        if full and card.overseas is True:
            continue
        candidate_slots = card.slots & open_slots
        if not candidate_slots:
            continue
        new_keeper_have = keeper_have or card.role == "keeper"
        new_bowl_have = bowl_have + (1 if card.has_bowl else 0)
        remaining_after = remaining - 1
        if any(
            could_still_complete(open_slots - {slot}, new_keeper_have, new_bowl_have,
                                  remaining_after)
            for slot in candidate_slots
        ):
            yield card


def _franchise_of(deck: Deck, fs_id: int) -> str | None:
    """The franchise name a franchise-season belongs to, read off one of its own cards
    rather than stored separately on `Deck` -- every card dealt from the same fs shares
    one franchise by construction, so the first is as good as any. `None` for an fs with
    no cards at all (declared in `Deck.fs_ids` but absent from `cards_by_fs`, e.g. a
    season with nobody rated) -- never guessed at, per the standing rule against
    defaulting an unobserved value."""
    cards = deck.cards_by_fs.get(fs_id, ())
    return cards[0].franchise if cards else None


def choose_slot(card: Card, open_slots: frozenset[int]) -> int:
    """An automated policy's slot choice: the lowest-numbered open eligible batting
    position, so the choice is deterministic and legible. Impact only if that's the only
    open slot he has -- a card is never offered at all otherwise (see `eligible`)."""
    numbered = sorted(p for p in card.positions if p in open_slots)
    return numbered[0] if numbered else IMPACT_SLOT


@dataclass(frozen=True)
class DraftState:
    """Everything a policy might need beyond the list of candidates on offer: what's been
    picked so far (draft order), how it's arranged, and the running counts the forward
    check works from. Bundled into one object -- rather than several positional args --
    so a caller that needs to snapshot "the draft so far" (a paused human session, in
    `web/session.py`) can build it directly from what `run_draft` already had in scope,
    with no second replay needed to reconstruct it.
    """

    picks: tuple[Card, ...]
    order: tuple[Card | None, ...]
    impact: Card | None
    open_slots: frozenset[int]
    keeper_have: bool
    bowl_have: int
    remaining: int


def pick_rational(candidates: list[Card], state: DraftState,
                   rng: random.Random) -> tuple[Card, int]:
    """Reduce the most binding requirement first -- a keeper if none yet, bowling depth
    if short of five -- then rating. There is no slack to hoard any more (A73's atomic
    model has no spare picks), so this is simpler than its predecessor: just don't leave
    the hard requirements until luck has to supply them.
    """
    needs_bowler = state.bowl_have < BOWLERS_IN_TWELVE

    def key(c: Card):
        return (
            0 if (not state.keeper_have and c.role == "keeper") else 1,
            0 if (needs_bowler and c.has_bowl) else 1,
            -c.rating,
        )

    card = min(candidates, key=key)
    return card, choose_slot(card, state.open_slots)


def pick_naive(candidates: list[Card], state: DraftState,
               rng: random.Random) -> tuple[Card, int]:
    """Take the best-rated player, need be damned. With ratings visible this is what
    "draft the best available" means to most people."""
    card = max(candidates, key=lambda c: c.rating)
    return card, choose_slot(card, state.open_slots)


def pick_afk(candidates: list[Card], state: DraftState,
             rng: random.Random) -> tuple[Card, int]:
    """The room draft's timeout fallback: the LOWEST-rated eligible candidate, not the
    best -- a player who let the clock run out gets a pick made FOR them, not a gift.
    Symmetric to `pick_naive` (which takes `max`), never used as a real drafter policy."""
    card = min(candidates, key=lambda c: c.rating)
    return card, choose_slot(card, state.open_slots)


def pick_random(candidates: list[Card], state: DraftState,
                 rng: random.Random) -> tuple[Card, int]:
    """Draws from the SAME `rng` `run_draft` uses for dealing, never the global `random`
    module -- a policy that reached into ambient global state would make this policy's
    behaviour depend on whatever else in the process happened to touch `random` first,
    which is exactly the kind of thing A62's seed-determinism guarantee exists to rule
    out. (Found live: the pre-A73 code used `random.choice` here, which is why a fixed
    seed did not reproduce a fixed outcome for this policy -- fixed now, not merely
    inherited.)
    """
    card = rng.choice(candidates)
    slot = rng.choice(sorted(card.slots & state.open_slots))
    return card, slot


POLICIES = {"rational": pick_rational, "naive": pick_naive, "random": pick_random}


@dataclass
class Result:
    completed: bool
    stranded_on: tuple[str, ...] = ()
    redraws: list[int] = field(default_factory=list)
    fs_served: list[int] = field(default_factory=list)
    picks: list[Card] = field(default_factory=list)          # draft order
    order: list[Card | None] = field(default_factory=lambda: [None] * XI_SIZE)
    impact: Card | None = None
    player_rerolls: int = 0        # total RerollRequested catches across the whole draft


def run_draft(deck: Deck, policy, rng: random.Random, guarantee: bool = True,
              require_legal: bool = True, cap: int | None = OVERSEAS_CAP) -> Result:
    """With `guarantee` off, an unservable deal strands the drafter instead of
    re-drawing. With `require_legal` off, a pick is allowed even if it makes a legal
    twelve unreachable -- the ablation that shows how much work the forward check is
    doing, the same role A40's guarantee-off ablation played for the old template.

    [A73] Placement is atomic: every pick commits to exactly one open slot (a batting
    position or Impact) the instant it is made, so `order`/`impact` are built up directly
    as the loop runs rather than derived afterward from an over-full squad.

    The policy is now consulted on EVERY attempt that finds eligible candidates, not just
    the first one that has to loop past an ineligible deal -- because a policy may reject
    a perfectly servable deal by raising `RerollRequested` (a deliberate choice, unlike the
    guarantee's own silent redraw past a deal with nothing eligible on it). Both count as
    an "attempt" on the same `for` loop, but are tallied separately: `result.redraws`
    stays exactly what it always measured (empty-candidate attempts the guarantee had to
    skip past), and `result.player_rerolls` is new.

    A `RerollRequested(kind="season")` narrows the POOL for the very next draw to
    franchise-seasons sharing the rejected deal's own franchise, excluding the fs just
    rejected; `kind="team"` excludes just-rejected fs from the whole deck instead. Either
    way the narrowing applies to exactly one attempt -- `pool` resets to the full deck
    before every draw and is only overridden inside the `except` branch below, so a
    reroll never biases the guarantee's own later, unrelated redraws. If the narrowed
    pool is empty (a franchise with only one season on the archive, or a one-fs deck),
    it silently widens back to the full deck rather than raising -- a rare edge case, not
    a promise this module can always keep.
    """
    taken: set[str] = set()
    overseas_taken = 0
    open_slots: set[int] = set(ALL_SLOTS)
    keeper_have = False
    bowl_have = 0
    order: list[Card | None] = [None] * XI_SIZE
    impact: Card | None = None
    picks: list[Card] = []
    result = Result(completed=False)

    for pick_no in range(TWELVE_SIZE):
        remaining = TWELVE_SIZE - pick_no
        full = cap is not None and overseas_taken >= cap
        served = None
        empty_attempts = 0
        pool = deck.fs_ids
        for attempt in range(REDRAW_CAP if guarantee else 1):
            fs_id = rng.choice(pool)
            pool = deck.fs_ids
            cards = deck.cards_by_fs.get(fs_id, ())
            if require_legal:
                candidates = list(eligible(
                    cards, taken, frozenset(open_slots), keeper_have, bowl_have,
                    overseas_taken, remaining, cap,
                ))
            else:
                candidates = [
                    c for c in cards
                    if c.person_id not in taken and not (full and c.overseas is True)
                    and (c.slots & open_slots)
                ]
            if not candidates:
                empty_attempts += 1
                continue
            state = DraftState(
                picks=tuple(picks), order=tuple(order), impact=impact,
                open_slots=frozenset(open_slots), keeper_have=keeper_have,
                bowl_have=bowl_have, remaining=remaining,
            )
            try:
                card, slot = policy(candidates, state, rng)
            except RerollRequested as reroll:
                result.player_rerolls += 1
                if reroll.kind == "season":
                    franchise = _franchise_of(deck, fs_id)
                    narrowed = [f for f in deck.cards_by_fs
                                if f != fs_id and _franchise_of(deck, f) == franchise]
                else:
                    narrowed = [f for f in deck.fs_ids if f != fs_id]
                if narrowed:
                    pool = narrowed
                continue
            served = (fs_id, card, slot)
            break
        if served is None:
            # Preserve progress on the way out: a stranded Result should say how far the
            # draft actually got, not silently read as "stranded on the very first pick"
            # regardless of where it really happened.
            result.stranded_on = ("no_eligible_card",)
            result.picks = picks
            result.order = order
            result.impact = impact
            return result

        fs_id, card, slot = served
        assert slot in (card.slots & open_slots), (
            f"policy chose an unavailable slot {slot} for {card.name}"
        )
        taken.add(card.person_id)
        if card.overseas is True:
            overseas_taken += 1
        if card.role == "keeper":
            keeper_have = True
        if card.has_bowl:
            bowl_have += 1
        open_slots.discard(slot)
        if slot == IMPACT_SLOT:
            impact = card
        else:
            order[slot - 1] = card
        result.redraws.append(empty_attempts)
        result.fs_served.append(fs_id)
        picks.append(card)

    result.completed = True
    result.picks = picks
    result.order = order
    result.impact = impact
    return result


def simulate(deck: Deck, policy_name: str, trials: int, seed: int,
             guarantee: bool = True, require_legal: bool = True) -> list[Result]:
    rng = random.Random(seed)
    policy = POLICIES[policy_name]
    return [run_draft(deck, policy, rng, guarantee, require_legal) for _ in range(trials)]


def report(deck: Deck, trials: int, seed: int) -> None:
    supply = deck.position_supply()
    print(f"=== SPEC 1.1/1.2 draft feasibility: {TWELVE_SIZE} picks straight into the "
          f"final twelve, over {len(deck.fs_ids)} franchise-seasons ===\n")

    people = defaultdict(set)
    for cards in deck.cards_by_fs.values():
        for card in cards:
            for p in card.positions:
                people[p].add(card.person_id)

    print(f"    {'position':10} {'fs able':>8} {'distinct people':>16}")
    for pos in range(1, XI_SIZE + 1):
        n = supply.get(pos, 0)
        flag = "  <-- scarce" if n < len(deck.fs_ids) // 2 else ""
        print(f"      {pos:<10} {n:4} of {len(deck.fs_ids)}   {len(people[pos]):8}{flag}")

    keeper_people = {c.person_id for cards in deck.cards_by_fs.values()
                     for c in cards if c.role == "keeper"}
    bowler_people = {c.person_id for cards in deck.cards_by_fs.values()
                      for c in cards if c.has_bowl}
    print(f"\n    distinct people who can keep:  {len(keeper_people)}")
    print(f"    distinct people with a bowling rating:  {len(bowler_people)}")

    print(f"\n    {trials} drafts per policy, uniform deck, seed {seed}")
    print("    'guarantee on' is the draft as played; 'off' is the same draft with the\n"
          "    silent re-draw removed, which is how much work the guarantee is doing.\n"
          "    'legal off' additionally allows a pick even if it strands the final\n"
          "    twelve, showing what the forward check alone is buying. [A73] Completion\n"
          "    now implies legality by construction -- there is no separate 'stranded\n"
          "    after completing' case left to measure.\n")
    print(f"    {'policy':10} {'guarantee':>10} {'legal':>6} {'completed':>10} "
          f"{'failed':>8} {'fail rate':>10}   {'redraws':>8} {'worst':>6}")
    for name in ("rational", "naive", "random"):
        for guarantee in (True, False):
            for require_legal in (True, False):
                if not guarantee and not require_legal:
                    continue  # both off is just "any twelve", not a useful ablation
                results = simulate(deck, name, trials, seed, guarantee, require_legal)
                failed = [r for r in results if not r.completed]
                redraws = [sum(r.redraws) for r in results if r.completed]
                worst = max((max(r.redraws) for r in results if r.redraws), default=0)
                mean = sum(redraws) / len(redraws) if redraws else 0.0
                print(f"    {name:10} {'on' if guarantee else 'off':>10} "
                      f"{'on' if require_legal else 'off':>6} "
                      f"{len(results) - len(failed):10} {len(failed):8} "
                      f"{len(failed) / len(results):9.1%}   {mean:8.1f} {worst:6}")

    print(f"\n=== how often the guarantee fires (require_legal on, the net's real load) ===\n")
    print(f"    {'policy':10} {'drafts where it fired':>22} {'picks where it fired':>21} "
          f"{'worst single pick':>18}")
    for policy in ("rational", "naive", "random"):
        results = simulate(deck, policy, trials, seed, guarantee=True, require_legal=True)
        picks = [n for r in results for n in r.redraws]
        fired_draft = sum(any(n > 0 for n in r.redraws) for r in results)
        fired_pick = sum(n > 0 for n in picks)
        print(f"    {policy:10} {fired_draft / len(results):21.1%} "
              f"{fired_pick / len(picks):20.2%} {max(picks, default=0):18}")


def main() -> None:
    ap = argparse.ArgumentParser(description="SPEC 1.1/1.2 draft feasibility")
    ap.add_argument("--trials", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    load_dotenv()
    with psycopg.connect(os.environ["DIRECT_URL"]) as conn:
        deck = load_deck(conn)
    report(deck, args.trials, args.seed)


if __name__ == "__main__":
    main()
