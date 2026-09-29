"""The auction engine (A136). No database: fake cards for the algebra, the committed deck
snapshot for whole auctions.

The legality algebra is checked against a BRUTE-FORCE solver written independently here --
subsets and augmenting paths, sharing no code with `game.auction`'s dynamic programme --
because `twelve_feasible` is the one rule that keeps a squad from getting stuck and it is
a rule that lives in code, not in a table (the standing rule: a behavioural rule without a
test is an unprotected rule).
"""

from __future__ import annotations

import random
from itertools import combinations

import pytest

import game.auction as au
from etl.feasibility import (
    BATTING_ROLE_SLOTS, BOWLERS_IN_TWELVE, IMPACT_SLOT, OVERSEAS_CAP, TWELVE_SIZE, Card,
    order_errors,
)
from tools import snapshot_deck

DOC = snapshot_deck.read_document()
needs_snapshot = pytest.mark.skipif(DOC is None, reason="no deck snapshot committed")
BANDS = ("top", "middle", "finisher", "tail")


def card(i: int, band: str = "top", *, bowls: bool = False, keeper: bool = False,
         overseas: bool = False, display: int = 80) -> Card:
    return Card(fs_id=1, person_id=f"p{i}", name=f"p{i}", bat=0.1,
                bowl=0.1 if bowls else None, role="keeper" if keeper else "batter",
                keeper_eligible=keeper, overseas=overseas, display=display,
                positions=BATTING_ROLE_SLOTS[band])


def random_squad(rng: random.Random, n: int) -> list[Card]:
    return [card(i, rng.choice(BANDS), bowls=rng.random() < 0.45,
                 keeper=rng.random() < 0.12, overseas=rng.random() < 0.35,
                 display=rng.randint(70, 99)) for i in range(n)]


# --- an independent solver ---------------------------------------------------------------

def _brute_matchable(cards: list[Card]) -> bool:
    owner: dict[int, int] = {}

    def place(i, seen):
        for slot in cards[i].slots:
            if slot not in seen:
                seen.add(slot)
                if slot not in owner or place(owner[slot], seen):
                    owner[slot] = i
                    return True
        return False
    return all(place(i, set()) for i in range(len(cards)))


def _brute_legal(chosen: list[Card], wild: int) -> bool:
    return (sum(c.overseas is True for c in chosen) <= OVERSEAS_CAP
            and sum(c.has_bowl for c in chosen) + wild >= BOWLERS_IN_TWELVE
            and (any(c.keeper_eligible for c in chosen) or wild >= 1)
            and _brute_matchable(chosen))


def brute_feasible(squad: list[Card], wildcards: int) -> bool:
    wild = min(wildcards, TWELVE_SIZE)
    real = TWELVE_SIZE - wild
    return any(_brute_legal(list(sub), wild) for sub in combinations(squad, real))


def brute_best_value(squad: list[Card]) -> float | None:
    best = None
    for sub in combinations(squad, TWELVE_SIZE):
        if _brute_legal(list(sub), 0):
            v = sum(au._card_value(c) for c in sub)
            best = v if best is None else max(best, v)
    return best


# --- money -------------------------------------------------------------------------------

def test_the_increment_ladder_is_the_official_one():
    assert [au.increment(p) for p in (30, 95, 100, 195, 200, 295, 300, 2000)] == \
        [5, 5, 10, 10, 20, 20, 25, 25]


def test_base_price_rises_with_rating_and_spans_the_real_tiers():
    prices = [au.base_price(card(0, display=d)) for d in range(70, 100)]
    assert prices == sorted(prices)
    assert prices[0] == au.MIN_PRICE == 30 and prices[-1] == 200


# --- the legality algebra ----------------------------------------------------------------

def test_hall_condition_agrees_with_a_real_matching_on_every_count_vector():
    for t in range(5):
        for m in range(5):
            for f in range(5):
                for l in range(6):
                    cards = ([card(i, "top") for i in range(t)]
                             + [card(10 + i, "middle") for i in range(m)]
                             + [card(20 + i, "finisher") for i in range(f)]
                             + [card(30 + i, "tail") for i in range(l)])
                    assert au._matchable((t, m, f, l)) == _brute_matchable(cards), (t, m, f, l)


def test_twelve_feasible_agrees_with_brute_force_on_random_squads():
    rng = random.Random(11)
    au._feasible.cache_clear()
    for _ in range(400):
        squad = random_squad(rng, rng.randint(6, 14))
        wild = rng.randint(0, 7)
        assert au.twelve_feasible(squad, wild) == brute_feasible(squad, wild), \
            ([(au.band(c), c.has_bowl, c.keeper_eligible, c.overseas) for c in squad], wild)


def test_best_twelve_finds_the_brute_force_optimum():
    rng = random.Random(5)
    checked = 0
    for _ in range(400):
        squad = random_squad(rng, rng.randint(12, 14))
        expected = brute_best_value(squad)
        got = au.best_twelve(squad)
        if expected is None:
            assert got is None
            continue
        checked += 1
        assert got is not None
        assert sum(au._card_value(c) for c in got) == pytest.approx(expected)
    assert checked >= 20, "too few feasible squads to have tested anything"


def test_arrange_always_produces_a_legal_order_the_independent_verifier_accepts():
    rng = random.Random(3)
    arranged = 0
    for _ in range(80):
        squad = random_squad(rng, 18)
        twelve = au.best_twelve(squad)
        if twelve is None:
            continue
        result = au.arrange(twelve)
        assert result is not None
        order, impact = result
        assert order_errors(order, impact, squad) == []
        assert any(c.keeper_eligible for c in order), "the keeper must be in the eleven"
        arranged += 1
    assert arranged >= 30


def test_the_impact_player_is_a_specialist_when_one_can_be_spared():
    """He plays one discipline only (A78), so an all-rounder there wastes half of him.

    Built so the all-rounder CAN be spared -- he is one of five tail cards, and removing
    any one of the five leaves a legal eleven with five bowlers -- and is the most valuable
    card there, so a rule that simply benches the best removable player picks him."""
    def spec(i, band, role, bowls=False, display=80, keeper=False):
        return Card(fs_id=1, person_id=f"s{i}", name=f"s{i}", bat=0.1,
                    bowl=0.1 if bowls else None, role=role, keeper_eligible=keeper,
                    overseas=False, display=display, positions=BATTING_ROLE_SLOTS[band])
    allrounder = spec(99, "tail", "allrounder", bowls=True, display=99)
    twelve = ([spec(0, "top", "keeper", keeper=True), spec(1, "top", "batter"),
               spec(2, "top", "batter"), spec(3, "middle", "batter", bowls=True),
               spec(4, "middle", "batter"), spec(5, "finisher", "batter"),
               spec(6, "finisher", "batter")]
              + [spec(7 + i, "tail", "bowler", bowls=True) for i in range(4)]
              + [allrounder])
    order, impact = au.arrange(twelve)
    assert impact.role in ("batter", "bowler")
    assert allrounder in order


# --- a lot -------------------------------------------------------------------------------

def _lot(base=100):
    return au.Lot(3, card(0), "BA1", base)


def test_the_highest_ceiling_wins_near_the_second_highest():
    bids = au.bid_log(_lot(), {0: 500, 1: 300, 2: 0}, seed=1, round_no=0)
    assert bids[-1].team == 0
    assert 300 <= bids[-1].price <= au.next_price(300)


def test_nobody_willing_means_unsold_and_a_lone_bidder_pays_the_base():
    assert au.bid_log(_lot(), {0: 90, 1: 0}, 1, 0) == []
    assert au.bid_log(_lot(), {0: 900, 1: 0}, 1, 0) == [au.Bid(0, 100)]


def test_clicking_and_setting_a_maximum_are_the_same_game():
    """The property both bidding styles are built on: raising your ceiling from X1 to X2
    only EXTENDS the log you saw at X1 -- nothing already shown to you changes."""
    rng = random.Random(9)
    for trial in range(300):
        cpu = {t: rng.choice([0, rng.randint(100, 3000)]) for t in range(1, 10)}
        x1 = rng.randint(100, 2000)
        x2 = x1 + rng.randint(1, 1500)
        low = au.bid_log(_lot(), {**cpu, 0: x1}, trial, 0, human=0)
        high = au.bid_log(_lot(), {**cpu, 0: x2}, trial, 0, human=0)
        # Every step priced at or below x1 sees the human equally willing in both runs, so
        # it must be decided identically; only a step priced above x1 may differ.
        cut = next((i for i, b in enumerate(low) if b.price > x1), len(low))
        assert high[:cut] == low[:cut]
        assert all(b.price <= x2 for b in high if b.team == 0)


def test_a_team_never_bids_above_its_ceiling():
    rng = random.Random(4)
    for trial in range(200):
        caps = {t: rng.randint(0, 2500) for t in range(10)}
        for b in au.bid_log(_lot(), caps, trial, 0):
            assert b.price <= caps[b.team]


# --- whole auctions ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def deck():
    return snapshot_deck.deck_from(DOC)


@needs_snapshot
def test_an_auction_replays_exactly_from_its_seed(deck):
    a = au.run_auction(deck, 42)
    b = au.run_auction(deck, 42)
    assert [(s.lot.card.person_id, s.winner, s.price, s.bids) for s in a.sales] == \
        [(s.lot.card.person_id, s.winner, s.price, s.bids) for s in b.sales]


@needs_snapshot
@pytest.mark.parametrize("seed", range(6))
def test_every_team_ends_with_eighteen_and_a_legal_twelve(deck, seed):
    a = au.run_auction(deck, seed)
    assert a.stranded == []
    for team in a.teams:
        assert len(team.squad) == au.SQUAD_SIZE
        assert len({c.person_id for c in team.squad}) == au.SQUAD_SIZE
        assert team.purse >= 0
        assert team.overseas <= au.SQUAD_OVERSEAS_CAP
        order, impact = a.twelve(team)
        assert order_errors(order, impact, team.squad) == []


@needs_snapshot
def test_a_player_is_sold_at_most_once(deck):
    a = au.run_auction(deck, 3)
    owned = [c.person_id for t in a.teams for c in t.squad]
    assert len(owned) == len(set(owned))


@needs_snapshot
def test_a_human_who_never_bids_is_still_given_a_legal_squad(deck):
    class Passive(au.Human):
        pass
    a = au.run_auction(deck, 8, human_short="MI", human=Passive())
    mi = next(t for t in a.teams if t.short == "MI")
    assert len(mi.squad) == au.SQUAD_SIZE
    assert order_errors(*a.twelve(mi), mi.squad) == []


@needs_snapshot
def test_the_purse_reserve_stops_a_reckless_human_from_stranding(deck):
    class AllIn(au.Human):
        def ceiling(self, auction, team, lot, round_no):
            return 10 ** 6
    a = au.run_auction(deck, 2, human_short="CSK", human=AllIn())
    csk = next(t for t in a.teams if t.short == "CSK")
    assert len(csk.squad) == au.SQUAD_SIZE and csk.purse >= 0
    assert order_errors(*a.twelve(csk), csk.squad) == []


@needs_snapshot
def test_the_catalogue_offers_each_person_once_and_respects_the_split(deck):
    lots = au.build_catalogue(deck, 1)
    assert len({lot.card.person_id for lot in lots}) == len(lots)
    assert sum(lot.card.overseas is True for lot in lots) == au.OVERSEAS_LOTS
    assert lots[0].set_code == "M1"


@needs_snapshot
def test_when_the_catalogue_runs_dry_the_register_finishes_every_squad(deck, monkeypatch):
    """The fill round's second source. A catalogue far too small for 180 places forces it,
    since the unsold lots alone cannot complete ten squads."""
    monkeypatch.setattr(au, "DOMESTIC_LOTS", 60)
    monkeypatch.setattr(au, "OVERSEAS_LOTS", 30)
    a = au.run_auction(deck, 4)
    assert a.fills > 0
    assert a.stranded == []
    assert all(len(t.squad) == au.SQUAD_SIZE for t in a.teams)


@needs_snapshot
def test_the_price_cap_binds_the_computer_teams_and_never_the_human(deck):
    """Ratified by the user: the ₹30 cr cap is a model of how COMPUTER franchises behave.
    A human may pay whatever they like, so the first time a star reaches the cap the human
    can still outbid it -- by one increment, since that is all it takes."""
    cap = int(au.MAX_SHARE * au.PURSE)

    class AllIn(au.Human):
        def ceiling(self, auction, team, lot, round_no):
            return team.max_bid()

    for seed in range(20):
        a = au.run_auction(deck, seed, human_short="RCB", human=AllIn())
        human = next(t.index for t in a.teams if t.human)
        assert all(s.price <= cap for s in a.sales
                   if s.winner is not None and s.winner != human)
        if any(s.winner == human and s.price > cap for s in a.sales):
            return
    pytest.fail("the human never paid above the computer cap in 20 auctions")


# --- retentions and Right to Match [A138] ------------------------------------------------

def _pool_card(deck, franchise, name, year=None):
    return next(c for c in au.retention_pool(deck, franchise)
                if c.name == name and (year is None or c.season_year == year))


@needs_snapshot
def test_a_retention_must_come_from_your_own_franchise_one_season_per_player(deck):
    team = au.Team(0, "DC", "Delhi Capitals")
    rahul_dc = _pool_card(deck, "Delhi Capitals", "KL Rahul")
    rahul_pbks = _pool_card(deck, "Punjab Kings", "KL Rahul", 2021)
    assert au.retention_errors(team, [rahul_dc]) == []
    assert au.retention_errors(team, [rahul_pbks])            # another franchise's season
    other_dc = [c for c in au.retention_pool(deck, "Delhi Capitals")
                if c.person_id == rahul_dc.person_id and c is not rahul_dc]
    assert au.retention_errors(team, [rahul_dc, other_dc[0]])  # the same man twice
    six = []
    for c in au.retention_pool(deck, "Delhi Capitals"):
        if c.person_id not in {x.person_id for x in six}:
            six.append(c)
        if len(six) == 6:
            break
    assert au.retention_errors(team, six)                      # over the limit


@needs_snapshot
def test_retentions_are_charged_at_the_slabs_and_leave_the_rest_as_cards(deck):
    a = au.run_auction(deck, 13, mega=True)
    for team in a.teams:
        slabs = list(au.RETENTION_SLABS[:team.retained])
        assert team.paid[:team.retained] == slabs
        assert team.rtm + sum(1 for s in a.sales if s.rtm and s.rtm.matched
                              and s.rtm.holder == team.index) == au.RTM_PLACES - team.retained
        for c in team.squad[:team.retained]:
            assert au.franchise_of(c) == team.franchise


@needs_snapshot
def test_nobody_is_retained_twice_or_offered_after_being_kept(deck):
    a = au.run_auction(deck, 13, mega=True)
    kept = [c.person_id for t in a.teams for c in t.squad[:t.retained]]
    assert len(kept) == len(set(kept))
    assert not set(kept) & {lot.card.person_id for lot in a.lots}


@needs_snapshot
def test_the_human_retains_first_so_a_shared_legend_is_theirs(deck):
    """LSG is team 9 and PBKS team 7, so in plain team order PBKS would keep Rahul before
    the human was asked. The fixture has to put the human AFTER the computer team, or the
    priority rule never gets anything to do -- the first version used DC (team 5) and
    passed with the rule deleted."""
    rahul = _pool_card(deck, "Lucknow Super Giants", "KL Rahul")
    assert [s for s, _ in au.FRANCHISES].index("LSG") > \
        [s for s, _ in au.FRANCHISES].index("PBKS")

    class KeepRahul(au.Human):
        def retain(self, auction, team, pool):
            return [rahul]

    alone = au.run_auction(deck, 13, mega=True)
    pbks = next(t for t in alone.teams if t.short == "PBKS")
    assert rahul.person_id in {c.person_id for c in pbks.squad[:pbks.retained]}, \
        "the fixture needs PBKS to want Rahul when nobody else has him"
    a = au.run_auction(deck, 13, human_short="LSG", human=KeepRahul(), mega=True)
    pbks = next(t for t in a.teams if t.short == "PBKS")
    lsg = next(t for t in a.teams if t.short == "LSG")
    assert lsg.squad[0] is rahul
    assert rahul.person_id not in {c.person_id for c in pbks.squad}


@needs_snapshot
def test_an_illegal_human_retention_is_refused(deck):
    class Poach(au.Human):
        def retain(self, auction, team, pool):
            return [_pool_card(deck, "Punjab Kings", "KL Rahul", 2021)]
    with pytest.raises(au.RetentionError):
        au.run_auction(deck, 13, human_short="DC", human=Poach(), mega=True)


@needs_snapshot
@pytest.mark.parametrize("seed", range(4))
def test_a_mega_auction_still_ends_with_every_squad_legal(deck, seed):
    a = au.run_auction(deck, seed, mega=True)
    assert a.stranded == []
    for team in a.teams:
        assert len(team.squad) == au.SQUAD_SIZE and team.purse >= 0
        assert order_errors(*a.twelve(team), team.squad) == []


@needs_snapshot
def test_right_to_match_goes_to_the_franchise_the_season_was_played_for(deck):
    for seed in range(6):
        a = au.run_auction(deck, seed, mega=True)
        for s in a.sales:
            if s.rtm:
                holder = a.teams[s.rtm.holder]
                assert holder.franchise == au.franchise_of(s.lot.card)
                assert s.rtm.raised_to >= s.rtm.hammer
                assert s.winner == (s.rtm.holder if s.rtm.matched else s.bids[-1].team)
                assert s.price == s.rtm.raised_to


def _find_rtm(deck, human_short, role):
    """A seed on which the human's franchise meets a Right to Match in `role`:
    'holder' (their old player sold elsewhere) or 'winner' (they bought someone's)."""
    class Probe(au.Human):
        hit = False

        def ceiling(self, auction, team, lot, round_no):
            return team.max_bid() if role == "winner" and (lot.card.display or 0) >= 88 else 0

        # Each probe counts only its OWN kind of question: the first version marked a hit
        # on either, so a "winner" search could return a seed where the human only ever
        # HELD a card, and the test then waited for a raise that never came.
        def rtm_use(self, *a):
            Probe.hit = Probe.hit or role == "holder"
            return False

        def rtm_raise(self, auction, team, lot, price, holder):
            Probe.hit = Probe.hit or role == "winner"
            return price

    for seed in range(40):
        Probe.hit = False
        au.run_auction(deck, seed, human_short=human_short, human=Probe(), mega=True)
        if Probe.hit:
            return seed
    pytest.skip(f"no {role} RTM for {human_short} in 40 seeds")


@needs_snapshot
def test_a_human_holder_who_uses_and_matches_takes_the_player(deck):
    seed = _find_rtm(deck, "RCB", "holder")

    class UseAndMatch(au.Human):
        def rtm_use(self, *a): return True
        def rtm_match(self, *a): return True

    a = au.run_auction(deck, seed, human_short="RCB", human=UseAndMatch(), mega=True)
    rcb = next(t for t in a.teams if t.short == "RCB")
    events = [s for s in a.sales if s.rtm and s.rtm.holder == rcb.index]
    assert events and all(s.winner == rcb.index and s.rtm.matched for s in events)
    assert rcb.rtm == au.RTM_PLACES - rcb.retained - len(events)


@needs_snapshot
def test_a_human_holder_who_declines_to_match_keeps_the_card(deck):
    seed = _find_rtm(deck, "RCB", "holder")

    class UseNoMatch(au.Human):
        def rtm_use(self, *a): return True
        def rtm_match(self, *a): return False

    a = au.run_auction(deck, seed, human_short="RCB", human=UseNoMatch(), mega=True)
    rcb = next(t for t in a.teams if t.short == "RCB")
    for s in a.sales:
        if s.rtm and s.rtm.holder == rcb.index and not s.rtm.matched:
            assert s.winner != rcb.index and s.price == s.rtm.raised_to > s.rtm.hammer
    assert rcb.rtm == au.RTM_PLACES - rcb.retained - sum(
        1 for s in a.sales if s.rtm and s.rtm.holder == rcb.index and s.rtm.matched)


@needs_snapshot
def test_a_human_winner_s_final_raise_is_what_the_holder_must_match(deck):
    seed = _find_rtm(deck, "KKR", "winner")
    raises = []

    class RaiseHard(au.Human):
        def ceiling(self, auction, team, lot, round_no):
            return team.max_bid() if (lot.card.display or 0) >= 88 else 0

        def rtm_raise(self, auction, team, lot, price, holder):
            raises.append(price)
            return 10 ** 7                       # everything the reserve allows

    a = au.run_auction(deck, seed, human_short="KKR", human=RaiseHard(), mega=True)
    kkr = next(t for t in a.teams if t.short == "KKR")
    events = [s for s in a.sales if s.rtm and s.bids[-1].team == kkr.index]
    assert raises and len(events) == len(raises)
    for s in events:
        assert s.rtm.raised_to > s.rtm.hammer
        assert not s.rtm.matched or a.teams[s.rtm.holder].purse >= 0


@needs_snapshot
def test_without_mega_there_are_no_retentions_and_no_cards(deck):
    a = au.run_auction(deck, 13)
    assert all(t.retained == 0 and t.rtm == 0 for t in a.teams)
    assert not any(s.rtm for s in a.sales)
