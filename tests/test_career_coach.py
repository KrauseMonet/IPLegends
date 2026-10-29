"""Build a Career: changing role on the team coach's offer (A238).

A batter who bowls well can become an all-rounder, a bowler who bats well can move up and
become one, and an all-rounder can become a full-time bowler -- each an offer from the coach,
which the player may turn down, through a trial season judged on the new discipline alone,
with a way back after a poor season in the new role."""

from __future__ import annotations

import dataclasses
import json

import pytest

from game import career as C
from game.__main__ import bat_split
from tools.snapshot_deck import deck_from, model_from, read_document

COACH_TESTS = True          # conftest's `coach_off` leaves the coach on for this module
DOC = read_document()
pytestmark = [pytest.mark.skipif(DOC is None, reason="no deck snapshot"),
              pytest.mark.usefixtures("no_incidents")]


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


def _starts(deck, role):
    return C.template_pools(deck, role)[0]


def _setup(role):
    return C.Setup("Test", role, 2010, False)


# --- the changed role itself -------------------------------------------------------------------


def test_the_role_you_were_created_in_is_no_change(deck, model):
    start = _starts(deck, "opener")[0]
    assert C.role_state(deck, model, 1, _setup("opener"), start, "", 0.0, "", 2012) is None
    assert C.role_state(deck, model, 1, _setup("opener"), start, "opener", 0.0, "", 2012) is None


def test_where_you_bat_follows_the_job(deck, model):
    opener = _starts(deck, "opener")[0]
    rd = C.role_state(deck, model, 1, _setup("opener"), opener, "allrounder", 0.0, "pace", 2012)
    assert rd["kind"] == "allrounder" and set(rd["positions"]) == set(opener.positions)
    assert rd["band"] == opener.band and rd["add"] == "bowl"
    spinner = _starts(deck, "spin")[0]
    rd = C.role_state(deck, model, 1, _setup("spin"), spinner, "allrounder", 0.0, "", 2012)
    assert set(rd["positions"]) == set(C.FINISHER_SLOTS) and rd["band"] == "finisher"
    assert rd["add"] == "bat"
    ar = _starts(deck, "allrounder")[0]
    rd = C.role_state(deck, model, 1, _setup("allrounder"), ar, ar.bowling_style, 0.0, "", 2012)
    assert rd["kind"] == "bowler" and set(rd["positions"]) == set(C.TAIL_SLOTS)
    assert rd["add"] == ""                      # an all-rounder already bowls


def test_a_batter_who_never_bowled_starts_at_the_part_timers_level(deck, model):
    never = next(c for c in _starts(deck, "opener") if c.bowl_scoring is None)
    rd = C.role_state(deck, model, 1, _setup("opener"), never, "allrounder", 0.0, "spin", 2012)
    assert tuple(rd["base"]) == C.part_time_bowling(deck)
    # the pooled level of real part-timers, recomputed here from the deck
    cards = [c for cs in deck.cards_by_fs.values() for c in cs
             if c.role in ("batter", "keeper") and c.bowl_scoring is not None and c.bowl_balls]
    balls = sum(c.bowl_balls for c in cards)
    assert rd["base"][0] == pytest.approx(sum(c.bowl_scoring * c.bowl_balls for c in cards) / balls)
    assert C.bowl_value(*rd["base"]) < 0          # below an average bowler, never league average
    assert rd["style"] == "spin"                  # the style he chose


def test_a_batter_who_bowled_starts_from_his_own_bowling(deck, model):
    bowled = next(c for c in _starts(deck, "opener") if c.bowl_scoring is not None)
    rd = C.role_state(deck, model, 1, _setup("opener"), bowled, "allrounder", 0.0, "", 2012)
    assert tuple(rd["base"]) == (bowled.bowl_scoring, bowled.bowl_dismissal)
    assert rd["style"] == bowled.bowling_style    # his own style wins over a chosen one


def test_a_bowler_who_moves_up_bats_like_the_finisher_band_not_like_a_tailender(deck, model):
    spinner = _starts(deck, "spin")[0]
    rd = C.role_state(deck, model, 1, _setup("spin"), spinner, "allrounder", 0.0, "", 2015)
    # the engine's own rule for a number six with no batting of his own
    blank = dataclasses.replace(spinner, bat_scoring=None, bat_dismissal=None, band="finisher",
                                season_year=2015)
    assert tuple(rd["base"]) == pytest.approx(bat_split(blank, model))


def test_the_second_ceiling_is_a_real_season_better_than_where_you_start(deck, model):
    for role, add in (("opener", "bowl"), ("spin", "bat")):
        start = _starts(deck, role)[0]
        rd = C.role_state(deck, model, 7, _setup(role), start, "allrounder", 0.0, "pace", 2012)
        value = C.bowl_value if add == "bowl" else C.bat_value
        assert value(*rd["top"]) > value(*rd["base"])
        if add == "bowl":
            pool = [c for cs in deck.cards_by_fs.values() for c in cs
                    if c.role in ("bowler", "allrounder") and c.bowl_scoring is not None
                    and (c.bowl_balls or 0) >= C.BOWL_REGULAR_BALLS and c.bowling_style == rd["style"]]
            halves = {(c.bowl_scoring, c.bowl_dismissal) for c in pool}
        else:
            halves = {(c.bat_scoring, c.bat_dismissal)
                      for cs in deck.cards_by_fs.values() for c in cs if C._fits(c, "finisher")}
        assert tuple(rd["top"]) in halves          # a real player's numbers (A197)
        again = C.role_state(deck, model, 7, _setup(role), start, "allrounder", 0.0, "pace", 2012)
        assert again["top"] == rd["top"]           # from the seed: the same every time


def test_the_new_discipline_grows_with_its_own_progress_and_declines_with_you(deck, model):
    start = next(c for c in _starts(deck, "opener") if c.bowl_scoring is None)
    setup = _setup("opener")
    rd = C.role_state(deck, model, 3, setup, start, "allrounder", 0.0, "pace", 2012)
    card = C.ability(start, start, 1.0, setup=setup, fs_id=1, year=2012, franchise=None, role=rd)
    assert card.bowl_scoring == pytest.approx(rd["base"][0])        # no progress: the base
    assert card.role == "allrounder"
    # a batter who bowled a little is a part-timer (A206) until the coach makes him more
    bowled = next(c for c in _starts(deck, "opener") if c.bowl_scoring is not None)
    assert C.ability(bowled, bowled, 1.0, setup=setup, fs_id=1, year=2012,
                     franchise=None).bowl_balls == 0
    rd2 = C.role_state(deck, model, 3, setup, bowled, "allrounder", 0.0, "", 2012)
    assert C.ability(bowled, bowled, 1.0, setup=setup, fs_id=1, year=2012, franchise=None,
                     role=rd2).bowl_balls is None
    full = dict(rd, second=1.0)
    card = C.ability(start, start, 1.0, setup=setup, fs_id=1, year=2012, franchise=None, role=full)
    assert card.bowl_scoring == pytest.approx(rd["top"][0])
    assert card.bowl_dismissal == pytest.approx(rd["top"][1])
    # declining at t = 0.3, the new discipline is no better than t allows, however far it came
    card = C.ability(start, start, 0.3, setup=setup, fs_id=1, year=2012, franchise=None, role=full)
    half = C.ability(start, start, 0.3, setup=setup, fs_id=1, year=2012, franchise=None,
                     role=dict(rd, second=0.3))
    assert card.bowl_scoring == pytest.approx(half.bowl_scoring)


# --- one discipline's place among its regulars ------------------------------------------------


def _line(role, innings=0, runs=0, balls=0, outs=0, bowl_balls=0, bowl_runs=0, wickets=0,
          band="opener"):
    card = C.Card(fs_id=1, person_id=f"p{id(object())}", name="x", bat=0.0, bowl=0.0, band=band,
                  role=role, keeper_eligible=False, season_year=2012, franchise="F",
                  overseas=False, bowling_style="pace", display=80)
    return C.Line(card=card, side=None, matches=14, innings=innings, runs=runs, balls=balls,
                  outs=outs, bowl_balls=bowl_balls, bowl_runs=bowl_runs, wickets=wickets)


def test_a_discipline_is_ranked_among_its_own_regulars_with_enough_volume():
    me = _line("allrounder", innings=10, runs=200, balls=150, outs=8,
               bowl_balls=200, bowl_runs=300, wickets=6)
    lines = {("you", 0): me}
    for i in range(10):     # bowlers better and worse than him, and batters who never bowled
        lines[(f"b{i}", 0)] = _line("bowler", bowl_balls=240, bowl_runs=260 + 15 * i, wickets=12 - i)
        lines[(f"t{i}", 0)] = _line("batter", innings=12, runs=250 + 20 * i, balls=190, outs=10)
    bowl = C.discipline_rank(lines, ("you", 0), "bowl")
    assert 0 < bowl < 1
    # the batters do not bowl, so they cannot change his bowling rank
    no_batters = {k: l for k, l in lines.items() if not k[0].startswith("t")}
    assert C.discipline_rank(no_batters, ("you", 0), "bowl") == bowl
    me.bowl_balls = C.PEER_MIN_BOWL_BALLS - 1
    assert C.discipline_rank(lines, ("you", 0), "bowl") is None      # too little to judge
    me.innings = C.PEER_MIN_INNINGS - 1
    assert C.discipline_rank(lines, ("you", 0), "bat") is None


def test_the_best_bowler_ranks_top_and_the_worst_bottom():
    lines = {(f"b{i}", 0): _line("bowler", bowl_balls=240, bowl_runs=250 + 20 * i, wickets=14 - i)
             for i in range(8)}
    assert C.discipline_rank(lines, ("b0", 0), "bowl") == 1.0
    assert C.discipline_rank(lines, ("b7", 0), "bowl") == 0.0


# --- what the coach offers ---------------------------------------------------------------------


def _prev(**kw):
    return C.SeasonRecord(k=0, year=2011, franchise="F", short="F", price=0, stayed=True,
                          event="quiet", choice="", captain=False, ability=0.0, teams=8, **kw)


def _cp(**kw):
    return C.Checkpoint(**kw)


def _trial_seed(k=2):
    return next(s for s in range(1, 500)
                if C.random.Random(f"{s}:coach:{k}").random() < C.COACH_TRIAL_CHANCE)


def test_a_good_trial_brings_the_full_time_offer_first_even_in_a_squad_season(deck):
    start = _starts(deck, "opener")[0]
    cp = _cp(k=3, coach_next="allrounder", seasons=[_prev()])
    card = C.coach_card(1, _setup("opener"), start, cp, 3, squad=True)
    assert card["key"] == "switch" and card["target"] == "allrounder"


def test_an_allrounder_whose_bowling_carries_him_is_asked_to_specialise(deck):
    ar = _starts(deck, "allrounder")[0]
    setup = _setup("allrounder")
    strong = C.coach_card(1, setup, ar, _cp(seasons=[_prev(bowl_rank=0.7, bat_rank=0.6)]), 2, False)
    assert strong["key"] == "specialise" and strong["target"] == ar.bowling_style
    assert strong["text"] == C.COACH_SPECIALISE["text"]
    fading = C.coach_card(1, setup, ar, _cp(seasons=[_prev(bowl_rank=0.45, bat_rank=0.2)]), 2, False)
    assert fading["key"] == "specialise" and fading["text"] == C.COACH_FADING["text"]
    neither = C.coach_card(1, setup, ar, _cp(seasons=[_prev(bowl_rank=0.45, bat_rank=0.5)]), 2, False)
    assert neither is None or neither["key"] != "specialise"
    # turned down last season: not asked again straight away
    declined = C.coach_card(1, setup, ar, _cp(seasons=[_prev(bowl_rank=0.8)], coach_declined=1), 2, False)
    assert declined is None or declined["key"] != "specialise"
    # nothing in a squad season
    assert C.coach_card(1, setup, ar, _cp(seasons=[_prev(bowl_rank=0.8)]), 2, True) is None


def test_a_trial_is_offered_only_where_the_rules_allow(deck):
    seed = _trial_seed()
    opener = next(c for c in _starts(deck, "opener") if c.bowling_style)
    card = C.coach_card(seed, _setup("opener"), opener, _cp(seasons=[_prev()]), 2, False)
    assert card["key"] == "trial_bowl" and card["target"] == "allrounder"
    assert len(card["options"]) == 2                     # his style is known
    never = next(c for c in _starts(deck, "opener") if not c.bowling_style)
    styled = C.coach_card(seed, _setup("opener"), never, _cp(seasons=[_prev()]), 2, False)
    assert [o["style"] for o in styled["options"] if o["take"]] == ["pace", "spin"]
    spinner = _starts(deck, "spin")[0]
    assert C.coach_card(seed, _setup("spin"), spinner, _cp(seasons=[_prev()]), 2,
                        False)["key"] == "trial_bat"
    # not in the first season, nor past the last, nor after the allowance, nor once changed
    assert C.coach_card(seed, _setup("opener"), opener, _cp(), 0, False) is None
    late = C.COACH_TRIAL_UNTIL + 1
    assert C.coach_card(next(s for s in range(1, 500) if C.random.Random(
        f"{s}:coach:{late}").random() < C.COACH_TRIAL_CHANCE), _setup("opener"), opener,
        _cp(seasons=[_prev()]), late, False) is None
    assert C.coach_card(seed, _setup("opener"), opener,
                        _cp(seasons=[_prev()], trials=C.COACH_TRIALS_MAX), 2, False) is None
    assert C.coach_card(seed, _setup("opener"), opener,
                        _cp(seasons=[_prev()], roles=["allrounder"]), 2, False) is None
    # a finisher or a keeper does not change role
    for role in ("finisher", "keeper"):
        start = _starts(deck, role)[0]
        assert C.coach_card(seed, _setup(role), start, _cp(seasons=[_prev()]), 2, False) is None
    # and a season whose own draw misses gets no trial
    miss = next(s for s in range(1, 500)
                if C.random.Random(f"{s}:coach:2").random() >= C.COACH_TRIAL_CHANCE)
    assert C.coach_card(miss, _setup("opener"), opener, _cp(seasons=[_prev()]), 2, False) is None


def test_the_coach_is_silent_when_turned_off(deck):
    C_ON = C.COACH_ON
    try:
        C.COACH_ON = False
        cp = _cp(coach_next="allrounder")
        assert C.coach_card(1, _setup("opener"), _starts(deck, "opener")[0], cp, 3, False) is None
    finally:
        C.COACH_ON = C_ON


# --- after the season ---------------------------------------------------------------------------


def _rd(role="allrounder", add="bowl", trial=False):
    return {"role": role, "kind": C.role_kind(role), "positions": [1, 2, 3], "band": "opener",
            "style": "pace", "add": add, "base": [0.0, 1.0], "top": [0.1, 1.2], "second": 0.0,
            "trial": trial}


def test_a_trial_passes_at_the_way_backs_own_bar():
    """A trial that passes is never a season that would send a full-time player back."""
    assert C.TRIAL_PASS >= C.WAY_BACK


def test_a_passed_trial_brings_the_offer():
    said = []
    cp = _cp()
    C.coach_after(cp, _setup("opener"), _rd(trial=True), _prev(bowl_rank=C.TRIAL_PASS), lambda *a: said.append(a))
    assert cp.coach_next == "allrounder" and said[0][1] == "up"
    # a passed trial counts as a season of the new skill
    assert cp.second == pytest.approx(C.SECOND_STEP + C.SECOND_FORM * (C.TRIAL_PASS - 0.5))
    cp = _cp()
    C.coach_after(cp, _setup("opener"), _rd(trial=True), _prev(bowl_rank=C.TRIAL_PASS - 0.01),
                  lambda *a: None)
    assert cp.coach_next == "" and cp.second == 0.0
    cp = _cp()                                   # too little bowling to judge: no pass
    C.coach_after(cp, _setup("opener"), _rd(trial=True), _prev(bowl_rank=None), lambda *a: None)
    assert cp.coach_next == ""
    # a bowler's batting trial reads his batting
    cp = _cp()
    C.coach_after(cp, _setup("spin"), _rd(add="bat", trial=True), _prev(bowl_rank=0.0, bat_rank=0.9),
                  lambda *a: None)
    assert cp.coach_next == "allrounder"


def test_the_new_discipline_grows_after_a_season_in_the_role():
    cp = _cp(roles=["allrounder"], second=0.2)
    C.coach_after(cp, _setup("opener"), _rd(), _prev(bowl_rank=0.8), lambda *a: None)
    assert cp.second == pytest.approx(0.2 + C.SECOND_STEP + C.SECOND_FORM * 0.3)
    assert cp.roles == ["allrounder"]
    cp = _cp(roles=["allrounder"], second=0.95)
    C.coach_after(cp, _setup("opener"), _rd(), _prev(bowl_rank=0.9), lambda *a: None)
    assert cp.second == 1.0                       # never past the second ceiling


def test_the_coming_on_note_is_for_a_top_half_season():
    said = []
    C.coach_after(_cp(roles=["allrounder"]), _setup("opener"), _rd(), _prev(bowl_rank=0.4),
                  lambda *a: said.append(a[0]))
    assert not any("coming on" in t for t in said)        # passed the bar, not the top half
    C.coach_after(_cp(roles=["allrounder"]), _setup("opener"), _rd(), _prev(bowl_rank=0.6),
                  lambda *a: said.append(a[0]))
    assert any("bowling is coming on" in t for t in said)


def test_the_first_full_time_season_has_grace():
    said = []
    cp = _cp(roles=["allrounder"], role_since=3)
    first = dataclasses.replace(_prev(bowl_rank=0.05), k=3)
    C.coach_after(cp, _setup("opener"), _rd(), first, lambda *a: said.append(a))
    assert cp.roles == ["allrounder"] and "giving you time" in said[-1][0]
    second = dataclasses.replace(_prev(bowl_rank=0.05), k=4)
    C.coach_after(cp, _setup("opener"), _rd(), second, lambda *a: said.append(a))
    assert cp.roles == []                         # the season after: the way back as usual


def test_a_poor_season_in_the_new_role_is_the_way_back():
    said = []
    cp = _cp(roles=["allrounder"])
    C.coach_after(cp, _setup("opener"), _rd(), _prev(bowl_rank=C.WAY_BACK - 0.01), lambda *a: said.append(a))
    assert cp.roles == [] and said[-1][1] == "down" and "batter again" in said[-1][0]
    cp = _cp(roles=["allrounder"])
    C.coach_after(cp, _setup("opener"), _rd(), _prev(bowl_rank=C.WAY_BACK), lambda *a: None)
    assert cp.roles == ["allrounder"]
    # one step back at a time: a batter turned all-rounder turned bowler goes back to doing both
    cp = _cp(roles=["allrounder", "pace"])
    C.coach_after(cp, _setup("opener"), _rd("pace"), _prev(bowl_rank=0.1), lambda *a: said.append(a))
    assert cp.roles == ["allrounder"] and "both" in said[-1][0]
    # an injured season with nothing to judge changes nothing
    cp = _cp(roles=["allrounder"], second=0.4)
    C.coach_after(cp, _setup("opener"), _rd(), _prev(bowl_rank=None), lambda *a: None)
    assert cp.roles == ["allrounder"] and cp.second == 0.4


# --- whole careers ------------------------------------------------------------------------------


def _drive(deck, model, seed, setup, answer, limit=400):
    """Play a career with the 'strength' player, answering the coach with `answer(card)`."""
    import random
    rng = random.Random(f"{seed}:policy")
    moves = []
    for _ in range(limit):
        career = C.play_career(deck, model, seed, setup, moves)
        if career.complete:
            return career, moves
        p = career.pending
        moves.append(f"t{answer(p.coach)}" if p.kind == "coach" else C.policy_move(career, "strength", rng))
    raise AssertionError("career did not finish")


def _stats(r):
    return (r.year, r.franchise, r.matches, r.runs, r.balls, r.wickets, r.bowl_balls, r.form,
            r.position)


def test_turning_the_coach_down_changes_no_match(deck, model):
    """A declined offer is a sentence in the season's notes and nothing else: every season
    plays exactly as it would with no coach at all."""
    setup = C.Setup("Test", "opener", 2010, False)
    declined, moves = _drive(deck, model, 3, setup, lambda c: len(c["options"]) - 1)
    assert any(r.coach for r in declined.seasons)
    assert all(r.role is None for r in declined.seasons)
    C.COACH_ON = False
    try:
        plain = C.play_career(deck, model, 3, setup, [m for m in moves if not m.startswith("t")])
    finally:
        C.COACH_ON = True
    assert [_stats(r) for r in declined.seasons] == [_stats(r) for r in plain.seasons]
    # the plain career shares those seasons through the cache, but not what was said there
    assert all(r.coach is None for r in plain.seasons)


def test_a_batter_who_takes_the_trial_bowls_and_one_who_passes_is_offered_the_role(deck, model):
    setup = C.Setup("Test", "opener", 2010, False)
    career, moves = _drive(deck, model, 3, setup, lambda c: 0)
    trials = [r for r in career.seasons if r.role and r.role["trial"]]
    assert trials, "seed 3's opener is offered a trial"
    for r in trials:
        assert r.bowl_balls > 0                     # a trial season really bowls
    passed = [r for r in trials if r.bowl_rank is not None and r.bowl_rank >= C.TRIAL_PASS]
    assert passed, "and one trial passes"
    i = career.seasons.index(passed[0])
    nxt = career.seasons[i + 1]
    assert nxt.coach and nxt.coach["key"] == "switch" and nxt.coach["take"]
    assert nxt.role and nxt.role["role"] == "allrounder" and not nxt.role["trial"]


def test_resuming_after_a_role_change_is_the_same_as_replaying(deck, model):
    setup = C.Setup("Test", "spin", 2010, False)
    career, moves = _drive(deck, model, 4, setup, lambda c: 0)
    changed = [i for i, r in enumerate(career.seasons) if r.role and not r.role["trial"]]
    assert changed, "seed 4's spinner becomes an all-rounder"
    # replay to just after the change, then resume from a JSON round trip of the checkpoint
    k = changed[0] + 1
    used, partial = [], None
    for n in range(len(moves) + 1):
        c = C.play_career(deck, model, 4, setup, moves[:n])
        if len(c.seasons) == k and c.pending is not None:
            used = moves[:n]
            partial = c
            break
    assert partial is not None and partial.checkpoint.roles == ["allrounder"]
    assert partial.checkpoint.role_since == changed[0]          # its grace season
    cp = C.Checkpoint.from_dict(json.loads(json.dumps(partial.checkpoint.to_dict())))
    resumed = C.play_career(deck, model, 4, setup, moves[len(used):], resume=cp)
    assert [_stats(r) for r in resumed.seasons] == [_stats(r) for r in career.seasons]
    assert [r.role for r in resumed.seasons] == [r.role for r in career.seasons]


def test_a_changed_role_is_part_of_the_season_cache_key(deck, model, monkeypatch):
    """Two careers can reach one season with different roles; the cache must not hand one
    the other's season (a trial season played as a plain batter's)."""
    real = C._cached_season
    seen = []

    def spy(deck, model, seed, k, you, record, key, *a, **kw):
        seen.append((record.role, key))
        return real(deck, model, seed, k, you, record, key, *a, **kw)

    monkeypatch.setattr(C, "_cached_season", spy)
    _drive(deck, model, 4, C.Setup("Test", "spin", 2010, False), lambda c: 0)
    assert any(rd for rd, _ in seen)
    assert all(key[-2] == C.role_key(rd) for rd, key in seen)


def test_the_legend_is_carded_in_the_role_his_best_season_was_played_in(deck, model):
    from web import career_results

    start = _starts(deck, "spin")[0]
    ceiling = C.template_pools(deck, "spin")[1][0]
    setup = _setup("spin")
    rd = C.role_state(deck, model, 4, setup, start, "allrounder", 0.5, "", 2013)
    best = dataclasses.replace(_prev(), year=2013, rating=88, display=88, ability=0.8, role=rd)
    career = C.Career(setup=setup, seed=4, start=start, ceiling=ceiling, seasons=[best])
    _, card = C.legend_card(career)
    assert card.role == "allrounder" and set(card.positions) == set(C.FINISHER_SLOTS)
    # a season measured like a real card (A225) keeps its role too
    measured = dataclasses.replace(best, measured=[0.1, 0.9, 0.05, 1.1])
    _, card = C.legend_card(dataclasses.replace(career, seasons=[measured]))
    assert card.role == "allrounder" and card.bat_scoring == 0.1
    saved = career_results.legend(career)
    assert saved["role"] == "allrounder" and saved["role_label"] == "All-rounder"


def test_the_role_state_survives_a_checkpoint_round_trip():
    cp = C.Checkpoint(k=5, roles=["allrounder", "pace"], second=0.6, style="pace",
                      coach_next="allrounder", trials=2, coach_declined=3, role_since=4)
    back = C.Checkpoint.from_dict(json.loads(json.dumps(cp.to_dict())))
    assert (back.roles, back.second, back.style, back.coach_next, back.trials,
            back.coach_declined, back.role_since) == (["allrounder", "pace"], 0.6, "pace",
                                                       "allrounder", 2, 3, 4)


def test_an_old_checkpoint_reads_as_no_change():
    cp = C.Checkpoint(k=2, roles=["allrounder"], second=0.5)
    d = cp.to_dict()
    for key in ("roles", "second", "style", "coach_next", "trials", "coach_declined",
                "role_since"):
        d.pop(key)
    old = C.Checkpoint.from_dict(d)
    assert old.roles == [] and old.second == 0.0 and old.coach_next == "" and old.trials == 0
    assert old.role_since == -1


def test_the_automatic_players_answer_the_coach():
    import random
    career = C.Career(setup=_setup("opener"), seed=1, start=None, ceiling=None,
                      pending=C.Pending("coach", 2012, 2, coach=C.COACH_TRIAL_STYLE))
    assert C.policy_move(career, "strength", random.Random(1)) == "t0"
    moves = {C.policy_move(career, "random", random.Random(s)) for s in range(40)}
    assert moves == {"t0", "t1", "t2"}


# --- the API ------------------------------------------------------------------------------------


def test_the_coach_offer_and_the_role_path_reach_the_page(deck, model):
    from fastapi.testclient import TestClient

    from web import career_routes as R
    from web.app import app

    setup = C.Setup("Test", "spin", 2010, False)
    career, moves = _drive(deck, model, 4, setup, lambda c: 0)
    path = R.role_path(career)
    assert path[0] == "Spinner" and any(p.startswith("All-rounder from ") for p in path)
    # the first coach offer, as the page receives it
    for n in range(len(moves)):
        c = C.play_career(deck, model, 4, setup, moves[:n])
        if c.pending is not None and c.pending.kind == "coach":
            out = R._pending(c.pending)
            assert out.kind == "coach" and out.coach.title and len(out.coach.options) >= 2
            assert out.coach.options[0].label == c.pending.coach["options"][0]["label"]
            break
    else:
        raise AssertionError("no coach offer")
    with TestClient(app):                         # the app loads its deck for `_season`
        season = R._season(next(r for r in career.seasons if r.role))
    assert season.role == "allrounder" and season.role_label == "All-rounder"
