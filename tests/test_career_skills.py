"""Skills a Build a Career season earns (A278)."""

import hashlib
import random

import pytest

from etl import skills as S
from game import career as C
from game import career_skills as CS
from game import press
from game import season as SEA
from game.simulator import Player, ball_log, play_innings
from tools.snapshot_deck import deck_from, model_from, read_document

DOC = read_document()
pytestmark = [pytest.mark.skipif(DOC is None, reason="no deck snapshot"),
              pytest.mark.usefixtures("no_incidents")]


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


# --- the record: draws nothing, counts what the scorecard counts ------------------------------

def _sides(deck, year):
    from game.__main__ import opposition_twelve
    out = []
    for fs in sorted(f for f, c in deck.cards_by_fs.items() if c and c[0].season_year == year):
        xi, imp = opposition_twelve(list(deck.cards_by_fs[fs]))
        c = deck.cards_by_fs[fs][0]
        out.append(SEA.Side(name=c.franchise, short=str(fs), xi=xi, impact=imp,
                            franchise=c.franchise, year=year))
    return out


def _fingerprint(model, sides, log):
    rng, h = random.Random(5), hashlib.sha256()
    for a in sides[:4]:
        for b in sides[:4]:
            if a is b:
                continue
            if log is None:
                r = SEA.play(model, a, b, rng)
            else:
                with ball_log(log):
                    r = SEA.play(model, a, b, rng)
            for inn in (r.home_innings, r.away_innings):
                h.update(repr([(x.player.person_id, x.runs, x.balls, x.out) for x in inn.batting]
                              + [(w.player.person_id, w.runs, w.wickets) for w in inn.bowling]).encode())
    return h.hexdigest()


def test_recording_a_season_plays_the_very_same_season(deck, model):
    sides = _sides(deck, 2016)
    col = CS.Collector()
    assert _fingerprint(model, sides, None) == _fingerprint(model, sides, col)
    assert col.acc, "and it recorded something"


def test_the_record_counts_exactly_what_the_scorecard_does(deck, model):
    sides = _sides(deck, 2016)
    col, rng = CS.Collector(), random.Random(9)
    faced, outs, bowled, wkts = {}, {}, {}, {}
    with ball_log(col):
        for a in sides[:3]:
            for b in sides[:3]:
                if a is b:
                    continue
                r = SEA.play(model, a, b, rng)
                for inn in (r.home_innings, r.away_innings):
                    for x in inn.batting:
                        p = x.player.person_id
                        faced[p] = faced.get(p, 0) + x.balls
                        outs[p] = outs.get(p, 0) + x.out
                    for w in inn.bowling:
                        p = w.player.person_id
                        bowled[p] = bowled.get(p, 0) + w.balls
                        wkts[p] = wkts.get(p, 0) + w.wickets
    for p, n in faced.items():
        if n:
            got = col.acc[(p, "bat")]["all"]
            assert (got.n, got.events) == (n, outs[p]), p
    for p, n in bowled.items():
        if n:
            got = col.acc[(p, "bowl")]["all"]
            assert (got.n, got.events) == (n, wkts[p]), p


def test_par_is_what_an_average_player_scores(model):
    """Average sides against an average attack: what happened minus par sums to about nothing,
    for runs, dismissals, dots, boundaries and sixes, if par is the engine's own average."""
    avg = [Player(name=f"a{i}", bat=0.0, bowl=0.0, person_id=f"a{i}",
                  bowling_style="pace" if i % 2 else "spin") for i in range(11)]
    col = CS.Collector()
    with ball_log(col):
        for k in range(600):
            play_innings(model, avg, avg[:5], random.Random(k))
    tot = {t: S.Acc() for t in ("all", "new", "set", "~dot", "~bound", "~six")}
    for (p, disc), d in col.acc.items():
        if disc != "bat":
            continue
        for t in tot:
            a = d.get(t)
            if a:
                tot[t].n += a.n
                tot[t].resid += a.resid
                tot[t].events += a.events
                tot[t].expected += a.expected
    a = tot["all"]
    assert abs(a.resid / a.n) < 0.02, "runs off the bat against par"
    assert a.events / a.expected == pytest.approx(1, abs=0.04), "dismissals"
    for t in ("new", "set"):          # par knows the league's settling curve (A260)
        assert abs(tot[t].resid / tot[t].n) < 0.03, t
        assert tot[t].events / tot[t].expected == pytest.approx(1, abs=0.06), t
    for t in ("~dot", "~bound", "~six"):
        assert tot[t].events / tot[t].expected == pytest.approx(1, abs=0.05), t


def test_a_dot_counts_a_wicket_ball_as_the_archive_does():
    from game.simulator import BatterCard, BowlerCard
    col = CS.Collector("me")
    st_ = BatterCard(Player(name="m", bat=0.0, person_id="me"))
    bw = BowlerCard(Player(name="b", bat=0.0, bowl=0.0, person_id="b", bowling_style="spin"))
    col(3, 0, st_, bw, 0, (1.2, 0.05, 0.4, 0.15, 0.05))      # out: no runs off the bat
    d = col.acc[("me", "bat")]
    assert d["~dot"].events == 1 and d["all"].events == 1
    assert d["~dot"].expected == 0.4 and d["spin"].n == 1 and d["pp"].n == 1


# --- judging --------------------------------------------------------------------------------

def _season(n: int, resid: float, tag_extra: dict | None = None, out_ratio: float = 1.0) -> dict:
    """A kept summary: n balls faced, runs above par spread evenly, dismissals `out_ratio`
    times par."""
    exp = 0.05 * n
    row = [n, resid, resid * resid / n + 1.4 * n, exp * out_ratio, exp]
    d = {t: list(row) for t in ("all", "mid", "new", "set", "pace")}
    for t in ("~dot", "~bound", "~six"):
        d[t] = [n, 0.0, 0.0, 0.3 * n, 0.3 * n]
    d.update(tag_extra or {})
    return {"bat": d}


def _doc(cuts):
    real = CS.document()
    return {"cuts": cuts, "spreads": real["spreads"], "pooled_var": real["pooled_var"],
            "played_sd": {k: 1.0 for k in cuts}}


def test_a_season_earns_a_skill_only_past_its_cut():
    s = _season(300, 60.0)
    got = CS.metric_values(CS.accs(s, "bat"), {}, CS.accs(s, "bat"),
                           S.METRIC_BY_KEY["pace"], {k: tuple(v) for k, v in CS.document()["spreads"].items()})
    played = got["played"]
    assert "pace_basher" in [x[0] for x in CS.judge([s], _doc({"pace_basher": {"played": played - 1e-6}}))]
    assert "pace_basher" not in [x[0] for x in CS.judge([s], _doc({"pace_basher": {"played": played + 1e-6}}))]


def test_a_thin_season_earns_nothing_whatever_its_value():
    s = _season(S.SEASON_FLOOR["situation"] - 1, 40.0)
    assert CS.judge([s], _doc({"pace_basher": {"played": -1e9}})) == []
    s = _season(S.SEASON_FLOOR["situation"], 40.0)
    assert CS.judge([s], _doc({"pace_basher": {"played": -1e9}}))


def test_an_elite_skill_needs_the_career_as_well_as_the_season():
    s = _season(600, 90.0)
    doc = _doc({"mid_dominator": {"season": -1e9, "career": 1e9}})
    assert CS.judge([s], doc) == [], "no career at the top: no elite skill"
    doc = _doc({"mid_dominator": {"season": -1e9, "career": -1e9}})
    assert CS.judge([s], doc)[0][0] == "mid_dominator"
    thin = _season(S.POOL_FLOOR["situation"] - 1, 90.0)
    assert CS.judge([thin], doc) == [], "nor with too little career behind it"


def test_the_career_before_moves_the_backed_season():
    """A season is shrunk toward the career before it, so the same season reads higher after
    good seasons than after poor ones."""
    good, poor, this = _season(400, 80.0), _season(400, -80.0), _season(300, 20.0)
    sp = {k: tuple(v) for k, v in CS.document()["spreads"].items()}
    m = S.METRIC_BY_KEY["pace"]
    after = lambda prev: CS.metric_values(CS.accs(this, "bat"), CS.accs(prev, "bat"),
                                          S._add([CS.accs(prev, "bat"), CS.accs(this, "bat")]), m, sp)
    assert after(good)["season"] > after(poor)["season"]
    assert after(good)["played"] == after(poor)["played"], "the season as played is its own"


def _bowl_season(n: int, pp_saved: float) -> dict:
    """A bowling season of n balls a phase, `pp_saved` runs saved in the powerplay."""
    exp = 0.05 * n
    d = {t: [n, 0.0, 1.4 * n, exp, exp] for t in ("mid", "death")}
    d["pp"] = [n, pp_saved, pp_saved * pp_saved / n + 1.4 * n, exp, exp]
    d["all"] = [3 * n, pp_saved, sum(v[2] for v in d.values()), 3 * exp, 3 * exp]
    for t in ("~dot", "~bound", "~six"):
        d[t] = [3 * n, 0.0, 0.0, 0.3 * n, 0.3 * n]
    return {"bowl": d}


def test_skills_the_engine_does_not_play_are_never_earned():
    s = _bowl_season(200, 40.0)
    doc = _doc({k: {"played": -1e9} for k in CS.NOT_PLAYED} | {"miser": {"played": -1e9}})
    assert [x[0] for x in CS.judge([s], doc)] == ["miser"], "the fixture can earn a bowling skill"
    assert {"pp_controller", "mid_controller", "death_controller"} == CS.NOT_PLAYED


def test_only_the_last_season_is_judged_the_rest_is_its_prior():
    strong, poor = _season(400, 160.0), _season(300, -40.0)
    sp = {k: tuple(v) for k, v in CS.document()["spreads"].items()}
    v = lambda x: CS.metric_values(CS.accs(x, "bat"), {}, CS.accs(x, "bat"),
                                   S.METRIC_BY_KEY["pace"], sp)["played"]
    doc = _doc({"pace_basher": {"played": (v(strong) + v(poor)) / 2}})
    assert "pace_basher" in [x[0] for x in CS.judge([strong], doc)]
    assert "pace_basher" not in [x[0] for x in CS.judge([strong, poor], doc)]


def test_a_season_from_before_skills_counts_for_nothing():
    s = _season(300, 60.0)
    doc = _doc({"pace_basher": {"played": -1e9}})
    assert CS.judge([None], doc) == []
    assert CS.judge([None, s], doc), "an older season is no career, not an error"


def test_slots_open_at_80_87_94_one_skill_a_tier_the_clearest():
    earned = [["anchor", 1, 0.5], ["slow_burner", 1, 0.9], ["pace_basher", 2, 1.2],
              ["miser", 2, 2.0], ["mid_dominator", 3, 1.0]]
    assert CS.shown(earned, 79, "allrounder") == []
    assert CS.shown(earned, 80, "allrounder") == [["slow_burner", 1, 0.9]]
    assert [x[0] for x in CS.shown(earned, 87, "allrounder")] == ["slow_burner", "miser"]
    assert [x[0] for x in CS.shown(earned, 94, "allrounder")] == ["slow_burner", "miser",
                                                                  "mid_dominator"]
    assert [x[0] for x in CS.shown(earned, 94, "batter")] == ["slow_burner", "pace_basher",
                                                              "mid_dominator"], \
        "a batter shows no bowling skill"


def test_the_signature_counts_seasons_and_ranks_by_tier():
    class R:
        def __init__(self, skills):
            self.skills = skills
    sig = CS.signature([R([["anchor", 1, 1.0]]), R([["anchor", 1, 0.8], ["miser", 2, 1.0]]),
                        R([["new_ball", 3, 1.0]]), R([])])
    assert sig == [["new_ball", 3, 1], ["miser", 2, 1], ["anchor", 1, 2]]


def test_the_clipping_names_the_new_skills():
    v = press.Voice("Asha Rao", "spin", 3)
    c = press.render({"k": "skill", "y": 2014, "club": "Chennai Super Kings 2014",
                      "skills": ["Miser", "Hard to hit"]}, v)
    assert c is not None and "Miser and Hard to hit" in c.body[0]


# --- the career ---------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def played(deck, model):
    """A real career, built with the switches the per-test fixtures would set."""
    with pytest.MonkeyPatch.context() as mp:
        for name, v in (("COACH_ON", False), ("PHASE_ON", False), ("KEEPER_ASK_CHANCE", 0.0),
                        ("LEANS_ON", False)):
            mp.setattr(C, name, v)
        C._SEASON_CACHE.clear()
        car, _ = C.auto_career(deck, model, 7, C.Setup("X", "opener", 2012), "strength")
    C._SEASON_CACHE.clear()
    return car


def test_a_career_season_keeps_its_record_and_the_scorecard_agrees(played):
    for r in played.seasons:
        bat = (r.skill_log or {}).get("bat", {}).get("all")
        assert bat is not None and bat[0] == r.balls and bat[3] == r.outs, r.year


def test_each_season_is_judged_against_the_career_before_it(played):
    logs = [r.skill_log for r in played.seasons]
    for i, r in enumerate(played.seasons):
        assert r.skills_earned == CS.judge(logs[: i + 1])
        assert r.skills == CS.shown(r.skills_earned, r.rating, "batter")
    assert any(r.skills for r in played.seasons), "the fixture shows a skill"


def test_a_new_skill_is_a_milestone_and_a_clipping_once(played):
    seen, firsts = set(), []
    for r in played.seasons:
        new = [k for k, _t, _s in r.skills if k not in seen]
        seen |= {k for k, _t, _s in r.skills}
        ms = [m.text for m in r.milestones if m.key == "skill"]
        assert ms == [f"New skill: {CS.label(k)}" for k in new], r.year
        clips = [f for f in r.press if f["k"] == "skill"]
        assert (clips[0]["skills"] if clips else []) == [CS.label(k) for k in new]
        firsts += new
    assert firsts, "the fixture earns one"


def test_skills_off_earns_none_and_plays_the_same(deck, model, played, monkeypatch):
    monkeypatch.setattr(C, "COACH_ON", False)
    monkeypatch.setattr(C, "PHASE_ON", False)
    monkeypatch.setattr(C, "KEEPER_ASK_CHANCE", 0.0)
    monkeypatch.setattr(C, "LEANS_ON", False)
    monkeypatch.setattr(C, "CAREER_SKILLS_ON", False)
    C._SEASON_CACHE.clear()
    car, _ = C.auto_career(deck, model, 7, C.Setup("X", "opener", 2012), "strength")
    C._SEASON_CACHE.clear()
    assert all(not r.skills for r in car.seasons)
    assert [(r.runs, r.balls, r.display) for r in car.seasons] == \
        [(r.runs, r.balls, r.display) for r in played.seasons]
