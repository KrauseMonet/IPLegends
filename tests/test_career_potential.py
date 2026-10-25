"""Build a Career: a potential that grows, a hidden career shape, and a clock form moves (A224)."""

from __future__ import annotations

import json
import random
from collections import Counter

import pytest

from game import career as C
from tools.snapshot_deck import deck_from, model_from, read_document

DOC = read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="no deck snapshot")


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


# --- the shape --------------------------------------------------------------------------------


def test_the_standard_shape_is_the_arc_exactly():
    for k, v in enumerate(C.ARC):
        assert C.STANDARD.arc(k) == v
    assert C.STANDARD.arc(len(C.ARC) + 3) == C.ARC[-1]          # past the table: the floor
    for k in range(len(C.ARC)):
        assert C.STANDARD.growing(k) == (k < C.DEV_SEASONS)    # the old development window


@pytest.mark.parametrize("peak", list(C.PEAK_SEASONS))
def test_each_shape_peaks_when_it_says_and_rises_until_then(peak):
    s = C.Shape(peak, "standard")
    r = C.PEAK_SEASONS[peak]
    assert s.arc(0) == 0.0
    assert s.arc(r) == s.arc(r + 1) == 1.0                      # a season at the peak
    assert s.arc(r + 2) < 1.0                                   # then the decline
    rising = [s.arc(c / 4) for c in range(4 * r + 1)]
    assert rising == sorted(rising)
    assert s.growing(r + 0.99) and not s.growing(r + 1)


def test_a_steep_decline_falls_faster_than_a_gentle_one():
    for c in (7.5, 9, 11, 13):
        steep, std, gentle = (C.Shape("standard", d).arc(c) for d in ("steep", "standard", "gentle"))
        assert steep < std < gentle
    assert C.Shape("standard", "steep").arc(40) == C.ARC[-1]      # and every decline floors


def test_shapes_are_drawn_from_the_seed_in_the_declared_proportions():
    assert C.draw_shape(77) == C.draw_shape(77)
    shapes = [C.draw_shape(s) for s in range(4000)]
    peaks, declines = Counter(s.peak for s in shapes), Counter(s.decline for s in shapes)
    for counts, weights in ((peaks, C.PEAK_WEIGHTS), (declines, C.DECLINE_WEIGHTS)):
        total = sum(weights.values())
        for key, w in weights.items():
            assert abs(counts[key] / 4000 - w / total) < 0.03
    assert len(set(shapes)) == 9


# --- the potential ----------------------------------------------------------------------------


def _spun(deck, role="opener", below=88):
    """A spin with a ceiling that has room to grow."""
    for seed in range(500):
        start, ceiling = C.spin(deck, seed, role)
        legend = C.potential_legend(deck, role, ceiling)
        if ceiling.display < below and legend is not None:
            return start, ceiling, legend
    raise AssertionError("no spin with room")


def test_the_legend_is_better_than_the_ceiling_on_the_card_and_at_the_job(deck):
    for role in C.ROLES:
        _, ceilings = C.template_pools(deck, role)
        for ceiling in ceilings[:40]:
            legend = C.potential_legend(deck, role, ceiling)
            if legend is None:
                assert not any(c.display > ceiling.display and
                               C.main_rating(c, role) >= C.main_rating(ceiling, role)
                               for c in ceilings)
                continue
            assert legend.display > ceiling.display
            assert C.main_rating(legend, role) >= C.main_rating(ceiling, role)


def test_the_room_to_grow_is_capped_and_never_passes_99(deck):
    start, ceiling, legend = _spun(deck)
    room = C.potential_room(ceiling, legend)
    assert room == min(C.POTENTIAL_MAX, legend.display - ceiling.display)
    assert C.potential_room(ceiling, None) == 0
    for role in C.ROLES:
        for c in C.template_pools(deck, role)[1]:
            assert c.display + C.potential_room(c, C.potential_legend(deck, role, c)) <= 99


def test_the_top_card_moves_toward_the_legend_and_never_past_it(deck):
    _, ceiling, legend = _spun(deck)
    assert C.top_card(ceiling, legend, 0) is ceiling
    gap = legend.display - ceiling.display
    half = C.top_card(ceiling, legend, gap / 2)
    assert half.display == ceiling.display + gap / 2
    for f in ("bat_scoring", "bowl_scoring", "bat", "bowl"):
        a, b, m = getattr(ceiling, f), getattr(legend, f), getattr(half, f)
        if a is not None and b is not None:
            assert m == pytest.approx((a + b) / 2)
    if ceiling.bat_dismissal and legend.bat_dismissal:
        assert half.bat_dismissal == pytest.approx((ceiling.bat_dismissal * legend.bat_dismissal) ** 0.5)
    full = C.top_card(ceiling, legend, 3 * gap)
    assert full.bat_scoring == pytest.approx(legend.bat_scoring)   # all the way, no further


def _cp(permanent=0.0, potential=0.0, clock=3.0):
    return C.Checkpoint(permanent=permanent, potential=potential, clock=clock)


@pytest.mark.parametrize("over,step", [(2, 0), (3, C.POTENTIAL_STEP), (7, C.POTENTIAL_STEP),
                                       (8, C.POTENTIAL_BIG_STEP), (15, C.POTENTIAL_BIG_STEP)])
def test_a_season_must_beat_the_potential_by_the_margin(deck, over, step):
    start, ceiling, legend = _spun(deck)
    cp = _cp()
    note = C.grow_potential(cp, ceiling.display + over, True, start, ceiling, legend, C.STANDARD)
    assert cp.potential == step
    assert (note is not None) == bool(step)
    if step:
        assert note == C.potential_words(ceiling.display, ceiling.display + step, ceiling.display + over)


def test_nothing_grows_once_you_are_past_your_peak(deck):
    start, ceiling, legend = _spun(deck)
    cp = _cp(permanent=0.4, clock=8.0)
    assert C.grow_potential(cp, 99, False, start, ceiling, legend, C.STANDARD) is None
    assert cp.potential == 0 and cp.permanent == 0.4


def test_the_potential_stops_at_its_room(deck):
    start, ceiling, legend = _spun(deck)
    room = C.potential_room(ceiling, legend)
    cp = _cp(potential=room - 0.5)
    C.grow_potential(cp, 99, True, start, ceiling, legend, C.STANDARD)
    assert cp.potential == room
    C.grow_potential(cp, 99, True, start, ceiling, legend, C.STANDARD)
    assert cp.potential == room


def test_points_for_good_the_peak_would_swallow_move_into_the_potential(deck):
    """Next season at the peak (ARC 1.0) with 0.3 of ability for good: all 0.3 is above the
    cap, so it becomes potential in Rating points, and nothing is left for good."""
    start, ceiling, legend = _spun(deck)
    gap = ceiling.display - start.display
    cp = _cp(permanent=0.3, clock=5.0)
    note = C.grow_potential(cp, 0, True, start, ceiling, legend, C.STANDARD)
    assert cp.potential == pytest.approx(0.3 * gap)
    assert cp.permanent == pytest.approx(0.0)
    assert note is None or "carried you past your potential" in note


def test_only_the_swallowed_part_moves_and_the_rest_keeps_its_rating_points(deck):
    """At ARC 0.9 with 0.3 for good, 0.2 is above the cap: that moves, and the 0.1 left keeps
    the same Rating points on the bigger gap."""
    start, ceiling, legend = _spun(deck)
    gap = ceiling.display - start.display
    cp = _cp(permanent=0.3, clock=4.0)
    C.grow_potential(cp, 0, True, start, ceiling, legend, C.STANDARD)
    moved = 0.2 * gap
    assert cp.potential == pytest.approx(moved)
    new_gap = C.top_card(ceiling, legend, cp.potential).display - start.display
    assert cp.permanent * new_gap == pytest.approx(0.1 * gap)


def test_a_negative_change_never_moves_into_the_potential(deck):
    start, ceiling, legend = _spun(deck)
    cp = _cp(permanent=-0.2, clock=5.0)
    assert C.grow_potential(cp, 0, True, start, ceiling, legend, C.STANDARD) is None
    assert cp.potential == 0 and cp.permanent == -0.2


# --- the clock, in played careers -------------------------------------------------------------


AGE = {(e.key, o.label): o.age for e in C.EVENTS for o in e.options}


# Found by search: one of each kind of shape, between them a slowed and a sped-up clock.
CLOCK_FIXTURES = ((9, C.Shape("early", "steep")), (34, C.Shape("late", "gentle")),
                  (16, C.Shape("standard", "standard")))


@pytest.fixture(scope="module")
def careers(deck, model):
    out = []
    for seed, shape in CLOCK_FIXTURES:
        car, moves = C.auto_career(deck, model, seed, C.Setup("X", "opener", 2008, False), "random")
        assert car.shape == shape
        out.append((seed, car, moves))
    return out


def test_the_clock_runs_a_season_a_season_unless_form_or_a_choice_moves_it(careers):
    moved = Counter()
    for _, car, _ in careers:
        poor = C.poor_bar("opener")
        for r, nxt in zip(car.seasons, car.seasons[1:]):
            step = 1.0 + AGE[(r.event, r.choice)]
            if car.shape.declining(r.clock):
                if r.form >= r.rating + C.CLOCK_MARGIN:
                    step += C.CLOCK_SLOW - 1
                    moved["slow"] += 1
                elif r.percentile < poor:
                    step += C.CLOCK_FAST - 1
                    moved["fast"] += 1
            assert nxt.clock == pytest.approx(r.clock + step), (r.year, r.clock, nxt.clock)
    assert moved["slow"] and moved["fast"], "the fixtures must exercise both"


def test_the_season_card_names_what_moved_the_clock_and_the_shape(careers):
    """Each hint once, on the season before the one it describes -- stated here from the
    shape's own numbers rather than by calling `shape_words`, which would agree with itself."""
    for _, car, _ in careers:
        rise = C.PEAK_SEASONS[car.shape.peak]
        early = [r.k for r in car.seasons if any("already close to your best" in n for n in r.notes)]
        late = [r.k for r in car.seasons if any("Still improving at" in n for n in r.notes)]
        past = [r.k for r in car.seasons if any(n.startswith("Past your peak now") for n in r.notes)]
        assert early == ([rise - 1] if car.shape.peak == "early" else [])
        assert late == ([C.PEAK_SEASONS["standard"] - 1] if car.shape.peak == "late" else [])
        assert len(past) == 1 and car.seasons[past[0]].clock <= rise + 1 < car.seasons[past[0] + 1].clock
        for r, nxt in zip(car.seasons, car.seasons[1:]):
            slow = C.clock_words(C.CLOCK_SLOW) in r.notes
            fast = C.clock_words(C.CLOCK_FAST) in r.notes
            assert not (slow and fast)
            if not car.shape.declining(r.clock):
                assert not slow and not fast


def test_each_seasons_rating_is_read_on_its_own_shape_and_potential(careers):
    for _, car, _ in careers:
        for r in car.seasons:
            top = C.top_card(car.ceiling, car.legend, r.potential)
            assert r.rating == C.rating_at(car.start, top, r.ability)


def test_a_raised_potential_is_said_on_the_season_that_raised_it(careers):
    for _, car, _ in careers:
        for r, nxt in zip(car.seasons, car.seasons[1:]):
            before = round(car.ceiling.display + r.potential)
            after = round(car.ceiling.display + nxt.potential)
            said = [n for n in r.notes if "potential" in n and "raised to" in n]
            assert bool(said) == (after != before), (r.year, before, after, r.notes)
            if said:
                assert said[0].endswith(f"raised to {after}.")
            assert nxt.potential >= r.potential                 # a potential never falls


def test_a_career_from_before_the_shapes_keeps_the_standard_curve(deck, model):
    """A token saved before A224 carries no clock: its clock picks up at its season count and
    its shape stays standard, rather than changing shape halfway through."""
    seed = next(s for s in range(2000) if C.draw_shape(s) != C.STANDARD)
    setup = C.Setup("X", "opener", 2010, False)
    moves = ["o0"]
    car = C.play_career(deck, model, seed, setup, moves)
    while not (car.pending.kind == "offer" and car.seasons):
        moves.append(C.policy_move(car, "loyal", random.Random(0)))
        car = C.play_career(deck, model, seed, setup, moves)
    old = car.checkpoint.to_dict()
    for key in ("clock", "potential", "shaped"):
        old.pop(key)
    for r in old["seasons"]:
        r.pop("potential"), r.pop("clock")
    cp = C.Checkpoint.from_dict(json.loads(json.dumps(old)))
    assert cp.clock == cp.k and cp.potential == 0 and not cp.shaped
    resumed = C.play_career(deck, model, seed, setup, [], resume=cp)
    assert resumed.shape == C.STANDARD
    fresh = C.play_career(deck, model, seed, setup, [], resume=car.checkpoint)
    assert fresh.shape == C.draw_shape(seed) != C.STANDARD


# --- events -----------------------------------------------------------------------------------


def test_the_fitness_programme_is_only_offered_to_a_senior_player():
    seen = [k for s in range(400) for k, e in enumerate(C.event_sequence(s, False, "opener"))
            if e.key == "fitness"]
    assert seen and min(seen) == C.VETERAN_FROM


def test_an_answer_that_moves_the_clock_says_so():
    fitness = next(e for e in C.EVENTS if e.key == "fitness")
    assert fitness.options[0].age < 0
    assert "ageing held back" in C._effect_words("x", 0, 0, 0, fitness.options[0].age)
    managed = next(o for e in C.EVENTS if e.key == "fracture" for o in e.options if o.age)
    assert "older" in C._effect_words("x", 0, managed.permanent, 0, managed.age)
