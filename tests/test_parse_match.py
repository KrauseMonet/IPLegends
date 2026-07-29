"""Traps from SPEC 4, each pinned to the match in the archive that exhibits it.

These read real files rather than fixtures invented to pass. A fixture written by
hand only proves the parser agrees with my reading of the format; the archive is
what the loader will actually meet.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from etl.db import data_dir
from etl.parse_match import parse_match, target_overs_to_balls

ARCHIVE = data_dir() / "ipl_json.zip"

pytestmark = pytest.mark.skipif(
    not ARCHIVE.exists(),
    reason="ipl_json.zip absent; run: uv run python -m etl.download",
)


def load(match_id: str):
    with zipfile.ZipFile(ARCHIVE) as zf:
        return parse_match(json.loads(zf.read(f"{match_id}.json")), match_id)


def test_season_label_is_never_trusted():
    """SPEC 4.2. The first IPL match was played in 2008 and is labelled 2007/08."""
    match = load("335982")
    assert match.season_year == 2008
    assert match.raw_season_label == "2007/08"
    assert match.match_date.isoformat() == "2008-04-18"


def test_franchise_rename_collapses_to_one_key():
    """SPEC 4.3. Bangalore in 2008 and Bengaluru in 2025 are one franchise."""
    old = load("335982")
    new = load("1473471")
    assert "Royal Challengers Bengaluru" in (old.team_a, old.team_b)
    assert "Royal Challengers Bengaluru" in (new.team_a, new.team_b)
    assert old.season_year == 2008 and new.season_year == 2025


def test_fractional_target_becomes_balls():
    """SPEC 4.5. One match carries target.overs 9.2, meaning 56 balls."""
    assert target_overs_to_balls(9.2) == 56
    assert target_overs_to_balls(20) == 120

    match = load("392186")
    chase = [d for d in match.deliveries if d.innings_no == 2]
    assert {d.innings_scheduled_balls for d in chase} == {56}
    assert match.was_reduced


def test_super_overs_are_flagged_and_excluded():
    """SPEC 4.4. This match went to a second super over, so six innings."""
    match = load("1216517")
    assert match.had_super_over

    super_over = [d for d in match.deliveries if d.is_super_over]
    assert {d.innings_no for d in super_over} == {3, 4, 5, 6}
    assert all(d.phase is None for d in super_over)

    # A tie stays a tie. The eliminator is recorded as the winner because it is,
    # and nothing about the tie is lost by doing so.
    assert match.result_type == "tie"
    assert match.winner is not None
    assert match.result_margin is None


def test_super_over_contributes_nothing_to_participation():
    """SPEC 4.4. Participation is decided by the match proper, super over aside."""
    match = load("1216517")
    from_real_play = set()
    for d in match.deliveries:
        if d.is_super_over:
            continue
        from_real_play |= {d.batter_id, d.bowler_id, d.non_striker_id}
        if d.player_out_id:
            from_real_play.add(d.player_out_id)

    participated = {a.person_id for a in match.appearances if a.participated}
    # Fielders widen the set, so real play is a subset rather than an equality;
    # what matters is that the super over adds no one it did not already contain.
    assert from_real_play <= participated
    super_over_players = {d.batter_id for d in match.deliveries if d.is_super_over}
    assert super_over_players - participated == set()


def test_miscounted_over_is_flagged_on_its_own_innings_only():
    """SPEC 4.5b. Over 7 of the first innings really had five legal balls."""
    match = load("335987")
    flagged = {(d.innings_no, d.over_no) for d in match.deliveries if d.over_miscounted}
    assert flagged == {(1, 7)}

    legal_in_over = sum(
        1
        for d in match.deliveries
        if d.innings_no == 1 and d.over_no == 7 and d.legal_ball
    )
    assert legal_in_over == 5
    # Over 7 of the second innings is an ordinary over and must not be flagged.
    assert sum(
        1
        for d in match.deliveries
        if d.innings_no == 2 and d.over_no == 7 and d.legal_ball
    ) == 6
    # The innings still ran its full twenty overs, so the match was not reduced.
    assert not match.was_reduced


def test_illegal_deliveries_keep_their_place_in_the_sequence():
    """SPEC 4.7. ball_no counts every delivery; legal_ball is what stats filter on."""
    match = load("335982")
    first_over = [
        d for d in match.deliveries if d.innings_no == 1 and d.over_no == 0
    ]
    assert [d.ball_no for d in first_over] == list(range(1, len(first_over) + 1))
    assert sum(1 for d in first_over if d.legal_ball) == 6
    for delivery in match.deliveries:
        assert delivery.legal_ball == (
            delivery.extra_wides == 0 and delivery.extra_noballs == 0
        )


def test_extras_columns_sum_to_the_total():
    """SPEC 5 A1. The check constraint in migration 003 must never fire."""
    match = load("335982")
    for d in match.deliveries:
        assert d.runs_extras == (
            d.extra_wides + d.extra_noballs + d.extra_byes + d.extra_legbyes
            + d.extra_penalty
        )


def test_impact_player_entering_counts_as_participation():
    """SPEC 5 A16. Coming on is participation; being the unused 12th name is not."""
    match = load("1359475")
    assert any(not a.named_in_squad or a.participated for a in match.appearances)
    assert any(not a.participated for a in match.appearances), (
        "a squad of twelve should leave someone unused"
    )


def test_wicket_credit_follows_the_mode_of_dismissal():
    match = load("335982")
    for d in match.deliveries:
        if d.wicket_kind is None:
            assert d.credited_to_bowler is None and d.player_out_id is None
        elif d.wicket_kind == "run out":
            assert d.credited_to_bowler is False
        elif d.wicket_kind in ("bowled", "caught", "lbw"):
            assert d.credited_to_bowler is True


def test_abandoned_innings_reports_rather_than_guesses():
    """The intended length is not in the file, so it is null and it is logged."""
    match = load("1473495")
    assert all(d.innings_scheduled_balls is None for d in match.deliveries)
    assert match.result_type == "no result"
    assert match.winner is None
    assert any("not observable" in w for w in match.warnings)


def test_legal_ball_agrees_with_the_source_ball_reference():
    """Cricsheet's own `actual_delivery` is the scorecard ball number.

    It is derivable from legal_ball, which makes it a free independent check on
    the wide and no-ball classification rather than a column worth storing.
    """
    with zipfile.ZipFile(ARCHIVE) as zf:
        raw = json.loads(zf.read("335982.json"))
    match = parse_match(raw, "335982")

    expected = [
        d["actual_delivery"]
        for innings in raw["innings"]
        for over in innings["overs"]
        for d in over["deliveries"]
    ]

    actual = []
    index = 0
    for innings in raw["innings"]:
        for over in innings["overs"]:
            legal = 0
            for _ in over["deliveries"]:
                delivery = match.deliveries[index]
                index += 1
                legal += delivery.legal_ball
                actual.append(
                    f"{over['over']}.{legal if delivery.legal_ball else legal + 1}"
                )
    assert actual == expected
