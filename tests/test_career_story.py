"""Build a Career told as a story (A230): choices worded without numbers, things that happen
DURING a season set off by a real match, a hidden standing with your club, and threads a later
season remembers.

The incident fixtures are real seasons found by search (Chennai Super Kings 2019, the fourth
season of a career spun from seed 4), each asserting the shape it needs."""

from __future__ import annotations

import dataclasses
from collections import Counter
import re

import pytest

from game import career as C
from game import career_incidents as I
from tools.snapshot_deck import deck_from, model_from, read_document

DOC = read_document()
needs_deck = pytest.mark.skipif(DOC is None, reason="no deck snapshot")
CSK_2019 = 26
K = 4
# seed -> the incident it brings, for an opener and a fast bowler (found by search)
OPENER = {"press": 2, "slump": 3, "row": 7, "hamstring": 9, "wedding": 12, "collapse": 20}
PACE = {"press": 2, "side_strain": 3, "hamstring": 5, "back": 9, "death": 18, "wedding": 25}


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


def _you(deck, role):
    start, ceiling = C.spin(deck, 4, role)
    c0 = deck.cards_by_fs[CSK_2019][0]
    return C.ability(start, ceiling, 0.5, setup=C.Setup("Ann Example", role, 2015),
                     fs_id=CSK_2019, year=2019, franchise=c0.franchise)


def _play(deck, model, seed, you, choice):
    """The season with every key moment played Balanced and the incident answered `choice`.
    Returns the year and the prompt it stopped at."""
    answers: list = []
    prompt = None
    while True:
        try:
            return C.simulate_year(deck, model, seed, K, you, answers=tuple(answers)), prompt
        except C._NeedMoment as need:
            if need.prompt:
                prompt = need.prompt
                answers.append(("i", choice))
            else:
                answers.append(1)


# --- no numbers anywhere a player decides ---------------------------------------------------

DIGIT = re.compile(r"\d")


def _all_options():
    for e in list(C.EVENTS) + list(C.FOLLOW_UPS.values()):
        for o in e.options:
            yield e.key, o.label, o.hint, o.story
    for i in I.INCIDENTS.values():
        for o in i.options:
            yield i.key, o.label, o.hint, o.story


def test_no_choice_is_worded_in_numbers():
    for key, label, hint, story in _all_options():
        for text in (label, hint, story):
            assert not DIGIT.search(text), (key, text)
            assert "Rating" not in text and "%" not in text, (key, text)


def test_every_choice_with_an_alternative_has_a_voice():
    for e in list(C.EVENTS) + list(C.FOLLOW_UPS.values()):
        if len(e.options) > 1:
            assert all(o.hint for o in e.options), e.key
    for i in I.INCIDENTS.values():
        assert len(i.options) > 1 and all(o.hint and o.story for o in i.options), i.key


def test_the_season_notes_carry_no_numbers_of_the_game_s_own():
    for words in (C.DEV_UP + C.DEV_DOWN + C.POTENTIAL_GRADE + C.POTENTIAL_GROWN
                  + C.CLOCK_SLOW_WORDS + C.CLOCK_FAST_WORDS):
        assert not DIGIT.search(words) and "Rating" not in words
    assert C.development_words(1.0, 0) != C.development_words(1.0, 1)   # it varies by season
    assert C.potential_words(84, 86, 92) in C.POTENTIAL_GRADE
    assert C.potential_words(84, 86, None) in C.POTENTIAL_GROWN


def test_a_lasting_change_is_told_in_words_with_its_direction():
    base = C.Card(fs_id=1, person_id="x", name="X", bat=0.0, bowl=0.0, band="top",
                  role="allrounder", keeper_eligible=False, season_year=2012, franchise="F",
                  overseas=False, bowling_style="pace", display=None, positions=(1,),
                  bat_scoring=0.0, bat_dismissal=0.05, bowl_scoring=0.0, bowl_dismissal=0.05)
    better = I.formed(base, 1)
    said = C.skill_words(base, better)
    assert said and not DIGIT.search(said) and "harder to get out" in said
    assert C.skill_tone(base, better) == "up" and C.skill_tone(better, base) == "down"


# --- the cards before a season ---------------------------------------------------------------


def test_injuries_and_slumps_are_no_longer_pre_season_cards():
    keys = {e.key for e in C.EVENTS}
    assert not keys & {"niggle", "injury", "fracture", "slump"}


def test_a_quiet_pre_season_is_rare_and_almost_every_card_asks():
    seen = [e for s in range(300) for e in C.event_sequence(s, False, "opener")[:12]]
    quiet = sum(e.key == "quiet" for e in seen)
    assert quiet / len(seen) < 0.08
    assert sum(len(e.options) > 1 for e in seen) / len(seen) > 0.9


@needs_deck
def test_a_card_that_does_not_fit_the_season_gives_way(deck):
    cp = C.Checkpoint(k=3, current="Chennai Super Kings")
    setup = C.Setup("Ann Example", "opener", 2015)
    trade = next(e for e in C.EVENTS if e.key == "trade")
    work = next(e for e in C.EVENTS if e.key == "workload")
    seed = next(s for s in range(500) if C.draw_event(s, 3, False, "opener") is trade)
    moving = C.Offer(1, "Mumbai Indians", "MI", 100, 80, False)
    staying = dataclasses.replace(moving, stay=True)
    assert C.season_event(cp, 3, 2019, staying, False, False, seed, setup) is trade
    assert C.season_event(cp, 3, 2019, moving, False, False, seed, setup).key in C.FALLBACK
    assert C.season_event(cp, 3, 2019, staying, True, False, seed, setup).key in C.FALLBACK
    assert C.season_event(cp, 3, 2018, staying, False, False, seed, setup).key in C.FALLBACK
    seed = next(s for s in range(500) if C.draw_event(s, 3, False, "opener") is work)
    assert C.season_event(cp, 3, 2019, staying, False, False, seed, setup).key in C.FALLBACK
    capped = dataclasses.replace(cp, national={"capped": True, "in_side": True, "caps": 9})
    assert C.season_event(capped, 3, 2019, staying, False, False, seed, setup) is work


def test_follow_ups_take_the_season_s_place():
    setup = C.Setup("Ann Example", "opener", 2015)
    offer = C.Offer(1, "Mumbai Indians", "MI", 100, 80, True)
    feud = C.Checkpoint(k=4, threads=[{"k": "press_feud", "year": 2018}])
    assert C.season_event(feud, 4, 2019, offer, False, False, 1, setup).key == "press_feud"
    assert C.season_event(feud, 4, 2020, offer, False, False, 1, setup).key != "press_feud"
    trusted = C.Checkpoint(k=4, standing=C.STANDING_LEAD)
    assert C.season_event(trusted, 4, 2019, offer, False, False, 1, setup).key == "lead"
    assert C.season_event(trusted, 4, 2019, offer, False, True, 1, setup).key != "lead"
    asked = dataclasses.replace(trusted, threads=[{"k": "lead", "club": "Mumbai Indians"}])
    assert C.season_event(asked, 4, 2019, offer, False, False, 1, setup).key != "lead"
    assert C.season_event(dataclasses.replace(trusted, standing=C.STANDING_LEAD - 1), 4, 2019,
                          offer, False, False, 1, setup).key != "lead"


# --- the incidents' own rules ---------------------------------------------------------------


def _match(**kw):
    base = dict(opp="X", played=True, batted=True, runs=30, out=True, bowl_balls=0,
                bowl_runs=0, wickets=0, lost=False, heavy=None, collapse=None, death_hit=None)
    base.update(kw)
    return I.Match(**base)


def test_each_trigger_reads_the_match_it_names():
    inc = I.INCIDENTS
    assert I.fires(inc["press"], "batter", _match(runs=0), 4, 0, []) == "Out for a duck"
    assert I.fires(inc["press"], "batter", _match(runs=9), 4, 0, []) == "Out for 9"
    assert I.fires(inc["press"], "batter", _match(runs=10), 4, 0, []) is None
    assert I.fires(inc["press"], "batter", _match(runs=3, out=False), 4, 0, []) is None
    assert I.fires(inc["press"], "batter", _match(runs=0), 2, 0, []) is None      # too early
    assert I.fires(inc["press"], "pace", _match(batted=False, bowl_balls=24, bowl_runs=44), 4, 0, []) \
        == "4 overs for 44 and no wicket"
    assert I.fires(inc["press"], "pace", _match(batted=False, bowl_balls=24, bowl_runs=44,
                                                wickets=1), 4, 0, []) is None
    assert I.fires(inc["row"], "batter", _match(heavy="the batters"), 3, 0, []) == "the batters"
    assert I.fires(inc["collapse"], "batter", _match(collapse="Bowled out for 98"), 3, 0, []) \
        == "Bowled out for 98"
    assert I.fires(inc["collapse"], "batter", _match(batted=False, collapse="Bowled out for 98"),
                   3, 0, []) is None
    assert I.fires(inc["death"], "pace", _match(death_hit=("DL Chahar", 21, 19)), 3, 0, []) \
        == "DL Chahar went for 21 in the 19th over"
    assert I.fires(inc["hamstring"], "batter", _match(), 5, 6, []) is None        # not yet
    assert I.fires(inc["hamstring"], "batter", _match(), 6, 6, []) == "going for a second run"
    assert I.fires(inc["hamstring"], "batter", _match(played=False), 6, 6, []) is None
    assert I.fires(inc["back"], "pace", _match(), 6, 2, [24, 18]) is None          # not two full spells
    assert I.fires(inc["back"], "pace", _match(), 6, 2, [18, 24, 24]) == ""


def test_who_each_incident_can_happen_to():
    for _ in range(1):
        for seed in range(400):
            for kind in ("batter", "keeper", "spin", "pace", "allrounder"):
                key, _t = I.candidate(seed, 3, kind, 14)
                if key:
                    assert kind in I.INCIDENTS[key].kinds, (key, kind)
                    assert not (key == "back" and kind != "pace")
                    assert not (key == "death" and kind in ("batter", "keeper"))


@pytest.mark.parametrize("kind", ["batter", "pace", "spin"])
def test_injuries_come_at_the_role_s_rate(kind):
    n = 6000
    hurt = sum(I.candidate(s, 3, kind, 14)[0] in ("hamstring", "side_strain", "back")
               for s in range(n))
    want = I.INJURY_RISK["opener" if kind == "batter" else kind]
    assert abs(hurt / n - want) < 0.015, (kind, hurt / n, want)


def test_injuries_come_mostly_to_fast_bowlers_and_a_stress_fracture_only_to_them():
    """[A230] Injuries happen mid-season now (`career_incidents`)."""
    def injuries(kind):
        return Counter(I.candidate(s, k, kind, 14)[0] for s in range(500) for k in range(12))
    pace, batter, spin = injuries("pace"), injuries("batter"), injuries("spin")
    hurt = lambda c: c["hamstring"] + c["side_strain"] + c["back"]
    assert hurt(pace) > 2 * hurt(spin) and hurt(spin) > 0.9 * hurt(batter)
    assert pace["back"] > 0 and batter["back"] == spin["back"] == 0


def test_form_moves_both_halves_of_both_disciplines():
    c = C.Card(fs_id=1, person_id="x", name="X", bat=0.0, bowl=0.0, band="top", role="allrounder",
               keeper_eligible=False, season_year=2012, franchise="F", overseas=False,
               bowling_style="pace", display=None, positions=(1,), bat_scoring=0.1,
               bat_dismissal=0.05, bowl_scoring=-0.1, bowl_dismissal=0.04)
    assert I.formed(c, 0) is c
    up, down = I.formed(c, 1), I.formed(c, -1)
    assert up.bat_scoring > c.bat_scoring > down.bat_scoring
    assert up.bat_dismissal < c.bat_dismissal < down.bat_dismissal
    assert up.bowl_scoring > c.bowl_scoring > down.bowl_scoring          # runs saved
    assert up.bowl_dismissal > c.bowl_dismissal > down.bowl_dismissal    # wickets taken


def test_a_career_has_each_incident_no_more_often_than_its_limit():
    rec = lambda key: C.SeasonRecord(k=0, year=2012, franchise="F", short="F", price=1,
                                     stayed=False, event="quiet", choice="", captain=False,
                                     ability=0.0, teams=8, incident={"key": key})
    assert C.incidents_spent([rec("back")]) == ("back",)
    assert C.incidents_spent([rec("hamstring")]) == ()
    assert C.incidents_spent([rec("hamstring"), rec("hamstring")]) == ("hamstring",)


# --- incidents in a real season -------------------------------------------------------------


@needs_deck
@pytest.mark.parametrize("role, seeds", [("opener", OPENER), ("pace", PACE)])
def test_the_fixtures_bring_the_incident_they_are_pinned_for(deck, model, role, seeds):
    you = _you(deck, role)
    for key, seed in seeds.items():
        _, prompt = _play(deck, model, seed, you, 0)
        assert prompt and prompt["key"] == key, (role, key)


@needs_deck
def test_a_season_with_no_incident_is_the_season_it_always_was(deck, model):
    """An incident is the only thing that can change a season: with none, every result is the
    one the season plays with nobody asked anything."""
    you = _you(deck, "opener")
    seed = next(s for s in range(60) if _play(deck, model, s, you, 0)[1] is None)
    asked, _ = _play(deck, model, seed, you, 0)
    plain = C.simulate_year(deck, model, seed, K, you)
    assert asked.incident is None
    assert [(r.home_runs, r.away_runs, r.home_wickets, r.away_wickets) for r in asked.results] == \
        [(r.home_runs, r.away_runs, r.home_wickets, r.away_wickets) for r in plain.results]


@needs_deck
def test_an_injury_rested_keeps_you_out_of_exactly_the_next_matches(deck, model):
    you = _you(deck, "opener")
    year, prompt = _play(deck, model, OPENER["hamstring"], you, 0)
    inc = year.incident
    assert inc["key"] == "hamstring" and inc["choice"] == "Sit out and let it heal"
    out = sorted(year.incident_out)
    assert len(out) == 2 and out[1] == out[0] + 1
    assert all(year.incident_out[i] == C.INCIDENT_ABSENCE["hamstring"] for i in out)
    feed = C.season_detail(year).feed
    league = [l for l in feed if l.stage == "league"]
    assert [i for i, l in enumerate(league) if l.absence == "a hamstring"] == out
    assert all(not league[i].played for i in out)
    assert league[out[0]].incident == "A tight hamstring: Sit out and let it heal"
    assert inc["at"] == out[0] + 1
    assert inc["injuries"] == ["Hamstring strain, rested (2 matches)"]
    # playing on keeps you in the side for the next match
    on, _ = _play(deck, model, OPENER["hamstring"], you, 1)
    nxt = on.incident["at"] - 1
    assert nxt not in on.incident_out and nxt not in on.absent


@needs_deck
def test_an_answer_s_form_reaches_your_card_from_the_next_match(deck, model, monkeypatch):
    """Captured at the seam: the level the season asks `formed` for, match by match."""
    you = _you(deck, "opener")
    levels: list[int] = []
    real = I.formed
    monkeypatch.setattr(I, "formed", lambda c, lv: (levels.append(lv), real(c, lv))[1])
    year, _ = _play(deck, model, OPENER["hamstring"], you, 1)        # strap it: form down
    assert year.incident["choice"] == "Strap it and play on"
    assert levels and set(levels) == {-1}
    levels.clear()
    _play(deck, model, OPENER["hamstring"], you, 0)                  # rested: no form change
    assert levels == []


@needs_deck
def test_a_call_kept_for_the_season_is_applied_after_a_collapse(deck, model, monkeypatch):
    you = _you(deck, "opener")
    calls: list[int] = []
    real = C.approach_card
    monkeypatch.setattr(C, "approach_card", lambda c, ch: (calls.append(ch), real(c, ch))[1])
    year, _ = _play(deck, model, OPENER["collapse"], you, 0)
    assert year.incident["key"] == "collapse"
    assert calls and 0 in calls                                        # the anchor's call, kept
    calls.clear()
    _play(deck, model, OPENER["collapse"], you, 1)
    assert 0 not in calls


@needs_deck
def test_death_overs_are_taken_from_the_next_match(deck, model):
    you = _you(deck, "pace")
    year, _ = _play(deck, model, PACE["death"], you, 0)
    assert year.incident["key"] == "death"
    assert year.yours.bowling_plan.get(C.YOU_ID) == "death"
    plain, _ = _play(deck, model, PACE["death"], you, 1)
    assert not (plain.yours.bowling_plan or {}).get(C.YOU_ID)


@needs_deck
def test_surgery_ends_the_season(deck, model):
    you = _you(deck, "pace")
    year, _ = _play(deck, model, PACE["back"], you, 0)
    first = year.incident["at"] - 1
    league = [l for l in C.season_detail(year).feed if l.stage == "league"]
    assert all(not l.played for l in league[first:])
    assert sorted(year.incident_out) == list(range(first, len(league)))
    assert all(not l.played for l in C.season_detail(year).feed if l.stage != "league")


@needs_deck
def test_a_slump_break_spares_the_make_or_break(deck, model, monkeypatch):
    """Sitting out benches you for the match the make-or-break would have been, and a benched
    player is never asked one: no separate rule is needed (a break of one was measured to be
    an equivalent mutant)."""
    you = _you(deck, "opener")
    year, _ = _play(deck, model, OPENER["slump"], you, 1)
    at = year.incident["at"]
    assert year.incident["key"] == "slump" and at not in year.verdicts
    assert len(year.incident_out) == 2


@needs_deck
def test_an_answer_of_the_wrong_kind_is_refused(deck, model):
    you = _you(deck, "opener")
    with pytest.raises(C.CareerError):
        C.simulate_year(deck, model, OPENER["hamstring"], K, you, answers=(1, 1, 1))
    with pytest.raises(C.CareerError):
        C.simulate_year(deck, model, OPENER["hamstring"], K, you, answers=(("i", 7),))


# --- standing and threads -------------------------------------------------------------------


@needs_deck
def test_a_cold_club_does_not_ask_you_to_stay(deck, model):
    setup = C.Setup("Ann Example", "opener", 2012)
    car, moves = C.auto_career(deck, model, 3, setup, "loyal")
    cp = car.checkpoint
    k = next(i for i, r in enumerate(cp.seasons[:-1]) if r.year + 1 not in C.MEGA_YEARS
             and not cp.seasons[i + 1].contract_year and i >= 1)
    resume = C.Checkpoint.from_dict(cp.to_dict())
    resume.seasons, resume.k = resume.seasons[:k + 1], k + 1
    resume.current = C.canonical(resume.seasons[-1].franchise)
    resume.contract, resume.ended, resume.epilogue = None, None, None
    warm = C.play_career(deck, model, 3, setup, [], resume=resume)
    assert warm.pending.kind == "offer" and warm.pending.offers[0].stay
    resume.standing = C.STANDING_COLD
    cold = C.play_career(deck, model, 3, setup, [], resume=resume)
    assert cold.pending.cold and not any(o.stay for o in cold.pending.offers)
    assert [o.franchise for o in cold.pending.offers] == [o.franchise for o in warm.pending.offers[1:]]


def test_a_trusted_player_is_retained_a_little_below_the_bar(monkeypatch):
    assert C.retention_slab(C.RETAIN_CARD - 2, capped=True) is None
    assert C.retention_slab(C.RETAIN_CARD - 2 + C.RETAIN_TRUSTED, capped=True) is not None


@needs_deck
def test_a_trusted_club_keeps_you_through_a_poor_season(deck, model, monkeypatch):
    setup = C.Setup("Ann Example", "opener", 2012)
    car, _ = C.auto_career(deck, model, 3, setup, "loyal")
    base = C.Checkpoint.from_dict(car.checkpoint.to_dict())
    k = 5                                   # 2017 at Kolkata, where 2016 was played: a stay
    assert C.canonical(base.seasons[k - 1].franchise) == C.canonical(base.seasons[k].franchise)
    monkeypatch.setattr(C, "form_after", lambda squad, pct, bar=None: (True, False))
    # nothing else may move the standing this season: a quiet card, no incident
    monkeypatch.setattr(C, "season_event", lambda *a, **kw: C.EVENTS[0])
    monkeypatch.setattr(I, "candidate", lambda *a, **kw: (None, 0))
    monkeypatch.setattr(I, "SLUMP_CHANCE", 0.0)

    def season_after(standing):
        resume = C.Checkpoint.from_dict(base.to_dict())
        resume.seasons, resume.k = resume.seasons[:k], k
        resume.current = C.canonical(resume.seasons[-1].franchise)
        resume.contract, resume.ended, resume.epilogue = None, None, None
        resume.squad, resume.standing, resume.threads = False, standing, []
        moves = []
        while True:
            c = C.play_career(deck, model, 3, setup, moves, resume=resume)
            if len(c.checkpoint.seasons) > k:
                return c.checkpoint
            moves.append(C.policy_move(c, "loyal", __import__("random").Random(1)))

    trusted = season_after(C.STANDING_BACKED)
    assert not trusted.squad and trusted.standing == C.STANDING_BACKED - 1
    assert any("stood by you" in n for n in trusted.seasons[k].notes)
    doubted = season_after(C.STANDING_BACKED - 1)
    assert doubted.squad
    assert not any("stood by you" in n for n in doubted.seasons[k].notes)


@needs_deck
def test_threads_pay_off_in_the_archive_s_own_terms(deck):
    _, years = C._deck_index(deck)
    by_year = C._cards_by_year(deck)
    mentor = next(p for p, ys in years.items() if len(ys) >= 6 and max(ys) <= C.MATE_LAST_BEFORE)
    last = max(years[mentor])
    pupil = next(p for p, ys in by_year.items() if max(ys.values()) >= C.PUPIL_STAR
                 and len(ys) >= 3)
    star = min(y for y, d in by_year[pupil].items() if d >= C.PUPIL_STAR)
    said: list = []
    say = lambda text, tone="": said.append((text, tone))
    cp = C.Checkpoint(threads=[{"k": "mentor", "id": mentor, "name": "M", "year": 2009},
                               {"k": "pupil", "id": pupil, "name": "P", "year": 2009}])
    C.threads_due(deck, cp, last, say)                  # his last season is still being played
    assert not any("M," in t for t, _ in said)
    C.threads_due(deck, cp, last + 1, say)
    assert [t for t, _ in said if t.startswith("M,")] == [
        "M, who took you under his wing in 2009, has played his last IPL season."]
    assert all(t["k"] != "mentor" for t in cp.threads)
    said.clear()
    cp = C.Checkpoint(threads=[{"k": "pupil", "id": pupil, "name": "P", "year": 2009}])
    C.threads_due(deck, cp, star, say)
    assert said == [("P, the youngster who shadowed you in 2009, is now one of the best "
                     "players in the league.", "up")]
    assert cp.threads == []


def test_an_old_checkpoint_has_no_standing_and_no_threads():
    d = C.Checkpoint().to_dict()
    del d["standing"], d["threads"]
    cp = C.Checkpoint.from_dict(d)
    assert cp.standing == 0 and cp.threads == []


# --- through a whole career ------------------------------------------------------------------


@needs_deck
def test_a_career_s_notes_have_their_marks_and_its_choices_say_who_and_where(deck, model):
    setup = C.Setup("Ann Example", "pace", 2010)
    car, moves = C.auto_career(deck, model, 11, setup, "random")
    assert any(m.startswith("i") for m in moves), "the fixture must meet an incident"
    for r in car.seasons:
        assert len(r.tones) == len(r.notes), r.year
        assert set(r.tones) <= {"up", "down", ""}
        for n in r.notes:
            assert "Rating" not in n and "%" not in n and "{" not in n, n
        assert "{" not in r.choice
    assert car.checkpoint.injuries
    assert C.STANDING_MIN <= car.checkpoint.standing <= C.STANDING_MAX


@pytest.mark.parametrize("role,kind,lo,hi,most", [("opener", "batter", 0.8, 1.1, 3),
                                                  ("keeper", "keeper", 0.8, 1.1, 3),
                                                  ("spin", "spin", 0.85, 1.15, 4),
                                                  ("allrounder", "allrounder", 0.85, 1.2, 4),
                                                  ("pace", "pace", 2.0, 3.0, 5)])
def test_about_one_injury_a_career_and_two_to_three_for_a_fast_bowler(role, kind, lo, hi, most):
    """[A226] Ratified by the user: injuries in a twelve-season career average about one, two
    to three for a fast bowler. [A230] They happen mid-season now, one chance a season
    (`career_incidents.candidate`), with no kind more often than its career limit."""
    counts = []
    for s in range(2000):
        seen: dict = {}
        for k in range(12):
            key, _ = I.candidate(s, k, kind, 14)
            if key in ("hamstring", "side_strain", "back") and seen.get(key, 0) < I.LIMIT[key]:
                seen[key] = seen.get(key, 0) + 1
        counts.append(sum(seen.values()))
    assert lo <= sum(counts) / len(counts) <= hi, sum(counts) / len(counts)
    assert max(counts) <= most


# --- through the API ------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-only-career-secret")
    from fastapi.testclient import TestClient
    from web.app import app
    with TestClient(app) as c:
        yield c


@needs_deck
def test_an_incident_pauses_the_season_and_its_answer_reaches_the_feed(client):
    d = client.post("/api/career/start", json={"name": "Ann Example", "role": "opener",
                                               "debut": 2014, "seed": 7}).json()
    for _ in range(120):
        p = d["pending"]
        if p["kind"] == "incident":
            break
        move = {"offer": "o0", "event": "e0", "moment": "m1"}.get(p["kind"])
        if p["kind"] == "mega":
            move = "k" if p["mega"]["retain"] else f"b{p['mega']['suggested']}"
        elif p["kind"] == "plan":
            move = "p" + "0" * len(p["plan"])
        d = client.post("/api/career/move", json={"token": d["token"], "move": move}).json()
    inc = d["pending"]["incident"]
    assert inc and len(inc["options"]) >= 2 and not DIGIT.search(
        " ".join(o["label"] + o["hint"] for o in inc["options"]))
    shown = len(d["season_play"]["feed"])                    # the season told up to here
    wrong = client.post("/api/career/move", json={"token": d["token"], "move": "m1"})
    assert wrong.status_code == 409
    d = client.post("/api/career/move", json={"token": d["token"], "move": "i0"}).json()
    while d["pending"] and d["pending"]["kind"] in ("moment", "incident"):
        d = client.post("/api/career/move", json={"token": d["token"],
                                                  "move": "m1" if d["pending"]["kind"] == "moment"
                                                  else "i0"}).json()
    feed = d["season_play"]["feed"]
    tagged = [l for l in feed if l["incident"]]
    assert len(tagged) == 1 and feed.index(tagged[0]) == shown
    assert tagged[0]["incident"] == f"{inc['title']}: {inc['options'][0]['label']}"
    last = d["seasons"][-1]
    assert len(last["note_tones"]) == len(last["notes"])
    assert not any("Rating" in n for n in last["notes"])


@needs_deck
def test_a_later_pause_still_tells_the_incident_before_it(deck, model):
    """A season paused at a key moment AFTER an incident's match shows the incident on its
    feed. Found in the browser: the paused season was told without it."""
    you = _you(deck, "opener")
    found = None
    for seed in OPENER.values():
        answers: list = []
        while found is None:
            try:
                C.simulate_year(deck, model, seed, K, you, answers=tuple(answers))
                break
            except C._NeedMoment as need:
                if need.prompt:
                    answers.append(("i", 0))
                    continue
                n = sum(need.yours in (r.home, r.away) for r in need.season.results)
                if need.incident and n >= need.incident["at"]:
                    found = need
                answers.append(1)
        if found:
            break
    assert found, "a fixture must pause at a key moment after its incident's match"
    need = found
    so_far = C.Year(need.season, need.yours,
                    list(need.season.results) + list(need.season.playoffs or []), need.roster, 0,
                    need.approaches, need.dropped, need.verdicts, need.absent,
                    need.absence_reason, incident=need.incident, incident_out=need.incident_out)
    feed = C.season_detail(so_far, complete=False).feed
    assert [l.incident for l in feed if l.incident] == [
        f"{need.incident['title']}: {need.incident['choice']}"]


def _result(home_runs, home_wkts, away_runs, away_wkts, you_home, won, death_over=None):
    from types import SimpleNamespace as NS
    you, them = NS(name="You 2019"), NS(name="Mumbai Indians 2019")
    me = NS(player=NS(person_id=C.YOU_ID), runs=5, balls=6, out=True, faced_any=True,
            wickets=0, )
    log = [] if death_over is None else [NS(over=death_over[0], over_runs=death_over[1],
                                            bowler="DL Chahar" if len(death_over) < 3 else "You",
                                            bowler_id="x" if len(death_over) < 3 else C.YOU_ID)]
    mine = NS(batting=[me], bowling=[], over_log=[])
    theirs = NS(batting=[], bowling=[], over_log=log)
    home, away = (you, them) if you_home else (them, you)
    return NS(home=home, away=away, home_runs=home_runs, home_wickets=home_wkts,
              away_runs=away_runs, away_wickets=away_wkts, winner=you if won else them,
              home_innings=mine if you_home else theirs,
              away_innings=theirs if you_home else mine), you


def test_a_hammering_blames_the_half_that_failed():
    r, you = _result(114, 5, 116, 2, True, False)            # 114 batting first, lost by 8 wkts
    assert C._your_match(r, you)[0].heavy == "the batters"
    r, you = _result(210, 5, 212, 3, True, False)            # 210 and still hammered
    assert C._your_match(r, you)[0].heavy == "the bowlers"
    r, you = _result(220, 6, 170, 8, False, False)           # chased 221, lost by 50
    assert C._your_match(r, you)[0].heavy == "the bowlers"
    r, you = _result(180, 6, 120, 10, False, False)          # chased 181, bowled out for 120
    assert C._your_match(r, you)[0].heavy == "the batters"
    r, you = _result(180, 6, 150, 8, False, False)           # lost by 30: not a hammering
    assert C._your_match(r, you)[0].heavy is None
    r, you = _result(150, 6, 151, 4, True, False)            # lost by 6 wickets: not one either
    assert C._your_match(r, you)[0].heavy is None


def test_a_collapse_and_a_bad_death_over_are_read_off_the_match():
    r, you = _result(98, 10, 99, 1, True, False)
    assert C._your_match(r, you)[0].collapse == "Bowled out for 98"
    r, you = _result(125, 7, 126, 3, True, False)
    assert C._your_match(r, you)[0].collapse == "Kept to 125 batting first"
    r, you = _result(160, 7, 161, 3, True, False)
    assert C._your_match(r, you)[0].collapse is None
    r, you = _result(160, 7, 161, 3, True, False, death_over=(18, 22))
    assert C._your_match(r, you)[0].death_hit == ("DL Chahar", 22, 19)
    r, you = _result(160, 7, 161, 3, True, False, death_over=(12, 25))     # not the death
    assert C._your_match(r, you)[0].death_hit is None
    r, you = _result(160, 7, 161, 3, True, False, death_over=(18, 22, "you"))  # your own over
    assert C._your_match(r, you)[0].death_hit is None


@needs_deck
def test_an_incident_a_career_has_used_up_does_not_come_again(deck, model):
    you = _you(deck, "opener")
    seed = OPENER["hamstring"]
    with pytest.raises(C._NeedMoment) as first:
        C.simulate_year(deck, model, seed, K, you, answers=())
    assert first.value.prompt["key"] == "hamstring"
    try:
        C.simulate_year(deck, model, seed, K, you, answers=(), spent=("hamstring",))
    except C._NeedMoment as need:
        assert not need.prompt or need.prompt["key"] != "hamstring"
