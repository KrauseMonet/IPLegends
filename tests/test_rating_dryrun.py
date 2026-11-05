"""The rating dry-run's own rules, on hand-built cards. [A256]

The tool's database half (run a draft migration, read the view, roll back) is exercised by
running it; what is tested here is everything that decides what it REPORTS, because a
reporting tool that classifies wrongly is a check that passes when it should not.
"""

import pytest

from tools.rating_dryrun import (CardRow, cause, classify, contradictions, engine_moved,
                                 percentile, potm_band, to_display)


def card(key=(1, "p"), *, display=80, merit=1.0, career=0.5, blended=0.85, year=2016,
         role="batter", runs=0, faced=0, outs=0, bowled=0, conceded=0, wickets=0,
         played=14, engine=None):
    c = CardRow(key, key[1], year, role, display, merit, career, blended, played, 0.0)
    c.runs, c.faced, c.outs = runs, faced, outs
    c.bowled, c.conceded, c.wickets, c.played = bowled, conceded, wickets, played
    c.engine = engine if engine is not None else {"batting": (0.1, 1.0)}
    return c


def test_percentile_matches_postgres_percentile_cont():
    # percentile_cont(0.25) over 1..5 is 2.0; over 1,2,3,4 it is 1.75 (linear between ranks)
    assert percentile([5, 1, 3, 2, 4], 0.25) == 2.0
    assert percentile([1, 2, 3, 4], 0.25) == pytest.approx(1.75)


def test_display_rounds_half_up_like_postgres():
    # 70 + 29 * 0.5 = 84.5, which Postgres's round() takes to 85 and Python's to 84
    assert to_display(0.5, 0.0, 1.0) == 85
    assert to_display(-9, 0.0, 1.0) == 70 and to_display(9, 0.0, 1.0) == 99


def test_a_league_shift_alone_is_collateral_not_career():
    """The trap the first version fell into: when anybody's merit changes the LEAGUE mean
    moves, which moves every career a little. A card whose own seasons are untouched must
    still read as collateral, or the gate on collateral can never fire."""
    old = card(merit=1.0, career=0.500, blended=0.850)
    new = card(merit=1.0, career=0.502, blended=0.8506)    # moved by a share of the shift
    assert cause(old, new, player_changed=False) == "scale"


def test_causes_in_order():
    old = card()
    assert cause(old, card(merit=1.2), player_changed=False) == "own season"
    assert cause(old, card(career=0.6, blended=0.88), player_changed=True) == "career"
    # merit and career identical, the blend moved: the career RULE changed
    assert cause(old, card(blended=0.70), player_changed=False) == "blend"
    assert cause(old, card(display=81), player_changed=False) == "scale"


def test_a_blend_that_moves_further_than_its_career_is_the_rule():
    old = card(career=0.500, blended=0.850)
    new = card(career=0.502, blended=0.800)               # career +0.002, blend -0.05
    assert cause(old, new, player_changed=False) == "blend"


def test_classify_reads_another_season_of_the_same_player_as_career():
    a_old, a_new = card((1, "x"), merit=1.0, display=80), card((1, "x"), merit=2.0, display=85)
    b_old = card((2, "x"), career=0.5, blended=0.8, display=78)
    b_new = card((2, "x"), career=0.7, blended=0.86, display=79)
    c_old = card((3, "y"), display=75)
    c_new = card((3, "y"), display=76)                    # nothing of its own changed
    moved = classify({c.key: c for c in (a_old, b_old, c_old)},
                     {c.key: c for c in (a_new, b_new, c_new)})
    causes = {a.key: why for _, why, a, _ in moved}
    assert causes == {(1, "x"): "own season", (2, "x"): "career", (3, "y"): "scale"}


def test_classify_refuses_a_candidate_rating_different_seasons():
    with pytest.raises(SystemExit):
        classify({(1, "a"): card((1, "a"))}, {(2, "a"): card((2, "a"))})


def test_engine_moved_counts_only_engine_numbers():
    base = {(1, "a"): card(engine={"batting": (0.10, 1.0), "bowling": (-0.05, 1.1)})}
    same = {(1, "a"): card(display=99, engine={"batting": (0.10, 1.0), "bowling": (-0.05, 1.1)})}
    one = {(1, "a"): card(engine={"batting": (0.10, 1.0), "bowling": (-0.05, 1.2)})}
    assert engine_moved(base, same) == 0
    assert engine_moved(base, one) == 1


def test_a_contradiction_is_the_better_scorecard_holding_the_lower_card():
    better = card((1, "a"), display=76, runs=400, faced=300, outs=10)
    worse = card((2, "b"), display=80, runs=300, faced=260, outs=12)
    k = contradictions({c.key: c for c in (better, worse)})
    assert (k["batting pairs"], k["batting contradicted"]) == (1, 1)
    better.display = 84
    k = contradictions({c.key: c for c in (better, worse)})
    assert (k["batting pairs"], k["batting contradicted"]) == (1, 0)


def test_a_bowling_contradiction_needs_more_wickets_cheaper_and_comparable_volume():
    better = card((1, "a"), role="bowler", display=78, bowled=300, conceded=350, wickets=20)
    worse = card((2, "b"), role="bowler", display=85, bowled=300, conceded=400, wickets=15)
    k = contradictions({c.key: c for c in (better, worse)})
    assert (k["bowling pairs"], k["bowling contradicted"]) == (1, 1)
    better.bowled = 200        # far fewer balls: not a clear scorecard any more
    better.conceded = 230
    k = contradictions({c.key: c for c in (better, worse)})
    assert k["bowling pairs"] == 0


def test_potm_bands():
    assert [potm_band(x) for x in (0, 0.05, 0.10, 0.15, 0.25)] == \
        ["0", "<=.10", "<=.10", "<=.20", ">.20"]
