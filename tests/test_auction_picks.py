"""[A280] tools/auction_picks: a legal twelve, two backups a buy, a walk-away price.

Rooms here are built from the real auction engine and the committed deck (no database, no
network), so catalogues, purses and prices are genuine. The search is checked against
exhaustive enumeration over the same inputs, which is the only way a hill-climb's claim to
be best is worth anything."""

from __future__ import annotations

import itertools
import random

import pytest

import game.auction as au
from tests.auction_picks_fixture import mid_auction
from tools import auction_picks as ap
from tools import snapshot_deck


@pytest.fixture(scope="module")
def deck():
    return snapshot_deck.deck_from(snapshot_deck.read_document())


def _room(deck, seed=7, n=30, mega=True):
    return mid_auction(deck, seed, n, mega)


def _plan(deck, seed=7, n=30, mega=True):
    room, sets, a, kept = _room(deck, seed, n, mega)
    return ap.plan_room(room, sets, deck, "KKR", kept), room, sets, kept


# --- the plan itself ----------------------------------------------------------------------


def test_the_twelve_is_legal_and_holds_every_retention(deck):
    plan, room, sets, kept = _plan(deck)
    assert len(plan.retained) == len(kept) == 3
    assert len(plan.twelve) == 12 - plan.holes
    assert au.twelve_feasible(plan.twelve, plan.holes)
    assert {c.person_id for c in plan.retained} <= {c.person_id for c in plan.twelve}


def test_a_retention_stays_in_the_twelve_even_when_he_is_the_weakest_card_there(deck):
    """Retentions are paid for and the user said the twelve is built around them, so one is
    never dropped for being poor value. The weak man is the lowest-valued domestic card in
    the deck, which an unforced search would leave out at once."""
    room, sets, a, kept = _room(deck)
    cards = [c for cs in deck.cards_by_fs.values() for c in cs
             if c.overseas is False and c.person_id not in {k.person_id for k in kept}]
    vals = ap.values_of(cards)
    weak = min(cards, key=lambda c: vals[ap.card_key(c)].bat + (vals[ap.card_key(c)].bowl or 0))
    assert vals[ap.card_key(weak)].bat < 0
    mine = next(t for t in room["auction"]["teams"] if t["short"] == "KKR")
    mine["players"] += 1
    mine["purse"] -= 1100
    plan = ap.plan_room(room, sets, deck, "KKR", kept + [weak])
    assert weak.person_id in {c.person_id for c in plan.twelve}


def test_the_buys_fit_the_purse_the_places_and_the_overseas_cap(deck):
    for seed in (7, 8, 9):
        plan, room, *_ = _plan(deck, seed)
        mine = next(t for t in room["auction"]["teams"] if t["short"] == "KKR")
        assert len(plan.targets) <= plan.open_places
        assert sum(t.cand.price - ap.RESERVE for t in plan.targets) <= plan.budget
        assert plan.budget == mine["purse"] - ap.RESERVE * plan.open_places
        overseas = mine["overseas"] + sum(t.cand.card.overseas is True for t in plan.targets)
        assert overseas <= au.SQUAD_OVERSEAS_CAP


def test_targets_are_lots_still_to_come_in_lot_order_and_never_unsold_ones(deck):
    plan, *_ = _plan(deck)
    lots = [t.cand.lot for t in plan.targets]
    assert lots == sorted(lots)
    assert all(t.cand.status in ("upcoming", "current") for t in plan.targets)


def test_backups_come_after_their_target_fit_the_twelve_and_are_used_once(deck):
    plan, *_ = _plan(deck)
    seen = set()
    chosen = {t.cand.key for t in plan.targets}
    for t in plan.targets:
        assert len(t.backups) <= 2
        for b in t.backups:
            assert b.cand.lot > t.cand.lot or b.cand.status == "unsold"
            assert b.cand.key not in chosen and b.cand.key not in seen
            seen.add(b.cand.key)
            swapped = [c for c in plan.twelve if c.person_id != t.cand.card.person_id] + [b.cand.card]
            assert au.twelve_feasible(swapped, plan.holes)
        assert [b.value_after for b in t.backups] == sorted(
            (b.value_after for b in t.backups), reverse=True)


def test_walk_away_is_a_ladder_price_between_the_estimate_and_the_most_you_may_bid(deck):
    plan, room, *_ = _plan(deck)
    mine = next(t for t in room["auction"]["teams"] if t["short"] == "KKR")
    most = mine["purse"] - ap.RESERVE * (plan.open_places - 1)
    for t in plan.targets:
        assert t.cand.price <= t.walk_away <= most
        assert t.walk_away == ap._snap(t.walk_away)


def test_walk_away_never_falls_below_the_estimate_when_money_is_tight(deck):
    room, sets, a, kept = _room(deck, 8)
    mine = next(t for t in room["auction"]["teams"] if t["short"] == "KKR")
    mine["purse"] = 2600                                       # tight enough to be binding
    plan = ap.plan_room(room, sets, deck, "KKR", kept)
    assert plan.lam > 1e-6, "the budget must bind for this to test the priced walk-away"
    assert all(t.walk_away >= t.cand.price for t in plan.targets)
    assert any(t.walk_away > t.cand.price for t in plan.targets)


def test_spare_money_is_shared_out_not_promised_to_every_buy(deck):
    room, sets, a, kept = _room(deck, 8)
    next(t for t in room["auction"]["teams"] if t["short"] == "KKR")["purse"] = 9000
    plan = ap.plan_room(room, sets, deck, "KKR", kept)
    assert plan.lam <= 1e-6 and plan.spare > 0, "this needs a plan with money to spare"
    over = sum(t.walk_away - t.cand.price for t in plan.targets)
    rounding = sum(au.next_price(t.walk_away) - t.walk_away for t in plan.targets)   # one rung each
    assert over <= plan.spare + rounding
    assert plan.spare > 4 * rounding, "the spare must dwarf the rounding or this proves nothing"


def test_a_full_overseas_squad_buys_no_overseas_player(deck):
    room, sets, a, kept = _room(deck, 8)
    mine = next(t for t in room["auction"]["teams"] if t["short"] == "KKR")
    mine["purse"] = 4465
    free_plan = ap.plan_room(room, sets, deck, "KKR", kept)
    assert any(t.cand.card.overseas is True for t in free_plan.targets)   # the cap is what changes it
    mine["overseas"] = au.SQUAD_OVERSEAS_CAP
    plan = ap.plan_room(room, sets, deck, "KKR", kept)
    assert not any(t.cand.card.overseas is True for t in plan.targets)


def test_the_same_room_gives_the_same_plan(deck):
    a, *_ = _plan(deck)
    b, *_ = _plan(deck)
    assert [(t.cand.lot, t.walk_away) for t in a.targets] == [(t.cand.lot, t.walk_away) for t in b.targets]
    assert a.value == b.value


# --- the search against exhaustive enumeration --------------------------------------------


def _exhaustive(i: ap.Inputs) -> float:
    """The best twelve over EVERY subset of the movable pool, by the same rules and value."""
    size = 12 - i.holes
    free = i.owned + i.retained
    best = float("-inf")
    forced = {c.person_id for c in i.retained}
    by_card = {c.key: c for c in i.movable}
    for k in range(0, size - len(i.retained) + 1):
        for combo in itertools.combinations(i.movable, k):
            spend = sum(c.price - ap.RESERVE for c in combo)
            ov = sum(c.card.overseas is True for c in combo)
            if spend > i.budget or len(combo) > i.open_places or ov > i.ov_room:
                continue
            rest = size - len(i.retained) - k
            for extra in itertools.combinations(i.owned, rest):
                cards = list(i.retained) + list(extra) + [c.card for c in combo]
                if {c.person_id for c in cards} >= forced and au.twelve_feasible(cards, i.holes):
                    best = max(best, ap.twelve_value(cards, i.vals))
    return best


# (room seed, pool sample). Seed 8's samples are the ones where picking greedily is NOT optimal
# (34.597 against 34.921 for sample 0), so they are what makes the local search testable.
@pytest.mark.parametrize("seed,sample", [(7, 0), (8, 0), (8, 2), (8, 4), (11, 0)])
def test_the_search_matches_exhaustive_search_on_a_small_pool(deck, seed, sample):
    room, sets, a, kept = _room(deck, seed, 150)
    i = ap.prepare(room, sets, deck, "KKR", kept)
    # keep the pool small enough to enumerate but rich enough to hold a legal twelve: the best
    # two of each kind, then random company
    rng = random.Random(sample)
    by_kind: dict = {}
    for c in i.movable:
        v = i.vals[c.key]
        by_kind.setdefault((au.band(c.card), c.card.has_bowl, c.card.keeper_eligible,
                            c.card.overseas is True), []).append(c)
    small = []
    for g in by_kind.values():
        small += sorted(g, key=lambda c: -(i.vals[c.key].bat + (i.vals[c.key].bowl or 0)))[:1]
    rest = [c for c in i.movable if c not in small]
    small += rng.sample(rest, max(0, 15 - len(small)))
    small = sorted(small[:15], key=lambda c: c.lot)
    i.movable = small
    i.pool = small
    plan = ap.plan_from(i, 0)
    want = _exhaustive(i)
    assert plan.value == pytest.approx(want, abs=1e-9)


def test_pruning_never_costs_value(deck):
    for seed in (7, 8):
        room, sets, a, kept = _room(deck, seed, 60)
        i = ap.prepare(room, sets, deck, "KKR", kept)
        full = [c for c in i.pool if c.status != "unsold"]
        assert len(i.movable) < len(full)
        a1 = ap._search(i.owned + i.retained, i.movable, i.retained, i.holes, i.budget,
                        i.open_places, i.ov_room, i.vals, 0)
        a2 = ap._search(i.owned + i.retained, full, i.retained, i.holes, i.budget,
                        i.open_places, i.ov_room, i.vals, 0)
        assert a1[2] == pytest.approx(a2[2], abs=1e-9)


# --- value, price, and reading the room ---------------------------------------------------


def test_a_twelve_counts_only_its_five_best_bowlers(deck):
    cards = [c for cs in deck.cards_by_fs.values() for c in cs]
    bowlers = [c for c in cards if ap.legend_values(c)[1] is not None][:7]
    vals = ap.values_of(bowlers)
    total = ap.twelve_value(bowlers, vals)
    bat = sum(v.bat for v in vals.values())
    best5 = sorted((v.bowl for v in vals.values()), reverse=True)[:5]
    assert total == pytest.approx(bat + sum(best5))


def test_price_uses_the_rooms_own_sales_then_the_curve():
    sold = [(90, v) for v in (800, 900, 1000, 1100, 1200)] + [(70, v) for v in (60, 80, 100, 120, 140)]
    price = ap.price_model(sold, True)
    assert price(90, 30) == 1000 and price(91, 30) == 1000       # the median within two points
    assert price(70, 30) == 100
    assert price(80, 30) > 30                                      # no comparables: the scaled curve
    prior = ap.price_model([], False)
    assert prior(85, 30) == round(au.value_curve(85, False) * ap.PRICE_PRIOR[False])
    assert ap.price_model([(99, 9000)] * 5, True)(99, 30) == ap.PRICE_CAP
    assert ap.price_model([], True)(60, 200) == 200               # never under the base price


def test_signings_are_found_from_the_sales_so_you_do_not_list_them(deck):
    room, sets, a, kept = _room(deck, 7, 150)
    mine = next(t for t in room["auction"]["teams"] if t["short"] == "KKR")
    bought = [L for st in sets["sets"] for L in st["lots"]
              if L["status"] == "sold" and L["team"] == "KKR"]
    assert bought, "the fixture needs a team that has bought somebody"
    i = ap.prepare(room, sets, deck, "KKR", kept)
    assert {c.name for c in i.owned} == {L["name"] for L in bought}
    assert i.holes == mine["players"] - len(i.owned) - len(i.retained) == 0


def test_a_squad_member_the_room_shows_but_you_did_not_name_becomes_a_hole(deck):
    room, sets, a, kept = _room(deck)
    mine = next(t for t in room["auction"]["teams"] if t["short"] == "KKR")
    mine["players"] += 1
    mine["purse"] -= 500
    plan = ap.plan_room(room, sets, deck, "KKR", kept)
    assert plan.holes == 1 and len(plan.twelve) == 11
    assert any("not accounted for" in w for w in plan.warnings)
    assert au.twelve_feasible(plan.twelve, 1)


def test_you_can_name_the_team_by_short_code_or_owner(deck):
    room, *_ = _room(deck)
    teams = room["auction"]["teams"]
    assert ap._find_team(teams, "kkr")["short"] == "KKR"
    assert ap._find_team(teams, "me")["short"] == "KKR"
    with pytest.raises(ap.PlanError):
        ap._find_team(teams, "nobody")


def test_keep_parses_a_season_and_refuses_to_guess(deck):
    index = ap.index_deck(deck)
    [c] = ap.parse_keep("Andre Russell 2019", index)
    assert (c.name, c.season_year) == ("Andre Russell", 2019)
    with pytest.raises(ap.PlanError, match="ambiguous"):
        ap.parse_keep("Andre Russell", index)
    with pytest.raises(ap.PlanError, match="nobody"):
        ap.parse_keep("Zzzzz Qqqq 2019", index)
    assert ap.parse_keep("", index) == []


def test_it_refuses_a_room_that_has_not_started_or_has_no_catalogue_yet(deck):
    room, sets, a, kept = _room(deck)
    with pytest.raises(ap.PlanError, match="not started"):
        ap.plan_room(dict(room, status="lobby"), sets, deck, "KKR", kept)
    with pytest.raises(ap.PlanError, match="empty"):
        ap.plan_room(room, {"sets": [], "current_set": None}, deck, "KKR", kept)


def test_unsold_lots_are_offered_only_as_backups_at_their_base_price(deck):
    room, sets, a, kept = _room(deck, 7, 80)
    unsold = [L for st in sets["sets"] for L in st["lots"] if L["status"] == "unsold"]
    assert unsold, "the fixture needs some unsold lots by now"
    i = ap.prepare(room, sets, deck, "KKR", kept)
    assert not any(c.status == "unsold" for c in i.movable)
    mine = {L["name"]: L for L in unsold}
    for c in i.pool:
        if c.status == "unsold":
            assert c.price == c.base == mine[c.card.name]["base"]


def test_the_output_names_every_buy_its_walk_away_price_and_its_backups(deck):
    plan, *_ = _plan(deck)
    cards = [t.cand.card for t in plan.targets] + plan.owned + plan.retained
    for t in plan.targets:
        cards += [b.cand.card for b in t.backups]
    text = ap.render(plan, ap.values_of(cards))
    for t in plan.targets:
        assert t.cand.card.name in text
    assert text.count("WALK AWAY") == len(plan.targets)
    assert text.count("backup 1:") + text.count("backup: none") == len(plan.targets)
    assert "[retained]" in text
