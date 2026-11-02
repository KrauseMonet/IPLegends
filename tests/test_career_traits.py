"""Build a Career's traits and card sense checks (A245)."""

import dataclasses
import math

import pytest

from game import career as C
from game import career_incidents as I
from game import career_traits as ct
from tools.snapshot_deck import deck_from, model_from, read_document

DOC = read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="no deck snapshot")


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


def _card(**kw):
    base = dict(fs_id=1, person_id="x", name="X", bat=0.0, bowl=0.0, band="top_order",
                role="allrounder", keeper_eligible=False, season_year=2019, franchise="F",
                overseas=False, bowling_style="pace", display=80, positions=frozenset({3}),
                bat_scoring=0.1, bat_dismissal=1.0, bowl_scoring=0.05, bowl_dismissal=1.0)
    base.update(kw)
    return C.Card(**base)


# --- the traits themselves ---------------------------------------------------------------

def test_nudge_clamps_and_does_not_change_its_input():
    t = ct.empty()
    out = ct.nudge(t, [("work", 5), ("temper", -1.5)])
    assert out["work"] == ct.TRAIT_MAX and out["temper"] == -1.5
    assert t == ct.empty()
    with pytest.raises(ValueError):
        ct.nudge(t, [("luck", 1)])


def test_level_rounds_toward_zero():
    assert [ct.level(x) for x in (0.9, -0.9, 1.0, -2.5, 1 / 3 * 3)] == [0, 0, 1, -2, 1]


def test_zero_traits_change_nothing():
    c = _card()
    assert ct.styled(c, ct.empty()) == c
    assert ct.big_match(c, ct.empty()) == c
    assert ct.styled(c, None) == c


def test_temper_is_the_measured_trade_off():
    """Aggression: a batter scores faster AND gets out more; a bowler takes more wickets AND
    concedes more. By TRADE_SLOPE, as a key-moment call is (A204)."""
    c = _card()
    hot = ct.styled(c, {"temper": 2})
    d = 2 * ct.TEMPER_STEP
    assert hot.bat_scoring == pytest.approx(c.bat_scoring + d)
    assert hot.bat_dismissal == pytest.approx(math.exp(C.TRADE_SLOPE * d))
    assert hot.bowl_scoring == pytest.approx(c.bowl_scoring - d)
    assert hot.bowl_dismissal == pytest.approx(math.exp(C.TRADE_SLOPE * d))
    calm = ct.styled(c, {"temper": -2})
    assert calm.bat_scoring < c.bat_scoring and calm.bat_dismissal < 1
    assert ct.TRADE_SLOPE == C.TRADE_SLOPE


def test_nerve_is_better_at_everything_only_in_big_matches():
    c = _card()
    steely = ct.big_match(c, {"nerve": 3})
    assert steely.bat_scoring > c.bat_scoring and steely.bat_dismissal < 1
    assert steely.bowl_scoring > c.bowl_scoring and steely.bowl_dismissal > 1
    nervy = ct.big_match(c, {"nerve": -3})
    assert nervy.bat_scoring < c.bat_scoring and nervy.bowl_dismissal < 1


def test_a_mentors_style_comes_from_his_own_numbers():
    """Projected on the trade-off: fast scoring that gets out a lot reads aggressive; a
    bowler who takes wickets and leaks reads aggressive; a miser reads patient."""
    assert ct.style_from(0.3, 1.4, bowling=False) == 1
    assert ct.style_from(-0.2, 0.7, bowling=False) == -1
    assert ct.style_from(-0.2, 1.4, bowling=True) == 1      # leaks, strikes
    assert ct.style_from(0.2, 0.75, bowling=True) == -1     # saves runs, rarely strikes
    assert ct.style_from(0.0, 1.0, bowling=False) == 0


def test_changes_report_whole_levels_only():
    assert ct.changes(ct.empty(), ct.nudge(ct.empty(), [("temper", 0.5)])) == []
    said = ct.changes(ct.empty(), ct.nudge(ct.empty(), [("work", 1), ("temper", 1)]))
    assert ("You are playing with more aggression.", "") in said
    assert ("Your work ethic is making you a better player.", "up") in said


def test_words_never_carry_numbers():
    for t in ct.TRAITS:
        for lv in range(-3, 4):
            for w in ct.words({t: lv}) + ct.words({t: lv}, bowler=True):
                assert not any(ch.isdigit() for ch in w)


def test_work_takes_you_past_the_ceiling():
    """The cap holds the arc; work is added after it, so it is the way past the potential."""
    start, top = _card(display=72), _card(display=84)
    cp = C.Checkpoint(clock=5.0, traits={"work": 3})
    t = C.career_t(cp, start, top)
    assert t > C.ABILITY_CAP
    assert C.rating_at(start, top, t) == 84 + round(3 * ct.WORK_RATING)
    assert C.career_t(C.Checkpoint(clock=5.0), start, top) == C.ABILITY_CAP


# --- every choice does something ----------------------------------------------------------

def _effect(o) -> bool:
    fields = ("season", "permanent", "standing", "missed", "age", "money", "trade", "retire",
              "captain", "out", "form", "death", "thread", "injury", "turns", "outcomes",
              "traits")
    return (any(bool(getattr(o, f, None)) for f in fields)
            or getattr(o, "approach", None) is not None)


def _cards():
    for e in list(C.EVENTS) + list(C.FOLLOW_UPS.values()):
        yield e.key, e.options
    for i in I.INCIDENTS.values():
        yield i.key, i.options
        if i.captain_options:
            yield i.key + " (captain)", i.captain_options


@pytest.mark.parametrize("key,options", list(_cards()))
def test_every_choice_has_a_lasting_effect_or_says_it_protects_something(key, options):
    if len(options) == 1:
        return                                  # not a choice
    for o in options:
        assert _effect(o) or o.neutral, f"{key}: {o.label!r} does nothing"
        if o.neutral:
            assert not _effect(o), f"{key}: {o.label!r} is marked neutral but has an effect"
    assert sum(not o.neutral for o in options) >= 1, f"{key}: no option does anything"


def test_the_easy_way_out_costs_something():
    """All-wrong choices must be able to cost a player, not only fail to help him: several
    cards' comfortable answers move a trait the wrong way."""
    downs = [o for _, opts in _cards() for o in opts
             if any(t != "temper" and d < 0 for t, d in o.traits)]
    assert len(downs) >= 6


# --- cards that make sense --------------------------------------------------------------

def test_a_captain_is_never_asked_to_back_the_captain():
    row = I.INCIDENTS["row"]
    assert any("captain" in o.label.lower() for o in row.options)
    mine = row.for_captain(True)
    assert not any("captain" in (o.label + o.hint).lower() for o in mine.options)
    assert "your dressing room" in mine.text.lower()
    assert row.for_captain(False) is row


@pytest.mark.parametrize("key", ["collapse", "death"])
def test_nobody_tells_the_captain_what_the_captain_asks(key):
    inc = I.INCIDENTS[key].for_captain(True)
    assert "captain asks" not in inc.text.lower()
    assert all("he asked you" not in o.hint.lower() for o in inc.options)


def test_the_form_card_names_whom_you_answer_to():
    e = next(e for e in C.EVENTS if e.key == "form")
    assert C.fill(e.options[0].label, boss="coach") == "Make your case to the coach"
    assert C.fill(e.options[0].label) == "Make your case to the captain"


def _mentor():
    return next(e for e in C.EVENTS if e.key == "mentor")


def test_a_mentor_is_for_a_young_player_still_growing():
    ok = C.EventContext(k=3, growing=True, rating=78, mentor_ok=True)
    assert C.event_fits(_mentor(), ok)
    for bad in (dataclasses.replace(ok, growing=False),
                dataclasses.replace(ok, k=C.MENTOR_UNTIL + 1),
                dataclasses.replace(ok, rating=C.MENTOR_MAX_RATING),
                dataclasses.replace(ok, mentor_ok=False)):
        assert not C.event_fits(_mentor(), bad), bad


def test_nobody_rebuilds_a_great_players_technique():
    coach = next(e for e in C.EVENTS if e.key == "coach")
    ok = C.EventContext(k=3, rating=80)
    assert C.event_fits(coach, ok)
    assert not C.event_fits(coach, dataclasses.replace(ok, k=C.VETERAN_FROM))
    assert not C.event_fits(coach, dataclasses.replace(ok, rating=C.REBUILD_MAX_RATING))


def test_a_captain_is_not_offered_the_captaincy():
    cap = next(e for e in C.EVENTS if e.key == "captain")
    assert C.event_fits(cap, C.EventContext())
    assert not C.event_fits(cap, C.EventContext(captain=True))


@pytest.mark.parametrize("policy", ["virtue", "vice", "random"])
@pytest.mark.parametrize("role,seed", [("opener", 3), ("allrounder", 8)])
def test_no_card_turns_up_where_it_makes_no_sense(deck, model, policy, role, seed):
    """The sweep: whole careers, every season held to the rules -- not only the rules as
    written, but what was actually dealt."""
    setup = C.Setup("Sweep", role, 2009)
    career, _ = C.auto_career(deck, model, seed, setup, policy)
    prev = None
    for r in career.seasons:
        if r.event == "mentor":
            assert r.k <= C.MENTOR_UNTIL and r.rating < C.MENTOR_MAX_RATING + 3, r
        if r.event == "coach":
            assert r.k < C.VETERAN_FROM, r
        held = prev is not None and prev.captain and C.canonical(prev.franchise) == \
            C.canonical(r.franchise)
        if held and not prev.squad:
            assert r.event != "captain", r
        if r.incident and r.incident["key"] == "row" and r.captain:
            labels = [o.label for o in I.INCIDENTS["row"].captain_options]
            assert r.incident["choice"] in labels, r.incident
        prev = r


# --- the wiring ---------------------------------------------------------------------------

@pytest.mark.parametrize("policy", ["virtue", "vice"])
def test_every_season_moves_traits_by_exactly_its_choices(deck, model, policy):
    """The checkpoint after a season is the one before it, moved by that season's recorded
    moves -- and those include the chosen card's own traits and any incident's."""
    setup = C.Setup("Wiring", "middle", 2010)
    career, _ = C.auto_career(deck, model, 11, setup, policy)
    traits = ct.empty()
    by_label = {}
    for e in list(C.EVENTS) + list(C.FOLLOW_UPS.values()):
        for o in e.options:
            by_label.setdefault((e.key, o.label), o)
    for r in career.seasons:
        assert r.traits == pytest.approx(traits)
        moves = [tuple(m) for m in r.trait_moves]
        opt = by_label.get((r.event, r.choice.replace("coach", "{boss}").replace(
            "captain", "{boss}"))) or by_label.get((r.event, r.choice))
        if opt is not None:
            for m in opt.traits:
                assert m in moves, (r.event, r.choice, moves)
        if r.incident:
            for m in r.incident.get("traits", []):
                assert tuple(m) in moves
        traits = ct.nudge(traits, moves)
    assert career.checkpoint.traits == pytest.approx(traits)


def test_virtue_and_vice_end_up_different_players(deck, model):
    setup = C.Setup("Two roads", "opener", 2010)
    good, _ = C.auto_career(deck, model, 4, setup, "virtue")
    bad, _ = C.auto_career(deck, model, 4, setup, "vice")
    assert good.checkpoint.traits["work"] > bad.checkpoint.traits["work"]
    assert good.checkpoint.traits["lead"] > bad.checkpoint.traits["lead"]


def test_key_moment_calls_build_temper(deck, model, monkeypatch):
    """Three attacking calls make one step of aggression; balanced calls none."""
    seen = []
    real = C._cached_season

    def spy(*a, **kw):
        out = real(*a, **kw)
        seen.append(a[7] if len(a) > 7 else kw.get("answers"))
        return out
    monkeypatch.setattr(C, "_cached_season", spy)
    setup = C.Setup("Calls", "opener", 2012)
    moves: list[str] = []
    rng = __import__("random").Random(1)
    while True:
        car = C.play_career(deck, model, 21, setup, moves)
        if car.complete or len(car.seasons) >= 2:
            break
        p = car.pending
        moves.append("m2" if p.kind == "moment" else C.policy_move(car, "loyal", rng))
    first = car.seasons[0]
    calls = [m for m in first.trait_moves if m[0] == "temper" and m[1] == pytest.approx(ct.CALL_STEP)]
    attacking = sum(1 for a in seen[0] if a == 2) if seen else 0
    assert len(calls) == attacking > 0


def test_traits_reach_the_season_and_its_cache_key(deck, model, monkeypatch):
    """Your temper is in the card the season plays, and two careers with different traits
    never share a cached season."""
    keys, cards = [], []
    real = C._cached_season

    def spy(deck_, model_, seed, k, you, record, key, *a, **kw):
        keys.append(key)
        cards.append(you)
        return real(deck_, model_, seed, k, you, record, key, *a, **kw)
    monkeypatch.setattr(C, "_cached_season", spy)
    setup = C.Setup("Key", "opener", 2012)
    _, moves = C.auto_career(deck, model, 2, setup, "loyal")
    cut = next(i for i in range(len(moves))
               if len(C.play_career(deck, model, 2, setup, moves[:i]).seasons) == 1)
    cp = C.play_career(deck, model, 2, setup, moves[:cut]).checkpoint
    hot = dataclasses.replace(cp, traits={"temper": 3.0})
    runs = []
    for resume in (cp, hot):
        keys.clear(), cards.clear()
        C.play_career(deck, model, 2, setup, moves[cut:], resume=resume)
        runs.append((keys[0], cards[0]))
    assert runs[0][0] != runs[1][0]
    assert runs[1][1].bat_scoring == pytest.approx(runs[0][1].bat_scoring + 3 * ct.TEMPER_STEP)


def test_nerve_is_applied_at_key_moments_and_nowhere_else(deck, model, monkeypatch):
    calls = []
    real = ct.big_match
    monkeypatch.setattr(ct, "big_match", lambda c, t: calls.append(1) or real(c, t))
    start, ceiling = C.spin(deck, 3, "opener")
    you = C.ability(start, ceiling, 0.5, setup=C.Setup("N", "opener", 2019), fs_id=26,
                    year=2019, franchise=deck.cards_by_fs[26][0].franchise)
    year = C.simulate_year(deck, model, 3, 2, you, answers=None, traits={"nerve": 2.0})
    playoffs = sum(1 for r in year.season.playoffs if year.yours in (r.home, r.away))
    assert len(calls) >= playoffs
    assert len(calls) <= playoffs + C.MUST_WIN_MAX + C.MAKE_OR_BREAK_MAX
    calm = C.simulate_year(deck, model, 3, 2, you, answers=None, traits=ct.empty())
    plain = C.simulate_year(deck, model, 3, 2, you, answers=None)
    assert [r.home_innings.runs for r in calm.results] == [r.home_innings.runs for r in plain.results]


def test_a_captain_is_never_dropped_at_a_make_or_break(deck, model):
    from tests.test_career import MOB_FS, MOB_SEED, _mob_you
    you = _mob_you(deck, MOB_FS["dropped"])
    assert C.simulate_year(deck, model, MOB_SEED["dropped"], 2, you, answers=None).verdicts
    assert not C.simulate_year(deck, model, MOB_SEED["dropped"], 2, you, answers=None,
                               captain=True).verdicts


def test_a_captain_meets_the_captains_row(deck, model, monkeypatch):
    """Forced: the row is the season's incident, set off by the first heavy defeat."""
    monkeypatch.setattr(I, "candidate", lambda *a, **kw: ("row", 0))
    monkeypatch.setattr(I, "SLUMP_CHANCE", 0.0)
    C._SEASON_CACHE.clear()
    start, ceiling = C.spin(deck, 3, "opener")
    you = C.ability(start, ceiling, 0.5, setup=C.Setup("R", "opener", 2019), fs_id=26,
                    year=2019, franchise=deck.cards_by_fs[26][0].franchise)
    for captain, want in ((True, "Call a team meeting"), (False, "Back the captain")):
        try:
            C.simulate_year(deck, model, 7, 2, you, answers=(), captain=captain)
        except C._NeedMoment as need:
            assert need.prompt is not None
            assert need.prompt["options"][0].label == want
        else:
            pytest.fail("the season had no heavy defeat to set the row off")
    C._SEASON_CACHE.clear()


# --- the coach's mentor --------------------------------------------------------------------

def test_the_coachs_mentor_is_the_squads_best_bowler_of_your_style(deck):
    fs = 26
    m = C.coach_mentor(deck, fs, "bowl", "spin")
    cards = [c for c in deck.cards_by_fs[fs] if (c.bowl_balls or 0) >= C.BOWL_REGULAR_BALLS
             and c.bowling_style == "spin" and c.display is not None]
    assert m["id"] == max(cards, key=lambda c: (c.display, c.person_id)).person_id
    best = next(c for c in cards if c.person_id == m["id"])
    assert m["halves"] == [best.bowl_scoring, best.bowl_dismissal]
    assert m["lean"] == ct.style_from(best.bowl_scoring, best.bowl_dismissal, True)


def test_you_grow_toward_the_bowler_your_mentor_was(deck, model):
    start, _ = C.spin(deck, 1, "opener")
    setup = C.Setup("T", "opener", 2012)
    plain = C.role_state(deck, model, 1, setup, start, "allrounder", 0.0, "spin", 2012)
    base = plain["base"]
    better = [base[0] + 0.3, base[1] * 1.2]
    m = {"add": "bowl", "halves": better, "style": "spin"}
    mentored = C.role_state(deck, model, 1, setup, start, "allrounder", 0.0, "spin", 2012, mentor=m)
    assert mentored["top"] == better
    worse = {"add": "bowl", "halves": [base[0] - 0.5, base[1] * 0.5], "style": "spin"}
    assert C.role_state(deck, model, 1, setup, start, "allrounder", 0.0, "spin", 2012,
                        mentor=worse)["top"] == plain["top"]
    other = dict(m, style="pace")
    assert C.role_state(deck, model, 1, setup, start, "allrounder", 0.0, "spin", 2012,
                        mentor=other)["top"] == plain["top"]


def test_a_trial_names_its_mentor_and_passes_on_his_style(deck):
    start, _ = C.spin(deck, 1, "opener")
    cp = C.Checkpoint()
    card = C.with_coach_mentor(deck, C.COACH_TRIAL_STYLE, 26, start, cp)
    takes = [o for o in card["options"] if o["take"]]
    assert {o["style"] for o in takes} == {"pace", "spin"}
    for o in takes:
        m = C.coach_mentor(deck, 26, "bowl", o["style"])
        assert m["name"] in o["label"] and o["mentor"] == m
        assert o["traits"] == ([("temper", C.MENTOR_LEAN * m["lean"])] if m["lean"] else [])
    no = [o for o in card["options"] if not o["take"]]
    assert no and "mentor" not in no[0]
