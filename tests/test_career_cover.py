"""A teammate breaks down, a batter bowls his overs, and the coach asks (A247)."""

import random

import pytest

from game import career as C
from game.simulator import BALLS_PER_OVER, MAX_OVERS_PER_BOWLER, Breakdown, play_innings
from tools.snapshot_deck import deck_from, model_from, read_document

COACH_TESTS = True          # conftest leaves the coach on for this module
DOC = read_document()
pytestmark = pytest.mark.skipif(DOC is None, reason="no deck snapshot")


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


@pytest.fixture(scope="module")
def model():
    return model_from(DOC)


def _sides(deck, model, i):
    from game.__main__ import attack, lineup, opposition_twelve, to_player
    fss = sorted(deck.cards_by_fs)
    xi, _ = opposition_twelve(list(deck.cards_by_fs[fss[i]]))
    other, _ = opposition_twelve(list(deck.cards_by_fs[fss[i - 1]]))
    bowl = attack(xi, model)
    cover = to_player(next(c for c in xi if not c.has_bowl), model)
    return lineup(other, model), bowl, cover


def _sig(inn):
    return (inn.runs, inn.wickets, inn.balls,
            [(b.player.person_id, b.balls, b.runs, b.wickets) for b in inn.bowling if b.balls],
            [o.bowler_id for o in inn.over_log])


def test_a_breakdown_that_never_comes_plays_the_innings_it_always_was(deck, model):
    """Pulling up after four overs is no breakdown at all: the cover is carried but never
    bowls, and the innings is the one played with no breakdown, draw for draw."""
    for i in range(1, 40, 3):
        bats, bowl, cover = _sides(deck, model, i)
        for s in range(3):
            plain = play_innings(model, bats, bowl, random.Random(s))
            late = play_innings(model, bats, bowl, random.Random(s),
                                breakdown=Breakdown(bowl[0].person_id, MAX_OVERS_PER_BOWLER, cover))
            assert _sig(plain) == _sig(late)
            assert late.breakdown_at is None


def test_the_injured_bowler_stops_and_the_cover_bowls_his_overs(deck, model):
    fired = 0
    for i in range(1, 166, 2):
        bats, bowl, cover = _sides(deck, model, i)
        for after in C.COVER_AFTER:
            hurt = bowl[i % 5]
            inn = play_innings(model, bats, bowl, random.Random(i * 7 + after),
                               breakdown=Breakdown(hurt.person_id, after, cover))
            mine = {b.player.person_id: b for b in inn.bowling}
            assert mine[hurt.person_id].balls <= after * BALLS_PER_OVER
            assert mine[cover.person_id].balls <= (MAX_OVERS_PER_BOWLER - after) * BALLS_PER_OVER
            ids = [o.bowler_id for o in inn.over_log]
            assert all(a != b for a, b in zip(ids, ids[1:])), "never two overs running"
            if inn.breakdown_at is not None:
                assert mine[hurt.person_id].balls == after * BALLS_PER_OVER
                assert cover.person_id not in ids[:inn.breakdown_at], "he comes on after"
                if inn.balls == 120:
                    fired += 1
                    # a full innings: the cover bowls the overs the injured man had left,
                    # less any a change bowler took (A239)
                    change = sum(b.balls for b in inn.bowling if b.change)
                    assert (mine[cover.person_id].balls
                            >= (MAX_OVERS_PER_BOWLER - after) * BALLS_PER_OVER - change)
                    assert mine[cover.person_id].balls > 0
    assert fired > 50


def _setup():
    return C.Setup("N", "opener", 2010)


def _start(deck):
    return C.spin(deck, 3, "opener")[0]


def _cp_with(cover, k=3):
    cp = C.Checkpoint()
    cp.seasons = [C.SeasonRecord(k=k - 1, year=2010, franchise="X", short="X", price=0,
                                 stayed=False, event="", choice="", captain=False,
                                 ability=0.5, teams=8, cover=cover)]
    return cp


GOOD = {"at": 4, "opp": "Kolkata Knight Riders", "opp_short": "KKR", "injured": "Z Khan",
        "after": 2, "balls": 12, "runs": 14, "wickets": 1, "good": True}
BAD = dict(GOOD, runs=31, wickets=0, good=False)


def test_a_cover_spell_brings_the_coach_whatever_the_draw(deck):
    """Certain, not drawn: every seed, including those whose trial draw would say no."""
    for seed in range(60):
        card = C.coach_card(seed, _setup(), _start(deck), _cp_with(GOOD), 3, False)
        assert card is not None and card.get("cover") and card["target"] == "allrounder"
        assert "Z Khan" in card["text"] and "Kolkata" in card["text"]
        assert "1/14 in 2 overs" in card["text"]


def test_a_bad_spell_is_still_offered_but_doubtfully(deck):
    good = C.coach_card(1, _setup(), _start(deck), _cp_with(GOOD), 3, False)
    bad = C.coach_card(1, _setup(), _start(deck), _cp_with(BAD), 3, False)
    assert bad is not None and bad["text"] != good["text"]
    assert "got us out of a hole" in good["text"] and "went for some" in bad["text"]


def test_no_cover_offer_once_the_coach_could_not_offer_a_trial(deck):
    cp = _cp_with(GOOD, k=C.COACH_TRIAL_UNTIL + 1)
    card = C.coach_card(0, _setup(), _start(deck), cp, C.COACH_TRIAL_UNTIL + 1, False)
    assert card is None or not card.get("cover")
    cp = _cp_with(GOOD)
    cp.trials = C.COACH_TRIALS_MAX
    card = C.coach_card(0, _setup(), _start(deck), cp, 3, False)
    assert card is None or not card.get("cover")


def test_who_can_be_handed_the_ball(deck):
    cp = C.Checkpoint()
    assert C.cover_eligible(_setup(), cp, 2, False)
    assert not C.cover_eligible(_setup(), cp, 2, True), "never in a squad season"
    assert not C.cover_eligible(C.Setup("N", "finisher", 2010), cp, 2, False)
    assert not C.cover_eligible(C.Setup("N", "pace", 2010), cp, 2, False)
    assert not C.cover_eligible(_setup(), cp, C.COACH_TRIAL_UNTIL, False), \
        "only where a trial could follow next season"


def _year(deck, model, fs, chance, monkeypatch, cover_ok=True, seed=4, k=2):
    monkeypatch.setattr(C, "COVER_CHANCE", chance)
    year = deck.cards_by_fs[fs][0].season_year
    start, top = C.spin(deck, 3, "opener")
    you = C.ability(start, top, 0.5, setup=_setup(), fs_id=fs, year=year,
                    franchise=deck.cards_by_fs[fs][0].franchise)
    return C.simulate_year(deck, model, seed, k, you, cover_ok=cover_ok)


def _fs_with_cover(deck, model, monkeypatch):
    for fs in sorted(deck.cards_by_fs)[::7]:
        y = _year(deck, model, fs, 1.0, monkeypatch)
        if y.cover:
            return fs, y
    raise AssertionError("no season found where the breakdown handed you the ball")


def test_the_season_records_the_spell_off_its_own_scorecard(deck, model, monkeypatch):
    fs, y = _fs_with_cover(deck, model, monkeypatch)
    c = y.cover
    mine = [r for r in y.season.results if y.yours in (r.home, r.away)]
    r = mine[c["at"] - 1]
    inn = r.away_innings if r.home is y.yours else r.home_innings
    assert inn.breakdown_at is not None
    me = next(b for b in inn.bowling if b.player.person_id == C.YOU_ID)
    assert (me.balls, me.runs, me.wickets) == (c["balls"], c["runs"], c["wickets"])
    hurt = next(b for b in inn.bowling if b.player.name == c["injured"])
    assert hurt.balls == c["after"] * BALLS_PER_OVER and hurt.player.person_id != C.YOU_ID
    # exactly one breakdown a season, and only in that match
    assert sum(1 for x in mine for i in (x.home_innings, x.away_innings)
               if i is not None and i.breakdown_at is not None) == 1


def test_no_chance_or_no_eligibility_plays_the_season_unchanged(deck, model, monkeypatch):
    """The breakdown draws on its own stream: with it off the season is the same season."""
    fs, y = _fs_with_cover(deck, model, monkeypatch)
    off = _year(deck, model, fs, 0.0, monkeypatch)
    never = _year(deck, model, fs, 1.0, monkeypatch, cover_ok=False)
    sig = lambda yy: [(r.home_runs, r.away_runs, r.home_wickets, r.away_wickets)
                      for r in yy.season.results]
    assert sig(off) == sig(never)
    assert off.cover is None and never.cover is None
    assert sig(y) != sig(off), "the fixture's breakdown must change its match"


def test_a_bowler_is_never_handed_the_ball_this_way(deck, model, monkeypatch):
    monkeypatch.setattr(C, "COVER_CHANCE", 1.0)
    for fs in sorted(deck.cards_by_fs)[::20]:
        year = deck.cards_by_fs[fs][0].season_year
        start, top = C.spin(deck, 3, "spin")
        you = C.ability(start, top, 0.5, setup=C.Setup("N", "spin", 2010), fs_id=fs,
                        year=year, franchise=deck.cards_by_fs[fs][0].franchise)
        y = C.simulate_year(deck, model, 4, 2, you, cover_ok=True)
        assert y.cover is None
        assert all(i is None or i.breakdown_at is None
                   for r in y.season.results for i in (r.home_innings, r.away_innings))


def test_played_through_the_spell_leads_to_the_coach(deck, model, monkeypatch):
    """A whole career: the season with the spell says so, and the next pre-season the coach
    asks, naming it."""
    monkeypatch.setattr(C, "COVER_CHANCE", 1.0)
    C._SEASON_CACHE.clear()
    rng, moves = random.Random(0), []
    asked = None
    for _ in range(80):
        car = C.play_career(deck, model, 0, _setup(), moves)
        p = car.pending
        if p is not None and p.kind == "coach" and p.coach.get("cover"):
            asked = (car, p)
            break
        moves.append(C.policy_move(car, "loyal", rng))
    C._SEASON_CACHE.clear()
    assert asked is not None
    car, p = asked
    last = car.seasons[-1]
    assert last.cover and last.cover["injured"] in p.coach["text"]
    assert any(last.cover["injured"] in n for n in last.notes)
