"""The coach asks because the side needs it (A246)."""

import random

import pytest

from game import career as C
from game import career_traits as ct
from tools.snapshot_deck import deck_from, model_from, read_document

COACH_TESTS = True          # conftest leaves the coach on for this module
DOC = read_document()
pytestmark = [pytest.mark.skipif(DOC is None, reason="no deck snapshot"),
              pytest.mark.usefixtures("no_incidents")]


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


def _you(deck, fs, role):
    start, ceiling = C.spin(deck, 3, role)
    year = deck.cards_by_fs[fs][0].season_year
    return C.ability(start, ceiling, 0.5, setup=C.Setup("N", role, 2008), fs_id=fs, year=year,
                     franchise=deck.cards_by_fs[fs][0].franchise)


def _regular_bowlers(deck, you, fs):
    from game.__main__ import ATTACK_MIN_BALLS
    xi, impact = C.xi_with(you, list(deck.cards_by_fs[fs]))
    twelve = [c for c in xi + ([impact] if impact else []) if c.person_id != C.YOU_ID]
    return sum((c.bowl_balls or 0) >= ATTACK_MIN_BALLS for c in twelve)


def _regular_batters(deck, you, fs):
    from game.__main__ import BAT_MIN_BALLS
    xi, impact = C.xi_with(you, list(deck.cards_by_fs[fs]))
    twelve = [c for c in xi + ([impact] if impact else []) if c.person_id != C.YOU_ID]
    return sum((c.bat_balls or 0) >= BAT_MIN_BALLS and c.role != "bowler" for c in twelve)


def test_a_batters_need_is_read_from_the_twelve_his_side_picks(deck):
    """Recomputed independently over every squad: short exactly when the twelve, without
    you, has fewer than five regular bowlers -- and the archive holds both kinds."""
    seen = set()
    for fs in sorted(deck.cards_by_fs)[::3]:
        you = _you(deck, fs, "opener")
        want = "bowl" if _regular_bowlers(deck, you, fs) < C.NEED_BOWLERS else None
        assert C.squad_need(deck, you, fs) == want, fs
        seen.add(want)
    assert seen == {"bowl", None}, "the squads must include short and full attacks"


def test_a_bowlers_need_is_a_twelve_short_of_regular_batters(deck):
    seen = set()
    for fs in sorted(deck.cards_by_fs)[::3]:
        you = _you(deck, fs, "spin")
        want = "bat" if _regular_batters(deck, you, fs) < C.NEED_BATTERS else None
        assert C.squad_need(deck, you, fs) == want, fs
        seen.add(want)
    assert seen == {"bat", None}, "the squads must include both"


def test_an_all_rounder_has_no_need(deck):
    fs = sorted(deck.cards_by_fs)[0]
    assert C.squad_need(deck, _you(deck, fs, "allrounder"), fs) is None


def _seed_between(lo, hi, k=3):
    """A seed whose coach draw falls between the two chances: offered only when needed."""
    return next(s for s in range(5000) if lo <= random.Random(f"{s}:coach:{k}").random() < hi)


def test_need_raises_the_chance_on_the_same_draw(deck):
    setup = C.Setup("N", "opener", 2010)
    start, _ = C.spin(deck, 3, "opener")
    cp = C.Checkpoint()
    s = _seed_between(C.COACH_TRIAL_CHANCE, C.COACH_NEED_CHANCE)
    assert C.coach_card(s, setup, start, cp, 3, False) is None
    assert C.coach_card(s, setup, start, cp, 3, False, need="bat") is None   # not his job
    card = C.coach_card(s, setup, start, cp, 3, False, need="bowl")
    assert card is not None and card["need"] and "bowler short" in card["text"]
    low = _seed_between(0.0, C.COACH_TRIAL_CHANCE)
    plain = C.coach_card(low, setup, start, cp, 3, False)
    assert plain is not None and not plain.get("need")          # no need: as before


def test_a_needed_offer_costs_you_to_refuse_and_builds_you_to_take(deck):
    card = C.asked_by_need(dict(C.COACH_TRIAL_BOWL, target="allrounder"))
    yes = [o for o in card["options"] if o["take"]]
    no = [o for o in card["options"] if not o["take"]]
    assert all(("lead", C.NEED_LEAD) in o["traits"] for o in yes)
    assert all(o["standing"] == C.NEED_STANDING and ("lead", -C.NEED_LEAD) in o["traits"]
               for o in no)
    assert "standing" not in C.COACH_TRIAL_BOWL["options"][1]      # the template is untouched


def test_a_mentor_adds_to_the_traits_of_a_needed_offer(deck):
    start, _ = C.spin(deck, 1, "opener")
    card = C.asked_by_need(dict(C.COACH_TRIAL_STYLE, target="allrounder"))
    card = C.with_coach_mentor(deck, card, 26, start, C.Checkpoint())
    for o in card["options"]:
        if o["take"] and o.get("mentor"):
            assert ("lead", C.NEED_LEAD) in o["traits"]


def test_a_refusal_reaches_the_clubs_standing(deck, monkeypatch):
    """Played through: two careers identical but for the coach's answer end that season with
    the club's standing apart by exactly the refusal's cost, and the record says why."""
    model = model_from(DOC)
    monkeypatch.setattr(C, "squad_need", lambda deck, you, fs: "bowl")
    monkeypatch.setattr(C, "COACH_NEED_CHANCE", 1.0)
    setup = C.Setup("N", "opener", 2010)
    out = {}
    for answer in (True, False):
        C._SEASON_CACHE.clear()
        rng = random.Random(1)
        moves: list[str] = []
        asked_at = None
        for _ in range(80):
            car = C.play_career(deck, model, 5, setup, moves)
            if asked_at is not None and len(car.seasons) > asked_at:
                break
            p = car.pending
            if p.kind == "coach":
                asked_at = len(car.seasons)
                moves.append(f"t{next(i for i, o in enumerate(p.coach['options']) if o['take'] == answer)}")
            else:
                moves.append(C.policy_move(car, "loyal", rng))
        out[answer] = (car.checkpoint.standing, car.seasons[asked_at].coach)
    C._SEASON_CACHE.clear()
    assert out[True][1]["need"] and out[False][1]["need"]
    assert not out[False][1]["take"] and out[True][1]["take"]
    assert out[False][0] - out[True][0] == C.NEED_STANDING


IMPACT_FS = 168      # the one real squad whose need changes when its Impact Player is counted


def test_the_impact_player_counts_toward_the_attack(deck):
    """A twelve's Impact Player bowls where its eleven falls short (A78), so he counts. Only
    one real squad of 166 turns on it, found by search; the fixture checks it still does."""
    from game.__main__ import ATTACK_MIN_BALLS
    you = _you(deck, IMPACT_FS, "opener")
    xi, impact = C.xi_with(you, list(deck.cards_by_fs[IMPACT_FS]))
    xi_only = sum((c.bowl_balls or 0) >= ATTACK_MIN_BALLS for c in xi if c.person_id != C.YOU_ID)
    assert impact is not None and xi_only < C.NEED_BOWLERS <= _regular_bowlers(deck, you, IMPACT_FS), \
        "the fixture must be short without its Impact Player and full with him"
    assert C.squad_need(deck, you, IMPACT_FS) is None
