"""Career skills, season skills and season roles [A258]. The measuring rules on hand-built
balls and accumulators, where the right answer is obvious; the card rules on hand-built
tables; and the user's own examples against the committed snapshot, which is what a player
will actually see."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from types import SimpleNamespace

import pytest

from etl import skills as S
from game import skills as G


def row(over, ball, bat, ns, bowl, runs=0, wides=0, out=None, kind=None, mid="m1", inn=1,
        season=2016, credited=True):
    return (mid, season, inn, over, ball, bat, ns, bowl, "pace", runs, wides, out, kind, 1,
            None, credited)


# --------------------------------------------------------------------------- the scan

def test_a_wide_is_not_a_ball_faced_and_does_not_count_toward_settling():
    balls = S.scan([row(0, 1, "a", "b", "x", wides=1), row(0, 2, "a", "b", "x", runs=4),
                    row(0, 3, "a", "b", "x", runs=1)])
    assert len(balls) == 2
    assert [b.faced for b in balls] == [0, 1]


def test_only_the_striker_is_out_and_retired_hurt_is_no_wicket():
    balls = S.scan([
        row(0, 1, "a", "b", "x", out="b", kind="run out"),       # non-striker run out
        row(0, 2, "a", "c", "x", out="a", kind="retired hurt"),
        row(0, 3, "d", "c", "x", out="d", kind="bowled"),
        row(0, 4, "e", "c", "x", runs=1)])
    assert [b.out for b in balls] == [False, False, True, False]
    assert balls[3].wk == 2          # run out and bowled are wickets; retired hurt is not


def test_a_run_out_is_not_credited_to_the_bowler():
    balls = S.scan([row(0, 1, "a", "b", "x", out="a", kind="run out", credited=False)])
    assert balls[0].out and not balls[0].credited


def test_batting_order_and_entry_are_first_at_the_crease_either_end():
    balls = S.scan([row(0, 1, "a", "b", "x", out="a", kind="bowled"),
                    row(3, 1, "b", "c", "x"), row(3, 2, "c", "b", "x")])
    by = {b.batter: b for b in balls}
    assert by["b"].pos == 2 and by["c"].pos == 3
    assert by["c"].entry_over == 3      # first at the crease, non-striker, in over 3


# --------------------------------------------------------------------------- measuring

def _part(rng, edge, n):
    d = defaultdict(S.Acc)
    for _ in range(n):
        r = edge / 6 + rng.gauss(0, 1.6)          # edge in runs an over, per-ball noise
        for t in ("all", "death"):
            a = d[t]
            a.n += 1
            a.resid += r
            a.sq += r * r
    return d


def _acc(players, seed=1):
    """{player: (career edge, {season: (that season's own deviation, balls)})} -> the
    accumulator shape `measure` reads, each season split into two halves."""
    rng = random.Random(seed)
    acc = {}
    for p, (edge, seasons) in players.items():
        acc[(p, "bat")] = {}
        for s, (dev, n) in seasons.items():
            a, b = _part(rng, edge + dev, n // 2), _part(rng, edge + dev, n - n // 2)
            acc[(p, "bat")][s] = [a, b, S._add([a, b])]
    return acc


DEATH = "death"


def _crowd(rng, n=90, season_sd=0.0, balls=300):
    return {f"p{i}": (rng.gauss(0, 0.7), {y: (rng.gauss(0, season_sd) if season_sd else 0.0,
                                               balls) for y in range(2010, 2016)})
            for i in range(n)}


def test_a_thin_career_is_shrunk_toward_nothing_far_more_than_a_thick_one():
    rng = random.Random(3)
    players = _crowd(rng)
    players["thick"] = (1.8, {y: (0.0, 300) for y in range(2010, 2016)})
    players["thin"] = (1.8, {2012: (0.0, 15)})
    acc = _acc(players)
    m = S.measure(acc)[DEATH]
    assert m.career_var > 0
    raw = {p: S.value(S.METRIC_BY_KEY[DEATH], S._add(x[2] for x in acc[(p, "bat")].values()))[0]
           for p in ("thick", "thin")}
    assert abs(m.career["thin"]) < 0.3 * abs(raw["thin"])       # mostly noise: pulled in hard
    assert m.career["thick"] > 0.7 * raw["thick"]               # plenty of balls: kept


def test_a_season_that_cannot_be_told_from_luck_reads_as_his_career():
    rng = random.Random(4)
    players = _crowd(rng, season_sd=0.0)
    m = S.measure(_acc(players, seed=6))[DEATH]
    assert m.season_var < 0.05 * m.career_var
    for p in ("p1", "p2", "p3"):
        for y in range(2010, 2016):
            assert m.season[(p, y)] == pytest.approx(m.career[p], abs=0.25)


def test_a_season_that_really_differs_reads_as_different():
    rng = random.Random(5)
    players = _crowd(rng, season_sd=1.0, balls=600)
    players["swing"] = (0.0, {y: (2.5 if y == 2013 else 0.0, 600) for y in range(2010, 2016)})
    m = S.measure(_acc(players, seed=7))[DEATH]
    assert m.season_var > 0.3
    assert m.season[("swing", 2013)] > m.season[("swing", 2012)] + 1.0


def test_a_season_is_judged_against_the_rest_of_his_career_not_including_itself():
    rng = random.Random(8)
    players = _crowd(rng, season_sd=0.5)
    players["one"] = (0.0, {2010: (0.0, 300), 2011: (0.0, 300), 2012: (3.0, 300)})
    acc = _acc(players, seed=9)
    m = S.measure(acc)[DEATH]
    met = S.METRIC_BY_KEY[DEATH]
    full = acc[("one", "bat")]
    rest = S.value(met, S._minus(S._add(p[2] for p in full.values()), full[2012][2]))
    pm = S._shrink(rest[0], rest[1], 0.0, m.career_var)
    pv = m.career_var * rest[1] / (m.career_var + rest[1])
    v = S.value(met, full[2012][2])
    assert m.season[("one", 2012)] == pytest.approx(
        S._shrink(v[0], v[1], pm, pv + m.season_var))


def test_the_slow_burner_lean_is_set_minus_new_not_the_other_way():
    d = {"set": S.Acc(100, 30.0, 300.0), "new": S.Acc(100, -20.0, 300.0),
         "all": S.Acc(200, 10.0, 600.0)}
    v, _noise, n = S.value(S.Metric("slow_burn", "bat", "lean", "set"), d)
    assert v == pytest.approx((0.30 - (-0.20)) * 6)
    assert n == 100


def test_survival_is_signed_so_higher_is_better_for_a_batter_and_wickets_for_a_bowler():
    a = {"all": S.Acc(n=300, events=5, expected=15)}
    assert S.value(S.Metric("survival", "bat", "ratio", "all"), a)[0] > 0
    assert S.value(S.Metric("wickets", "bowl", "ratio", "all"), a)[0] < 0


# --------------------------------------------------------------------------- skills

def _measured(**over):
    """A full `measure` result of empty metrics, with the ones a test sets."""
    out = {m.key: S.Measured(0.0, 0.0, 0, 0.0, 0) for m in S.METRICS}
    out.update(over)
    return out


def _metric(career, season, alone=None, balls=500, r=0.5, cv=1.0, sv=0.5):
    return S.Measured(cv, sv, 50, r, 50, career=career, career_balls={p: balls for p in career},
                      season=season, season_balls={k: balls for k in season},
                      alone=alone if alone is not None else dict(season))


def test_an_elite_skill_needs_a_top_career_and_a_top_season_on_its_own_evidence():
    career = {f"p{i}": i / 10 for i in range(100)}
    season = {(f"p{i}", 2016): i / 10 for i in range(100)}
    alone = dict(season)
    alone[("p99", 2016)] = 0.0          # career at the top, the season itself showed nothing
    career["late"], season[("late", 2016)], alone[("late", 2016)] = 0.0, 9.0, 9.0
    ms = _measured(death=_metric(career, season, alone))
    cuts = S.thresholds(ms)
    held = S.season_skills(ms, cuts)
    hitters = {p for p, d in held.items() for k, *_ in d.get("2016", []) if k == "death_hitter"}
    assert "p99" not in hitters         # no top season
    assert "late" not in hitters        # no career that built to it
    # 101 careers, top 5% -> the 6th best (p94); 101 seasons, top 10% -> the 11th best
    assert hitters == {"p94", "p95", "p96", "p97", "p98"}


def test_a_special_skill_is_earned_by_the_season_and_only_by_someone_who_played_there():
    season = {(f"p{i}", 2016): i / 100 for i in range(100)}
    m = _metric({}, season)
    m.season_balls[("p99", 2016)] = 10            # top value, but barely faced spin
    held = S.season_skills(_measured(spin=m), S.thresholds(_measured(spin=m)))
    got = {p for p, d in held.items() if any(k == "spin_basher" for k, *_ in d["2016"])}
    # p99 is out of the pool, leaving 99 seasons: the top 15% is the best 15 of them
    assert "p99" not in got and got == {f"p{i}" for i in range(84, 99)}


def test_a_metric_that_does_not_repeat_holds_no_skill_however_its_quota_reads():
    season = {(f"p{i}", 2016): i / 100 for i in range(100)}
    ms = _measured(spin=_metric({}, season, r=S.MIN_R - 0.01))
    assert "spin_basher" not in S.thresholds(ms)
    ms = _measured(spin=_metric({}, season, r=S.MIN_R + 0.01))
    assert "spin_basher" in S.thresholds(ms)


def test_anchor_and_aggressor_are_a_shape_so_a_player_good_at_both_is_neither():
    sc = {("both", 1): 2.0, ("anch", 1): 0.0, ("aggr", 1): 2.0}
    sv = {("both", 1): 2.0, ("anch", 1): 2.0, ("aggr", 1): 0.0}
    for i in range(40):
        sc[(f"f{i}", 1)] = sv[(f"f{i}", 1)] = 0.0
    ms = _measured(scoring=_metric({}, sc, cv=1.0, sv=0.0),
                   survival=_metric({}, sv, cv=1.0, sv=0.0))
    held = S.season_skills(ms, S.thresholds(ms))
    keys = {p: {k for k, *_ in d["1"]} for p, d in held.items()}
    assert "anchor" in keys["anch"] and "aggressor" in keys["aggr"]
    assert not {"anchor", "aggressor"} & keys.get("both", set())


def test_run_machine_prices_survival_in_runs_beside_scoring():
    sc = {("a", 1): 0.5}
    sv = {("a", 1): 0.2}
    season, _career = S._complete(_measured(scoring=_metric({}, sc), survival=_metric({}, sv)))
    assert season[("a", 1)] == pytest.approx(0.5 + 0.2 * 5.8 * 0.051 * 6)


def test_no_skill_rests_on_wicket_rates_the_archive_says_do_not_repeat():
    keys = {s.metric for s in S.SKILLS}
    assert "wickets" not in keys
    assert not any("partnership" in s.key for s in S.SKILLS)
    assert keys <= {m.key for m in S.METRICS} | {"style", "complete"}
    assert {s.tier for s in S.SKILLS} == {1, 2, 3}


# --------------------------------------------------------------------------- roles

def test_a_batting_job_is_when_he_came_in_never_how_he_did():
    assert S.bat_job([0, 0, 0, 0, 0], [1, 2, 1, 1, 2]) == "opener"
    assert S.bat_job([2, 3, 4, 5, 3], [3, 3, 3, 4, 3]) == "top"
    assert S.bat_job([7, 8, 9, 6, 8], [4, 4, 5, 4, 4]) == "middle"     # over 8: past the powerplay
    assert S.bat_job([15, 16, 17, 14, 18], [6, 6, 7, 6, 6]) == "finisher"
    assert S.bat_job([18, 19, 17, 18, 19], [8, 9, 8, 9, 8]) == "lower"


def test_a_thin_season_takes_the_career_job_and_a_thin_career_takes_none():
    rows = [row(0, 1, "a", "b", "x", mid=f"o{k}", season=2016) for k in range(6)]
    rows.append(row(10, 1, "a", "z", "x", mid="late", season=2017))
    rows.append(row(10, 1, "q", "z", "x", mid="q1", season=2017))
    roles = S.roles(S.scan(rows))
    assert roles["a"]["bat"] == {"2016": "opener", "2017": "opener"}
    assert "q" not in roles or "2017" not in roles["q"]["bat"]


# --------------------------------------------------------------------------- cards

HELD = {"k": {"2016": [["death_hitter", 3, 1.4], ["anchor", 1, 1.6], ["aggressor", 1, 0.5],
                       ["miser", 2, 2.0], ["pace_basher", 2, 0.9]],
              "2017": [["anchor", 1, 1.2]]}}
AT = {1: 80, 2: 88, 3: 96}


def test_slots_follow_the_higher_of_the_season_card_and_career_standing():
    assert [s.key for s in G.card_skills("k", 2016, "batter", 75, 97, HELD, AT)] == \
        ["anchor", "pace_basher", "death_hitter"]
    assert [s.key for s in G.card_skills("k", 2016, "batter", 90, 0, HELD, AT)] == \
        ["anchor", "pace_basher"]
    assert G.card_skills("k", 2016, "batter", 79, 79, HELD, AT) == []


def test_a_card_shows_what_its_own_season_earned():
    assert [s.key for s in G.card_skills("k", 2017, "batter", 99, 99, HELD, AT)] == ["anchor"]
    assert G.card_skills("k", 2018, "batter", 99, 99, HELD, AT) == []


def test_one_skill_per_tier_the_clearest_and_no_part_time_bowling_skill_for_a_batter():
    shown = G.card_skills("k", 2016, "batter", 85, 0, HELD, AT)
    assert [s.key for s in shown] == ["anchor"]             # not aggressor (weaker)
    bowl = G.card_skills("k", 2016, "bowler", 90, 0, HELD, AT)
    assert [s.key for s in bowl] == ["anchor", "miser"]     # a bowler's batting still shows


def test_standing_is_the_second_best_season_and_one_season_has_none():
    cards = [SimpleNamespace(person_id="a", display=d) for d in (99, 84, 78)] + \
            [SimpleNamespace(person_id="b", display=90)]
    assert G.standing(cards) == {"a": 84, "b": 0}


def _bands(spec):
    """[(level, cards, share earning, tier)] -> (cards, held)."""
    held, cards = {}, []
    key = {1: "anchor", 2: "pace_basher", 3: "death_hitter"}
    for lvl, n, share, tier in spec:
        for i in range(n):
            pid = f"{lvl}-{tier}-{i}"
            cards.append(SimpleNamespace(person_id=pid, season_year=2016, role="batter",
                                         display=lvl))
            if i < share * n:
                held[pid] = {"2016": [[key[tier], tier, 1.0]]}
    return cards, held


def test_a_tier_opens_where_every_band_above_earns_it_half_as_often_as_the_top():
    # Bands are 4 wide, aligned to 99: 96-99, 92-95, 88-91, 84-87, 80-83.
    cards, held = _bands([(98, 40, 0.80, 1), (94, 40, 0.50, 1), (90, 40, 0.45, 1),
                          (86, 40, 0.30, 1),     # under the 40% bar: the tier opens above
                          (82, 40, 0.60, 1)])    # a band below a failing one does not count
    assert G.measure_blocks(cards, held)[1] == 88


def test_a_band_too_thin_to_be_evidence_is_ignored():
    cards, held = _bands([(98, 40, 0.80, 1), (94, 40, 0.50, 1), (90, 40, 0.45, 1),
                          (86, 10, 0.00, 1),     # ten cards: not evidence either way
                          (82, 40, 0.60, 1), (78, 40, 0.10, 1)])
    assert G.measure_blocks(cards, held)[1] == 80


def test_a_higher_tier_never_opens_below_a_lower_one():
    cards, held = _bands([(98, 40, 0.80, 1), (94, 40, 0.10, 1), (90, 40, 0.10, 1),
                          (98, 40, 0.80, 2), (94, 40, 0.80, 2), (90, 40, 0.80, 2)])
    got = G.measure_blocks(cards, held)
    assert got[1] == 96 and got[2] == 96


def test_a_batter_shows_no_bowling_job():
    roles = {"k": {"bat": {"2016": "opener"}, "bowl": {"2016": "middle"}}}
    assert G.season_roles("k", 2016, "batter", roles=roles) == {"bat": "opener"}
    assert G.season_roles("k", 2016, "allrounder", roles=roles) == \
        {"bat": "opener", "bowl": "middle"}


# --------------------------------------------------------------------------- the archive

def _deck_cards():
    from tools.snapshot_deck import deck_from, read_document
    return [c for cs in deck_from(read_document()).cards_by_fs.values() for c in cs]


def _earned(name: str, season: int) -> set[str]:
    doc = G.read_document()
    assert doc, "no skills snapshot: run uv run python -m tools.snapshot_skills"
    pid = next(c.person_id for c in _deck_cards() if c.name == name)
    return {k for k, *_ in doc["skills"].get(pid, {}).get(str(season), [])}


def test_the_users_examples_read_as_the_archive_says():
    assert "death_hitter" in _earned("Andre Russell", 2019)
    assert "death_hitter" in _earned("Virat Kohli", 2016)       # his top season at it
    assert "death_hitter" not in _earned("Virat Kohli", 2022)   # an elite skill needs one
    assert "anchor" in _earned("Virat Kohli", 2022)              # a style skill does not
    assert "death_bowler" in _earned("Jasprit Bumrah", 2024)
    # Measured, and against the brief (A257): Bravo bowled the death without being better
    # at it than others, in any season.
    for y in range(2008, 2023):
        assert not {"death_bowler", "death_wickets"} & _earned("Dwayne Bravo", y)


def test_the_committed_blocks_are_the_ones_the_committed_deck_measures():
    doc = G.read_document()
    assert {int(k): v for k, v in doc["blocks"].items()} == \
        G.measure_blocks(_deck_cards(), doc["skills"])
