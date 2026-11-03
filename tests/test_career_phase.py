"""Phase specialisation for Build a Career's own player (A248)."""

import dataclasses
import json
import random

import pytest

from etl import phase_skill as E
from game import career as C
from game import phase_skill as P
from game import simulator as S
from tools.snapshot_deck import deck_from, model_from, read_document

PHASE_TESTS = True          # conftest leaves phases on for this module
COACH_TESTS = True
DOC = read_document()
pytestmark = [pytest.mark.skipif(DOC is None, reason="no deck snapshot"),
              pytest.mark.usefixtures("no_incidents")]


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


# --- the archive's leans --------------------------------------------------------------------

def test_a_lean_is_against_his_own_average_and_shrunk_by_the_measured_balls():
    # par 1.0 a ball in every phase; he concedes 0.8 at the death and 1.2 elsewhere over equal
    # balls: his own average is par, so his death lean is +0.2 saved, shrunk by k
    rows = [("x", 2020, ph, 600, 600) for ph in E.PHASES]
    rows += [("me", 2020, "powerplay", 300, 360), ("me", 2020, "middle", 300, 360),
             ("me", 2020, "death", 300, 240)]
    lean = E.leans(rows, "bowl")["me"]
    par = {ph: (600 + (360 if ph != "death" else 240)) / 900 for ph in E.PHASES}
    exc = {"powerplay": 1.2 - par["powerplay"], "middle": 1.2 - par["middle"],
           "death": 0.8 - par["death"]}
    own = sum(exc.values()) / 3
    for i, ph in enumerate(E.PHASES):
        k = E.SHRINK_BALLS[("bowl", ph)]
        assert lean[i] == pytest.approx(-(exc[ph] - own) * 300 / (300 + k), abs=1e-6)
    assert lean[2] > 0 > lean[0], "a bowler cheaper at the death leans toward it, saved runs +"


def test_an_even_player_has_no_lean_and_is_left_out():
    rows = [("x", 2020, ph, 600, 600) for ph in E.PHASES]
    rows += [("me", 2020, ph, 300, 300) for ph in E.PHASES]
    assert "me" not in E.leans(rows, "bat")


def test_batting_leans_score_faster_as_positive():
    rows = [("x", 2020, ph, 600, 600) for ph in E.PHASES]
    rows += [("me", 2020, "powerplay", 300, 450), ("me", 2020, "middle", 300, 300),
             ("me", 2020, "death", 300, 300)]
    lean = E.leans(rows, "bat")["me"]
    assert lean[0] > 0 > lean[2]


def test_the_committed_leans_read_true_to_the_record():
    """Real specialists, by person id, as the record has them: Malinga a death bowler,
    Bhuvneshwar a new-ball one, de Villiers dangerous at the death."""
    by_name = {c.name: c.person_id for cs in deck_from(DOC).cards_by_fs.values() for c in cs}
    malinga = P.lean(by_name["SL Malinga"], "bowl")
    bhuvi = P.lean(by_name["B Kumar"], "bowl")
    abd = P.lean(by_name["AB de Villiers"], "bat")
    assert malinga[2] > 0 and malinga[2] > malinga[0]
    assert bhuvi[0] > 0 and bhuvi[0] > bhuvi[2]
    assert abd[2] > 0 and abd[2] > abd[0]
    assert P.lean("nobody", "bat") == P.NONE


# --- the engine ---------------------------------------------------------------------------

def _aims_by_over(monkeypatch, model, bat, bowl):
    """Every ball's scoring target, beside the over it was bowled in."""
    seen, overs = [], []
    real = S.split_ball

    def spy(probs, values, target, odds):
        seen.append(target)
        return real(probs, values, target, odds)

    class M:
        def __getattr__(self, n):
            return getattr(model, n)

        def state(self, over, wickets):
            overs.append(over)
            return model.state(over, wickets)
    monkeypatch.setattr(S, "split_ball", spy)
    S.play_innings(M(), [bat] * 11, [bowl] * 5, random.Random(5))
    return list(zip(overs, seen))


@pytest.mark.parametrize("disc", ["bat", "bowl"])
def test_a_lean_moves_the_ball_only_in_its_own_phase(monkeypatch, model, disc):
    bat = S.Player("b", 0.0, bat_scoring=0.1, person_id="b")
    bowl = S.Player("w", 0.0, bowl=0.0, bowl_scoring=0.05, person_id="w")
    if disc == "bat":
        bat = dataclasses.replace(bat, phase_bat=(0.0, 0.0, 0.25))
        extra = 0.25
    else:
        bowl = dataclasses.replace(bowl, phase_bowl=(0.0, 0.0, 0.2))
        extra = -0.2
    pairs = _aims_by_over(monkeypatch, model, bat, bowl)
    assert any(S.phase_of_over(o) == "death" for o, _ in pairs)
    for over, aim in pairs:
        want = 0.1 - 0.05 + (extra if S.phase_of_over(over) == "death" else 0.0)
        assert aim == pytest.approx(want, abs=1e-12), over


def test_no_lean_is_the_engine_exactly(model):
    a = S.Player("b", 0.0, bat_scoring=0.05, person_id="b")
    w = S.Player("w", 0.0, bowl=0.0, person_id="w")
    one = S.play_innings(model, [a] * 11, [w] * 5, random.Random(9))
    two = S.play_innings(model, [dataclasses.replace(a, phase_bat=None)] * 11, [w] * 5,
                         random.Random(9))
    assert (one.runs, one.wickets, one.balls) == (two.runs, two.wickets, two.balls)


# --- the career ---------------------------------------------------------------------------

def test_the_coachs_work_gains_in_one_phase_costs_in_the_others_and_is_capped():
    built = {}
    for _ in range(5):
        built = C.phase_after(built, "bowl", 2)
    assert built["bowl"][2] == pytest.approx(C.PHASE_MAX)
    assert built["bowl"][0] == pytest.approx(-C.PHASE_COST * C.PHASE_MAX / C.PHASE_STEP)
    once = C.phase_after({}, "bat", 0)
    assert once["bat"] == pytest.approx([C.PHASE_STEP, -C.PHASE_COST, -C.PHASE_COST])


def _card(**kw):
    base = dict(fs_id=1, person_id=C.YOU_ID, name="N", bat=0.0, bowl=0.0, role="bowler",
                positions=frozenset({9, 10, 11}), bat_scoring=0.0, bat_dismissal=1.0,
                bowl_scoring=0.0, bowl_dismissal=1.0, bowling_style="pace")
    base.update(kw)
    return C.Card(**base)


def test_which_phase_the_coach_makes_your_job():
    assert C.phase_job(_card(role="batter", positions=frozenset({1, 2, 3}), bowl=None,
                             bowl_scoring=None)) == ("bat", 0)
    assert C.phase_job(_card(role="batter", positions=frozenset({5, 6, 7}), bowl=None,
                             bowl_scoring=None,
                             phase_bat=(0.2, 0.0, -0.1))) == ("bat", 2), \
        "a lower-order batter's job is the death whatever his lean"
    assert C.phase_job(_card()) == ("bowl", 2)
    assert C.phase_job(_card(bowling_style="spin")) == ("bowl", 0)
    assert C.phase_job(_card(bowling_style="spin", phase_bowl=(0.0, 0.0, 0.08))) == ("bowl", 0), \
        "a spinner's job is the new ball whatever his death lean"
    assert C.phase_job(_card(phase_bowl=(0.05, 0.0, 0.01))) == ("bowl", 0)
    assert C.phase_job(_card(phase_bowl=(0.01, 0.0, 0.05))) == ("bowl", 2)


def test_a_lean_comes_from_the_two_real_players_behind_the_spins(deck):
    start, top = C.spin(deck, 2, "pace")
    a, b = P.lean(start.person_id, "bowl"), P.lean(top.person_id, "bowl")
    card = C.with_phase(_card(), start, top, 0.5, {})
    want = tuple(round((x + y) / 2, 6) for x, y in zip(a, b))
    assert card.phase_bowl == (want if any(want) else None)
    worked = C.with_phase(_card(), start, top, 0.5, {"bowl": [0.0, 0.0, 0.03]})
    assert worked.phase_bowl[2] == pytest.approx(want[2] + 0.03, abs=1e-6)


def test_off_or_empty_means_no_lean_at_all(deck, monkeypatch):
    start, top = C.spin(deck, 2, "pace")
    monkeypatch.setattr(C, "PHASE_ON", False)
    assert C.with_phase(_card(), start, top, 0.5, {"bowl": [0, 0, 0.09]}).phase_bowl is None


def test_your_side_bowls_you_where_you_chose_to_specialise():
    death = {"bowl": [-0.01, -0.01, 0.03]}
    assert C.phase_placement(_card(phase_bowl=(-0.01, -0.01, 0.06)), death) == "death"
    assert C.phase_placement(_card(phase_bowl=(0.04, -0.01, -0.01)),
                             {"bowl": [0.03, -0.01, -0.01]}) == "powerplay"
    assert C.phase_placement(_card(phase_bowl=(-0.01, -0.01, 0.06))) is None, \
        "a lean from the templates alone moves no overs"
    assert C.phase_placement(_card(phase_bowl=(0.06, -0.01, -0.01)), death) is None, \
        "only the phase you chose"
    assert C.phase_placement(_card(phase_bowl=(0.01, 0.0, 0.01)), death) is None
    assert C.phase_placement(_card(), death) is None


def _season(deck, model, phase_bowl, fs=None, seed=4, built=None):
    fs = fs or sorted(deck.cards_by_fs)[40]
    year = deck.cards_by_fs[fs][0].season_year
    start, top = C.spin(deck, 3, "pace")
    you = C.ability(start, top, 0.5, setup=C.Setup("N", "pace", 2010), fs_id=fs, year=year,
                    franchise=deck.cards_by_fs[fs][0].franchise)
    you = dataclasses.replace(you, phase_bowl=phase_bowl)
    return C.simulate_year(deck, model, seed, 2, you, phase_built=built)


def _death_share(y):
    death = total = 0
    for r in y.season.results:
        for inn in (r.home_innings, r.away_innings):
            for o in inn.over_log:
                if o.bowler_id == C.YOU_ID:
                    total += 1
                    death += S.phase_of_over(o.over) == "death"
    return death / total


def test_a_placed_specialist_really_bowls_at_the_death(deck, model):
    plain = _death_share(_season(deck, model, None))
    lean = (-0.03, -0.03, 0.09)
    assert _death_share(_season(deck, model, lean)) == pytest.approx(plain), \
        "a lean you did not choose moves no overs"
    placed = _death_share(_season(deck, model, lean, built={"bowl": [-0.02, -0.02, 0.06]}))
    assert placed > plain + 0.3, (plain, placed)


def _drive(deck, model, setup, seed, answer, limit=60):
    rng, moves = random.Random(seed), []
    for _ in range(limit):
        car = C.play_career(deck, model, seed, setup, moves)
        p = car.pending
        if p is None or car.checkpoint.ended:
            return car, None
        if p.kind == "coach" and p.coach["key"] == "phase":
            return car, p
        moves.append("t1" if p.kind == "coach" else C.policy_move(car, "loyal", rng))
    return car, None


def test_taking_the_coachs_job_builds_the_lean_and_shows_it(deck, model):
    setup = C.Setup("N", "pace", 2010)
    found = None
    for seed in range(40):
        C._SEASON_CACHE.clear()
        car, p = _drive(deck, model, setup, seed, None)
        # a death job: an opening bowler gets half his overs in the powerplay unplaced, so
        # only the death can show placement against a baseline of about a quarter
        if p is not None and p.coach["phase"] == ["bowl", 2]:
            found = (seed, car, p)
            break
    assert found, "no seed offered a death job"
    seed, car, p = found
    disc, idx = p.coach["phase"]
    before = dict(car.checkpoint.phase)
    take = next(i for i, o in enumerate(p.coach["options"]) if o["take"])
    # the move list that reached the offer, then the answer
    rng, moves = random.Random(seed), []
    while True:
        c2 = C.play_career(deck, model, seed, setup, moves)
        if c2.pending.kind == "coach" and c2.pending.coach["key"] == "phase":
            break
        moves.append("t1" if c2.pending.kind == "coach" else C.policy_move(c2, "loyal", rng))
    moves.append(f"t{take}")
    played = len(c2.seasons)
    while True:                   # the checkpoint moves on once the season is played
        after = C.play_career(deck, model, seed, setup, moves)
        if len(after.seasons) > played or after.pending is None:
            break
        moves.append("t1" if after.pending.kind == "coach"
                     else C.policy_move(after, "loyal", rng))
    assert after.checkpoint.phase == C.phase_after(before, disc, idx)
    # and the season played with the job in it bowls you in that phase
    mine = [o for r in after.detail.results for inn in (r.home_innings, r.away_innings)
            for o in inn.over_log if o.bowler_id == C.YOU_ID]
    assert mine
    share = sum(S.phase_of_over(o.over) == S.PLAN_PHASES[idx] for o in mine) / len(mine)
    # unplaced, a bowler gets about the phase's own share of overs (0.25-0.3); never
    # two running caps how far placement can push it
    assert share > 0.45, share
    assert after.checkpoint.phase_offers == car.checkpoint.phase_offers + 1
    # the checkpoint carries it through a JSON round trip
    back = C.Checkpoint.from_dict(json.loads(json.dumps(after.checkpoint.to_dict())))
    assert back.phase == after.checkpoint.phase and back.phase_offers == after.checkpoint.phase_offers
    C._SEASON_CACHE.clear()


def test_the_offer_is_seeded_on_its_own_stream_and_bounded(deck):
    you = _card()
    cp = C.Checkpoint()
    offered = [k for k in range(1, 12) if C.phase_card(7, k, cp, you, False)]
    want = [k for k in range(1, 12) if random.Random(f"7:phase:{k}").random() < C.PHASE_OFFER_CHANCE]
    assert offered == want and offered
    assert C.phase_card(7, offered[0], cp, you, True) is None, "never in a squad season"
    assert C.phase_card(7, 0, cp, you, False) is None, "not in the first season"
    cp.phase_offers = C.PHASE_OFFERS_MAX
    assert C.phase_card(7, offered[0], cp, you, False) is None
    cp.phase_offers = 0
    cp.phase = {"bowl": [0.0, 0.0, C.PHASE_MAX]}
    assert C.phase_card(7, offered[0], cp, you, False) is None, "nothing left to work on"
    decline = C.phase_card(7, offered[0], C.Checkpoint(), you, False)["options"][1]
    assert decline["neutral"] and not decline["take"]


def test_the_words_follow_a_clear_lean():
    assert C.phase_words(_card(phase_bowl=(0.0, 0.0, 0.06))) == ["Death-overs specialist"]
    assert C.phase_words(_card(phase_bat=(0.06, 0.0, 0.0), phase_bowl=None)) == ["Powerplay hitter"]
    assert C.phase_words(_card(phase_bowl=(0.0, 0.06, 0.0))) == []
    assert C.phase_words(_card(phase_bowl=(0.0, 0.0, 0.04))) == []


def test_a_legend_brings_no_lean_into_a_room(deck, model):
    from web import career_results as R
    setup = C.Setup("N", "pace", 2010)
    car, _ = C.auto_career(deck, model, 2, setup, "loyal")
    leg = R.legend(car)
    assert leg is not None
    assert leg["card"]["phase_bat"] is None and leg["card"]["phase_bowl"] is None


def test_the_lean_reaches_the_engine_through_to_player(model):
    from game.__main__ import to_player
    p = to_player(_card(phase_bat=(0.01, 0.0, 0.02), phase_bowl=(0.0, 0.0, 0.05)), model)
    assert p.phase_bat == (0.01, 0.0, 0.02) and p.phase_bowl == (0.0, 0.0, 0.05)
    assert to_player(_card(), model).phase_bowl is None
