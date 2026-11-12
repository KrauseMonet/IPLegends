"""A focus each pre-season, the coach's challenge, and mastered skills in Build a Career (A285)."""

import dataclasses
import random
from types import SimpleNamespace as NS

import pytest

from game import career as C
from game import career_focus as F
from game import career_skills as CS
from game import career_traits as ct
from tools.snapshot_deck import deck_from, model_from, read_document

FOCUS_TESTS = True          # conftest leaves the focus on for this module
DRILL_TESTS = True          # ...and the drills and phase work, which a focus builds
PHASE_TESTS = True
DOC = read_document()
pytestmark = [pytest.mark.skipif(DOC is None, reason="no deck snapshot"),
              pytest.mark.usefixtures("no_incidents")]


@pytest.fixture(scope="module", autouse=True)
def focus_on():
    """On for the module before any module-scoped career below is built (conftest has it off
    for the session)."""
    was, F.FOCUS_ON = F.FOCUS_ON, True
    yield
    F.FOCUS_ON = was


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


YORKER_SEED = 1


def _m(**kw):
    m = dict.fromkeys(F.FIELDS, 0)
    m.update(kw)
    return m


# --- the challenges, one match at a time -----------------------------------------------------

@pytest.mark.parametrize("key,yes,no", [
    ("spin", _m(spin_runs=25), _m(spin_runs=24, pace_runs=60)),
    ("pace", _m(pace_runs=30), _m(pace_runs=29, spin_runs=60)),
    ("burn", _m(faced=30), _m(faced=29)),
    ("anchor", _m(faced=15), _m(faced=40, out=True)),
    ("anchor", _m(faced=15), _m(faced=14)),
    ("aggressor", _m(faced=12, runs=21), _m(faced=12, runs=20)),
    ("aggressor", _m(faced=12, runs=21), _m(faced=11, runs=40)),
    ("pp_bat", _m(pp_runs=25), _m(pp_runs=24, death_runs=60)),
    ("death_bat", _m(death_runs=20), _m(death_runs=19, pp_runs=60)),
    ("yorker", _m(death_wkts=1), _m(wkts=4)),
    ("new_ball", _m(pp_bowled=12, pp_conceded=11), _m(pp_bowled=12, pp_conceded=12)),
    ("new_ball", _m(pp_bowled=12, pp_conceded=11), _m(pp_bowled=11, pp_conceded=0)),
    ("death_ball", _m(death_bowled=12, death_conceded=17), _m(death_bowled=12, death_conceded=18)),
    ("strike", _m(wkts=3), _m(wkts=2)),
    ("miser", _m(bowled=24, conceded=27), _m(bowled=24, conceded=28)),
    ("miser", _m(bowled=24, conceded=27), _m(bowled=23, conceded=0)),
])
def test_each_challenge_is_met_on_its_own_figure_and_only_that(key, yes, no):
    assert F.progress(key, [yes, no]) == [True, False]


def test_every_focus_works_toward_a_skill_the_engine_plays():
    for f in F.FOCI:
        assert f.skill in CS.S.SKILL_BY_KEY, f.key
        assert f.skill not in CS.NOT_PLAYED, f.key
        assert f.target >= 2


def test_the_menu_fits_how_you_play():
    bat = F.available("batter", "top", False, None)
    assert "pp_bat" in bat and "death_bat" not in bat
    assert not {"yorker", "new_ball", "strike", "miser", "death_ball"} & set(bat)
    assert "death_bat" in F.available("batter", "finisher", False, None)
    pace = F.available("bowler", "finisher", True, "pace")
    assert {"yorker", "death_ball", "new_ball", "strike", "miser"} <= set(pace)
    assert not {"spin", "pace", "anchor", "aggressor", "pp_bat", "death_bat"} & set(pace)
    spin = F.available("bowler", "finisher", True, "spin")
    assert "yorker" not in spin and "death_ball" not in spin and "new_ball" in spin
    ar = F.available("allrounder", "middle", True, "pace")
    # one temper for both disciplines: an all-rounder's temper is a batting focus only
    assert "anchor" in ar and "strike" not in ar and "miser" not in ar and "yorker" in ar


def test_a_step_is_full_when_met_half_when_missed_and_halved_again_on_a_new_focus():
    assert F.step(True, False) == 1.0
    assert F.step(False, False) == 0.5
    assert F.step(True, True) == 0.5
    assert F.step(False, True) == 0.25


def test_a_missed_phase_challenge_builds_half_a_step_of_phase_work():
    """phase_after scales with the step: half the gain where you worked, half the cost in the
    other two phases (stated, not read from the constants: 0.03 and 0.01)."""
    half = C.phase_after({}, "bat", 0, 0.5)["bat"]
    full = C.phase_after({}, "bat", 0)["bat"]
    assert full == pytest.approx([0.03, -0.01, -0.01])
    assert half == pytest.approx([0.015, -0.005, -0.005])
    near = C.phase_after({"bat": [0.08, 0, 0]}, "bat", 0, 1.0)["bat"]
    assert near[0] == pytest.approx(0.09)          # never past the cap


def test_a_lever_built_to_its_limit_is_out_of_the_menu():
    assert F.built_out("spin", {"spin": 2.0}, {}, {}, 2.0, 0.09, 3.0)
    assert not F.built_out("spin", {"spin": 1.5}, {}, {}, 2.0, 0.09, 3.0)
    assert F.built_out("death_bat", {}, {"bat": [0, 0, 0.09]}, {}, 2.0, 0.09, 3.0)
    assert not F.built_out("pp_bat", {}, {"bat": [0, 0, 0.09]}, {}, 2.0, 0.09, 3.0)
    assert F.built_out("anchor", {}, {}, {"temper": -3.0}, 2.0, 0.09, 3.0)
    assert not F.built_out("aggressor", {}, {}, {"temper": -3.0}, 2.0, 0.09, 3.0)


def test_the_tuned_foci_build_two_steps_a_season_and_the_rest_one():
    """Stated, not read from the table (A207): yorker, new ball and strike were too weak at
    one step a season and were tuned to two; every other focus builds one."""
    two = {"yorker", "new_ball", "strike"}
    for f in F.FOCI:
        assert F.judge(f.key, [], False).built == (1.0 if f.key in two else 0.5)
    yes = [_m(death_wkts=1)] * 5
    assert F.judge("yorker", yes, False).built == 2.0
    assert F.judge("yorker", yes, True).built == 1.0


def test_the_tuned_foci_may_build_their_lever_twice_as_far():
    assert not F.built_out("yorker", {"death_wkts": 2.0}, {}, {}, 2.0, 0.09, 3.0)
    assert F.built_out("yorker", {"death_wkts": 4.0}, {}, {}, 2.0, 0.09, 3.0)
    assert not F.built_out("new_ball", {}, {"bowl": [0.09, 0, 0]}, {}, 2.0, 0.09, 3.0)
    assert F.built_out("new_ball", {}, {"bowl": [0.18, 0, 0]}, {}, 2.0, 0.09, 3.0)
    assert F.built_out("death_ball", {}, {"bowl": [0, 0, 0.09]}, {}, 2.0, 0.09, 3.0)
    assert F.built_out("spin", {"spin": 2.0}, {}, {}, 2.0, 0.09, 3.0)
    capped = C.phase_after({"bowl": [0.17, 0, 0]}, "bowl", 0, 2.0, cap=0.18)["bowl"]
    assert capped[0] == pytest.approx(0.18)


def test_a_yorker_focus_builds_its_drill_past_the_usual_cap(deck, model):
    car, _ = _focus_career(deck, model, YORKER_SEED, C.Setup("T", "pace", 2010), "yorker")
    built = [r.drills_built.get("death_wkts", 0.0) for r in car.seasons]
    assert max(built) > 2.0, "the fixture must build past one cap"
    assert max(built) <= 4.0


def test_the_menu_puts_last_seasons_focus_first_and_no_focus_last():
    card = dataclasses.replace(next(iter(deck_from(DOC).cards_by_fs.values()))[0],
                               role="batter", positions=frozenset({3, 4, 5}),
                               bowl_scoring=None, bowling_style=None)
    cp = C.Checkpoint(k=1, permanent=0.0, current=None, last_display=None, squad=False,
                      seasons=[], focus="burn")
    menu = C.focus_menu(card, cp)
    assert menu[0] == "burn" and menu[-1] == "" and menu.count("") == 1
    cp.drills = {"burn": C.DRILL_MAX}
    assert "burn" not in C.focus_menu(card, cp)


# --- counting a season's matches -------------------------------------------------------------

def _ball(who_bats, who_bowls, style="spin"):
    striker = NS(player=NS(person_id=who_bats), balls=0)
    bowler = NS(player=NS(person_id=who_bowls, bowling_style=style))
    return striker, bowler


def test_the_counter_keeps_your_figures_match_by_match_and_nobody_elses():
    c = F.Counter("me")
    c(0, 0, *_ball("me", "x"), 5, None)          # before any match: ignored
    c.next_match()
    c(0, 0, *_ball("me", "x", "spin"), 5, None)  # a four off spin in the powerplay
    c(15, 0, *_ball("me", "x", "pace"), 7, None)  # a six off pace in the 16th over: the death
    c(16, 0, *_ball("me", "x", "pace"), 0, None)  # out
    c(3, 0, *_ball("other", "x"), 7, None)        # somebody else's six
    c.next_match()
    c(17, 0, *_ball("other", "me"), 0, None)      # you take a wicket at the death
    c(5, 0, *_ball("other", "me"), 2, None)       # a single off you in the 6th: the powerplay
    c(6, 0, *_ball("other", "me"), 2, None)       # ...and one in the 7th, which is not
    a, b = c.matches
    assert (a["faced"], a["runs"], a["out"]) == (3, 10, True)
    assert (a["spin_runs"], a["pace_runs"], a["pp_runs"], a["death_runs"]) == (4, 6, 4, 6)
    assert a["bowled"] == 0
    assert (b["bowled"], b["wkts"], b["death_wkts"], b["death_bowled"]) == (3, 1, 1, 1)
    assert (b["pp_bowled"], b["pp_conceded"], b["conceded"]) == (1, 1, 2)
    assert b["faced"] == 0


def _focus_career(deck, model, seed, setup, focus, policy="strength"):
    """A career that always picks `focus` (or the menu's first if it is not offered)."""
    moves, rng = [], random.Random(seed)
    while True:
        car = C.play_career(deck, model, seed, setup, moves)
        if car.complete:
            return car, moves
        p = car.pending
        if p.kind == "focus":
            opts = p.focus["options"]
            moves.append(f"f{opts.index(focus)}" if focus in opts else "f0")
        else:
            moves.append(C.policy_move(car, policy, rng))


@pytest.fixture(scope="module")
def spin_career(deck, model):
    return _focus_career(deck, model, 3, C.Setup("T", "middle", 2012), "spin")


def test_the_counter_agrees_with_the_scorecards(deck, model, monkeypatch):
    """Every match's runs and wickets, summed, are the season's own -- the counter reads the
    same balls the scorecards do -- and its matches line up one to one with your feed."""
    seen = []
    real = F.judge

    def spy(key, matches, switched):
        seen.append([dict(m) for m in matches])
        return real(key, matches, switched)

    monkeypatch.setattr(F, "judge", spy)
    setup, moves, rng = C.Setup("T", "allrounder", 2012), [], random.Random(1)
    while True:
        car = C.play_career(deck, model, 1, setup, moves)
        if car.seasons:
            break
        moves.append("f0" if car.pending.kind == "focus" else C.policy_move(car, "strength", rng))
    r = car.seasons[0]
    (matches,) = seen
    assert sum(m["runs"] for m in matches) == r.runs
    assert sum(m["wkts"] for m in matches) == r.wickets
    assert sum(m["conceded"] for m in matches) <= r.bowl_runs      # extras are the scorecard's
    assert len(matches) == len(car.detail.feed) == len(r.focus["hits"])
    assert r.runs > 0 and r.wickets > 0


def test_the_season_record_keeps_the_verdict_not_the_matches(spin_career):
    car, _ = spin_career
    for r in car.seasons:
        assert r.focus_log == []
        if r.focus:
            assert set(r.focus) == {"focus", "met", "target", "done", "built", "switched", "hits"}


def test_a_drill_focus_builds_its_drill_by_what_the_challenge_earned(spin_career):
    car, _ = spin_career
    built = 0.0
    for r in car.seasons:
        assert r.drills_built.get("spin", 0.0) == pytest.approx(min(C.DRILL_MAX, built))
        f = r.focus
        if f and f["focus"] == "spin":
            expect = F.step(f["met"] >= f["target"], f["switched"])
            assert f["built"] == expect
            assert f["done"] == (f["met"] >= f["target"])
            assert f["met"] == sum(f["hits"])
            built += f["built"]
    assert built > 0


def test_a_temper_focus_moves_temper_and_a_phase_focus_builds_the_phase(deck, model):
    car, _ = _focus_career(deck, model, 4, C.Setup("T", "opener", 2012), "anchor")
    r = next(r for r in car.seasons if r.focus and r.focus["focus"] == "anchor")
    assert ["temper", -r.focus["built"]] in r.trait_moves
    car2, _ = _focus_career(deck, model, 4, C.Setup("T", "opener", 2012), "pp_bat")
    first = next(i for i, r in enumerate(car2.seasons) if r.focus and r.focus["focus"] == "pp_bat")
    nxt = car2.seasons[first + 1]
    want = C.phase_after(car2.seasons[first].phase_built, "bat", 0, car2.seasons[first].focus["built"])
    assert nxt.phase_built["bat"] == pytest.approx(want["bat"])


def test_switching_focus_is_marked_and_costs_half(deck, model):
    setup = C.Setup("T", "middle", 2012)
    moves, rng = [], random.Random(0)
    picks = iter(["spin", "burn"])
    car = None
    while True:
        car = C.play_career(deck, model, 6, setup, moves)
        if car.complete or len(car.seasons) >= 2:
            break
        p = car.pending
        if p.kind == "focus":
            want = next(picks)
            moves.append(f"f{p.focus['options'].index(want)}")
        else:
            moves.append(C.policy_move(car, "strength", rng))
    a, b = car.seasons[:2]
    assert a.focus["focus"] == "spin" and not a.focus["switched"]
    assert b.focus["focus"] == "burn" and b.focus["switched"]
    assert b.focus["built"] == F.step(b.focus["done"], True)


def test_no_focus_builds_nothing(deck, model):
    car, _ = _focus_career(deck, model, 3, C.Setup("T", "middle", 2012), "")
    assert all(r.focus is None for r in car.seasons)
    assert all(not r.drills_built for r in car.seasons)


def test_a_bad_focus_move_is_refused(deck, model):
    setup = C.Setup("T", "middle", 2012)
    with pytest.raises(C.CareerError):
        C.play_career(deck, model, 3, setup, ["o0", "f99"])
    with pytest.raises(C.CareerError):
        C.play_career(deck, model, 3, setup, ["o0", "e0"])


def test_an_older_checkpoint_reads_with_no_focus():
    cp = C.Checkpoint(k=0, permanent=0.0, current=None, last_display=None, squad=False, seasons=[])
    d = cp.to_dict()
    d.pop("focus")
    assert C.Checkpoint.from_dict(d).focus == ""


# --- mastered -------------------------------------------------------------------------------

def _rec(year, *keys):
    return NS(year=year, skills=[[k, CS.S.SKILL_BY_KEY[k].tier, 1.0] for k in keys])


def test_a_skill_is_mastered_in_the_season_it_is_shown_for_the_fourth_time():
    seasons = [_rec(2010, "anchor"), _rec(2011, "anchor", "miser"), _rec(2012),
               _rec(2013, "anchor", "miser"), _rec(2014, "miser"), _rec(2015, "anchor"),
               _rec(2016, "miser")]
    assert CS.mastered(seasons) == [["anchor", 1, 2015], ["miser", 2, 2016]]
    assert CS.mastered(seasons[:6]) == [["anchor", 1, 2015]]


def test_mastery_needs_four_seasons_not_three():
    # stated, not read from CS.MASTER_SEASONS, so a changed constant fails here (A207)
    three = [_rec(2010 + i, "anchor") for i in range(3)]
    assert CS.mastered(three) == []
    assert CS.mastered(three + [_rec(2013, "anchor")]) == [["anchor", 1, 2013]]


def test_a_saved_card_reads_mastery_off_its_signature():
    assert CS.mastered_from_signature([["anchor", 1, 4], ["miser", 2, 3]]) == [["anchor", 1, None]]


def test_mastery_is_a_milestone_once_in_the_season_it_happens(spin_career):
    car, _ = spin_career
    want = {(k, y) for k, _t, y in CS.mastered(car.seasons)}
    got = {(m.text.removeprefix("Mastered: "), r.year) for r in car.seasons
           for m in r.milestones if m.key == "mastered"}
    assert got == {(CS.label(k), y) for k, y in want}
