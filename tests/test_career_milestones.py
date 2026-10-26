"""Career milestones (A209): firsts and round numbers on the match they happened in, and a
season's honours, counted from the season records before it."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from game import career as C
from game.simulator import Milestone
from tools.snapshot_deck import deck_from, model_from, read_document


def _rec(**kw) -> C.SeasonRecord:
    base = dict(k=0, year=2010, franchise="Mumbai Indians", short="MI 2010", price=100,
                stayed=False, event="quiet", choice="", captain=False, ability=0.0, teams=8)
    base.update(kw)
    return C.SeasonRecord(**base)


def _line(stage="league", runs=0, out=True, batted=True, wickets=0, bowl_balls=0,
          played=True, landmarks=()) -> C.MatchLine:
    return C.MatchLine(stage=stage, opponent="Kolkata Knight Riders 2010", opp_short="KKR 2010",
                       opp_franchise=None, opp_year=None, result="W", score_for="1",
                       score_against="0", margin="", played=played, batted=batted, runs=runs,
                       balls=max(runs, 1), out=out, wickets=wickets, bowl_runs=20,
                       bowl_balls=bowl_balls, landmarks=list(landmarks))


def _texts(ms, key=None):
    return [m.text for m in ms if key is None or m.key == key]


def test_a_maiden_fifty_fires_once_on_its_match():
    feed = [_line(runs=30), _line(runs=55, out=False), _line(runs=70)]
    ms = C.career_milestones([], feed)
    maiden = [m for m in ms if m.key == "maiden_fifty"]
    assert [(m.match, m.text) for m in maiden] == [(1, "Maiden IPL fifty: 55* v KKR 2010")]


def test_a_veteran_resumed_from_an_old_token_gets_no_maiden_anything():
    """Prior counts come from the records, which old tokens carry, not from stored milestones."""
    veteran = _rec(fifties=6, hundreds=1, four_fors=1, five_fors=1, runs=2400, matches=40)
    feed = [_line(runs=64), _line(runs=101), _line(bowl_balls=24, wickets=5)]
    ms = C.career_milestones([veteran], feed)
    assert not [m for m in ms if m.key.startswith("maiden")]


def test_a_first_hundred_is_a_hundred_and_no_fifty_follows_it():
    ms = C.career_milestones([], [_line(runs=104), _line(runs=60)])
    assert _texts(ms, "maiden_hundred") == ["Maiden IPL hundred: 104 v KKR 2010"]
    assert _texts(ms, "maiden_fifty") == []


def test_round_numbers_land_on_the_match_that_crossed_them():
    prior = _rec(runs=960, wickets=48, matches=48)
    feed = [_line(played=False), _line(runs=20), _line(runs=25, bowl_balls=24, wickets=3),
            _line(runs=1)]
    ms = C.career_milestones([prior], feed)
    assert [(m.text, m.match) for m in ms if m.key == "runs"] == [("1,000 IPL runs", 2)]
    assert [(m.text, m.match) for m in ms if m.key == "wickets"] == [("50 IPL wickets", 2)]
    # (did not play), 49, 50, 51: the fiftieth is the third row, not the second
    assert [(m.text, m.match) for m in ms if m.key == "matches"] == [("50 IPL matches", 2)]


def test_a_four_for_then_a_five_for_are_both_firsts_but_not_twice():
    feed = [_line(bowl_balls=24, wickets=4), _line(bowl_balls=24, wickets=4),
            _line(bowl_balls=24, wickets=5), _line(bowl_balls=24, wickets=6)]
    ms = C.career_milestones([], feed)
    assert [(m.key, m.match) for m in ms if m.key.startswith("maiden")] == [
        ("maiden_four_for", 0), ("maiden_five_for", 2)]


def test_every_hat_trick_is_a_milestone():
    feed = [_line(bowl_balls=24, wickets=3, landmarks=["hattrick"]), _line(bowl_balls=6),
            _line(bowl_balls=24, wickets=3, landmarks=["hattrick"])]
    assert [m.match for m in C.career_milestones([], feed) if m.key == "hattrick"] == [0, 2]


def test_season_honours_count_from_the_career_before():
    before = [_rec(champion=True, display=91, captain=True), _rec(champion=True, display=80)]
    feed = [_line(), _line(stage="Qualifier 1"), _line(stage="Final")]
    rec = _rec(champion=True, display=94, captain=True, orange_cap=True, runs=701)
    ms = C.career_milestones(before, feed, rec)
    assert [(m.key, m.text, m.match) for m in ms if m.match is None or m.key == "title"] == [
        ("title", "Third IPL title", 2), ("orange_cap", "Orange Cap: 701 runs", None)]
    first = C.career_milestones([], feed, _rec(champion=True, display=93, captain=True))
    assert _texts(first) == ["IPL champions", "First 90+ season grade: 93",
                             "Captain of Mumbai Indians"]


def test_a_paused_season_has_no_honours_yet():
    feed = [_line(runs=55), _line(stage="Final")]
    champ = _rec(champion=True, display=95, captain=True)
    assert len(C.career_milestones([], feed, champ)) == 4          # the honours exist...
    assert _texts(C.career_milestones([], feed, None)) == ["Maiden IPL fifty: 55 v KKR 2010"]


def test_landmarks_read_your_hat_trick_off_the_innings_you_bowled_in():
    line = _line(bowl_balls=24, wickets=3)
    mine = Milestone("hattrick", "Me", C.YOU_ID, 40, 20, 3, 24)
    other = Milestone("hattrick", "Him", "abc", 40, 20, 3, 24)
    assert "hattrick" in C._landmarks(line, SimpleNamespace(milestones=[mine]))
    assert "hattrick" not in C._landmarks(line, SimpleNamespace(milestones=[other]))
    assert C._landmarks(_line(runs=100), None) == ["hundred"]
    assert C._landmarks(_line(runs=99), None) == ["fifty"]
    assert C._landmarks(_line(runs=99, batted=False), None) == []


def test_an_old_checkpoint_without_milestones_still_reads():
    cp = C.Checkpoint(seasons=[_rec(milestones=[C.CareerMilestone("runs", "1,000 IPL runs", 3)])])
    d = cp.to_dict()
    assert C.Checkpoint.from_dict(d).seasons[0].milestones[0].match == 3
    del d["seasons"][0]["milestones"], d["seasons"][0]["five_fors"], d["seasons"][0]["hat_tricks"]
    assert C.Checkpoint.from_dict(d).seasons[0].milestones == []


DOC = read_document()


@pytest.mark.skipif(DOC is None, reason="no deck snapshot")
def test_a_real_career_records_its_landmarks_against_its_own_feed():
    """Played through: every season's landmarks agree with its figures, the hat-tricks and
    five-fors on the record agree with the feed, and the season's milestones are the ones the
    rule gives for the career before it."""
    deck, model = deck_from(DOC), model_from(DOC)
    setup = C.Setup("X", "pace", 2010, False)
    # seed 1, found by search (again after A229 priced uncapped players lower, which moved
    # the mega auctions): a hat-trick, so the count has something to agree with
    career, moves = C.auto_career(deck, model, 1, setup, "strength")
    seasons, checked = career.seasons, 0
    assert sum(r.hat_tricks for r in seasons) >= 1, "the fixture must take a hat-trick"
    for k in range(len(seasons)):
        played = C.play_career(deck, model, 1, setup, moves[:_moves_through(moves, k)])
        feed = played.detail.feed
        rec = played.checkpoint.seasons[k]
        for l in feed:
            assert ("five_for" in l.landmarks) == (l.wickets >= 5)
            assert ("four_for" in l.landmarks) == (4 <= l.wickets < 5)
        assert rec.five_fors == sum(l.wickets >= 5 for l in feed)
        assert rec.hat_tricks == sum("hattrick" in l.landmarks for l in feed)
        # The selectors' milestones (A229) and an ambition's (A228) come from outside the
        # feed and are checked by their own tests.
        own = [m for m in rec.milestones if m.key not in ("international", "caps", "ambition")]
        assert own == C.career_milestones(seasons[:k], feed, rec)
        checked += 1
    assert checked == len(seasons) >= 3
    assert career.summary.milestones == [(r.year, m.text) for r in seasons for m in r.milestones]


def _moves_through(moves, k):
    """The moves that play the career up to and including season k: an offer opens each."""
    opens = [i for i, m in enumerate(moves) if m[0] in "ocbk"]   # an offer, or a mega year
    return opens[k + 1] if k + 1 < len(opens) else len(moves)
